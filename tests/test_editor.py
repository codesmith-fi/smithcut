"""EditorController-operaatioiden testit (ffprobe mockattu)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from controller.editor import EditorController


PROBE_META = {
    "duration": 30.0,
    "width": 1920,
    "height": 1080,
    "fps": 60.0,
    "has_audio": True,
}


@pytest.fixture
def editor(tmp_path: Path) -> EditorController:
    return EditorController(workspace=tmp_path)


def _import(editor: EditorController, path: str = "/videos/a.mp4"):
    with patch("controller.editor.probe", return_value=PROBE_META):
        return editor.import_video(path)


def test_import_and_add_clip(editor: EditorController):
    src = _import(editor)
    assert src.id in editor.timeline.sources
    clip = editor.add_clip(src.id, 0.0, 5.0)
    assert len(editor.timeline.clips) == 1
    assert clip.in_sec == 0.0
    assert clip.out_sec == 5.0


def test_undo_redo_add_clip(editor: EditorController):
    src = _import(editor)
    editor.add_clip(src.id, 0.0, 4.0)
    assert len(editor.timeline.clips) == 1
    assert editor.undo() is True
    assert len(editor.timeline.clips) == 0
    assert editor.redo() is True
    assert len(editor.timeline.clips) == 1


def test_trim_split_remove(editor: EditorController):
    src = _import(editor)
    editor.add_clip(src.id, 0.0, 10.0)
    trimmed = editor.trim_clip(0, 1.0, 9.0)
    assert trimmed.in_sec == 1.0
    assert trimmed.out_sec == 9.0

    left, right = editor.split_clip(0, 4.0)
    assert len(editor.timeline.clips) == 2
    assert left.out_sec == pytest.approx(5.0)  # 1 + 4
    assert right.in_sec == pytest.approx(5.0)
    assert left.duration + right.duration == pytest.approx(8.0)

    editor.remove_clip(1)
    assert len(editor.timeline.clips) == 1


def test_set_transition_and_effects(editor: EditorController):
    src = _import(editor)
    editor.add_clip(src.id, 0.0, 5.0)
    editor.add_clip(src.id, 5.0, 10.0)
    tr = editor.set_transition(0, "wipeleft", 0.5)
    assert tr.type == "wipeleft"
    assert tr.after_clip_index == 0

    zoom = editor.add_zoom(0, 0.0, 1.0, region="center", zoom_factor=2.0)
    assert zoom.zoom_factor == 2.0
    assert zoom.target_x == 0.5

    text = editor.add_text(1, "Title", 0.0, 2.0, position="top")
    assert text.text == "Title"
    assert text.position == "top"


def test_set_music(editor: EditorController, tmp_path: Path):
    music = tmp_path / "bg.mp3"
    music.write_bytes(b"fake")
    bgm = editor.set_music(str(music), bgm_volume=0.7)
    assert editor.timeline.bgm is not None
    assert bgm.bgm_volume == 0.7


def test_get_state_summary_is_one_based(editor: EditorController):
    src = _import(editor)
    editor.add_clip(src.id, 0.0, 5.0)
    editor.add_clip(src.id, 5.0, 10.0)
    editor.set_transition(0, "fade", 0.4)
    editor.add_zoom(0, 0.0, 1.0)
    editor.add_text(1, "Hi", 0.0, 1.0)

    summary = editor.get_state_summary()
    assert summary["clip_count"] == 2
    assert summary["clips"][0]["index"] == 1
    assert summary["clips"][1]["index"] == 2
    assert summary["transitions"][0]["after_clip"] == 1
    assert summary["total_duration_sec"] == pytest.approx(9.6)
    assert summary["can_undo"] is True
    assert summary["can_redo"] is False
    assert any(e["type"] == "zoom" for e in summary["clips"][0]["effects"])
    assert any(e["type"] == "text" for e in summary["clips"][1]["effects"])


def test_render_delegates_to_ffmpeg(editor: EditorController, tmp_path: Path):
    src = _import(editor)
    editor.add_clip(src.id, 0.0, 3.0)
    out = tmp_path / "cutsmith_preview.mp4"
    with patch("controller.editor.ffmpeg_render", return_value=str(out)) as render:
        path = editor.render("preview")
        assert path == str(out)
        render.assert_called_once()
        kwargs = render.call_args.kwargs
        assert kwargs["mode"] == "preview"


def test_invalid_clip_index(editor: EditorController):
    with pytest.raises(IndexError):
        editor.trim_clip(0, 0.0, 1.0)
