"""CutSmith FastAPI-näkymä: vain EditorController-rajapinnan kautta."""

from __future__ import annotations

import mimetypes
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from controller.editor import EditorController, chat_stub_reply, normalize_lang

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_WORKSPACE = ROOT / "workspace"
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 Mt striimauspala

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
    action: Literal["zoom", "text", "transition", "remove_transition"]
    clip_index: Optional[int] = Field(default=None, ge=1)
    after_clip: Optional[int] = Field(default=None, ge=1)
    start_sec: float = 0.0
    duration: float = 1.0
    region: str = "center"
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    zoom_factor: float = 1.8
    animated: bool = False
    text: Optional[str] = None
    position: str = "bottom"
    font_style: Literal["clean", "cinema", "mono", "impact"] = "clean"
    transition_type: str = "fadeblack"


class RenderRequest(BaseModel):
    mode: Literal["preview", "final", "export"] = "preview"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    lang: Literal["fi", "en"] = "fi"


class PlayheadRequest(BaseModel):
    sec: float = Field(ge=0)


class SelectionRequest(BaseModel):
    start_sec: float | None = None
    end_sec: float | None = None


class CutRangeRequest(BaseModel):
    start_sec: float | None = None
    end_sec: float | None = None
    include_audio: bool = True


class AudioRequest(BaseModel):
    action: Literal[
        "detach",
        "split",
        "cut_range",
        "move",
        "volume",
        "remove",
    ]
    timestamp_sec: Optional[float] = None
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None
    clip_index: Optional[int] = Field(default=None, ge=1)
    new_start_sec: Optional[float] = None
    volume: Optional[float] = None
    ripple: bool = False


class ResetProjectRequest(BaseModel):
    delete_media: bool = False


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
    # Lähdevideoiden /media/-URL:t → soittimelle heti tuonnin jälkeen
    # (ennen kuin cutsmith_preview.mp4 on valmis)
    for source in summary.get("sources") or []:
        path = source.get("path")
        source["url"] = _media_url(workspace, Path(path)) if path else None
    for clip in summary.get("clips") or []:
        path = clip.get("source_path")
        clip["source_url"] = _media_url(workspace, Path(path)) if path else None
    if not summary["preview_url"]:
        for clip in summary.get("clips") or []:
            if clip.get("source_url"):
                summary["preview_url"] = clip["source_url"]
                break
        if not summary["preview_url"]:
            for source in summary.get("sources") or []:
                if source.get("url"):
                    summary["preview_url"] = source["url"]
                    break
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
    app.state.chat_history: list[dict[str, str]] = []
    app.state.chat_lock = threading.Lock()

    @app.middleware("http")
    async def _no_cache_static(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in ("/", "/index.html") or path.endswith((".css", ".js")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    def get_editor() -> EditorController:
        return app.state.editor

    @app.get("/api/state")
    def api_state(lang: str = Query(default="fi")) -> dict[str, Any]:
        code = _lang_from_request(lang)
        editor = get_editor()
        return {
            "ok": True,
            "state": _state_payload(editor, lang=code),
            "progress": editor.get_progress(),
        }

    @app.get("/api/timeline")
    def api_timeline(lang: str = Query(default="fi")) -> dict[str, Any]:
        """Aikajanan tila playhead mukaan lukien (UI + agentti)."""
        code = _lang_from_request(lang)
        editor = get_editor()
        return {
            "ok": True,
            "state": _state_payload(editor, lang=code),
        }

    @app.post("/api/timeline/playhead", response_model=None)
    def api_set_playhead(body: PlayheadRequest, lang: str = Query(default="fi")):
        editor = get_editor()
        code = _lang_from_request(lang)
        try:
            sec = editor.set_playhead(body.sec)
            return {
                "ok": True,
                "playhead_sec": sec,
                "state": _state_payload(editor, lang=code),
            }
        except (ValueError, IndexError) as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, status=500)

    @app.post("/api/timeline/selection", response_model=None)
    def api_set_selection(body: SelectionRequest, lang: str = Query(default="fi")):
        editor = get_editor()
        code = _lang_from_request(lang)
        try:
            start, end = editor.set_selection(body.start_sec, body.end_sec)
            return {
                "ok": True,
                "selection_start_sec": start,
                "selection_end_sec": end,
                "state": _state_payload(editor, lang=code),
            }
        except (ValueError, IndexError) as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, status=500)

    @app.post("/api/timeline/cut-range", response_model=None)
    def api_cut_timeline_range(
        body: CutRangeRequest | None = None,
        lang: str = Query(default="fi"),
    ):
        """Poista valittu tai annettu aikaväli (video + valinnaisesti erotettu ääni)."""
        editor = get_editor()
        code = _lang_from_request(lang)
        req = body or CutRangeRequest()
        try:
            if req.start_sec is None and req.end_sec is None:
                editor.cut_selected_range(include_audio=req.include_audio)
            elif req.start_sec is None or req.end_sec is None:
                raise ValueError("provide both start_sec and end_sec, or omit both")
            else:
                duration = float(req.end_sec) - float(req.start_sec)
                editor.cut_out_range(
                    float(req.start_sec),
                    duration,
                    include_audio=req.include_audio,
                )
                editor.clear_selection()
            return {"ok": True, "state": _state_payload(editor, lang=code)}
        except (ValueError, IndexError) as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, status=500)

    @app.get("/api/timeline/frame")
    def api_timeline_frame(t: float = Query(..., ge=0)):
        """Still-JPEG aikajanan kohdasta t (sekunteina)."""
        editor = get_editor()
        try:
            path = editor.get_frame_at_time(t)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileResponse(
            path=path,
            media_type="image/jpeg",
            filename=path.name,
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        """Kevyt edistymistilapollaus (ei levyoperaatioita)."""
        return {"ok": True, "progress": get_editor().get_progress()}

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

        total_bytes = 0
        try:
            with dest.open("wb") as out_fh:
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    out_fh.write(chunk)
        except Exception:
            dest.unlink(missing_ok=True)
            raise

        if total_bytes == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Empty file")

        try:
            if inferred == "music":
                music = await run_in_threadpool(editor.set_music, str(dest))
                state = await run_in_threadpool(_state_payload, editor, code)
                return {
                    "ok": True,
                    "kind": "music",
                    "music": music.model_dump(),
                    "state": state,
                }

            def _import_and_add():
                source = editor.import_video(str(dest))
                # Automaattisesti koko lähde klipiksi – ei-insinöörin UX
                clip = editor.add_clip(source.id, 0.0, source.duration)
                return source, clip, _state_payload(editor, lang=code)

            source, clip, state = await run_in_threadpool(_import_and_add)
            return {
                "ok": True,
                "kind": "video",
                "source": source.model_dump(),
                "clip": clip.model_dump(),
                "state": state,
            }
        except Exception as exc:  # noqa: BLE001 – rajapinta palauttaa viestin
            dest.unlink(missing_ok=True)
            return _error(exc)

    @app.get("/api/clips/{index}/thumbnail")
    def api_clip_thumbnail(index: int, v: float | None = Query(default=None)):
        """Palauta klipin aloitusframen JPEG (1-pohjainen indeksi)."""
        editor = get_editor()
        try:
            path = editor.get_clip_thumbnail(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        headers = {"Cache-Control": "no-cache"}
        if v is not None:
            headers["X-Trim-Start"] = str(v)
        return FileResponse(
            path=path,
            media_type="image/jpeg",
            filename=path.name,
            headers=headers,
        )

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

    @app.post("/api/audio", response_model=None)
    def api_audio(body: AudioRequest, lang: str = Query(default="fi")):
        editor = get_editor()
        code = _lang_from_request(lang)
        try:
            if body.action == "detach":
                editor.detach_audio_to_timeline()
            elif body.action == "split":
                if body.timestamp_sec is None:
                    raise ValueError("split requires timestamp_sec")
                editor.split_audio_clip(body.timestamp_sec)
            elif body.action == "cut_range":
                if body.start_sec is None or body.end_sec is None:
                    raise ValueError("cut_range requires start_sec and end_sec")
                editor.cut_audio_range(
                    body.start_sec, body.end_sec, ripple=body.ripple
                )
            elif body.action == "move":
                if body.clip_index is None or body.new_start_sec is None:
                    raise ValueError("move requires clip_index and new_start_sec")
                editor.move_audio_clip(body.clip_index, body.new_start_sec)
            elif body.action == "volume":
                if body.volume is None:
                    raise ValueError("volume requires volume")
                editor.set_audio_clip_volume(
                    clip_index=body.clip_index, volume=body.volume
                )
            elif body.action == "remove":
                if body.clip_index is None:
                    raise ValueError("remove requires clip_index")
                editor.remove_audio_clip(body.clip_index)
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
                    animated=body.animated,
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
            elif body.action == "remove_transition":
                if body.after_clip is None:
                    raise ValueError("remove_transition requires after_clip")
                editor.remove_transition(_one_to_zero(body.after_clip))
            return {"ok": True, "state": _state_payload(editor, lang=code)}
        except (ValueError, IndexError) as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc, status=500)

    @app.post("/api/project/reset", response_model=None)
    def api_project_reset(
        body: ResetProjectRequest | None = None,
        lang: str = Query(default="fi"),
        delete_media: bool = Query(default=False),
    ):
        editor = get_editor()
        code = _lang_from_request(lang)
        req = body or ResetProjectRequest()
        flag = bool(req.delete_media or delete_media)
        try:
            result = editor.reset_project(delete_media=flag)
            with app.state.chat_lock:
                app.state.chat_history.clear()
            return {
                "ok": True,
                "result": result,
                "state": _state_payload(editor, lang=code),
            }
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
        editor.set_progress(
            active=True,
            phase="chat",
            percent=None,
            detail="Processing command (Qwen3)",
        )
        try:
            with app.state.chat_lock:
                result = run_agent(
                    editor,
                    body.message,
                    lang=code,
                    history=app.state.chat_history,
                )
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
                "progress": editor.get_progress(),
                # Taaksepäin yhteensopiva stub-kenttä (Vaihe 2 testit)
                "fallback_hint": None if result.ready else chat_stub_reply(code),
            }
        finally:
            editor.clear_progress()

    @app.get("/media/{file_path:path}")
    async def serve_media(file_path: str, request: Request) -> FileResponse:
        workspace = Path(get_editor().workspace).resolve()
        target = (workspace / file_path).resolve()
        if not target.is_relative_to(workspace):
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
