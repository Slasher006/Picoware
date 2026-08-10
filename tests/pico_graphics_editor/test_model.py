"""Tests for pixel drawing and color conversion."""

# ruff: noqa: E402

import sys
import unittest
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.model import PixelArt, rgb565_to_rgb, rgb_to_rgb565


class PixelArtTests(unittest.TestCase):
    """Verify deterministic pixel operations."""

    def test_line_and_rectangle_drawing(self) -> None:
        """Draw basic shapes into expected pixels."""
        art = PixelArt(8, 8)
        art.draw_line(0, 0, 3, 3, 0xFFFF)
        art.draw_rectangle(4, 1, 3, 3, 0xF800, True)
        self.assertEqual(art.pixel(2, 2), 0xFFFF)
        self.assertEqual(art.pixel(5, 2), 0xF800)
        self.assertIsNone(art.pixel(0, 7))

    def test_flood_fill_stays_bounded(self) -> None:
        """Fill only one enclosed pixel region."""
        art = PixelArt(7, 7)
        art.draw_rectangle(1, 1, 5, 5, 0xFFFF)
        art.flood_fill(3, 3, 0x07E0)
        self.assertEqual(art.pixel(3, 3), 0x07E0)
        self.assertIsNone(art.pixel(0, 0))
        self.assertEqual(art.pixel(1, 1), 0xFFFF)

    def test_horizontal_runs_only_include_changes(self) -> None:
        """Compress adjacent changed pixels into one run."""
        baseline = PixelArt(6, 3)
        edited = baseline.copy()
        edited.set_pixel(1, 1, 0x001F)
        edited.set_pixel(2, 1, 0x001F)
        edited.set_pixel(4, 1, 0xF800)
        self.assertEqual(
            edited.horizontal_runs(baseline),
            [(1, 1, 2, 0x001F), (4, 1, 1, 0xF800)],
        )

    def test_rgb565_round_trip_primary_colors(self) -> None:
        """Preserve primary colors through RGB conversion."""
        for color in (0x0000, 0xFFFF, 0xF800, 0x07E0, 0x001F):
            self.assertEqual(rgb_to_rgb565(*rgb565_to_rgb(color)), color)


if __name__ == "__main__":
    unittest.main()
