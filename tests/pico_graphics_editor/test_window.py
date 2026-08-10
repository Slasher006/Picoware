"""Tests for animation controls in the Qt main window."""

# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from pico_graphics_editor.model import PixelArt
from pico_graphics_editor.source import build_new_graphic_patch
from pico_graphics_editor.designer_model import GuiProject
from pico_graphics_editor.window import DiffDialog, MainWindow


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

    def test_designer_workspace_uses_project_undo_history(self) -> None:
        """Route global undo and redo actions to the GUI designer."""
        self.window.workspace_tabs.setCurrentIndex(1)
        self.window.screen_designer._add_element("button")
        self.assertTrue(self.window.undo_action.isEnabled())
        self.window._undo_current()
        self.assertEqual(self.window.designer_session.current_screen().elements, [])
        self.assertTrue(self.window.redo_action.isEnabled())
        self.window._redo_current()
        self.assertEqual(len(self.window.designer_session.current_screen().elements), 1)
        self.window.designer_session.set_project(GuiProject.create())

    def test_dirty_gui_project_writes_and_clears_recovery(self) -> None:
        """Keep a safety backup after clearing an explicit save recovery."""
        recovery_path = Path(self.temporary.name) / "recovery.picogui.json"
        backup_root = Path(self.temporary.name) / "backups"
        self.window._designer_recovery_path = lambda: recovery_path
        self.window._gui_backup_root = lambda: backup_root
        self.window.screen_designer._add_element("button")
        self.window._write_designer_recovery()
        recovered = GuiProject.load(recovery_path)
        self.assertEqual(len(recovered.screens[0].elements), 1)
        project_path = Path(self.temporary.name) / "saved.picogui.json"
        self.assertTrue(self.window._save_gui_project_to(project_path))
        self.assertFalse(recovery_path.exists())
        backups = list(backup_root.glob("saved.picogui.json.*.bak"))
        self.assertEqual(len(backups), 1)
        project_path.unlink()
        restored = GuiProject.load(backups[0])
        self.assertEqual(len(restored.screens[0].elements), 1)

    def test_new_pixel_asset_loads_and_apply_writes_edits(self) -> None:
        """Load a created asset so Apply writes later pixel changes."""
        target = Path(self.temporary.name) / "new_asset.py"
        art = PixelArt(2, 2)
        art.set_pixel(0, 0, 0xF800)
        source_patch = build_new_graphic_patch(target, "draw_badge", [art])
        source_patch.apply(Path(self.temporary.name) / "creation-backups")
        self.window._source_backup_root = lambda: (
            Path(self.temporary.name) / "apply-backups"
        )
        self.assertTrue(self.window._open_created_graphic(target, source_patch.key))
        self.assertIsNotNone(self.window.current_asset)
        self.assertEqual(self.window.current_asset.record.name, "draw_badge")
        self.assertEqual(self.window._scan_path, target.resolve())
        self.assertEqual(self.window.screen_designer.pixel_asset_list.count(), 1)
        pixel_item = self.window.screen_designer.pixel_asset_list.item(0)
        self.assertEqual(pixel_item.text(), "draw_badge")
        pixel_key = str(pixel_item.data(Qt.ItemDataRole.UserRole))
        self.assertIn(pixel_key, self.window.screen_designer.pixel_assets)
        self.window.canvas.art().set_pixel(0, 0, 0x07E0)
        self.window._canvas_changed()
        self.assertTrue(self.window.apply_button.isEnabled())
        with (
            mock_patch.object(
                DiffDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            mock_patch.object(QMessageBox, "information"),
        ):
            self.window.apply_button.click()
        updated = target.read_text(encoding="utf-8")
        self.assertIn("0x07E0", updated)
        self.assertFalse(self.window._dirty)
        self.assertEqual(self.window.screen_designer.pixel_asset_list.count(), 1)
        refreshed_asset = next(iter(self.window.screen_designer.pixel_assets.values()))
        self.assertEqual(refreshed_asset.art.pixel(0, 0), 0x07E0)

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
