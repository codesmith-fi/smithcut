"""LLM-agentin testit (Ollama mockattu)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent.llm_agent import AgentResult, run_agent
from agent.tools import execute_tool, one_to_zero
from controller.editor import EditorController
from model.timeline import Clip, Timeline, VideoSource


def _editor_with_clip() -> EditorController:
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=30.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    editor = EditorController(
        timeline=Timeline(
            sources={"src1": src},
            clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=10)],
        )
    )
    return editor


def test_one_to_zero_rejects_zero():
    with pytest.raises(ValueError, match="1-based"):
        one_to_zero(0, label="clip_index")
    assert one_to_zero(1) == 0


def test_execute_tool_add_text_and_zoom_1_based():
    editor = _editor_with_clip()
    text, changed = execute_tool(
        editor,
        "add_text",
        {
            "clip_index": 1,
            "text": "Hello",
            "start_sec": 0,
            "duration": 2,
            "font_style": "cinema",
        },
    )
    assert changed is True
    assert "Hello" in text
    assert editor.timeline.clips[0].effects[-1].font_style == "cinema"

    zoom, _ = execute_tool(
        editor,
        "add_zoom",
        {"clip_index": 1, "start_sec": 0, "duration": 1, "zoom_factor": 2.0},
    )
    assert "zoom" in zoom


def test_run_agent_tool_then_reply():
    editor = _editor_with_clip()
    responses = [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {
                            "name": "add_text",
                            "arguments": {
                                "clip_index": 1,
                                "text": "Hei",
                                "start_sec": 0,
                                "duration": 2,
                                "font_style": "impact",
                            },
                        },
                    }
                ],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": "Lisäsin tekstin klippiin 1.",
            }
        },
    ]

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = responses
    mock_client.post.return_value = mock_response

    result = run_agent(
        editor,
        "Lisää teksti Hei",
        lang="fi",
        client=mock_client,
        model="qwen3:8b",
    )
    assert isinstance(result, AgentResult)
    assert result.ready is True
    assert result.changed is True
    assert "add_text" in result.tool_calls
    assert "klippiin 1" in result.reply
    assert any(
        getattr(e, "text", None) == "Hei" for e in editor.timeline.clips[0].effects
    )
    # keep_alive VRAM-vapautukseen
    payload = mock_client.post.call_args_list[0].kwargs["json"]
    assert payload["keep_alive"] == "15s"
    assert payload["model"] == "qwen3:8b"


def test_run_agent_unreachable_localized():
    editor = _editor_with_clip()
    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.ConnectError("connection refused")

    fi = run_agent(editor, "hei", lang="fi", client=mock_client)
    assert fi.ready is False
    assert fi.error == "unreachable"
    assert "ollama" in fi.reply.lower()

    en = run_agent(editor, "hi", lang="en", client=mock_client)
    assert en.ready is False
    assert "not responding" in en.reply.lower()


def test_run_agent_model_missing():
    editor = _editor_with_clip()
    mock_client = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "model not found"
    mock_client.post.return_value = resp

    result = run_agent(editor, "hi", lang="en", client=mock_client, model="missing:1b")
    assert result.ready is False
    assert result.error == "model_missing"
    assert "ollama pull missing:1b" in result.reply
