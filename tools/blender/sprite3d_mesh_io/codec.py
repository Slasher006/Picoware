# SPDX-License-Identifier: GPL-3.0-or-later
"""Codec for the specific headerless triangle layout found in knight.sprite3d.

Each record: 9 little-endian float32 coordinates, 2 opaque little-endian uint16s.
The first uint16 is *interpreted* as RGB565 when color editing is requested.
The meaning of the second uint16 is unknown. It must not be treated as padding.
This is not a general Godot scene/resource loader. No third-party dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import struct
import stat
import tempfile
from typing import Iterable

RECORD = struct.Struct('<9fHH')
VERTEX = struct.Struct('<3f')
MAX_TRIANGLES = 250_000
MAX_BYTES = MAX_TRIANGLES * RECORD.size


class FormatError(ValueError):
    """Unsupported input or data that cannot be represented by this format."""


@dataclass(frozen=True)
class Triangle:
    coordinates: tuple[float, ...]
    color: int
    extra: int


def decode(data: bytes) -> list[Triangle]:
    if not data:
        raise FormatError('Empty file: expected headerless 40-byte triangle records.')
    if len(data) % RECORD.size:
        raise FormatError(
            f'File size {len(data):,} is not a multiple of 40. '
            'Only the binary layout used by knight.sprite3d is supported.')
    if len(data) > MAX_BYTES:
        raise FormatError(f'Safety limit: at most {MAX_TRIANGLES:,} triangles per file.')
    result = []
    for index, values in enumerate(RECORD.iter_unpack(data), 1):
        if not all(math.isfinite(v) for v in values[:9]):
            raise FormatError(f'Triangle {index} has a non-finite coordinate; wrong format?')
        result.append(Triangle(values[:9], values[9], values[10]))
    return result


def read(path: str | Path) -> list[Triangle]:
    with Path(path).open('rb') as stream:
        # Limit the read as well as checking file size; do not trust a size race.
        return decode(stream.read(MAX_BYTES + 1))


def encode(triangles: Iterable[Triangle]) -> bytes:
    output = bytearray()
    for index, triangle in enumerate(triangles, 1):
        if index > MAX_TRIANGLES:
            raise FormatError(f'Safety limit: at most {MAX_TRIANGLES:,} triangles per file.')
        coords = triangle.coordinates
        if len(coords) != 9 or not all(math.isfinite(v) for v in coords):
            raise FormatError(f'Triangle {index} needs nine finite coordinates.')
        for label, value in (('color', triangle.color), ('extra data', triangle.extra)):
            if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
                raise FormatError(f'Triangle {index}: {label} must be an integer from 0 to 65535.')
        try:
            output.extend(RECORD.pack(*coords, triangle.color, triangle.extra))
        except (OverflowError, struct.error) as exc:
            raise FormatError(f'Triangle {index} is outside the float32 range.') from exc
    if not output:
        raise FormatError('Nothing to export: no triangle faces were found.')
    return bytes(output)


def source_to_blender(vertex, axes: str = 'Y_UP', scale: float = 1.0):
    """Exact permutation, not a trigonometric 90-degree rotation matrix."""
    check_scale(scale)
    x, y, z = vertex
    if axes == 'Y_UP':
        result = (x, -z, y)
    elif axes == 'BLENDER':
        result = (x, y, z)
    else:
        raise FormatError(f'Unsupported axis convention: {axes!r}')
    return result if scale == 1.0 else tuple(v * scale for v in result)


def blender_to_source(vertex, axes: str = 'Y_UP', import_scale: float = 1.0):
    check_scale(import_scale)
    x, y, z = vertex
    if axes == 'Y_UP':
        result = (x, z, -y)
    elif axes == 'BLENDER':
        result = (x, y, z)
    else:
        raise FormatError(f'Unsupported axis convention: {axes!r}')
    return result if import_scale == 1.0 else tuple(v / import_scale for v in result)


def check_scale(scale: float):
    if not math.isfinite(scale) or scale <= 0:
        raise FormatError('Scale must be a finite number greater than zero.')


def build_mesh_arrays(triangles, axes='Y_UP', scale=1.0, weld=True):
    """Return vertices/faces; weld only bit-identical source coordinates.

    Joining coincident coordinates can join formerly disconnected surfaces.
    It is optional. Degenerate triangles use separate indices to keep valid
    Blender loop topology, without moving their original positions.
    """
    vertices, faces, lookup = [], [], {}
    for tri in triangles:
        face = []
        for start in (0, 3, 6):
            xyz = tri.coordinates[start:start + 3]
            key = VERTEX.pack(*xyz)
            index = lookup.get(key) if weld else None
            if index is None or index in face:
                index = len(vertices)
                vertices.append(source_to_blender(xyz, axes, scale))
                lookup.setdefault(key, index)
            face.append(index)
        faces.append(tuple(face))
    return vertices, faces


def rgb565_to_srgb(value: int):
    return ((value >> 11 & 31) / 31.0,
            (value >> 5 & 63) / 63.0,
            (value & 31) / 31.0)


def srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value: float) -> float:
    if not math.isfinite(value):
        raise FormatError('Material color contains a non-finite value.')
    value = min(1.0, max(0.0, value))
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1.0 / 2.4) - 0.055


def rgb565_to_linear(value: int):
    return tuple(srgb_to_linear(v) for v in rgb565_to_srgb(value))


def linear_to_rgb565(rgb) -> int:
    if len(rgb) < 3:
        raise FormatError('Material color needs three channels.')
    r, g, b = (linear_to_srgb(float(v)) for v in rgb[:3])
    # Half-up rounding, clamped for float roundoff. No texture or shader baking.
    red = min(31, max(0, int(r * 31 + 0.5)))
    green = min(63, max(0, int(g * 63 + 0.5)))
    blue = min(31, max(0, int(b * 31 + 0.5)))
    return (red << 11) | (green << 5) | blue


def write_atomic(path: str | Path, data: bytes, backup: bool = True) -> Path | None:
    """Validate, write a temporary sibling, back up old bytes, then replace.

    Backup names are unique. A failed backup prevents replacement. Symlinks
    are refused to avoid accidentally replacing an unrelated linked asset.
    Returns the new backup path, if a previous destination was backed up.
    """
    decode(data)
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise FormatError('Refusing to replace a symbolic link. Choose a regular file path.')
    if path.exists() and not path.is_file():
        raise FormatError('The destination exists but is not a regular file.')
    if not path.parent.is_dir():
        raise FormatError('The destination folder does not exist.')
    previous = None
    temp_path = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
        temp_path = Path(temp_name)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temp_path, stat.S_IMODE(path.stat().st_mode))
        if path.exists() and backup:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
            previous = path.with_name(f'{path.name}.{stamp}.bak')
            with path.open('rb') as source, previous.open('xb') as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
        os.replace(temp_path, path)
        temp_path = None
        return previous
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
