"""Bounded GUI-thread thumbnail cache shared by asset browser surfaces."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap

from .canvas import pixel_art_image
from .model import PixelArt


THUMBNAIL_CACHE_LIMIT = 32 * 1024 * 1024
_thumbnail_cache: OrderedDict[tuple[object, ...], QPixmap] = OrderedDict()
_thumbnail_cache_bytes = 0


def cached_pixel_frame_pixmap(
    identity: object,
    width: int,
    height: int,
    origin_x: int,
    origin_y: int,
    pixels: Sequence[int | None],
    target: QSize,
    *,
    frame_index: int = 0,
    checker: bool = True,
) -> QPixmap:
    """Return a nearest-neighbor thumbnail without repeatedly decoding pixels."""
    key = (
        identity,
        frame_index,
        width,
        height,
        target.width(),
        target.height(),
        checker,
    )
    cached = _thumbnail_cache.get(key)
    if cached is not None:
        _thumbnail_cache.move_to_end(key)
        return cached

    art = PixelArt(width, height, origin_x, origin_y, list(pixels))
    pixmap = QPixmap.fromImage(pixel_art_image(art, checker=checker)).scaled(
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    _store_thumbnail(key, pixmap)
    return pixmap


def cached_pixel_art_pixmap(
    identity: object,
    art: PixelArt,
    target: QSize,
    *,
    frame_index: int = 0,
    checker: bool = True,
) -> QPixmap:
    """Return a cached thumbnail for an already materialized pixel document."""
    return cached_pixel_frame_pixmap(
        identity,
        art.width,
        art.height,
        art.origin_x,
        art.origin_y,
        art.pixels,
        target,
        frame_index=frame_index,
        checker=checker,
    )


def _store_thumbnail(key: tuple[object, ...], pixmap: QPixmap) -> None:
    """Insert one pixmap and evict least-recently-used entries over the limit."""
    global _thumbnail_cache_bytes
    _thumbnail_cache[key] = pixmap
    _thumbnail_cache_bytes += pixmap.width() * pixmap.height() * 4
    while _thumbnail_cache_bytes > THUMBNAIL_CACHE_LIMIT and len(_thumbnail_cache) > 1:
        unused_key, unused = _thumbnail_cache.popitem(last=False)
        del unused_key
        _thumbnail_cache_bytes -= unused.width() * unused.height() * 4
