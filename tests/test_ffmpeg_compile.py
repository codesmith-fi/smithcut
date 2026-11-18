"""FFmpeg filter_complex -generoinnin testit (ffprobe mockattu)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from compiler.ffmpeg import (
    PREVIEW_HEIGHT,
    build_command,
    build_filter_complex,
    is_simple_preview_timeline,
    normalize_render_mode,
    reset_nvenc_cache,
    validate_timeline,
)
from model.timeline import (
    BackgroundMusic,
    Clip,
    TextEffect,
    Timeline,
    Transition,
    VideoSource,
    ZoomEffect,
)


@pytest.fixture(autouse=True)
def _reset_nvenc():
    reset_nvenc_cache()
    yield
    reset_nvenc_cache()


def make_three_clip_timeline() -> Timeline:
    src = VideoSource(
        id="src1",
        path="/videos/clip_source.mp4",
        duration=60.0,
        width=1920,
        height=1080,
        fps=60.0,
        has_audio=True,
    )
    clips = [
        Clip(
            id="c0",
            source_id="src1",
            in_sec=0,
            out_sec=5,
            effects=[
                ZoomEffect(
                    start_sec=0.5,
                    duration=2.0,
                    target_x=0.5,
                    target_y=0.5,
                    zoom_factor=1.8,
                )
            ],
        ),
        Clip(
            id="c1",
            source_id="src1",
            in_sec=10,
            out_sec=15,
            effects=[
                TextEffect(
                    text="Hello CutSmith",
                    start_sec=0.0,
                    duration=2.0,
                    position="bottom",
                )
            ],
        ),
        Clip(id="c2", source_id="src1", in_sec=20, out_sec=25),
    ]
    transitions = [
        Transition(after_clip_index=0, type="fadeblack", duration=0.4),
        Transition(after_clip_index=1, type="fade", duration=0.4),
    ]
    return Timeline(
        sources={"src1": src},
        clips=clips,
        transitions=transitions,
        output_fps=60,
    )


def test_validate_rejects_empty():
    with pytest.raises(ValueError, match="no clips"):
        validate_timeline(Timeline())


def test_three_clip_filter_contains_xfade_zoompan_drawtext():
    tl = make_three_clip_timeline()
    fc = build_filter_complex(tl, mode="preview")

    assert "xfade=transition=fadeblack" in fc
    assert "xfade=transition=fade" in fc
    assert "acrossfade=d=0.4" in fc
    assert "zoompan=" in fc
    assert "drawtext=text='Hello CutSmith'" in fc
    assert "[vout]" in fc
    assert "[aout]" in fc

    # Offsetit: clip0=5s, transition 0.4 → first offset 4.6
    assert "offset=4.600000" in fc
    # Toinen: 5 + 5 - 0.4 - 0.4 = 9.2
    assert "offset=9.200000" in fc

    # Preview-skaalaus: scale=-2:480 + parilliset mitat
    assert f"scale=-2:{PREVIEW_HEIGHT}" in fc
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in fc


def test_filter_uses_trim_and_setpts_per_clip():
    tl = make_three_clip_timeline()
    fc = build_filter_complex(tl)
    assert "trim=start=0.0:end=5.0" in fc
    assert "trim=start=10.0:end=15.0" in fc
    assert "trim=start=20.0:end=25.0" in fc
    assert "atrim=start=0.0:end=5.0" in fc


def test_hard_cut_uses_concat_when_no_transition():
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
            Clip(id="c0", source_id="src1", in_sec=0, out_sec=3),
            Clip(id="c1", source_id="src1", in_sec=3, out_sec=6),
        ],
    )
    fc = build_filter_complex(tl)
    assert "concat=n=2:v=1:a=0" in fc
    assert "xfade=" not in fc


def test_bgm_mix_in_filter():
    tl = make_three_clip_timeline()
    tl.bgm = BackgroundMusic(path="/music/bg.mp3", bgm_volume=0.5, sfx_volume=0.3)
    fc = build_filter_complex(tl)
    assert "amix=inputs=2" in fc
    assert "volume=0.3[a_sfx]" in fc
    assert "volume=0.5" in fc
    assert "afade=t=out" in fc


def test_normalize_render_mode():
    assert normalize_render_mode("preview") == "preview"
    assert normalize_render_mode("export") == "export"
    assert normalize_render_mode("final") == "export"


def test_preview_vs_export_profiles():
    tl = make_three_clip_timeline()
    preview = build_command(tl, "/tmp/out.mp4", mode="preview")
    assert preview[0] == "ffmpeg"
    assert "-filter_complex" in preview
    assert "libx264" in preview
    assert "ultrafast" in preview
    assert "28" in preview
    assert "-threads" in preview
    assert "96k" in preview
    # 1920x1080 → 480p: leveys parilliseksi (852)
    assert "852x480" in preview
    fc_preview = preview[preview.index("-filter_complex") + 1]
    assert f"scale=-2:{PREVIEW_HEIGHT}" in fc_preview

    with patch("compiler.ffmpeg.nvenc_available", return_value=False):
        export = build_command(tl, "/tmp/final.mp4", mode="export")
    assert "medium" in export
    assert "libx264" in export
    assert "18" in export
    assert "192k" in export
    assert "852x480" not in export
    fc_export = export[export.index("-filter_complex") + 1]
    assert "scale=1920:1080" in fc_export
    assert f"scale=-2:{PREVIEW_HEIGHT}" not in fc_export

    # final on alias exportille
    with patch("compiler.ffmpeg.nvenc_available", return_value=False):
        final = build_command(tl, "/tmp/final2.mp4", mode="final")
    assert "192k" in final
    assert "medium" in final


def test_simple_preview_timeline_detection():
    tl = make_three_clip_timeline()
    assert is_simple_preview_timeline(tl) is False
    simple = Timeline(
        sources=tl.sources,
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=3)],
    )
    assert is_simple_preview_timeline(simple) is True


def test_lc_all_c_in_render_env():
    tl = make_three_clip_timeline()
    with patch("compiler.ffmpeg.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        from compiler.ffmpeg import render

        render(tl, output_path="/tmp/out.mp4", mode="preview")
        env = run.call_args.kwargs["env"]
        assert env["LC_ALL"] == "C"


def test_silent_source_uses_anullsrc():
    src = VideoSource(
        id="src1",
        path="/videos/silent.mp4",
        duration=10.0,
        width=640,
        height=360,
        fps=30.0,
        has_audio=False,
    )
    tl = Timeline(
        sources={"src1": src},
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=2)],
    )
    fc = build_filter_complex(tl)
    assert "anullsrc=" in fc
