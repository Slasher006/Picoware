"""Tests for safe existing-application GUI importing."""

# ruff: noqa: E402

import ast
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.app_importer import (
    ExistingAppImporter,
    build_imported_app_patches,
    refresh_import_metadata,
)
from pico_graphics_editor.designer_model import GuiProject, ScreenDesign


APP_SOURCE = '''TFT_BLACK = 0x0000
TFT_WHITE = 0xFFFF
TFT_BLUE = 0x001F


class ExistingApp:
    """Fixture application."""

    def __init__(self, draw):
        """Initialize the fixture."""
        self.draw = draw
        self.screen = "menu"

    def render(self):
        """Render the current screen."""
        if self.screen == "menu":
            self.draw._fill_rectangle(0, 0, 320, 320, TFT_BLACK)
            self.draw._text(12, 10, "Main Menu", TFT_WHITE)
            self.draw._fill_rectangle(20, 40, 120, 32, TFT_BLUE)
            self._draw_runtime_widget(self.player)
        elif self.screen == "game":
            self.draw._fill_rectangle(0, 0, 320, 320, TFT_BLACK)
            self.draw._text(12, 10, "Game", TFT_WHITE)

    def handle_input(self, key):
        """Handle navigation input."""
        if self.screen == "menu":
            if key == "ENTER":
                self.screen = "game"
'''


class AppImporterTests(unittest.TestCase):
    """Verify imported screens, flows, and narrow source edits."""

    def setUp(self) -> None:
        """Create one temporary existing application."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "existing_app.py"
        self.path.write_text(APP_SOURCE, encoding="utf-8")
        self.result = ExistingAppImporter().import_path(self.path)

    def tearDown(self) -> None:
        """Remove the temporary existing application."""
        self.temporary.cleanup()

    def test_import_discovers_screens_elements_and_flow(self) -> None:
        """Discover state screens, editable calls, and navigation."""
        project = self.result.project
        self.assertEqual([screen.name for screen in project.screens], ["Menu", "Game"])
        self.assertGreaterEqual(self.result.editable_count, 5)
        self.assertGreaterEqual(self.result.locked_count, 1)
        self.assertEqual(project.screen(project.start_screen_id).name, "Menu")
        self.assertEqual(len(project.connections), 1)
        connection = project.connections[0]
        self.assertEqual(connection.trigger, "ENTER")
        self.assertEqual(project.screen(connection.target_id).name, "Game")
        self.assertFalse(connection.locked)

    def test_imported_element_edit_builds_narrow_patch(self) -> None:
        """Patch one direct drawing call and preserve application logic."""
        project = self.result.project
        menu = next(screen for screen in project.screens if screen.name == "Menu")
        label = next(element for element in menu.elements if element.kind == "label")
        label.x = 24
        label.text = "Start Menu"
        patches = build_imported_app_patches(project)
        self.assertEqual(len(patches), 1)
        self.assertIn("self.draw._text(24, 10, 'Start Menu'", patches[0].updated)
        self.assertIn("def handle_input", patches[0].updated)
        ast.parse(patches[0].updated)

    def test_text_preview_size_is_not_a_source_change(self) -> None:
        """Ignore a label bounding-box size that has no source argument."""
        project = self.result.project
        label = next(
            element
            for element in project.screens[0].elements
            if element.kind == "label"
        )
        label.width += 20
        label.height += 10
        self.assertEqual(build_imported_app_patches(project), [])

    def test_imported_flow_target_can_be_changed(self) -> None:
        """Patch a source-backed state assignment through the graph."""
        project = self.result.project
        third = project.screens[0]
        connection = project.connections[0]
        connection.target_id = third.id
        patches = build_imported_app_patches(project)
        self.assertIn("self.screen = 'menu'", patches[0].updated)

    def test_numeric_flow_trigger_keeps_its_type(self) -> None:
        """Write an edited integer trigger without converting it to text."""
        self.path.write_text(APP_SOURCE.replace('"ENTER"', "5"), encoding="utf-8")
        project = ExistingAppImporter().import_path(self.path).project
        project.connections[0].trigger = "7"
        patches = build_imported_app_patches(project)
        self.assertIn("if key == 7:", patches[0].updated)
        self.assertNotIn("if key == '7':", patches[0].updated)

    def test_flow_target_requires_an_imported_state(self) -> None:
        """Reject source transitions aimed at design-only screens."""
        project = self.result.project
        design_only = ScreenDesign.create(
            "Design Only", project.width, project.height, len(project.screens)
        )
        project.screens.append(design_only)
        project.connections[0].target_id = design_only.id
        with self.assertRaisesRegex(ValueError, "no editable source state"):
            build_imported_app_patches(project)

    def test_external_source_change_blocks_patch(self) -> None:
        """Reject edits when source changed after import."""
        project = self.result.project
        project.screens[0].elements[0].x = 4
        self.path.write_text(APP_SOURCE + "\nVALUE = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after import"):
            build_imported_app_patches(project)

    def test_empty_folder_is_rejected(self) -> None:
        """Reject an import target without Python source files."""
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "No Python source files"):
            ExistingAppImporter().import_path(empty)

    def test_metadata_refresh_allows_followup_patch(self) -> None:
        """Refresh anchors after applying one imported edit."""
        project = self.result.project
        element = project.screens[0].elements[0]
        element.fill_color = 0xF800
        patches = build_imported_app_patches(project)
        self.path.write_text(patches[0].updated, encoding="utf-8")
        refresh_import_metadata(project, patches)
        element.fill_color = 0x07E0
        followup = build_imported_app_patches(project)
        self.assertEqual(len(followup), 1)
        self.assertIn("0x07E0", followup[0].updated)

    def test_source_metadata_survives_project_round_trip(self) -> None:
        """Persist exact source anchors in the companion GUI project."""
        project_path = self.root / "existing.picogui.json"
        self.result.project.save(project_path)
        loaded = GuiProject.load(project_path)
        element = loaded.screens[0].elements[0]
        self.assertEqual(loaded.import_root, str(self.path))
        self.assertEqual(loaded.imported_sources, self.result.project.imported_sources)
        self.assertEqual(element.source_path, str(self.path))
        element.x += 1
        self.assertEqual(len(build_imported_app_patches(loaded)), 1)

    def test_cross_file_state_flow_is_inferred(self) -> None:
        """Match navigation code to uniquely named renderer states."""
        render_path = self.root / "render.py"
        render_path.write_text(
            '''def render(draw, state):
    """Render a state."""
    if state == "menu":
        draw._text(8, 8, "Menu", 0xFFFF)
    elif state == "game":
        draw._text(8, 8, "Game", 0xFFFF)
''',
            encoding="utf-8",
        )
        self.path.write_text(
            '''class ExistingApp:
    """Fixture application."""

    def handle_input(self, key):
        """Handle navigation input."""
        if self.state == "menu":
            if key == "ENTER":
                self.state = "game"
''',
            encoding="utf-8",
        )
        result = ExistingAppImporter().import_path(self.root)
        self.assertEqual(len(result.project.connections), 1)
        connection = result.project.connections[0]
        self.assertEqual(result.project.screen(connection.source_id).name, "Menu")
        self.assertEqual(result.project.screen(connection.target_id).name, "Game")

    def test_current_pico_bomber_app_imports_conservatively(self) -> None:
        """Discover repository screens while preserving dynamic game code."""
        path = REPOSITORY_PATH / "builds/MicroPython/apps_unfrozen/games/pico_bomber"
        result = ExistingAppImporter().import_path(path)
        names = {screen.name for screen in result.project.screens}
        self.assertGreaterEqual(result.files_scanned, 4)
        self.assertIn("Title", names)
        self.assertGreaterEqual(len(result.project.screens), 4)
        self.assertGreater(result.locked_count, 0)


if __name__ == "__main__":
    unittest.main()
