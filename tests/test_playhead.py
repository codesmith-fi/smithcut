"""Playhead-, cut_out_range- ja still-frame -testit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.tools import execute_tool
from controller.editor import EditorController, format_timecode
from mcp_server.server import create_mcp_server
from model.timeline import Clip, Timeline, VideoSource
from web.api import create_app

from fastapi.testclient import TestClient


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
            clips=[
                Clip(id="c0", source_id="src1", in_sec=0.0, out_sec=10.0),
                Clip(id="c1", source_id="src1", in_sec=10.0, out_sec=20.0),
            ],
        ),
    )


def test_format_timecode():
    assert format_timecode(0) == "00:00.0"
    assert format_timecode(65.2) == "01:05.2"
    assert format_timecode(-1) == "00:00.0"


def test_set_playhead_clamps(editor: EditorController):
    assert editor.set_playhead(-5) == 0.0
    assert editor.set_playhead(3.5) == pytest.approx(3.5)
    assert editor.set_playhead(999) == pytest.approx(20.0)
    summary = editor.get_state_summary()
    assert summary["playhead_sec"] == pytest.approx(20.0)
    assert summary["playhead_timecode"] == "00:20.0"


def test_cut_out_range_middle_of_clip(editor: EditorController):
    # Poista 2 s klipin 1 keskeltä (aikajana 4..6)
    editor.cut_out_range(4.0, 2.0)
    clips = editor.timeline.clips
    assert len(clips) == 3
    assert clips[0].in_sec == pytest.approx(0.0)
    assert clips[0].out_sec == pytest.approx(4.0)
    assert clips[1].in_sec == pytest.approx(6.0)
    assert clips[1].out_sec == pytest.approx(10.0)
    assert clips[2].in_sec == pytest.approx(10.0)
    assert clips[2].out_sec == pytest.approx(20.0)
    assert editor.timeline.total_duration() == pytest.approx(18.0)
    assert editor.playhead_sec == pytest.approx(4.0)
    assert editor.history.can_undo()


def test_cut_at_playhead(editor: EditorController):
    editor.set_playhead(12.0)
    editor.cut_at_playhead(3.0)
    # Toinen klippi oli 10–20; leikkaus 12–15 → 10–12 ja 15–20 + ensimmäinen 0–10
    assert len(editor.timeline.clips) == 3
    assert editor.timeline.total_duration() == pytest.approx(17.0)
    assert editor.playhead_sec == pytest.approx(12.0)


def test_cut_out_range_removes_whole_clip(editor: EditorController):
    editor.cut_out_range(0.0, 10.0)
    assert len(editor.timeline.clips) == 1
    assert editor.timeline.clips[0].in_sec == pytest.approx(10.0)
    assert editor.timeline.total_duration() == pytest.approx(10.0)


def test_get_frame_at_time_cached(editor: EditorController, tmp_path: Path):
    fake = tmp_path / "frames" / "cached.jpg"

    def fake_extract(source_path, timestamp, output_path, scale="960:-1"):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"jpeg-bytes")
        return output_path

    with patch("controller.editor.extract_still_frame", side_effect=fake_extract) as mocked:
        path1 = editor.get_frame_at_time(2.5)
        path2 = editor.get_frame_at_time(2.5)
        assert path1 == path2
        assert path1.is_file()
        assert mocked.call_count == 1


def test_api_playhead_and_frame(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    video = ws / "clip.mp4"
    video.write_bytes(b"fake")
    editor = EditorController(workspace=ws)
    app = create_app(workspace=ws, editor=editor)

    def fake_extract(source_path, timestamp, output_path, scale="960:-1"):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\xff\xd8\xffjpeg")
        return output_path

    with (
        patch("controller.editor.probe", return_value=PROBE_META),
        patch("controller.editor.extract_still_frame", side_effect=fake_extract),
    ):
        with TestClient(app) as client:
            client.post(
                "/api/upload",
                files={"file": ("clip.mp4", b"fake-video", "video/mp4")},
                data={"kind": "video"},
            )
            state = client.get("/api/state").json()["state"]
            assert "playhead_sec" in state
            assert "playhead_timecode" in state

            res = client.post("/api/timeline/playhead", json={"sec": 2.5})
            assert res.status_code == 200
            body = res.json()
            assert body["ok"] is True
            assert body["playhead_sec"] == pytest.approx(2.5)
            assert body["state"]["playhead_sec"] == pytest.approx(2.5)

            frame = client.get("/api/timeline/frame", params={"t": 2.5})
            assert frame.status_code == 200
            assert frame.headers["content-type"].startswith("image/jpeg")
            assert frame.headers.get("cache-control") == "no-cache"
            assert frame.content.startswith(b"\xff\xd8")

            timeline = client.get("/api/timeline").json()
            assert timeline["ok"] is True
            assert timeline["state"]["playhead_sec"] == pytest.approx(2.5)


def test_mcp_cut_timeline_range(editor: EditorController):
    import asyncio
    import json

    server = create_mcp_server(editor)
    editor.set_playhead(4.0)

    async def _run():
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert "cut_timeline_range" in names

        result = await server.call_tool(
            "cut_timeline_range", {"duration_sec": 2.0}
        )
        payload = json.loads(result.content[0].text)
        assert payload["ok"] is True
        assert payload["start_sec"] == pytest.approx(4.0)
        assert editor.timeline.total_duration() == pytest.approx(18.0)

        result2 = await server.call_tool(
            "cut_timeline_range",
            {"duration_sec": 1.0, "start_sec": 0.0},
        )
        payload2 = json.loads(result2.content[0].text)
        assert payload2["ok"] is True
        assert editor.timeline.total_duration() == pytest.approx(17.0)

    asyncio.run(_run())


def test_execute_tool_cut_timeline_range_defaults_to_playhead(editor: EditorController):
    editor.set_playhead(8.0)
    text, changed = execute_tool(
        editor, "cut_timeline_range", {"duration_sec": 2.0}
    )
    assert changed is True
    assert editor.timeline.total_duration() == pytest.approx(18.0)
    assert '"ok": true' in text.lower() or '"ok":true' in text.lower()
