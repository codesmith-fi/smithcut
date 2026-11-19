"""Paikallinen Ollama/Qwen-agentti tool calling -tuella."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

from agent.tools import OLLAMA_TOOLS, execute_tool
from controller.editor import EditorController, normalize_lang

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)
_CLOSE_THINK_RE = re.compile(r"</think>", re.IGNORECASE)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"
KEEP_ALIVE = "5m"
DEFAULT_NUM_CTX = 8192  # Estää 40k kontekstin vuotamisen CPU:lle 8 Gt VRAM -kortilla
MAX_TOOL_ROUNDS = 8
MAX_HISTORY_MESSAGES = 10  # 5 viimeisintä käyttäjä/assistentti-vuoroa

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
        "done": "Muutos tehty.",
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
        "done": "Done.",
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


def strip_think_tags(reply: str) -> str:
    """Poista Qwen3:n <think>...</think> -päättelylohkot (myös sulkemattomat)."""
    text = reply or ""
    # 1) Poista täydet <think>...</think> -lohkot
    text = _THINK_BLOCK_RE.sub("", text)
    # 2) Jos pelkkä </think> jäi (esim. avaustagi puuttui), ota teksti sen jälkeen
    if _CLOSE_THINK_RE.search(text):
        parts = _CLOSE_THINK_RE.split(text)
        after = parts[-1].strip()
        if after:
            text = after
        else:
            text = "".join(parts[:-1])
    # 3) Poista sulkematta jäänyt <think>... loppuun asti
    text = _OPEN_THINK_RE.sub("", text)
    return text.strip()


def _model_name() -> str:
    return os.environ.get("CUTSMITH_MODEL", DEFAULT_MODEL)


def _keep_alive() -> str:
    return os.environ.get("CUTSMITH_KEEP_ALIVE", KEEP_ALIVE)


def _num_ctx() -> int:
    raw = os.environ.get("CUTSMITH_NUM_CTX")
    if raw:
        try:
            val = int(raw)
            if val >= 2048:
                return val
        except ValueError:
            pass
    return DEFAULT_NUM_CTX


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

    playhead = state_summary.get("playhead_sec", 0.0)
    playhead_tc = state_summary.get("playhead_timecode", "00:00.0")
    selection_line = _selection_context_line(state_summary, lang=lang)
    return (
        "/no_think\n"
        "You are CutSmith, a friendly local video-editing assistant.\n"
        f"{language_line}\n"
        "Use tools to edit the timeline. Clip and transition indices are "
        "1-based (the first clip is 1; after_clip=1 is between clip 1 and 2).\n"
        f"The timeline playhead (marker) is currently at {playhead_tc} "
        f"({playhead} s). When the user asks to cut from the playhead / "
        "timeline marker (e.g. 'Cut 5 seconds starting at the playhead' or "
        "'Leikkaa timeline markkerin valitusta kohdasta 10 sekuntia pois'), "
        "call cut_timeline_range with duration_sec and omit start_sec so it "
        "uses the playhead.\n"
        f"{selection_line}"
        "When the user refers to the painted/selected range "
        "('valittu alue', 'maalattu kohta', 'selected range') without numbers, "
        "call cut_timeline_range with no start_sec and no duration_sec so the "
        "active selection is used.\n"
        "When the user asks for a crossfade on the selected range "
        "('Tee timelinestä valitulle alueelle crossfade' / "
        "'Apply a crossfade to the selected range'), call "
        "apply_crossfade_to_selection (duration defaults to the selection).\n"
        "Crossfade without painting: if the user asks to take N seconds from "
        "the end of clip 1 and N seconds from the start of clip 2 "
        "(e.g. 'Valitse clip1 lopusta ja clip 2 alusta 5 sekuntia molemmista "
        "ja tee crossfade'), set duration_sec = 2*N (5+5=10.0) and call "
        "apply_crossfade_to_selection with clip_index=1, duration_sec=10.0 "
        "(clip_index is 1-based: 1 = seam between clip 1 and 2). "
        "If they say 'tee N sekunnin crossfade clippien 1 ja 2 välille' / "
        "'N second crossfade between clips 1 and 2', call with "
        "clip_index=1, duration_sec=N.\n"
        "Audio: 'Poista videosta ääniraita' → remove_video_audio(). "
        "'Lisää ääniraita 1 videoon' / 'Lisää klippinauhasta ääniraita 1 videoon' "
        "→ attach_audio_track(track_index=1). Audio track indices are 1-based.\n"
        "Timeline fades: 'Tee alkuun 5 sekunnin fade in ja feidaa lopussa pois "
        "10 sekunnin ajan' → set_timeline_fades(fade_in_sec=5, fade_out_sec=10). "
        "'Tee videon alkuun 5 sekunnin fade in ja videon loppuun sama 5 sekunnin "
        "fade out' → set_timeline_fades(fade_in_sec=5, fade_out_sec=5). "
        "'Lisää 3 sekunnin loppuhäivytys' → set_timeline_fades(fade_out_sec=3). "
        "Omit unused fade_* args so existing values stay unchanged.\n"
        "Separate audio timeline: 'Erota videosta ääni omalle raidalleen' → "
        "detach_audio_to_timeline(). After detach, edit audio independently: "
        "'Leikkaa ääniraidasta valittu alue pois' → cut_audio_range() (omit times "
        "to use selection); 'Poista ääni kohdasta 0:10–0:15' → "
        "cut_audio_range(start_sec=10, end_sec=15); "
        "'Siirrä ääniklippi 2 alkamaan kohdasta 0:08' → "
        "move_audio_clip(clip_index=2, new_start_sec=8); "
        "'Laske ääniraidan voimakkuus puoleen' → set_audio_clip_volume(volume=0.5). "
        "Audio clip indices (A1, A2, …) are 1-based. Video cuts do not move "
        "detached audio_clips unless the user asks.\n"
        "New project: 'Aloita uusi projekti' / 'Tyhjennä aikajana' / "
        "'Nollaa työtila' → reset_project(). Only pass delete_media=true if the "
        "user explicitly asks to delete imported source files too.\n"
        "When the user asks for a soft blur transition on the selected range "
        "('Tee valitulle alueelle pehmeä blur-siirtymä' / "
        "'Add a soft blur to the selected range'), call add_blur_to_selection.\n"
        "Visual targeting (VLM): when the user asks to zoom into or target a "
        "specific visible object, person, ship, text, or UI element in the video "
        "(e.g. 'zoomaa alukseen', 'zoomaa punaiseen ruusuun kohdassa 2:30', "
        "'zoom into the menu button'), call locate_object_in_frame(query=..., "
        "at_sec=...) FIRST to find its normalized coordinates. If found=true, "
        "pass the returned target_x and target_y directly to add_zoom. If "
        "found=false, explain clearly that the object was not found instead of "
        "guessing coordinates.\n"
        "Animated zoom: when the user asks for an incremental / gradual / smooth "
        "zoom over a range (e.g. 'inkrementaalinen zoomaus', 'asteittainen "
        "zoomaus', 'tasainen zoomaus välillä 0:40–0:45', 'smooth zoom in'), "
        "pass animated=true to add_zoom (combined with locate_object_in_frame "
        "if targeting a specific visible object).\n"
        "Time codes: interpret marks like 2:20 or 2.20 (when minute/second "
        "context is clear) as MM:SS → seconds (2*60+20 = 140.0 s). Do not treat "
        "2.20 as 2.2 decimal seconds in that case.\n"
        "Split: when the user asks to split the video/clip in two at a time "
        "('Jaa klippi kohdasta 2:20', 'Split at 1:05') or at the current marker "
        "('Jaa video tästä kohdasta', 'Split here'), call split_clip. "
        "split_at_sec is relative to the clip start: if the user gives a "
        "timeline time T, pick the clip whose timeline_start_sec..timeline_end_sec "
        "contains T and set split_at_sec = T - timeline_start_sec; if they say "
        "'tästä kohdasta' / 'here' / 'playhead', use the playhead time the same way.\n"
        "Batch all needed tool calls first; do not ask the user to preview — "
        "the app renders preview once after you finish.\n"
        "After tools finish, give a short plain-language confirmation of what changed.\n"
        "Current timeline summary (JSON):\n"
        f"{json.dumps(state_summary, ensure_ascii=False)}"
    )


def _selection_context_line(state_summary: dict[str, Any], *, lang: str = "fi") -> str:
    """Muotoile valintaväli system-prompttiin (tyhjä jos ei valintaa)."""
    start = state_summary.get("selection_start_sec")
    end = state_summary.get("selection_end_sec")
    if start is None or end is None:
        return ""
    duration = state_summary.get("selection_duration_sec")
    if duration is None:
        duration = float(end) - float(start)
    tc = state_summary.get("selection_timecode")
    if not tc:
        from controller.editor import format_timecode

        tc = f"{format_timecode(float(start))} – {format_timecode(float(end))}"
    if lang == "en":
        return (
            f"Selected range: {tc} (duration {duration}s). "
            "Prefer this range when the user says 'selected range' / "
            "'painted area' without explicit times.\n"
        )
    return (
        f"Selected range: {tc} (duration {duration}s). "
        "Käytä tätä väliä kun käyttäjä sanoo 'valittu alue' / "
        "'maalattu kohta' ilman erillisiä lukuja.\n"
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
        "keep_alive": _keep_alive(),
        # Qwen3: kytke sisäinen ajatteluketju pois (nopeampi tool calling)
        "think": False,
        # Rajaa konteksti 8192 tokeniin → mahtuu 100 % 8 Gt VRAMiin (ei CPU-vuotoa)
        "options": {"num_ctx": _num_ctx()},
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
    history: list[dict[str, str]] | None = None,
) -> AgentResult:
    """Suorita yksi käyttäjäviesti tool calling -silmukalla."""
    lang = normalize_lang(lang)
    model = model or _model_name()
    strings = _ERROR_I18N[lang]
    user_text = message.strip()
    prior: list[dict[str, Any]] = []
    if history:
        for item in history[-MAX_HISTORY_MESSAGES:]:
            role = item.get("role")
            content_item = item.get("content")
            if role in ("user", "assistant") and isinstance(content_item, str):
                prior.append({"role": role, "content": content_item})
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": ""},
        *prior,
        {"role": "user", "content": user_text},
    ]

    owns_client = client is None
    http = client or httpx.Client(timeout=120.0)
    changed = False
    used_tools: list[str] = []

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            # Päivitä playhead + tila joka kierroksella
            messages[0] = {
                "role": "system",
                "content": _system_prompt(
                    lang, editor.get_state_summary(lang=lang)
                ),
            }
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

            content = strip_think_tags(msg.get("content") or "")
            if not content:
                return AgentResult(
                    reply=strings["done"] if changed else strings["empty"],
                    changed=changed,
                    ready=True,
                    tool_calls=used_tools,
                )
            if history is not None:
                if "reset_project" in used_tools:
                    history.clear()
                else:
                    history.append({"role": "user", "content": user_text})
                    history.append({"role": "assistant", "content": content})
                    if len(history) > MAX_HISTORY_MESSAGES:
                        del history[:-MAX_HISTORY_MESSAGES]
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
