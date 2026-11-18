"""Paikallinen Ollama/Qwen-agentti tool calling -tuella."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

from agent.tools import OLLAMA_TOOLS, execute_tool
from controller.editor import EditorController, normalize_lang

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"
KEEP_ALIVE = "5m"
MAX_TOOL_ROUNDS = 8

_ERROR_I18N = {
    "fi": {
        "unreachable": (
            "Paikallinen kielimalli ei vastaa. Käynnistä Ollama "
            "(http://localhost:11434) ja varmista että malli on asennettu."
        ),
        "model_missing": (
            "Kielimallia ei löydy. Asenna se komennolla: ollama pull {model}"
        ),
        "failed": "Kielimallin kutsu epäonnistui: {detail}",
        "empty": "En saanut vastausta kielimallilta. Kokeile uudelleen.",
    },
    "en": {
        "unreachable": (
            "The local language model is not responding. Start Ollama "
            "(http://localhost:11434) and make sure the model is installed."
        ),
        "model_missing": (
            "Language model not found. Install it with: ollama pull {model}"
        ),
        "failed": "Language model request failed: {detail}",
        "empty": "No reply from the language model. Please try again.",
    },
}


@dataclass
class AgentResult:
    reply: str
    changed: bool = False
    ready: bool = True
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None


class AgentError(Exception):
    def __init__(self, message: str, *, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _model_name() -> str:
    return os.environ.get("CUTSMITH_MODEL", DEFAULT_MODEL)


def _ollama_base() -> str:
    return (
        os.environ.get("OLLAMA_HOST")
        or os.environ.get("CUTSMITH_OLLAMA_URL")
        or DEFAULT_OLLAMA_URL
    ).rstrip("/")


def _system_prompt(lang: str, state_summary: dict[str, Any]) -> str:
    lang = normalize_lang(lang)
    if lang == "en":
        language_line = (
            "Always reply in clear, concise English. No technical jargon, "
            "no FFmpeg talk, no JSON dumps."
        )
    else:
        language_line = (
            "Vastaa aina selkeällä, tiiviillä suomella. Ei teknistä jargonia, "
            "ei FFmpeg-puhetta, ei JSON-dumppia."
        )

    return (
        "/no_think\n"
        "You are CutSmith, a friendly local video-editing assistant.\n"
        f"{language_line}\n"
        "Use tools to edit the timeline. Clip and transition indices are "
        "1-based (the first clip is 1; after_clip=1 is between clip 1 and 2).\n"
        "Batch all needed tool calls first; do not ask the user to preview — "
        "the app renders preview once after you finish.\n"
        "After tools finish, give a short plain-language confirmation of what changed.\n"
        "Current timeline summary (JSON):\n"
        f"{json.dumps(state_summary, ensure_ascii=False)}"
    )


def _parse_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls") or []
    parsed: list[dict[str, Any]] = []
    for call in calls:
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name")
        raw_args = fn.get("arguments", call.get("arguments", {}))
        if isinstance(raw_args, str):
            try:
                arguments = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            arguments = {}
        if name:
            parsed.append(
                {
                    "id": call.get("id") or name,
                    "name": name,
                    "arguments": arguments,
                }
            )
    return parsed


def _chat_once(
    client: httpx.Client,
    *,
    model: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "tools": OLLAMA_TOOLS,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        # Qwen3: kytke sisäinen ajatteluketju pois (nopeampi tool calling)
        "think": False,
    }
    try:
        response = client.post(f"{_ollama_base()}/api/chat", json=payload)
    except httpx.RequestError as exc:
        raise AgentError(str(exc), code="unreachable") from exc

    if response.status_code == 404:
        raise AgentError("model missing", code="model_missing")
    if response.status_code >= 400:
        detail = response.text.strip() or f"HTTP {response.status_code}"
        lowered = detail.lower()
        if "not found" in lowered or "pull" in lowered:
            raise AgentError(detail, code="model_missing")
        raise AgentError(detail, code="failed")

    data = response.json()
    return data


def run_agent(
    editor: EditorController,
    message: str,
    *,
    lang: str = "fi",
    client: httpx.Client | None = None,
    model: str | None = None,
) -> AgentResult:
    """Suorita yksi käyttäjäviesti tool calling -silmukalla."""
    lang = normalize_lang(lang)
    model = model or _model_name()
    strings = _ERROR_I18N[lang]
    state = editor.get_state_summary(lang=lang)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(lang, state)},
        {"role": "user", "content": message.strip()},
    ]

    owns_client = client is None
    http = client or httpx.Client(timeout=120.0)
    changed = False
    used_tools: list[str] = []

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            data = _chat_once(http, model=model, messages=messages)
            msg = data.get("message") or {}
            tool_calls = _parse_tool_calls(msg)

            if tool_calls:
                messages.append(msg)
                for call in tool_calls:
                    used_tools.append(call["name"])
                    result_text, did_change = execute_tool(
                        editor,
                        call["name"],
                        call["arguments"],
                        lang=lang,
                    )
                    changed = changed or did_change
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": call["name"],
                            "content": result_text,
                        }
                    )
                continue

            content = (msg.get("content") or "").strip()
            if not content:
                return AgentResult(
                    reply=strings["empty"],
                    changed=changed,
                    ready=True,
                    tool_calls=used_tools,
                )
            return AgentResult(
                reply=content,
                changed=changed,
                ready=True,
                tool_calls=used_tools,
            )

        return AgentResult(
            reply=strings["empty"],
            changed=changed,
            ready=True,
            tool_calls=used_tools,
        )
    except AgentError as exc:
        if exc.code == "unreachable":
            reply = strings["unreachable"]
        elif exc.code == "model_missing":
            reply = strings["model_missing"].format(model=model)
        else:
            reply = strings["failed"].format(detail=exc.message)
        return AgentResult(
            reply=reply,
            changed=changed,
            ready=False,
            tool_calls=used_tools,
            error=exc.code,
        )
    finally:
        if owns_client:
            http.close()
