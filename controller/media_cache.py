"""Still-ruutujen ja klippi-pikkukuvien välimuistitus workspace-kansioon."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        return 0.0


def cached_still_frame(
    workspace: Path,
    source_path: Path,
    source_ts: float,
    *,
    extractor: Callable[..., Path],
    scale: str = "960:-1",
) -> Path:
    """Palauta välimuistitettu still-JPEG aikajanan kohdasta."""
    mtime = _file_mtime(source_path)
    digest = hashlib.sha256(
        f"{source_path.resolve()}|{mtime:.6f}|{source_ts:.3f}".encode()
    ).hexdigest()[:16]
    frames_dir = Path(workspace) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    output = frames_dir / f"{digest}.jpg"
    if output.is_file() and output.stat().st_size > 0:
        return output
    return extractor(source_path, source_ts, output, scale=scale)


def cached_clip_thumbnail(
    workspace: Path,
    source_path: Path,
    trim_start: float,
    *,
    extractor: Callable[[Path, float, Path], Path],
) -> Path:
    """Palauta välimuistitettu aloitusruudun pikkukuva klipille."""
    mtime = _file_mtime(source_path)
    digest = hashlib.sha256(
        f"{source_path.resolve()}|{mtime:.6f}|{trim_start:.6f}".encode()
    ).hexdigest()[:16]
    thumb_dir = Path(workspace) / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    output = thumb_dir / f"{digest}.jpg"
    if output.is_file() and output.stat().st_size > 0:
        return output
    return extractor(source_path, trim_start, output)
