"""Aito FFmpeg-integraatio: preview-renderöinti testsrc-videolla."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from compiler.ffmpeg import compile_timeline, format_ffmpeg_error
from compiler.ffprobe import probe
from model.timeline import (
    BlurEffect,
    Clip,
    TextEffect,
    Timeline,
    Transition,
    VideoSource,
    ZoomEffect,
)

ffmpeg_bin = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(ffmpeg_bin is None, reason="ffmpeg not installed")


def _make_testsrc(path: Path, *, duration: float = 6.0, size: str = "1280x720") -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={size}:rate=30:duration={duration}",
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
    assert path.is_file()


def test_format_ffmpeg_error_prefers_tail_and_error_lines():
    stderr = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/tmp/x.mp4':\n"
        "  Metadata:\n"
        "    encoder         : Lavf\n"
        "  Duration: 00:00:08.00\n"
        "Stream mapping:\n"
        "[Parsed_pad_5 @ 0x1] Padded dimensions cannot be smaller than input dimensions.\n"
        "Error while filtering: Invalid argument\n"
        "Conversion failed!\n"
    )
    msg = format_ffmpeg_error(stderr)
    assert "Input #0" not in msg
    assert "Padded dimensions" in msg
    assert "Invalid argument" in msg
    assert "Conversion failed" in msg


def test_compile_timeline_preview_with_trim_start(tmp_path: Path):
    src = tmp_path / "src.mp4"
    out = tmp_path / "preview.mp4"
    _make_testsrc(src, duration=6.0, size="1280x720")

    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path=str(src),
                duration=6.0,
                width=1280,
                height=720,
                fps=30.0,
                has_audio=True,
            )
        },
        clips=[Clip(id="c0", source_id="src1", in_sec=1.25, out_sec=4.5)],
        output_fps=30,
    )

    result = compile_timeline(tl, out, mode="preview")
    assert Path(result).is_file()
    assert Path(result).stat().st_size > 1000


def test_compile_timeline_preview_with_effects_and_trim(tmp_path: Path):
    src = tmp_path / "src.mp4"
    out = tmp_path / "preview_fx.mp4"
    _make_testsrc(src, duration=5.0, size="1920x1080")

    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path=str(src),
                duration=5.0,
                width=1920,
                height=1080,
                fps=30.0,
                has_audio=True,
            )
        },
        clips=[
            Clip(
                id="c0",
                source_id="src1",
                in_sec=0.5,
                out_sec=4.0,
                effects=[
                    ZoomEffect(
                        start_sec=0.2,
                        duration=1.0,
                        target_x=0.4,
                        target_y=0.6,
                        zoom_factor=1.6,
                    ),
                    TextEffect(text="Preview", start_sec=0.0, duration=1.5),
                ],
            )
        ],
        output_fps=30,
    )

    result = compile_timeline(tl, out, mode="preview")
    assert Path(result).is_file()
    assert Path(result).stat().st_size > 1000


def test_compile_timeline_xfade_fade_is_valid_mp4(tmp_path: Path):
    """Aito FFmpeg: 2 klippiä + fade-xfade → validi MP4, oikea kesto, videovirta."""
    src = tmp_path / "src.mp4"
    out = tmp_path / "xfade_preview.mp4"
    _make_testsrc(src, duration=8.0, size="1280x720")

    clip1_dur = 3.0
    clip2_dur = 3.0
    fade_dur = 1.0
    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path=str(src),
                duration=8.0,
                width=1280,
                height=720,
                fps=30.0,
                has_audio=True,
            )
        },
        clips=[
            Clip(id="c0", source_id="src1", in_sec=0.0, out_sec=clip1_dur),
            Clip(id="c1", source_id="src1", in_sec=3.0, out_sec=3.0 + clip2_dur),
        ],
        transitions=[
            Transition(after_clip_index=0, type="fade", duration=fade_dur),
        ],
        output_fps=30,
    )

    result = compile_timeline(tl, out, mode="preview")
    assert Path(result).is_file()
    assert Path(result).stat().st_size > 1000

    meta = probe(Path(result))
    expected = clip1_dur + clip2_dur - fade_dur
    assert meta["duration"] == pytest.approx(expected, abs=0.35)
    assert meta["width"] > 0 and meta["height"] > 0
    assert meta["fps"] > 0


def test_compile_timeline_xfade_without_audio_source(tmp_path: Path):
    """xfade toimii myös kun lähteellä ei ole ääniraitaa (hiljaisuus + acrossfade)."""
    src = tmp_path / "silent.mp4"
    out = tmp_path / "xfade_silent.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=30:duration=5",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(src),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr

    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path=str(src),
                duration=5.0,
                width=640,
                height=360,
                fps=30.0,
                has_audio=False,
            )
        },
        clips=[
            Clip(id="c0", source_id="src1", in_sec=0.0, out_sec=2.5),
            Clip(id="c1", source_id="src1", in_sec=2.5, out_sec=5.0),
        ],
        transitions=[Transition(after_clip_index=0, type="fade", duration=0.5)],
        output_fps=30,
    )
    path = compile_timeline(tl, out, mode="preview")
    assert Path(path).is_file()
    meta = probe(Path(path))
    assert meta["duration"] == pytest.approx(4.5, abs=0.4)
    assert meta["width"] > 0


def test_compile_timeline_animated_zoom_renders_cleanly_at_edges(tmp_path: Path):
    """Aito FFmpeg: animoitu (inkrementaalinen) zoomaus kuvan reunaan ilman virheitä."""
    src = tmp_path / "src.mp4"
    out = tmp_path / "animated_zoom.mp4"
    _make_testsrc(src, duration=4.0, size="1280x720")

    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path=str(src),
                duration=4.0,
                width=1280,
                height=720,
                fps=30.0,
                has_audio=True,
            )
        },
        clips=[
            Clip(
                id="c0",
                source_id="src1",
                in_sec=0.0,
                out_sec=4.0,
                effects=[
                    ZoomEffect(
                        start_sec=0.5,
                        duration=2.5,
                        target_x=0.05,
                        target_y=0.95,
                        zoom_factor=2.2,
                        animated=True,
                    )
                ],
            )
        ],
        output_fps=30,
    )
    result = compile_timeline(tl, out, mode="preview")
    assert Path(result).is_file()
    assert Path(result).stat().st_size > 1000
    meta = probe(Path(result))
    assert meta["duration"] == pytest.approx(4.0, abs=0.35)
    assert meta["width"] > 0 and meta["height"] > 0


def test_compile_timeline_progressive_blur_fade_in_renders_cleanly(tmp_path: Path):
    """Aito FFmpeg: animoitu zoom + asteittainen blur (fade_in) klipin loppuun."""
    src = tmp_path / "src.mp4"
    out = tmp_path / "blur_fade_in.mp4"
    _make_testsrc(src, duration=4.0, size="1280x720")

    tl = Timeline(
        sources={
            "src1": VideoSource(
                id="src1",
                path=str(src),
                duration=4.0,
                width=1280,
                height=720,
                fps=30.0,
                has_audio=True,
            )
        },
        clips=[
            Clip(
                id="c0",
                source_id="src1",
                in_sec=0.0,
                out_sec=4.0,
                effects=[
                    ZoomEffect(
                        start_sec=0.5,
                        duration=2.0,
                        target_x=0.5,
                        target_y=0.5,
                        zoom_factor=1.6,
                        animated=True,
                    ),
                    BlurEffect(
                        start_sec=1.5,
                        end_sec=None,
                        strength=14.0,
                        mode="fade_in",
                    ),
                ],
            )
        ],
        output_fps=30,
    )
    result = compile_timeline(tl, out, mode="preview")
    assert Path(result).is_file()
    assert Path(result).stat().st_size > 1000
    meta = probe(Path(result))
    assert meta["duration"] == pytest.approx(4.0, abs=0.35)
    assert meta["width"] > 0 and meta["height"] > 0
