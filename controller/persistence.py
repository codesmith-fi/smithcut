"""Projektitilan atominen tallennus/lataus ja workspace-siivous."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from model.timeline import Timeline, VideoSource

PROJECT_STATE_FILENAME = "project_state.json"
PROJECT_STATE_VERSION = 1

_RENDER_GLOBS = (
    "preview*.mp4",
    "export*.mp4",
    "final*.mp4",
    "cutsmith_*.mp4",
)
_TEMP_SUBDIRS = ("frames", "thumbnails", "audio")
_MEDIA_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".mp3",
    ".wav",
    ".aac",
    ".m4a",
    ".ogg",
    ".flac",
}


@dataclass
class LoadedProjectState:
    timeline: Timeline
    playhead_sec: float
    selection_start_sec: float | None
    selection_end_sec: float | None


def project_state_path(workspace: Path) -> Path:
    return Path(workspace) / PROJECT_STATE_FILENAME


def save_project_state(
    workspace: Path,
    timeline: Timeline,
    *,
    playhead_sec: float,
    selection_start_sec: float | None,
    selection_end_sec: float | None,
) -> bool:
    """Tallenna projektin tila atomisesti project_state.json -tiedostoon."""
    path = project_state_path(workspace)
    payload = {
        "version": PROJECT_STATE_VERSION,
        "playhead_sec": playhead_sec,
        "selection_start_sec": selection_start_sec,
        "selection_end_sec": selection_end_sec,
        "timeline": timeline.model_dump(mode="json"),
    }
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_project_state(workspace: Path) -> LoadedProjectState | None:
    """Lataa tila levyltä jos lähdevideot ovat yhä olemassa."""
    path = project_state_path(workspace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_tl = data.get("timeline") or {}
        timeline = Timeline.model_validate(raw_tl)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None

    kept_sources: dict[str, VideoSource] = {
        sid: source
        for sid, source in timeline.sources.items()
        if Path(source.path).is_file()
    }
    if not kept_sources and timeline.sources:
        return None

    kept_clips = [c for c in timeline.clips if c.source_id in kept_sources]
    n = len(kept_clips)
    kept_transitions = [
        t for t in timeline.transitions if 0 <= t.after_clip_index < n - 1
    ]
    bgm = timeline.bgm
    if bgm is not None and not Path(bgm.path).is_file():
        bgm = None

    kept_audio = [t for t in timeline.audio_tracks if Path(t.path).is_file()]
    active_id = timeline.active_audio_track_id
    if active_id and not any(t.id == active_id for t in kept_audio):
        active_id = None
    kept_audio_clips = [
        c for c in timeline.audio_clips if Path(c.source_path).is_file()
    ]

    cleaned = Timeline(
        sources=kept_sources,
        clips=kept_clips,
        transitions=kept_transitions,
        bgm=bgm,
        mute_video_audio=timeline.mute_video_audio,
        audio_tracks=kept_audio,
        active_audio_track_id=active_id,
        active_audio_start_sec=timeline.active_audio_start_sec,
        audio_clips=kept_audio_clips,
        fade_in_sec=timeline.fade_in_sec,
        fade_out_sec=timeline.fade_out_sec,
        fade_audio=timeline.fade_audio,
        output_fps=timeline.output_fps,
    )

    try:
        playhead_sec = float(data.get("playhead_sec") or 0.0)
    except (TypeError, ValueError):
        playhead_sec = 0.0
    start = data.get("selection_start_sec")
    end = data.get("selection_end_sec")
    try:
        sel_start = float(start) if start is not None else None
        sel_end = float(end) if end is not None else None
    except (TypeError, ValueError):
        sel_start = None
        sel_end = None

    return LoadedProjectState(
        timeline=cleaned,
        playhead_sec=playhead_sec,
        selection_start_sec=sel_start,
        selection_end_sec=sel_end,
    )


def cleanup_workspace(workspace: Path, *, delete_media: bool = False) -> list[str]:
    """Poista renderöidyt videot, väliaikaiskansiot, tilatiedosto ja valinnaisesti media."""
    removed: list[str] = []
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    for pattern in _RENDER_GLOBS:
        for path in ws.glob(pattern):
            if path.is_file():
                try:
                    path.unlink()
                    removed.append(path.name)
                except OSError:
                    pass

    for sub in _TEMP_SUBDIRS:
        folder = ws / sub
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.name == ".gitkeep":
                continue
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink()
                    removed.append(f"{sub}/{path.name}")
            except OSError:
                pass

    state_path = project_state_path(ws)
    if state_path.is_file():
        try:
            state_path.unlink()
            removed.append(PROJECT_STATE_FILENAME)
        except OSError:
            pass

    if delete_media:
        for path in ws.iterdir():
            if path.name == ".gitkeep":
                continue
            if path.is_file() and path.suffix.lower() in _MEDIA_EXTENSIONS:
                try:
                    path.unlink()
                    removed.append(path.name)
                except OSError:
                    pass

    return removed
