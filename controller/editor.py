"""EditorController – atomiset Timeline-operaatiot + historia."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from compiler.ffmpeg import extract_clip_thumbnail, extract_still_frame
from compiler.ffmpeg import render as ffmpeg_render
from compiler.ffprobe import probe
from model.history import HistoryManager
from model.timeline import (
    BackgroundMusic,
    BlurEffect,
    Clip,
    TextEffect,
    Timeline,
    Transition,
    VideoSource,
    ZoomEffect,
)

PROJECT_STATE_FILENAME = "project_state.json"
PROJECT_STATE_VERSION = 1

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
        "effect_blur": "Blur",
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
        "effect_blur": "Blur",
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


def format_timecode(sec: float) -> str:
    """Muotoile sekunnit MM:SS.d -aikakoodiksi."""
    sec = max(0.0, float(sec))
    minutes = int(sec // 60)
    seconds = sec - minutes * 60
    return f"{minutes:02d}:{seconds:04.1f}"


class EditorController:
    """Suorittaa atomiset editointioperaatiot ja hallitsee Undo/Redo-pinon."""

    def __init__(
        self,
        workspace: str | Path | None = None,
        timeline: Timeline | None = None,
        *,
        load_persisted: bool = True,
    ) -> None:
        self.workspace = Path(workspace) if workspace else Path("workspace")
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.history = HistoryManager()
        self.playhead_sec: float = 0.0
        self.selection_start_sec: float | None = None
        self.selection_end_sec: float | None = None
        self._progress: dict[str, Any] = {
            "active": False,
            "phase": "idle",
            "percent": None,
            "detail": "",
        }
        self._persist_enabled = True
        self._state_dirty = False
        if timeline is not None:
            self.timeline = timeline
        elif load_persisted and self._load_project_state():
            pass  # timeline + playhead/selection ladattu
        else:
            self.timeline = Timeline()

    # Valinnan minimikesto (sekunteina); lyhyemmät nollataan
    SELECTION_MIN_SEC = 0.05

    # ------------------------------------------------------------------
    # Sisäiset apurit
    # ------------------------------------------------------------------

    def _project_state_path(self) -> Path:
        return Path(self.workspace) / PROJECT_STATE_FILENAME

    def _persist(self) -> None:
        """Tallenna projektin tila atomisesti workspace/project_state.json -tiedostoon."""
        if not self._persist_enabled:
            return
        path = self._project_state_path()
        payload = {
            "version": PROJECT_STATE_VERSION,
            "playhead_sec": self.playhead_sec,
            "selection_start_sec": self.selection_start_sec,
            "selection_end_sec": self.selection_end_sec,
            "timeline": self.timeline.model_dump(mode="json"),
        }
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
            self._state_dirty = False
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_project_state(self) -> bool:
        """Lataa tila levyltä jos lähdevideot ovat yhä olemassa. Palauta True jos ok."""
        path = self._project_state_path()
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_tl = data.get("timeline") or {}
            timeline = Timeline.model_validate(raw_tl)
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return False

        # Pidä vain lähteet joiden tiedostot ovat levyllä
        kept_sources: dict[str, VideoSource] = {}
        for sid, source in timeline.sources.items():
            if Path(source.path).is_file():
                kept_sources[sid] = source
        if not kept_sources and timeline.sources:
            return False

        kept_clips = [c for c in timeline.clips if c.source_id in kept_sources]
        n = len(kept_clips)
        kept_transitions = [
            t
            for t in timeline.transitions
            if 0 <= t.after_clip_index < n - 1
        ]
        bgm = timeline.bgm
        if bgm is not None and not Path(bgm.path).is_file():
            bgm = None

        self.timeline = Timeline(
            sources=kept_sources,
            clips=kept_clips,
            transitions=kept_transitions,
            bgm=bgm,
            output_fps=timeline.output_fps,
        )
        try:
            self.playhead_sec = float(data.get("playhead_sec") or 0.0)
        except (TypeError, ValueError):
            self.playhead_sec = 0.0
        start = data.get("selection_start_sec")
        end = data.get("selection_end_sec")
        try:
            self.selection_start_sec = (
                float(start) if start is not None else None
            )
            self.selection_end_sec = float(end) if end is not None else None
        except (TypeError, ValueError):
            self.selection_start_sec = None
            self.selection_end_sec = None
        self._clamp_selection_to_total()
        total = self.timeline.total_duration()
        self.playhead_sec = (
            min(max(0.0, self.playhead_sec), total) if total > 0 else 0.0
        )
        return True

    def _checkpoint(self) -> None:
        self.history.push(self.timeline)
        self._state_dirty = True

    def _flush_persist(self) -> None:
        """Kirjoita tila levylle jos mutaatioita on pending."""
        if getattr(self, "_state_dirty", False):
            self._persist()
            self._state_dirty = False

    def set_progress(
        self,
        *,
        active: bool,
        phase: str = "idle",
        percent: int | None = None,
        detail: str = "",
    ) -> None:
        """Päivitä ajonaikainen edistymistila (render / chat)."""
        clamped = None if percent is None else min(100, max(0, int(percent)))
        self._progress = {
            "active": bool(active),
            "phase": phase,
            "percent": clamped,
            "detail": detail,
        }

    def clear_progress(self) -> None:
        self.set_progress(active=False, phase="idle", percent=None, detail="")

    def get_progress(self) -> dict[str, Any]:
        """Palauta kevyt kopio edistymistilasta (pollaukseen)."""
        return dict(self._progress)

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
        if duration <= 0:
            raise ValueError("transition duration must be > 0")
        if duration >= left or duration >= right:
            raise ValueError(
                f"transition duration {duration}s must be shorter than "
                f"both adjacent clips ({left:.3f}s, {right:.3f}s)"
            )
        allowed = {"fadeblack", "fade", "wipeleft", "wiperight"}
        if transition_type not in allowed:
            raise ValueError(f"unsupported transition type {transition_type!r}")

        # Varmista että duration mahtuu molempiin klipeihin (xfade-offset ≥ 0)
        safe_duration = min(float(duration), left - 1e-3, right - 1e-3)
        if safe_duration <= 0:
            raise ValueError(
                f"transition duration {duration}s does not fit adjacent clips "
                f"({left:.3f}s, {right:.3f}s)"
            )

        transition = Transition(
            after_clip_index=after_clip_index,
            type=transition_type,  # type: ignore[arg-type]
            duration=safe_duration,
        )
        self._checkpoint()
        self.timeline.transitions = [
            t
            for t in self.timeline.transitions
            if t.after_clip_index != after_clip_index
        ]
        self.timeline.transitions.append(transition)
        self.timeline.transitions.sort(key=lambda t: t.after_clip_index)
        self._flush_persist()
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

    def add_blur_effect(
        self,
        clip_index: int,
        start_sec: float,
        end_sec: float,
        *,
        max_sigma: float = 18.0,
        fade_sec: float = 0.6,
    ) -> BlurEffect:
        """Lisää pehmeä blur-siirtymä klipin paikalliseen aikaväliin."""
        clip = self._clip(clip_index)
        effect = BlurEffect(
            start_sec=float(start_sec),
            end_sec=float(end_sec),
            max_sigma=float(max_sigma),
            fade_sec=float(fade_sec),
        )
        if effect.end_sec > clip.duration + 1e-9:
            raise ValueError("blur effect exceeds clip duration")
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
        self._state_dirty = True
        self._flush_persist()
        return True

    def redo(self) -> bool:
        nxt = self.history.redo(self.timeline)
        if nxt is None:
            return False
        self.timeline = nxt
        self._state_dirty = True
        self._flush_persist()
        return True

    def get_state_summary(self, lang: str = "fi") -> dict[str, Any]:
        """Selkokielinen 1-pohjainen yhteenveto UI:ta ja LLM:ää varten."""
        lang = normalize_lang(lang)
        L = _SUMMARY_I18N[lang]

        spans = self.clip_spans()
        span_by_index = {i: (start, end) for i, start, end in spans}

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
                elif isinstance(effect, BlurEffect):
                    effects.append(
                        {
                            "type": "blur",
                            "label": L["effect_blur"],
                            "start_sec": effect.start_sec,
                            "end_sec": effect.end_sec,
                            "duration": effect.duration,
                            "max_sigma": effect.max_sigma,
                            "fade_sec": effect.fade_sec,
                        }
                    )
            clip_index = i + 1
            t_start, t_end = span_by_index.get(i, (0.0, clip.duration))
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
                    "timeline_start_sec": round(t_start, 3),
                    "timeline_end_sec": round(t_end, 3),
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

        # Pidä playhead / valinta synkassa kokonaiskestoon
        self.playhead_sec = min(self.playhead_sec, total) if total > 0 else 0.0
        self._clamp_selection_to_total(total)

        sel_start = self.selection_start_sec
        sel_end = self.selection_end_sec
        sel_duration = None
        if sel_start is not None and sel_end is not None:
            sel_duration = round(sel_end - sel_start, 3)

        self._flush_persist()

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
            "playhead_sec": round(self.playhead_sec, 3),
            "playhead_timecode": format_timecode(self.playhead_sec),
            "selection_start_sec": (
                round(sel_start, 3) if sel_start is not None else None
            ),
            "selection_end_sec": round(sel_end, 3) if sel_end is not None else None,
            "selection_duration_sec": sel_duration,
            "selection_timecode": (
                f"{format_timecode(sel_start)} – {format_timecode(sel_end)}"
                if sel_start is not None and sel_end is not None
                else None
            ),
            "can_undo": self.history.can_undo(),
            "can_redo": self.history.can_redo(),
        }

    # ------------------------------------------------------------------
    # Playhead & aikavälileikkaus
    # ------------------------------------------------------------------

    def clip_spans(self) -> list[tuple[int, float, float]]:
        """(clip_index, timeline_start, timeline_end) siirtymät huomioiden."""
        spans: list[tuple[int, float, float]] = []
        cursor = 0.0
        n = len(self.timeline.clips)
        for i, clip in enumerate(self.timeline.clips):
            start = cursor
            end = cursor + clip.duration
            spans.append((i, start, end))
            cursor = end
            transition = self.timeline.transition_after(i)
            if transition is not None and i < n - 1:
                cursor -= transition.duration
        return spans

    def set_playhead(self, sec: float) -> float:
        """Aseta playhead; raja-arvot 0 .. total_duration."""
        total = self.timeline.total_duration()
        value = float(sec)
        if value < 0:
            value = 0.0
        if total <= 0:
            value = 0.0
        elif value > total:
            value = total
        self.playhead_sec = value
        self._state_dirty = True
        self._flush_persist()
        return self.playhead_sec

    def locate_timeline_sec(self, sec: float) -> tuple[int, float] | None:
        """Palauta (clip_index, source_timestamp) aikajanan sekunnille."""
        if not self.timeline.clips:
            return None
        total = self.timeline.total_duration()
        sec = min(max(0.0, float(sec)), total if total > 0 else 0.0)
        spans = self.clip_spans()
        match: tuple[int, float, float] | None = None
        for span in spans:
            i, start, end = span
            if start <= sec <= end + 1e-9:
                match = span
        if match is None:
            match = spans[-1]
            sec = match[2]
        i, start, _end = match
        clip = self.timeline.clips[i]
        local = max(0.0, sec - start)
        # lokalisaatio klipin lähdeaikaan (speed huomioiden)
        source_ts = clip.in_sec + local * clip.speed
        source_ts = min(max(clip.in_sec, source_ts), clip.out_sec)
        return i, source_ts

    def get_frame_at_time(self, sec: float) -> Path:
        """Poimi still-JPEG aikajanan kohdasta (välimuisti workspace/frames/)."""
        located = self.locate_timeline_sec(sec)
        if located is None:
            raise ValueError("timeline has no clips")
        _clip_index, source_ts = located
        clip = self.timeline.clips[_clip_index]
        source = self.timeline.sources[clip.source_id]
        source_path = Path(source.path)
        try:
            mtime = source_path.stat().st_mtime if source_path.is_file() else 0.0
        except OSError:
            mtime = 0.0
        digest = hashlib.sha256(
            f"{source_path.resolve()}|{mtime:.6f}|{source_ts:.3f}".encode()
        ).hexdigest()[:16]
        frames_dir = Path(self.workspace) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        output = frames_dir / f"{digest}.jpg"
        if output.is_file() and output.stat().st_size > 0:
            return output
        return extract_still_frame(source_path, source_ts, output, scale="960:-1")

    def _effects_for_subclip(
        self,
        clip: Clip,
        local_start: float,
        local_end: float,
    ) -> list:
        """Siirrä/suodata efektit aliklippille [local_start, local_end) klipin ajassa."""
        kept = []
        span = local_end - local_start
        for effect in clip.effects:
            e_start = effect.start_sec
            e_end = effect.start_sec + effect.duration
            if e_end <= local_start + 1e-9 or e_start >= local_end - 1e-9:
                continue
            new_start = max(0.0, e_start - local_start)
            new_end = min(span, e_end - local_start)
            if new_end - new_start <= 1e-9:
                continue
            if isinstance(effect, BlurEffect):
                kept.append(
                    effect.model_copy(
                        update={"start_sec": new_start, "end_sec": new_end}
                    )
                )
            else:
                kept.append(
                    effect.model_copy(
                        update={
                            "start_sec": new_start,
                            "duration": new_end - new_start,
                        }
                    )
                )
        return kept

    def cut_out_range(self, start_sec: float, duration_sec: float) -> None:
        """Poista aikaväli [start_sec, start_sec+duration] aikajanalta."""
        if duration_sec <= 0:
            raise ValueError("duration_sec must be > 0")
        total = self.timeline.total_duration()
        if total <= 0:
            raise ValueError("timeline is empty")
        start = max(0.0, float(start_sec))
        end = min(total, start + float(duration_sec))
        if end <= start + 1e-9:
            raise ValueError("cut range is empty or outside timeline")

        spans = self.clip_spans()
        self._checkpoint()
        new_clips: list[Clip] = []

        for i, cs, ce in spans:
            clip = self.timeline.clips[i]
            if ce <= start + 1e-9 or cs >= end - 1e-9:
                new_clips.append(clip)
                continue
            if cs >= start - 1e-9 and ce <= end + 1e-9:
                # Koko klippi poistetaan
                continue

            # Osittainen osuma
            if cs < start and ce > end:
                # Jätä alku ja loppu
                left_local_end = (start - cs) * clip.speed
                right_local_start = (end - cs) * clip.speed
                left_out = clip.in_sec + left_local_end
                right_in = clip.in_sec + right_local_start
                left = clip.model_copy(
                    update={
                        "id": _new_id("clip"),
                        "out_sec": left_out,
                        "effects": self._effects_for_subclip(
                            clip, 0.0, start - cs
                        ),
                    }
                )
                right = clip.model_copy(
                    update={
                        "id": _new_id("clip"),
                        "in_sec": right_in,
                        "effects": self._effects_for_subclip(
                            clip, end - cs, clip.duration
                        ),
                    }
                )
                if left.out_sec > left.in_sec + 1e-9:
                    new_clips.append(left)
                if right.out_sec > right.in_sec + 1e-9:
                    new_clips.append(right)
            elif cs < start:
                # Trimmaa loppu kohtaan start
                local_end = (start - cs) * clip.speed
                updated = clip.model_copy(
                    update={
                        "id": _new_id("clip"),
                        "out_sec": clip.in_sec + local_end,
                        "effects": self._effects_for_subclip(clip, 0.0, start - cs),
                    }
                )
                if updated.out_sec > updated.in_sec + 1e-9:
                    new_clips.append(updated)
            else:
                # Trimmaa alku kohtaan end
                local_start = (end - cs) * clip.speed
                updated = clip.model_copy(
                    update={
                        "id": _new_id("clip"),
                        "in_sec": clip.in_sec + local_start,
                        "effects": self._effects_for_subclip(
                            clip, end - cs, clip.duration
                        ),
                    }
                )
                if updated.out_sec > updated.in_sec + 1e-9:
                    new_clips.append(updated)

        self.timeline.clips = new_clips
        self.timeline.transitions = []
        self.set_playhead(start)

    def cut_at_playhead(self, duration_sec: float) -> None:
        """Poista aikaväli nykyisestä playhead-kohdasta eteenpäin."""
        self.cut_out_range(self.playhead_sec, duration_sec)

    # ------------------------------------------------------------------
    # Aluevalinta (range selection)
    # ------------------------------------------------------------------

    def _clamp_selection_to_total(self, total: float | None = None) -> None:
        """Rajaa aktiivinen valinta kokonaiskestoon; nollaa jos epäkelpo."""
        if self.selection_start_sec is None or self.selection_end_sec is None:
            self.selection_start_sec = None
            self.selection_end_sec = None
            return
        if total is None:
            total = self.timeline.total_duration()
        if total <= 0:
            self.selection_start_sec = None
            self.selection_end_sec = None
            return
        start = min(max(0.0, float(self.selection_start_sec)), total)
        end = min(max(0.0, float(self.selection_end_sec)), total)
        if start > end:
            start, end = end, start
        if end - start < self.SELECTION_MIN_SEC:
            self.selection_start_sec = None
            self.selection_end_sec = None
            return
        self.selection_start_sec = start
        self.selection_end_sec = end

    def set_selection(
        self,
        start_sec: float | None,
        end_sec: float | None,
    ) -> tuple[float | None, float | None]:
        """Aseta valintaväli; normalisoi järjestyksen ja rajaa kestoon."""
        if start_sec is None or end_sec is None:
            self.selection_start_sec = None
            self.selection_end_sec = None
            self._state_dirty = True
            self._flush_persist()
            return None, None

        total = self.timeline.total_duration()
        if total <= 0:
            self.selection_start_sec = None
            self.selection_end_sec = None
            self._state_dirty = True
            self._flush_persist()
            return None, None

        start = min(max(0.0, float(start_sec)), total)
        end = min(max(0.0, float(end_sec)), total)
        if start > end:
            start, end = end, start
        if end - start < self.SELECTION_MIN_SEC:
            self.selection_start_sec = None
            self.selection_end_sec = None
            self._state_dirty = True
            self._flush_persist()
            return None, None

        self.selection_start_sec = start
        self.selection_end_sec = end
        self._state_dirty = True
        self._flush_persist()
        return self.selection_start_sec, self.selection_end_sec

    def clear_selection(self) -> None:
        """Tyhjennä aluevalinta."""
        self.selection_start_sec = None
        self.selection_end_sec = None
        self._state_dirty = True
        self._flush_persist()

    def has_selection(self) -> bool:
        return (
            self.selection_start_sec is not None
            and self.selection_end_sec is not None
            and self.selection_end_sec - self.selection_start_sec
            >= self.SELECTION_MIN_SEC
        )

    def cut_selected_range(self) -> None:
        """Leikkaa aktiivinen valintaväli pois aikajanalta."""
        if not self.has_selection():
            raise ValueError("no active selection range")
        start = float(self.selection_start_sec)  # type: ignore[arg-type]
        end = float(self.selection_end_sec)  # type: ignore[arg-type]
        duration = end - start
        self.cut_out_range(start, duration)
        self.clear_selection()

    def selection_boundary_index(self) -> int | None:
        """0-pohjainen after_clip_index, jos valinta ylittää klippien sauman."""
        if not self.has_selection():
            return None
        sel_s = float(self.selection_start_sec)  # type: ignore[arg-type]
        sel_e = float(self.selection_end_sec)  # type: ignore[arg-type]
        spans = self.clip_spans()
        for i in range(len(spans) - 1):
            _ai, a_start, a_end = spans[i]
            _bi, b_start, b_end = spans[i + 1]
            overlaps_a = sel_s < a_end - 1e-9 and sel_e > a_start + 1e-9
            overlaps_b = sel_s < b_end - 1e-9 and sel_e > b_start + 1e-9
            if overlaps_a and overlaps_b:
                return i
        return None

    def apply_crossfade_to_selection(
        self,
        transition_type: str = "fade",
        duration_sec: float | None = None,
        clip_index: int | None = None,
    ) -> Transition:
        """Aseta crossfade saumaan.

        Parametrit
        ----------
        clip_index:
            0-pohjainen indeksi ensimmäiselle klipille (sauma clip_index →
            clip_index+1). Jos None, käytetään valinnan ylittämää saumaa
            tai ainoaa saumaa kun aikajanalla on tasan kaksi klippiä.
        duration_sec:
            Siirtymän kesto. Jos None ja valinta on aktiivinen, käytetään
            valinnan pituutta.
        """
        n_clips = len(self.timeline.clips)
        if n_clips < 2:
            raise ValueError("crossfade requires at least two clips")

        boundary: int | None = None
        requested: float | None = (
            float(duration_sec) if duration_sec is not None else None
        )

        if clip_index is not None:
            boundary = int(clip_index)
            if boundary < 0 or boundary >= n_clips - 1:
                raise IndexError(
                    f"clip_index {boundary} requires a following clip "
                    f"(valid 0..{n_clips - 2})"
                )
        elif self.has_selection():
            boundary = self.selection_boundary_index()
            if boundary is None:
                raise ValueError(
                    "selection does not span a clip boundary; "
                    "paint a range across two adjacent clips"
                )
            if requested is None:
                sel_s = float(self.selection_start_sec)  # type: ignore[arg-type]
                sel_e = float(self.selection_end_sec)  # type: ignore[arg-type]
                requested = sel_e - sel_s
        elif n_clips == 2:
            # Ainoa sauma – sallitaan ilman hiirivalintaa kun kesto annettu
            boundary = 0
        else:
            raise ValueError(
                "no active selection and clip_index not provided; "
                "paint a range, pass clip_index, or use a two-clip timeline"
            )

        if requested is None:
            raise ValueError(
                "duration_sec is required when there is no active selection"
            )

        assert boundary is not None
        left = self.timeline.clips[boundary]
        right = self.timeline.clips[boundary + 1]
        # xfade vaatii duration < molemmat klipit; max 90 % lyhyemmästä
        max_dur = min(left.duration, right.duration) * 0.9
        max_safe = min(left.duration, right.duration) - 1e-3
        duration = min(max(requested, self.SELECTION_MIN_SEC), max_dur, max_safe)
        if duration <= self.SELECTION_MIN_SEC:
            raise ValueError("computed crossfade duration is too short")

        # Näytä sauman ympärille limitys valintana (UI + agenttikuittaus)
        spans = self.clip_spans()
        _i, left_start, left_end = spans[boundary]
        half = duration / 2.0
        sel_start = max(left_start, left_end - half)
        sel_end = min(spans[boundary + 1][2], left_end + half)
        # Jos sauma ei ole limitetty vielä, left_end == next start
        if sel_end - sel_start < duration - 1e-6:
            sel_start = max(0.0, left_end - half)
            sel_end = sel_start + duration
        self.set_selection(sel_start, sel_end)

        transition = self.set_transition(boundary, transition_type, duration)
        self.clear_selection()
        self._flush_persist()
        return transition

    def add_blur_to_selection(
        self,
        *,
        max_sigma: float = 18.0,
        fade_sec: float = 0.6,
    ) -> list[BlurEffect]:
        """Lisää pehmeä blur valitulle alueelle (kaikki osuvat klipit)."""
        if not self.has_selection():
            raise ValueError("no active selection range")
        sel_s = float(self.selection_start_sec)  # type: ignore[arg-type]
        sel_e = float(self.selection_end_sec)  # type: ignore[arg-type]

        planned: list[tuple[int, float, float]] = []
        for i, cs, ce in self.clip_spans():
            o_start = max(sel_s, cs)
            o_end = min(sel_e, ce)
            if o_end - o_start < self.SELECTION_MIN_SEC:
                continue
            planned.append((i, o_start - cs, o_end - cs))
        if not planned:
            raise ValueError("selection does not overlap any clip")

        self._checkpoint()
        created: list[BlurEffect] = []
        for clip_index, local_start, local_end in planned:
            clip = self.timeline.clips[clip_index]
            local_fade = min(float(fade_sec), (local_end - local_start) / 2.0)
            effect = BlurEffect(
                start_sec=local_start,
                end_sec=local_end,
                max_sigma=float(max_sigma),
                fade_sec=max(local_fade, 0.05),
            )
            updated = clip.model_copy(update={"effects": [*clip.effects, effect]})
            self.timeline.clips[clip_index] = updated
            created.append(effect)

        self.clear_selection()
        return created

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
        if mode not in ("preview", "final", "export"):
            raise ValueError("mode must be 'preview', 'final', or 'export'")
        profile = "preview" if mode == "preview" else "export"
        phase = profile
        detail = (
            "Updating preview"
            if profile == "preview"
            else "Rendering final video"
        )
        self._flush_persist()
        self.set_progress(active=True, phase=phase, percent=0, detail=detail)
        suffix = "preview" if profile == "preview" else "final"
        output = self.workspace / f"cutsmith_{suffix}.mp4"
        # Pidä kopio tilasta – renderöintivirhe ei saa tyhjentää aikajanaa
        timeline_snapshot = self.timeline.model_copy(deep=True)

        def _on_progress(percent: int) -> None:
            self.set_progress(
                active=True,
                phase=phase,
                percent=percent,
                detail=detail,
            )

        try:
            path = ffmpeg_render(
                self.timeline,
                output_path=output,
                mode=profile,
                workspace=self.workspace,
                on_progress=_on_progress,
                total_duration=self.timeline.total_duration(),
            )
            self.set_progress(active=True, phase=phase, percent=100, detail=detail)
            return path
        except Exception as exc:  # noqa: BLE001 – säilytä timeline, palauta selkeä virhe
            self.timeline = timeline_snapshot
            detail_msg = str(exc).strip() or exc.__class__.__name__
            raise RuntimeError(
                f"Preview/export failed (timeline unchanged): {detail_msg}"
            ) from exc
        finally:
            self.clear_progress()
