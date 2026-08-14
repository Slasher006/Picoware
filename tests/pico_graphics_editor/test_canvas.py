"""Tests for the Qt mouse pixel canvas."""

# ruff: noqa: E402

import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from pico_graphics_editor.canvas import PixelCanvas, pixel_art_image
from pico_graphics_editor.model import PixelArt


class CanvasTests(unittest.TestCase):
    """Verify mouse painting and undo behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the shared offscreen Qt application."""
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """Create one visible test canvas."""
        self.canvas = PixelCanvas()
        self.canvas.set_art(PixelArt(8, 8))
        self.canvas.set_zoom(10)
        self.canvas.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        """Close the test canvas."""
        self.canvas.close()

    def test_mouse_pencil_and_undo(self) -> None:
        """Paint one pixel and restore transparency."""
        self.canvas.set_color(0xF800)
        QTest.mouseClick(
            self.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(25, 35),
        )
        self.assertEqual(self.canvas.art().pixel(2, 3), 0xF800)
        self.canvas.undo()
        self.assertIsNone(self.canvas.art().pixel(2, 3))

    def test_large_pencil_stroke_patches_cache_and_commits_once(self) -> None:
        """Avoid full-image cache and preview work for every pointer movement."""
        self.canvas.set_art(PixelArt(320, 320))
        self.canvas.set_zoom(2)
        self.canvas.set_color(0xF800)
        self.canvas.grab()
        display_cache = self.canvas._display_cache
        self.assertIsNotNone(display_cache)
        changes = QSignalSpy(self.canvas.document_changed)

        QTest.mousePress(
            self.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(20, 20),
        )
        for coordinate in (30, 40, 50, 60, 70):
            QTest.mouseMove(self.canvas, QPoint(coordinate, coordinate))
        self.assertEqual(changes.count(), 0)
        self.assertIs(self.canvas._display_cache, display_cache)
        QTest.mouseRelease(
            self.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(70, 70),
        )

        self.assertEqual(changes.count(), 1)
        self.assertEqual(self.canvas.art().pixel(10, 10), 0xF800)
        self.assertEqual(self.canvas.art().pixel(35, 35), 0xF800)
        self.canvas.undo()
        self.assertIsNone(self.canvas.art().pixel(10, 10))

    def test_managed_eraser_clears_to_transparency(self) -> None:
        """Erase managed pixels without painting a replacement color."""
        art = PixelArt(8, 8)
        art.set_pixel(2, 3, 0xF800)
        self.canvas.set_art(art)
        self.canvas.set_tool("eraser")
        self.canvas.set_transparent_eraser(True)
        QTest.mouseClick(
            self.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(25, 35),
        )
        self.assertIsNone(self.canvas.art().pixel(2, 3))

    def test_selection_cut_paste_and_undo(self) -> None:
        """Cut and restore a rectangular pixel selection."""
        art = PixelArt(8, 8)
        art.set_pixel(1, 1, 0xF800)
        art.set_pixel(2, 1, 0x07E0)
        self.canvas.set_art(art)
        self.canvas.select_rectangle(1, 1, 2, 1)
        self.assertTrue(self.canvas.cut_selection())
        self.assertIsNone(self.canvas.art().pixel(1, 1))
        self.assertTrue(self.canvas.paste_selection())
        self.assertEqual(self.canvas.art().pixel(1, 1), 0xF800)
        self.assertEqual(self.canvas.art().pixel(2, 1), 0x07E0)
        self.canvas.undo()
        self.assertIsNone(self.canvas.art().pixel(1, 1))

    def test_system_clipboard_moves_lossless_pixels_between_canvases(self) -> None:
        """Copy transparent RGB565 pixels between separate editor canvases."""
        art = PixelArt(3, 2)
        art.set_pixel(0, 0, 0xF800)
        art.set_pixel(2, 1, 0x07E0)
        self.canvas.set_art(art)
        self.canvas.select_all()
        self.assertTrue(self.canvas.copy_selection())
        other = PixelCanvas()
        other.set_art(PixelArt(4, 4))
        self.assertTrue(other.has_clipboard())
        self.assertTrue(other.paste_selection())
        self.assertEqual(other.art().pixel(0, 0), 0xF800)
        self.assertIsNone(other.art().pixel(1, 0))
        self.assertEqual(other.art().pixel(2, 1), 0x07E0)
        other.close()
        self.application.clipboard().clear()
        self.application.processEvents()

    def test_selection_drag_moves_pixels_as_one_edit(self) -> None:
        """Move selected pixels with a mouse drag and undo the gesture."""
        art = PixelArt(8, 8)
        art.set_pixel(1, 1, 0xF800)
        self.canvas.set_art(art)
        self.canvas.select_rectangle(1, 1, 1, 1)
        self.canvas.set_tool("select")
        QTest.mousePress(
            self.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(15, 15),
        )
        QTest.mouseMove(self.canvas, QPoint(35, 25))
        QTest.mouseRelease(
            self.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(35, 25),
        )
        self.assertIsNone(self.canvas.art().pixel(1, 1))
        self.assertEqual(self.canvas.art().pixel(3, 2), 0xF800)
        self.canvas.undo()
        self.assertEqual(self.canvas.art().pixel(1, 1), 0xF800)

    def test_selection_transforms_and_document_dimensions(self) -> None:
        """Flip, rotate, crop, resize, and scale pixel artwork."""
        art = PixelArt(4, 3)
        art.set_pixel(0, 0, 0xF800)
        art.set_pixel(1, 0, 0x07E0)
        self.canvas.set_art(art)
        self.canvas.select_rectangle(0, 0, 2, 1)
        self.canvas.flip_selection(True)
        self.assertEqual(self.canvas.art().pixel(0, 0), 0x07E0)
        self.assertEqual(self.canvas.art().pixel(1, 0), 0xF800)
        self.canvas.rotate_selection_clockwise()
        self.assertEqual(self.canvas.selection(), (0, 0, 1, 2))
        self.canvas.crop_to_selection()
        self.assertEqual((self.canvas.art().width, self.canvas.art().height), (1, 2))
        self.canvas.resize_canvas(3, 4, True)
        self.assertEqual((self.canvas.art().width, self.canvas.art().height), (3, 4))
        self.canvas.scale_artwork(6, 8)
        self.assertEqual((self.canvas.art().width, self.canvas.art().height), (6, 8))

    def test_image_preserves_transparency(self) -> None:
        """Export empty pixels with zero alpha."""
        image = pixel_art_image(self.canvas.art())
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_checker_image_is_opaque(self) -> None:
        """Render transparent pixels as alternating canvas shades."""
        image = pixel_art_image(self.canvas.art(), transparent=False, checker=True)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 255)
        self.assertNotEqual(image.pixelColor(0, 0), image.pixelColor(1, 0))

    def test_mouse_wheel_zooms_without_modifier(self) -> None:
        """Zoom the canvas from a plain mouse-wheel event."""
        event = QWheelEvent(
            QPointF(20, 20),
            QPointF(20, 20),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        self.canvas.wheelEvent(event)
        self.assertEqual(self.canvas.zoom(), 11)
        self.assertTrue(event.isAccepted())

    def test_middle_mouse_pans_scrollable_canvas(self) -> None:
        """Pan a zoomed canvas while holding the middle mouse button."""
        self.canvas.set_art(PixelArt(40, 40))
        self.canvas.set_zoom(10)
        scroll = QScrollArea()
        scroll.resize(220, 180)
        scroll.setWidget(self.canvas)
        scroll.show()
        self.application.processEvents()
        horizontal = scroll.horizontalScrollBar()
        vertical = scroll.verticalScrollBar()
        horizontal.setValue(100)
        vertical.setValue(100)
        before = (horizontal.value(), vertical.value())
        QTest.mousePress(
            self.canvas,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(150, 130),
        )
        QTest.mouseMove(self.canvas, QPoint(100, 80))
        QTest.mouseRelease(
            self.canvas,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(100, 80),
        )
        self.assertGreater(horizontal.value(), before[0])
        self.assertGreater(vertical.value(), before[1])
        scroll.takeWidget()
        scroll.close()

    def test_onion_frame_aligns_by_source_origin(self) -> None:
        """Align a previous frame without changing active pixels."""
        current = PixelArt(8, 8, 10, 10)
        current.set_pixel(2, 2, 0xF800)
        previous = PixelArt(4, 4, 11, 11)
        previous.set_pixel(1, 1, 0x07E0)
        self.canvas.set_art(current)
        self.canvas.set_onion_art(previous)
        self.canvas.grab()
        self.assertEqual(self.canvas.art().pixel(2, 2), 0xF800)


if __name__ == "__main__":
    unittest.main()
