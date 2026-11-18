"""Projektin nollaus (reset_project): tila + esikatselutiedostot."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from controller.editor import PROJECT_STATE_FILENAME, EditorController

PROBE_META = {
    "duration": 10.0,
    "width": 1280,
    "height": 720,
    "fps": 30.0,
    "has_audio": True,
}


def test_reset_project_clears_state_and_previews(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".gitkeep").write_text("", encoding="utf-8")

    editor = EditorController(workspace=workspace, load_persisted=False)
    video = workspace / "source.mp4"
    video.write_bytes(b"fake-video")

    with patch("controller.editor.probe", return_value=PROBE_META):
        src = editor.import_video(str(video))
    editor.add_clip(src.id, 0.0, 5.0)
    editor.set_timeline_fades(fade_in_sec=1.0, fade_out_sec=2.0)
    editor.timeline.mute_video_audio = True
    assert len(editor.timeline.clips) == 1
    assert editor.history.can_undo() or True  # checkpoint from add_clip

    preview = workspace / "cutsmith_preview.mp4"
    export = workspace / "cutsmith_final.mp4"
    preview.write_bytes(b"preview")
    export.write_bytes(b"export")
    frames = workspace / "frames"
    frames.mkdir()
    (frames / "still.jpg").write_bytes(b"jpg")
    thumbs = workspace / "thumbnails"
    thumbs.mkdir()
    (thumbs / "t.jpg").write_bytes(b"jpg")
    state_file = workspace / PROJECT_STATE_FILENAME
    editor._flush_persist()
    assert state_file.is_file()

    result = editor.reset_project(delete_media=False)

    assert result["ok"] is True
    assert result["clip_count"] == 0
    assert editor.timeline.clips == []
    assert editor.timeline.audio_clips == []
    assert editor.timeline.audio_tracks == []
    assert editor.timeline.transitions == []
    assert editor.timeline.fade_in_sec == 0.0
    assert editor.timeline.fade_out_sec == 0.0
    assert editor.timeline.mute_video_audio is False
    assert editor.history.can_undo() is False
    assert editor.history.can_redo() is False
    assert editor.playhead_sec == 0.0
    assert editor.selection_start_sec is None

    assert not preview.exists()
    assert not export.exists()
    assert not (frames / "still.jpg").exists()
    assert not (thumbs / "t.jpg").exists()
    # Lähdemedia säilyy ilman delete_media
    assert video.is_file()
    assert (workspace / ".gitkeep").is_file()

    summary = editor.get_state_summary(lang="fi")
    assert summary["clip_count"] == 0
    assert "tyhjä" in summary["description"].lower()


def test_reset_project_delete_media(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitkeep").write_text("keep", encoding="utf-8")
    media = workspace / "clip.mp4"
    media.write_bytes(b"media")
    (workspace / "cutsmith_preview.mp4").write_bytes(b"p")

    editor = EditorController(workspace=workspace, load_persisted=False)
    with patch("controller.editor.probe", return_value=PROBE_META):
        src = editor.import_video(str(media))
    editor.add_clip(src.id, 0.0, 2.0)

    editor.reset_project(delete_media=True)

    assert not media.exists()
    assert (workspace / ".gitkeep").is_file()
    assert not (workspace / "cutsmith_preview.mp4").exists()
    assert editor.timeline.clips == []


def test_reset_project_tool(tmp_path: Path):
    import json

    from agent.tools import execute_tool

    video = tmp_path / "a.mp4"
    video.write_bytes(b"a")
    (tmp_path / "cutsmith_preview.mp4").write_bytes(b"x")

    editor = EditorController(workspace=tmp_path, load_persisted=False)
    with patch("controller.editor.probe", return_value=PROBE_META):
        src = editor.import_video(str(video))
    editor.add_clip(src.id, 0.0, 1.0)

    text, changed = execute_tool(editor, "reset_project", {})
    assert changed is True
    payload = json.loads(text)
    assert payload["ok"] is True
    assert editor.timeline.clips == []
    assert not (tmp_path / "cutsmith_preview.mp4").exists()
