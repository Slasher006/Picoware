"""Tests for animation controls in the Qt main window."""

# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtWidgets import QApplication

from pico_graphics_editor.window import MainWindow


ANIMATION_SOURCE = '''class Renderer:
    """Draw animation fixtures."""

    def _draw_animation(self, x, y, frame=0):
        """Draw one inferred frame."""
        if frame % 2:
            self.draw._fill_rectangle(x, y, 3, 1, 0xF800)
        else:
            self.draw._fill_rectangle(x, y, 1, 3, 0x07E0)
'''


class WindowTests(unittest.TestCase):
    """Verify dedicated animation frame behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the shared offscreen Qt application."""
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """Create a window and temporary animation source."""
        self.temporary = tempfile.TemporaryDirectory()
        self.source_path = Path(self.temporary.name) / "animation.py"
        self.source_path.write_text(ANIMATION_SOURCE, encoding="utf-8")
        self.window = MainWindow()
        asset = self.window.scanner.scan_file(self.source_path)[0]
        self.window._load_asset(asset)

    def tearDown(self) -> None:
        """Close the window and remove its source."""
        self.window.close()
        self.temporary.cleanup()

    def test_frame_controls_select_distinct_variants(self) -> None:
        """Expose and render inferred frame variants."""
        first_pixels = self.window.canvas.art().pixels.copy()
        self.assertEqual(self.window.animation_parameter, "frame")
        self.assertFalse(self.window.animation_group.isHidden())
        self.assertEqual(self.window.frame_combo.count(), 8)
        self.window.frame_combo.setCurrentIndex(1)
        self.application.processEvents()
        self.assertEqual(self.window.variant_values["frame"], 1)
        self.assertNotEqual(first_pixels, self.window.canvas.art().pixels)

    def test_window_exposes_three_design_workspaces(self) -> None:
        """Expose pixel, screen, and flow editing modes."""
        self.assertEqual(self.window.workspace_tabs.count(), 3)
        self.assertEqual(
            [self.window.workspace_tabs.tabText(index) for index in range(3)],
            ["Pixel Art", "App GUI", "Screen Flow"],
        )
        self.assertTrue(self.window.import_existing_app_action.isEnabled())
        self.assertFalse(self.window.apply_imported_app_action.isEnabled())

    def test_animation_frames_can_be_duplicated_and_reordered(self) -> None:
        """Duplicate a source frame and change playback order."""
        original_count = self.window.frame_combo.count()
        self.window._duplicate_animation_frame()
        self.assertEqual(self.window.frame_combo.count(), original_count + 1)
        duplicated_value = self.window.frame_combo.currentData()
        self.window._move_animation_frame(-1)
        self.assertEqual(self.window.frame_combo.currentData(), duplicated_value)
        self.assertIn(duplicated_value, self.window.animation_drafts)

    def test_playback_and_onion_skin_are_available(self) -> None:
        """Enable frame playback and the previous-frame overlay."""
        self.window.frame_combo.setCurrentIndex(1)
        self.window.onion_skin_check.setChecked(True)
        self.window.play_button.setChecked(True)
        self.assertTrue(self.window.animation_timer.isActive())
        self.assertEqual(self.window.play_button.text(), "Stop")
        self.window.play_button.setChecked(False)
        self.assertFalse(self.window.animation_timer.isActive())

    def test_frame_edit_targets_only_selected_frame(self) -> None:
        """Build a source overlay guarded by the active frame."""
        self.window.frame_combo.setCurrentIndex(1)
        edited = self.window.canvas.art().copy()
        edited.set_pixel(0, 0, 0x001F)
        patch = self.window.exporter.build_patch(
            self.window.current_asset,
            self.window.current_trace,
            edited,
            self.window.variant_values,
        )
        self.assertIn("if frame == 1:", patch.updated)


if __name__ == "__main__":
    unittest.main()
