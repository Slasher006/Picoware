"""Tests for source discovery, tracing, and patching."""

# ruff: noqa: E402

import ast
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_PATH = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_PATH) not in sys.path:
    sys.path.insert(0, str(TOOLS_PATH))

from pico_graphics_editor.source import SourceExporter, SourceScanner, TraceInterpreter


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


if __name__ == "__main__":
    unittest.main()
