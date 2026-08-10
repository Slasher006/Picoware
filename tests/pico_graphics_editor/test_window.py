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
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

from pico_graphics_editor.model import PixelArt
from pico_graphics_editor.source import SourcePatch, build_new_graphic_patch
from pico_graphics_editor.designer_model import GuiProject
from pico_graphics_editor.window import (
    DiffDialog,
    MainWindow,
    MultiPatchDialog,
    NewGraphicDialog,
)


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

    def test_pixel_toolbar_exposes_new_asset_action(self) -> None:
        """Show the pixel asset creation action in the Pixel Art toolbar."""
        self.assertIn(self.window.new_graphic_action, self.window.tool_bar.actions())
        button = self.window.tool_bar.widgetForAction(self.window.new_graphic_action)
        self.assertEqual(button.text(), "New Asset")
        self.window.workspace_tabs.setCurrentIndex(1)
        self.assertTrue(self.window.tool_bar.isHidden())
        self.window.workspace_tabs.setCurrentIndex(0)
        self.assertFalse(self.window.tool_bar.isHidden())

    def test_new_asset_dialog_offers_explicit_creation_modes(self) -> None:
        """Choose blank, current, reference, or animation asset sources."""
        dialog = NewGraphicDialog(24, 16, True, 3)
        modes = {
            dialog.mode_combo.itemData(index)
            for index in range(dialog.mode_combo.count())
        }
        self.assertEqual(
            modes,
            {
                "blank",
                "current",
                "reference",
                "animation_file",
                "imported_frames",
            },
        )
        self.assertEqual(dialog.settings(), ("draw_new_graphic", 24, 16, "blank"))
        dialog.close()

    def test_apply_buttons_accept_source_review_dialogs(self) -> None:
        """Close source review dialogs when their Apply buttons are clicked."""
        source_patch = SourcePatch(
            Path(self.temporary.name) / "review.py",
            "",
            "",
            "",
            "review",
            0,
        )
        dialogs = (DiffDialog(source_patch), MultiPatchDialog([source_patch]))
        for dialog in dialogs:
            buttons = dialog.findChild(QDialogButtonBox)
            apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
            apply_button.click()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            dialog.close()

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
        self.assertIn("Managed asset", self.window.asset_mode_label.text())
        self.assertTrue(self.window.resize_canvas_action.isEnabled())
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

    def test_pixel_draft_recovery_restores_unsaved_managed_art(self) -> None:
        """Recover unsaved managed pixels from an atomic draft."""
        target = Path(self.temporary.name) / "recover_asset.py"
        art = PixelArt(3, 2)
        art.set_pixel(0, 0, 0xF800)
        source_patch = build_new_graphic_patch(target, "draw_recover", [art])
        source_patch.apply(Path(self.temporary.name) / "creation-backups")
        self.assertTrue(self.window._open_created_graphic(target, source_patch.key))
        recovery_path = Path(self.temporary.name) / "pixel-recovery.json"
        self.window._pixel_recovery_path = lambda: recovery_path
        self.window.canvas.art().set_pixel(1, 1, 0x07E0)
        self.window._canvas_changed()
        self.window._write_pixel_recovery()
        self.assertTrue(recovery_path.is_file())
        self.window.canvas.art().set_pixel(1, 1, None)
        with mock_patch.object(self.window, "_confirm_discard", return_value=True):
            self.window._recover_pixel_asset()
        self.assertEqual(self.window.canvas.art().pixel(1, 1), 0x07E0)
        self.assertTrue(self.window._dirty)
        self.window._dirty = False
        self.window._clear_pixel_recovery()

    def test_save_discard_prompt_can_save_pixel_edits(self) -> None:
        """Offer Save before abandoning a dirty pixel asset."""
        self.window._dirty = True
        with (
            mock_patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Save,
            ),
            mock_patch.object(
                self.window,
                "_apply_to_source",
                return_value=True,
            ) as apply_source,
        ):
            self.assertTrue(self.window._confirm_discard())
        apply_source.assert_called_once_with()
        self.window._dirty = False

    def test_current_pixel_asset_can_be_handed_to_gui_designer(self) -> None:
        """Select the current source asset in the App GUI catalogue."""
        self.window._use_current_asset_in_gui()
        self.assertEqual(self.window.workspace_tabs.currentIndex(), 1)
        item = self.window.screen_designer.pixel_asset_list.currentItem()
        self.assertIsNotNone(item)
        self.assertIn("animation.py", str(item.data(Qt.ItemDataRole.UserRole)))

    def test_animation_frames_can_be_duplicated_and_reordered(self) -> None:
        """Duplicate a source frame and change playback order."""
        original_count = self.window.frame_combo.count()
        self.window._duplicate_animation_frame()
        self.assertEqual(self.window.frame_combo.count(), original_count + 1)
        duplicated_value = self.window.frame_combo.currentData()
        self.window._move_animation_frame(-1)
        self.assertEqual(self.window.frame_combo.currentData(), duplicated_value)
        self.assertIn(duplicated_value, self.window.animation_drafts)

    def test_managed_animation_structure_changes_are_saved(self) -> None:
        """Persist managed frame deletion instead of treating it as preview-only."""
        target = Path(self.temporary.name) / "managed_animation.py"
        first = PixelArt(2, 2)
        first.set_pixel(0, 0, 0xF800)
        second = PixelArt(2, 2)
        second.set_pixel(1, 0, 0x07E0)
        source_patch = build_new_graphic_patch(
            target, "draw_managed_animation", [first, second]
        )
        source_patch.apply(Path(self.temporary.name) / "creation-backups")
        self.assertTrue(self.window._open_created_graphic(target, source_patch.key))
        self.assertEqual(self.window.frame_combo.count(), 2)
        self.window._delete_animation_frame()
        self.assertEqual(self.window.frame_combo.count(), 1)
        self.assertTrue(self.window._dirty)
        frames = self.window._managed_frames_for_save()
        self.assertIsNotNone(frames)
        self.assertEqual(len(frames), 1)
        self.window._dirty = False
        self.window._animation_structure_dirty = False

    def test_managed_canvas_can_be_cleared_and_undone(self) -> None:
        """Clear all managed pixels after confirmation and retain undo."""
        target = Path(self.temporary.name) / "managed_clear.py"
        art = PixelArt(2, 2)
        art.set_pixel(0, 0, 0xF800)
        source_patch = build_new_graphic_patch(target, "draw_clear", [art])
        source_patch.apply(Path(self.temporary.name) / "creation-backups")
        self.assertTrue(self.window._open_created_graphic(target, source_patch.key))
        with mock_patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window._clear_pixel_canvas()
        self.assertTrue(all(color is None for color in self.window.canvas.art().pixels))
        self.window.canvas.undo()
        self.assertEqual(self.window.canvas.art().pixel(0, 0), 0xF800)
        self.window._dirty = False

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
