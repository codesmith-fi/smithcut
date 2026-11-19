"""Paikallinen VLM-kohdistus (Ollama vision API) still-ruuduille."""

from __future__ import annotations

import base64
import json
import os
import re
import struct
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_VLM_MODEL = "qwen2.5vl:3b"
DEFAULT_VLM_KEEP_ALIVE = "0"

_THINK_TAG_RE = re.compile(r"<think>.*?</think>|</?think>", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

_VLM_SYSTEM_PROMPT = (
    "You are a precise visual object locator for video frames. "
    "Given an image and a description of an object or UI element, find the center "
    "of that target on a 0 to 1000 coordinate scale where x=0 is the left edge, "
    "x=1000 is the right edge, y=0 is the top edge, and y=1000 is the bottom edge "
    "(for example, the exact center of the frame is x=500, y=500). "
    "Respond ONLY with a single JSON object matching this exact schema:\n"
    '{"found": true, "x": 500, "y": 500, "confidence": "high", "reason": "short explanation"}\n'
    "If the target is not visible in the frame, return:\n"
    '{"found": false, "x": null, "y": null, "confidence": "low", "reason": "not visible"}'
)


class VisionError(RuntimeError):
    """VLM-analyysin virhe (yhteys, puuttuva malli tai virheellinen vastaus)."""

    def __init__(self, message: str, *, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


def vlm_model_name() -> str:
    return os.environ.get("CUTSMITH_VLM_MODEL", DEFAULT_VLM_MODEL)


def vlm_keep_alive() -> str:
    return os.environ.get("CUTSMITH_VLM_KEEP_ALIVE", DEFAULT_VLM_KEEP_ALIVE)


def _ollama_base() -> str:
    return (
        os.environ.get("OLLAMA_HOST")
        or os.environ.get("CUTSMITH_OLLAMA_URL")
        or DEFAULT_OLLAMA_URL
    ).rstrip("/")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _read_image_size(data: bytes) -> tuple[int | None, int | None]:
    """Lue PNG- tai JPEG-kuvan (width, height) ilman ulkoisia kirjastoja."""
    # PNG: IHDR on kiinteässä kohdassa tavuissa 16..24
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return (w if w > 0 else None, h if h > 0 else None)

    # JPEG: käy markkerit läpi SOF-lohkoon asti
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i + 4 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            while i < n and data[i] == 0xFF:
                i += 1
            if i >= n:
                break
            marker = data[i]
            i += 1
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > n:
                break
            seg_len = struct.unpack(">H", data[i : i + 2])[0]
            if seg_len < 2 or i + seg_len > n:
                break
            # SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ) and seg_len >= 7:
                h, w = struct.unpack(">HH", data[i + 3 : i + 7])
                return (w if w > 0 else None, h if h > 0 else None)
            i += seg_len
    return None, None


def _normalize_coords(
    raw_x: float,
    raw_y: float,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> tuple[float, float]:
    """Normalisoi koordinaatit 0.0..1.0 -välille (tukee 0..1, 0..1000 ja pikseleitä)."""
    if raw_x <= 2.0 and raw_y <= 2.0:
        return round(_clamp01(raw_x), 4), round(_clamp01(raw_y), 4)
    if raw_x <= 1000.0 and raw_y <= 1000.0:
        return (
            round(_clamp01(raw_x / 1000.0), 4),
            round(_clamp01(raw_y / 1000.0), 4),
        )
    w = float(image_width) if image_width and image_width > 0 else 1000.0
    h = float(image_height) if image_height and image_height > 0 else 1000.0
    return round(_clamp01(raw_x / w), 4), round(_clamp01(raw_y / h), 4)


def parse_vision_json(
    raw: str,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> dict[str, Any]:
    """Parsi ja normalisoi VLM:n JSON-vastaus (kestää markdown-koodilohkot)."""
    cleaned = _THINK_TAG_RE.sub("", raw or "").strip()
    fence = _CODE_FENCE_RE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    data: dict[str, Any] | None = None
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError:
        # Etsi ensimmäinen {...}-lohko tekstin seasta
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                data = None

    if data is None:
        raise VisionError(
            f"VLM returned invalid JSON: {cleaned[:200]!r}",
            code="invalid_response",
        )

    found = bool(data.get("found", False))
    raw_x = data.get("x")
    raw_y = data.get("y")
    x: float | None = None
    y: float | None = None

    if found:
        try:
            if raw_x is None or raw_y is None:
                found = False
            else:
                x, y = _normalize_coords(
                    float(raw_x),
                    float(raw_y),
                    image_width=image_width,
                    image_height=image_height,
                )
        except (TypeError, ValueError):
            found = False
            x = None
            y = None

    raw_conf = str(data.get("confidence") or "").strip().lower()
    if raw_conf not in ("high", "medium", "low"):
        confidence = "medium" if found else "low"
    else:
        confidence = raw_conf

    reason = str(data.get("reason") or "").strip()
    return {
        "found": found,
        "x": x,
        "y": y,
        "confidence": confidence,
        "reason": reason,
    }


def locate_object_in_image(
    image_path: Path | str,
    query: str,
    *,
    client: httpx.Client | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Paikanna kohde still-kuvasta paikallisella Ollama VLM -mallilla.

    Palauttaa sanakirjan:
        {"found": bool, "x": float|None, "y": float|None,
         "confidence": "high"|"medium"|"low", "reason": str}
    """
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"still frame not found: {path}")
    target_query = (query or "").strip()
    if not target_query:
        raise ValueError("query must not be empty")

    model_name = model or vlm_model_name()
    raw_bytes = path.read_bytes()
    img_w, img_h = _read_image_size(raw_bytes)
    b64_image = base64.b64encode(raw_bytes).decode("ascii")

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": _VLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Locate this object or UI element in the video frame: "
                    f"{target_query}"
                ),
                "images": [b64_image],
            },
        ],
        "stream": False,
        "format": "json",
        "keep_alive": vlm_keep_alive(),
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
            "num_predict": 160,
        },
    }

    owns_client = client is None
    http = client or httpx.Client(timeout=120.0)
    try:
        try:
            response = http.post(f"{_ollama_base()}/api/chat", json=payload)
        except httpx.RequestError as exc:
            raise VisionError(
                f"Ollama is not reachable at {_ollama_base()}: {exc}",
                code="unreachable",
            ) from exc

        if response.status_code == 404:
            raise VisionError(
                f"Vision model '{model_name}' not found in Ollama. "
                f"Install it with: ollama pull {model_name}",
                code="model_missing",
            )
        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            lowered = detail.lower()
            if "not found" in lowered or "pull" in lowered:
                raise VisionError(
                    f"Vision model '{model_name}' not found in Ollama. "
                    f"Install it with: ollama pull {model_name}",
                    code="model_missing",
                )
            raise VisionError(
                f"Vision model request failed: {detail}",
                code="failed",
            )

        data = response.json()
        content = (data.get("message") or {}).get("content") or ""
        return parse_vision_json(content, image_width=img_w, image_height=img_h)
    finally:
        if owns_client:
            http.close()
