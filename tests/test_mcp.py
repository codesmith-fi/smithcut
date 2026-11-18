"""MCP-palvelimen testit (1-pohjaiset indeksit)."""

from __future__ import annotations

import asyncio
import json

import pytest

from controller.editor import EditorController
from mcp_server.server import FastMCP, create_mcp_server
from model.timeline import Clip, Timeline, VideoSource


@pytest.fixture
def editor() -> EditorController:
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=30.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    return EditorController(
        timeline=Timeline(
            sources={"src1": src},
            clips=[
                Clip(id="c0", source_id="src1", in_sec=0, out_sec=8),
                Clip(id="c1", source_id="src1", in_sec=8, out_sec=16),
            ],
        )
    )


def _text(result) -> str:
    return result.content[0].text


def test_mcp_lists_expected_tools(editor: EditorController):
    server = create_mcp_server(editor)
    assert isinstance(server, FastMCP)

    async def _run():
        return await server.list_tools()

    tools = asyncio.run(_run())
    names = {t.name for t in tools}
    assert {
        "add_clip",
        "trim_clip",
        "split_clip",
        "remove_clip",
        "move_clip",
        "add_zoom",
        "add_text",
        "set_transition",
        "set_music",
        "undo",
        "redo",
        "get_state",
        "cut_timeline_range",
        "apply_crossfade_to_selection",
        "add_blur_to_selection",
        "remove_video_audio",
        "attach_audio_track",
        "set_timeline_fades",
        "detach_audio_to_timeline",
        "split_audio_clip",
        "cut_audio_range",
        "move_audio_clip",
        "set_audio_clip_volume",
    } <= names
    by_name = {t.name: t for t in tools}
    assert "1-based" in (by_name["add_text"].description or "")
    assert "1-based" in (by_name["set_transition"].description or "")


def test_mcp_tools_use_one_based_indices(editor: EditorController):
    server = create_mcp_server(editor)

    async def _run():
        state = json.loads(_text(await server.call_tool("get_state", {})))
        assert state["clips"][0]["index"] == 1

        await server.call_tool(
            "add_text",
            {
                "clip_index": 1,
                "text": "Title",
                "start_sec": 0,
                "duration": 1.5,
                "font_style": "cinema",
            },
        )
        assert editor.timeline.clips[0].effects[-1].text == "Title"
        assert editor.timeline.clips[0].effects[-1].font_style == "cinema"

        await server.call_tool(
            "set_transition",
            {"after_clip": 1, "transition_type": "fade", "duration": 0.4},
        )
        assert editor.timeline.transitions[0].after_clip_index == 0

        await server.call_tool("split_clip", {"clip_index": 2, "split_at_sec": 2.0})
        assert len(editor.timeline.clips) == 3

        err = json.loads(
            _text(await server.call_tool("remove_clip", {"clip_index": 0}))
        )
        assert "error" in err
        assert "1-based" in err["error"]

    asyncio.run(_run())
