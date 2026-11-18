"""ffprobe-kääreen testit (aliprosessi mockattu)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from compiler.ffprobe import probe


def test_probe_parses_streams():
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "60000/1001",
                "duration": "12.5",
            },
            {"codec_type": "audio"},
        ],
        "format": {"duration": "12.5"},
    }
    with patch("compiler.ffprobe.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(payload), stderr=""
        )
        meta = probe("/tmp/video.mp4")

    assert meta["width"] == 1920
    assert meta["height"] == 1080
    assert meta["duration"] == pytest.approx(12.5)
    assert meta["has_audio"] is True
    assert meta["fps"] == pytest.approx(60000 / 1001)
    env = run.call_args.kwargs["env"]
    assert env["LC_ALL"] == "C"
    assert run.call_args.args[0][0] == "ffprobe"


def test_probe_fails_without_video_stream():
    payload = {"streams": [{"codec_type": "audio"}], "format": {}}
    with patch("compiler.ffprobe.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(payload), stderr=""
        )
        with pytest.raises(RuntimeError, match="no video stream"):
            probe("/tmp/audio.mp3")
