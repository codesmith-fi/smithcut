"""Fonttityylien resoluutio ja drawtext-generointi."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from compiler.ffmpeg import build_filter_complex, reset_font_cache, resolve_font_file
from model.timeline import Clip, TextEffect, Timeline, VideoSource


@pytest.fixture(autouse=True)
def _reset_fonts():
    reset_font_cache()
    yield
    reset_font_cache()


def test_text_effect_font_style_default_and_validation():
    effect = TextEffect(text="Hi", start_sec=0, duration=1)
    assert effect.font_style == "clean"
    TextEffect(text="Hi", start_sec=0, duration=1, font_style="cinema")
    with pytest.raises(ValidationError):
        TextEffect(text="Hi", start_sec=0, duration=1, font_style="comic")  # type: ignore[arg-type]


def test_resolve_font_file_returns_existing_path_for_all_styles():
    for style in ("clean", "cinema", "mono", "impact"):
        path = resolve_font_file(style)
        assert path is not None
        assert Path(path).is_file()
        assert path.lower().endswith((".ttf", ".otf"))


def test_resolve_font_file_falls_back_when_missing():
    with patch("compiler.ffmpeg._FONT_STYLE_PATHS", {"clean": ["/no/such/font.ttf"]}):
        with patch("compiler.ffmpeg._fc_match", return_value=None):
            reset_font_cache()
            # Yleisfallback voi silti löytyä levyltä
            path = resolve_font_file("clean")
            assert path is None or Path(path).is_file()


def _text_timeline(font_style: str = "mono") -> Timeline:
    src = VideoSource(
        id="src1",
        path="/videos/a.mp4",
        duration=20.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
    )
    return Timeline(
        sources={"src1": src},
        clips=[
            Clip(
                id="c0",
                source_id="src1",
                in_sec=0,
                out_sec=5,
                effects=[
                    TextEffect(
                        text="Hello",
                        start_sec=0,
                        duration=2,
                        font_style=font_style,  # type: ignore[arg-type]
                    )
                ],
            )
        ],
    )


def test_filter_complex_includes_fontfile_for_text():
    fc = build_filter_complex(_text_timeline("mono"))
    assert "drawtext=text='Hello'" in fc
    assert "fontfile=" in fc


def test_filter_complex_works_without_fontfile():
    with patch("compiler.ffmpeg.resolve_font_file", return_value=None):
        fc = build_filter_complex(_text_timeline("cinema"))
    assert "drawtext=text='Hello'" in fc
    assert "fontfile=" not in fc
