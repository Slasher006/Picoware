"""Reference-image preparation and deterministic pixel conversion."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QTransform

from .model import PixelArt, rgb565_to_rgb, rgb_to_rgb565


def prepare_reference_image(
    image: QImage,
    width: int,
    height: int,
    mode: str = "contain",
    rotation: int = 0,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
    scale_percent: int = 100,
    offset_x: int = 0,
    offset_y: int = 0,
) -> QImage:
    """Transform a reference into a transparent target-sized image."""
    target = QImage(width, height, QImage.Format.Format_ARGB32)
    target.fill(QColor(0, 0, 0, 0))
    if image.isNull():
        return target
    transformed = image.convertToFormat(QImage.Format.Format_ARGB32)
    if flip_horizontal or flip_vertical:
        transformed = transformed.mirrored(flip_horizontal, flip_vertical)
    normalized_rotation = rotation % 360
    if normalized_rotation:
        transformed = transformed.transformed(QTransform().rotate(normalized_rotation))
    source_width = max(1, transformed.width())
    source_height = max(1, transformed.height())
    if mode == "stretch":
        draw_width = width
        draw_height = height
    else:
        ratios = (width / source_width, height / source_height)
        ratio = max(ratios) if mode == "cover" else min(ratios)
        draw_width = max(1, round(source_width * ratio))
        draw_height = max(1, round(source_height * ratio))
    factor = max(1, scale_percent) / 100
    draw_width = max(1, round(draw_width * factor))
    draw_height = max(1, round(draw_height * factor))
    draw_x = (width - draw_width) // 2 + offset_x
    draw_y = (height - draw_height) // 2 + offset_y
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawImage(QRect(draw_x, draw_y, draw_width, draw_height), transformed)
    painter.end()
    return target


def image_to_pixel_art(
    image: QImage,
    width: int,
    height: int,
    color_count: int = 16,
    dither: bool = False,
    alpha_threshold: int = 16,
) -> PixelArt:
    """Convert an image into palette-limited RGB565 pixel art."""
    prepared = image.convertToFormat(QImage.Format.Format_ARGB32)
    if prepared.width() != width or prepared.height() != height:
        prepared = prepare_reference_image(prepared, width, height, "contain")
    palette = _median_cut_palette(prepared, color_count, alpha_threshold)
    art = PixelArt(width, height)
    if not palette:
        return art
    if dither:
        _dither_image(prepared, art, palette, alpha_threshold)
    else:
        for y in range(height):
            for x in range(width):
                color = prepared.pixelColor(x, y)
                if color.alpha() < alpha_threshold:
                    continue
                red, green, blue = _nearest_color(
                    color.red(), color.green(), color.blue(), palette
                )
                art.set_pixel(x, y, rgb_to_rgb565(red, green, blue))
    return art


def read_image_frames(path: str | Path) -> list[QImage]:
    """Read all supported frames from an image file."""
    frames, unused_durations = read_image_frames_with_durations(path)
    del unused_durations
    return frames


def read_image_frames_with_durations(
    path: str | Path,
) -> tuple[list[QImage], tuple[int, ...]]:
    """Read image frames together with supported per-frame delays."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    frames: list[QImage] = []
    delays: list[int] = []
    while reader.canRead():
        frame = reader.read()
        if frame.isNull():
            break
        frames.append(frame.convertToFormat(QImage.Format.Format_ARGB32))
        delays.append(reader.nextImageDelay())
        if not reader.jumpToNextImage():
            break
    durations = (
        tuple(max(40, delay) for delay in delays)
        if len(frames) > 1 and any(delay > 0 for delay in delays)
        else ()
    )
    return frames, durations


def split_sprite_sheet(
    image: QImage,
    frame_width: int,
    frame_height: int,
    margin: int = 0,
    spacing: int = 0,
) -> list[QImage]:
    """Split a regular sprite sheet into row-major frames."""
    if frame_width < 1 or frame_height < 1:
        return []
    frames: list[QImage] = []
    y = max(0, margin)
    while y + frame_height <= image.height() - max(0, margin):
        x = max(0, margin)
        while x + frame_width <= image.width() - max(0, margin):
            frames.append(image.copy(x, y, frame_width, frame_height))
            x += frame_width + max(0, spacing)
        y += frame_height + max(0, spacing)
    return frames


def _median_cut_palette(
    image: QImage, color_count: int, alpha_threshold: int
) -> list[tuple[int, int, int]]:
    """Build a deterministic weighted median-cut palette."""
    frequencies: Counter[tuple[int, int, int]] = Counter()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() >= alpha_threshold:
                frequencies[(color.red(), color.green(), color.blue())] += 1
    if not frequencies:
        return []
    boxes = [list(frequencies.items())]
    target_count = max(1, min(256, color_count))
    while len(boxes) < target_count:
        split_index = max(
            range(len(boxes)),
            key=lambda index: _box_priority(boxes[index]),
        )
        current = boxes.pop(split_index)
        split = _split_color_box(current)
        if split is None:
            boxes.insert(split_index, current)
            break
        boxes.extend(split)
    palette: list[tuple[int, int, int]] = []
    for box in boxes:
        total = sum(count for _, count in box)
        averaged = tuple(
            round(sum(color[channel] * count for color, count in box) / total)
            for channel in range(3)
        )
        palette.append(rgb565_to_rgb(rgb_to_rgb565(*averaged)))
    return palette


def _box_priority(box: list[tuple[tuple[int, int, int], int]]) -> int:
    """Rank a color box for the next palette split."""
    if len(box) < 2:
        return -1
    ranges = [
        max(color[channel] for color, _ in box)
        - min(color[channel] for color, _ in box)
        for channel in range(3)
    ]
    return max(ranges) * sum(count for _, count in box)


def _split_color_box(
    box: list[tuple[tuple[int, int, int], int]],
) -> (
    tuple[
        list[tuple[tuple[int, int, int], int]],
        list[tuple[tuple[int, int, int], int]],
    ]
    | None
):
    """Split a weighted color box around its median."""
    if len(box) < 2:
        return None
    ranges = [
        max(color[channel] for color, _ in box)
        - min(color[channel] for color, _ in box)
        for channel in range(3)
    ]
    channel = max(range(3), key=ranges.__getitem__)
    ordered = sorted(box, key=lambda item: item[0][channel])
    midpoint = sum(count for _, count in ordered) / 2
    cumulative = 0
    split_at = 1
    for index, (_, count) in enumerate(ordered[:-1], 1):
        cumulative += count
        split_at = index
        if cumulative >= midpoint:
            break
    return ordered[:split_at], ordered[split_at:]


def _nearest_color(
    red: float,
    green: float,
    blue: float,
    palette: list[tuple[int, int, int]],
) -> tuple[int, int, int]:
    """Return the nearest palette entry by squared distance."""
    return min(
        palette,
        key=lambda color: (red - color[0]) ** 2
        + (green - color[1]) ** 2
        + (blue - color[2]) ** 2,
    )


def _dither_image(
    image: QImage,
    art: PixelArt,
    palette: list[tuple[int, int, int]],
    alpha_threshold: int,
) -> None:
    """Apply Floyd-Steinberg error diffusion into pixel art."""
    width = art.width
    height = art.height
    errors = [[[0.0, 0.0, 0.0] for _ in range(width + 2)] for _ in range(2)]
    for y in range(height):
        current = errors[y % 2]
        following = errors[(y + 1) % 2]
        for item in following:
            item[:] = (0.0, 0.0, 0.0)
        for x in range(width):
            source = image.pixelColor(x, y)
            if source.alpha() < alpha_threshold:
                continue
            red = max(0, min(255, source.red() + current[x + 1][0]))
            green = max(0, min(255, source.green() + current[x + 1][1]))
            blue = max(0, min(255, source.blue() + current[x + 1][2]))
            chosen = _nearest_color(red, green, blue, palette)
            art.set_pixel(x, y, rgb_to_rgb565(*chosen))
            differences = (red - chosen[0], green - chosen[1], blue - chosen[2])
            _add_error(current[x + 2], differences, 7 / 16)
            if x:
                _add_error(following[x], differences, 3 / 16)
            _add_error(following[x + 1], differences, 5 / 16)
            _add_error(following[x + 2], differences, 1 / 16)


def _add_error(
    target: list[float], differences: tuple[float, ...], weight: float
) -> None:
    """Accumulate one weighted dithering error."""
    for channel in range(3):
        target[channel] += differences[channel] * weight
