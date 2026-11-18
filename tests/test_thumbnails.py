"""Klippi-pikkukuvien testit (FFmpeg mockattu)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from controller.editor import EditorController
from model.timeline import Clip, Timeline, VideoSource
from web.api import create_app


def _editor(tmp_path: Path, *, in_sec: float = 1.5) -> EditorController:
    source_file = tmp_path / "clip.mp4"
    source_file.write_bytes(b"fake-video")
    src = VideoSource(
        id="src1",
        path=str(source_file),
        duration=30.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
    )
    return EditorController(
        workspace=tmp_path / "workspace",
        timeline=Timeline(
            sources={"src1": src},
            clips=[Clip(id="c0", source_id="src1", in_sec=in_sec, out_sec=10.0)],
        ),
    )


def test_extract_clip_thumbnail_command(tmp_path: Path):
    from compiler.ffmpeg import extract_clip_thumbnail

    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    out = tmp_path / "t.jpg"

    with patch("compiler.ffmpeg.subprocess.run") as run:
        def _side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"jpeg")
            return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

        run.side_effect = _side_effect
        result = extract_clip_thumbnail(src, 2.5, out)

    assert result == out
    assert out.is_file()
    cmd = run.call_args.args[0]
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd
    assert cmd[cmd.index("-ss") + 1] == "2.500000"
    assert "scale=160:-1" in cmd
    assert run.call_args.kwargs["env"]["LC_ALL"] == "C"


def test_get_clip_thumbnail_caches_by_trim_start(tmp_path: Path):
    editor = _editor(tmp_path, in_sec=2.0)
    calls: list[float] = []

    def fake_extract(source_path, timestamp, output_path):
        calls.append(timestamp)
        Path(output_path).write_bytes(b"jpeg-bytes")
        return Path(output_path)

    with patch("controller.editor.extract_clip_thumbnail", side_effect=fake_extract):
        first = editor.get_clip_thumbnail(1)
        second = editor.get_clip_thumbnail(1)

    assert first == second
    assert first.is_file()
    assert len(calls) == 1
    assert calls[0] == pytest.approx(2.0)

    # Uusi trim → uusi välimuistiavain
    editor.timeline.clips[0] = editor.timeline.clips[0].model_copy(
        update={"in_sec": 3.5, "out_sec": 10.0}
    )
    with patch("controller.editor.extract_clip_thumbnail", side_effect=fake_extract):
        third = editor.get_clip_thumbnail(1)
    assert third != first
    assert len(calls) == 2
    assert calls[1] == pytest.approx(3.5)


def test_get_clip_thumbnail_rejects_zero_index(tmp_path: Path):
    editor = _editor(tmp_path)
    with pytest.raises(IndexError, match="1-based"):
        editor.get_clip_thumbnail(0)


def test_api_clip_thumbnail(tmp_path: Path):
    editor = _editor(tmp_path, in_sec=1.25)
    app = create_app(workspace=tmp_path / "workspace", editor=editor)
    jpeg = b"\xff\xd8\xfffakejpeg"

    def fake_extract(source_path, timestamp, output_path):
        Path(output_path).write_bytes(jpeg)
        return Path(output_path)

    with patch("controller.editor.extract_clip_thumbnail", side_effect=fake_extract):
        with TestClient(app) as client:
            res = client.get("/api/clips/1/thumbnail?v=1.25")
            assert res.status_code == 200
            assert res.headers["content-type"].startswith("image/jpeg")
            assert res.headers.get("cache-control") == "no-cache"
            assert res.content == jpeg

            missing = client.get("/api/clips/9/thumbnail")
            assert missing.status_code == 404
