"""Tests for the reusable local personal asset library."""

# ruff: noqa: E402

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.asset_library import AssetLibrary, LibraryAsset
from pico_graphics_editor.model import PixelArt


class AssetLibraryTests(unittest.TestCase):
    """Verify portable records and safe persistence across projects."""

    def test_add_reload_rename_and_remove_static_asset(self) -> None:
        """Keep visible black, transparency, origin, and stable identity."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "asset-library.json"
            library = AssetLibrary(path)
            art = PixelArt(2, 2, -1, 3)
            art.set_pixel(0, 0, 0x0000)
            art.set_pixel(1, 1, 0xF800)
            stored = library.add("Badge", [art])
            self.assertTrue(path.is_file())
            reloaded = AssetLibrary(path).asset(stored.id)
            self.assertEqual(reloaded.pixel_frames()[0], art)
            self.assertEqual(reloaded.fingerprint, stored.fingerprint)
            renamed = library.rename(stored.id, "Status Badge")
            self.assertEqual(renamed.id, stored.id)
            self.assertEqual(library.assets()[0].name, "Status Badge")
            self.assertTrue(library.remove(stored.id))
            self.assertFalse(library.remove(stored.id))
            self.assertEqual(library.assets(), ())

    def test_animation_frames_and_durations_round_trip(self) -> None:
        """Store one animation as a reusable asset rather than separate files."""
        with tempfile.TemporaryDirectory() as folder:
            library = AssetLibrary(Path(folder) / "library.json")
            first = PixelArt(2, 1)
            first.set_pixel(0, 0, 0x07E0)
            second = PixelArt(2, 1)
            second.set_pixel(1, 0, 0x001F)
            stored = library.add("Spinner", [first, second], [100, 200])
            self.assertEqual(stored.pixel_frames(), (first, second))
            self.assertEqual(stored.durations, (100, 200))

    def test_batch_add_is_one_validated_library_transaction(self) -> None:
        """Write all recovered assets together and leave no partial import."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "library.json"
            library = AssetLibrary(path)
            first = PixelArt(2, 1, -1, 2)
            first.set_pixel(0, 0, 0x0000)
            second = PixelArt(1, 2)
            second.set_pixel(0, 1, 0x07E0)
            stored = library.add_many(
                (
                    ("First", (first,), None),
                    ("Second", (second,), None),
                )
            )
            self.assertEqual(len(stored), 2)
            self.assertEqual(
                {asset.name for asset in library.assets()}, {"First", "Second"}
            )

            before = path.read_bytes()
            incompatible = PixelArt(3, 1)
            with self.assertRaisesRegex(ValueError, "dimensions"):
                library.add_many(
                    (
                        ("Valid until commit", (first,), None),
                        ("Broken animation", (first, incompatible), (100, 100)),
                    )
                )
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(library.assets()), 2)

    def test_invalid_or_future_library_is_rejected_without_rewrite(self) -> None:
        """Do not guess at unknown library formats or damaged fingerprints."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "library.json"
            path.write_text(
                json.dumps({"format_version": 99, "assets": []}), encoding="utf-8"
            )
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                AssetLibrary(path).assets()
            self.assertEqual(path.read_bytes(), before)

    def test_empty_library_does_not_create_a_file(self) -> None:
        """Keep inspection read-only until the first explicit library write."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "library.json"
            self.assertEqual(AssetLibrary(path).assets(), ())
            self.assertFalse(path.exists())

    def test_duplicate_and_replace_preserve_expected_identities(self) -> None:
        """Clone IDs deliberately and replace pixels without breaking identity."""
        with tempfile.TemporaryDirectory() as folder:
            library = AssetLibrary(Path(folder) / "library.json")
            first = PixelArt(2, 1)
            first.set_pixel(0, 0, 0xF800)
            stored = library.add("Badge", [first])
            duplicate = library.duplicate(stored.id)
            self.assertNotEqual(duplicate.id, stored.id)
            self.assertEqual(duplicate.name, "Badge Copy")
            second = PixelArt(3, 1)
            second.set_pixel(2, 0, 0x07E0)
            replacement = library.replace(stored.id, [second])
            self.assertEqual(replacement.id, stored.id)
            self.assertEqual(replacement.name, "Badge")
            self.assertEqual(replacement.pixel_frames()[0], second)

    def test_display_names_are_disambiguated_case_insensitively(self) -> None:
        """Keep catalogue cards distinguishable when users reuse a visible name."""
        with tempfile.TemporaryDirectory() as folder:
            library = AssetLibrary(Path(folder) / "library.json")
            art = PixelArt(1, 1)

            first = library.add("Badge", [art])
            second = library.add("badge", [art])
            third = library.add("Badge", [art])
            renamed = library.rename(third.id, "BADGE")

            self.assertEqual(first.name, "Badge")
            self.assertEqual(second.name, "badge 2")
            self.assertEqual(third.name, "Badge 3")
            self.assertEqual(renamed.name, "BADGE 3")

    def test_reserved_builtin_names_receive_the_next_available_number(self) -> None:
        """Keep personal imports distinct from read-only standard catalogue names."""
        with tempfile.TemporaryDirectory() as folder:
            library = AssetLibrary(
                Path(folder) / "library.json", reserved_names=("Home",)
            )
            art = PixelArt(1, 1)

            first = library.add("Home", [art])
            second = library.add("home", [art])
            third = library.add("Home 2", [art])

            self.assertEqual(first.name, "Home 2")
            self.assertEqual(second.name, "home 3")
            self.assertEqual(third.name, "Home 4")
            self.assertEqual(
                len({record.name.casefold() for record in library.assets()}), 3
            )

    def test_load_and_restore_reject_duplicate_names_and_reserved_ids(self) -> None:
        """Enforce catalogue identity even for snapshots and external file edits."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "library.json"
            art = PixelArt(1, 1)
            first = LibraryAsset.from_frames("library_first", "Badge", (art,))
            duplicate = LibraryAsset.from_frames("library_second", " badge ", (art,))
            path.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "assets": [first.to_dict(), duplicate.to_dict()],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "already in use"):
                AssetLibrary(path).assets()

            configured = AssetLibrary(
                Path(folder) / "configured.json",
                reserved_names=("Home",),
                reserved_ids=("builtin_icon_home",),
            )
            reserved_id = LibraryAsset.from_frames(
                "builtin_icon_home", "Personal House", (art,)
            )
            with self.assertRaisesRegex(ValueError, "conflicts with built-in ID"):
                configured.restore_snapshot((reserved_id,))
            self.assertFalse(configured.path.exists())

            configured.path.write_text(
                json.dumps(
                    {"format_version": 1, "assets": [reserved_id.to_dict()]}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "conflicts with built-in ID"):
                configured.assets()

    def test_remove_many_is_one_complete_library_update(self) -> None:
        """Delete a file-manager selection without leaving a partial batch."""
        with tempfile.TemporaryDirectory() as folder:
            library = AssetLibrary(Path(folder) / "library.json")
            art = PixelArt(1, 1)
            first = library.add("First", [art])
            second = library.add("Second", [art])
            kept = library.add("Kept", [art])

            removed = library.remove_many((first.id, second.id, "missing"))

            self.assertEqual({asset.id for asset in removed}, {first.id, second.id})
            self.assertEqual(library.assets(), (kept,))

    def test_complete_snapshot_can_be_restored_atomically(self) -> None:
        """Support session undo without reconstructing IDs or pixel metadata."""
        with tempfile.TemporaryDirectory() as folder:
            library = AssetLibrary(Path(folder) / "library.json")
            art = PixelArt(2, 2, -2, 4)
            art.set_pixel(0, 0, 0x0000)
            stored = library.add("Recoverable", [art])
            snapshot = library.assets()
            library.remove(stored.id)
            self.assertEqual(library.assets(), ())

            restored = library.restore_snapshot(snapshot)

            self.assertEqual(restored, snapshot)
            self.assertEqual(restored[0].pixel_frames()[0], art)

    def test_validated_snapshot_is_cached_until_the_file_changes(self) -> None:
        """Avoid reparsing unchanged pixels but detect atomic external updates."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "library.json"
            writer = AssetLibrary(path)
            art = PixelArt(4, 4)
            art.set_pixel(1, 1, 0xF800)
            stored = writer.add("Badge", [art])
            reader = AssetLibrary(path)

            with patch(
                "pico_graphics_editor.asset_library.json.loads",
                wraps=json.loads,
            ) as loads:
                self.assertEqual(reader.assets()[0].name, "Badge")
                self.assertEqual(loads.call_count, 1)
                self.assertEqual(reader.assets()[0].name, "Badge")
                self.assertEqual(loads.call_count, 1)
                writer.rename(stored.id, "Updated Badge")
                self.assertEqual(reader.assets()[0].name, "Updated Badge")
                self.assertEqual(loads.call_count, 2)


if __name__ == "__main__":
    unittest.main()
