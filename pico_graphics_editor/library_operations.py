"""Shared non-UI operations for reusable library assets."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence

from .asset_library import LibraryAsset
from .canvas import pixel_art_image
from .model import PixelArt


@dataclass(frozen=True)
class PlannedPngExport:
    """Describe one collision-free PNG output before any file is written."""

    path: Path
    art: PixelArt


def plan_png_exports(
    records: Sequence[LibraryAsset], destination: Path
) -> tuple[PlannedPngExport, ...]:
    """Plan unique filenames across frames and files already in the directory."""
    occupied = {
        entry.name.casefold() for entry in destination.iterdir() if entry.exists()
    }
    planned: list[PlannedPngExport] = []
    for record in records:
        stem = _safe_stem(record.name)
        frames = record.pixel_frames()
        for index, frame in enumerate(frames):
            requested = f"{stem}-frame-{index + 1}" if len(frames) > 1 else stem
            filename = _available_png_filename(requested, occupied)
            occupied.add(filename.casefold())
            planned.append(PlannedPngExport(destination / filename, frame))
    return tuple(planned)


def write_png_exports(exports: Sequence[PlannedPngExport]) -> None:
    """Stage and publish a complete PNG batch without overwriting any target."""
    staged: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for export in exports:
            handle, temporary_name = tempfile.mkstemp(
                prefix=f".{export.path.name}.",
                suffix=".tmp",
                dir=export.path.parent,
            )
            os.close(handle)
            temporary = Path(temporary_name)
            staged.append((temporary, export.path))
            if not pixel_art_image(export.art).save(str(temporary), "PNG"):
                raise OSError(f"Could not encode {export.path.name}")
        for temporary, destination in staged:
            os.link(temporary, destination)
            published.append(destination)
    except Exception:
        for destination in published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        for temporary, unused_destination in staged:
            del unused_destination
            temporary.unlink(missing_ok=True)


def _safe_stem(name: str) -> str:
    """Return a portable visible filename stem."""
    return (
        "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in name
        ).strip("_")
        or "asset"
    )


def _available_png_filename(stem: str, occupied: set[str]) -> str:
    """Number one PNG filename until it is unused case-insensitively."""
    filename = f"{stem}.png"
    suffix = 2
    while filename.casefold() in occupied:
        filename = f"{stem}-{suffix}.png"
        suffix += 1
    return filename
