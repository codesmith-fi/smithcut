"""EditorController – atomiset Timeline-operaatiot + historia."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from compiler.ffmpeg import extract_clip_thumbnail
from compiler.ffmpeg import render as ffmpeg_render
from compiler.ffprobe import probe
from model.history import HistoryManager
from model.timeline import (
    BackgroundMusic,
    Clip,
    TextEffect,
    Timeline,
    Transition,
    VideoSource,
    ZoomEffect,
)

REGION_TARGETS: dict[str, tuple[float, float]] = {
    "center": (0.5, 0.5),
    "top": (0.5, 0.25),
    "bottom": (0.5, 0.75),
    "left": (0.25, 0.5),
    "right": (0.75, 0.5),
    "top_left": (0.25, 0.25),
    "top_right": (0.75, 0.25),
    "bottom_left": (0.25, 0.75),
    "bottom_right": (0.75, 0.75),
}

_SUMMARY_I18N: dict[str, dict[str, Any]] = {
    "fi": {
        "clip_title": "Klippi {n}",
        "effect_zoom": "Zoom",
        "effect_text": "Teksti",
        "bgm_on": "Taustamusiikki käytössä",
        "empty": "Aikajana on tyhjä – tuo video aloittaaksesi.",
        "description": (
            "Aikajanalla on {count} klippiä, kokonaiskesto {duration} s, "
            "siirtymiä {transitions}."
        ),
        "transitions": {
            "fadeblack": "Pimennys",
            "fade": "Häivytys",
            "wipeleft": "Pyyhkäisy vasemmalle",
            "wiperight": "Pyyhkäisy oikealle",
        },
    },
    "en": {
        "clip_title": "Clip {n}",
        "effect_zoom": "Zoom",
        "effect_text": "Text",
        "bgm_on": "Background music on",
        "empty": "Timeline is empty — import a video to get started.",
        "description": (
            "Timeline has {count} clip(s), total duration {duration} s, "
            "{transitions} transition(s)."
        ),
        "transitions": {
            "fadeblack": "Fade to black",
            "fade": "Fade",
            "wipeleft": "Wipe left",
            "wiperight": "Wipe right",
        },
    },
}

_CHAT_STUB: dict[str, str] = {
    "fi": (
        "Paikallinen kielimalli ei ole vielä kytketty. "
        "Käytä toistaiseksi klippinauhaa, zoomausta esikatselusta "
        "tai pikakomentoja. (Vaihe 3: Ollama / Qwen)"
    ),
    "en": (
        "The local language model is not connected yet. "
        "For now use the clip strip, preview zoom, or quick actions. "
        "(Phase 3: Ollama / Qwen)"
    ),
}


def normalize_lang(lang: str | None) -> str:
    value = (lang or "fi").strip().lower()
    return value if value in _SUMMARY_I18N else "fi"


def chat_stub_reply(lang: str = "fi") -> str:
    """Vaiheen 3 valmiusviesti käyttöliittymän kielellä."""
    return _CHAT_STUB[normalize_lang(lang)]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class EditorController:
    """Suorittaa atomiset editointioperaatiot ja hallitsee Undo/Redo-pinon."""

    def __init__(
        self,
        workspace: str | Path | None = None,
        timeline: Timeline | None = None,
    ) -> None:
        self.workspace = Path(workspace) if workspace else Path("workspace")
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeline = timeline or Timeline()
        self.history = HistoryManager()

    # ------------------------------------------------------------------
    # Sisäiset apurit
    # ------------------------------------------------------------------

    def _checkpoint(self) -> None:
        self.history.push(self.timeline)

    def _clip(self, clip_index: int) -> Clip:
        if clip_index < 0 or clip_index >= len(self.timeline.clips):
            raise IndexError(
                f"clip_index {clip_index} out of range "
                f"(0..{len(self.timeline.clips) - 1})"
            )
        return self.timeline.clips[clip_index]

    def _adjust_transitions_after_remove(self, removed_index: int) -> None:
        kept: list[Transition] = []
        for t in self.timeline.transitions:
            if t.after_clip_index == removed_index:
                continue
            if t.after_clip_index > removed_index:
                kept.append(
                    t.model_copy(update={"after_clip_index": t.after_clip_index - 1})
                )
            else:
                kept.append(t)
        # Poista siirtymät jotka osoittavat viimeisen klipin jälkeen
        n = len(self.timeline.clips)
        self.timeline.transitions = [
            t for t in kept if t.after_clip_index < n - 1
        ]

    def _adjust_transitions_after_insert(self, insert_at: int) -> None:
        updated: list[Transition] = []
        for t in self.timeline.transitions:
            if t.after_clip_index >= insert_at:
                updated.append(
                    t.model_copy(update={"after_clip_index": t.after_clip_index + 1})
                )
            else:
                updated.append(t)
        self.timeline.transitions = updated

    # ------------------------------------------------------------------
    # Julkinen API
    # ------------------------------------------------------------------

    def import_video(self, file_path: str) -> VideoSource:
        path = str(Path(file_path).resolve())
        meta = probe(path)
        source = VideoSource(
            id=_new_id("src"),
            path=path,
            duration=meta["duration"],
            width=meta["width"],
            height=meta["height"],
            fps=meta["fps"],
            has_audio=meta["has_audio"],
        )
        self._checkpoint()
        self.timeline.sources[source.id] = source
        return source

    def add_clip(
        self,
        source_id: str,
        start_sec: float,
        end_sec: float,
        insert_at: int | None = None,
    ) -> Clip:
        if source_id not in self.timeline.sources:
            raise KeyError(f"unknown source_id {source_id!r}")
        source = self.timeline.sources[source_id]
        if start_sec < 0 or end_sec > source.duration or end_sec <= start_sec:
            raise ValueError(
                f"invalid clip range [{start_sec}, {end_sec}] "
                f"for source duration {source.duration}"
            )

        clip = Clip(
            id=_new_id("clip"),
            source_id=source_id,
            in_sec=start_sec,
            out_sec=end_sec,
        )
        self._checkpoint()
        if insert_at is None:
            self.timeline.clips.append(clip)
        else:
            if insert_at < 0 or insert_at > len(self.timeline.clips):
                raise IndexError(f"insert_at {insert_at} out of range")
            self.timeline.clips.insert(insert_at, clip)
            self._adjust_transitions_after_insert(insert_at)
        return clip

    def trim_clip(self, clip_index: int, start_sec: float, end_sec: float) -> Clip:
        clip = self._clip(clip_index)
        source = self.timeline.sources[clip.source_id]
        if start_sec < 0 or end_sec > source.duration or end_sec <= start_sec:
            raise ValueError(
                f"invalid trim range [{start_sec}, {end_sec}] "
                f"for source duration {source.duration}"
            )
        self._checkpoint()
        tentative = clip.model_copy(
            update={"in_sec": start_sec, "out_sec": end_sec, "effects": []}
        )
        new_dur = tentative.duration
        kept = [
            e
            for e in clip.effects
            if e.start_sec + e.duration <= new_dur + 1e-9
        ]
        updated = tentative.model_copy(update={"effects": kept})
        self.timeline.clips[clip_index] = updated
        return updated

    def split_clip(self, clip_index: int, split_at_sec: float) -> tuple[Clip, Clip]:
        """Jaa klippi suhteellisessa ajassa (klipin alusta, ei lähdeajassa)."""
        clip = self._clip(clip_index)
        if split_at_sec <= 0 or split_at_sec >= clip.duration:
            raise ValueError(
                f"split_at_sec {split_at_sec} must be within (0, {clip.duration})"
            )
        source_split = clip.in_sec + split_at_sec * clip.speed

        left_effects = [
            e
            for e in clip.effects
            if e.start_sec + e.duration <= split_at_sec + 1e-9
        ]
        right_effects = []
        for e in clip.effects:
            if e.start_sec >= split_at_sec - 1e-9:
                right_effects.append(
                    e.model_copy(update={"start_sec": e.start_sec - split_at_sec})
                )

        left = clip.model_copy(
            update={
                "id": _new_id("clip"),
                "out_sec": source_split,
                "effects": left_effects,
            }
        )
        right = clip.model_copy(
            update={
                "id": _new_id("clip"),
                "in_sec": source_split,
                "effects": right_effects,
            }
        )

        self._checkpoint()
        self.timeline.clips[clip_index] = left
        self.timeline.clips.insert(clip_index + 1, right)
        self._adjust_transitions_after_insert(clip_index + 1)
        return left, right

    def remove_clip(self, clip_index: int) -> None:
        self._clip(clip_index)
        self._checkpoint()
        self.timeline.clips.pop(clip_index)
        self._adjust_transitions_after_remove(clip_index)

    def move_clip(self, from_index: int, to_index: int) -> None:
        """Siirrä klippi uuteen indeksiin (0-pohjainen)."""
        n = len(self.timeline.clips)
        if from_index < 0 or from_index >= n:
            raise IndexError(f"from_index {from_index} out of range")
        if to_index < 0 or to_index >= n:
            raise IndexError(f"to_index {to_index} out of range")
        if from_index == to_index:
            return

        self._checkpoint()
        clip = self.timeline.clips.pop(from_index)
        self.timeline.clips.insert(to_index, clip)
        # Indeksipohjaiset siirtymät eivät säily järjestyksen vaihdossa luotettavasti.
        self.timeline.transitions = []

    def set_transition(
        self,
        after_clip_index: int,
        transition_type: str = "fadeblack",
        duration: float = 0.4,
    ) -> Transition:
        if after_clip_index < 0 or after_clip_index >= len(self.timeline.clips) - 1:
            raise IndexError(
                f"after_clip_index {after_clip_index} requires a following clip"
            )
        left = self.timeline.clips[after_clip_index].duration
        right = self.timeline.clips[after_clip_index + 1].duration
        if duration >= left or duration >= right:
            raise ValueError(
                f"transition duration {duration}s must be shorter than "
                f"both adjacent clips ({left:.3f}s, {right:.3f}s)"
            )
        allowed = {"fadeblack", "fade", "wipeleft", "wiperight"}
        if transition_type not in allowed:
            raise ValueError(f"unsupported transition type {transition_type!r}")

        transition = Transition(
            after_clip_index=after_clip_index,
            type=transition_type,  # type: ignore[arg-type]
            duration=duration,
        )
        self._checkpoint()
        self.timeline.transitions = [
            t
            for t in self.timeline.transitions
            if t.after_clip_index != after_clip_index
        ]
        self.timeline.transitions.append(transition)
        self.timeline.transitions.sort(key=lambda t: t.after_clip_index)
        return transition

    def add_zoom(
        self,
        clip_index: int,
        start_sec: float,
        duration: float,
        region: str = "center",
        target_x: float | None = None,
        target_y: float | None = None,
        zoom_factor: float = 1.8,
    ) -> ZoomEffect:
        clip = self._clip(clip_index)
        if target_x is None or target_y is None:
            if region not in REGION_TARGETS:
                raise ValueError(f"unknown region {region!r}")
            rx, ry = REGION_TARGETS[region]
            target_x = rx if target_x is None else target_x
            target_y = ry if target_y is None else target_y

        effect = ZoomEffect(
            start_sec=start_sec,
            duration=duration,
            target_x=target_x,
            target_y=target_y,
            zoom_factor=zoom_factor,
        )
        if effect.start_sec + effect.duration > clip.duration + 1e-9:
            raise ValueError("zoom effect exceeds clip duration")

        self._checkpoint()
        updated = clip.model_copy(update={"effects": [*clip.effects, effect]})
        self.timeline.clips[clip_index] = updated
        return effect

    def add_text(
        self,
        clip_index: int,
        text: str,
        start_sec: float,
        duration: float,
        position: str = "bottom",
        font_style: str = "clean",
    ) -> TextEffect:
        clip = self._clip(clip_index)
        if position not in ("bottom", "top", "center"):
            raise ValueError(f"invalid position {position!r}")
        allowed_fonts = {"clean", "cinema", "mono", "impact"}
        if font_style not in allowed_fonts:
            raise ValueError(f"invalid font_style {font_style!r}")
        effect = TextEffect(
            text=text,
            start_sec=start_sec,
            duration=duration,
            position=position,  # type: ignore[arg-type]
            font_style=font_style,  # type: ignore[arg-type]
        )
        if effect.start_sec + effect.duration > clip.duration + 1e-9:
            raise ValueError("text effect exceeds clip duration")

        self._checkpoint()
        updated = clip.model_copy(update={"effects": [*clip.effects, effect]})
        self.timeline.clips[clip_index] = updated
        return effect

    def set_music(
        self,
        file_path: str,
        bgm_volume: float = 0.85,
        sfx_volume: float = 0.35,
        fade_out_sec: float = 2.0,
    ) -> BackgroundMusic:
        path = str(Path(file_path).resolve())
        if not Path(path).is_file():
            raise FileNotFoundError(f"music file not found: {path}")
        music = BackgroundMusic(
            path=path,
            bgm_volume=bgm_volume,
            sfx_volume=sfx_volume,
            fade_out_sec=fade_out_sec,
        )
        self._checkpoint()
        self.timeline.bgm = music
        return music

    def undo(self) -> bool:
        previous = self.history.undo(self.timeline)
        if previous is None:
            return False
        self.timeline = previous
        return True

    def redo(self) -> bool:
        nxt = self.history.redo(self.timeline)
        if nxt is None:
            return False
        self.timeline = nxt
        return True

    def get_state_summary(self, lang: str = "fi") -> dict[str, Any]:
        """Selkokielinen 1-pohjainen yhteenveto UI:ta ja LLM:ää varten."""
        lang = normalize_lang(lang)
        L = _SUMMARY_I18N[lang]

        clips_summary = []
        for i, clip in enumerate(self.timeline.clips):
            source = self.timeline.sources.get(clip.source_id)
            effects = []
            for effect in clip.effects:
                if isinstance(effect, ZoomEffect):
                    effects.append(
                        {
                            "type": "zoom",
                            "label": L["effect_zoom"],
                            "start_sec": effect.start_sec,
                            "duration": effect.duration,
                            "target_x": effect.target_x,
                            "target_y": effect.target_y,
                            "zoom_factor": effect.zoom_factor,
                        }
                    )
                elif isinstance(effect, TextEffect):
                    effects.append(
                        {
                            "type": "text",
                            "label": L["effect_text"],
                            "text": effect.text,
                            "start_sec": effect.start_sec,
                            "duration": effect.duration,
                            "position": effect.position,
                            "font_style": effect.font_style,
                        }
                    )
            clip_index = i + 1
            clips_summary.append(
                {
                    "index": clip_index,
                    "title": L["clip_title"].format(n=clip_index),
                    "id": clip.id,
                    "source_id": clip.source_id,
                    "source_path": source.path if source else None,
                    "in_sec": clip.in_sec,
                    "trim_start": clip.in_sec,
                    "out_sec": clip.out_sec,
                    "duration_sec": round(clip.duration, 3),
                    "speed": clip.speed,
                    "effects": effects,
                }
            )

        transitions_summary = []
        for t in self.timeline.transitions:
            after = t.after_clip_index + 1
            transitions_summary.append(
                {
                    "after_clip": after,
                    "type": t.type,
                    "label": L["transitions"].get(t.type, t.type),
                    "duration_sec": t.duration,
                }
            )

        sources_summary = [
            {
                "id": s.id,
                "path": s.path,
                "duration_sec": round(s.duration, 3),
                "resolution": f"{s.width}x{s.height}",
                "fps": s.fps,
                "has_audio": s.has_audio,
            }
            for s in self.timeline.sources.values()
        ]

        bgm = None
        if self.timeline.bgm is not None:
            bgm = {
                "path": self.timeline.bgm.path,
                "bgm_volume": self.timeline.bgm.bgm_volume,
                "sfx_volume": self.timeline.bgm.sfx_volume,
                "fade_out_sec": self.timeline.bgm.fade_out_sec,
                "label": L["bgm_on"],
            }

        total = round(self.timeline.total_duration(), 3)
        n_clips = len(self.timeline.clips)
        if n_clips == 0:
            description = L["empty"]
        else:
            description = L["description"].format(
                count=n_clips,
                duration=total,
                transitions=len(self.timeline.transitions),
            )

        return {
            "lang": lang,
            "description": description,
            "clip_count": n_clips,
            "clips": clips_summary,
            "transitions": transitions_summary,
            "sources": sources_summary,
            "bgm": bgm,
            "total_duration_sec": total,
            "output_fps": self.timeline.output_fps,
            "can_undo": self.history.can_undo(),
            "can_redo": self.history.can_redo(),
        }

    def get_clip_thumbnail(self, index: int) -> Path:
        """Palauta klipin aloitusframen pikkukuva (1-pohjainen indeksi).

        Välimuistittaa JPEG-kuvan workspace/thumbnails/ -kansioon avaimella
        (lähdepolku + mtime + trim_start).
        """
        if index < 1:
            raise IndexError(
                f"clip index must be 1-based (got {index}); first clip is 1"
            )
        clip = self._clip(index - 1)
        source = self.timeline.sources.get(clip.source_id)
        if source is None:
            raise KeyError(f"unknown source_id {clip.source_id!r}")

        source_path = Path(source.path)
        trim_start = float(clip.in_sec)
        try:
            mtime = source_path.stat().st_mtime if source_path.is_file() else 0.0
        except OSError:
            mtime = 0.0

        digest = hashlib.sha256(
            f"{source_path.resolve()}|{mtime:.6f}|{trim_start:.6f}".encode()
        ).hexdigest()[:16]
        thumb_dir = Path(self.workspace) / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        output_path = thumb_dir / f"{digest}.jpg"

        if output_path.is_file() and output_path.stat().st_size > 0:
            return output_path

        return extract_clip_thumbnail(source_path, trim_start, output_path)

    def render(self, mode: str = "preview") -> str:
        if mode not in ("preview", "final"):
            raise ValueError("mode must be 'preview' or 'final'")
        suffix = "preview" if mode == "preview" else "final"
        output = self.workspace / f"cutsmith_{suffix}.mp4"
        return ffmpeg_render(
            self.timeline,
            output_path=output,
            mode=mode,  # type: ignore[arg-type]
            workspace=self.workspace,
        )
