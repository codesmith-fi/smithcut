"""Valitun alueen crossfade- ja blur-siirtymien testit."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.llm_agent import strip_think_tags
from agent.tools import execute_tool
from compiler.ffmpeg import _gblur_step_filters, build_filter_complex
from controller.editor import EditorController
from model.timeline import BlurEffect, Clip, Timeline, VideoSource


@pytest.fixture
def two_clip_editor(tmp_path: Path) -> EditorController:
    src = VideoSource(
        id="src1",
        path=str(tmp_path / "a.mp4"),
        duration=40.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    (tmp_path / "a.mp4").write_bytes(b"fake")
    return EditorController(
        workspace=tmp_path,
        timeline=Timeline(
            sources={"src1": src},
            clips=[
                Clip(id="c0", source_id="src1", in_sec=0.0, out_sec=10.0),
                Clip(id="c1", source_id="src1", in_sec=10.0, out_sec=20.0),
            ],
        ),
    )


def test_apply_crossfade_to_selection(two_clip_editor: EditorController):
    editor = two_clip_editor
    # Sauma on 10 s; maalaa 8–12 → kesto 4 s
    editor.set_selection(8.0, 12.0)
    tr = editor.apply_crossfade_to_selection(transition_type="fade")
    assert tr.after_clip_index == 0
    assert tr.type == "fade"
    assert tr.duration == pytest.approx(4.0)
    assert not editor.has_selection()
    assert editor.history.can_undo()


def test_apply_crossfade_caps_duration(two_clip_editor: EditorController):
    editor = two_clip_editor
    # Lyhyet klipit: max 0.9 * 10 = 9
    editor.set_selection(0.5, 19.5)
    tr = editor.apply_crossfade_to_selection()
    assert tr.duration == pytest.approx(9.0)


def test_apply_crossfade_requires_boundary(two_clip_editor: EditorController):
    editor = two_clip_editor
    editor.set_selection(1.0, 3.0)
    with pytest.raises(ValueError, match="clip boundary"):
        editor.apply_crossfade_to_selection()


def test_apply_crossfade_by_clip_index_without_selection(
    two_clip_editor: EditorController,
):
    editor = two_clip_editor
    assert not editor.has_selection()
    tr = editor.apply_crossfade_to_selection(
        clip_index=0, duration_sec=5.0, transition_type="fade"
    )
    assert tr.after_clip_index == 0
    assert tr.type == "fade"
    assert tr.duration == pytest.approx(5.0)
    assert not editor.has_selection()


def test_apply_crossfade_verbal_5_plus_5_is_10(
    two_clip_editor: EditorController,
):
    """'5 s lopusta + 5 s alusta' → duration_sec=10."""
    editor = two_clip_editor
    # Klipit ovat 10 s → max 90 % = 9 s
    tr = editor.apply_crossfade_to_selection(clip_index=0, duration_sec=10.0)
    assert tr.duration == pytest.approx(9.0)


def test_execute_tool_crossfade_clip_index_duration(
    two_clip_editor: EditorController,
):
    editor = two_clip_editor
    text, changed = execute_tool(
        editor,
        "apply_crossfade_to_selection",
        {"clip_index": 0, "duration_sec": 4.0},
    )
    assert changed is True
    assert editor.timeline.transitions[0].duration == pytest.approx(4.0)
    assert "4.0" in text or "4" in text


def test_system_prompt_documents_verbal_crossfade():
    from agent.llm_agent import _system_prompt

    prompt = _system_prompt(
        "fi",
        {
            "playhead_sec": 0.0,
            "playhead_timecode": "00:00.0",
            "selection_start_sec": None,
            "selection_end_sec": None,
        },
    )
    assert "clip_index=0" in prompt
    assert "duration_sec=10.0" in prompt or "2*N" in prompt
    assert "clippien 1 ja 2" in prompt.lower() or "clips 1 and 2" in prompt.lower()


def test_add_blur_to_selection_single_clip(two_clip_editor: EditorController):
    editor = two_clip_editor
    editor.set_selection(2.0, 5.0)
    effects = editor.add_blur_to_selection(max_sigma=12.0, fade_sec=0.5)
    assert len(effects) == 1
    assert effects[0].start_sec == pytest.approx(2.0)
    assert effects[0].end_sec == pytest.approx(5.0)
    assert effects[0].max_sigma == pytest.approx(12.0)
    assert not editor.has_selection()
    assert any(
        isinstance(e, BlurEffect) for e in editor.timeline.clips[0].effects
    )


def test_add_blur_to_selection_across_clips(two_clip_editor: EditorController):
    editor = two_clip_editor
    editor.set_selection(8.0, 13.0)
    effects = editor.add_blur_to_selection()
    assert len(effects) == 2
    assert effects[0].start_sec == pytest.approx(8.0)
    assert effects[0].end_sec == pytest.approx(10.0)
    assert effects[1].start_sec == pytest.approx(0.0)
    assert effects[1].end_sec == pytest.approx(3.0)


def test_gblur_step_filters_contain_enable_and_sigma():
    effect = BlurEffect(start_sec=1.0, end_sec=4.0, max_sigma=18.0, fade_sec=0.6)
    parts = _gblur_step_filters(effect, steps=5)
    assert parts
    joined = ",".join(parts)
    assert "gblur=sigma=" in joined
    assert "enable='between(t" in joined
    assert "18.000" in joined


def test_filter_complex_includes_gblur(two_clip_editor: EditorController):
    editor = two_clip_editor
    editor.set_selection(1.0, 3.0)
    editor.add_blur_to_selection(max_sigma=10.0, fade_sec=0.4)
    fc = build_filter_complex(editor.timeline, mode="preview")
    assert "gblur=sigma=" in fc
    assert "between(t" in fc


def test_execute_tool_crossfade_and_blur(two_clip_editor: EditorController):
    editor = two_clip_editor
    editor.set_selection(7.0, 12.0)
    text, changed = execute_tool(editor, "apply_crossfade_to_selection", {})
    assert changed is True
    assert "fade" in text
    assert editor.timeline.transitions[0].duration == pytest.approx(5.0)

    editor.set_selection(1.0, 2.5)
    blur_text, blur_changed = execute_tool(editor, "add_blur_to_selection", {})
    assert blur_changed is True
    assert "blur" in blur_text
    assert any(isinstance(e, BlurEffect) for e in editor.timeline.clips[0].effects)


def test_strip_think_tags_again():
    assert strip_think_tags("<think>x</think>Valmis.</think>") == "Valmis."
