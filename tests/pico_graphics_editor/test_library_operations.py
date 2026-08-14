"""Tests for shared non-UI Asset Library operations."""

# ruff: noqa: E402

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.asset_library import LibraryAsset
from pico_graphics_editor.library_operations import (
    plan_png_exports,
    write_png_exports,
)
from pico_graphics_editor.model import PixelArt


class LibraryOperationTests(unittest.TestCase):
    """Keep batch exports collision-free and all-or-nothing."""

    def test_export_plan_avoids_existing_and_derived_frame_names(self) -> None:
        """Never overwrite an existing file or another output from the same batch."""
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            existing = destination / "A-frame-1.png"
            existing.write_bytes(b"keep")
            art = PixelArt(1, 1)
            animation = LibraryAsset.from_frames("one", "A", (art, art))
            static = LibraryAsset.from_frames("two", "A-frame-1", (art,))

            exports = plan_png_exports((animation, static), destination)
            write_png_exports(exports)

            names = [export.path.name for export in exports]
            self.assertEqual(len(names), len({name.casefold() for name in names}))
            self.assertNotIn(
                existing.name.casefold(), {name.casefold() for name in names}
            )
            self.assertEqual(existing.read_bytes(), b"keep")
            self.assertTrue(all(export.path.is_file() for export in exports))

    def test_failed_batch_export_keeps_no_partial_outputs(self) -> None:
        """Remove every newly published file when staging cannot complete."""
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            art = PixelArt(1, 1)
            records = (
                LibraryAsset.from_frames("one", "First", (art,)),
                LibraryAsset.from_frames("two", "Second", (art,)),
            )
            exports = plan_png_exports(records, destination)
            encoded = Mock()
            encoded.save.side_effect = (True, False)

            with patch(
                "pico_graphics_editor.library_operations.pixel_art_image",
                return_value=encoded,
            ):
                with self.assertRaisesRegex(OSError, "Second.png"):
                    write_png_exports(exports)

            self.assertEqual(tuple(destination.iterdir()), ())


if __name__ == "__main__":
    unittest.main()
