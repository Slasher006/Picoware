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

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QAction, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)

from pico_graphics_editor.model import PixelArt
from pico_graphics_editor.image_dialog import LibraryImageImportResult
from pico_graphics_editor.library_workspace import PersonalAssetLibraryWidget
from pico_graphics_editor.asset_library import AssetLibrary, LibraryAsset
from pico_graphics_editor.asset_codegen import (
    GeneratedRasterEntry,
    generate_asset_resource,
)
from pico_graphics_editor.source import SourcePatch, build_new_graphic_patch
from pico_graphics_editor.designer_model import GuiElement, GuiProject, ProjectAsset
from pico_graphics_editor.designer import flow_endpoint_key
from pico_graphics_editor.window import (
    AppPresetDialog,
    DiffDialog,
    GeneratedAppReviewDialog,
    MainWindow,
    MultiPatchDialog,
    NewGraphicDialog,
    TextReportDialog,
)
from pico_graphics_editor.ui_help import (
    INTERACTIVE_WIDGETS,
    _is_generated_help,
    _is_generic_example,
    _split_tooltip,
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

    def test_placed_project_asset_opens_in_pixel_editor_and_updates_in_place(
        self,
    ) -> None:
        """Round-trip a detached screen asset without editing generated PGA output."""
        project = GuiProject.create("Project Asset Edit")
        art = PixelArt(2, 2)
        art.set_pixel(0, 0, 0xF800)
        asset = ProjectAsset.from_pixel_art("asset-edit", "Editable", art)
        project.assets.append(asset)
        element = GuiElement.create("icon", 1)
        element.asset_id = asset.id
        element.asset_link_state = "detached"
        element.asset_width = 2
        element.asset_height = 2
        project.screens[0].elements.append(element)
        self.window.designer_session.set_project(project)

        self.window._edit_project_asset(asset.id, 0)

        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.pixel_art_tab_index,
        )
        self.assertEqual(self.window._editing_project_asset_id, asset.id)
        self.assertEqual(self.window.canvas.art().pixel(0, 0), 0xF800)
        edited = self.window.canvas.art().copy()
        edited.set_pixel(1, 1, 0x07E0)
        self.window.canvas.set_art(edited)
        self.assertTrue(self.window._dirty)
        self.assertEqual(self.window.apply_button.text(), "Update Project Asset")
        self.assertTrue(self.window._apply_to_source())
        self.assertEqual(asset.frames[0][3], 0x07E0)
        self.assertEqual(element.asset_runs[-1], [1, 1, 1, 0x07E0])
        self.assertTrue(self.window.designer_session.dirty)
        self.window.designer_session.dirty = False

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
        self.window.workspace_tabs.setCurrentIndex(self.window.pixel_art_tab_index)

    def tearDown(self) -> None:
        """Close the window and remove its source."""
        self.window._dirty = False
        self.window.designer_session.dirty = False
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

    def test_source_comparison_view_saves_the_composite_edit(self) -> None:
        """Never replace a dirty overlay with the read-only Original view."""
        self.window._source_backup_root = lambda: (
            Path(self.temporary.name) / "comparison-backups"
        )
        self.window._pixel_recovery_path = lambda: (
            Path(self.temporary.name) / "comparison-recovery.json"
        )
        self.window.canvas.art().set_pixel(2, 2, 0x001F)
        self.window._canvas_changed()
        self.assertTrue(self.window._dirty)
        self.window.source_view_combo.setCurrentIndex(
            self.window.source_view_combo.findData("original")
        )
        with (
            mock_patch.object(
                DiffDialog, "exec", return_value=QDialog.DialogCode.Accepted
            ),
            mock_patch.object(QMessageBox, "information"),
        ):
            self.assertTrue(self.window._apply_to_source())
        self.assertIn("0x001F", self.source_path.read_text(encoding="utf-8"))

    def test_window_exposes_five_design_workspaces(self) -> None:
        """Start in App GUI and expose all five workspaces in task order."""
        self.assertEqual(self.window.workspace_tabs.count(), 5)
        self.assertEqual(
            [self.window.workspace_tabs.tabText(index) for index in range(5)],
            ["App GUI", "Screen Flow", "Simulator", "Pixel Art", "Asset Library"],
        )
        fresh_window = MainWindow()
        try:
            self.assertEqual(fresh_window.app_gui_tab_index, 0)
            self.assertEqual(
                fresh_window.workspace_tabs.currentIndex(),
                fresh_window.app_gui_tab_index,
            )
            self.assertEqual(
                [action.data() for action in fresh_window.workspace_actions],
                [
                    "app_gui",
                    "screen_flow",
                    "simulator",
                    "pixel_art",
                    "asset_library",
                ],
            )
        finally:
            fresh_window.designer_session.dirty = False
            fresh_window.close()

    def test_app_preset_dialog_previews_and_returns_project_settings(self) -> None:
        """Let beginners compare all starters before creating a project."""
        dialog = AppPresetDialog(self.window)
        try:
            self.assertEqual(dialog.preset_list.count(), 10)
            for row in range(dialog.preset_list.count()):
                dialog.preset_list.setCurrentRow(row)
                self.application.processEvents()
                self.assertFalse(dialog.preview_label.pixmap().isNull())
            dialog.preset_list.setCurrentRow(5)
            dialog.device_combo.setCurrentText("Cardputer 240x135")
            dialog.project_name_edit.setText("Workshop Monitor")
            self.application.processEvents()
            self.assertEqual(
                dialog.settings(),
                ("pocket_converter", "Workshop Monitor", "Cardputer 240x135"),
            )
            self.assertIn("screens", dialog.screen_summary.text())
            self.assertIn("navigation links", dialog.screen_summary.text())
            dialog.project_name_edit.clear()
            self.assertFalse(
                dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
            )
        finally:
            dialog.close()

    def test_new_from_preset_creates_an_unsaved_app_gui_project(self) -> None:
        """Create the selected structure and route users directly into App GUI."""
        self.assertIn(self.window.new_preset_action, self.window.new_menu.actions())
        self.assertIn(self.window.new_preset_action, self.window.app_menu.actions())
        self.window.designer_session.dirty = False
        with (
            mock_patch.object(
                AppPresetDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            mock_patch.object(
                AppPresetDialog,
                "settings",
                return_value=(
                    "quick_note",
                    "Workshop Round",
                    "PicoCalc 320x320",
                ),
            ),
        ):
            self.window._new_gui_project_from_preset()

        project = self.window.designer_session.project
        self.assertEqual(project.name, "Workshop Round")
        self.assertEqual(project.generated_app["starter_id"], "quick_note")
        self.assertEqual(len(project.screens), 2)
        self.assertIsNone(self.window.designer_session.path)
        self.assertFalse(self.window.designer_session.dirty)
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(), self.window.app_gui_tab_index
        )
        self.assertIn(
            "Created Quick Note starter", self.window.statusBar().currentMessage()
        )

    def test_bundled_mqtt_example_opens_as_an_unsaved_editable_copy(self) -> None:
        """Expose the complete example without targeting its bundled source on save."""
        self.assertIn(
            self.window.open_mqtt_example_action, self.window.open_menu.actions()
        )
        self.assertIn(
            self.window.mqtt_tutorial_help_action, self.window.help_menu.actions()
        )
        self.window.designer_session.dirty = False

        self.window.open_mqtt_example_action.trigger()

        project = self.window.designer_session.project
        self.assertEqual(project.name, "MQTT Client")
        self.assertEqual(len(project.screens), 5)
        self.assertEqual(len(project.assets), 1)
        self.assertEqual(len(project.connections), 6)
        self.assertEqual(len(project.behavior_nodes), 7)
        self.assertEqual(project.flow_standard_version, 2)
        self.assertEqual(
            {
                element.native_widget
                for screen in project.screens
                for element in screen.elements
                if element.native_widget
            },
            {"alert", "keyboard", "menu", "textbox", "toggle"},
        )
        self.assertEqual(project.generated_app["destination"], "")
        self.assertIsNone(self.window.designer_session.path)
        self.assertFalse(self.window.designer_session.dirty)
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(), self.window.app_gui_tab_index
        )
        self.assertIn(
            "unsaved copy of the bundled MQTT Client example",
            self.window.statusBar().currentMessage(),
        )

        example_root = (
            REPOSITORY_PATH / "pico_graphics_editor" / "examples" / "mqtt_client"
        )
        self.assertTrue((example_root / "README.md").is_file())
        self.assertTrue((example_root / "export" / "MQTT Client.py").is_file())
        self.assertTrue(
            (example_root / "export" / "mqtt_client" / "mqtt_transport.py").is_file()
        )
        with mock_patch.object(
            TextReportDialog,
            "exec",
            return_value=QDialog.DialogCode.Rejected,
        ) as tutorial_exec:
            self.window.mqtt_tutorial_help_action.trigger()
        tutorial_exec.assert_called_once_with()

    def test_new_from_preset_respects_the_dirty_project_guard(self) -> None:
        """Do not replace an unsaved project after the user cancels confirmation."""
        original = self.window.designer_session.project
        self.window.designer_session.dirty = True
        with (
            mock_patch.object(
                AppPresetDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            mock_patch.object(
                AppPresetDialog,
                "settings",
                return_value=("focus_timer", "Focus Timer", "PicoCalc 320x320"),
            ),
            mock_patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Cancel,
            ),
        ):
            self.window._new_gui_project_from_preset()

        self.assertIs(self.window.designer_session.project, original)

    def test_open_path_routes_each_document_to_its_workspace(self) -> None:
        """Open GUI projects in App GUI and Python sources in Pixel Art."""
        project_path = Path(self.temporary.name) / "routing.picogui.json"
        GuiProject.create("Routing").save(project_path)

        self.window.open_path(project_path)
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.app_gui_tab_index,
        )

        self.window.open_path(self.source_path)
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.pixel_art_tab_index,
        )

    def test_app_gui_interaction_handoff_prepares_screen_flow(self) -> None:
        """Continue from a selected App GUI element without making users rebuild context."""
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        self.window.screen_designer._add_element("button")
        screen = self.window.designer_session.current_screen()
        element = screen.elements[-1]

        with mock_patch.object(
            self.window.screen_flow,
            "create_behavior_from_element_dialog",
            return_value=False,
        ):
            self.window.screen_designer._open_selected_element_flow()

        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.screen_flow_tab_index,
        )
        self.assertTrue(self.window.screen_flow.manual_relation_group.isChecked())
        self.assertEqual(
            self.window.screen_flow.source_combo.currentData(),
            flow_endpoint_key(screen.id, element.id),
        )
        self.assertIn(
            "selected App GUI element", self.window.statusBar().currentMessage()
        )
        self.window.designer_session.dirty = False

    def test_project_exposes_and_persists_asset_storage_mode(self) -> None:
        """Make combined versus individual deployment an obvious project choice."""
        combo = self.window.screen_designer.asset_storage_combo
        index = combo.findData("individual")
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)
        self.assertEqual(
            self.window.designer_session.project.generated_app["asset_storage"],
            "individual",
        )
        restored = GuiProject.from_dict(self.window.designer_session.project.to_dict())
        self.assertEqual(restored.generated_app["asset_storage"], "individual")
        self.assertEqual(
            [self.window.workspace_tabs.tabText(index) for index in range(5)],
            ["App GUI", "Screen Flow", "Simulator", "Pixel Art", "Asset Library"],
        )
        self.assertFalse(self.window.import_existing_app_action.isVisible())
        self.assertFalse(self.window.apply_imported_app_action.isEnabled())
        self.assertEqual(
            self.window.export_generated_app_action.text(),
            "Export Generated App Structure v1...",
        )
        self.assertEqual(
            self.window.export_gui_action.text(),
            "Export GUI to Python (Legacy)...",
        )
        self.assertFalse(self.window.export_generated_app_action.isVisible())
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        self.assertTrue(self.window.import_existing_app_action.isEnabled())
        self.assertTrue(self.window.export_generated_app_action.isVisible())

    def test_main_menus_follow_the_five_workspace_information_architecture(
        self,
    ) -> None:
        """Keep global menus stable and expose exactly one contextual workspace menu."""
        expected = {
            self.window.app_gui_tab_index: [
                "File",
                "Edit",
                "View",
                "Project",
                "App GUI",
                "Simulator",
                "Help",
            ],
            self.window.screen_flow_tab_index: [
                "File",
                "Edit",
                "View",
                "Project",
                "Screen Flow",
                "Simulator",
                "Help",
            ],
            self.window.simulator_tab_index: [
                "File",
                "View",
                "Project",
                "Simulator",
                "Help",
            ],
            self.window.pixel_art_tab_index: [
                "File",
                "Edit",
                "View",
                "Pixel Art",
                "Simulator",
                "Help",
            ],
            self.window.library_tab_index: [
                "File",
                "Edit",
                "View",
                "Asset Library",
                "Simulator",
                "Help",
            ],
        }
        for index, menu_names in expected.items():
            self.window.workspace_tabs.setCurrentIndex(index)
            self.application.processEvents()
            self.assertEqual(
                [
                    action.text()
                    for action in self.window.menuBar().actions()
                    if action.isVisible()
                ],
                menu_names,
            )
            self.assertTrue(self.window.workspace_actions[index].isChecked())

    def test_active_open_close_and_save_labels_are_workspace_specific(self) -> None:
        """Never let standard document shortcuts target a hidden workspace."""
        self.assertEqual(self.window.open_active_action.shortcut().toString(), "Ctrl+O")
        self.assertEqual(
            self.window.close_active_action.shortcut().toString(), "Ctrl+W"
        )
        self.window.workspace_tabs.setCurrentIndex(self.window.pixel_art_tab_index)
        self.window._update_file_menu()
        self.assertEqual(
            self.window.open_active_action.text(), "Open Pixel Art Source..."
        )
        self.assertEqual(
            self.window.save_as_active_action.text(), "Export Pixel Asset as PNG..."
        )
        with mock_patch.object(self.window, "_open_file") as open_pixel:
            self.window._open_active_document()
        open_pixel.assert_called_once_with()

        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        self.window._update_file_menu()
        self.assertEqual(self.window.open_active_action.text(), "Open GUI Project...")
        self.assertEqual(self.window.close_active_action.text(), "Close GUI Project")
        self.assertEqual(self.window.save_active_action.text(), "Save GUI Project")
        with mock_patch.object(self.window, "_open_gui_project") as open_project:
            self.window._open_active_document()
        open_project.assert_called_once_with()

        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)
        self.window._update_file_menu()
        self.assertEqual(
            self.window.open_active_action.text(),
            "Import Image into Asset Library...",
        )
        self.assertFalse(self.window.close_active_action.isEnabled())

    def test_close_active_document_never_closes_a_hidden_source_or_project(
        self,
    ) -> None:
        """Apply Ctrl+W semantics only to the visible workspace document."""
        folder = Path(self.temporary.name) / "close-active-source"
        folder.mkdir()
        (folder / "asset.py").write_text(ANIMATION_SOURCE, encoding="utf-8")
        self.window.open_path(folder)
        source_path = self.window._scan_path
        self.window.designer_session.set_project(GuiProject.create("Close Me"))
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        self.window._close_active_document()
        self.assertEqual(self.window._scan_path, source_path)
        self.assertEqual(self.window.designer_session.project.name, "Untitled GUI")

        project_id = self.window.designer_session.project.project_id
        self.window.workspace_tabs.setCurrentIndex(self.window.pixel_art_tab_index)
        self.window._close_active_document()
        self.assertIsNone(self.window._scan_path)
        self.assertEqual(self.window.designer_session.project.project_id, project_id)

    def test_workspace_shortcuts_and_context_menus_expose_primary_operations(
        self,
    ) -> None:
        """Expose navigation, layer, flow-layout, Library, validation, and Help commands."""
        self.assertEqual(
            [action.shortcut().toString() for action in self.window.workspace_actions],
            ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5"],
        )
        self.window.workspace_actions[4].trigger()
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(), self.window.library_tab_index
        )
        self.assertIn(
            "Import Images from PGA...",
            {action.text() for action in self.window.library_menu.actions()},
        )
        self.assertEqual(
            set(action.text() for action in self.window.app_layer_actions.values()),
            {
                "Bring to Front",
                "Move Forward One Layer",
                "Move Backward One Layer",
                "Send to Back",
            },
        )
        self.assertIn(
            self.window.flow_auto_layout_action, self.window.flow_menu.actions()
        )
        self.assertIn(
            self.window.validate_project_action, self.window.gui_menu.actions()
        )
        self.assertIn(
            self.window.shortcuts_help_action, self.window.help_menu.actions()
        )

    def test_recent_documents_are_bounded_deduplicated_and_non_destructive(
        self,
    ) -> None:
        """Remember successful paths and clear only their metadata."""
        self.window.settings.remove("recent/paths")
        first = Path(self.temporary.name) / "first.py"
        second = Path(self.temporary.name) / "second.picogui.json"
        first.write_text("# first\n", encoding="utf-8")
        second.write_text("{}\n", encoding="utf-8")
        self.window._remember_recent_path(first)
        self.window._remember_recent_path(second)
        self.window._remember_recent_path(first)
        self.assertEqual(
            self.window._recent_paths(), [first.resolve(), second.resolve()]
        )
        self.window._rebuild_recent_menu()
        recent_data = [
            action.data()
            for action in self.window.recent_menu.actions()
            if action.data()
        ]
        self.assertEqual(recent_data, [str(first.resolve()), str(second.resolve())])
        self.window._clear_recent_paths()
        self.assertEqual(self.window._recent_paths(), [])
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    def test_project_preflight_reports_invalid_asset_scale_before_run(self) -> None:
        """Collect a generator-blocking asset placement before simulation or export."""
        project = GuiProject.create("Invalid Scale")
        art = PixelArt(3, 2)
        asset = ProjectAsset.from_pixel_art("asset-scale", "Scale Asset", art)
        project.assets.append(asset)
        element = GuiElement.create("icon", 1)
        element.asset_id = asset.id
        element.width = 5
        element.height = 4
        project.screens[0].elements.append(element)
        self.window.designer_session.set_project(project)
        with mock_patch.object(TextReportDialog, "exec", return_value=0):
            self.assertFalse(self.window._validate_gui_project())
        with (
            mock_patch.object(QMessageBox, "critical") as critical,
            mock_patch.object(
                self.window.simulator_workspace, "run_current_design"
            ) as run_design,
        ):
            self.assertFalse(self.window._run_current_design())
        critical.assert_called_once()
        run_design.assert_not_called()

        element.width = 6
        element.height = 4
        with mock_patch.object(TextReportDialog, "exec", return_value=0):
            self.assertTrue(self.window._validate_gui_project())

    def test_simulator_has_global_run_entry_and_persistent_state(self) -> None:
        """Run from App GUI and keep process state visible outside Simulator."""
        run_actions = {action.text() for action in self.window.run_menu.actions()}
        self.assertIn("Run Current GUI Project", run_actions)
        self.assertIn("Open Device Simulator", run_actions)
        self.assertIn("Restart Simulator", run_actions)
        self.assertEqual(
            self.window.run_current_design_action.shortcut().toString(),
            "Ctrl+Return",
        )
        with mock_patch.object(
            self.window.simulator_workspace,
            "show_design_preview",
        ) as show_preview:
            self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
            self.window.show()
            self.application.processEvents()
            self.window.screen_designer.design_preview_button.click()
        show_preview.assert_called_once_with()
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.simulator_tab_index,
        )
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        with mock_patch.object(
            self.window.simulator_workspace,
            "run_current_design",
            return_value=True,
        ) as run_design:
            self.window.document_run_button.click()
        run_design.assert_called_once_with()
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.simulator_tab_index,
        )
        self.window._simulator_running_changed(True)
        self.assertEqual(
            self.window.workspace_tabs.tabText(self.window.simulator_tab_index),
            "Simulator ●",
        )
        self.assertEqual(
            self.window.document_simulator_button.text(),
            "Simulator ● Running",
        )
        self.assertTrue(self.window.restart_simulator_action.isEnabled())
        self.assertFalse(self.window.run_current_design_action.isEnabled())
        self.window._simulator_error_changed("Generated app failed")
        self.assertEqual(
            self.window.workspace_tabs.tabText(self.window.simulator_tab_index),
            "Simulator !",
        )
        self.assertTrue(self.window.copy_simulator_error_action.isEnabled())
        self.window.simulator_workspace._last_error = ""
        self.window._simulator_running_changed(False)

    def test_open_source_folder_can_be_closed_without_touching_other_data(self) -> None:
        """Close the scanned source scope while retaining GUI and library records."""
        folder = Path(self.temporary.name) / "source-folder"
        folder.mkdir()
        (folder / "asset.py").write_text(ANIMATION_SOURCE, encoding="utf-8")
        library_path = Path(self.temporary.name) / "close-source-library.json"
        self.window._pixel_recovery_path = lambda: (
            Path(self.temporary.name) / "close-source-recovery.json"
        )
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(2, 1)
        art.set_pixel(0, 0, 0xF800)
        self.window.asset_library.add("Kept Asset", [art])
        self.window._refresh_personal_asset_library()
        self.window.screen_designer._add_element("button")
        project_id = self.window.designer_session.project.project_id
        self.window.open_path(folder)
        self.assertEqual(self.window._scan_path, folder.resolve())
        self.assertTrue(self.window._scan_folder)
        self.assertTrue(self.window.close_source_action.isEnabled())
        self.assertTrue(self.window.close_source_button.isEnabled())
        self.window._close_source()
        self.assertIsNone(self.window._scan_path)
        self.assertFalse(self.window._scan_folder)
        self.assertEqual(self.window.assets, [])
        self.assertEqual(self.window.asset_list.count(), 0)
        self.assertFalse(self.window.close_source_action.isEnabled())
        self.assertEqual(self.window.designer_session.project.project_id, project_id)
        self.assertEqual(len(self.window.designer_session.current_screen().elements), 1)
        self.assertEqual(self.window.asset_library.assets()[0].name, "Kept Asset")
        self.window.designer_session.dirty = False

    def test_library_workspace_manages_and_inserts_existing_assets(self) -> None:
        """Browse, animate, rename, insert, filter, and remove reusable records."""
        library_path = Path(self.temporary.name) / "workspace-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        first = PixelArt(3, 2)
        first.set_pixel(0, 0, 0xF800)
        second = PixelArt(3, 2)
        second.set_pixel(2, 1, 0x07E0)
        stored = self.window.asset_library.add(
            "Animated Badge", [first, second], [120, 240]
        )
        self.window._refresh_personal_asset_library()
        library = self.window.library_workspace
        self.assertEqual(library.asset_list.count(), 301)
        self.assertEqual(library.selected_asset_id(), stored.id)
        self.assertIn("Frame 1 of 2", library.frame_label.text())
        library.next_frame_button.click()
        self.assertIn("Frame 2 of 2", library.frame_label.text())
        library.search_edit.setText("missing")
        self.assertTrue(library.asset_list.item(0).isHidden())
        self.assertEqual(library.count_label.text(), "0 of 301 assets")
        self.assertEqual(library.selected_asset_id(), "")
        self.assertFalse(library.delete_button.isEnabled())
        self.assertIn("No assets match", library.empty_label.text())
        library.search_edit.clear()
        with mock_patch.object(
            QInputDialog, "getText", return_value=("Renamed Badge", True)
        ):
            library.rename_button.click()
        self.assertEqual(
            self.window.asset_library.asset(stored.id).name, "Renamed Badge"
        )
        library.add_to_project_button.click()
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.app_gui_tab_index,
        )
        element = self.window.designer_session.current_screen().elements[-1]
        self.assertEqual(element.asset_link_state, "detached")
        with mock_patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)
            library.delete_button.click()
        self.assertEqual(self.window.asset_library.assets(), ())
        self.assertEqual(library.asset_list.count(), 300)
        self.window.designer_session.dirty = False

    def test_library_workspace_imports_images_without_an_open_folder(self) -> None:
        """Import a reusable raster directly from the dedicated Library tab."""
        library_path = Path(self.temporary.name) / "direct-import-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        self.window._refresh_personal_asset_library()
        image_path = Path(self.temporary.name) / "direct.png"
        image = QImage(5, 4, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFF0000)
        imported = PixelArt(5, 4)
        imported.pixels = [0xF800] * 20
        self.window._close_source()
        with (
            mock_patch(
                "pico_graphics_editor.window.get_open_image_filename",
                return_value=(str(image_path), "Images (*.png)"),
            ),
            mock_patch(
                "pico_graphics_editor.window.read_image_frames_with_durations",
                return_value=([image], ()),
            ),
            mock_patch.object(
                self.window,
                "_review_library_images",
                return_value=LibraryImageImportResult(
                    "Direct Import",
                    (imported,),
                    (),
                    5,
                    4,
                    16,
                    False,
                    250,
                ),
            ),
        ):
            self.window.library_workspace.import_button.click()
        record = self.window.asset_library.assets()[0]
        self.assertEqual(record.name, "Direct Import")
        self.assertEqual((record.width, record.height), (5, 4))
        self.assertEqual(self.window.library_workspace.asset_list.count(), 301)
        self.assertEqual(self.window.library_workspace.selected_asset_id(), record.id)

    def test_library_import_numbers_names_conflicting_with_builtin_and_personal(
        self,
    ) -> None:
        """Never show two catalogue records with the same case-insensitive name."""
        library_path = Path(self.temporary.name) / "unique-import-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(1, 1)
        image = QImage(1, 1, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFFFFFF)
        review = LibraryImageImportResult(
            "Home",
            (art,),
            (),
            1,
            1,
            16,
            False,
            250,
        )
        with (
            mock_patch(
                "pico_graphics_editor.window.get_open_image_filename",
                return_value=("/tmp/home.png", "Images (*.png)"),
            ),
            mock_patch(
                "pico_graphics_editor.window.read_image_frames_with_durations",
                return_value=([image], ()),
            ),
            mock_patch.object(
                self.window, "_review_library_images", return_value=review
            ),
        ):
            self.window._import_image_to_asset_library()
            self.window._import_image_to_asset_library()

        self.assertEqual(
            [record.name for record in self.window.asset_library.assets()],
            ["Home 2", "Home 3"],
        )
        visible_names = [
            asset.name for asset in self.window.library_workspace.assets.values()
        ]
        self.assertEqual(
            len(visible_names), len({name.casefold() for name in visible_names})
        )

    def test_library_copy_and_rename_show_numbered_names_consistently(self) -> None:
        """Suggest the stored copy name and report an adjusted manual rename."""
        library_path = Path(self.temporary.name) / "numbered-dialog-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        self.window._refresh_personal_asset_library()
        library = self.window.library_workspace
        home = self.window.standard_library_assets[0]
        library.select_asset(home.id)
        suggested: list[str] = []

        def accept_suggestion(*unused_args, **kwargs):
            del unused_args
            suggested.append(str(kwargs["text"]))
            return kwargs["text"], True

        with mock_patch.object(QInputDialog, "getText", side_effect=accept_suggestion):
            library.duplicate_button.click()

        self.assertEqual(suggested, ["Home 2"])
        copied = self.window.asset_library.assets()[0]
        self.assertEqual(copied.name, "Home 2")
        other = self.window.asset_library.add("Other", [PixelArt(1, 1)])
        self.window._refresh_personal_asset_library(other.id)
        with mock_patch.object(QInputDialog, "getText", return_value=("Home 2", True)):
            library.rename_button.click()

        self.assertEqual(self.window.asset_library.asset(other.id).name, "Home 3")
        self.assertIn("already used", self.window.statusBar().currentMessage())

    def test_legacy_personal_name_collision_keeps_both_records_distinguishable(
        self,
    ) -> None:
        """Display old personal names safely without rewriting their stored file."""
        library_path = Path(self.temporary.name) / "legacy-name-library.json"
        unconfigured = AssetLibrary(library_path)
        personal = unconfigured.add("Home", [PixelArt(1, 1)])
        before = library_path.read_bytes()
        self.window.asset_library = AssetLibrary(library_path)

        self.window._refresh_personal_asset_library()

        names = [asset.name for asset in self.window.library_workspace.assets.values()]
        self.assertIn("Home", names)
        self.assertIn("Home · Built-in", names)
        self.assertEqual(len(names), len({name.casefold() for name in names}))
        self.assertEqual(self.window.asset_library.asset(personal.id).name, "Home")
        self.assertEqual(library_path.read_bytes(), before)

    def test_library_workspace_imports_every_image_from_pga3(self) -> None:
        """Expose lossless PGA3 recovery and store independent library copies."""
        library_path = Path(self.temporary.name) / "pga-import-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        self.window._refresh_personal_asset_library()
        first = PixelArt(2, 1, -2, 4)
        first.set_pixel(0, 0, 0x0000)
        second = PixelArt(2, 1, -2, 4)
        second.set_pixel(1, 0, 0xF800)
        static = PixelArt(1, 1)
        static.set_pixel(0, 0, 0x07E0)
        resource = generate_asset_resource(
            "import-project",
            "8.0",
            (
                GeneratedRasterEntry(
                    "asset-animation", "Animation", (first, second), (80, 160)
                ),
                GeneratedRasterEntry("asset-static", "Static", (static,)),
            ),
        )
        resource_path = Path(self.temporary.name) / "generated_assets.pga"
        resource_path.write_bytes(resource.data)
        with (
            mock_patch.object(
                QFileDialog,
                "getOpenFileName",
                return_value=(str(resource_path), "Picoware generated assets (*.pga)"),
            ),
            mock_patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            mock_patch.object(QMessageBox, "information"),
        ):
            self.window.library_workspace.import_pga_button.click()
        records = {record.name: record for record in self.window.asset_library.assets()}
        self.assertEqual(set(records), {"Animation", "Static"})
        self.assertEqual(records["Animation"].pixel_frames(), (first, second))
        self.assertEqual(records["Animation"].durations, (80, 160))
        self.assertEqual(records["Static"].pixel_frames(), (static,))
        self.assertEqual(self.window.library_workspace.asset_list.count(), 302)
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(), self.window.library_tab_index
        )

    def test_library_workspace_full_management_actions_are_explicit(self) -> None:
        """Duplicate, replace, export, copy metadata, and edit a detached frame copy."""
        library_path = Path(self.temporary.name) / "full-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(2, 2)
        art.set_pixel(0, 0, 0xF800)
        stored = self.window.asset_library.add("Badge", [art])
        self.window._refresh_personal_asset_library()
        library = self.window.library_workspace
        self.assertEqual(
            library.library_path_label.text(), "Library file: full-library.json"
        )
        self.assertIn(str(library_path), library.library_path_label.toolTip())
        library.copy_id_button.click()
        self.assertEqual(QApplication.clipboard().text(), stored.id)
        library.copy_fingerprint_button.click()
        self.assertEqual(QApplication.clipboard().text(), stored.fingerprint)
        library.display_mode_combo.setCurrentIndex(
            library.display_mode_combo.findData("list")
        )
        self.assertEqual(
            library.asset_list.viewMode(), library.asset_list.ViewMode.ListMode
        )
        with mock_patch.object(
            QInputDialog, "getText", return_value=("Badge Variant", True)
        ):
            library.duplicate_button.click()
        self.assertEqual(
            {record.name for record in self.window.asset_library.assets()},
            {"Badge", "Badge Variant"},
        )
        self.assertEqual(library.selected_asset().name, "Badge Variant")
        self.assertTrue(library.select_asset(stored.id))

        export_path = Path(self.temporary.name) / "badge-export.png"
        with mock_patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(export_path), "PNG images (*.png)"),
        ):
            library.export_button.click()
        self.assertTrue(export_path.is_file())

        replacement_image = QImage(4, 3, QImage.Format.Format_RGBA8888)
        replacement_image.fill(0xFF00FF00)
        replacement = PixelArt(4, 3)
        replacement.pixels = [0x07E0] * 12
        with (
            mock_patch(
                "pico_graphics_editor.window.get_open_image_filename",
                return_value=("replacement.png", "Images (*.png)"),
            ),
            mock_patch(
                "pico_graphics_editor.window.read_image_frames_with_durations",
                return_value=([replacement_image], ()),
            ),
            mock_patch.object(
                self.window,
                "_review_library_images",
                return_value=LibraryImageImportResult(
                    "Badge",
                    (replacement,),
                    (),
                    4,
                    3,
                    16,
                    False,
                    250,
                ),
            ),
            mock_patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
        ):
            library.replace_button.click()
        replaced = self.window.asset_library.asset(stored.id)
        self.assertEqual((replaced.width, replaced.height), (4, 3))
        self.assertEqual(replaced.id, stored.id)

        library.edit_copy_button.click()
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.pixel_art_tab_index,
        )
        self.assertFalse(self.window._dirty)
        self.assertIn("LIBRARY ASSET", self.window.asset_mode_label.text())
        self.assertEqual(
            (self.window.canvas.art().width, self.window.canvas.art().height),
            (4, 3),
        )
        edited = self.window.canvas.art().copy()
        edited.set_pixel(0, 0, 0x001F)
        self.window.canvas.set_art(edited)
        self.assertTrue(self.window._dirty)
        self.assertTrue(self.window._apply_to_source())
        self.assertEqual(
            self.window.asset_library.asset(stored.id).pixel_frames()[0].pixel(0, 0),
            0x001F,
        )
        self.window._dirty = False
        self.window._pending_image_path = None

    def test_library_double_click_edits_without_mutating_current_project(self) -> None:
        """Reserve project insertion for the explicit button or Enter shortcut."""
        library_path = Path(self.temporary.name) / "double-click-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(3, 2)
        art.set_pixel(1, 1, 0xF800)
        self.window.asset_library.add("Editable", [art])
        self.window._refresh_personal_asset_library()
        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)
        item = self.window.library_workspace.asset_list.currentItem()
        before_elements = len(self.window.designer_session.current_screen().elements)

        self.window.library_workspace.asset_list.itemDoubleClicked.emit(item)

        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.pixel_art_tab_index,
        )
        self.assertEqual(
            len(self.window.designer_session.current_screen().elements), before_elements
        )
        self.assertEqual(
            self.window._editing_library_asset_id,
            item.data(Qt.ItemDataRole.UserRole),
        )

    def test_library_read_error_is_persistent_and_retry_recovers(self) -> None:
        """Never present a damaged library as a harmless empty catalogue."""
        library_path = Path(self.temporary.name) / "damaged-library.json"
        library_path.write_text("{broken", encoding="utf-8")
        self.window.asset_library = AssetLibrary(library_path)
        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)

        self.window._refresh_personal_asset_library()

        library = self.window.library_workspace
        self.assertFalse(library.storage_available())
        self.assertFalse(library.state_panel.isHidden())
        self.assertIn("Library unavailable", library.state_label.text())
        self.assertFalse(library.import_button.isEnabled())
        self.assertFalse(self.window.library_import_image_action.isEnabled())

        library_path.unlink()
        self.window._refresh_personal_asset_library()

        self.assertTrue(library.storage_available())
        self.assertTrue(library.import_button.isEnabled())
        self.assertFalse(library.empty_label.isVisible())
        self.assertEqual(library.count_label.text(), "300 assets")

    def test_external_refresh_discards_stale_history_without_losing_new_assets(
        self,
    ) -> None:
        """Never restore a full snapshot over changes written by another instance."""
        library_path = Path(self.temporary.name) / "external-history-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(2, 2)
        base = self.window.asset_library.add("Base", [art])
        self.window._refresh_personal_asset_library(base.id)
        before = self.window._library_history_snapshot()
        local = self.window.asset_library.add("Local", [art])
        self.window._record_library_change("Added Local", before, local.id)
        AssetLibrary(library_path).add("External", [art])

        self.window._refresh_personal_asset_library()

        self.assertEqual(
            {asset.name for asset in self.window.asset_library.assets()},
            {"Base", "External", "Local"},
        )
        self.assertEqual(self.window._library_undo_stack, [])
        self.assertFalse(self.window.library_undo_action.isEnabled())

    def test_direct_stale_undo_is_cancelled_before_overwriting_external_changes(
        self,
    ) -> None:
        """Guard Ctrl+Z even when the user has not refreshed the catalogue first."""
        library_path = Path(self.temporary.name) / "stale-undo-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(2, 2)
        base = self.window.asset_library.add("Base", [art])
        self.window._refresh_personal_asset_library(base.id)
        before = self.window._library_history_snapshot()
        local = self.window.asset_library.add("Local", [art])
        self.window._record_library_change("Added Local", before, local.id)
        AssetLibrary(library_path).add("External", [art])

        with mock_patch.object(QMessageBox, "warning") as warning:
            self.window._undo_library_change()

        self.assertEqual(
            {asset.name for asset in self.window.asset_library.assets()},
            {"Base", "External", "Local"},
        )
        self.assertEqual(self.window._library_undo_stack, [])
        warning.assert_called_once()

    def test_deleted_open_library_master_becomes_a_safe_unlinked_copy(self) -> None:
        """Keep open pixels without leaving Update linked to a missing record."""
        library_path = Path(self.temporary.name) / "open-master-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(2, 2)
        art.set_pixel(0, 0, 0xF800)
        stored = self.window.asset_library.add("Open Master", [art])
        self.window._refresh_personal_asset_library(stored.id)
        self.window._edit_library_asset_copy(stored.id)
        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)

        with mock_patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window._delete_library_asset(stored.id)

        self.assertEqual(self.window._editing_library_asset_id, "")
        self.assertIn("UNLINKED LIBRARY COPY", self.window.asset_mode_label.text())
        self.assertEqual(self.window.canvas.art().pixel(0, 0), 0xF800)
        self.assertIsNone(self.window.asset_library.asset(stored.id))

    def test_changed_open_library_master_cannot_overwrite_newer_pixels(self) -> None:
        """Detach a stale canvas when another writer replaces its stored master."""
        library_path = Path(self.temporary.name) / "changed-master-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        original = PixelArt(2, 2)
        original.set_pixel(0, 0, 0xF800)
        stored = self.window.asset_library.add("Open Master", [original])
        self.window._refresh_personal_asset_library(stored.id)
        self.window._edit_library_asset_copy(stored.id)
        replacement = PixelArt(2, 2)
        replacement.set_pixel(0, 0, 0x07E0)

        AssetLibrary(library_path).replace(stored.id, [replacement])
        self.window._refresh_personal_asset_library(stored.id)

        self.assertEqual(self.window._editing_library_asset_id, "")
        self.assertEqual(self.window.canvas.art().pixel(0, 0), 0xF800)
        self.assertEqual(
            self.window.asset_library.asset(stored.id).pixel_frames()[0].pixel(0, 0),
            0x07E0,
        )

    def test_library_error_disables_every_history_entry_point(self) -> None:
        """Keep panel, menu, and global undo consistent while storage is invalid."""
        library_path = Path(self.temporary.name) / "history-error-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(1, 1)
        base = self.window.asset_library.add("Base", [art])
        self.window._refresh_personal_asset_library(base.id)
        before = self.window._library_history_snapshot()
        added = self.window.asset_library.add("Added", [art])
        self.window._record_library_change("Added asset", before, added.id)
        library_path.write_text("{broken", encoding="utf-8")
        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)

        self.window._refresh_personal_asset_library()
        self.window._update_history_actions()

        self.assertFalse(self.window.library_workspace.undo_button.isEnabled())
        self.assertFalse(self.window.library_undo_action.isEnabled())
        self.assertFalse(self.window.library_redo_action.isEnabled())
        self.assertFalse(self.window.undo_action.isEnabled())
        self.assertFalse(self.window.redo_action.isEnabled())

    def test_library_delete_can_be_undone_and_redone(self) -> None:
        """Recover complete auto-saved records through active-workspace history."""
        library_path = Path(self.temporary.name) / "undo-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(2, 2, -1, 3)
        art.set_pixel(0, 0, 0xF800)
        stored = self.window.asset_library.add("Recover Me", [art])
        self.window._refresh_personal_asset_library()
        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)
        with mock_patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.library_workspace.delete_button.click()
        self.assertEqual(self.window.asset_library.assets(), ())
        self.assertTrue(self.window.undo_action.isEnabled())
        self.assertFalse(self.window.library_workspace.undo_button.isHidden())

        self.window._undo_current()

        restored = self.window.asset_library.asset(stored.id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.pixel_frames()[0], art)
        self.assertTrue(self.window.redo_action.isEnabled())
        self.assertFalse(self.window.library_workspace.redo_button.isHidden())
        self.assertTrue(self.window.library_workspace.undo_button.isHidden())

        self.window.library_workspace.redo_button.click()

        self.assertEqual(self.window.asset_library.assets(), ())

    def test_library_animation_playback_advances_and_stops_on_workspace_change(
        self,
    ) -> None:
        """Preview complete animations using their stored frame durations."""
        library_path = Path(self.temporary.name) / "playback-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        first = PixelArt(2, 1)
        second = PixelArt(2, 1)
        first.set_pixel(0, 0, 0xF800)
        second.set_pixel(1, 0, 0x07E0)
        self.window.asset_library.add("Spinner", [first, second], [90, 180])
        self.window._refresh_personal_asset_library()
        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)
        library = self.window.library_workspace
        library.play_button.setChecked(True)
        self.assertTrue(library.play_timer.isActive())

        library._advance_playback()

        self.assertEqual(library.selected_frame_index(), 1)
        self.assertIn("180 ms", library.frame_label.text())
        self.window.workspace_tabs.setCurrentIndex(self.window.pixel_art_tab_index)
        self.assertFalse(library.play_button.isChecked())
        self.assertFalse(library.play_timer.isActive())

    def test_library_view_preferences_and_scoped_shortcuts(self) -> None:
        """Restore layout choices and keep power actions inside the Library widget."""
        settings = QSettings(
            str(Path(self.temporary.name) / "library-ui.ini"),
            QSettings.Format.IniFormat,
        )
        first = PersonalAssetLibraryWidget()
        first.display_mode_combo.setCurrentIndex(
            first.display_mode_combo.findData("list")
        )
        first.collection_combo.setCurrentIndex(
            first.collection_combo.findData("personal")
        )
        first.theme_combo.setCurrentIndex(first.theme_combo.findData("feminine"))
        first.asset_kind_combo.setCurrentIndex(
            first.asset_kind_combo.findData("widget")
        )
        first.splitter.setSizes((420, 360))
        first.management_group.setChecked(True)
        first.technical_group.setChecked(True)
        first.save_ui_state(settings)
        settings.sync()

        second = PersonalAssetLibraryWidget()
        second.restore_ui_state(settings)

        self.assertEqual(second.display_mode_combo.currentData(), "list")
        self.assertEqual(second.collection_combo.currentData(), "personal")
        self.assertEqual(second.theme_combo.currentData(), "feminine")
        self.assertEqual(second.asset_kind_combo.currentData(), "widget")
        self.assertTrue(second.management_group.isChecked())
        self.assertTrue(second.technical_group.isChecked())
        art = PixelArt(2, 2)
        record = LibraryAsset.from_frames("library_shortcut", "Shortcut", (art,))
        second.set_assets((record,))
        adds: list[str] = []
        renames: list[str] = []
        deletes: list[str] = []
        second.add_to_project_requested.connect(adds.append)
        second.rename_requested.connect(renames.append)
        second.delete_requested.connect(deletes.append)
        second.add_shortcut.activated.emit()
        second.rename_shortcut.activated.emit()
        second.delete_shortcut.activated.emit()
        self.assertEqual(adds, [(record.id,)])
        self.assertEqual(renames, [record.id])
        self.assertEqual(deletes, [(record.id,)])
        first.close()
        second.close()

    def test_library_ships_300_filterable_read_only_standard_assets(self) -> None:
        """Give a new user complete starter and themed design systems."""
        library_path = Path(self.temporary.name) / "standard-only-library.json"
        self.window.asset_library = AssetLibrary(library_path)

        self.window._refresh_personal_asset_library()

        library = self.window.library_workspace
        self.assertEqual(len(self.window.standard_library_assets), 300)
        self.assertEqual(library.asset_list.count(), 300)
        self.assertEqual(len(library.standard_asset_ids), 300)
        self.assertEqual(library.personal_asset_ids, set())
        designer = self.window.screen_designer
        self.assertEqual(designer.asset_tabs.currentIndex(), 0)
        self.assertEqual(designer.asset_tabs.tabText(0), "Library (300)")
        self.assertEqual(designer.library_asset_list.count(), 300)
        self.assertTrue(designer.library_empty_label.isHidden())
        library.collection_combo.setCurrentIndex(
            library.collection_combo.findData("standard")
        )
        self.assertEqual(library.count_label.text(), "300 of 300 assets")
        library.collection_combo.setCurrentIndex(
            library.collection_combo.findData("personal")
        )
        self.assertEqual(library.count_label.text(), "0 of 300 assets")

        library.collection_combo.setCurrentIndex(
            library.collection_combo.findData("standard")
        )
        library.theme_combo.setCurrentIndex(library.theme_combo.findData("creative"))
        self.assertEqual(library.count_label.text(), "50 of 300 assets")
        library.asset_kind_combo.setCurrentIndex(
            library.asset_kind_combo.findData("background")
        )
        self.assertEqual(library.count_label.text(), "6 of 300 assets")

        designer.library_theme_combo.setCurrentIndex(
            designer.library_theme_combo.findData("creative")
        )
        designer.library_kind_combo.setCurrentIndex(
            designer.library_kind_combo.findData("button")
        )
        self.assertEqual(
            sum(
                not designer.library_asset_list.item(row).isHidden()
                for row in range(designer.library_asset_list.count())
            ),
            12,
        )
        library.theme_combo.setCurrentIndex(0)
        library.asset_kind_combo.setCurrentIndex(0)
        designer.library_theme_combo.setCurrentIndex(0)
        designer.library_kind_combo.setCurrentIndex(0)

    def test_library_multi_selection_adds_an_arranged_single_undo_batch(self) -> None:
        """Add Ctrl-selected assets together instead of stacking them at one point."""
        library_path = Path(self.temporary.name) / "multi-add-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        self.window._refresh_personal_asset_library()
        library = self.window.library_workspace
        library.asset_list.clearSelection()
        selected_items = [library.asset_list.item(index) for index in range(3)]
        library.asset_list.setCurrentItem(selected_items[0])
        for item in selected_items:
            item.setSelected(True)
        self.application.processEvents()
        self.assertFalse(library.preview_label.pixmap().isNull())
        self.assertIn("Home", library.preview_label.toolTip())
        before = len(self.window.designer_session.current_screen().elements)

        library.add_to_project_button.click()

        screen = self.window.designer_session.current_screen()
        added = screen.elements[before:]
        self.assertEqual(len(added), 3)
        self.assertEqual(len({(element.x, element.y) for element in added}), 3)
        self.assertEqual(len(self.window.designer_session._undo_stack), 1)
        self.window.designer_session.undo()
        self.assertEqual(
            len(self.window.designer_session.current_screen().elements), before
        )

    def test_library_multi_selection_copies_exports_and_deletes_as_batches(
        self,
    ) -> None:
        """Apply file-manager batch operations while built-in originals stay safe."""
        library_path = Path(self.temporary.name) / "multi-manage-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        self.window._refresh_personal_asset_library()
        library = self.window.library_workspace
        library.asset_list.clearSelection()
        standards = [library.asset_list.item(index) for index in range(2)]
        library.asset_list.setCurrentItem(standards[0])
        for item in standards:
            item.setSelected(True)
        self.application.processEvents()

        with mock_patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            library.duplicate_button.click()

        personal = self.window.asset_library.assets()
        self.assertEqual(len(personal), 2)
        self.assertEqual(library.asset_list.count(), 302)

        export_folder = Path(self.temporary.name) / "batch-export"
        export_folder.mkdir()
        library.asset_list.clearSelection()
        library.asset_list.setCurrentItem(standards[0])
        for item in standards:
            item.setSelected(True)
        with mock_patch.object(
            QFileDialog,
            "getExistingDirectory",
            return_value=str(export_folder),
        ):
            library.export_button.click()
        self.assertEqual(len(tuple(export_folder.glob("*.png"))), 2)

        library.asset_list.clearSelection()
        library.asset_list.setCurrentItem(library._catalogue_items[personal[0].id])
        standards[0].setSelected(True)
        for asset in personal:
            library._catalogue_items[asset.id].setSelected(True)
        self.application.processEvents()
        with mock_patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            library.delete_button.click()

        self.assertEqual(self.window.asset_library.assets(), ())
        self.assertEqual(len(library.standard_asset_ids), 300)
        self.assertIsNotNone(
            library.assets.get(standards[0].data(Qt.ItemDataRole.UserRole))
        )

    def test_library_drag_rectangle_selects_multiple_catalogue_items(self) -> None:
        """Use the native icon-view rubber band like a desktop file manager."""
        widget = PersonalAssetLibraryWidget()
        widget.set_assets((), self.window.standard_library_assets)
        widget.resize(760, 520)
        widget.show()
        self.application.processEvents()
        viewport = widget.asset_list.viewport()

        QTest.mousePress(
            viewport,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(viewport.width() - 4, viewport.height() - 4),
        )
        QTest.mouseMove(viewport, QPoint(2, 2), 50)
        QTest.mouseRelease(
            viewport,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(2, 2),
        )
        self.application.processEvents()

        self.assertGreater(len(widget.selected_asset_ids()), 1)
        widget.close()

    def test_editing_a_builtin_icon_opens_an_unlinked_personal_copy(self) -> None:
        """Protect standard originals while keeping them beginner-editable."""
        library_path = Path(self.temporary.name) / "builtin-edit-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        self.window._refresh_personal_asset_library()
        library = self.window.library_workspace
        home = self.window.standard_library_assets[0]
        library.select_asset(home.id)

        library.edit_copy_button.click()

        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.pixel_art_tab_index,
        )
        self.assertEqual(self.window._editing_library_asset_id, "")
        self.assertIn("BUILT-IN COPY", self.window.asset_mode_label.text())
        self.assertEqual(self.window.asset_library.assets(), ())

    def test_active_save_never_targets_a_dirty_hidden_document(self) -> None:
        """Route Ctrl+S semantics exclusively through the visible workspace."""
        self.window._dirty = True
        self.window.designer_session.dirty = True
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        with (
            mock_patch.object(
                self.window, "_apply_to_source", return_value=True
            ) as save_pixel,
            mock_patch.object(
                self.window, "_save_gui_project", return_value=True
            ) as save_gui,
        ):
            self.assertTrue(self.window._save_active_workspace())
        save_gui.assert_called_once_with()
        save_pixel.assert_not_called()
        self.window._dirty = False
        self.window.designer_session.dirty = False

    def test_screen_flow_save_uses_the_gui_project(self) -> None:
        """Use the same GUI save target from designer and Screen Flow."""
        self.window.designer_session.dirty = True
        self.window.workspace_tabs.setCurrentIndex(self.window.screen_flow_tab_index)
        with mock_patch.object(
            self.window, "_save_gui_project", return_value=True
        ) as save_gui:
            self.assertTrue(self.window._save_active_workspace())
        save_gui.assert_called_once_with()
        self.window.designer_session.dirty = False

    def test_window_remains_within_1366_by_768(self) -> None:
        """Keep hidden workspace minimum hints from growing the main window."""
        self.window.resize(1366, 768)
        self.window.show()
        self.application.processEvents()
        self.assertLessEqual(self.window.width(), 1366)
        self.assertLessEqual(self.window.height(), 768)
        self.assertTrue(self.window.document_save_button.isVisible())
        self.assertIsNotNone(self.window.screen_designer.property_scroll)

    def test_document_strip_tracks_workspace_path_and_dirty_state(self) -> None:
        """Keep active document identity and save state visible."""
        project_path = Path(self.temporary.name) / "demo.picogui.json"
        self.window.designer_session.path = project_path
        self.window.designer_session.dirty = True
        self.window.workspace_tabs.setCurrentIndex(self.window.screen_flow_tab_index)
        self.window._update_document_strip()
        self.assertEqual(self.window.document_workspace_label.text(), "Screen Flow")
        self.assertEqual(self.window.document_name_label.text(), project_path.name)
        self.assertEqual(self.window.document_name_label.toolTip(), str(project_path))
        self.assertEqual(self.window.document_state_label.text(), "Modified")
        self.window.designer_session.dirty = False

    def test_document_strip_distinguishes_unsaved_and_automatic_storage(self) -> None:
        """Avoid calling a never-saved project saved or offering no-op library saves."""
        self.window.designer_session.path = None
        self.window.designer_session.dirty = False
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        self.assertEqual(self.window.document_state_label.text(), "Not saved")
        self.assertTrue(self.window.document_save_button.isEnabled())
        self.assertTrue(self.window.save_active_action.isEnabled())
        self.window.workspace_tabs.setCurrentIndex(self.window.library_tab_index)
        self.assertEqual(self.window.document_workspace_label.text(), "Asset Library")
        self.assertEqual(self.window.document_state_label.text(), "Auto-saved")
        self.assertFalse(self.window.save_active_action.isEnabled())
        self.assertFalse(self.window.save_as_active_action.isEnabled())

    def test_pixel_toolbar_exposes_new_asset_action(self) -> None:
        """Show the pixel asset creation action in the Pixel Art toolbar."""
        self.assertIn(self.window.new_graphic_action, self.window.tool_bar.actions())
        button = self.window.tool_bar.widgetForAction(self.window.new_graphic_action)
        self.assertEqual(button.text(), "New Asset")
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        self.assertTrue(self.window.tool_bar.isHidden())
        self.window.workspace_tabs.setCurrentIndex(self.window.pixel_art_tab_index)
        self.assertFalse(self.window.tool_bar.isHidden())

    def test_document_strip_exposes_contextual_personal_library_save(self) -> None:
        """Keep reusable-asset storage visible without requiring a menu."""
        self.assertFalse(self.window.document_library_button.isHidden())
        self.assertEqual(self.window.document_library_button.text(), "Save to Library")
        self.assertIn("reuse", self.window.document_library_button.toolTip().lower())
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
        self.assertTrue(self.window.document_library_button.isHidden())
        self.window.workspace_tabs.setCurrentIndex(self.window.pixel_art_tab_index)
        self.assertFalse(self.window.document_library_button.isHidden())

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
        self.assertEqual(dialog.settings(), ("New Asset", 24, 16, "blank"))
        dialog.close()

        imported_dialog = NewGraphicDialog(24, 16, True, 0, initial_mode="reference")
        self.assertEqual(imported_dialog.settings()[3], "reference")
        imported_dialog.close()

    def test_new_blank_asset_opens_before_any_destination_is_chosen(self) -> None:
        """Draw first without forcing a Python filename or source write."""
        self.window._dirty = False
        with (
            mock_patch.object(
                NewGraphicDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            mock_patch.object(
                NewGraphicDialog,
                "settings",
                return_value=("Status Icon", 18, 12, "blank"),
            ),
            mock_patch.object(QFileDialog, "getSaveFileName") as save_dialog,
        ):
            self.assertTrue(self.window._create_new_graphic())
        save_dialog.assert_not_called()
        self.assertTrue(self.window._is_portable_pixel_asset())
        self.assertEqual(
            (self.window.canvas.art().width, self.window.canvas.art().height), (18, 12)
        )
        self.assertEqual(self.window.document_save_button.text(), "Save to Library")
        self.assertEqual(self.window.document_state_label.text(), "Modified")
        self.assertTrue(self.window.canvas.isEnabled())

    def test_imported_asset_save_as_exports_the_visible_pixels(self) -> None:
        """Make Ctrl+Shift+S reliable before Python generation."""
        art = PixelArt(3, 2)
        art.set_pixel(1, 1, 0xF800)
        self.window._open_portable_pixel_asset("Export Me", [art])
        destination = Path(self.temporary.name) / "portable.png"
        with mock_patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(destination), "PNG images (*.png)"),
        ):
            self.assertTrue(self.window._save_as_active_workspace())
        self.assertTrue(destination.is_file())

    def test_idle_pixel_workspace_has_clear_entry_points_without_simulator_clutter(
        self,
    ) -> None:
        """Make the blank state explain the three supported starting paths."""
        self.window._dirty = False
        self.window.current_asset = None
        self.window._clear_editor()
        self.assertFalse(self.window.catalogue_panel.isVisible())
        self.assertFalse(self.window.canvas.isEnabled())
        self.assertFalse(self.window.pixel_empty_widget.isHidden())
        self.assertEqual(self.window.empty_new_button.text(), "New Blank Asset…")
        self.assertEqual(
            self.window.empty_import_button.text(), "Import Image as Asset…"
        )
        self.assertTrue(self.window.document_run_button.isHidden())
        self.assertTrue(self.window.document_simulator_button.isHidden())

    def test_complete_library_animation_edits_and_updates_together(self) -> None:
        """Keep every frame and duration while round-tripping through Pixel Art."""
        library_path = Path(self.temporary.name) / "animation-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        first = PixelArt(2, 1)
        first.set_pixel(0, 0, 0xF800)
        second = PixelArt(2, 1)
        second.set_pixel(1, 0, 0x07E0)
        stored = self.window.asset_library.add("Spinner", [first, second], [90, 180])
        self.window._refresh_personal_asset_library()
        self.window._edit_library_asset_copy(stored.id)
        self.assertEqual(self.window.frame_combo.count(), 2)
        self.assertEqual(self.window._portable_durations, [90, 180])
        self.window.frame_combo.setCurrentIndex(1)
        edited = self.window.canvas.art().copy()
        edited.set_pixel(0, 0, 0x001F)
        self.window.canvas.set_art(edited)
        self.assertTrue(self.window._apply_to_source())
        updated = self.window.asset_library.asset(stored.id)
        self.assertEqual(len(updated.frames), 2)
        self.assertEqual(updated.durations, (90, 180))
        self.assertEqual(updated.pixel_frames()[0], first)
        self.assertEqual(updated.pixel_frames()[1].pixel(0, 0), 0x001F)

    def test_fresh_image_import_saves_as_managed_asset(self) -> None:
        """Treat a fresh image import as an unsaved, savable pixel document."""
        self.window.close()
        self.window = MainWindow()
        self.window._pixel_recovery_path = lambda: (
            Path(self.temporary.name) / "fresh-image-recovery.json"
        )
        image_path = Path(self.temporary.name) / "status-badge.png"
        target = Path(self.temporary.name) / "imported_asset.py"
        image = QImage(12, 7, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFF0000)
        with (
            mock_patch(
                "pico_graphics_editor.window.get_open_image_filename",
                return_value=(str(image_path), "Images (*.png)"),
            ),
            mock_patch(
                "pico_graphics_editor.window.read_image_frames",
                return_value=[image],
            ),
        ):
            self.window._import_image_asset()

        self.assertIsNone(self.window.current_asset)
        self.assertTrue(self.window._dirty)
        self.assertEqual(
            (self.window.canvas.art().width, self.window.canvas.art().height),
            (12, 7),
        )
        self.assertFalse(self.window.canvas.has_reference_image())
        self.assertTrue(
            all(pixel is not None for pixel in self.window.canvas.art().pixels)
        )
        self.assertTrue(self.window.document_save_button.isEnabled())
        self.assertEqual(self.window.document_save_button.text(), "Save to Library")
        self.assertTrue(self.window.apply_button.isEnabled())
        self.assertTrue(self.window.save_to_library_action.isEnabled())
        self.assertTrue(self.window.document_library_button.isEnabled())
        self.assertIn("status-badge", self.window.document_name_label.text())
        self.assertIn("actual pixels", self.window.statusBar().currentMessage())
        self.window.asset_library = AssetLibrary(
            Path(self.temporary.name) / "fresh-import-library.json"
        )
        self.window._refresh_personal_asset_library()
        with mock_patch.object(
            QInputDialog,
            "getText",
            return_value=("Reusable Status Badge", True),
        ):
            self.assertTrue(self.window._save_active_workspace())
        self.assertEqual(
            [record.name for record in self.window.asset_library.assets()],
            ["Reusable Status Badge"],
        )
        stored = self.window.asset_library.assets()[0]
        self.assertEqual(stored.pixel_frames()[0], self.window.canvas.art())
        self.assertEqual(
            self.window.document_save_button.text(), "Update Library Asset"
        )

        self.window._source_backup_root = lambda: (
            Path(self.temporary.name) / "import-backups"
        )
        with (
            mock_patch.object(
                QInputDialog,
                "getText",
                return_value=("draw_status_badge", True),
            ),
            mock_patch.object(
                QFileDialog,
                "getSaveFileName",
                return_value=(str(target), "Python files (*.py)"),
            ),
            mock_patch.object(
                DiffDialog, "exec", return_value=QDialog.DialogCode.Accepted
            ),
            mock_patch.object(QMessageBox, "information"),
        ):
            self.assertTrue(self.window._generate_python_asset())

        self.assertIsNotNone(self.window.current_asset)
        self.assertEqual(self.window.current_asset.record.name, "draw_status_badge")
        self.assertIn(
            "# Pico graphic draw_status_badge begin",
            target.read_text(encoding="utf-8"),
        )
        self.assertFalse(self.window._dirty)

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

    def test_personal_library_saves_asset_and_imports_it_into_a_new_project(
        self,
    ) -> None:
        """Reuse one complete local asset without retaining its original source link."""
        library_path = Path(self.temporary.name) / "personal-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        self.window._refresh_personal_asset_library()
        with mock_patch.object(
            QInputDialog,
            "getText",
            return_value=("Reusable Animation", True),
        ):
            self.window._save_current_asset_to_library()
        records = self.window.asset_library.assets()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Reusable Animation")
        self.assertGreater(len(records[0].frames), 1)
        self.assertEqual(self.window.screen_designer.library_asset_list.count(), 301)

        self.window.designer_session.set_project(GuiProject.create("New Project"))
        self.window.screen_designer._add_selected_library_asset()
        element = self.window.designer_session.current_screen().elements[0]
        project_asset = self.window.designer_session.project.asset(element.asset_id)
        self.assertEqual(element.asset_link_state, "detached")
        self.assertEqual(len(project_asset.frames), len(records[0].frames))
        self.window.designer_session.dirty = False

    def test_freshly_imported_image_can_enter_personal_library_before_source_save(
        self,
    ) -> None:
        """Store an unsaved imported raster for reuse in another GUI project."""
        library_path = Path(self.temporary.name) / "import-library.json"
        self.window.asset_library = AssetLibrary(library_path)
        art = PixelArt(3, 2)
        art.set_pixel(0, 0, 0x0000)
        art.set_pixel(2, 1, 0xF81F)
        self.window._open_portable_pixel_asset(
            "badge",
            [art],
            source_path=Path(self.temporary.name) / "badge.png",
        )
        with mock_patch.object(
            QInputDialog,
            "getText",
            return_value=("Imported Badge", True),
        ):
            self.window._save_current_asset_to_library()
        stored = self.window.asset_library.assets()[0]
        self.assertEqual(stored.pixel_frames()[0], art)
        self.assertEqual(self.window.screen_designer.library_asset_list.count(), 301)
        self.window._dirty = False

    def test_pixel_context_menu_and_tooltips_cover_common_development_actions(
        self,
    ) -> None:
        """Expose frequent Pixel Art operations and concrete inline help."""
        actions = {
            action.text() for action in self.window._pixel_context_menu().actions()
        }
        self.assertIn("Save Python Asset", actions)
        self.assertIn("Save Asset to Personal Library...", actions)
        self.assertIn("Place on Current Screen", actions)
        self.assertIn("Export PNG...", actions)
        for widget in (
            self.window.workspace_tabs,
            self.window.canvas,
            self.window.search_edit,
            self.window.document_library_button,
            self.window.screen_designer.library_asset_list,
            self.window.screen_flow.fit_graph_button,
        ):
            self.assertTrue(widget.toolTip())
            self.assertIn("Example:", widget.toolTip())
        self.assertIn("Asset Library", self.window.workspace_tabs.toolTip())
        for action in (
            self.window.save_to_library_action,
            self.window.export_generated_app_action,
            self.window.undo_action,
        ):
            self.assertIn("Example:", action.toolTip())

    def test_owned_editor_controls_have_semantic_non_generic_tooltips(self) -> None:
        """Reject label-repeating fallback help across every primary workspace."""
        roots = (
            self.window,
            self.window.screen_designer,
            self.window.screen_flow,
            self.window.simulator_workspace,
            self.window.library_workspace,
        )
        controls = {}
        for root in roots:
            for name, value in vars(root).items():
                if isinstance(value, INTERACTIVE_WIDGETS):
                    controls[value] = f"{root.__class__.__name__}.{name}"
                elif isinstance(value, dict):
                    for key, child in value.items():
                        if isinstance(child, INTERACTIVE_WIDGETS):
                            controls[child] = (
                                f"{root.__class__.__name__}.{name}[{key!r}]"
                            )

        failures = []
        for control, identity in controls.items():
            tooltip = control.toolTip().strip()
            unused_description, example = _split_tooltip(tooltip)
            if (
                not tooltip
                or not example
                or _is_generated_help(tooltip)
                or _is_generic_example(example)
            ):
                failures.append(f"{identity}: {tooltip!r}")

        self.assertGreaterEqual(len(controls), 240)
        self.assertEqual(failures, [])

    def test_named_actions_and_destructive_controls_explain_consequences(self) -> None:
        """Keep menu help meaningful and warn before high-impact operations."""
        action_failures = []
        for action in self.window.findChildren(QAction):
            if not action.text().strip() or action.isSeparator():
                continue
            tooltip = action.toolTip().strip()
            unused_description, example = _split_tooltip(tooltip)
            if not example or _is_generated_help(tooltip):
                action_failures.append(f"{action.text()}: {tooltip!r}")
        self.assertEqual(action_failures, [])

        self.assertIn("confirmation", self.window.screen_designer.delete_screen_button.toolTip())
        self.assertIn("Undo", self.window.clear_canvas_button.toolTip())
        self.assertIn(
            "does not create replacement behavior",
            self.window.screen_flow.clear_navigation_logic_button.toolTip(),
        )
        self.assertIn(
            "without saving",
            self.window.simulator_workspace.start_live_button.toolTip(),
        )

    def test_nested_tab_help_names_its_own_tabs(self) -> None:
        """Do not describe every nested tab bar as the top-level workspace."""
        tooltip = self.window.screen_flow.flow_inspector_tabs.toolTip()
        self.assertIn("Connect", tooltip)
        self.assertIn("Issues", tooltip)
        self.assertNotIn("Pixel Art", tooltip)

    def test_generated_app_review_apply_creates_the_seven_python_file_shape(self) -> None:
        """Run the explicit v1 review and atomic apply workflow."""
        destination = Path(self.temporary.name) / "generated"
        destination.mkdir()
        self.window.designer_session.project = GuiProject.create("Status Demo")
        with (
            mock_patch.object(
                QFileDialog,
                "getExistingDirectory",
                return_value=str(destination),
            ),
            mock_patch.object(
                GeneratedAppReviewDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            mock_patch.object(QMessageBox, "information"),
            mock_patch(
                "pico_graphics_editor.window.QStandardPaths.writableLocation",
                return_value=self.temporary.name,
            ),
        ):
            self.window._export_generated_app_structure()
        expected = {
            destination / "Status Demo.py",
            destination / "status_demo" / "__init__.py",
            destination / "status_demo" / "app.py",
            destination / "status_demo" / "behavior_handlers.py",
            destination / "status_demo" / "generated_behavior.py",
            destination / "status_demo" / "generated_ui.py",
            destination / "status_demo" / "generated_assets.py",
        }
        self.assertEqual({path for path in destination.rglob("*.py")}, expected)
        self.assertEqual(
            self.window.designer_session.project.generated_app["destination"],
            str(destination.resolve()),
        )
        self.assertTrue(self.window.designer_session.dirty)
        self.window.designer_session.dirty = False

    def test_generated_app_destination_and_review_cancellation_write_nothing(
        self,
    ) -> None:
        """Leave disk unchanged when either pre-apply user decision is cancelled."""
        destination = Path(self.temporary.name) / "cancelled"
        destination.mkdir()
        with mock_patch.object(QFileDialog, "getExistingDirectory", return_value=""):
            self.window._export_generated_app_structure()
        self.assertEqual(list(destination.iterdir()), [])
        with (
            mock_patch.object(
                QFileDialog,
                "getExistingDirectory",
                return_value=str(destination),
            ),
            mock_patch.object(
                GeneratedAppReviewDialog,
                "exec",
                return_value=QDialog.DialogCode.Rejected,
            ),
        ):
            self.window._export_generated_app_structure()
        self.assertEqual(list(destination.iterdir()), [])

    def test_generated_app_conflict_does_not_create_a_partial_package(self) -> None:
        """Report a preflight collision before any output path is created."""
        destination = Path(self.temporary.name) / "conflict"
        destination.mkdir()
        collision = destination / "Status Demo.py"
        collision.write_text("# unrelated\n", encoding="utf-8")
        self.window.designer_session.project = GuiProject.create("Status Demo")
        with (
            mock_patch.object(
                QFileDialog,
                "getExistingDirectory",
                return_value=str(destination),
            ),
            mock_patch.object(QMessageBox, "critical") as critical,
        ):
            self.window._export_generated_app_structure()
        critical.assert_called_once()
        self.assertEqual(list(destination.iterdir()), [collision])

    def test_designer_workspace_uses_project_undo_history(self) -> None:
        """Route global undo and redo actions to the GUI designer."""
        self.window.workspace_tabs.setCurrentIndex(self.window.app_gui_tab_index)
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
        self.assertIn("MANAGED", self.window.asset_mode_label.text())
        self.assertTrue(self.window.resize_canvas_action.isEnabled())
        self.assertEqual(self.window.screen_designer.pixel_asset_list.count(), 1)
        pixel_item = self.window.screen_designer.pixel_asset_list.item(0)
        self.assertEqual(pixel_item.text(), "new_asset.py / draw_badge")
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

    def test_managed_animation_timeline_reorders_save_sequence(self) -> None:
        """Keep the frame combo save order synchronized with timeline moves."""
        target = Path(self.temporary.name) / "managed_animation.py"
        frames = []
        for color in (0xF800, 0x07E0, 0x001F):
            frame = PixelArt(2, 2)
            frame.set_pixel(0, 0, color)
            frames.append(frame)
        source_patch = build_new_graphic_patch(target, "draw_animation", frames)
        source_patch.apply(Path(self.temporary.name) / "creation-backups")
        self.assertTrue(self.window._open_created_graphic(target, source_patch.key))
        self.assertEqual(self.window.frame_timeline.count(), 3)
        self.window.frame_combo.setCurrentIndex(0)
        self.window._move_animation_frame(1)
        self.assertEqual(
            [
                self.window.frame_combo.itemData(index)
                for index in range(self.window.frame_combo.count())
            ],
            [1, 0, 2],
        )
        self.assertTrue(self.window._animation_structure_dirty)
        self.window._dirty = False

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

    def test_pixel_recovery_restores_unsaved_portable_animation(self) -> None:
        """Protect draw-first assets even before they have a library or Python target."""
        recovery_path = Path(self.temporary.name) / "portable-recovery.json"
        self.window._pixel_recovery_path = lambda: recovery_path
        first = PixelArt(2, 1)
        first.set_pixel(0, 0, 0xF800)
        second = PixelArt(2, 1)
        second.set_pixel(1, 0, 0x07E0)
        self.window._open_portable_pixel_asset(
            "Unsaved Spinner", [first, second], [75, 125]
        )
        self.window.frame_combo.setCurrentIndex(1)
        edited = self.window.canvas.art().copy()
        edited.set_pixel(0, 0, 0x001F)
        self.window.canvas.set_art(edited)
        self.window._write_pixel_recovery()
        self.assertTrue(recovery_path.is_file())
        self.window._portable_frames = [PixelArt(1, 1)]
        with mock_patch.object(self.window, "_confirm_discard", return_value=True):
            self.window._recover_pixel_asset()
        self.assertEqual(self.window._active_pixel_name(), "Unsaved Spinner")
        self.assertEqual(len(self.window._portable_frames), 2)
        self.assertEqual(self.window._portable_durations, [75, 125])
        self.assertEqual(self.window._portable_frames[1].pixel(0, 0), 0x001F)
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
        self.assertEqual(
            self.window.workspace_tabs.currentIndex(),
            self.window.app_gui_tab_index,
        )
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
