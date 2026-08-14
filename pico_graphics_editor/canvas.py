"""Mouse-driven Qt pixel editing canvas."""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QWidget

from .model import PixelArt, rgb565_to_rgb
from .reference import image_to_pixel_art, prepare_reference_image


class PixelCanvas(QWidget):
    """Edit one pixel document with mouse tools."""

    color_picked = Signal(int)
    document_changed = Signal()
    cursor_changed = Signal(int, int, object)
    selection_changed = Signal(bool)
    zoom_changed = Signal(int)
    PIXEL_MIME_TYPE = "application/x-pico-graphics-pixels+json"

    def __init__(self, parent: QWidget | None = None):
        """Initialize the editable canvas."""
        super().__init__(parent)
        self._art = PixelArt(32, 32)
        self._tool = "pencil"
        self._color = 0xFFFF
        self._background = 0x0000
        self._erase_transparent = False
        self._zoom = 12
        self._show_grid = True
        self._undo: list[PixelArt] = []
        self._redo: list[PixelArt] = []
        self._drawing = False
        self._start: tuple[int, int] | None = None
        self._last: tuple[int, int] | None = None
        self._gesture_base: PixelArt | None = None
        self._selection: QRect | None = None
        self._selection_mode = ""
        self._selection_origin: QRect | None = None
        self._selection_pixels: PixelArt | None = None
        self._selection_move_changed = False
        self._clipboard: PixelArt | None = None
        self._panning = False
        self._pan_start = QPoint()
        self._pan_scroll = QPoint()
        self._display_cache: QImage | None = None
        self._checker_cache: QImage | None = None
        self._onion_art: PixelArt | None = None
        self._onion_cache: QImage | None = None
        self._reference_source: QImage | None = None
        self._reference_cache: QImage | None = None
        self._reference_opacity = 0.55
        self._reference_foreground = False
        self._reference_options: dict[str, object] = {
            "mode": "contain",
            "rotation": 0,
            "flip_horizontal": False,
            "flip_vertical": False,
            "scale_percent": 100,
            "offset_x": 0,
            "offset_y": 0,
        }
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
        self._selection = None
        self._selection_mode = ""
        self._selection_origin = None
        self._selection_pixels = None
        self._display_cache = None
        self._checker_cache = None
        self._onion_art = None
        self._onion_cache = None
        self._rebuild_reference_cache()
        self._update_size()
        self.update()
        self.selection_changed.emit(False)
        self.document_changed.emit()

    def apply_art(self, art: PixelArt) -> None:
        """Apply compatible pixel art as one undoable edit."""
        if (
            art.width != self._art.width
            or art.height != self._art.height
            or art.origin_x != self._art.origin_x
            or art.origin_y != self._art.origin_y
        ):
            raise ValueError("Pixel surfaces are not aligned")
        self._push_undo()
        self._art = art.copy()
        self._display_cache = None
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

    def set_transparent_eraser(self, enabled: bool) -> None:
        """Choose whether erasing clears pixels instead of painting a color."""
        self._erase_transparent = enabled

    def selection(self) -> tuple[int, int, int, int] | None:
        """Return the active pixel selection rectangle."""
        if self._selection is None:
            return None
        return (
            self._selection.x(),
            self._selection.y(),
            self._selection.width(),
            self._selection.height(),
        )

    def select_all(self) -> None:
        """Select the complete pixel document."""
        self._set_selection(QRect(0, 0, self._art.width, self._art.height))

    def select_rectangle(self, x: int, y: int, width: int, height: int) -> None:
        """Select a clipped document rectangle."""
        rectangle = QRect(x, y, width, height).intersected(
            QRect(0, 0, self._art.width, self._art.height)
        )
        self._set_selection(rectangle if not rectangle.isEmpty() else None)

    def clear_selection(self) -> None:
        """Remove the active pixel selection without changing pixels."""
        self._set_selection(None)

    def copy_selection(self) -> bool:
        """Copy selected pixels to the shared lossless and PNG clipboard."""
        if self._selection is None:
            return False
        self._clipboard = self._copy_rect(self._selection)
        application = QApplication.instance()
        if application is not None:
            payload = {
                "width": self._clipboard.width,
                "height": self._clipboard.height,
                "origin_x": self._clipboard.origin_x,
                "origin_y": self._clipboard.origin_y,
                "pixels": self._clipboard.pixels,
            }
            mime = QMimeData()
            mime.setData(
                self.PIXEL_MIME_TYPE,
                json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            )
            mime.setImageData(pixel_art_image(self._clipboard, checker=False))
            application.clipboard().setMimeData(mime)
        return True

    def has_clipboard(self) -> bool:
        """Return whether internal or system pixel clipboard data is available."""
        application = QApplication.instance()
        if application is None:
            return self._clipboard is not None
        mime = application.clipboard().mimeData()
        if mime is None:
            return self._clipboard is not None
        return bool(
            self._clipboard is not None
            or mime.hasFormat(self.PIXEL_MIME_TYPE)
            or mime.hasImage()
        )

    def cut_selection(self) -> bool:
        """Copy and clear the active pixel selection."""
        if not self.copy_selection():
            return False
        return self.delete_selection()

    def paste_selection(self) -> bool:
        """Paste lossless shared pixels, PNG pixels, or internal fallback data."""
        clipboard_art = self._clipboard_art()
        if clipboard_art is None:
            return False
        target_x = self._selection.x() if self._selection is not None else 0
        target_y = self._selection.y() if self._selection is not None else 0
        self._push_undo()
        for y in range(clipboard_art.height):
            for x in range(clipboard_art.width):
                self._art.set_pixel(
                    target_x + x,
                    target_y + y,
                    clipboard_art.pixel(x, y),
                )
        width = min(clipboard_art.width, self._art.width - target_x)
        height = min(clipboard_art.height, self._art.height - target_y)
        self._set_selection(QRect(target_x, target_y, width, height), emit=False)
        self._finish_edit()
        return True

    def _clipboard_art(self) -> PixelArt | None:
        """Decode the preferred shared clipboard representation."""
        application = QApplication.instance()
        if application is not None:
            mime = application.clipboard().mimeData()
            if mime is not None and mime.hasFormat(self.PIXEL_MIME_TYPE):
                try:
                    values = json.loads(bytes(mime.data(self.PIXEL_MIME_TYPE)))
                    return PixelArt(
                        int(values["width"]),
                        int(values["height"]),
                        int(values.get("origin_x", 0)),
                        int(values.get("origin_y", 0)),
                        values["pixels"],
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    pass
            if mime is not None and mime.hasImage():
                image = QImage(mime.imageData())
                if not image.isNull():
                    return image_to_pixel_art(image, image.width(), image.height(), 256)
        return self._clipboard.copy() if self._clipboard is not None else None

    def delete_selection(self) -> bool:
        """Clear selected pixels to transparency."""
        if self._selection is None:
            return False
        self._push_undo()
        self._clear_rect(self._art, self._selection)
        self._finish_edit()
        return True

    def flip_selection(self, horizontal: bool) -> None:
        """Flip the selection or complete artwork along one axis."""
        rectangle = self._operation_rect()
        source = self._copy_rect(rectangle)
        self._push_undo()
        for y in range(source.height):
            for x in range(source.width):
                source_x = source.width - 1 - x if horizontal else x
                source_y = y if horizontal else source.height - 1 - y
                self._art.set_pixel(
                    rectangle.x() + x,
                    rectangle.y() + y,
                    source.pixel(source_x, source_y),
                )
        self._finish_edit()

    def rotate_selection_clockwise(self) -> None:
        """Rotate the selection or complete artwork clockwise."""
        rectangle = self._operation_rect()
        source = self._copy_rect(rectangle)
        rotated = PixelArt(source.height, source.width)
        for y in range(source.height):
            for x in range(source.width):
                rotated.set_pixel(source.height - 1 - y, x, source.pixel(x, y))
        self._push_undo()
        if self._selection is None:
            rotated.origin_x = self._art.origin_x
            rotated.origin_y = self._art.origin_y
            self._replace_art(rotated)
            return
        self._clear_rect(self._art, rectangle)
        self._paste_art(rotated, rectangle.x(), rectangle.y())
        width = min(rotated.width, self._art.width - rectangle.x())
        height = min(rotated.height, self._art.height - rectangle.y())
        self._set_selection(
            QRect(rectangle.x(), rectangle.y(), width, height), emit=False
        )
        self._finish_edit()

    def crop_to_selection(self) -> bool:
        """Crop the document to its active selection."""
        if self._selection is None:
            return False
        cropped = self._copy_rect(self._selection)
        cropped.origin_x = 0
        cropped.origin_y = 0
        self._push_undo()
        self._selection = None
        self._replace_art(cropped)
        return True

    def resize_canvas(
        self, width: int, height: int, center_content: bool = False
    ) -> None:
        """Resize the canvas while preserving existing pixels."""
        width = max(1, min(320, width))
        height = max(1, min(320, height))
        resized = PixelArt(width, height)
        offset_x = (width - self._art.width) // 2 if center_content else 0
        offset_y = (height - self._art.height) // 2 if center_content else 0
        for y in range(self._art.height):
            for x in range(self._art.width):
                resized.set_pixel(offset_x + x, offset_y + y, self._art.pixel(x, y))
        self._push_undo()
        self._selection = None
        self._replace_art(resized)

    def scale_artwork(self, width: int, height: int) -> None:
        """Scale all artwork with nearest-neighbor pixel sampling."""
        width = max(1, min(320, width))
        height = max(1, min(320, height))
        scaled = PixelArt(width, height)
        for y in range(height):
            source_y = min(self._art.height - 1, y * self._art.height // height)
            for x in range(width):
                source_x = min(self._art.width - 1, x * self._art.width // width)
                scaled.set_pixel(x, y, self._art.pixel(source_x, source_y))
        self._push_undo()
        self._selection = None
        self._replace_art(scaled)

    def clear_art(self) -> None:
        """Clear the complete document to transparency."""
        self._push_undo()
        self._art = PixelArt(
            self._art.width,
            self._art.height,
            self._art.origin_x,
            self._art.origin_y,
        )
        self._finish_edit()

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

    def set_reference_image(self, image: QImage | None) -> None:
        """Set or clear the tracing reference image."""
        self._reference_source = None if image is None else image.copy()
        self._rebuild_reference_cache()
        self.update()

    def has_reference_image(self) -> bool:
        """Return whether a tracing reference is loaded."""
        return self._reference_source is not None

    def reference_source_image(self) -> QImage | None:
        """Return a copy of the untransformed tracing reference."""
        return None if self._reference_source is None else self._reference_source.copy()

    def set_reference_opacity(self, percent: int) -> None:
        """Set the tracing reference opacity percentage."""
        self._reference_opacity = max(0, min(100, percent)) / 100
        self.update()

    def set_reference_foreground(self, foreground: bool) -> None:
        """Place the reference above or below active pixels."""
        self._reference_foreground = foreground
        self.update()

    def set_reference_options(
        self,
        mode: str,
        rotation: int,
        flip_horizontal: bool,
        flip_vertical: bool,
        scale_percent: int,
        offset_x: int,
        offset_y: int,
    ) -> None:
        """Update reference placement and transformation options."""
        self._reference_options = {
            "mode": mode,
            "rotation": rotation,
            "flip_horizontal": flip_horizontal,
            "flip_vertical": flip_vertical,
            "scale_percent": scale_percent,
            "offset_x": offset_x,
            "offset_y": offset_y,
        }
        self._rebuild_reference_cache()
        self.update()

    def reference_art(self, color_count: int, dither: bool) -> PixelArt | None:
        """Convert the positioned reference into editable pixel art."""
        if self._reference_cache is None:
            return None
        art = image_to_pixel_art(
            self._reference_cache,
            self._art.width,
            self._art.height,
            color_count,
            dither,
        )
        art.origin_x = self._art.origin_x
        art.origin_y = self._art.origin_y
        return art

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
        self._selection = None
        self._replace_art(self._art, emit_selection=True)

    def redo(self) -> None:
        """Restore the next pixel state."""
        if not self._redo:
            return
        self._undo.append(self._art.copy())
        self._art = self._redo.pop()
        self._selection = None
        self._replace_art(self._art, emit_selection=True)

    def sizeHint(self) -> QSize:
        """Return the zoomed document size."""
        return QSize(self._art.width * self._zoom, self._art.height * self._zoom)

    def paintEvent(self, event) -> None:
        """Paint the scaled pixels and optional grid."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        cell = self._zoom
        if self._checker_cache is None:
            self._checker_cache = pixel_art_image(
                PixelArt(self._art.width, self._art.height),
                transparent=False,
                checker=True,
            )
        if self._display_cache is None:
            self._display_cache = pixel_art_image(self._art)
        target = QRect(0, 0, self._art.width * cell, self._art.height * cell)
        painter.drawImage(target, self._checker_cache)
        if self._reference_cache is not None and not self._reference_foreground:
            painter.setOpacity(self._reference_opacity)
            painter.drawImage(target, self._reference_cache)
            painter.setOpacity(1.0)
        painter.drawImage(
            target,
            self._display_cache,
        )
        if self._reference_cache is not None and self._reference_foreground:
            painter.setOpacity(self._reference_opacity)
            painter.drawImage(target, self._reference_cache)
            painter.setOpacity(1.0)
        if self._onion_cache is not None:
            painter.setOpacity(0.28)
            painter.drawImage(
                QRect(0, 0, self._art.width * cell, self._art.height * cell),
                self._onion_cache,
            )
            painter.setOpacity(1.0)
        if self._show_grid and cell >= 6:
            painter.setPen(QPen(QColor(0, 0, 0, 55), 1))
            exposed = event.rect()
            first_x = max(0, exposed.left() // cell)
            last_x = min(self._art.width, exposed.right() // cell + 1)
            first_y = max(0, exposed.top() // cell)
            last_y = min(self._art.height, exposed.bottom() // cell + 1)
            for x in range(first_x, last_x + 1):
                painter.drawLine(x * cell, 0, x * cell, self._art.height * cell)
            for y in range(first_y, last_y + 1):
                painter.drawLine(0, y * cell, self._art.width * cell, y * cell)
        if self._selection is not None:
            selection = QRect(
                self._selection.x() * cell,
                self._selection.y() * cell,
                self._selection.width() * cell,
                self._selection.height() * cell,
            )
            painter.setPen(QPen(QColor("#00aaff"), 2, Qt.PenStyle.DashLine))
            painter.drawRect(selection.adjusted(1, 1, -1, -1))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin drawing or pick a color."""
        if event.button() == Qt.MouseButton.MiddleButton:
            scroll_area = self._scroll_area()
            if scroll_area is not None:
                self._panning = True
                self._pan_start = event.globalPosition().toPoint()
                self._pan_scroll = QPoint(
                    scroll_area.horizontalScrollBar().value(),
                    scroll_area.verticalScrollBar().value(),
                )
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        point = self._pixel_at(event.position().toPoint())
        if point is None:
            return
        x, y = point
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        if self._tool == "picker":
            color = self._art.pixel(x, y)
            if color is not None:
                self.color_picked.emit(color)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._tool == "select":
            self._drawing = True
            self._start = point
            self._last = point
            if self._selection is not None and self._selection.contains(x, y):
                self._selection_mode = "move"
                self._gesture_base = self._art.copy()
                self._selection_origin = QRect(self._selection)
                self._selection_pixels = self._copy_rect(self._selection)
                self._selection_move_changed = False
            else:
                self._selection_mode = "marquee"
                self._set_selection(QRect(x, y, 1, 1))
            return
        self._push_undo()
        self._drawing = True
        self._start = point
        self._last = point
        self._gesture_base = (
            self._art.copy() if self._tool in {"line", "rectangle"} else None
        )
        if self._tool == "fill":
            self._art.flood_fill(x, y, self._color)
            self._display_cache = None
            self._finish_gesture()
        elif self._tool in {"pencil", "eraser"}:
            self._art.set_pixel(x, y, self._paint_color())
            self._refresh_display_rect(x, y, x, y)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Continue drawing and report cursor coordinates."""
        if self._panning:
            scroll_area = self._scroll_area()
            if scroll_area is not None:
                delta = event.globalPosition().toPoint() - self._pan_start
                scroll_area.horizontalScrollBar().setValue(
                    self._pan_scroll.x() - delta.x()
                )
                scroll_area.verticalScrollBar().setValue(
                    self._pan_scroll.y() - delta.y()
                )
            event.accept()
            return
        point = self._pixel_at(event.position().toPoint())
        if point is None:
            return
        x, y = point
        self.cursor_changed.emit(x, y, self._art.pixel(x, y))
        if not self._drawing or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._tool == "select" and self._start is not None:
            start_x, start_y = self._start
            if self._selection_mode == "marquee":
                left, right = sorted((start_x, x))
                top, bottom = sorted((start_y, y))
                self._set_selection(
                    QRect(left, top, right - left + 1, bottom - top + 1)
                )
            elif (
                self._selection_mode == "move"
                and self._gesture_base is not None
                and self._selection_pixels is not None
                and self._selection_origin is not None
            ):
                delta_x = x - start_x
                delta_y = y - start_y
                source_rect = self._selection_origin
                target_x = max(
                    0,
                    min(
                        self._art.width - source_rect.width(), source_rect.x() + delta_x
                    ),
                )
                target_y = max(
                    0,
                    min(
                        self._art.height - source_rect.height(),
                        source_rect.y() + delta_y,
                    ),
                )
                if (
                    target_x == source_rect.x()
                    and target_y == source_rect.y()
                    and not self._selection_move_changed
                ):
                    return
                if not self._selection_move_changed:
                    self._push_undo()
                    self._selection_move_changed = True
                self._art = self._gesture_base.copy()
                self._clear_rect(self._art, source_rect)
                self._paste_art(self._selection_pixels, target_x, target_y)
                self._set_selection(
                    QRect(
                        target_x,
                        target_y,
                        source_rect.width(),
                        source_rect.height(),
                    ),
                    emit=False,
                )
                self._last = point
                self._display_cache = None
                self.update()
            return
        if self._tool in {"pencil", "eraser"} and self._last is not None:
            last_x, last_y = self._last
            self._art.draw_line(last_x, last_y, x, y, self._paint_color())
            self._last = point
            self._refresh_display_rect(
                min(last_x, x),
                min(last_y, y),
                max(last_x, x),
                max(last_y, y),
            )
            return
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

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish the active drawing gesture."""
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            if self._tool == "select" and self._selection_mode == "marquee":
                self._drawing = False
                self._start = None
                self._last = None
                self._selection_mode = ""
                self.update()
                return
            if self._tool == "select" and not self._selection_move_changed:
                self._finish_gesture(emit_change=False)
                return
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

    def _paint_color(self) -> int | None:
        """Return the active tool's paint color."""
        if self._tool == "eraser" and self._erase_transparent:
            return None
        return self._background if self._tool == "eraser" else self._color

    def _refresh_display_rect(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> None:
        """Patch changed pixels into the display cache and repaint only that area."""
        left = max(0, left)
        top = max(0, top)
        right = min(self._art.width - 1, right)
        bottom = min(self._art.height - 1, bottom)
        if left > right or top > bottom:
            return
        if self._display_cache is None:
            self._display_cache = pixel_art_image(self._art)
        transparent = QColor(0, 0, 0, 0)
        for y in range(top, bottom + 1):
            row = y * self._art.width
            for x in range(left, right + 1):
                color = self._art.pixels[row + x]
                self._display_cache.setPixelColor(
                    x,
                    y,
                    transparent if color is None else qcolor_from_rgb565(color),
                )
        cell = self._zoom
        self.update(
            QRect(
                left * cell,
                top * cell,
                (right - left + 1) * cell,
                (bottom - top + 1) * cell,
            ).adjusted(-1, -1, 1, 1)
        )

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

    def _finish_gesture(self, emit_change: bool = True) -> None:
        """Finish drawing and emit one update."""
        self._drawing = False
        self._start = None
        self._last = None
        self._gesture_base = None
        self._selection_mode = ""
        self._selection_origin = None
        self._selection_pixels = None
        self._selection_move_changed = False
        self.update()
        if emit_change:
            self.document_changed.emit()

    def _operation_rect(self) -> QRect:
        """Return the selection or complete document rectangle."""
        return self._selection or QRect(0, 0, self._art.width, self._art.height)

    def _copy_rect(self, rectangle: QRect) -> PixelArt:
        """Copy one document rectangle into a normalized pixel surface."""
        copied = PixelArt(rectangle.width(), rectangle.height())
        for y in range(rectangle.height()):
            for x in range(rectangle.width()):
                copied.set_pixel(
                    x,
                    y,
                    self._art.pixel(rectangle.x() + x, rectangle.y() + y),
                )
        return copied

    def _clear_rect(self, art: PixelArt, rectangle: QRect) -> None:
        """Clear one rectangle on a pixel surface."""
        for y in range(rectangle.y(), rectangle.y() + rectangle.height()):
            for x in range(rectangle.x(), rectangle.x() + rectangle.width()):
                art.set_pixel(x, y, None)

    def _paste_art(self, source: PixelArt, target_x: int, target_y: int) -> None:
        """Paste all source pixels into the active document."""
        for y in range(source.height):
            for x in range(source.width):
                self._art.set_pixel(target_x + x, target_y + y, source.pixel(x, y))

    def _set_selection(self, rectangle: QRect | None, emit: bool = True) -> None:
        """Set and optionally announce the active selection rectangle."""
        self._selection = rectangle
        self.update()
        if emit:
            self.selection_changed.emit(rectangle is not None)

    def _finish_edit(self) -> None:
        """Refresh caches and announce one completed pixel edit."""
        self._display_cache = None
        self._checker_cache = None
        self.update()
        self.document_changed.emit()

    def _replace_art(self, art: PixelArt, emit_selection: bool = True) -> None:
        """Replace pixel dimensions while retaining undo history."""
        self._art = art.copy()
        self._display_cache = None
        self._checker_cache = None
        self._onion_art = None
        self._onion_cache = None
        self._rebuild_reference_cache()
        self._update_size()
        self.update()
        if emit_selection:
            self.selection_changed.emit(self._selection is not None)
        self.document_changed.emit()

    def _update_size(self) -> None:
        """Update fixed size for scrolling."""
        self.setFixedSize(self.sizeHint())

    def _scroll_area(self) -> QAbstractScrollArea | None:
        """Return the nearest scroll area containing the canvas."""
        ancestor = self.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QAbstractScrollArea):
                return ancestor
            ancestor = ancestor.parentWidget()
        return None

    def _rebuild_reference_cache(self) -> None:
        """Rebuild the transformed tracing reference."""
        if self._reference_source is None:
            self._reference_cache = None
            return
        self._reference_cache = prepare_reference_image(
            self._reference_source,
            self._art.width,
            self._art.height,
            **self._reference_options,
        )


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
