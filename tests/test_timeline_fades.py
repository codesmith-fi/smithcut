"""Koko aikajanan fade in / fade out (video + audio)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from compiler.ffmpeg import build_filter_complex, compile_timeline
from compiler.ffprobe import probe
from controller.editor import EditorController
from model.timeline import Clip, Timeline, Transition, VideoSource

ffmpeg_bin = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(
    ffmpeg_bin is None, reason="ffmpeg not installed"
)

PROBE_META = {
    "duration": 30.0,
    "width": 1280,
    "height": 720,
    "fps": 30.0,
    "has_audio": True,
}


def _make_testsrc(path: Path, *, duration: float = 20.0) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=1280x720:rate=30:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_timeline_fade_defaults_backward_compat():
    tl = Timeline.model_validate(
        {"sources": {}, "clips": [], "transitions": [], "output_fps": 30}
    )
    assert tl.fade_in_sec == 0.0
    assert tl.fade_out_sec == 0.0
    assert tl.fade_audio is True


def test_filter_timeline_fade_in_out_video_and_audio():
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=40.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    tl = Timeline(
        sources={"src1": src},
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=30)],
        fade_in_sec=5.0,
        fade_out_sec=10.0,
        fade_audio=True,
    )
    fc = build_filter_complex(tl)
    assert "fade=t=in:st=0:d=5.000000" in fc
    assert "fade=t=out:st=20.000000:d=10.000000" in fc
    assert "afade=t=in:st=0:d=5.000000" in fc
    assert "afade=t=out:st=20.000000:d=10.000000" in fc
    assert "[vout]" in fc
    assert "[aout]" in fc


def test_filter_fade_out_only_no_audio_fade_when_disabled():
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=20.0,
        width=640,
        height=360,
        fps=30.0,
        has_audio=True,
    )
    tl = Timeline(
        sources={"src1": src},
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=12)],
        fade_out_sec=3.0,
        fade_audio=False,
    )
    fc = build_filter_complex(tl)
    assert "fade=t=out:st=9.000000:d=3.000000" in fc
    assert "fade=t=in:" not in fc
    assert "afade=" not in fc


def test_filter_fades_with_xfade_still_apply_on_final():
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=20.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    tl = Timeline(
        sources={"src1": src},
        clips=[
            Clip(id="c0", source_id="src1", in_sec=0, out_sec=6),
            Clip(id="c1", source_id="src1", in_sec=6, out_sec=12),
        ],
        transitions=[Transition(after_clip_index=0, type="fade", duration=0.5)],
        fade_in_sec=2.0,
        fade_out_sec=2.0,
    )
    # total = 6+6-0.5 = 11.5
    fc = build_filter_complex(tl)
    assert "xfade=transition=fade" in fc
    assert "fade=t=in:st=0:d=2.000000" in fc
    assert "fade=t=out:st=9.500000:d=2.000000" in fc
    assert "afade=t=in:st=0:d=2.000000" in fc


def test_set_timeline_fades_partial_update(tmp_path: Path):
    editor = EditorController(workspace=tmp_path)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with patch("controller.editor.probe", return_value=PROBE_META):
        src = editor.import_video(str(video))
    editor.add_clip(src.id, 0.0, 20.0)

    editor.set_timeline_fades(fade_in_sec=5.0, fade_out_sec=10.0)
    assert editor.timeline.fade_in_sec == 5.0
    assert editor.timeline.fade_out_sec == 10.0
    assert editor.timeline.fade_audio is True

    editor.set_timeline_fades(fade_out_sec=3.0)
    assert editor.timeline.fade_in_sec == 5.0
    assert editor.timeline.fade_out_sec == 3.0

    summary = editor.get_state_summary(lang="fi")
    assert summary["fade_in_sec"] == 5.0
    assert summary["fade_out_sec"] == 3.0
    assert summary["timeline_fade_label"] is not None
    assert "5" in summary["timeline_fade_label"]


@needs_ffmpeg
def test_compile_timeline_with_fades_integration(tmp_path: Path):
    src_path = tmp_path / "src.mp4"
    _make_testsrc(src_path, duration=20.0)
    out = tmp_path / "faded.mp4"

    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path=str(src_path),
                duration=20.0,
                width=1280,
                height=720,
                fps=30.0,
                has_audio=True,
            )
        },
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=20)],
        fade_in_sec=5.0,
        fade_out_sec=10.0,
        fade_audio=True,
        output_fps=30,
    )
    compile_timeline(tl, out, mode="preview")
    assert out.is_file() and out.stat().st_size > 1000
    meta = probe(str(out))
    assert meta["has_audio"] is True
    assert meta["duration"] == pytest.approx(20.0, abs=0.4)
    assert meta["width"] > 0
