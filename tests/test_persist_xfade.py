"""project_state.json -persistenssi ja pitkä xfade."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from compiler.ffmpeg import build_filter_complex
from controller.editor import EditorController, PROJECT_STATE_FILENAME
from model.timeline import Clip, Timeline, VideoSource


def _two_long_clips(tmp_path: Path) -> EditorController:
    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake-video")
    src = VideoSource(
        id="src1",
        path=str(video),
        duration=60.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    return EditorController(
        workspace=tmp_path,
        timeline=Timeline(
            sources={"src1": src},
            clips=[
                Clip(id="c0", source_id="src1", in_sec=0.0, out_sec=30.0),
                Clip(id="c1", source_id="src1", in_sec=30.0, out_sec=60.0),
            ],
        ),
    )


def test_project_state_survives_restart(tmp_path: Path):
    editor = _two_long_clips(tmp_path)
    editor.set_playhead(12.5)
    editor.set_selection(5.0, 9.0)
    editor.set_transition(0, "fade", 1.2)
    editor.get_state_summary()  # flush

    state_path = tmp_path / PROJECT_STATE_FILENAME
    assert state_path.is_file()

    reloaded = EditorController(workspace=tmp_path, load_persisted=True)
    assert len(reloaded.timeline.clips) == 2
    assert reloaded.playhead_sec == pytest.approx(12.5)
    assert reloaded.selection_start_sec == pytest.approx(5.0)
    assert reloaded.selection_end_sec == pytest.approx(9.0)
    assert reloaded.timeline.transitions[0].type == "fade"
    assert reloaded.timeline.transitions[0].duration == pytest.approx(1.2)


def test_project_state_skips_missing_sources(tmp_path: Path):
    editor = _two_long_clips(tmp_path)
    editor.get_state_summary()
    # Poista lähdetiedosto
    Path(editor.timeline.sources["src1"].path).unlink()
    reloaded = EditorController(workspace=tmp_path, load_persisted=True)
    assert reloaded.timeline.clips == []
    assert reloaded.timeline.sources == {}


def test_20s_crossfade_xfade_offset_non_negative(tmp_path: Path):
    editor = _two_long_clips(tmp_path)
    # Valinta sauman yli, kesto 20 s
    editor.set_selection(20.0, 40.0)
    tr = editor.apply_crossfade_to_selection(transition_type="fade")
    assert tr.duration == pytest.approx(20.0)
    assert tr.duration < editor.timeline.clips[0].duration
    assert tr.duration < editor.timeline.clips[1].duration

    fc = build_filter_complex(editor.timeline, mode="preview")
    assert "xfade=transition=fade" in fc
    assert "acrossfade=d=20" in fc
    # offset=... ei negatiivinen
    assert "offset=-" not in fc
    assert "offset=10.000000" in fc or "offset=10." in fc


def test_render_failure_keeps_timeline(tmp_path: Path):
    editor = _two_long_clips(tmp_path)
    editor.set_transition(0, "fade", 2.0)
    before = editor.timeline.model_copy(deep=True)

    with patch(
        "controller.editor.ffmpeg_render",
        side_effect=RuntimeError("xfade failed: Invalid argument"),
    ):
        with pytest.raises(RuntimeError, match="timeline unchanged"):
            editor.render(mode="preview")

    assert len(editor.timeline.clips) == len(before.clips)
    assert editor.timeline.transitions[0].duration == pytest.approx(2.0)
