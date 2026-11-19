"""Kaksikielinen (FI/EN) tilayhteenveto UI:ta ja LLM-agenttia varten."""

from __future__ import annotations

from typing import Any

from model.timeline import BlurEffect, TextEffect, Timeline, ZoomEffect

_SUMMARY_I18N: dict[str, dict[str, Any]] = {
    "fi": {
        "clip_title": "Klippi {n}",
        "effect_zoom": "Zoom",
        "effect_text": "Teksti",
        "effect_blur": "Blur",
        "bgm_on": "Taustamusiikki käytössä",
        "audio_track": "Ääniraita {n}",
        "video_audio_muted": "Videon oma ääni mykistetty",
        "video_audio_on": "Videon oma ääni käytössä",
        "fade_in_out": "Alku­häivytys {fade_in} s, loppuhäivytys {fade_out} s",
        "fade_in_only": "Alku­häivytys {fade_in} s",
        "fade_out_only": "Loppuhäivytys {fade_out} s",
        "audio_clip": "A{n}",
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
        "audio_track": "Audio track {n}",
        "video_audio_muted": "Video audio muted",
        "video_audio_on": "Video audio on",
        "fade_in_out": "Fade in {fade_in} s, fade out {fade_out} s",
        "fade_in_only": "Fade in {fade_in} s",
        "fade_out_only": "Fade out {fade_out} s",
        "audio_clip": "A{n}",
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


def format_timecode(sec: float) -> str:
    """Muotoile sekunnit MM:SS.d -aikakoodiksi."""
    sec = max(0.0, float(sec))
    minutes = int(sec // 60)
    seconds = sec - minutes * 60
    return f"{minutes:02d}:{seconds:04.1f}"


def build_state_summary(
    timeline: Timeline,
    spans: list[tuple[int, float, float]],
    *,
    playhead_sec: float,
    selection_start_sec: float | None,
    selection_end_sec: float | None,
    can_undo: bool,
    can_redo: bool,
    lang: str = "fi",
) -> dict[str, Any]:
    """Muodosta selkokielinen 1-pohjainen tilayhteenveto UI:lle ja LLM:lle."""
    lang = normalize_lang(lang)
    L = _SUMMARY_I18N[lang]
    span_by_index = {i: (start, end) for i, start, end in spans}

    clips_summary = []
    for i, clip in enumerate(timeline.clips):
        source = timeline.sources.get(clip.source_id)
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
                        "animated": effect.animated,
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

    transitions_summary = [
        {
            "after_clip": t.after_clip_index + 1,
            "type": t.type,
            "label": L["transitions"].get(t.type, t.type),
            "duration_sec": t.duration,
        }
        for t in timeline.transitions
    ]

    sources_summary = [
        {
            "id": s.id,
            "path": s.path,
            "duration_sec": round(s.duration, 3),
            "resolution": f"{s.width}x{s.height}",
            "fps": s.fps,
            "has_audio": s.has_audio,
        }
        for s in timeline.sources.values()
    ]

    bgm = None
    if timeline.bgm is not None:
        bgm = {
            "path": timeline.bgm.path,
            "bgm_volume": timeline.bgm.bgm_volume,
            "sfx_volume": timeline.bgm.sfx_volume,
            "fade_out_sec": timeline.bgm.fade_out_sec,
            "label": L["bgm_on"],
        }

    active_id = timeline.active_audio_track_id
    audio_tracks_summary = [
        {
            "index": i + 1,
            "id": track.id,
            "path": track.path,
            "duration_sec": round(track.duration, 3),
            "origin": track.origin,
            "label": L["audio_track"].format(n=i + 1),
            "active": track.id == active_id,
        }
        for i, track in enumerate(timeline.audio_tracks)
    ]

    audio_clips_summary = [
        {
            "index": i + 1,
            "id": ac.id,
            "label": L["audio_clip"].format(n=i + 1),
            "source_path": ac.source_path,
            "in_point": round(ac.in_point, 3),
            "out_point": round(ac.out_point, 3),
            "timeline_start_sec": round(ac.timeline_start, 3),
            "timeline_end_sec": round(ac.timeline_start + ac.duration, 3),
            "duration_sec": round(ac.duration, 3),
            "volume": ac.volume,
            "fade_in_sec": ac.fade_in_sec,
            "fade_out_sec": ac.fade_out_sec,
        }
        for i, ac in enumerate(timeline.audio_clips)
    ]

    total = round(timeline.total_duration(), 3)
    n_clips = len(timeline.clips)
    if n_clips == 0:
        description = L["empty"]
    else:
        description = L["description"].format(
            count=n_clips,
            duration=total,
            transitions=len(timeline.transitions),
        )

    sel_duration = None
    if selection_start_sec is not None and selection_end_sec is not None:
        sel_duration = round(selection_end_sec - selection_start_sec, 3)

    if timeline.fade_in_sec > 0 and timeline.fade_out_sec > 0:
        fade_label = L["fade_in_out"].format(
            fade_in=round(timeline.fade_in_sec, 2),
            fade_out=round(timeline.fade_out_sec, 2),
        )
    elif timeline.fade_in_sec > 0:
        fade_label = L["fade_in_only"].format(
            fade_in=round(timeline.fade_in_sec, 2)
        )
    elif timeline.fade_out_sec > 0:
        fade_label = L["fade_out_only"].format(
            fade_out=round(timeline.fade_out_sec, 2)
        )
    else:
        fade_label = None

    return {
        "lang": lang,
        "description": description,
        "clip_count": n_clips,
        "clips": clips_summary,
        "transitions": transitions_summary,
        "sources": sources_summary,
        "bgm": bgm,
        "mute_video_audio": timeline.mute_video_audio,
        "video_audio_status": (
            L["video_audio_muted"]
            if timeline.mute_video_audio
            else L["video_audio_on"]
        ),
        "audio_tracks": audio_tracks_summary,
        "active_audio_track_index": next(
            (t["index"] for t in audio_tracks_summary if t["active"]),
            None,
        ),
        "active_audio_start_sec": timeline.active_audio_start_sec,
        "audio_clips": audio_clips_summary,
        "audio_clip_count": len(audio_clips_summary),
        "fade_in_sec": round(timeline.fade_in_sec, 3),
        "fade_out_sec": round(timeline.fade_out_sec, 3),
        "fade_audio": timeline.fade_audio,
        "timeline_fade_label": fade_label,
        "total_duration_sec": total,
        "output_fps": timeline.output_fps,
        "playhead_sec": round(playhead_sec, 3),
        "playhead_timecode": format_timecode(playhead_sec),
        "selection_start_sec": (
            round(selection_start_sec, 3) if selection_start_sec is not None else None
        ),
        "selection_end_sec": (
            round(selection_end_sec, 3) if selection_end_sec is not None else None
        ),
        "selection_duration_sec": sel_duration,
        "selection_timecode": (
            f"{format_timecode(selection_start_sec)} – {format_timecode(selection_end_sec)}"
            if selection_start_sec is not None and selection_end_sec is not None
            else None
        ),
        "can_undo": can_undo,
        "can_redo": can_redo,
    }
