"""Puhdas Pydantic v2 -datamalli CutSmith-projektille.

Ei FFmpeg-, aliprosessi- eikä UI-riippuvuuksia.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class VideoSource(BaseModel):
    id: str
    path: str
    duration: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    has_audio: bool = True


class ZoomEffect(BaseModel):
    type: Literal["zoom"] = "zoom"
    start_sec: float = Field(ge=0)
    duration: float = Field(gt=0)
    target_x: float = Field(ge=0.0, le=1.0)
    target_y: float = Field(ge=0.0, le=1.0)
    zoom_factor: float = Field(ge=1.1, le=4.0)


FontStyle = Literal["clean", "cinema", "mono", "impact"]


class TextEffect(BaseModel):
    type: Literal["text"] = "text"
    text: str
    start_sec: float = Field(ge=0)
    duration: float = Field(gt=0)
    position: Literal["bottom", "top", "center"] = "bottom"
    font_size: int = Field(default=36, gt=0)
    color: str = "white"
    font_style: FontStyle = "clean"

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        return value


class BlurEffect(BaseModel):
    """Pehmeä focus-in/out -sumennus klipin aikavälillä."""

    type: Literal["blur"] = "blur"
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    max_sigma: float = Field(default=18.0, gt=0.0, le=50.0)
    fade_sec: float = Field(default=0.6, gt=0.0)

    @model_validator(mode="after")
    def validate_blur_range(self) -> BlurEffect:
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        return self

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


Effect = Annotated[
    Union[ZoomEffect, TextEffect, BlurEffect], Field(discriminator="type")
]


class Clip(BaseModel):
    id: str
    source_id: str
    in_sec: float = Field(ge=0)
    out_sec: float = Field(gt=0)
    speed: float = Field(default=1.0, gt=0)
    effects: list[Effect] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> Clip:
        if self.out_sec <= self.in_sec:
            raise ValueError("out_sec must be greater than in_sec")
        clip_dur = self.duration
        for effect in self.effects:
            if effect.start_sec + effect.duration > clip_dur + 1e-9:
                raise ValueError(
                    f"effect exceeds clip duration "
                    f"({effect.start_sec + effect.duration:.3f}s > {clip_dur:.3f}s)"
                )
        return self

    @property
    def source_duration(self) -> float:
        return self.out_sec - self.in_sec

    @property
    def duration(self) -> float:
        return self.source_duration / self.speed


class Transition(BaseModel):
    after_clip_index: int = Field(ge=0)
    type: Literal["fadeblack", "fade", "wipeleft", "wiperight"] = "fadeblack"
    duration: float = Field(default=0.4, gt=0)


class BackgroundMusic(BaseModel):
    path: str
    bgm_volume: float = Field(default=0.85, ge=0.0, le=2.0)
    sfx_volume: float = Field(default=0.35, ge=0.0, le=2.0)
    fade_out_sec: float = Field(default=2.0, ge=0.0)


class AudioTrack(BaseModel):
    """Itsenäinen ääniraita (tuotu tai videosta irrotettu)."""

    id: str
    path: str
    duration: float = Field(gt=0)
    origin: Literal["extracted", "imported"] = "imported"
    source_video_id: Optional[str] = None


class Timeline(BaseModel):
    sources: dict[str, VideoSource] = Field(default_factory=dict)
    clips: list[Clip] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    bgm: Optional[BackgroundMusic] = None
    # Kun True, videoklippien omaa ääntä ei käytetä (eikä acrossfadea)
    mute_video_audio: bool = False
    # Materiaalipoolin ääniraidat (Ääniraita 1, 2, …)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    # Aktiivinen pool-ääniraita (id) – soi katkeamattomana koko aikajanan yli
    active_audio_track_id: Optional[str] = None
    active_audio_start_sec: float = Field(default=0.0, ge=0.0)
    # Koko aikajanan alku-/loppuhäivytys mustasta/mustaan
    fade_in_sec: float = Field(default=0.0, ge=0.0)
    fade_out_sec: float = Field(default=0.0, ge=0.0)
    fade_audio: bool = True
    output_fps: int = Field(default=60, gt=0)

    def audio_track_by_id(self, track_id: str) -> AudioTrack | None:
        for track in self.audio_tracks:
            if track.id == track_id:
                return track
        return None

    def active_audio_track(self) -> AudioTrack | None:
        if not self.active_audio_track_id:
            return None
        return self.audio_track_by_id(self.active_audio_track_id)

    def clip_duration(self, index: int) -> float:
        return self.clips[index].duration

    def transition_after(self, clip_index: int) -> Transition | None:
        for transition in self.transitions:
            if transition.after_clip_index == clip_index:
                return transition
        return None

    def total_duration(self) -> float:
        if not self.clips:
            return 0.0
        total = sum(clip.duration for clip in self.clips)
        for transition in self.transitions:
            if 0 <= transition.after_clip_index < len(self.clips) - 1:
                total -= transition.duration
        return max(total, 0.0)

    def validate_consistency(self) -> None:
        """Validoi lähteet, klipit ja siirtymät toisiaan vasten."""
        for i, clip in enumerate(self.clips):
            if clip.source_id not in self.sources:
                raise ValueError(f"clip[{i}] references unknown source_id {clip.source_id!r}")
            source = self.sources[clip.source_id]
            if clip.out_sec > source.duration + 1e-6:
                raise ValueError(
                    f"clip[{i}] out_sec {clip.out_sec} exceeds source duration {source.duration}"
                )

        seen: set[int] = set()
        for transition in self.transitions:
            idx = transition.after_clip_index
            if idx in seen:
                raise ValueError(f"duplicate transition after_clip_index={idx}")
            seen.add(idx)
            if idx >= len(self.clips) - 1:
                raise ValueError(
                    f"transition after_clip_index={idx} requires a following clip"
                )
            left = self.clips[idx].duration
            right = self.clips[idx + 1].duration
            if transition.duration >= left or transition.duration >= right:
                raise ValueError(
                    f"transition after clip {idx} duration {transition.duration}s "
                    f"must be shorter than both adjacent clips ({left:.3f}s, {right:.3f}s)"
                )
