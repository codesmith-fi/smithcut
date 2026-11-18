"""CutSmith FastAPI-näkymä: vain EditorController-rajapinnan kautta."""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from controller.editor import EditorController, chat_stub_reply, normalize_lang

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_WORKSPACE = ROOT / "workspace"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}


# ---------------------------------------------------------------------------
# Request-mallit
# ---------------------------------------------------------------------------


class ClipRequest(BaseModel):
    action: Literal["add", "trim", "split", "remove", "move"]
    # 1-pohjaiset indeksit UI:lle
    clip_index: Optional[int] = Field(default=None, ge=1)
    source_id: Optional[str] = None
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None
    split_at_sec: Optional[float] = None
    insert_at: Optional[int] = Field(default=None, ge=1)
    from_index: Optional[int] = Field(default=None, ge=1)
    to_index: Optional[int] = Field(default=None, ge=1)


class EffectRequest(BaseModel):
    action: Literal["zoom", "text", "transition"]
    clip_index: Optional[int] = Field(default=None, ge=1)
    after_clip: Optional[int] = Field(default=None, ge=1)
    start_sec: float = 0.0
    duration: float = 1.0
    region: str = "center"
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    zoom_factor: float = 1.8
    text: Optional[str] = None
    position: str = "bottom"
    font_style: Literal["clean", "cinema", "mono", "impact"] = "clean"
    transition_type: str = "fadeblack"


class RenderRequest(BaseModel):
    mode: Literal["preview", "final"] = "preview"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    lang: Literal["fi", "en"] = "fi"


# ---------------------------------------------------------------------------
# Apurit
# ---------------------------------------------------------------------------


def _one_to_zero(index: int) -> int:
    return index - 1


def _safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)
    return cleaned or f"upload_{uuid.uuid4().hex[:8]}"


def _media_url(workspace: Path, path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return None
    return f"/media/{rel.as_posix()}"


def _state_payload(editor: EditorController, lang: str = "fi") -> dict[str, Any]:
    summary = editor.get_state_summary(lang=lang)
    workspace = Path(editor.workspace)
    preview = workspace / "cutsmith_preview.mp4"
    final = workspace / "cutsmith_final.mp4"
    summary["preview_url"] = (
        _media_url(workspace, preview) if preview.is_file() else None
    )
    summary["final_url"] = _media_url(workspace, final) if final.is_file() else None
    summary["can_undo"] = editor.history.can_undo()
    summary["can_redo"] = editor.history.can_redo()
    return summary


def _lang_from_request(
    lang: str | None = None,
    *,
    default: str = "fi",
) -> str:
    return normalize_lang(lang or default)


def _error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)


# ---------------------------------------------------------------------------
# Sovellus
# ---------------------------------------------------------------------------


def create_app(
    workspace: Path | str | None = None,
    editor: EditorController | None = None,
) -> FastAPI:
    ws = Path(workspace) if workspace else DEFAULT_WORKSPACE
    ws.mkdir(parents=True, exist_ok=True)
    ctrl = editor or EditorController(workspace=ws)

    app = FastAPI(title="CutSmith", version="0.2.0")
    app.state.editor = ctrl
    app.state.workspace = Path(ctrl.workspace)

    def get_editor() -> EditorController:
        return app.state.editor

    @app.get("/api/state")
    def api_state(lang: str = Query(default="fi")) -> dict[str, Any]:
        code = _lang_from_request(lang)
        return {"ok": True, "state": _state_payload(get_editor(), lang=code)}

    @app.post("/api/upload", response_model=None)
    async def api_upload(
        file: UploadFile = File(...),
        kind: str = Form(default="auto"),
        lang: str = Form(default="fi"),
    ):
        editor = get_editor()
        code = _lang_from_request(lang)
        workspace = Path(editor.workspace)
        original = file.filename or "upload.bin"
        safe = _safe_filename(original)
        stem = Path(safe).stem
        suffix = Path(safe).suffix.lower()
        dest = workspace / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty file")
        dest.write_bytes(data)

        inferred = kind
        if inferred == "auto":
            if suffix in AUDIO_EXTENSIONS:
                inferred = "music"
            elif suffix in VIDEO_EXTENSIONS:
                inferred = "video"
            else:
                ctype = (file.content_type or "").lower()
                if ctype.startswith("audio/"):
                    inferred = "music"
                elif ctype.startswith("video/"):
                    inferred = "video"
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Unknown file type – use video or audio",
                    )

        try:
            if inferred == "music":
                music = editor.set_music(str(dest))
                return {
                    "ok": True,
                    "kind": "music",
                    "music": music.model_dump(),
                    "state": _state_payload(editor, lang=code),
                }

            source = editor.import_video(str(dest))
            # Automaattisesti koko lähde klipiksi – ei-insinöörin UX
            clip = editor.add_clip(source.id, 0.0, source.duration)
            return {
                "ok": True,
                "kind": "video",
                "source": source.model_dump(),
                "clip": clip.model_dump(),
                "state": _state_payload(editor, lang=code),
            }
        except Exception as exc:  # noqa: BLE001 – rajapinta palauttaa viestin
            dest.unlink(missing_ok=True)
            return _error(exc)

    @app.post("/api/clips", response_model=None)
    def api_clips(body: ClipRequest, lang: str = Query(default="fi")):
        editor = get_editor()
        code = _lang_from_request(lang)
        try:
            if body.action == "add":
                if not body.source_id or body.start_sec is None or body.end_sec is None:
                    raise ValueError("add requires source_id, start_sec and end_sec")
                insert_at = (
                    _one_to_zero(body.insert_at) if body.insert_at is not None else None
                )
                editor.add_clip(
                    body.source_id, body.start_sec, body.end_sec, insert_at=insert_at
                )
            elif body.action == "trim":
                if body.clip_index is None or body.start_sec is None or body.end_sec is None:
                    raise ValueError("trim requires clip_index, start_sec and end_sec")
                editor.trim_clip(
                    _one_to_zero(body.clip_index), body.start_sec, body.end_sec
                )
            elif body.action == "split":
                if body.clip_index is None or body.split_at_sec is None:
                    raise ValueError("split requires clip_index and split_at_sec")
                editor.split_clip(_one_to_zero(body.clip_index), body.split_at_sec)
            elif body.action == "remove":
                if body.clip_index is None:
                    raise ValueError("remove requires clip_index")
                editor.remove_clip(_one_to_zero(body.clip_index))
            elif body.action == "move":
                if body.from_index is None or body.to_index is None:
                    raise ValueError("move requires from_index and to_index")
                editor.move_clip(
                    _one_to_zero(body.from_index), _one_to_zero(body.to_index)
                )
            return {"ok": True, "state": _state_payload(editor, lang=code)}
        except (ValueError, KeyError, IndexError) as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, status=500)

    @app.post("/api/effects", response_model=None)
    def api_effects(body: EffectRequest, lang: str = Query(default="fi")):
        editor = get_editor()
        code = _lang_from_request(lang)
        try:
            if body.action == "zoom":
                if body.clip_index is None:
                    raise ValueError("zoom requires clip_index")
                editor.add_zoom(
                    _one_to_zero(body.clip_index),
                    start_sec=body.start_sec,
                    duration=body.duration,
                    region=body.region,
                    target_x=body.target_x,
                    target_y=body.target_y,
                    zoom_factor=body.zoom_factor,
                )
            elif body.action == "text":
                if body.clip_index is None or not body.text:
                    raise ValueError("text requires clip_index and text")
                editor.add_text(
                    _one_to_zero(body.clip_index),
                    text=body.text,
                    start_sec=body.start_sec,
                    duration=body.duration,
                    position=body.position,
                    font_style=body.font_style,
                )
            elif body.action == "transition":
                if body.after_clip is None:
                    raise ValueError("transition requires after_clip")
                editor.set_transition(
                    _one_to_zero(body.after_clip),
                    transition_type=body.transition_type,
                    duration=body.duration,
                )
            return {"ok": True, "state": _state_payload(editor, lang=code)}
        except (ValueError, IndexError) as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, status=500)

    @app.post("/api/undo")
    def api_undo(lang: str = Query(default="fi")) -> dict[str, Any]:
        editor = get_editor()
        code = _lang_from_request(lang)
        changed = editor.undo()
        return {
            "ok": True,
            "changed": changed,
            "state": _state_payload(editor, lang=code),
        }

    @app.post("/api/redo")
    def api_redo(lang: str = Query(default="fi")) -> dict[str, Any]:
        editor = get_editor()
        code = _lang_from_request(lang)
        changed = editor.redo()
        return {
            "ok": True,
            "changed": changed,
            "state": _state_payload(editor, lang=code),
        }

    @app.post("/api/render", response_model=None)
    def api_render(body: RenderRequest, lang: str = Query(default="fi")):
        editor = get_editor()
        code = _lang_from_request(lang)
        try:
            path = editor.render(mode=body.mode)
            media = _media_url(Path(editor.workspace), Path(path))
            return {
                "ok": True,
                "mode": body.mode,
                "path": path,
                "url": media,
                "state": _state_payload(editor, lang=code),
            }
        except Exception as exc:  # noqa: BLE001
            return _error(exc, status=500)

    @app.post("/api/chat")
    def api_chat(body: ChatRequest) -> dict[str, Any]:
        from agent.llm_agent import run_agent

        code = _lang_from_request(body.lang)
        editor = get_editor()
        result = run_agent(editor, body.message, lang=code)
        return {
            "ok": True,
            "ready": result.ready,
            "lang": code,
            "reply": result.reply,
            "changed": result.changed,
            "tool_calls": result.tool_calls,
            "error": result.error,
            "echo": body.message.strip(),
            "state": _state_payload(editor, lang=code),
            # Taaksepäin yhteensopiva stub-kenttä (Vaihe 2 testit)
            "fallback_hint": None if result.ready else chat_stub_reply(code),
        }

    @app.get("/media/{file_path:path}")
    async def serve_media(file_path: str, request: Request) -> FileResponse:
        workspace = Path(get_editor().workspace).resolve()
        target = (workspace / file_path).resolve()
        if not str(target).startswith(str(workspace)):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        mime, _ = mimetypes.guess_type(str(target))
        # FileResponse tukee HTTP Range -pyyntöjä (HTML5 video seek)
        return FileResponse(
            path=target,
            media_type=mime or "application/octet-stream",
            filename=target.name,
        )

    if STATIC_DIR.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(STATIC_DIR), html=True),
            name="static",
        )

    return app


app = create_app()
