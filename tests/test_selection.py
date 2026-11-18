"""Aluevalinnan (range selection) testit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent.llm_agent import _selection_context_line, _system_prompt
from agent.tools import execute_tool
from controller.editor import EditorController
from model.timeline import Clip, Timeline, VideoSource
from web.api import create_app


PROBE_META = {
    "duration": 30.0,
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "has_audio": True,
}


@pytest.fixture
def editor(tmp_path: Path) -> EditorController:
    src = VideoSource(
        id="src1",
        path=str(tmp_path / "a.mp4"),
        duration=30.0,
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
            clips=[Clip(id="c0", source_id="src1", in_sec=0.0, out_sec=20.0)],
        ),
    )


def test_set_selection_normalizes_and_clamps(editor: EditorController):
    start, end = editor.set_selection(8.0, 3.0)
    assert start == pytest.approx(3.0)
    assert end == pytest.approx(8.0)

    start, end = editor.set_selection(-2.0, 999.0)
    assert start == pytest.approx(0.0)
    assert end == pytest.approx(20.0)

    start, end = editor.set_selection(5.0, 5.02)
    assert start is None and end is None

    start, end = editor.set_selection(None, 4.0)
    assert start is None and end is None


def test_clear_selection(editor: EditorController):
    editor.set_selection(2.0, 6.0)
    assert editor.has_selection()
    editor.clear_selection()
    assert not editor.has_selection()
    assert editor.selection_start_sec is None
    assert editor.selection_end_sec is None


def test_cut_selected_range(editor: EditorController):
    editor.set_selection(4.0, 7.0)
    editor.cut_selected_range()
    assert editor.timeline.total_duration() == pytest.approx(17.0)
    assert not editor.has_selection()
    assert editor.playhead_sec == pytest.approx(4.0)
    assert editor.history.can_undo()


def test_cut_selected_range_without_selection_raises(editor: EditorController):
    with pytest.raises(ValueError, match="no active selection"):
        editor.cut_selected_range()


def test_state_includes_selection_fields(editor: EditorController):
    editor.set_selection(1.5, 4.0)
    summary = editor.get_state_summary()
    assert summary["selection_start_sec"] == pytest.approx(1.5)
    assert summary["selection_end_sec"] == pytest.approx(4.0)
    assert summary["selection_duration_sec"] == pytest.approx(2.5)
    assert summary["selection_timecode"] == "00:01.5 – 00:04.0"


def test_api_timeline_selection(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    editor = EditorController(workspace=ws)
    app = create_app(workspace=ws, editor=editor)

    with patch("controller.editor.probe", return_value=PROBE_META):
        with TestClient(app) as client:
            client.post(
                "/api/upload",
                files={"file": ("clip.mp4", b"fake-video", "video/mp4")},
                data={"kind": "video"},
            )
            res = client.post(
                "/api/timeline/selection",
                json={"start_sec": 6.0, "end_sec": 2.0},
            )
            assert res.status_code == 200
            body = res.json()
            assert body["ok"] is True
            assert body["selection_start_sec"] == pytest.approx(2.0)
            assert body["selection_end_sec"] == pytest.approx(6.0)
            assert body["state"]["selection_duration_sec"] == pytest.approx(4.0)

            cleared = client.post(
                "/api/timeline/selection",
                json={"start_sec": None, "end_sec": None},
            )
            assert cleared.json()["selection_start_sec"] is None
            assert cleared.json()["state"]["selection_end_sec"] is None


def test_cut_timeline_range_uses_selection(editor: EditorController):
    editor.set_selection(5.0, 9.0)
    text, changed = execute_tool(editor, "cut_timeline_range", {})
    assert changed is True
    assert editor.timeline.total_duration() == pytest.approx(16.0)
    assert not editor.has_selection()
    assert '"used_selection":true' in text.lower().replace(" ", "")


def test_agent_selection_context_line(editor: EditorController):
    editor.set_selection(2.0, 5.5)
    summary = editor.get_state_summary(lang="en")
    line = _selection_context_line(summary, lang="en")
    assert line.startswith("Selected range:")
    assert "00:02.0 – 00:05.5" in line
    assert "3.5" in line

    empty = _selection_context_line(
        {"selection_start_sec": None, "selection_end_sec": None}, lang="fi"
    )
    assert empty == ""

    prompt = _system_prompt("en", editor.get_state_summary(lang="en"))
    assert "Selected range:" in prompt
    assert "selected range" in prompt.lower()
