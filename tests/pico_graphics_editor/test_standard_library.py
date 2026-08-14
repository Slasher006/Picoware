"""Tests for the built-in beginner icon collection."""

# ruff: noqa: E402

import sys
import unittest
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.standard_library import (
    BACKGROUND_DESIGNS,
    BUTTON_DESIGNS,
    STANDARD_ASSET_NAMES,
    STANDARD_ICON_NAMES,
    THEME_NAMES,
    THEMED_ASSET_METADATA,
    THEMED_ICON_NAMES,
    WIDGET_DESIGNS,
    is_standard_asset_id,
    standard_asset_metadata,
    standard_library_assets,
)


class StandardLibraryTests(unittest.TestCase):
    """Keep the starter library stable, useful, and lossless."""

    def test_standard_library_contains_starter_icons_and_250_themed_assets(self) -> None:
        """Ship the original set plus exactly 50 assets for all five themes."""
        assets = standard_library_assets()

        self.assertEqual(len(assets), 300)
        self.assertEqual(tuple(asset.name for asset in assets), STANDARD_ASSET_NAMES)
        self.assertEqual(tuple(asset.name for asset in assets[:50]), STANDARD_ICON_NAMES)
        self.assertEqual(len({asset.id for asset in assets}), 300)
        self.assertEqual(len({asset.fingerprint for asset in assets}), 300)
        self.assertTrue(all(is_standard_asset_id(asset.id) for asset in assets))

    def test_standard_icons_are_nonempty_transparent_rgb565_assets(self) -> None:
        """Provide editable pixel masters without a baked background."""
        for asset in standard_library_assets()[:50]:
            self.assertEqual((asset.width, asset.height), (16, 16))
            pixels = asset.frames[0]
            self.assertTrue(any(pixel is not None for pixel in pixels), asset.name)
            self.assertTrue(any(pixel is None for pixel in pixels), asset.name)
            self.assertTrue(
                all(pixel is None or 0 <= pixel <= 0xFFFF for pixel in pixels),
                asset.name,
            )

    def test_every_theme_has_20_icons_12_buttons_12_widgets_and_6_backgrounds(self) -> None:
        """Keep the promised 50-item theme systems complete and balanced."""
        self.assertEqual(len(THEMED_ICON_NAMES), 20)
        self.assertEqual(len(BUTTON_DESIGNS), 12)
        self.assertEqual(len(WIDGET_DESIGNS), 12)
        self.assertEqual(len(BACKGROUND_DESIGNS), 6)
        for theme, unused_theme_name in THEME_NAMES:
            themed = [item for item in THEMED_ASSET_METADATA if item.theme == theme]
            self.assertEqual(len(themed), 50)
            self.assertEqual(
                {kind: sum(item.kind == kind for item in themed) for kind in {
                    "icon", "button", "widget", "background"
                }},
                {"icon": 20, "button": 12, "widget": 12, "background": 6},
            )

    def test_backgrounds_cover_every_builtin_device_resolution(self) -> None:
        """Provide opaque native-size masters instead of generic thumbnails."""
        assets = {asset.id: asset for asset in standard_library_assets()}
        expected_profiles = {
            "PicoCalc 320x320",
            "Cardputer 240x135",
            "Flipper Zero 128x64",
            "Round display 240x240",
        }
        for theme, unused_theme_name in THEME_NAMES:
            backgrounds = [
                item
                for item in THEMED_ASSET_METADATA
                if item.theme == theme and item.kind == "background"
            ]
            self.assertEqual({item.device_profile for item in backgrounds}, expected_profiles)
            for metadata in backgrounds:
                asset = assets[metadata.asset_id]
                self.assertEqual((asset.width, asset.height), (metadata.width, metadata.height))
                self.assertTrue(all(pixel is not None for pixel in asset.frames[0]))
                self.assertEqual(standard_asset_metadata(asset.id), metadata)


if __name__ == "__main__":
    unittest.main()
