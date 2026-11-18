"""Timeline → deterministinen FFmpeg filter_complex -kääntäjä."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from model.timeline import (
    Clip,
    TextEffect,
    Timeline,
    VideoSource,
    ZoomEffect,
)

RenderMode = Literal["preview", "final"]

_NVENC_CHECKED: bool | None = None

PREVIEW_WIDTH = 1280
PREVIEW_HEIGHT = 720


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


def _output_size(timeline: Timeline, mode: RenderMode) -> tuple[int, int]:
    if mode == "preview":
        return PREVIEW_WIDTH, PREVIEW_HEIGHT
    first = timeline.sources[timeline.clips[0].source_id]
    return first.width, first.height


def _source_input_map(timeline: Timeline) -> tuple[list[str], dict[str, int]]:
    paths: list[str] = []
    index_by_id: dict[str, int] = {}
    for source_id, source in timeline.sources.items():
        if any(c.source_id == source_id for c in timeline.clips):
            if source.path not in paths:
                paths.append(source.path)
            index_by_id[source_id] = paths.index(source.path)
    return paths, index_by_id


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
    return parts


def _clip_video_filter(
    clip: Clip,
    input_index: int,
    out_label: str,
    width: int,
    height: int,
    fps: int,
) -> str:
    parts = [
        f"[{input_index}:v]trim=start={clip.in_sec}:end={clip.out_sec}",
        "setpts=PTS-STARTPTS",
    ]
    if clip.speed != 1.0:
        parts.append(f"setpts=PTS/{clip.speed}")
    parts.extend(
        [
            f"fps={fps}",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
            "format=yuv420p",
        ]
    )
    parts.extend(_effect_filters(clip, width, height, fps))
    return ",".join(parts) + f"[{out_label}]"


def _clip_audio_filter(
    clip: Clip,
    source: VideoSource,
    input_index: int,
    out_label: str,
) -> str:
    if not source.has_audio:
        dur = clip.duration
        return (
            f"anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=0:{dur},asetpts=PTS-STARTPTS[{out_label}]"
        )

    parts = [
        f"[{input_index}:a]atrim=start={clip.in_sec}:end={clip.out_sec}",
        "asetpts=PTS-STARTPTS",
    ]
    if clip.speed != 1.0:
        parts.append(_atempo_chain(clip.speed))
    return ",".join(parts) + f"[{out_label}]"


def build_filter_complex(timeline: Timeline, mode: RenderMode = "preview") -> str:
    """Käännä Timeline deterministiseksi filter_complex -merkkijonoksi."""
    validate_timeline(timeline)
    width, height = _output_size(timeline, mode)
    fps = timeline.output_fps
    paths, index_by_id = _source_input_map(timeline)

    lines: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []

    for i, clip in enumerate(timeline.clips):
        source = timeline.sources[clip.source_id]
        inp = index_by_id[clip.source_id]
        v_out = f"v{i}"
        a_out = f"a{i}"
        lines.append(_clip_video_filter(clip, inp, v_out, width, height, fps))
        lines.append(_clip_audio_filter(clip, source, inp, a_out))
        video_labels.append(v_out)
        audio_labels.append(a_out)

    current_v = video_labels[0]
    current_a = audio_labels[0]
    offset = timeline.clips[0].duration
    n_clips = len(timeline.clips)

    for i in range(1, n_clips):
        next_v = video_labels[i]
        next_a = audio_labels[i]
        transition = timeline.transition_after(i - 1)
        out_v = f"vx{i}"
        out_a = f"ax{i}"

        if transition is not None:
            xfade_offset = offset - transition.duration
            lines.append(
                f"[{current_v}][{next_v}]xfade=transition={transition.type}"
                f":duration={transition.duration}:offset={xfade_offset:.6f}[{out_v}]"
            )
            lines.append(
                f"[{current_a}][{next_a}]acrossfade=d={transition.duration}[{out_a}]"
            )
            offset = offset + timeline.clips[i].duration - transition.duration
        else:
            lines.append(f"[{current_v}][{next_v}]concat=n=2:v=1:a=0[{out_v}]")
            lines.append(f"[{current_a}][{next_a}]concat=n=2:v=0:a=1[{out_a}]")
            offset = offset + timeline.clips[i].duration

        current_v = out_v
        current_a = out_a

    # Lopulliset videotunnisteet
    if current_v != "vout":
        lines.append(f"[{current_v}]null[vout]")

    total = timeline.total_duration()

    if timeline.bgm is not None:
        bgm = timeline.bgm
        bgm_index = len(paths)
        fade_start = max(total - bgm.fade_out_sec, 0.0)
        lines.append(f"[{current_a}]volume={bgm.sfx_volume}[a_sfx]")
        bgm_parts = [
            f"[{bgm_index}:a]atrim=0:{total:.6f}",
            "asetpts=PTS-STARTPTS",
            f"volume={bgm.bgm_volume}",
        ]
        if bgm.fade_out_sec > 0 and total > 0:
            bgm_parts.append(
                f"afade=t=out:st={fade_start:.6f}:d={bgm.fade_out_sec:.6f}"
            )
        lines.append(",".join(bgm_parts) + "[a_bgm]")
        lines.append(
            "[a_sfx][a_bgm]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    elif current_a != "aout":
        lines.append(f"[{current_a}]anull[aout]")

    return ";\n".join(lines)


def _encoder_args(mode: RenderMode) -> list[str]:
    use_nvenc = nvenc_available()
    if mode == "preview":
        if use_nvenc:
            return ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "28"]
        return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"]
    if use_nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19"]
    return ["-c:v", "libx264", "-preset", "slow", "-crf", "18"]


def build_command(
    timeline: Timeline,
    output_path: str | Path,
    mode: RenderMode = "preview",
) -> list[str]:
    """Rakenna täydellinen ffmpeg-komento listana."""
    validate_timeline(timeline)
    paths, _ = _source_input_map(timeline)
    filter_complex = build_filter_complex(timeline, mode=mode)

    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner"]
    for path in paths:
        cmd.extend(["-i", path])
    if timeline.bgm is not None:
        cmd.extend(["-i", timeline.bgm.path])

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
    cmd.extend(_encoder_args(mode))
    cmd.extend(["-c:a", "aac", "-b:a", "192k", "-r", str(timeline.output_fps)])

    if mode == "preview":
        width, height = _output_size(timeline, mode)
        cmd.extend(["-s", f"{width}x{height}"])

    cmd.append(str(output_path))
    return cmd


def render(
    timeline: Timeline,
    output_path: str | Path | None = None,
    mode: RenderMode = "preview",
    workspace: str | Path | None = None,
) -> str:
    """Suorita ffmpeg ja palauta tulostiedoston polku."""
    validate_timeline(timeline)
    if output_path is None:
        root = Path(workspace) if workspace else Path(tempfile.gettempdir())
        root.mkdir(parents=True, exist_ok=True)
        suffix = "preview" if mode == "preview" else "final"
        output_path = root / f"cutsmith_{suffix}.mp4"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_command(timeline, output_path, mode=mode)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffmpeg render failed:\n{detail}")
    return str(output_path)


def extract_clip_thumbnail(
    source_path: Path,
    timestamp: float,
    output_path: Path,
) -> Path:
    """Poimi yksittäinen aloitusruutu JPEG-pikkukuvaksi (LC_ALL=C)."""
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
        "scale=160:-1",
        "-q:v",
        "4",
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
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"thumbnail extraction failed:\n{detail}")
    return output_path
