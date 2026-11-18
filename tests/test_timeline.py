"""Datamallin validointitestit."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from model.timeline import (
    Clip,
    TextEffect,
    Timeline,
    Transition,
    VideoSource,
    ZoomEffect,
)


def make_source(**overrides) -> VideoSource:
    data = {
        "id": "src1",
        "path": "/tmp/a.mp4",
        "duration": 30.0,
        "width": 1920,
        "height": 1080,
        "fps": 60.0,
        "has_audio": True,
    }
    data.update(overrides)
    return VideoSource(**data)


def test_video_source_requires_positive_fields():
    with pytest.raises(ValidationError):
        make_source(duration=0)
    with pytest.raises(ValidationError):
        make_source(width=0)


def test_zoom_effect_bounds():
    ZoomEffect(start_sec=0, duration=1, target_x=0.5, target_y=0.5, zoom_factor=1.5)
    with pytest.raises(ValidationError):
        ZoomEffect(start_sec=0, duration=1, target_x=0.5, target_y=0.5, zoom_factor=1.0)
    with pytest.raises(ValidationError):
        ZoomEffect(start_sec=0, duration=1, target_x=1.5, target_y=0.5, zoom_factor=2.0)


def test_text_effect_rejects_empty():
    with pytest.raises(ValidationError):
        TextEffect(text="  ", start_sec=0, duration=1)


def test_clip_out_must_exceed_in():
    with pytest.raises(ValidationError):
        Clip(id="c1", source_id="src1", in_sec=5, out_sec=5)


def test_clip_effect_must_fit_duration():
    with pytest.raises(ValidationError):
        Clip(
            id="c1",
            source_id="src1",
            in_sec=0,
            out_sec=2,
            effects=[
                ZoomEffect(
                    start_sec=0,
                    duration=3,
                    target_x=0.5,
                    target_y=0.5,
                    zoom_factor=2.0,
                )
            ],
        )


def test_clip_duration_respects_speed():
    clip = Clip(id="c1", source_id="src1", in_sec=0, out_sec=10, speed=2.0)
    assert clip.duration == pytest.approx(5.0)


def test_timeline_total_duration_with_transitions():
    src = make_source()
    clips = [
        Clip(id="c0", source_id="src1", in_sec=0, out_sec=5),
        Clip(id="c1", source_id="src1", in_sec=5, out_sec=10),
        Clip(id="c2", source_id="src1", in_sec=10, out_sec=15),
    ]
    transitions = [
        Transition(after_clip_index=0, duration=0.4),
        Transition(after_clip_index=1, duration=0.4),
    ]
    tl = Timeline(sources={"src1": src}, clips=clips, transitions=transitions)
    assert tl.total_duration() == pytest.approx(15.0 - 0.8)


def test_timeline_validate_consistency_unknown_source():
    tl = Timeline(
        sources={},
        clips=[Clip(id="c0", source_id="missing", in_sec=0, out_sec=1)],
    )
    with pytest.raises(ValueError, match="unknown source"):
        tl.validate_consistency()


def test_timeline_validate_transition_too_long():
    src = make_source()
    tl = Timeline(
        sources={"src1": src},
        clips=[
            Clip(id="c0", source_id="src1", in_sec=0, out_sec=1),
            Clip(id="c1", source_id="src1", in_sec=1, out_sec=2),
        ],
        transitions=[Transition(after_clip_index=0, duration=1.0)],
    )
    with pytest.raises(ValueError, match="shorter than both"):
        tl.validate_consistency()


def test_timeline_validate_clip_exceeds_source():
    src = make_source(duration=5.0)
    tl = Timeline(
        sources={"src1": src},
        clips=[Clip(id="c0", source_id="src1", in_sec=0, out_sec=6)],
    )
    with pytest.raises(ValueError, match="exceeds source"):
        tl.validate_consistency()
