"""Itsenäinen ääniraidan hallinta: malli, controller, FFmpeg-integraatio."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from compiler.ffmpeg import build_command, build_filter_complex, compile_timeline
from compiler.ffprobe import probe
from controller.editor import EditorController
from model.timeline import AudioTrack, Clip, Timeline, Transition, VideoSource

ffmpeg_bin = shutil.which("ffmpeg")
ffprobe_bin = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(
    ffmpeg_bin is None or ffprobe_bin is None,
    reason="ffmpeg/ffprobe not installed",
)


PROBE_META = {
    "duration": 10.0,
    "width": 1280,
    "height": 720,
    "fps": 30.0,
    "has_audio": True,
}


def _make_testsrc(path: Path, *, duration: float = 8.0, size: str = "1280x720") -> None:
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


def _audio_stream_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    value = (result.stdout or "").strip()
    if value and value != "N/A":
        return float(value)
    # Fallback format-duration
    cmd2 = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result2 = subprocess.run(cmd2, capture_output=True, text=True, check=False)
    assert result2.returncode == 0, result2.stderr
    return float((result2.stdout or "").strip())


def test_timeline_mute_and_audio_tracks_defaults():
    tl = Timeline()
    assert tl.mute_video_audio is False
    assert tl.audio_tracks == []
    assert tl.active_audio_track_id is None


def test_timeline_backward_compat_without_audio_fields():
    tl = Timeline.model_validate(
        {
            "sources": {},
            "clips": [],
            "transitions": [],
            "output_fps": 30,
        }
    )
    assert tl.mute_video_audio is False
    assert tl.audio_tracks == []
    assert tl.active_audio_track() is None


def test_filter_mute_skips_clip_audio_and_acrossfade():
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
    )
    fc = build_filter_complex(tl)
    assert "acrossfade=" not in fc
    assert "[0:a]" not in fc
    assert "[1:a]" not in fc
    assert "xfade=transition=fade" in fc
    assert "anullsrc=" in fc
    assert "[aout]" in fc


def test_filter_continuous_audio_track_maps_bed():
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=20.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    track = AudioTrack(
        id="aud1",
        path="/audio/bed.m4a",
        duration=20.0,
        origin="extracted",
        source_video_id="src1",
    )
    tl = Timeline(
        sources={"src1": src},
        clips=[
            Clip(id="c0", source_id="src1", in_sec=0, out_sec=4),
            Clip(id="c1", source_id="src1", in_sec=4, out_sec=8),
        ],
        transitions=[Transition(after_clip_index=0, type="fade", duration=0.5)],
        mute_video_audio=True,
        audio_tracks=[track],
        active_audio_track_id="aud1",
        active_audio_start_sec=0.0,
    )
    fc = build_filter_complex(tl)
    assert "acrossfade=" not in fc
    assert "xfade=transition=fade" in fc
    # Kaksi klippi-inputtia → bed on input 2
    assert "[2:a]atrim=" in fc
    assert "aresample=48000" in fc
    assert "atrim=0:" in fc
    assert "[a_bed]" in fc

    cmd = build_command(tl, "/tmp/out.mp4", mode="preview")
    assert "/audio/bed.m4a" in cmd
    assert cmd.count("-i") == 3  # 2 clips + audio bed


def test_remove_and_attach_audio_track_controller(tmp_path: Path):
    editor = EditorController(workspace=tmp_path)
    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")

    with patch("controller.editor.probe", return_value=PROBE_META):
        src = editor.import_video(str(video))
    editor.add_clip(src.id, 0.0, 8.0)

    extracted = tmp_path / "audio" / "extracted.m4a"
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.write_bytes(b"audio")

    def fake_extract(source_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"aac")
        return output_path

    with (
        patch("controller.editor.extract_audio_track", side_effect=fake_extract),
        patch(
            "controller.editor.probe_audio",
            return_value={"duration": 8.0, "has_audio": True},
        ),
    ):
        result = editor.remove_video_audio(keep_in_pool=True)

    assert result["mute_video_audio"] is True
    assert editor.timeline.mute_video_audio is True
    assert len(editor.timeline.audio_tracks) == 1
    assert editor.timeline.audio_tracks[0].origin == "extracted"

    summary = editor.get_state_summary(lang="fi")
    assert summary["mute_video_audio"] is True
    assert "mykistetty" in summary["video_audio_status"].lower()
    assert summary["audio_tracks"][0]["label"] == "Ääniraita 1"
    assert summary["active_audio_track_index"] is None

    track = editor.attach_audio_track(track_index=1)
    assert track.id == editor.timeline.active_audio_track_id
    assert editor.timeline.mute_video_audio is True
    summary2 = editor.get_state_summary(lang="fi")
    assert summary2["active_audio_track_index"] == 1
    assert summary2["audio_tracks"][0]["active"] is True


@needs_ffmpeg
def test_workflow_mute_xfade_attach_continuous_audio(tmp_path: Path):
    """tuo → poista ääniraita → jaa + xfade → liitä Ääniraita 1 → ffprobe."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    editor = EditorController(workspace=workspace)

    src_path = tmp_path / "src.mp4"
    _make_testsrc(src_path, duration=6.0)

    src = editor.import_video(str(src_path))
    editor.add_clip(src.id, 0.0, 6.0)

    result = editor.remove_video_audio(keep_in_pool=True)
    assert result["mute_video_audio"] is True
    assert len(editor.timeline.audio_tracks) == 1
    assert Path(editor.timeline.audio_tracks[0].path).is_file()

    editor.split_clip(0, 3.0)
    assert len(editor.timeline.clips) == 2
    editor.set_transition(0, "fade", 0.5)
    assert editor.timeline.total_duration() == pytest.approx(5.5, abs=0.05)

    editor.attach_audio_track(track_index=1, start_sec=0.0)
    assert editor.timeline.active_audio_track_id is not None

    out = workspace / "out_audio.mp4"
    compile_timeline(editor.timeline, out, mode="preview")
    assert out.is_file()
    assert out.stat().st_size > 1000

    meta = probe(str(out))
    assert meta["has_audio"] is True
    assert meta["duration"] == pytest.approx(5.5, abs=0.35)
    assert meta["width"] > 0 and meta["height"] > 0

    audio_dur = _audio_stream_duration(out)
    # Katkeamaton bed ≈ videon kesto (ei acrossfade-katkoja)
    assert audio_dur == pytest.approx(meta["duration"], abs=0.35)

    fc = build_filter_complex(editor.timeline, mode="preview")
    assert "acrossfade=" not in fc
    assert "xfade=transition=fade" in fc
    assert "[a_bed]" in fc
