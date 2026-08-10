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

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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

    def test_image_preserves_transparency(self) -> None:
        """Export empty pixels with zero alpha."""
        image = pixel_art_image(self.canvas.art())
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_checker_image_is_opaque(self) -> None:
        """Render transparent pixels as alternating canvas shades."""
        image = pixel_art_image(self.canvas.art(), transparent=False, checker=True)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 255)
        self.assertNotEqual(image.pixelColor(0, 0), image.pixelColor(1, 0))


if __name__ == "__main__":
    unittest.main()
