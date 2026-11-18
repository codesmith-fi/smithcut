"""Erillinen ääniaikajana (audio_clips): detach, cut, move, FFmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from compiler.ffmpeg import build_filter_complex, compile_timeline
from compiler.ffprobe import probe
from controller.editor import EditorController
from model.timeline import AudioClip, Clip, Timeline, Transition, VideoSource

ffmpeg_bin = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(
    ffmpeg_bin is None, reason="ffmpeg not installed"
)


def _make_testsrc(path: Path, *, duration: float = 8.0) -> None:
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


def test_audio_clip_model_and_timeline_compat():
    tl = Timeline.model_validate(
        {"sources": {}, "clips": [], "transitions": [], "output_fps": 30}
    )
    assert tl.audio_clips == []

    clip = AudioClip(
        id="a1",
        source_path="/tmp/a.m4a",
        in_point=0.0,
        out_point=2.5,
        timeline_start=1.0,
        volume=0.8,
    )
    assert clip.duration == pytest.approx(2.5)
    tl2 = Timeline(audio_clips=[clip], mute_video_audio=True)
    assert len(tl2.audio_clips) == 1


def test_filter_audio_clips_uses_adelay_amix_and_skips_clip_audio():
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
            Clip(id="c0", source_id="src1", in_sec=0, out_sec=4),
            Clip(id="c1", source_id="src1", in_sec=4, out_sec=8),
        ],
        transitions=[Transition(after_clip_index=0, type="fade", duration=0.5)],
        mute_video_audio=True,
        audio_clips=[
            AudioClip(
                id="a0",
                source_path="/audio/bed.m4a",
                in_point=0.0,
                out_point=4.0,
                timeline_start=0.0,
            ),
            AudioClip(
                id="a1",
                source_path="/audio/bed.m4a",
                in_point=4.0,
                out_point=8.0,
                timeline_start=3.5,
                volume=0.5,
            ),
        ],
    )
    fc = build_filter_complex(tl)
    assert "acrossfade=" not in fc
    assert "[0:a]" not in fc  # ei klippiääntä
    assert "adelay=" in fc
    assert "amix=inputs=2:duration=longest:normalize=0" in fc
    assert "volume=0.500000" in fc
    assert "xfade=transition=fade" in fc
    assert "[a_bed]" in fc
    assert "[aout]" in fc


def test_filter_falls_back_when_audio_clips_empty():
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=10.0,
        width=640,
        height=360,
        fps=30.0,
        has_audio=True,
    )
    tl = Timeline(
        sources={"src1": src},
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=3)],
        audio_clips=[],
    )
    fc = build_filter_complex(tl)
    assert "atrim=start=0.0:end=3.0" in fc
    assert "adelay=" not in fc
    assert "[ac0]" not in fc


@needs_ffmpeg
def test_detach_cut_move_render_workflow(tmp_path: Path):
    """tuo → detach → leikkaa video + xfade → siirrä/leikkaa ääntä → ffprobe."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    editor = EditorController(workspace=workspace)

    src_path = tmp_path / "src.mp4"
    _make_testsrc(src_path, duration=8.0)

    src = editor.import_video(str(src_path))
    editor.add_clip(src.id, 0.0, 8.0)

    result = editor.detach_audio_to_timeline()
    assert result["mute_video_audio"] is True
    assert result["audio_clip_count"] == 1
    assert editor.timeline.mute_video_audio is True
    assert len(editor.timeline.audio_clips) == 1
    assert editor.timeline.audio_clips[0].timeline_start == pytest.approx(0.0)
    assert editor.timeline.audio_clips[0].duration == pytest.approx(8.0, abs=0.05)
    assert len(editor.timeline.audio_tracks) >= 1

    # Videosta pala pois – ääniklipit eivät siirry
    audio_before = editor.timeline.audio_clips[0].model_copy(deep=True)
    editor.cut_out_range(2.0, 1.0)
    assert len(editor.timeline.clips) >= 1
    assert editor.timeline.audio_clips[0].timeline_start == audio_before.timeline_start
    assert editor.timeline.audio_clips[0].duration == pytest.approx(
        audio_before.duration, abs=0.05
    )

    # Jaa video kahtia jos yksi klippi jäi, ja xfade
    if len(editor.timeline.clips) == 1:
        mid = editor.timeline.clips[0].duration / 2
        editor.split_clip(0, mid)
    if len(editor.timeline.clips) >= 2:
        left = editor.timeline.clips[0].duration
        right = editor.timeline.clips[1].duration
        dur = min(0.4, left - 0.05, right - 0.05)
        if dur > 0.05:
            editor.set_transition(0, "fade", dur)

    # Äänileikkaus ja siirto
    editor.cut_audio_range(1.0, 2.0, ripple=False)
    assert len(editor.timeline.audio_clips) >= 2
    editor.move_audio_clip(2, new_start_sec=2.5)
    assert editor.timeline.audio_clips[1].timeline_start == pytest.approx(2.5)
    editor.set_audio_clip_volume(volume=0.5)
    assert all(c.volume == pytest.approx(0.5) for c in editor.timeline.audio_clips)

    summary = editor.get_state_summary(lang="fi")
    assert summary["audio_clip_count"] >= 2
    assert summary["audio_clips"][0]["label"] == "A1"

    out = workspace / "out_audio_tl.mp4"
    compile_timeline(editor.timeline, out, mode="preview")
    assert out.is_file() and out.stat().st_size > 1000
    meta = probe(str(out))
    assert meta["has_audio"] is True
    assert meta["width"] > 0
    assert meta["duration"] > 1.0

    fc = build_filter_complex(editor.timeline, mode="preview")
    assert "adelay=" in fc
    assert "acrossfade=" not in fc


@needs_ffmpeg
def test_split_audio_clip_controller(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    editor = EditorController(workspace=workspace)
    src_path = tmp_path / "src.mp4"
    _make_testsrc(src_path, duration=6.0)
    src = editor.import_video(str(src_path))
    editor.add_clip(src.id, 0.0, 6.0)
    editor.detach_audio_to_timeline()
    left, right = editor.split_audio_clip(2.0)
    assert left.duration == pytest.approx(2.0, abs=0.05)
    assert right.timeline_start == pytest.approx(2.0)
    assert len(editor.timeline.audio_clips) == 2
