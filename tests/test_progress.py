"""Edistymisseurannan testit (FFmpeg progress + API status)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from compiler.ffmpeg import parse_progress_value, percent_from_out_time_us, render
from controller.editor import EditorController
from model.timeline import Clip, Timeline, VideoSource
from web.api import create_app


def test_percent_from_out_time_us():
    assert percent_from_out_time_us(0, 10.0) == 0
    assert percent_from_out_time_us(5_000_000, 10.0) == 50
    assert percent_from_out_time_us(10_000_000, 10.0) == 100
    assert percent_from_out_time_us(20_000_000, 10.0) == 100
    assert percent_from_out_time_us(1, 0) == 0


def test_parse_progress_value():
    assert parse_progress_value("out_time_us=1234567") == ("out_time_us", "1234567")
    assert parse_progress_value("progress=end") == ("progress", "end")
    assert parse_progress_value("") is None
    assert parse_progress_value("nope") is None


def test_render_with_progress_callback():
    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path="/videos/a.mp4",
                duration=10.0,
                width=640,
                height=360,
                fps=30.0,
                has_audio=True,
            )
        },
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=5)],
    )
    percents: list[int] = []

    class FakeStdout:
        def __iter__(self):
            yield from [
                "out_time_us=0\n",
                "progress=continue\n",
                "out_time_us=2500000\n",
                "progress=continue\n",
                "out_time_us=5000000\n",
                "progress=end\n",
            ]

    class FakeProc:
        def __init__(self):
            self.stdout = FakeStdout()
            self.stderr = iter([])
            self._code = 0

        def wait(self):
            return self._code

        def poll(self):
            return self._code

        def kill(self):
            pass

    with (
        patch("compiler.ffmpeg.build_command", return_value=["ffmpeg", "/tmp/out.mp4"]),
        patch("compiler.ffmpeg.subprocess.Popen", return_value=FakeProc()),
        patch("compiler.ffmpeg.threading.Thread") as thread_cls,
    ):
        thread_cls.return_value = MagicMock()
        path = render(
            tl,
            output_path="/tmp/out.mp4",
            mode="preview",
            on_progress=percents.append,
            total_duration=5.0,
        )

    assert path == "/tmp/out.mp4"
    assert percents[0] == 0
    assert 50 in percents
    assert percents[-1] == 100
    # progress-liput komennossa
    # (build_command mockattu; _render_with_progress saa cmd:n jossa liput)


def test_editor_get_progress_during_render(tmp_path: Path):
    editor = EditorController(workspace=tmp_path)
    src = VideoSource(
        id="src1",
        path=str(tmp_path / "a.mp4"),
        duration=10.0,
        width=640,
        height=360,
        fps=30.0,
        has_audio=True,
    )
    (tmp_path / "a.mp4").write_bytes(b"x")
    editor.timeline.sources[src.id] = src
    editor.timeline.clips.append(
        Clip(id="c0", source_id=src.id, in_sec=0, out_sec=4)
    )

    seen: list[dict] = []

    def fake_render(timeline, output_path=None, mode="preview", workspace=None, on_progress=None, total_duration=None):
        assert on_progress is not None
        on_progress(0)
        seen.append(editor.get_progress())
        on_progress(42)
        seen.append(editor.get_progress())
        on_progress(100)
        Path(output_path).write_bytes(b"mp4")
        return str(output_path)

    with patch("controller.editor.ffmpeg_render", side_effect=fake_render):
        editor.render("preview")

    assert seen[0]["active"] is True
    assert seen[0]["phase"] == "preview"
    assert seen[0]["percent"] == 0
    assert seen[1]["percent"] == 42
    # render clear_progress finally-blokissa
    idle = editor.get_progress()
    assert idle["active"] is False
    assert idle["phase"] == "idle"


def test_api_status_endpoint(tmp_path: Path):
    editor = EditorController(workspace=tmp_path)
    app = create_app(workspace=tmp_path, editor=editor)
    with TestClient(app) as client:
        res = client.get("/api/status")
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["progress"]["active"] is False
        assert body["progress"]["phase"] == "idle"

        editor.set_progress(
            active=True, phase="chat", percent=None, detail="Processing"
        )
        busy = client.get("/api/status").json()["progress"]
        assert busy["active"] is True
        assert busy["phase"] == "chat"
        assert busy["percent"] is None
