"""Tests for deterministic compact generated-asset encoding."""

# ruff: noqa: E402

import io
import sys
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.asset_codegen import (
    ASSET_FORMAT_VERSION,
    EncodedAsset,
    GeneratedAudioEntry,
    GeneratedAssetEntry,
    GeneratedRasterEntry,
    asset_fingerprint,
    canonical_asset_bytes,
    decode_asset_resource,
    encode_asset,
    generate_asset_resource,
    generate_assets_module,
    generate_individual_asset_resources,
    parse_asset_resource_project,
    parse_individual_resource_marker_project,
    reconstruct_asset,
    validate_asset,
)
from pico_graphics_editor.model import PixelArt


class AssetCodegenTests(unittest.TestCase):
    """Verify lossless, deterministic palette and rectangle records."""

    def test_fully_transparent_asset_has_no_visible_records(self) -> None:
        """Represent transparency without inventing a visible background color."""
        art = PixelArt(5, 3)
        encoded = encode_asset([art])
        self.assertEqual(encoded.palette, (None,))
        self.assertEqual(encoded.frames, ((),))
        self.assertEqual(reconstruct_asset(encoded), art)

    def test_visible_black_remains_distinct_from_transparency(self) -> None:
        """Reserve palette zero for alpha while retaining RGB565 black."""
        art = PixelArt(4, 2)
        art.set_pixel(0, 0, 0x0000)
        art.set_pixel(2, 0, 0xF800)
        art.set_pixel(0, 1, 0x0000)
        encoded = encode_asset([art])
        self.assertEqual(encoded.palette, (None, 0x0000, 0xF800))
        self.assertEqual(
            encoded.frames[0],
            (
                (0, 0, 1, 2, 1),
                (2, 0, 1, 1, 2),
            ),
        )
        self.assertEqual(reconstruct_asset(encoded), art)
        self.assertIsNone(reconstruct_asset(encoded).pixel(1, 0))

    def test_matching_horizontal_runs_merge_vertically(self) -> None:
        """Collapse one solid area into a single taller rectangle."""
        art = PixelArt(6, 5)
        art.draw_rectangle(1, 1, 4, 3, 0x07E0, True)
        encoded = encode_asset([art])
        self.assertEqual(encoded.frames[0], ((1, 1, 4, 3, 1),))
        self.assertEqual(reconstruct_asset(encoded), art)

    def test_nonmatching_rows_remain_separate_rectangles(self) -> None:
        """Merge only runs with identical X, width, and palette index."""
        art = PixelArt(4, 2)
        art.set_pixel(0, 0, 0xFFFF)
        art.set_pixel(1, 0, 0xFFFF)
        art.set_pixel(0, 1, 0xFFFF)
        art.set_pixel(2, 1, 0xFFFF)
        encoded = encode_asset([art])
        self.assertEqual(
            encoded.frames[0],
            (
                (0, 0, 2, 1, 1),
                (0, 1, 1, 1, 1),
                (2, 1, 1, 1, 1),
            ),
        )
        self.assertEqual(reconstruct_asset(encoded), art)

    def test_palette_uses_first_occurrence_across_ordered_frames(self) -> None:
        """Keep palette and animation frame order deterministic."""
        first = PixelArt(3, 1)
        first.set_pixel(0, 0, 0x07E0)
        first.set_pixel(2, 0, 0xF800)
        second = PixelArt(3, 1)
        second.set_pixel(0, 0, 0x0000)
        second.set_pixel(1, 0, 0x07E0)
        second.set_pixel(2, 0, 0xF800)
        encoded = encode_asset([first, second], [120, 240])
        self.assertEqual(encoded.palette, (None, 0x07E0, 0xF800, 0x0000))
        self.assertEqual(encoded.durations, (120, 240))
        self.assertEqual(reconstruct_asset(encoded, 0), first)
        self.assertEqual(reconstruct_asset(encoded, 1), second)

    def test_negative_origin_is_preserved_by_round_trip(self) -> None:
        """Keep signed placement metadata separate from local rectangles."""
        art = PixelArt(3, 2, -4, -7)
        art.set_pixel(1, 1, 0x001F)
        encoded = encode_asset([art])
        self.assertEqual((encoded.origin_x, encoded.origin_y), (-4, -7))
        self.assertEqual(reconstruct_asset(encoded), art)

    def test_repeated_encoding_and_fingerprints_are_deterministic(self) -> None:
        """Produce byte-identical canonical input for unchanged pixels."""
        art = PixelArt(4, 3)
        art.draw_rectangle(0, 0, 2, 2, 0xF81F, True)
        art.set_pixel(3, 2, 0x0000)
        first = encode_asset([art], [200])
        second = encode_asset([art.copy()], [200])
        self.assertEqual(first, second)
        self.assertEqual(canonical_asset_bytes(first), canonical_asset_bytes(second))
        self.assertEqual(asset_fingerprint(first), asset_fingerprint(second))
        moved = encode_asset(
            [PixelArt(art.width, art.height, 1, 0, art.pixels.copy())],
            [200],
        )
        self.assertNotEqual(asset_fingerprint(first), asset_fingerprint(moved))

    def test_source_frames_must_be_present_and_compatible(self) -> None:
        """Reject empty, oversized, mismatched, or non-PixelArt sources."""
        with self.assertRaisesRegex(ValueError, "At least one"):
            encode_asset([])
        with self.assertRaisesRegex(TypeError, "PixelArt"):
            encode_asset([object()])
        with self.assertRaisesRegex(ValueError, "between 1 and 320"):
            encode_asset([PixelArt(321, 1)])
        with self.assertRaisesRegex(ValueError, "share dimensions and origin"):
            encode_asset([PixelArt(2, 2), PixelArt(3, 2)])
        with self.assertRaisesRegex(ValueError, "share dimensions and origin"):
            encode_asset([PixelArt(2, 2), PixelArt(2, 2, 1, 0)])

    def test_source_colors_must_be_valid_rgb565_values(self) -> None:
        """Reject booleans, negative values, and values above RGB565."""
        for invalid in (True, -1, 0x10000, "red"):
            with self.subTest(invalid=invalid):
                art = PixelArt(1, 1)
                art.set_pixel(0, 0, invalid)
                with self.assertRaisesRegex(ValueError, "RGB565"):
                    encode_asset([art])

    def test_duration_metadata_is_optional_but_strict(self) -> None:
        """Require one positive integer duration per frame when supplied."""
        frames = [PixelArt(1, 1), PixelArt(1, 1)]
        self.assertEqual(encode_asset(frames).durations, ())
        for durations in ([100], [100, 0], [100, True], "100"):
            with self.subTest(durations=durations):
                with self.assertRaises((TypeError, ValueError)):
                    encode_asset(frames, durations)

    def test_validate_rejects_invalid_palette_records(self) -> None:
        """Enforce transparent-first, unique, used RGB565 palette entries."""
        valid = encode_asset([self._one_pixel_art()])
        invalid_palettes = (
            (),
            (0xF800,),
            (None, None),
            (None, 0xF800, 0xF800),
            (None, -1),
            (None, 0x10000),
            (None, 0xF800, 0x07E0),
        )
        for palette in invalid_palettes:
            with self.subTest(palette=palette):
                with self.assertRaises(ValueError):
                    validate_asset(replace(valid, palette=palette))

    def test_validate_rejects_invalid_rectangles(self) -> None:
        """Reject malformed, out-of-bounds, overlapping, or unordered records."""
        valid = encode_asset([self._one_pixel_art()])
        invalid_frames = (
            (((0, 0, 1, 1),),),
            (((0, 0, 1, 1, True),),),
            (((-1, 0, 1, 1, 1),),),
            (((0, 0, 3, 1, 1),),),
            (((0, 0, 1, 1, 0),),),
            (((0, 0, 1, 1, 2),),),
            (((0, 0, 1, 1, 1), (0, 0, 1, 1, 1)),),
        )
        for frames in invalid_frames:
            with self.subTest(frames=frames):
                with self.assertRaises(ValueError):
                    validate_asset(replace(valid, frames=frames))

        unordered = EncodedAsset(
            2,
            2,
            0,
            0,
            (None, 0xF800),
            (((1, 1, 1, 1, 1), (0, 0, 1, 1, 1)),),
        )
        with self.assertRaisesRegex(ValueError, "canonical ordering"):
            validate_asset(unordered)

    def test_validate_rejects_invalid_format_and_frame_container(self) -> None:
        """Require the exact v1 record and immutable frame containers."""
        valid = encode_asset([self._one_pixel_art()])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_asset(replace(valid, format_version=ASSET_FORMAT_VERSION + 1))
        with self.assertRaisesRegex(ValueError, "at least one frame"):
            validate_asset(replace(valid, frames=()))
        with self.assertRaisesRegex(ValueError, "frames must be tuples"):
            validate_asset(replace(valid, frames=([valid.frames[0][0]],)))

    def test_reconstruction_rejects_an_invalid_frame_index(self) -> None:
        """Keep the proof helper strict rather than hiding a bad test request."""
        encoded = encode_asset([self._one_pixel_art()])
        for frame in (-1, 1, True):
            with self.subTest(frame=frame):
                with self.assertRaises(IndexError):
                    reconstruct_asset(encoded, frame)

    def test_generated_module_has_exact_header_and_stable_asset_order(self) -> None:
        """Emit editor ownership metadata and sort records by stable ID."""
        red = self._one_pixel_art()
        black = PixelArt(1, 1)
        black.set_pixel(0, 0, 0x0000)
        entries = [
            GeneratedAssetEntry("asset-z", "Black", encode_asset([black])),
            GeneratedAssetEntry("asset-a", "Red", encode_asset([red])),
        ]
        generated = generate_assets_module("project-1", "8.0", entries)
        self.assertTrue(
            generated.startswith(
                "# @picoware-generated structure=1\n"
                "# @picoware-generated role=assets\n"
                "# @picoware-generated project=project-1\n"
                "# @picoware-generator version=8.0\n"
                "# This file is editor-owned. Regenerate it instead of editing it manually.\n"
            )
        )
        self.assertLess(generated.index("'asset-a'"), generated.index("'asset-z'"))
        self.assertIn("0x0000", generated)
        compile(generated, "generated_assets.py", "exec")
        self.assertEqual(
            generated,
            generate_assets_module("project-1", "8.0", list(reversed(entries))),
        )

    def test_generated_runtime_draws_frames_with_origin_and_scale(self) -> None:
        """Prove the public runtime API against a recording draw target."""
        first = PixelArt(2, 2, -1, 2)
        first.draw_rectangle(0, 0, 2, 1, 0xF800, True)
        second = PixelArt(2, 2, -1, 2)
        second.set_pixel(1, 1, 0x07E0)
        source = generate_assets_module(
            "project-1",
            "8.0",
            [
                GeneratedAssetEntry(
                    "asset-icon",
                    "Icon",
                    encode_asset([first, second], [100, 200]),
                )
            ],
        )
        namespace: dict[str, object] = {}
        exec(compile(source, "generated_assets.py", "exec"), namespace)

        class RecordingDraw:
            def __init__(self) -> None:
                self.calls: list[tuple[int, int, int, int, int]] = []

            def _fill_rectangle(self, *args: int) -> None:
                self.calls.append(args)

        draw = RecordingDraw()
        self.assertTrue(namespace["has_asset"]("asset-icon"))
        self.assertFalse(namespace["has_asset"]("missing"))
        self.assertEqual(namespace["asset_size"]("asset-icon"), (2, 2))
        self.assertIsNone(namespace["asset_size"]("missing"))
        self.assertEqual(namespace["frame_count"]("asset-icon"), 2)
        self.assertEqual(namespace["frame_count"]("missing"), 0)
        self.assertTrue(namespace["draw_asset"](draw, "asset-icon", 10, 20, 1, 3))
        self.assertEqual(draw.calls, [(10, 29, 3, 3, 0x07E0)])
        draw.calls.clear()
        self.assertTrue(namespace["draw_asset"](draw, "asset-icon", 10, 20, 99, 0))
        self.assertEqual(draw.calls, [(9, 22, 2, 1, 0xF800)])
        self.assertFalse(namespace["draw_asset"](draw, "missing", 0, 0))

    def test_generated_runtime_accepts_a_fully_transparent_asset(self) -> None:
        """Keep transparent assets present without issuing draw calls."""
        source = generate_assets_module(
            "project-1",
            "8.0",
            [GeneratedAssetEntry("blank", "Blank", encode_asset([PixelArt(2, 2)]))],
        )
        namespace: dict[str, object] = {}
        exec(source, namespace)

        class RecordingDraw:
            def __init__(self) -> None:
                self.calls: list[object] = []

            def _fill_rectangle(self, *args: object) -> None:
                self.calls.append(args)

        draw = RecordingDraw()
        self.assertTrue(namespace["draw_asset"](draw, "blank", 0, 0))
        self.assertEqual(draw.calls, [])

    def test_streamed_resource_preserves_rgb565_black_alpha_and_scale(self) -> None:
        """Decode bounded rows without confusing transparent and black pixels."""
        art = PixelArt(4, 2, -1, 2)
        art.set_pixel(0, 0, 0x0000)
        art.set_pixel(1, 0, 0xF800)
        art.set_pixel(3, 0, 0x07E0)
        resource = generate_asset_resource(
            "project-1",
            "8.0",
            [GeneratedAssetEntry("asset-icon", "Icon", encode_asset([art]))],
        )
        self.assertEqual(parse_asset_resource_project(resource.data), "project-1")
        namespace: dict[str, object] = {
            "__file__": "/apps/demo/generated_assets.py",
            "open": lambda unused_path, unused_mode: io.BytesIO(resource.data),
        }
        exec(resource.module_source, namespace)

        class RecordingDraw:
            def __init__(self) -> None:
                self.blits: list[tuple[object, ...]] = []
                self.fills: list[tuple[object, ...]] = []

            def _bytearray(self, x, y, width, height, data, invert) -> None:
                self.blits.append((x, y, width, height, bytes(data), invert))

            def _fill_rectangle(self, *args: object) -> None:
                self.fills.append(args)

        draw = RecordingDraw()
        self.assertTrue(namespace["draw_asset"](draw, "asset-icon", 10, 20))
        self.assertEqual(
            draw.blits,
            [
                (9, 22, 2, 1, b"\x00\x00\x00\xf8", False),
                (12, 22, 1, 1, b"\xe0\x07", False),
            ],
        )
        self.assertTrue(namespace["draw_asset"](draw, "asset-icon", 10, 20, 0, 2))
        self.assertIn((8, 24, 2, 2, 0x0000), draw.fills)
        self.assertIn((10, 24, 2, 2, 0xF800), draw.fills)
        self.assertIn((14, 24, 2, 2, 0x07E0), draw.fills)

    def test_pga3_resource_decodes_losslessly_for_library_recovery(self) -> None:
        """Recover names, origins, animation timing, black, and transparency."""
        first = PixelArt(3, 2, -4, 7)
        first.set_pixel(0, 0, 0x0000)
        first.set_pixel(2, 1, 0xF800)
        second = PixelArt(3, 2, -4, 7)
        second.set_pixel(1, 0, 0x07E0)
        resource = generate_asset_resource(
            "recovery-project",
            "8.0",
            [
                GeneratedRasterEntry(
                    "asset-animated",
                    "Animated Ässet",
                    (first, second),
                    (110, 220),
                )
            ],
        )
        decoded = decode_asset_resource(resource.data)
        self.assertEqual(resource.data[:4], b"PGA3")
        self.assertEqual(decoded.format_version, 3)
        self.assertEqual(decoded.project_id, "recovery-project")
        self.assertEqual(len(decoded.assets), 1)
        self.assertEqual(decoded.assets[0].asset_id, "asset-animated")
        self.assertEqual(decoded.assets[0].name, "Animated Ässet")
        self.assertEqual(decoded.assets[0].frames, (first, second))
        self.assertEqual(decoded.assets[0].durations, (110, 220))

    def test_pga_decoder_rejects_legacy_and_tampered_indexes(self) -> None:
        """Never guess missing PGA1 metadata or trust a corrupt PGA3 cross-link."""
        legacy = b"PGA1" + (7).to_bytes(2, "little") + b"project" + b"payload"
        with self.assertRaisesRegex(ValueError, "PGA1 cannot be imported"):
            decode_asset_resource(legacy)

        resource = generate_asset_resource(
            "safe-project",
            "8.0",
            [GeneratedRasterEntry("asset-safe", "Safe", (self._one_pixel_art(),))],
        )
        corrupt = bytearray(resource.data)
        index_start = 6 + len("safe-project") + 10
        corrupt[index_start] ^= 0x01
        with self.assertRaisesRegex(ValueError, "hash index"):
            decode_asset_resource(bytes(corrupt))
        with self.assertRaisesRegex(ValueError, "total-size|truncated"):
            decode_asset_resource(resource.data[:-1])

    def test_pga3_mixes_images_and_bounded_wav_streams(self) -> None:
        """Index WAV files beside images without embedding either in Python."""
        art = self._one_pixel_art()
        wav_data = self._wav_bytes(frame_count=5000)
        entries = [
            GeneratedRasterEntry("image-icon", "Icon", (art,)),
            GeneratedAudioEntry(
                "wav-click",
                "Click",
                wav_data,
                loop_start_ms=100,
                loop_end_ms=300,
            ),
        ]
        resource = generate_asset_resource("mixed-project", "8.0", entries)
        reversed_resource = generate_asset_resource(
            "mixed-project", "8.0", list(reversed(entries))
        )
        self.assertEqual(resource.data[:4], b"PGA3")
        self.assertEqual(resource.data, reversed_resource.data)
        self.assertEqual(resource.asset_count, 1)
        self.assertEqual(resource.audio_count, 1)
        self.assertEqual(resource.resource_count, 2)

        decoded = decode_asset_resource(resource.data)
        self.assertEqual(decoded.format_version, 3)
        self.assertEqual(decoded.assets[0].frames, (art,))
        self.assertEqual(len(decoded.audio_assets), 1)
        decoded_wav = decoded.audio_assets[0]
        self.assertEqual(decoded_wav.data, wav_data)
        self.assertEqual(decoded_wav.sample_rate, 8000)
        self.assertEqual(decoded_wav.channels, 1)
        self.assertEqual(decoded_wav.bits_per_sample, 8)
        self.assertEqual(decoded_wav.duration_ms, 625)
        self.assertEqual(
            (decoded_wav.loop_start_ms, decoded_wav.loop_end_ms), (100, 300)
        )

        namespace: dict[str, object] = {
            "__file__": "/apps/demo/generated_assets.py",
            "open": lambda unused_path, unused_mode: io.BytesIO(resource.data),
        }
        exec(resource.module_source, namespace)
        self.assertTrue(namespace["has_asset"]("image-icon"))
        self.assertFalse(namespace["has_asset"]("wav-click"))
        self.assertTrue(namespace["has_wav"]("wav-click"))
        self.assertFalse(namespace["has_wav"]("image-icon"))
        self.assertEqual(
            namespace["wav_info"]("wav-click"),
            (8000, 1, 8, 625, 100, 300, len(wav_data)),
        )
        self.assertIsNone(namespace["wav_path"]("wav-click"))
        self.assertEqual(
            namespace["read_wav_chunk"]("wav-click", 0, 9000),
            wav_data[:4096],
        )
        self.assertEqual(
            namespace["read_wav_chunk"]("wav-click", len(wav_data) - 5, 100),
            wav_data[-5:],
        )

        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            (folder / resource.resource_name).write_bytes(resource.data)
            filesystem_namespace: dict[str, object] = {
                "__file__": str(folder / "generated_assets.py")
            }
            exec(resource.module_source, filesystem_namespace)
            extracted = folder / "selected.wav"
            self.assertTrue(
                filesystem_namespace["extract_wav"]("wav-click", str(extracted))
            )
            self.assertEqual(extracted.read_bytes(), wav_data)
            self.assertFalse((folder / "selected.wav.pga-tmp").exists())

    def test_individual_mode_emits_replaceable_pga_images_and_wav_files(self) -> None:
        """Keep the runtime API stable while deploying one file per resource."""
        art = self._one_pixel_art()
        wav_data = self._wav_bytes(frame_count=400)
        resource = generate_individual_asset_resources(
            "loose-project",
            "8.0",
            (
                GeneratedRasterEntry("asset-icon", "Icon", (art,)),
                GeneratedAudioEntry("wav-click", "Click", wav_data),
            ),
        )
        files = dict(resource.files)
        self.assertEqual(resource.storage_mode, "individual")
        self.assertEqual(resource.resource_count, 2)
        self.assertEqual(
            set(files),
            {
                "generated_assets/_picoware_assets.pgl",
                "generated_assets/asset-icon.pga",
                "generated_assets/wav-click.wav",
            },
        )
        self.assertEqual(files["generated_assets/wav-click.wav"], wav_data)
        self.assertEqual(
            parse_individual_resource_marker_project(
                files["generated_assets/_picoware_assets.pgl"]
            ),
            "loose-project",
        )
        decoded_image = decode_asset_resource(files["generated_assets/asset-icon.pga"])
        self.assertEqual(decoded_image.assets[0].frames, (art,))

        with tempfile.TemporaryDirectory() as folder_name:
            package = Path(folder_name) / "demo"
            for name, content in resource.files:
                target = package / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            namespace: dict[str, object] = {
                "__file__": str(package / "generated_assets.py")
            }
            exec(resource.module_source, namespace)
            self.assertTrue(namespace["has_asset"]("asset-icon"))
            self.assertEqual(namespace["asset_size"]("asset-icon"), (2, 2))
            self.assertTrue(namespace["has_wav"]("wav-click"))
            self.assertEqual(
                namespace["wav_path"]("wav-click"),
                str(package / "generated_assets" / "wav-click.wav"),
            )
            self.assertEqual(
                namespace["read_wav_chunk"]("wav-click", 0, 64), wav_data[:64]
            )

    def test_individual_mode_requires_portable_resource_ids(self) -> None:
        """Prevent path traversal and ambiguous filenames without a runtime index."""
        for asset_id in ("../escape", "asset/name", "_hidden", "ümlaut"):
            with self.subTest(asset_id=asset_id):
                with self.assertRaisesRegex(ValueError, "Individual resource ID"):
                    generate_individual_asset_resources(
                        "loose-project",
                        "8.0",
                        (
                            GeneratedRasterEntry(
                                asset_id, "Bad", (self._one_pixel_art(),)
                            ),
                        ),
                    )
        with self.assertRaisesRegex(ValueError, "loop metadata"):
            generate_individual_asset_resources(
                "loose-project",
                "8.0",
                (
                    GeneratedAudioEntry(
                        "wav-loop",
                        "Loop",
                        self._wav_bytes(frame_count=8000),
                        loop_start_ms=100,
                        loop_end_ms=500,
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "case-insensitive"):
            generate_individual_asset_resources(
                "loose-project",
                "8.0",
                (
                    GeneratedRasterEntry("Asset", "First", (self._one_pixel_art(),)),
                    GeneratedRasterEntry("asset", "Second", (self._one_pixel_art(),)),
                ),
            )

    def test_pga3_rejects_non_wav_or_incomplete_audio(self) -> None:
        """Keep the typed audio contract strictly complete PCM RIFF/WAVE."""
        for invalid in (
            b"ID3\x04\x00\x00not-an-mp3",
            b"RIFF\x20\x00\x00\x00WAVEfmt ",
            self._wav_bytes()[:-1],
        ):
            with self.subTest(invalid=invalid[:12]):
                with self.assertRaisesRegex(ValueError, "WAV|RIFF"):
                    generate_asset_resource(
                        "audio-project",
                        "8.0",
                        [GeneratedAudioEntry("wav-invalid", "Invalid", invalid)],
                    )

    def test_pga2_images_remain_decodable_but_cannot_hold_wav(self) -> None:
        """Retain image migration while making PGA3 the generated default."""
        art = self._one_pixel_art()
        resource = generate_asset_resource(
            "legacy-project",
            "8.0",
            [GeneratedRasterEntry("asset-old", "Old", (art,))],
            format_version=2,
        )
        self.assertEqual(resource.data[:4], b"PGA2")
        decoded = decode_asset_resource(resource.data)
        self.assertEqual(decoded.format_version, 2)
        self.assertEqual(decoded.assets[0].frames, (art,))
        self.assertEqual(decoded.audio_assets, ())
        with self.assertRaisesRegex(ValueError, "PGA2 does not support WAV"):
            generate_asset_resource(
                "legacy-project",
                "8.0",
                [GeneratedAudioEntry("wav-no", "No", self._wav_bytes())],
                format_version=2,
            )

    def test_dense_full_screen_image_keeps_python_manifest_small(self) -> None:
        """Move dense imported pixels out of parsed Python source."""
        art = PixelArt(300, 320)
        colors = tuple(index * 0x111 for index in range(17))
        for y in range(art.height):
            for x in range(art.width):
                art.set_pixel(x, y, colors[(x + y) % len(colors)])
        resource = generate_asset_resource(
            "project-dense",
            "8.0",
            [GeneratedRasterEntry("asset-dense", "Dense", (art,))],
        )
        self.assertLess(len(resource.module_source.encode("utf-8")), 12_500)
        pixel_payload_size = 320 * (((300 + 7) // 8) + 300 * 2)
        self.assertGreater(len(resource.data), pixel_payload_size)
        self.assertLess(len(resource.data) - pixel_payload_size, 128)
        self.assertEqual(resource.asset_count, 1)
        self.assertEqual(resource.frame_count, 1)
        self.assertEqual(resource.maximum_row_bytes, 638)
        self.assertNotIn("_fill_rectangle(0, 0", resource.module_source)
        self.assertNotIn("_ASSETS", resource.module_source)
        compile(resource.module_source, "generated_assets.py", "exec")

    def test_hundreds_of_assets_keep_the_runtime_module_constant_size(self) -> None:
        """Grow the binary catalogue without growing a parsed Python table."""
        art = self._one_pixel_art()
        entries = [
            GeneratedRasterEntry(f"asset-{index:04d}", f"Asset {index}", (art,))
            for index in range(500)
        ]
        resource = generate_asset_resource("project-many", "8.0", entries)
        empty = generate_asset_resource("project-many", "8.0", [])
        reversed_resource = generate_asset_resource(
            "project-many", "8.0", list(reversed(entries))
        )
        self.assertEqual(resource.data, reversed_resource.data)
        self.assertEqual(resource.module_source, reversed_resource.module_source)
        self.assertEqual(len(resource.module_source), len(empty.module_source))
        self.assertEqual(resource.asset_count, 500)
        self.assertNotIn("asset-0000", resource.module_source)
        namespace: dict[str, object] = {
            "__file__": "/apps/demo/generated_assets.py",
            "open": lambda unused_path, unused_mode: io.BytesIO(resource.data),
        }
        exec(resource.module_source, namespace)
        for asset_id in ("asset-0000", "asset-0250", "asset-0499"):
            self.assertTrue(namespace["has_asset"](asset_id))
            self.assertEqual(namespace["asset_size"](asset_id), (2, 2))
        self.assertFalse(namespace["has_asset"]("asset-0500"))

    def test_hash_collision_still_requires_the_complete_asset_id(self) -> None:
        """Use the fixed index for speed without trusting a 32-bit hash as identity."""
        art = self._one_pixel_art()
        resource = generate_asset_resource(
            "project-collision",
            "8.0",
            [
                GeneratedRasterEntry("asset-a", "A", (art,)),
                GeneratedRasterEntry("asset-b", "B", (art,)),
            ],
        )
        collision_data = bytearray(resource.data)
        index_start = 6 + len("project-collision") + 10
        for index in range(2):
            start = index_start + index * 8
            collision_data[start : start + 4] = (123).to_bytes(4, "little")
        namespace: dict[str, object] = {
            "__file__": "generated_assets.py",
            "open": lambda unused_path, unused_mode: io.BytesIO(collision_data),
        }
        exec(resource.module_source, namespace)
        namespace["_asset_hash"] = lambda unused_id: 123
        self.assertTrue(namespace["has_asset"]("asset-a"))
        self.assertTrue(namespace["has_asset"]("asset-b"))
        self.assertFalse(namespace["has_asset"]("asset-c"))

    def test_streamed_runtime_rejects_wrong_or_corrupt_resources(self) -> None:
        """Fail closed when ownership or a selected frame span is invalid."""
        art = self._one_pixel_art()
        resource = generate_asset_resource(
            "project-safe",
            "8.0",
            [GeneratedRasterEntry("asset-safe", "Safe", (art,))],
        )
        namespace: dict[str, object] = {"__file__": "generated_assets.py"}
        current_data = resource.data
        namespace["open"] = lambda unused_path, unused_mode: io.BytesIO(current_data)
        exec(resource.module_source, namespace)
        self.assertTrue(namespace["has_asset"]("asset-safe"))

        current_data = generate_asset_resource("other-project", "8.0", []).data
        self.assertFalse(namespace["has_asset"]("asset-safe"))

        corrupt = bytearray(resource.data)
        project_end = 6 + len("project-safe")
        index_start = project_end + 10
        record_offset = int.from_bytes(
            corrupt[index_start + 4 : index_start + 8], "little"
        )
        frame_record = record_offset + 2 + 2 + len("asset-safe") + 2 + len("Safe") + 14
        corrupt[frame_record : frame_record + 4] = (0).to_bytes(4, "little")
        current_data = bytes(corrupt)

        class EmptyDraw:
            def _bytearray(self, *unused_args: object) -> None:
                raise AssertionError("Corrupt data must not be drawn")

        self.assertFalse(namespace["draw_asset"](EmptyDraw(), "asset-safe", 0, 0))

    def test_resource_owner_parser_recognizes_all_pga_generations(self) -> None:
        """Identify old and current sidecars during ownership-safe migration."""
        legacy = b"PGA1" + (7).to_bytes(2, "little") + b"project" + b"payload"
        self.assertEqual(parse_asset_resource_project(legacy), "project")
        for version in (2, 3):
            resource = generate_asset_resource(
                "project",
                "8.0",
                [],
                format_version=version,
            )
            self.assertEqual(parse_asset_resource_project(resource.data), "project")

    def test_generated_module_rejects_bad_entry_metadata(self) -> None:
        """Require stable IDs, metadata, and unique runtime records."""
        encoded = encode_asset([self._one_pixel_art()])
        with self.assertRaisesRegex(ValueError, "project ID"):
            generate_assets_module("", "8.0", [])
        with self.assertRaisesRegex(ValueError, "generator version"):
            generate_assets_module("project-1", "", [])
        duplicate = GeneratedAssetEntry("same", "Icon", encoded)
        with self.assertRaisesRegex(ValueError, "unique"):
            generate_assets_module("project-1", "8.0", [duplicate, duplicate])
        with self.assertRaisesRegex(ValueError, "nonempty"):
            generate_assets_module(
                "project-1", "8.0", [GeneratedAssetEntry("", "Icon", encoded)]
            )

    @staticmethod
    def _one_pixel_art() -> PixelArt:
        """Return a small valid visible fixture."""
        art = PixelArt(2, 2)
        art.set_pixel(0, 0, 0xF800)
        return art

    @staticmethod
    def _wav_bytes(
        *,
        frame_count: int = 80,
        sample_rate: int = 8000,
    ) -> bytes:
        """Return a complete deterministic unsigned 8-bit mono PCM WAV."""
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(1)
            target.setframerate(sample_rate)
            target.writeframes(bytes(index % 256 for index in range(frame_count)))
        return output.getvalue()


if __name__ == "__main__":
    unittest.main()
