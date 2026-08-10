"""Pixel document and raster drawing models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable


Color = int | None


@dataclass
class Primitive:
    """Describe one renderer drawing operation."""

    kind: str
    values: tuple[int, ...]
    color: int
    line: int
    generated: bool = False


@dataclass
class PixelArt:
    """Store an editable RGB565 pixel surface."""

    width: int
    height: int
    origin_x: int = 0
    origin_y: int = 0
    pixels: list[Color] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate dimensions and initialize empty pixels."""
        if self.width < 1 or self.height < 1:
            raise ValueError("Pixel art dimensions must be positive")
        expected = self.width * self.height
        if not self.pixels:
            self.pixels = [None] * expected
        elif len(self.pixels) != expected:
            raise ValueError("Pixel data does not match dimensions")

    def copy(self) -> PixelArt:
        """Return an independent pixel document copy."""
        return PixelArt(
            self.width,
            self.height,
            self.origin_x,
            self.origin_y,
            self.pixels.copy(),
        )

    def contains(self, x: int, y: int) -> bool:
        """Return whether coordinates are inside the surface."""
        return 0 <= x < self.width and 0 <= y < self.height

    def pixel(self, x: int, y: int) -> Color:
        """Return one pixel or transparent outside the surface."""
        if not self.contains(x, y):
            return None
        return self.pixels[y * self.width + x]

    def set_pixel(self, x: int, y: int, color: Color) -> None:
        """Set one pixel when coordinates are valid."""
        if self.contains(x, y):
            self.pixels[y * self.width + x] = color

    def draw_line(self, x1: int, y1: int, x2: int, y2: int, color: Color) -> None:
        """Draw a one-pixel Bresenham line."""
        dx = abs(x2 - x1)
        sx = 1 if x1 < x2 else -1
        dy = -abs(y2 - y1)
        sy = 1 if y1 < y2 else -1
        error = dx + dy
        while True:
            self.set_pixel(x1, y1, color)
            if x1 == x2 and y1 == y2:
                return
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x1 += sx
            if doubled <= dx:
                error += dx
                y1 += sy

    def draw_rectangle(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        color: Color,
        filled: bool = False,
    ) -> None:
        """Draw a filled or outlined rectangle."""
        if width <= 0 or height <= 0:
            return
        if filled:
            start_x = max(0, x)
            end_x = min(self.width, x + width)
            start_y = max(0, y)
            end_y = min(self.height, y + height)
            for py in range(start_y, end_y):
                for px in range(start_x, end_x):
                    self.set_pixel(px, py, color)
            return
        self.draw_line(x, y, x + width - 1, y, color)
        self.draw_line(x, y + height - 1, x + width - 1, y + height - 1, color)
        self.draw_line(x, y, x, y + height - 1, color)
        self.draw_line(x + width - 1, y, x + width - 1, y + height - 1, color)

    def draw_circle(
        self,
        center_x: int,
        center_y: int,
        radius: int,
        color: Color,
        filled: bool = False,
    ) -> None:
        """Draw a filled or outlined midpoint circle."""
        radius = max(0, radius)
        x = radius
        y = 0
        decision = 1 - radius
        while x >= y:
            if filled:
                self.draw_line(
                    center_x - x, center_y + y, center_x + x, center_y + y, color
                )
                self.draw_line(
                    center_x - x, center_y - y, center_x + x, center_y - y, color
                )
                self.draw_line(
                    center_x - y, center_y + x, center_x + y, center_y + x, color
                )
                self.draw_line(
                    center_x - y, center_y - x, center_x + y, center_y - x, color
                )
            else:
                points = (
                    (center_x + x, center_y + y),
                    (center_x + y, center_y + x),
                    (center_x - y, center_y + x),
                    (center_x - x, center_y + y),
                    (center_x - x, center_y - y),
                    (center_x - y, center_y - x),
                    (center_x + y, center_y - x),
                    (center_x + x, center_y - y),
                )
                for px, py in points:
                    self.set_pixel(px, py, color)
            y += 1
            if decision <= 0:
                decision += 2 * y + 1
            else:
                x -= 1
                decision += 2 * (y - x) + 1

    def flood_fill(self, x: int, y: int, color: Color) -> None:
        """Replace one connected pixel region."""
        target = self.pixel(x, y)
        if target == color or not self.contains(x, y):
            return
        queue = deque([(x, y)])
        self.set_pixel(x, y, color)
        while queue:
            px, py = queue.popleft()
            for next_x, next_y in (
                (px - 1, py),
                (px + 1, py),
                (px, py - 1),
                (px, py + 1),
            ):
                if (
                    self.contains(next_x, next_y)
                    and self.pixel(next_x, next_y) == target
                ):
                    self.set_pixel(next_x, next_y, color)
                    queue.append((next_x, next_y))

    def used_colors(self) -> list[int]:
        """Return distinct nontransparent colors in display order."""
        seen: set[int] = set()
        result: list[int] = []
        for color in self.pixels:
            if color is not None and color not in seen:
                seen.add(color)
                result.append(color)
        return result

    def changed_pixels(self, baseline: PixelArt) -> list[tuple[int, int, Color]]:
        """Return pixels that differ from a compatible baseline."""
        if (
            self.width != baseline.width
            or self.height != baseline.height
            or self.origin_x != baseline.origin_x
            or self.origin_y != baseline.origin_y
        ):
            raise ValueError("Pixel surfaces are not aligned")
        result: list[tuple[int, int, Color]] = []
        for index, color in enumerate(self.pixels):
            if color != baseline.pixels[index]:
                result.append((index % self.width, index // self.width, color))
        return result

    def horizontal_runs(self, baseline: PixelArt) -> list[tuple[int, int, int, int]]:
        """Compress changed opaque pixels into horizontal runs."""
        changed = {(x, y): color for x, y, color in self.changed_pixels(baseline)}
        runs: list[tuple[int, int, int, int]] = []
        for y in range(self.height):
            x = 0
            while x < self.width:
                color = changed.get((x, y))
                if color is None:
                    x += 1
                    continue
                end = x + 1
                while end < self.width and changed.get((end, y)) == color:
                    end += 1
                runs.append((x, y, end - x, color))
                x = end
        return runs


def rgb565_to_rgb(color: int) -> tuple[int, int, int]:
    """Convert one RGB565 integer to RGB888."""
    red = (color >> 11) & 0x1F
    green = (color >> 5) & 0x3F
    blue = color & 0x1F
    return (
        (red * 255 + 15) // 31,
        (green * 255 + 31) // 63,
        (blue * 255 + 15) // 31,
    )


def rgb_to_rgb565(red: int, green: int, blue: int) -> int:
    """Convert RGB888 channels to one RGB565 integer."""
    red5 = (max(0, min(255, red)) * 31 + 127) // 255
    green6 = (max(0, min(255, green)) * 63 + 127) // 255
    blue5 = (max(0, min(255, blue)) * 31 + 127) // 255
    return (red5 << 11) | (green6 << 5) | blue5


def primitive_bounds(primitives: Iterable[Primitive]) -> tuple[int, int, int, int]:
    """Return inclusive bounds for drawable primitives."""
    minimum_x: int | None = None
    minimum_y: int | None = None
    maximum_x: int | None = None
    maximum_y: int | None = None
    for primitive in primitives:
        if primitive.kind in ("fill_rect", "rect"):
            x, y, width, height = primitive.values
            left, top = x, y
            right, bottom = x + max(1, width) - 1, y + max(1, height) - 1
        elif primitive.kind == "line":
            x1, y1, x2, y2 = primitive.values
            left, top = min(x1, x2), min(y1, y2)
            right, bottom = max(x1, x2), max(y1, y2)
        elif primitive.kind in ("circle", "fill_circle"):
            center_x, center_y, radius = primitive.values
            left, top = center_x - radius, center_y - radius
            right, bottom = center_x + radius, center_y + radius
        elif primitive.kind == "pixel":
            left, top = primitive.values
            right, bottom = left, top
        else:
            continue
        minimum_x = left if minimum_x is None else min(minimum_x, left)
        minimum_y = top if minimum_y is None else min(minimum_y, top)
        maximum_x = right if maximum_x is None else max(maximum_x, right)
        maximum_y = bottom if maximum_y is None else max(maximum_y, bottom)
    if minimum_x is None:
        return 0, 0, 31, 31
    return minimum_x, minimum_y or 0, maximum_x or 0, maximum_y or 0


def rasterize_primitives(
    primitives: Iterable[Primitive],
    padding: int = 2,
    maximum_size: int = 320,
) -> PixelArt:
    """Rasterize drawing primitives into an editable surface."""
    primitive_list = list(primitives)
    left, top, right, bottom = primitive_bounds(primitive_list)
    if left == 0 and top == 0 and right >= 300 and bottom >= 300:
        padding = 0
    left -= padding
    top -= padding
    right += padding
    bottom += padding
    width = min(maximum_size, max(1, right - left + 1))
    height = min(maximum_size, max(1, bottom - top + 1))
    art = PixelArt(width, height, left, top)
    for primitive in primitive_list:
        values = primitive.values
        color = primitive.color
        if primitive.kind == "fill_rect":
            x, y, item_width, item_height = values
            art.draw_rectangle(x - left, y - top, item_width, item_height, color, True)
        elif primitive.kind == "rect":
            x, y, item_width, item_height = values
            art.draw_rectangle(x - left, y - top, item_width, item_height, color, False)
        elif primitive.kind == "line":
            x1, y1, x2, y2 = values
            art.draw_line(x1 - left, y1 - top, x2 - left, y2 - top, color)
        elif primitive.kind == "fill_circle":
            center_x, center_y, radius = values
            art.draw_circle(center_x - left, center_y - top, radius, color, True)
        elif primitive.kind == "circle":
            center_x, center_y, radius = values
            art.draw_circle(center_x - left, center_y - top, radius, color, False)
        elif primitive.kind == "pixel":
            x, y = values
            art.set_pixel(x - left, y - top, color)
    return art
