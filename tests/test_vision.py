"""Vaihe 4 – VLM-visuaalisen kohdistuksen yksikkötestit (Ollama mockattu)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.tools import execute_tool
from agent.vision import (
    VisionError,
    locate_object_in_image,
    parse_vision_json,
)
from controller.editor import EditorController
from mcp_server.server import create_mcp_server
from model.timeline import Clip, Timeline, VideoSource


def _make_editor(tmp_path: Path) -> EditorController:
    src_file = tmp_path / "game.mp4"
    src_file.write_bytes(b"fake-mp4")
    src = VideoSource(
        id="src1",
        path=str(src_file),
        duration=20.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
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


def test_parse_vision_json_clean():
    raw = json.dumps(
        {
            "found": True,
            "x": 0.34,
            "y": 0.62,
            "confidence": "high",
            "reason": "Spaceship centered in upper-left quadrant",
        }
    )
    res = parse_vision_json(raw)
    assert res == {
        "found": True,
        "x": 0.34,
        "y": 0.62,
        "confidence": "high",
        "reason": "Spaceship centered in upper-left quadrant",
    }


def test_parse_vision_json_markdown_fence_and_clamping():
    raw = (
        "Here is the location:\n"
        "```json\n"
        '{"found": true, "x": -0.25, "y": 1.42, "confidence": "MEDIUM", "reason": "edge"}\n'
        "```\n"
    )
    res = parse_vision_json(raw)
    assert res["found"] is True
    assert res["x"] == pytest.approx(0.0)
    assert res["y"] == pytest.approx(1.0)
    assert res["confidence"] == "medium"
    assert res["reason"] == "edge"


def test_parse_vision_json_not_found():
    res = parse_vision_json(
        '{"found": false, "x": null, "y": null, "confidence": "low", "reason": "not in frame"}'
    )
    assert res["found"] is False
    assert res["x"] is None
    assert res["y"] is None
    assert res["confidence"] == "low"
    assert res["reason"] == "not in frame"


def test_parse_vision_json_normalizes_0_to_1000_and_pixel_scales():
    # qwen2.5vl:n natiivi 0..1000 -skaala
    res_1000 = parse_vision_json(
        '{"found": true, "x": 500, "y": 500, "confidence": "high", "reason": "center"}'
    )
    assert res_1000["x"] == pytest.approx(0.5)
    assert res_1000["y"] == pytest.approx(0.5)

    res_1000_off = parse_vision_json(
        '{"found": true, "x": 320, "y": 750, "confidence": "high", "reason": "ship"}'
    )
    assert res_1000_off["x"] == pytest.approx(0.32)
    assert res_1000_off["y"] == pytest.approx(0.75)

    # Pikselikoordinaatit (> 1000) kuvan mittojen mukaan
    res_px = parse_vision_json(
        '{"found": true, "x": 1280, "y": 540, "confidence": "high", "reason": "px"}',
        image_width=1920,
        image_height=1080,
    )
    assert res_px["x"] == pytest.approx(1280 / 1920, abs=1e-3)
    assert res_px["y"] == pytest.approx(0.5)


def test_locate_object_in_image_sends_base64_and_keep_alive_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("CUTSMITH_VLM_MODEL", raising=False)
    monkeypatch.delenv("CUTSMITH_VLM_KEEP_ALIVE", raising=False)

    img = tmp_path / "frame.jpg"
    img_bytes = b"\xff\xd8\xfffake-jpeg-data"
    img.write_bytes(img_bytes)

    mock_client = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "message": {
            "content": '{"found": true, "x": 0.51, "y": 0.28, "confidence": "high", "reason": "blue ship"}'
        }
    }
    mock_client.post.return_value = resp

    res = locate_object_in_image(img, "sininen alus", client=mock_client)
    assert res["found"] is True
    assert res["x"] == pytest.approx(0.51)
    assert res["y"] == pytest.approx(0.28)

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["model"] == "qwen2.5vl:3b"
    assert payload["keep_alive"] == "0"
    assert payload["format"] == "json"
    user_msg = payload["messages"][1]
    assert user_msg["images"] == [base64.b64encode(img_bytes).decode("ascii")]


def test_locate_object_in_image_missing_model_suggests_ollama_pull(
    tmp_path: Path,
):
    img = tmp_path / "frame.jpg"
    img.write_bytes(b"\xff\xd8\xffdata")

    mock_client = MagicMock()
    resp = MagicMock(status_code=404, text="model not found")
    mock_client.post.return_value = resp

    with pytest.raises(VisionError) as exc_info:
        locate_object_in_image(
            img, "menu button", client=mock_client, model="qwen2.5vl:7b"
        )
    assert exc_info.value.code == "model_missing"
    assert "ollama pull qwen2.5vl:7b" in str(exc_info.value)


def test_execute_tool_locate_object_in_frame_integration(tmp_path: Path):
    editor = _make_editor(tmp_path)
    editor.set_playhead(3.25)

    captured_ts: list[float] = []

    def fake_extract(source_path, timestamp, output_path, scale="960:-1"):
        captured_ts.append(float(timestamp))
        Path(output_path).write_bytes(b"\xff\xd8\xffframe")
        return Path(output_path)

    fake_vision = {
        "found": True,
        "x": 0.72,
        "y": 0.41,
        "confidence": "high",
        "reason": "red rose on right",
    }

    with (
        patch("controller.editor.extract_still_frame", side_effect=fake_extract),
        patch("agent.vision.locate_object_in_image", return_value=fake_vision),
    ):
        # 1) Oletus: käyttää playheadia (3.25 s)
        text1, changed1 = execute_tool(
            editor, "locate_object_in_frame", {"query": "punainen ruusu"}
        )
        assert changed1 is False
        p1 = json.loads(text1)
        assert p1["found"] is True
        assert p1["target_x"] == pytest.approx(0.72)
        assert p1["target_y"] == pytest.approx(0.41)
        assert p1["at_sec"] == pytest.approx(3.25)
        assert captured_ts[-1] == pytest.approx(3.25)

        # 2) clip_index=2 (1-pohjainen → klippi 10..20s, keskipiste 15.0s)
        text2, changed2 = execute_tool(
            editor,
            "locate_object_in_frame",
            {"query": "valikko", "clip_index": 2},
        )
        assert changed2 is False
        assert json.loads(text2)["at_sec"] == pytest.approx(15.0)
        assert captured_ts[-1] == pytest.approx(15.0)

        # 3) Eksplisiittinen at_sec=7.5
        text3, changed3 = execute_tool(
            editor,
            "locate_object_in_frame",
            {"query": "alus", "at_sec": 7.5},
        )
        assert changed3 is False
        assert json.loads(text3)["at_sec"] == pytest.approx(7.5)
        assert captured_ts[-1] == pytest.approx(7.5)


def test_mcp_exposes_locate_object_in_frame(tmp_path: Path):
    editor = _make_editor(tmp_path)
    server = create_mcp_server(editor)
    tools = asyncio.run(server.list_tools())
    assert "locate_object_in_frame" in {t.name for t in tools}
