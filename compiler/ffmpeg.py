"""Timeline → deterministinen FFmpeg filter_complex -kääntäjä."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from model.timeline import (
    AudioClip,
    BlurEffect,
    Clip,
    TextEffect,
    Timeline,
    VideoSource,
    ZoomEffect,
)

RenderMode = Literal["preview", "final", "export"]
RenderProfile = Literal["preview", "export"]

_NVENC_CHECKED: bool | None = None

PREVIEW_HEIGHT = 480


def normalize_render_mode(mode: str) -> RenderProfile:
    """Normalisoi API-tila → sisäinen profiili (preview | export)."""
    if mode in ("final", "export"):
        return "export"
    return "preview"


def _even_dim(value: float | int) -> int:
    """Pyöristä ylöspäin parilliseksi (vastaa FFmpegin scale=-2 -logiikkaa)."""
    n = max(2, int(round(float(value))))
    return n if n % 2 == 0 else n + 1


def format_ffmpeg_error(stderr: str, *, limit: int = 800) -> str:
    """Poimi stderristä varsinainen virhe (loppu / Error-rivit), ei Input-metaa."""
    text = (stderr or "").strip()
    if not text:
        return "(no stderr)"

    lines = text.splitlines()
    keys = (
        "Error",
        "Invalid",
        "Unable",
        "failed",
        "Failed",
        "Conversion failed",
        "No such",
        "not found",
        "cannot",
        "Cannot",
        "Failed to configure",
        "Error reinitializing",
        "Nothing was written",
    )
    picked: list[str] = []
    for i, ln in enumerate(lines):
        if any(key in ln for key in keys):
            # Sisällytä 1 rivi kontekstia ennen virhettä (Parsed_* -rivit)
            start = max(0, i - 1)
            for ctx in lines[start : i + 1]:
                if ctx not in picked:
                    picked.append(ctx)

    if picked:
        chunk = "\n".join(picked[-30:])
    else:
        # Ei avainsanoja → viimeiset rivit (ei tulosteen alkua / Input #0)
        chunk = "\n".join(lines[-25:])

    if len(chunk) > limit:
        return chunk[-limit:]
    return chunk


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return env


def nvenc_available() -> bool:
    """Tarkista onko h264_nvenc käytettävissä (cachetetaan prosessin ajaksi)."""
    global _NVENC_CHECKED
    if _NVENC_CHECKED is not None:
        return _NVENC_CHECKED

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
        available = "h264_nvenc" in (result.stdout or "")
        if available:
            probe = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=64x64:d=0.05",
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p1",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                env=_env(),
                check=False,
            )
            available = probe.returncode == 0
        _NVENC_CHECKED = available
    except FileNotFoundError:
        _NVENC_CHECKED = False
    return _NVENC_CHECKED


def reset_nvenc_cache() -> None:
    """Testejä varten: nollaa NVENC-cache."""
    global _NVENC_CHECKED
    _NVENC_CHECKED = None


def validate_timeline(timeline: Timeline) -> None:
    """Validoi aikaleimat ja johdonmukaisuus ennen kääntämistä."""
    timeline.validate_consistency()
    if not timeline.clips:
        raise ValueError("timeline has no clips")


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "%%")
    )


# Fonttityylit → ehdokaspolut / fontconfig-nimet (Linux).
_FONT_STYLE_PATHS: dict[str, list[str]] = {
    "clean": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ],
    "cinema": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/gentium/Gentium-R.ttf",
        "/usr/share/fonts/truetype/crosextra/Caladea-Regular.ttf",
    ],
    "mono": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    ],
    "impact": [
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ],
}

_FONT_STYLE_FC: dict[str, list[str]] = {
    "clean": ["DejaVu Sans", "Liberation Sans", "Noto Sans", "sans-serif"],
    "cinema": ["DejaVu Serif", "Liberation Serif", "Gentium", "serif"],
    "mono": ["DejaVu Sans Mono", "Liberation Mono", "FreeMono", "monospace"],
    "impact": ["Impact", "DejaVu Sans:style=Bold", "Liberation Sans:style=Bold", "sans-serif:style=Bold"],
}

_FONT_CACHE: dict[str, str | None] = {}


def reset_font_cache() -> None:
    """Testejä varten."""
    _FONT_CACHE.clear()


def _fc_match(query: str) -> str | None:
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}\\n", query],
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    path = (result.stdout or "").strip().splitlines()
    if not path:
        return None
    candidate = path[0].strip()
    if candidate and Path(candidate).is_file():
        return candidate
    return None


def resolve_font_file(font_style: str = "clean") -> str | None:
    """Palauta olemassa oleva .ttf/.otf-polku fonttityylille, tai None."""
    style = font_style if font_style in _FONT_STYLE_PATHS else "clean"
    if style in _FONT_CACHE:
        return _FONT_CACHE[style]

    for path in _FONT_STYLE_PATHS[style]:
        if Path(path).is_file():
            _FONT_CACHE[style] = path
            return path

    for query in _FONT_STYLE_FC.get(style, []):
        matched = _fc_match(query)
        if matched:
            _FONT_CACHE[style] = matched
            return matched

    # Turvallinen yleisfallback – älä kaada renderöintiä
    for fallback in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ):
        if Path(fallback).is_file():
            _FONT_CACHE[style] = fallback
            return fallback

    matched = _fc_match("sans-serif")
    _FONT_CACHE[style] = matched
    return matched


def _escape_fontfile(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _text_xy(position: str) -> tuple[str, str]:
    if position == "top":
        return ("(w-text_w)/2", "50")
    if position == "center":
        return ("(w-text_w)/2", "(h-text_h)/2")
    return ("(w-text_w)/2", "h-text_h-50")


def _atempo_chain(speed: float) -> str:
    filters: list[str] = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}".rstrip("0").rstrip("."))
    return ",".join(filters)


def _output_size(timeline: Timeline, mode: RenderMode | str) -> tuple[int, int]:
    profile = normalize_render_mode(mode)
    first = timeline.sources[timeline.clips[0].source_id]
    if profile == "preview":
        height = PREVIEW_HEIGHT
        width = _even_dim(int(round(first.width * height / first.height)))
        return width, height
    return _even_dim(first.width), _even_dim(first.height)


def is_simple_preview_timeline(timeline: Timeline) -> bool:
    """Yksi klippi ilman tehosteita/siirtymiä/BGM → kevyt esikatselupolku."""
    return (
        len(timeline.clips) == 1
        and not timeline.clips[0].effects
        and not timeline.transitions
        and timeline.bgm is None
        and timeline.clips[0].speed == 1.0
    )


def _source_input_map(timeline: Timeline) -> tuple[list[str], dict[str, int]]:
    """Uniikit lähdepolut (vanha API / yksinkertaiset polut)."""
    paths: list[str] = []
    index_by_id: dict[str, int] = {}
    for source_id, source in timeline.sources.items():
        if any(c.source_id == source_id for c in timeline.clips):
            if source.path not in paths:
                paths.append(source.path)
            index_by_id[source_id] = paths.index(source.path)
    return paths, index_by_id


def _clip_input_paths(timeline: Timeline) -> list[str]:
    """Yksi FFmpeg-input per klippi (sama tiedosto saa toistua) – vakaa xfade."""
    return [timeline.sources[c.source_id].path for c in timeline.clips]


def _gblur_step_filters(effect: BlurEffect, *, steps: int = 5) -> list[str]:
    """Portaittainen gblur focus-in / hold / focus-out (enable=between)."""
    start = float(effect.start_sec)
    end = float(effect.end_sec)
    span = end - start
    if span <= 1e-9:
        return []
    fade = min(float(effect.fade_sec), span / 2.0)
    max_sigma = float(effect.max_sigma)
    steps = max(1, int(steps))
    parts: list[str] = []

    if fade > 1e-9:
        for i in range(1, steps + 1):
            sigma = max_sigma * i / steps
            t0 = start + fade * (i - 1) / steps
            t1 = start + fade * i / steps
            parts.append(
                f"gblur=sigma={sigma:.3f}:enable='between(t\\,{t0:.6f}\\,{t1:.6f})'"
            )

    hold_start = start + fade
    hold_end = end - fade
    if hold_end > hold_start + 1e-9:
        parts.append(
            f"gblur=sigma={max_sigma:.3f}:"
            f"enable='between(t\\,{hold_start:.6f}\\,{hold_end:.6f})'"
        )

    if fade > 1e-9:
        for i in range(steps, 0, -1):
            sigma = max_sigma * i / steps
            t0 = end - fade * i / steps
            t1 = end - fade * (i - 1) / steps
            parts.append(
                f"gblur=sigma={sigma:.3f}:enable='between(t\\,{t0:.6f}\\,{t1:.6f})'"
            )

    return parts


def _effect_filters(clip: Clip, width: int, height: int, fps: int) -> list[str]:
    parts: list[str] = []
    for effect in clip.effects:
        if isinstance(effect, ZoomEffect):
            z = effect.zoom_factor
            tx, ty = effect.target_x, effect.target_y
            start = effect.start_sec
            end = effect.start_sec + effect.duration
            z_expr = f"if(between(on/{fps}\\,{start}\\,{end})\\,{z}\\,1)"
            x_expr = f"iw/2-(iw/zoom/2)+({tx}-0.5)*(iw-iw/zoom)"
            y_expr = f"ih/2-(ih/zoom/2)+({ty}-0.5)*(ih-ih/zoom)"
            parts.append(
                f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
                f":d=1:s={width}x{height}:fps={fps}"
            )
        elif isinstance(effect, TextEffect):
            x, y = _text_xy(effect.position)
            text = _escape_drawtext(effect.text)
            enable = (
                f"between(t\\,{effect.start_sec}\\,"
                f"{effect.start_sec + effect.duration})"
            )
            draw = (
                f"drawtext=text='{text}':fontsize={effect.font_size}"
                f":fontcolor={effect.color}:x={x}:y={y}:enable='{enable}'"
            )
            font_path = resolve_font_file(effect.font_style)
            if font_path:
                draw += f":fontfile='{_escape_fontfile(font_path)}'"
            parts.append(draw)
        elif isinstance(effect, BlurEffect):
            parts.extend(_gblur_step_filters(effect))
    return parts


def _clip_video_filter(
    clip: Clip,
    input_index: int,
    out_label: str,
    width: int,
    height: int,
    fps: int,
    *,
    profile: RenderProfile = "preview",
) -> str:
    parts = [
        f"[{input_index}:v]trim=start={clip.in_sec}:end={clip.out_sec}",
        "setpts=PTS-STARTPTS",
    ]
    if clip.speed != 1.0:
        parts.append(f"setpts=PTS/{clip.speed}")
    parts.append(f"fps={fps}")
    # Preview & export: sama pad-logiikka (pad >= skaalattu kuva).
    # Älä käytä erillistä scale=-2:H + trunc + pad=W:H -yhdistelmää:
    # scale=-2:480 voi tuottaa leveämmän kuvan kuin _output_size → pad failaa.
    parts.extend(
        [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        ]
    )
    parts.extend(["setsar=1", "format=yuv420p"])
    parts.extend(_effect_filters(clip, width, height, fps))
    # Yhtenäinen aikakanta + formaatti ennen xfade/concat
    parts.extend([f"fps={fps}", "format=yuv420p", "setpts=PTS-STARTPTS"])
    return ",".join(parts) + f"[{out_label}]"


def _silence_audio_filter(duration: float, out_label: str) -> str:
    """Stereo-hiljaisuus acrossfade/concat -yhteensopivassa muodossa."""
    dur = max(float(duration), 0.05)
    return (
        f"anullsrc=channel_layout=stereo:sample_rate=48000,"
        f"atrim=0:{dur:.6f},asetpts=PTS-STARTPTS,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000,"
        f"aresample=48000,asetpts=PTS-STARTPTS[{out_label}]"
    )


def _clip_audio_filter(
    clip: Clip,
    source: VideoSource,
    input_index: int,
    out_label: str,
) -> str:
    if not source.has_audio:
        return _silence_audio_filter(clip.duration, out_label)

    parts = [
        f"[{input_index}:a]atrim=start={clip.in_sec}:end={clip.out_sec}",
        "asetpts=PTS-STARTPTS",
    ]
    if clip.speed != 1.0:
        parts.append(_atempo_chain(clip.speed))
    # Yhtenäinen formaatti ennen acrossfade/concat
    parts.extend(
        [
            "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000",
            "aresample=48000",
            "asetpts=PTS-STARTPTS",
        ]
    )
    return ",".join(parts) + f"[{out_label}]"


def _extra_audio_inputs(timeline: Timeline) -> list[str]:
    """Lisä-inputit filter/command -rakenteelle (audio_clips / aktiivinen raita, BGM)."""
    extras: list[str] = []
    if timeline.audio_clips:
        extras.extend(c.source_path for c in timeline.audio_clips)
    else:
        active = timeline.active_audio_track()
        if active is not None:
            extras.append(active.path)
    if timeline.bgm is not None:
        extras.append(timeline.bgm.path)
    return extras


def _audio_clip_filter(
    clip: AudioClip,
    input_index: int,
    out_label: str,
) -> str:
    """Yksi AudioClip: atrim → volume/afade → adelay timeline_startiin."""
    dur = max(clip.duration, 0.05)
    parts = [
        f"[{input_index}:a]atrim=start={clip.in_point:.6f}:end={clip.out_point:.6f}",
        "asetpts=PTS-STARTPTS",
        "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000",
        "aresample=48000",
        f"volume={float(clip.volume):.6f}",
    ]
    fade_in = min(float(clip.fade_in_sec), dur) if clip.fade_in_sec > 0 else 0.0
    fade_out = min(float(clip.fade_out_sec), dur) if clip.fade_out_sec > 0 else 0.0
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={fade_in:.6f}")
    if fade_out > 0:
        st = max(0.0, dur - fade_out)
        parts.append(f"afade=t=out:st={st:.6f}:d={fade_out:.6f}")
    delay_ms = max(0, int(round(float(clip.timeline_start) * 1000.0)))
    parts.append(f"adelay={delay_ms}|{delay_ms}")
    return ",".join(parts) + f"[{out_label}]"


def build_filter_complex(timeline: Timeline, mode: RenderMode | str = "preview") -> str:
    """Käännä Timeline deterministiseksi filter_complex -merkkijonoksi."""
    validate_timeline(timeline)
    profile = normalize_render_mode(mode)
    width, height = _output_size(timeline, profile)
    # Esikatselu: hieman alempi FPS nopeuttaa pitkiä videoita
    fps = min(timeline.output_fps, 30) if profile == "preview" else timeline.output_fps
    clip_paths = _clip_input_paths(timeline)
    n_clip_inputs = len(clip_paths)

    use_audio_clips = bool(timeline.audio_clips)
    active_track = timeline.active_audio_track()
    # audio_clips voittaa; muuten katkeamaton pool-raita / klippiääni
    use_continuous = (not use_audio_clips) and active_track is not None
    use_clip_audio = (
        (not use_audio_clips)
        and (not timeline.mute_video_audio)
        and (not use_continuous)
    )

    lines: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []

    for i, clip in enumerate(timeline.clips):
        source = timeline.sources[clip.source_id]
        inp = i  # yksi -i per klippi
        v_out = f"v{i}"
        lines.append(
            _clip_video_filter(
                clip, inp, v_out, width, height, fps, profile=profile
            )
        )
        video_labels.append(v_out)
        if use_clip_audio:
            a_out = f"a{i}"
            lines.append(_clip_audio_filter(clip, source, inp, a_out))
            audio_labels.append(a_out)

    current_v = video_labels[0]
    current_a = audio_labels[0] if use_clip_audio else None
    # Kertyvä ulostulon kesto (ensimmäisen osan pituus ennen seuraavaa xfadea)
    accumulated = float(timeline.clips[0].duration)
    n_clips = len(timeline.clips)

    for i in range(1, n_clips):
        next_v = video_labels[i]
        transition = timeline.transition_after(i - 1)
        out_v = f"vx{i}"

        if transition is not None:
            left_dur = timeline.clips[i - 1].duration
            right_dur = timeline.clips[i].duration
            # duration täytyy mahtua molempiin klipeihin (FFmpeg xfade/acrossfade)
            dur = min(float(transition.duration), left_dur - 1e-3, right_dur - 1e-3)
            if dur <= 0:
                raise ValueError(
                    f"transition after clip {i} has non-positive duration "
                    f"({transition.duration}s) for clips "
                    f"({left_dur:.3f}s, {right_dur:.3f}s)"
                )
            # offset = ensimmäisen osan kesto − siirtymän kesto
            xfade_offset = max(0.0, accumulated - dur)
            lines.append(
                f"[{current_v}][{next_v}]xfade=transition={transition.type}"
                f":duration={dur:.6f}:offset={xfade_offset:.6f}[{out_v}]"
            )
            if use_clip_audio:
                next_a = audio_labels[i]
                out_a = f"ax{i}"
                lines.append(
                    f"[{current_a}][{next_a}]acrossfade=d={dur:.6f}:c1=tri:c2=tri[{out_a}]"
                )
                current_a = out_a
            accumulated = max(0.0, accumulated + right_dur - dur)
        else:
            lines.append(f"[{current_v}][{next_v}]concat=n=2:v=1:a=0[{out_v}]")
            if use_clip_audio:
                next_a = audio_labels[i]
                out_a = f"ax{i}"
                lines.append(f"[{current_a}][{next_a}]concat=n=2:v=0:a=1[{out_a}]")
                current_a = out_a
            accumulated = accumulated + timeline.clips[i].duration

        current_v = out_v

    total = timeline.total_duration()
    fade_in = min(float(timeline.fade_in_sec), total) if timeline.fade_in_sec > 0 else 0.0
    fade_out = (
        min(float(timeline.fade_out_sec), total) if timeline.fade_out_sec > 0 else 0.0
    )

    # Koko aikajanan video-häivytykset lopulliseen voutiin
    v_fade_parts: list[str] = []
    if fade_in > 0:
        v_fade_parts.append(f"fade=t=in:st=0:d={fade_in:.6f}")
    if fade_out > 0:
        fade_out_st = max(0.0, total - fade_out)
        v_fade_parts.append(f"fade=t=out:st={fade_out_st:.6f}:d={fade_out:.6f}")
    if v_fade_parts:
        lines.append(f"[{current_v}]{','.join(v_fade_parts)}[vout]")
    elif current_v != "vout":
        lines.append(f"[{current_v}]null[vout]")

    next_extra = n_clip_inputs
    bed_dur = max(total, 0.05)

    if use_audio_clips:
        ac_labels: list[str] = []
        for i, aclip in enumerate(timeline.audio_clips):
            inp = next_extra + i
            label = f"ac{i}"
            lines.append(_audio_clip_filter(aclip, inp, label))
            ac_labels.append(label)
        next_extra += len(timeline.audio_clips)
        if len(ac_labels) == 1:
            lines.append(f"[{ac_labels[0]}]anull[a_clips]")
        else:
            mixed = "".join(f"[{lab}]" for lab in ac_labels)
            lines.append(
                f"{mixed}amix=inputs={len(ac_labels)}:duration=longest:normalize=0"
                f"[a_clips]"
            )
        lines.append(
            f"[a_clips]atrim=0:{bed_dur:.6f},asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={bed_dur:.6f},atrim=0:{bed_dur:.6f},"
            f"asetpts=PTS-STARTPTS[a_bed]"
        )
        bed_label = "a_bed"
    elif use_continuous:
        assert active_track is not None
        track_index = next_extra
        next_extra += 1
        start = max(0.0, float(timeline.active_audio_start_sec))
        end = start + bed_dur
        # Katkeamaton bed: aresample + atrim täsmälleen videon kestoon (apad jos lyhyempi)
        lines.append(
            f"[{track_index}:a]atrim=start={start:.6f}:end={end:.6f},"
            f"asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000,"
            f"aresample=48000,apad=whole_dur={bed_dur:.6f},"
            f"atrim=0:{bed_dur:.6f},asetpts=PTS-STARTPTS[a_bed]"
        )
        bed_label = "a_bed"
    elif use_clip_audio:
        bed_label = current_a or "a0"
    else:
        # Mykistetty eikä pool-raidasta → hiljaisuus (selaintoistin tarvitsee äänen)
        lines.append(_silence_audio_filter(bed_dur, "a_bed"))
        bed_label = "a_bed"

    if timeline.bgm is not None:
        bgm = timeline.bgm
        bgm_index = next_extra
        bgm_fade_start = max(total - bgm.fade_out_sec, 0.0)
        lines.append(f"[{bed_label}]volume={bgm.sfx_volume}[a_sfx]")
        bgm_parts = [
            f"[{bgm_index}:a]atrim=0:{total:.6f}",
            "asetpts=PTS-STARTPTS",
            "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000",
            "aresample=48000",
            f"volume={bgm.bgm_volume}",
        ]
        if bgm.fade_out_sec > 0 and total > 0:
            bgm_parts.append(
                f"afade=t=out:st={bgm_fade_start:.6f}:d={bgm.fade_out_sec:.6f}"
            )
        lines.append(",".join(bgm_parts) + "[a_bgm]")
        lines.append(
            "[a_sfx][a_bgm]amix=inputs=2:duration=first:dropout_transition=0[a_pre]"
        )
    else:
        lines.append(f"[{bed_label}]anull[a_pre]")

    # Aikajanan afade (audio_clips / klippiääni / AudioTrack / BGM-mix)
    has_real_audio = (
        use_audio_clips
        or use_clip_audio
        or use_continuous
        or timeline.bgm is not None
    )
    a_fade_parts: list[str] = []
    if timeline.fade_audio and has_real_audio:
        if fade_in > 0:
            a_fade_parts.append(f"afade=t=in:st=0:d={fade_in:.6f}")
        if fade_out > 0:
            fade_out_st = max(0.0, total - fade_out)
            a_fade_parts.append(f"afade=t=out:st={fade_out_st:.6f}:d={fade_out:.6f}")
    if a_fade_parts:
        lines.append(f"[a_pre]{','.join(a_fade_parts)}[aout]")
    else:
        lines.append("[a_pre]anull[aout]")

    return ";\n".join(lines)


def _encoder_args(mode: RenderMode | str) -> list[str]:
    profile = normalize_render_mode(mode)
    if profile == "preview":
        # Nopea CPU-esikatselu (ultrafast) – ennustettava myös ilman NVENC:iä
        return [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-threads",
            "0",
        ]
    # export: korkea laatu, natiiviresoluutio
    if nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-threads",
        "0",
    ]


def build_command(
    timeline: Timeline,
    output_path: str | Path,
    mode: RenderMode | str = "preview",
) -> list[str]:
    """Rakenna täydellinen ffmpeg-komento listana (preview | export/final)."""
    validate_timeline(timeline)
    profile = normalize_render_mode(mode)
    clip_paths = _clip_input_paths(timeline)
    filter_complex = build_filter_complex(timeline, mode=profile)

    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner"]
    for path in clip_paths:
        cmd.extend(["-i", path])
    for path in _extra_audio_inputs(timeline):
        cmd.extend(["-i", path])

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
        ]
    )
    cmd.extend(_encoder_args(profile))
    audio_bitrate = "96k" if profile == "preview" else "192k"
    out_fps = (
        min(timeline.output_fps, 30) if profile == "preview" else timeline.output_fps
    )
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            "-r",
            str(out_fps),
            "-movflags",
            "+faststart",
        ]
    )

    if profile == "preview":
        width, height = _output_size(timeline, profile)
        cmd.extend(["-s", f"{width}x{height}"])

    cmd.append(str(output_path))
    return cmd


def compile_timeline(
    timeline: Timeline,
    output_path: str | Path,
    mode: RenderMode | str = "preview",
    **render_kwargs,
) -> str:
    """Selkeä entrypoint: käännä ja renderöi timeline (preview | export)."""
    return render(timeline, output_path=output_path, mode=mode, **render_kwargs)


def percent_from_out_time_us(out_time_us: int, total_duration_sec: float) -> int:
    """Laske valmistumisprosentti 0..100 out_time_us / kokonaiskesto -suhteesta."""
    if total_duration_sec <= 0 or out_time_us <= 0:
        return 0
    pct = int((out_time_us / (total_duration_sec * 1_000_000)) * 100)
    return min(100, max(0, pct))


def parse_progress_value(line: str) -> tuple[str, str] | None:
    """Parsii FFmpeg `-progress`-rivin (`key=value`)."""
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


def render(
    timeline: Timeline,
    output_path: str | Path | None = None,
    mode: RenderMode = "preview",
    workspace: str | Path | None = None,
    on_progress: Callable[[int], None] | None = None,
    total_duration: float | None = None,
) -> str:
    """Suorita ffmpeg ja palauta tulostiedoston polku.

    Kun ``on_progress`` annetaan, käytetään ``-progress pipe:1 -nostats``
    ja kutsutaan callbackia prosentilla 0..100.
    """
    validate_timeline(timeline)
    profile = normalize_render_mode(mode)
    if output_path is None:
        root = Path(workspace) if workspace else Path(tempfile.gettempdir())
        root.mkdir(parents=True, exist_ok=True)
        suffix = "preview" if profile == "preview" else "final"
        output_path = root / f"cutsmith_{suffix}.mp4"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_command(timeline, output_path, mode=profile)
    duration = (
        float(total_duration)
        if total_duration is not None
        else float(timeline.total_duration())
    )

    if on_progress is not None:
        # -progress pipe:1 ennen tulostiedostoa
        cmd = cmd[:-1] + ["-progress", "pipe:1", "-nostats"] + [cmd[-1]]
        return _render_with_progress(cmd, output_path, duration, on_progress)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    if result.returncode != 0:
        detail = format_ffmpeg_error(result.stderr or result.stdout or "")
        raise RuntimeError(f"ffmpeg render failed:\n{detail}")
    return str(output_path)


def _render_with_progress(
    cmd: list[str],
    output_path: Path,
    total_duration: float,
    on_progress: Callable[[int], None],
) -> str:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
        bufsize=1,
    )
    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for chunk in proc.stderr:
            stderr_chunks.append(chunk)

    reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()

    last_percent = -1
    try:
        on_progress(0)
        assert proc.stdout is not None
        for raw in proc.stdout:
            parsed = parse_progress_value(raw)
            if parsed is None:
                continue
            key, value = parsed
            if key == "out_time_us":
                try:
                    out_us = int(value)
                except ValueError:
                    continue
                percent = percent_from_out_time_us(out_us, total_duration)
                if percent != last_percent:
                    last_percent = percent
                    on_progress(percent)
            elif key == "progress" and value == "end":
                if last_percent < 100:
                    on_progress(100)
        returncode = proc.wait()
    finally:
        reader.join(timeout=5)
        if proc.poll() is None:
            proc.kill()

    if returncode != 0:
        detail = format_ffmpeg_error("".join(stderr_chunks))
        raise RuntimeError(f"ffmpeg render failed:\n{detail}")
    if last_percent < 100:
        on_progress(100)
    return str(output_path)


def extract_still_frame(
    source_path: Path,
    timestamp: float,
    output_path: Path,
    *,
    scale: str = "960:-1",
) -> Path:
    """Poimi yksittäinen still-JPEG lähdevideon aikakohdasta (LC_ALL=C)."""
    source_path = Path(source_path)
    output_path = Path(output_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"video source not found: {source_path}")
    if timestamp < 0:
        raise ValueError("timestamp must be >= 0")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.6f}",
        "-i",
        str(source_path),
        "-vframes",
        "1",
        "-vf",
        f"scale={scale}",
        "-q:v",
        "3",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    if result.returncode != 0 or not output_path.is_file():
        detail = format_ffmpeg_error(result.stderr or result.stdout or "")
        raise RuntimeError(f"still frame extraction failed:\n{detail}")
    return output_path


def extract_audio_track(
    source_path: Path,
    output_path: Path,
) -> Path:
    """Irrota ääniraita lähdevideosta AAC-tiedostoksi (LC_ALL=C)."""
    source_path = Path(source_path)
    output_path = Path(output_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"video source not found: {source_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vn",
        "-acodec",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    if result.returncode != 0 or not output_path.is_file():
        detail = format_ffmpeg_error(result.stderr or result.stdout or "")
        raise RuntimeError(f"audio extraction failed:\n{detail}")
    return output_path


def extract_clip_thumbnail(
    source_path: Path,
    timestamp: float,
    output_path: Path,
) -> Path:
    """Poimi yksittäinen aloitusruutu JPEG-pikkukuvaksi (LC_ALL=C)."""
    return extract_still_frame(
        source_path, timestamp, output_path, scale="160:-1"
    )
