"""Tests for reference image conversion and frame splitting."""

# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtGui import QColor, QImage

from pico_graphics_editor.reference import (
    image_to_pixel_art,
    prepare_reference_image,
    read_image_frames_with_durations,
    split_sprite_sheet,
)


class ReferenceTests(unittest.TestCase):
    """Verify deterministic image preparation."""

    def test_reference_transform_fits_target(self) -> None:
        """Contain a wide reference inside a square canvas."""
        source = QImage(8, 4, QImage.Format.Format_ARGB32)
        source.fill(QColor("red"))
        result = prepare_reference_image(source, 8, 8, "contain")
        self.assertEqual(result.size().width(), 8)
        self.assertEqual(result.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(result.pixelColor(0, 2).red(), 255)

    def test_conversion_limits_rgb565_palette(self) -> None:
        """Reduce an image to the requested RGB565 color count."""
        source = QImage(4, 1, QImage.Format.Format_ARGB32)
        for x, color in enumerate(("red", "green", "blue", "white")):
            source.setPixelColor(x, 0, QColor(color))
        art = image_to_pixel_art(source, 4, 1, 2)
        self.assertLessEqual(len(art.used_colors()), 2)

    def test_sprite_sheet_splits_row_major(self) -> None:
        """Extract regular cells from a sprite sheet."""
        sheet = QImage(8, 4, QImage.Format.Format_ARGB32)
        sheet.fill(QColor("black"))
        frames = split_sprite_sheet(sheet, 4, 2)
        self.assertEqual(len(frames), 4)
        self.assertEqual((frames[0].width(), frames[0].height()), (4, 2))

    def test_animated_reader_preserves_variable_frame_delays(self) -> None:
        """Carry supported GIF or WebP timing into the library review contract."""
        images = []
        for color in (QColor("red"), QColor("green"), QColor("blue")):
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(color)
            images.append(image)

        class FakeReader:
            def __init__(self, unused_path: str):
                del unused_path
                self.index = 0

            def setAutoTransform(self, unused_enabled: bool) -> None:  # noqa: N802
                del unused_enabled

            def canRead(self) -> bool:  # noqa: N802
                return self.index < len(images)

            def read(self) -> QImage:
                return images[self.index]

            def nextImageDelay(self) -> int:  # noqa: N802
                return (80, 140, 260)[self.index]

            def jumpToNextImage(self) -> bool:  # noqa: N802
                self.index += 1
                return self.index < len(images)

        with mock_patch("pico_graphics_editor.reference.QImageReader", FakeReader):
            frames, durations = read_image_frames_with_durations("animation.gif")

        self.assertEqual(len(frames), 3)
        self.assertEqual(durations, (80, 140, 260))


if __name__ == "__main__":
    unittest.main()
