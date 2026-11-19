"""EditorController – atomiset Timeline-operaatiot + historia."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from compiler.ffmpeg import (
    extract_audio_track,
    extract_clip_thumbnail,
    extract_still_frame,
)
from compiler.ffmpeg import render as ffmpeg_render
from compiler.ffprobe import probe, probe_audio
from controller.media_cache import cached_clip_thumbnail, cached_still_frame
from controller.persistence import (
    PROJECT_STATE_FILENAME,
    PROJECT_STATE_VERSION,
    cleanup_workspace,
    load_project_state,
    project_state_path,
    save_project_state,
)
from controller.summary import (
    _CHAT_STUB,
    _SUMMARY_I18N,
    build_state_summary,
    chat_stub_reply,
    format_timecode,
    normalize_lang,
)
from model.history import HistoryManager
from model.timeline import (
    AudioClip,
    AudioTrack,
    BackgroundMusic,
    BlurEffect,
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


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
        self._lock = threading.RLock()
        self._progress_lock = threading.Lock()
        self._batch_depth = 0
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
    # Leikkauksen jälkeisen aliklipin minimikesto (pudottaa 0–0.1 s haamuklipit)
    MIN_SUBCLIP_SEC = 0.1

    # ------------------------------------------------------------------
    # Sisäiset apurit
    # ------------------------------------------------------------------

    def _project_state_path(self) -> Path:
        return project_state_path(self.workspace)

    def _persist(self) -> None:
        """Tallenna projektin tila atomisesti workspace/project_state.json -tiedostoon."""
        if not self._persist_enabled:
            return
        with self._lock:
            saved = save_project_state(
                self.workspace,
                self.timeline,
                playhead_sec=self.playhead_sec,
                selection_start_sec=self.selection_start_sec,
                selection_end_sec=self.selection_end_sec,
            )
            if saved:
                self._state_dirty = False

    def _load_project_state(self) -> bool:
        """Lataa tila levyltä jos lähdevideot ovat yhä olemassa. Palauta True jos ok."""
        loaded = load_project_state(self.workspace)
        if loaded is None:
            return False
        self.timeline = loaded.timeline
        self.playhead_sec = loaded.playhead_sec
        self.selection_start_sec = loaded.selection_start_sec
        self.selection_end_sec = loaded.selection_end_sec
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
        """Kirjoita tila levylle jos mutaatioita on pending (ei sisäkkäisen batchin aikana)."""
        with self._lock:
            if self._batch_depth > 0:
                return
            if getattr(self, "_state_dirty", False):
                self._persist()
                self._state_dirty = False

    @contextmanager
    def _persist_batch(self) -> Iterator[None]:
        """Suojaa mutaation RLockilla ja tallentaa tilan kerran uloimman lohkon lopussa."""
        with self._lock:
            self._batch_depth += 1
            try:
                yield
            finally:
                self._batch_depth -= 1
                if self._batch_depth == 0 and self._state_dirty:
                    self._persist()

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
        with self._progress_lock:
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
        with self._progress_lock:
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
        with self._persist_batch():
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
        with self._persist_batch():
            if source_id not in self.timeline.sources:
                raise KeyError(f"unknown source_id {source_id!r}")
            source = self.timeline.sources[source_id]
            if start_sec < 0 or end_sec > source.duration or end_sec <= start_sec:
                raise ValueError(
                    f"invalid clip range [{start_sec}, {end_sec}] "
                    f"for source duration {source.duration}"
                )
            if insert_at is not None and (
                insert_at < 0 or insert_at > len(self.timeline.clips)
            ):
                raise IndexError(f"insert_at {insert_at} out of range")

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
                self.timeline.clips.insert(insert_at, clip)
                self._adjust_transitions_after_insert(insert_at)
            return clip

    def trim_clip(self, clip_index: int, start_sec: float, end_sec: float) -> Clip:
        with self._persist_batch():
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
            # Poista viereiset siirtymät jotka eivät enää mahdu trimmattuun klippiin
            self.timeline.transitions = [
                t
                for t in self.timeline.transitions
                if not (
                    t.after_clip_index in (clip_index, clip_index - 1)
                    and t.duration >= new_dur
                )
            ]
            return updated

    def split_clip(self, clip_index: int, split_at_sec: float) -> tuple[Clip, Clip]:
        """Jaa klippi suhteellisessa ajassa (klipin alusta, ei lähdeajassa)."""
        with self._persist_batch():
            clip = self._clip(clip_index)
            if split_at_sec <= 0 or split_at_sec >= clip.duration:
                raise ValueError(
                    f"split_at_sec {split_at_sec} must be within (0, {clip.duration})"
                )
            source_split = clip.in_sec + split_at_sec * clip.speed

            left_effects = self._effects_for_subclip(clip, 0.0, split_at_sec)
            right_effects = self._effects_for_subclip(
                clip, split_at_sec, clip.duration
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
        with self._persist_batch():
            self._clip(clip_index)
            self._checkpoint()
            self.timeline.clips.pop(clip_index)
            self._adjust_transitions_after_remove(clip_index)

    def move_clip(self, from_index: int, to_index: int) -> None:
        """Siirrä klippi uuteen indeksiin (0-pohjainen); säilytä koskemattomat saumat."""
        with self._persist_batch():
            n = len(self.timeline.clips)
            if from_index < 0 or from_index >= n:
                raise IndexError(f"from_index {from_index} out of range")
            if to_index < 0 or to_index >= n:
                raise IndexError(f"to_index {to_index} out of range")
            if from_index == to_index:
                return

            self._checkpoint()
            # Tallenna vanhat siirtymät klippiparien (left_id, right_id) mukaan
            pair_transitions: dict[tuple[str, str], Transition] = {}
            for t in self.timeline.transitions:
                if 0 <= t.after_clip_index < n - 1:
                    pair = (
                        self.timeline.clips[t.after_clip_index].id,
                        self.timeline.clips[t.after_clip_index + 1].id,
                    )
                    pair_transitions[pair] = t

            clip = self.timeline.clips.pop(from_index)
            self.timeline.clips.insert(to_index, clip)

            # Säilytä siirtymät niissä saumoissa joissa sama klippipari on yhä vierekkäin
            kept: list[Transition] = []
            for j in range(len(self.timeline.clips) - 1):
                pair = (self.timeline.clips[j].id, self.timeline.clips[j + 1].id)
                if pair in pair_transitions:
                    kept.append(
                        pair_transitions[pair].model_copy(
                            update={"after_clip_index": j}
                        )
                    )
            self.timeline.transitions = kept

    def set_transition(
        self,
        after_clip_index: int,
        transition_type: str = "fadeblack",
        duration: float = 0.4,
    ) -> Transition:
        with self._persist_batch():
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
            return transition

    def remove_transition(self, after_clip_index: int) -> bool:
        """Poista siirtymä annetun klipin jälkeen (palauttaa kovaleikkauksen)."""
        with self._persist_batch():
            if after_clip_index < 0 or after_clip_index >= max(
                len(self.timeline.clips) - 1, 0
            ):
                raise IndexError(
                    f"after_clip_index {after_clip_index} requires a following clip"
                )
            existing = [
                t
                for t in self.timeline.transitions
                if t.after_clip_index == after_clip_index
            ]
            if not existing:
                return False
            self._checkpoint()
            self.timeline.transitions = [
                t
                for t in self.timeline.transitions
                if t.after_clip_index != after_clip_index
            ]
            return True

    def add_zoom(
        self,
        clip_index: int,
        start_sec: float,
        duration: float,
        region: str = "center",
        target_x: float | None = None,
        target_y: float | None = None,
        zoom_factor: float = 1.8,
        animated: bool = False,
    ) -> ZoomEffect:
        with self._persist_batch():
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
                animated=bool(animated),
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
        with self._persist_batch():
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

    def add_blur(
        self,
        clip_index: int,
        start_sec: float,
        end_sec: float | None = None,
        *,
        strength: float = 12.0,
        mode: str = "static",
    ) -> BlurEffect:
        """Lisää staattinen tai asteittainen blur-efekti klipille (Undo/Redo-tuettu)."""
        with self._persist_batch():
            clip = self._clip(clip_index)
            allowed_modes = {"static", "fade_in", "fade_out"}
            if mode not in allowed_modes:
                raise ValueError(
                    f"invalid blur mode {mode!r}; expected one of {sorted(allowed_modes)}"
                )
            start = float(start_sec)
            if start < 0 or start >= clip.duration - 1e-9:
                raise ValueError(
                    f"blur start_sec {start} must be within [0, {clip.duration})"
                )
            end = float(end_sec) if end_sec is not None else None
            if end is not None and end > clip.duration + 1e-9:
                raise ValueError("blur effect exceeds clip duration")
            sigma = float(strength)
            if sigma < 1.0 or sigma > 30.0:
                raise ValueError("blur strength must be between 1.0 and 30.0")

            effect = BlurEffect(
                start_sec=start,
                end_sec=end,
                strength=sigma,
                max_sigma=sigma,
                mode=mode,  # type: ignore[arg-type]
            )
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
        with self._persist_batch():
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
        with self._persist_batch():
            self._checkpoint()
            self.timeline.bgm = music
            # Pidä myös ääniraitapoolissa (Ääniraita N)
            if not any(t.path == path for t in self.timeline.audio_tracks):
                try:
                    meta = probe_audio(path)
                    dur = float(meta["duration"])
                except Exception:  # noqa: BLE001
                    dur = 1.0
                self.timeline.audio_tracks.append(
                    AudioTrack(
                        id=_new_id("aud"),
                        path=path,
                        duration=max(dur, 0.05),
                        origin="imported",
                    )
                )
            return music

    def remove_video_audio(self, keep_in_pool: bool = True) -> dict[str, Any]:
        """Mykistä videoklippien oma ääni; tallenna halutessa pooliin Ääniraita N."""
        with self._persist_batch():
            if not self.timeline.clips:
                raise ValueError("timeline has no clips")

            self._checkpoint()
            extracted: AudioTrack | None = None

            if keep_in_pool:
                # Irrota ääni ensimmäisestä lähteestä jolla on audio (jos ei jo poolissa)
                for clip in self.timeline.clips:
                    source = self.timeline.sources.get(clip.source_id)
                    if source is None or not source.has_audio:
                        continue
                    already = any(
                        t.origin == "extracted" and t.source_video_id == source.id
                        for t in self.timeline.audio_tracks
                    )
                    if already:
                        break
                    audio_dir = Path(self.workspace) / "audio"
                    audio_dir.mkdir(parents=True, exist_ok=True)
                    out = audio_dir / f"track_{_new_id('aud')}.m4a"
                    try:
                        extract_audio_track(Path(source.path), out)
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError(
                            f"failed to extract audio from video: {exc}"
                        ) from exc
                    meta = probe_audio(str(out))
                    extracted = AudioTrack(
                        id=_new_id("aud"),
                        path=str(out.resolve()),
                        duration=float(meta["duration"]),
                        origin="extracted",
                        source_video_id=source.id,
                    )
                    self.timeline.audio_tracks.append(extracted)
                    break

            self.timeline.mute_video_audio = True
            return {
                "mute_video_audio": True,
                "extracted_track_id": extracted.id if extracted else None,
                "audio_track_count": len(self.timeline.audio_tracks),
            }

    def attach_audio_track(
        self,
        track_index: int = 1,
        start_sec: float = 0.0,
    ) -> AudioTrack:
        """Liitä 1-pohjainen Ääniraita N aikajanalle katkeamattomaksi ääneksi."""
        with self._persist_batch():
            if track_index < 1:
                raise ValueError(
                    f"track_index must be 1-based (first audio track is 1), got {track_index}"
                )
            if track_index > len(self.timeline.audio_tracks):
                raise IndexError(
                    f"audio track {track_index} not found "
                    f"(have {len(self.timeline.audio_tracks)})"
                )
            track = self.timeline.audio_tracks[track_index - 1]
            if not Path(track.path).is_file():
                raise FileNotFoundError(f"audio track file missing: {track.path}")
            if start_sec < 0:
                raise ValueError("start_sec must be >= 0")

            self._checkpoint()
            self.timeline.active_audio_track_id = track.id
            self.timeline.active_audio_start_sec = float(start_sec)
            # Pool-raidan käyttö tarkoittaa että klippiääntä ei käytetä
            self.timeline.mute_video_audio = True
            return track

    def add_audio_file(self, file_path: str) -> AudioTrack:
        """Lisää tuotu äänitiedosto pooliin (Ääniraita N)."""
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"audio file not found: {path}")
        meta = probe_audio(str(path))
        track = AudioTrack(
            id=_new_id("aud"),
            path=str(path),
            duration=float(meta["duration"]),
            origin="imported",
        )
        if track.duration <= 0:
            raise ValueError("audio duration must be > 0")
        with self._persist_batch():
            self._checkpoint()
            self.timeline.audio_tracks.append(track)
        return track

    def set_timeline_fades(
        self,
        fade_in_sec: float | None = None,
        fade_out_sec: float | None = None,
        fade_audio: bool = True,
    ) -> dict[str, Any]:
        """Aseta koko aikajanan alku-/loppuhäivytys (vain ei-None-arvot päivittyvät)."""
        with self._persist_batch():
            if fade_in_sec is not None and fade_in_sec < 0:
                raise ValueError("fade_in_sec must be >= 0")
            if fade_out_sec is not None and fade_out_sec < 0:
                raise ValueError("fade_out_sec must be >= 0")
            if fade_in_sec is None and fade_out_sec is None:
                raise ValueError("provide fade_in_sec and/or fade_out_sec")

            self._checkpoint()
            if fade_in_sec is not None:
                self.timeline.fade_in_sec = float(fade_in_sec)
            if fade_out_sec is not None:
                self.timeline.fade_out_sec = float(fade_out_sec)
            self.timeline.fade_audio = bool(fade_audio)
            return {
                "fade_in_sec": self.timeline.fade_in_sec,
                "fade_out_sec": self.timeline.fade_out_sec,
                "fade_audio": self.timeline.fade_audio,
            }

    def _ensure_extracted_audio_for_source(self, source: VideoSource) -> AudioTrack | None:
        """Irrota lähdevideon ääni pooliin (tai palauta olemassa oleva)."""
        if not source.has_audio:
            return None
        for track in self.timeline.audio_tracks:
            if track.origin == "extracted" and track.source_video_id == source.id:
                if Path(track.path).is_file():
                    return track
        audio_dir = Path(self.workspace) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        out = audio_dir / f"track_{_new_id('aud')}.m4a"
        extract_audio_track(Path(source.path), out)
        meta = probe_audio(str(out))
        track = AudioTrack(
            id=_new_id("aud"),
            path=str(out.resolve()),
            duration=float(meta["duration"]),
            origin="extracted",
            source_video_id=source.id,
        )
        self.timeline.audio_tracks.append(track)
        return track

    def detach_audio_to_timeline(self) -> dict[str, Any]:
        """Mykistä videoääni ja luo AudioClip-palat nykyisten videoklippien ajoituksilla."""
        with self._persist_batch():
            if not self.timeline.clips:
                raise ValueError("timeline has no clips")

            self._checkpoint()
            source_audio: dict[str, str] = {}
            for clip in self.timeline.clips:
                source = self.timeline.sources.get(clip.source_id)
                if source is None:
                    continue
                track = self._ensure_extracted_audio_for_source(source)
                if track is not None:
                    source_audio[source.id] = track.path

            spans = self.clip_spans()
            audio_clips: list[AudioClip] = []
            for i, t_start, _t_end in spans:
                clip = self.timeline.clips[i]
                path = source_audio.get(clip.source_id)
                if not path:
                    continue
                audio_clips.append(
                    AudioClip(
                        id=_new_id("aclip"),
                        source_path=path,
                        in_point=float(clip.in_sec),
                        out_point=float(clip.out_sec),
                        timeline_start=float(t_start),
                    )
                )

            self.timeline.audio_clips = audio_clips
            self.timeline.mute_video_audio = True
            # Erillinen ääniaikajana korvaa continuous bed -tilan
            self.timeline.active_audio_track_id = None
            return {
                "mute_video_audio": True,
                "audio_clip_count": len(audio_clips),
                "audio_track_count": len(self.timeline.audio_tracks),
            }

    def _audio_clip(self, zero_index: int) -> AudioClip:
        if zero_index < 0 or zero_index >= len(self.timeline.audio_clips):
            raise IndexError(
                f"audio clip {zero_index + 1} not found "
                f"(have {len(self.timeline.audio_clips)})"
            )
        return self.timeline.audio_clips[zero_index]

    def split_audio_clip(self, timestamp_sec: float) -> tuple[AudioClip, AudioClip]:
        """Jaa aikajanalla soiva ääniklippi kahdeksi."""
        with self._persist_batch():
            t = float(timestamp_sec)
            if t < 0:
                raise ValueError("timestamp_sec must be >= 0")
            hit: int | None = None
            for i, ac in enumerate(self.timeline.audio_clips):
                start = ac.timeline_start
                end = start + ac.duration
                if start + 1e-9 < t < end - 1e-9:
                    hit = i
                    break
            if hit is None:
                raise ValueError(f"no audio clip playing at {t:.3f}s")

            self._checkpoint()
            ac = self.timeline.audio_clips[hit]
            local = t - ac.timeline_start
            left = ac.model_copy(
                update={
                    "id": _new_id("aclip"),
                    "out_point": ac.in_point + local,
                }
            )
            right = ac.model_copy(
                update={
                    "id": _new_id("aclip"),
                    "in_point": ac.in_point + local,
                    "timeline_start": t,
                }
            )
            self.timeline.audio_clips[hit : hit + 1] = [left, right]
            return left, right

    def cut_audio_range(
        self,
        start_sec: float,
        end_sec: float,
        *,
        ripple: bool = False,
    ) -> dict[str, Any]:
        """Leikkaa ääniraidalta [start, end) siirtämättä videota (ellei ripple)."""
        with self._persist_batch():
            start = float(start_sec)
            end = float(end_sec)
            if end <= start + 1e-9:
                raise ValueError("end_sec must be greater than start_sec")
            if not self.timeline.audio_clips:
                raise ValueError("no audio clips on timeline")

            gap = end - start
            self._checkpoint()
            new_clips: list[AudioClip] = []
            for ac in self.timeline.audio_clips:
                new_clips.extend(self._cut_one_audio_clip(ac, start, end, ripple, gap))
            self.timeline.audio_clips = new_clips
            return {
                "start_sec": start,
                "end_sec": end,
                "ripple": ripple,
                "audio_clip_count": len(new_clips),
            }

    def _cut_one_audio_clip(
        self,
        ac: AudioClip,
        start: float,
        end: float,
        ripple: bool,
        gap: float,
    ) -> list[AudioClip]:
        a = ac.timeline_start
        b = a + ac.duration
        if b <= start + 1e-9 or a >= end - 1e-9:
            if ripple and a >= end - 1e-9:
                return [
                    ac.model_copy(
                        update={"timeline_start": max(0.0, a - gap)}
                    )
                ]
            return [ac]
        if a >= start - 1e-9 and b <= end + 1e-9:
            return []
        if a < start and b > end:
            left_len = start - a
            right_skip = end - a
            out: list[AudioClip] = []
            if left_len > self.MIN_SUBCLIP_SEC + 1e-9:
                out.append(
                    ac.model_copy(
                        update={
                            "id": _new_id("aclip"),
                            "out_point": ac.in_point + left_len,
                        }
                    )
                )
            rem = ac.duration - right_skip
            if rem > self.MIN_SUBCLIP_SEC + 1e-9:
                out.append(
                    ac.model_copy(
                        update={
                            "id": _new_id("aclip"),
                            "in_point": ac.in_point + right_skip,
                            "timeline_start": start if ripple else end,
                        }
                    )
                )
            return out
        if a < start < b:
            keep = start - a
            if keep <= self.MIN_SUBCLIP_SEC + 1e-9:
                return []
            return [
                ac.model_copy(update={"out_point": ac.in_point + keep})
            ]
        # Alkaa leikkauksen sisällä, jatkuu past end
        skip = max(0.0, end - a)
        rem = ac.duration - skip
        if rem <= self.MIN_SUBCLIP_SEC + 1e-9:
            return []
        return [
            ac.model_copy(
                update={
                    "in_point": ac.in_point + skip,
                    "timeline_start": start if ripple else end,
                }
            )
        ]

    def move_audio_clip(self, clip_index: int, new_start_sec: float) -> AudioClip:
        """Siirrä 1-pohjainen ääniklippi uuteen timeline_start-kohtaan."""
        with self._persist_batch():
            if clip_index < 1:
                raise ValueError(
                    f"clip_index must be 1-based (first audio clip is 1), got {clip_index}"
                )
            if new_start_sec < 0:
                raise ValueError("new_start_sec must be >= 0")
            zero = clip_index - 1
            self._audio_clip(zero)  # validate
            self._checkpoint()
            ac = self.timeline.audio_clips[zero]
            updated = ac.model_copy(update={"timeline_start": float(new_start_sec)})
            self.timeline.audio_clips[zero] = updated
            return updated

    def remove_audio_clip(self, clip_index: int) -> AudioClip:
        """Poista 1-pohjainen ääniklippi erilliseltä ääniaikajanalta."""
        with self._persist_batch():
            if clip_index < 1:
                raise ValueError(
                    f"clip_index must be 1-based (first audio clip is 1), got {clip_index}"
                )
            zero = clip_index - 1
            self._audio_clip(zero)  # validate
            self._checkpoint()
            return self.timeline.audio_clips.pop(zero)

    def set_audio_clip_volume(
        self,
        clip_index: int | None = None,
        volume: float = 1.0,
    ) -> dict[str, Any]:
        """Säädä yhden tai kaikkien ääniklipien voimakkuutta."""
        with self._persist_batch():
            if volume < 0:
                raise ValueError("volume must be >= 0")
            if not self.timeline.audio_clips:
                raise ValueError("no audio clips on timeline")
            if clip_index is not None and clip_index < 1:
                raise ValueError(
                    f"clip_index must be 1-based (first audio clip is 1), got {clip_index}"
                )

            self._checkpoint()
            if clip_index is None:
                self.timeline.audio_clips = [
                    c.model_copy(update={"volume": float(volume)})
                    for c in self.timeline.audio_clips
                ]
                updated = len(self.timeline.audio_clips)
            else:
                zero = clip_index - 1
                ac = self._audio_clip(zero)
                self.timeline.audio_clips[zero] = ac.model_copy(
                    update={"volume": float(volume)}
                )
                updated = 1
            return {"volume": float(volume), "updated_count": updated}

    def undo(self) -> bool:
        with self._persist_batch():
            previous = self.history.undo(self.timeline)
            if previous is None:
                return False
            self.timeline = previous
            self._state_dirty = True
            return True

    def redo(self) -> bool:
        with self._persist_batch():
            nxt = self.history.redo(self.timeline)
            if nxt is None:
                return False
            self.timeline = nxt
            self._state_dirty = True
            return True

    def reset_project(self, delete_media: bool = False) -> dict[str, Any]:
        """Nollaa aikajana, historia ja session väliaikaistiedostot."""
        with self._lock:
            removed = cleanup_workspace(
                self.workspace, delete_media=bool(delete_media)
            )
            # Tuore tyhjä tila
            self.timeline = Timeline()
            self.history.clear()
            self.playhead_sec = 0.0
            self.selection_start_sec = None
            self.selection_end_sec = None
            self._state_dirty = False
            self.clear_progress()

            return {
                "ok": True,
                "delete_media": bool(delete_media),
                "removed": removed,
                "clip_count": 0,
            }

    def get_state_summary(self, lang: str = "fi") -> dict[str, Any]:
        """Selkokielinen 1-pohjainen yhteenveto UI:ta ja LLM:ää varten."""
        with self._lock:
            total = round(self.timeline.total_duration(), 3)
            self.playhead_sec = min(self.playhead_sec, total) if total > 0 else 0.0
            self._clamp_selection_to_total(total)
            return build_state_summary(
                self.timeline,
                self.clip_spans(),
                playhead_sec=self.playhead_sec,
                selection_start_sec=self.selection_start_sec,
                selection_end_sec=self.selection_end_sec,
                can_undo=self.history.can_undo(),
                can_redo=self.history.can_redo(),
                lang=lang,
            )

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
        with self._persist_batch():
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

    def get_frame_at_time(self, sec: float, *, scale: str = "960:-1") -> Path:
        """Poimi still-JPEG aikajanan kohdasta (välimuisti workspace/frames/)."""
        with self._lock:
            located = self.locate_timeline_sec(sec)
            if located is None:
                raise ValueError("timeline has no clips")
            clip_index, source_ts = located
            clip = self.timeline.clips[clip_index]
            source = self.timeline.sources[clip.source_id]
            source_path = Path(source.path)
            workspace = self.workspace
        return cached_still_frame(
            workspace,
            source_path,
            source_ts,
            extractor=extract_still_frame,
            scale=scale,
        )

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
            if isinstance(effect, BlurEffect) and effect.end_sec is None:
                e_end = clip.duration
            else:
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

    def cut_out_range(
        self,
        start_sec: float,
        duration_sec: float,
        *,
        include_audio: bool = False,
    ) -> None:
        """Poista aikaväli [start_sec, start_sec+duration] aikajanalta.

        Kun ``include_audio=True`` ja erillisiä ääniklippejä (``audio_clips``)
        on olemassa, sama aikaväli leikataan ripple-tilassa myös ääniraidalta
        yhdellä Undo-askeleella.
        """
        with self._persist_batch():
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
            old_transitions = list(self.timeline.transitions)
            self._checkpoint()
            new_clips: list[Clip] = []
            # Seuraa kunkin vanhan klipin uusia indeksejä ja sitä, jäivätkö alku/loppu ehjiksi
            first_new_idx: dict[int, int] = {}
            last_new_idx: dict[int, int] = {}
            start_intact: dict[int, bool] = {}
            end_intact: dict[int, bool] = {}

            for i, cs, ce in spans:
                clip = self.timeline.clips[i]
                if ce <= start + 1e-9 or cs >= end - 1e-9:
                    idx = len(new_clips)
                    new_clips.append(clip)
                    first_new_idx[i] = idx
                    last_new_idx[i] = idx
                    start_intact[i] = True
                    end_intact[i] = True
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
                    if left.duration > self.MIN_SUBCLIP_SEC + 1e-9:
                        idx = len(new_clips)
                        new_clips.append(left)
                        first_new_idx[i] = idx
                        start_intact[i] = True
                    if right.duration > self.MIN_SUBCLIP_SEC + 1e-9:
                        idx = len(new_clips)
                        new_clips.append(right)
                        if i not in first_new_idx:
                            first_new_idx[i] = idx
                            start_intact[i] = False
                        last_new_idx[i] = idx
                        end_intact[i] = True
                    elif i in first_new_idx:
                        last_new_idx[i] = first_new_idx[i]
                        end_intact[i] = False
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
                    if updated.duration > self.MIN_SUBCLIP_SEC + 1e-9:
                        idx = len(new_clips)
                        new_clips.append(updated)
                        first_new_idx[i] = idx
                        last_new_idx[i] = idx
                        start_intact[i] = True
                        end_intact[i] = False
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
                    if updated.duration > self.MIN_SUBCLIP_SEC + 1e-9:
                        idx = len(new_clips)
                        new_clips.append(updated)
                        first_new_idx[i] = idx
                        last_new_idx[i] = idx
                        start_intact[i] = False
                        end_intact[i] = True

            # Säilytä siirtymät koskemattomissa saumoissa
            kept_transitions: list[Transition] = []
            for t in old_transitions:
                left_old = t.after_clip_index
                right_old = left_old + 1
                if (
                    left_old in last_new_idx
                    and right_old in first_new_idx
                    and end_intact.get(left_old, False)
                    and start_intact.get(right_old, False)
                ):
                    new_left = last_new_idx[left_old]
                    new_right = first_new_idx[right_old]
                    if (
                        new_left + 1 == new_right
                        and t.duration < new_clips[new_left].duration
                        and t.duration < new_clips[new_right].duration
                    ):
                        kept_transitions.append(
                            t.model_copy(update={"after_clip_index": new_left})
                        )
            kept_transitions.sort(key=lambda t: t.after_clip_index)

            self.timeline.clips = new_clips
            self.timeline.transitions = kept_transitions
            if include_audio and self.timeline.audio_clips:
                gap = end - start
                new_audio: list[AudioClip] = []
                for ac in self.timeline.audio_clips:
                    new_audio.extend(
                        self._cut_one_audio_clip(ac, start, end, True, gap)
                    )
                self.timeline.audio_clips = new_audio
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
        with self._persist_batch():
            if start_sec is None or end_sec is None:
                self.selection_start_sec = None
                self.selection_end_sec = None
                self._state_dirty = True
                return None, None

            total = self.timeline.total_duration()
            if total <= 0:
                self.selection_start_sec = None
                self.selection_end_sec = None
                self._state_dirty = True
                return None, None

            start = min(max(0.0, float(start_sec)), total)
            end = min(max(0.0, float(end_sec)), total)
            if start > end:
                start, end = end, start
            if end - start < self.SELECTION_MIN_SEC:
                self.selection_start_sec = None
                self.selection_end_sec = None
                self._state_dirty = True
                return None, None

            self.selection_start_sec = start
            self.selection_end_sec = end
            self._state_dirty = True
            return self.selection_start_sec, self.selection_end_sec

    def clear_selection(self) -> None:
        """Tyhjennä aluevalinta."""
        with self._persist_batch():
            self.selection_start_sec = None
            self.selection_end_sec = None
            self._state_dirty = True

    def has_selection(self) -> bool:
        return (
            self.selection_start_sec is not None
            and self.selection_end_sec is not None
            and self.selection_end_sec - self.selection_start_sec
            >= self.SELECTION_MIN_SEC
        )

    def cut_selected_range(self, *, include_audio: bool = False) -> None:
        """Leikkaa aktiivinen valintaväli pois aikajanalta."""
        with self._persist_batch():
            if not self.has_selection():
                raise ValueError("no active selection range")
            start = float(self.selection_start_sec)  # type: ignore[arg-type]
            end = float(self.selection_end_sec)  # type: ignore[arg-type]
            duration = end - start
            self.cut_out_range(start, duration, include_audio=include_audio)
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
        with self._persist_batch():
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
            return transition

    def add_blur_to_selection(
        self,
        *,
        max_sigma: float = 18.0,
        fade_sec: float = 0.6,
    ) -> list[BlurEffect]:
        """Lisää pehmeä blur valitulle alueelle (kaikki osuvat klipit)."""
        with self._persist_batch():
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
        with self._lock:
            clip = self._clip(index - 1)
            source = self.timeline.sources.get(clip.source_id)
            if source is None:
                raise KeyError(f"unknown source_id {clip.source_id!r}")
            source_path = Path(source.path)
            trim_start = float(clip.in_sec)
            workspace = self.workspace
        return cached_clip_thumbnail(
            workspace,
            source_path,
            trim_start,
            extractor=extract_clip_thumbnail,
        )

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
        # Ota syväkopio tilasta lukon alla – renderöinti ei koske self.timelineen
        # eikä estä edistymistilan pollausta
        with self._lock:
            self._flush_persist()
            timeline_snapshot = self.timeline.model_copy(deep=True)
            total_dur = timeline_snapshot.total_duration()
            workspace = self.workspace
        self.set_progress(active=True, phase=phase, percent=0, detail=detail)
        suffix = "preview" if profile == "preview" else "final"
        output = workspace / f"cutsmith_{suffix}.mp4"

        def _on_progress(percent: int) -> None:
            self.set_progress(
                active=True,
                phase=phase,
                percent=percent,
                detail=detail,
            )

        try:
            path = ffmpeg_render(
                timeline_snapshot,
                output_path=output,
                mode=profile,
                workspace=workspace,
                on_progress=_on_progress,
                total_duration=total_dur,
            )
            self.set_progress(active=True, phase=phase, percent=100, detail=detail)
            return path
        except Exception as exc:  # noqa: BLE001 – säilytä timeline, palauta selkeä virhe
            detail_msg = str(exc).strip() or exc.__class__.__name__
            raise RuntimeError(
                f"Preview/export failed (timeline unchanged): {detail_msg}"
            ) from exc
        finally:
            self.clear_progress()
