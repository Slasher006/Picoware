"""Tests for source discovery, tracing, and patching."""

# ruff: noqa: E402

import ast
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.model import PixelArt
from pico_graphics_editor.source import (
    SourceExporter,
    SourceScanner,
    TraceInterpreter,
    build_new_graphic_patch,
)


SAMPLE_SOURCE = '''"""Renderer fixture."""

COLOR_BASE = 0x2104
COLOR_LIGHT = 0xFFFF


class Renderer:
    """Draw fixture graphics."""

    def _fill(self, x, y, width, height, color):
        """Draw one filled rectangle."""
        self.draw._fill_rectangle(x, y, width, height, color)

    def _draw_icon(self, x, y, kind=0, selected=False):
        """Draw one selectable icon variant."""
        color = COLOR_LIGHT if selected else COLOR_BASE
        self._fill(x, y, 4, 4, color)
        if kind == 0:
            self._line(x, y, x + 3, y + 3, COLOR_LIGHT)
        else:
            self._fill(x + 1, y + 1, 2, 2, COLOR_LIGHT)
'''


class SourceTests(unittest.TestCase):
    """Verify source-aware graphics behavior."""

    def setUp(self) -> None:
        """Create one temporary renderer source."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "render.py"
        self.source_path.write_text(SAMPLE_SOURCE, encoding="utf-8")
        self.scanner = SourceScanner()
        self.tracer = TraceInterpreter()

    def tearDown(self) -> None:
        """Remove the temporary renderer source."""
        self.temporary.cleanup()

    def test_scanner_finds_asset_not_wrapper(self) -> None:
        """Expose the icon while hiding primitive wrappers."""
        assets = self.scanner.scan_file(self.source_path)
        self.assertEqual([asset.record.name for asset in assets], ["_draw_icon"])
        self.assertEqual(assets[0].variants["kind"], [0, 1])
        self.assertEqual(assets[0].variants["selected"], [False, True])

    def test_trace_renders_selected_variants(self) -> None:
        """Render distinct branches without importing source."""
        asset = self.scanner.scan_file(self.source_path)[0]
        first = self.tracer.render(asset, {"kind": 0, "selected": False})
        second = self.tracer.render(asset, {"kind": 1, "selected": True})
        self.assertGreaterEqual(len(first.primitives), 2)
        self.assertGreaterEqual(len(second.primitives), 2)
        self.assertNotEqual(first.current_art.pixels, second.current_art.pixels)

    def test_exporter_builds_parseable_variant_overlay(self) -> None:
        """Write a compact overlay inside the selected branch."""
        asset = self.scanner.scan_file(self.source_path)[0]
        variants = {"kind": 0, "selected": False}
        trace = self.tracer.render(asset, variants)
        edited = trace.current_art.copy()
        edited.set_pixel(2, 2, 0xF800)
        patch = SourceExporter().build_patch(asset, trace, edited, variants)
        ast.parse(patch.updated)
        self.assertIn("# Pixel overlay", patch.updated)
        self.assertIn("if kind == 0 and selected == False:", patch.updated)
        self.assertIn("self._fill", patch.updated)
        self.assertIn("0xF800", patch.updated)
        self.assertNotEqual(patch.original, patch.updated)

    def test_apply_creates_backup_and_valid_source(self) -> None:
        """Apply a reviewed patch with an external backup."""
        asset = self.scanner.scan_file(self.source_path)[0]
        variants = {"kind": 1, "selected": False}
        trace = self.tracer.render(asset, variants)
        edited = trace.current_art.copy()
        edited.set_pixel(3, 3, 0x07E0)
        patch = SourceExporter().build_patch(asset, trace, edited, variants)
        backup = patch.apply(self.root / "backups")
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), SAMPLE_SOURCE)
        ast.parse(self.source_path.read_text(encoding="utf-8"))

    def test_existing_overlay_is_replaced_cumulatively(self) -> None:
        """Preserve earlier pixels while updating one variant block."""
        variants = {"kind": 0, "selected": False}
        asset = self.scanner.scan_file(self.source_path)[0]
        trace = self.tracer.render(asset, variants)
        first_edit = trace.current_art.copy()
        first_edit.set_pixel(2, 2, 0xF800)
        first_patch = SourceExporter().build_patch(asset, trace, first_edit, variants)
        self.source_path.write_text(first_patch.updated, encoding="utf-8")

        rescanned = self.scanner.scan_file(self.source_path)[0]
        retraced = self.tracer.render(rescanned, variants)
        second_edit = retraced.current_art.copy()
        second_edit.set_pixel(3, 2, 0x07E0)
        second_patch = SourceExporter().build_patch(
            rescanned,
            retraced,
            second_edit,
            variants,
        )
        self.assertEqual(second_patch.updated.count("begin"), 1)
        self.assertIn("0xF800", second_patch.updated)
        self.assertIn("0x07E0", second_patch.updated)

    def test_current_pico_bomber_renderer_is_supported(self) -> None:
        """Discover and trace representative repository graphics."""
        renderer = (
            REPOSITORY_PATH
            / "builds/MicroPython/apps_unfrozen/games/pico_bomber/render.py"
        )
        assets = self.scanner.scan_file(renderer)
        names = {asset.record.name for asset in assets}
        self.assertIn("_draw_solid", names)
        self.assertIn("_draw_player", names)
        self.assertIn("_draw_slime_enemy", names)
        solid = next(asset for asset in assets if asset.record.name == "_draw_solid")
        slime = next(
            asset for asset in assets if asset.record.name == "_draw_slime_enemy"
        )
        self.assertEqual(solid.variants["theme"], list(range(8)))
        self.assertEqual(slime.variants["frame"], list(range(8)))
        trace = self.tracer.render(solid, {"theme": 0})
        self.assertGreaterEqual(len(trace.primitives), 4)

    def test_new_reference_graphic_generates_frames(self) -> None:
        """Generate a parseable new animated RGB565 function."""
        first = PixelArt(4, 4)
        first.set_pixel(1, 1, 0xF800)
        second = PixelArt(4, 4)
        second.set_pixel(2, 1, 0x07E0)
        target = self.root / "new_graphic.py"
        patch = build_new_graphic_patch(target, "draw_ship", [first, second])
        ast.parse(patch.updated)
        self.assertIn("def draw_ship(draw, x, y, frame=0):", patch.updated)
        self.assertIn("_pico_graphic_size = (4, 4)", patch.updated)
        self.assertIn("_pico_graphic_frames = (0, 1)", patch.updated)
        self.assertIn("if frame == 1:", patch.updated)
        self.assertEqual(patch.run_count, 2)
        backup = patch.apply(self.root / "backups")
        assets = self.scanner.scan_file(target)
        generated = next(asset for asset in assets if asset.record.name == "draw_ship")
        trace = self.tracer.render(generated, {"frame": 1})
        self.assertIsNone(backup)
        self.assertEqual(generated.variants["frame"], [0, 1])
        self.assertGreaterEqual(len(trace.primitives), 1)
        self.assertEqual((trace.current_art.width, trace.current_art.height), (4, 4))

    def test_managed_graphic_rewrite_supports_transparency_and_resize(self) -> None:
        """Rewrite managed pixels without source overlay restrictions."""
        target = self.root / "managed.py"
        original_art = PixelArt(4, 4)
        original_art.set_pixel(1, 1, 0xF800)
        build_new_graphic_patch(target, "draw_icon", [original_art]).apply(
            self.root / "backups"
        )
        asset = self.scanner.scan_file(target)[0]
        trace = self.tracer.render(asset)
        edited = PixelArt(6, 5)
        edited.set_pixel(4, 3, 0x07E0)
        patch = SourceExporter().build_patch(asset, trace, edited)
        ast.parse(patch.updated)
        self.assertNotIn("# Pixel overlay", patch.updated)
        self.assertNotIn("0xF800", patch.updated)
        self.assertIn("_pico_graphic_size = (6, 5)", patch.updated)
        patch.apply(self.root / "backups")
        rescanned = self.scanner.scan_file(target)[0]
        retraced = self.tracer.render(rescanned)
        self.assertEqual(
            (retraced.current_art.width, retraced.current_art.height), (6, 5)
        )
        self.assertIsNone(retraced.current_art.pixel(1, 1))
        self.assertEqual(retraced.current_art.pixel(4, 3), 0x07E0)

    def test_managed_animation_requires_every_frame(self) -> None:
        """Refuse a partial rewrite of a managed animation."""
        target = self.root / "managed_animation.py"
        build_new_graphic_patch(
            target,
            "draw_walk",
            [PixelArt(2, 2), PixelArt(2, 2)],
        ).apply(self.root / "backups")
        asset = self.scanner.scan_file(target)[0]
        trace = self.tracer.render(asset, {"frame": 0})
        with self.assertRaisesRegex(ValueError, "All managed animation frames"):
            SourceExporter().build_patch(asset, trace, trace.current_art, {"frame": 0})

    def test_blank_new_graphic_is_discoverable_at_requested_size(self) -> None:
        """Discover an empty generated graphic without fake visible pixels."""
        target = self.root / "blank_graphic.py"
        patch = build_new_graphic_patch(target, "draw_blank_icon", [PixelArt(19, 13)])
        patch.apply(self.root / "backups")
        assets = self.scanner.scan_file(target)
        generated = next(
            asset for asset in assets if asset.record.name == "draw_blank_icon"
        )
        trace = self.tracer.render(generated)
        self.assertEqual(trace.primitives, [])
        self.assertEqual((trace.current_art.width, trace.current_art.height), (19, 13))
        self.assertTrue(all(color is None for color in trace.current_art.pixels))

    def test_legacy_blank_generated_graphic_is_discoverable(self) -> None:
        """Open a blank generated function created before size metadata."""
        target = self.root / "legacy_graphic.py"
        target.write_text(
            "# Pico graphic draw_new_graphic begin\n"
            "def draw_new_graphic(draw, x, y):\n"
            '    """Draw generated RGB565 pixel graphics."""\n'
            "    pass\n"
            "# Pico graphic draw_new_graphic end\n",
            encoding="utf-8",
        )
        assets = self.scanner.scan_file(target)
        generated = next(
            asset for asset in assets if asset.record.name == "draw_new_graphic"
        )
        trace = self.tracer.render(generated)
        self.assertEqual((trace.current_art.width, trace.current_art.height), (32, 32))


if __name__ == "__main__":
    unittest.main()
