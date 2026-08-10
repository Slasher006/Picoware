"""Mouse-driven Qt pixel editing canvas."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from .model import PixelArt, rgb565_to_rgb


class PixelCanvas(QWidget):
    """Edit one pixel document with mouse tools."""

    color_picked = Signal(int)
    document_changed = Signal()
    cursor_changed = Signal(int, int, object)
    zoom_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        """Initialize the editable canvas."""
        super().__init__(parent)
        self._art = PixelArt(32, 32)
        self._tool = "pencil"
        self._color = 0xFFFF
        self._background = 0x0000
        self._zoom = 12
        self._show_grid = True
        self._undo: list[PixelArt] = []
        self._redo: list[PixelArt] = []
        self._drawing = False
        self._start: tuple[int, int] | None = None
        self._last: tuple[int, int] | None = None
        self._gesture_base: PixelArt | None = None
        self._display_cache: QImage | None = None
        self._onion_art: PixelArt | None = None
        self._onion_cache: QImage | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_size()

    def art(self) -> PixelArt:
        """Return the active mutable pixel document."""
        return self._art

    def set_art(self, art: PixelArt) -> None:
        """Replace the document and reset edit history."""
        self._art = art.copy()
        self._undo.clear()
        self._redo.clear()
        self._gesture_base = None
        self._display_cache = None
        self._onion_art = None
        self._onion_cache = None
        self._update_size()
        self.update()
        self.document_changed.emit()

    def set_tool(self, tool: str) -> None:
        """Select the current drawing tool."""
        self._tool = tool

    def set_color(self, color: int) -> None:
        """Set the active RGB565 drawing color."""
        self._color = color & 0xFFFF

    def set_background_color(self, color: int) -> None:
        """Set the eraser replacement color."""
        self._background = color & 0xFFFF

    def set_zoom(self, zoom: int) -> None:
        """Set the integer display zoom."""
        new_zoom = max(1, min(40, zoom))
        if new_zoom == self._zoom:
            return
        self._zoom = new_zoom
        self._update_size()
        self.update()
        self.zoom_changed.emit(self._zoom)

    def zoom(self) -> int:
        """Return the integer display zoom."""
        return self._zoom

    def set_grid_visible(self, visible: bool) -> None:
        """Show or hide pixel grid lines."""
        self._show_grid = visible
        self.update()

    def set_onion_art(self, art: PixelArt | None) -> None:
        """Set an optional animation frame overlay."""
        if art is None:
            self._onion_art = None
            self._onion_cache = None
        else:
            aligned = PixelArt(
                self._art.width,
                self._art.height,
                self._art.origin_x,
                self._art.origin_y,
            )
            for y in range(art.height):
                for x in range(art.width):
                    color = art.pixel(x, y)
                    if color is None:
                        continue
                    target_x = art.origin_x + x - self._art.origin_x
                    target_y = art.origin_y + y - self._art.origin_y
                    aligned.set_pixel(target_x, target_y, color)
            self._onion_art = aligned
            self._onion_cache = pixel_art_image(self._onion_art)
        self.update()

    def can_undo(self) -> bool:
        """Return whether an edit can be undone."""
        return bool(self._undo)

    def can_redo(self) -> bool:
        """Return whether an edit can be redone."""
        return bool(self._redo)

    def undo(self) -> None:
        """Restore the preceding pixel state."""
        if not self._undo:
            return
        self._redo.append(self._art.copy())
        self._art = self._undo.pop()
        self._display_cache = None
        self.update()
        self.document_changed.emit()

    def redo(self) -> None:
        """Restore the next pixel state."""
        if not self._redo:
            return
        self._undo.append(self._art.copy())
        self._art = self._redo.pop()
        self._display_cache = None
        self.update()
        self.document_changed.emit()

    def sizeHint(self) -> QSize:
        """Return the zoomed document size."""
        return QSize(self._art.width * self._zoom, self._art.height * self._zoom)

    def paintEvent(self, event) -> None:
        """Paint the scaled pixels and optional grid."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        cell = self._zoom
        if self._display_cache is None:
            self._display_cache = pixel_art_image(
                self._art,
                transparent=False,
                checker=True,
            )
        painter.drawImage(
            QRect(0, 0, self._art.width * cell, self._art.height * cell),
            self._display_cache,
        )
        if self._onion_cache is not None:
            painter.setOpacity(0.28)
            painter.drawImage(
                QRect(0, 0, self._art.width * cell, self._art.height * cell),
                self._onion_cache,
            )
            painter.setOpacity(1.0)
        if self._show_grid and cell >= 6:
            painter.setPen(QPen(QColor(0, 0, 0, 55), 1))
            for x in range(self._art.width + 1):
                painter.drawLine(x * cell, 0, x * cell, self._art.height * cell)
            for y in range(self._art.height + 1):
                painter.drawLine(0, y * cell, self._art.width * cell, y * cell)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin drawing or pick a color."""
        point = self._pixel_at(event.position().toPoint())
        if point is None:
            return
        x, y = point
        if event.button() == Qt.MouseButton.RightButton or self._tool == "picker":
            color = self._art.pixel(x, y)
            if color is not None:
                self.color_picked.emit(color)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._push_undo()
        self._drawing = True
        self._start = point
        self._last = point
        self._gesture_base = self._art.copy()
        if self._tool == "fill":
            self._art.flood_fill(x, y, self._color)
            self._display_cache = None
            self._finish_gesture()
        elif self._tool in {"pencil", "eraser"}:
            self._art.set_pixel(x, y, self._paint_color())
            self._display_cache = None
            self.update()
            self.document_changed.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Continue drawing and report cursor coordinates."""
        point = self._pixel_at(event.position().toPoint())
        if point is None:
            return
        x, y = point
        self.cursor_changed.emit(x, y, self._art.pixel(x, y))
        if not self._drawing or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._tool in {"pencil", "eraser"} and self._last is not None:
            last_x, last_y = self._last
            self._art.draw_line(last_x, last_y, x, y, self._paint_color())
            self._last = point
        elif (
            self._tool in {"line", "rectangle"}
            and self._gesture_base is not None
            and self._start is not None
        ):
            self._art = self._gesture_base.copy()
            start_x, start_y = self._start
            if self._tool == "line":
                self._art.draw_line(start_x, start_y, x, y, self._color)
            else:
                left, right = sorted((start_x, x))
                top, bottom = sorted((start_y, y))
                self._art.draw_rectangle(
                    left, top, right - left + 1, bottom - top + 1, self._color
                )
        self._display_cache = None
        self.update()
        self.document_changed.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish the active drawing gesture."""
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._finish_gesture()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Adjust zoom directly with the mouse wheel."""
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        direction = 1 if delta > 0 else -1
        self.set_zoom(self._zoom + direction)
        event.accept()

    def _paint_color(self) -> int:
        """Return the active tool's paint color."""
        return self._background if self._tool == "eraser" else self._color

    def _pixel_at(self, point: QPoint) -> tuple[int, int] | None:
        """Convert widget coordinates into pixel coordinates."""
        x = point.x() // self._zoom
        y = point.y() // self._zoom
        if self._art.contains(x, y):
            return x, y
        return None

    def _push_undo(self) -> None:
        """Save the current state for undo."""
        self._undo.append(self._art.copy())
        if len(self._undo) > 64:
            self._undo.pop(0)
        self._redo.clear()

    def _finish_gesture(self) -> None:
        """Finish drawing and emit one update."""
        self._drawing = False
        self._start = None
        self._last = None
        self._gesture_base = None
        self.update()
        self.document_changed.emit()

    def _update_size(self) -> None:
        """Update fixed size for scrolling."""
        self.setFixedSize(self.sizeHint())


def qcolor_from_rgb565(color: int) -> QColor:
    """Convert one RGB565 integer into QColor."""
    red, green, blue = rgb565_to_rgb(color)
    return QColor(red, green, blue)


def pixel_art_image(
    art: PixelArt,
    transparent: bool = True,
    checker: bool = False,
) -> QImage:
    """Convert pixel art into an unscaled Qt image."""
    image = QImage(art.width, art.height, QImage.Format.Format_ARGB32)
    for y in range(art.height):
        for x in range(art.width):
            color = art.pixel(x, y)
            if color is None and checker:
                shade = 0xD0 if (x + y) & 1 else 0xB8
                image.setPixelColor(x, y, QColor(shade, shade, shade))
            elif color is None and transparent:
                image.setPixelColor(x, y, QColor(0, 0, 0, 0))
            elif color is None:
                image.setPixelColor(x, y, QColor(0, 0, 0))
            else:
                image.setPixelColor(x, y, qcolor_from_rgb565(color))
    return image
