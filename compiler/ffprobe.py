"""FFprobe-kääre videotiedoston metadatalle (LC_ALL=C)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return env


def probe(path: str) -> dict[str, Any]:
    """Lue videotiedoston kesto, resoluutio, FPS ja ääniraidan olemassaolo.

    Palauttaa sanakirjan:
      duration, width, height, fps, has_audio
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffprobe failed for {path!r}: {detail}")

    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    format_info = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"no video stream in {path!r}")

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = int(video["width"])
    height = int(video["height"])
    fps = _parse_fps(video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/0")

    duration = _parse_duration(video, format_info)
    if duration <= 0:
        raise RuntimeError(f"could not determine duration for {path!r}")

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": audio is not None,
    }


def _parse_fps(rate: str) -> float:
    if "/" in rate:
        num_s, den_s = rate.split("/", 1)
        num, den = float(num_s), float(den_s)
        if den == 0:
            return 30.0
        return num / den
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


def _parse_duration(video: dict[str, Any], format_info: dict[str, Any]) -> float:
    for candidate in (
        video.get("duration"),
        format_info.get("duration"),
    ):
        if candidate is None:
            continue
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value

    # Fallback: nb_frames / fps
    nb_frames = video.get("nb_frames")
    fps = _parse_fps(video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/0")
    if nb_frames is not None and fps > 0:
        try:
            return float(nb_frames) / fps
        except (TypeError, ValueError):
            pass
    return 0.0
