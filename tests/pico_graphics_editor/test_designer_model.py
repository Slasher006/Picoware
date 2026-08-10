"""Tests for GUI designer persistence and Python generation."""

# ruff: noqa: E402

import ast
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.designer_model import (
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
    build_designer_patch,
    generate_live_app_python,
    generate_python,
)


class DesignerModelTests(unittest.TestCase):
    """Verify project round trips and generated source."""

    def test_project_round_trip_preserves_flow(self) -> None:
        """Save and reload screens, elements, and navigation."""
        project = GuiProject.create("Demo")
        element = GuiElement.create("button", 1)
        element.editor_locked = True
        element.focus_order = 7
        element.event_name = "launch_game"
        element.enabled = False
        element.focus_style = "corners"
        element.focus_color = 0xF81F
        element.focus_thickness = 3
        element.focus_padding = 5
        project.screens[0].elements.append(element)
        second = ScreenDesign.create("Game", 320, 320, 1)
        target_element = GuiElement.create("icon", 1)
        second.elements.append(target_element)
        project.screens.append(second)
        project.connections.append(
            FlowConnection.create(
                project.screens[0].id,
                second.id,
                "launch_game",
                element.id,
                target_element.id,
            )
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "demo.picogui.json"
            project.save(path)
            loaded = GuiProject.load(path)
        self.assertEqual(loaded.name, "Demo")
        self.assertEqual(loaded.screens[0].elements[0].kind, "button")
        self.assertTrue(loaded.screens[0].elements[0].editor_locked)
        self.assertTrue(loaded.screens[0].elements[0].focusable)
        self.assertEqual(loaded.screens[0].elements[0].focus_order, 7)
        self.assertFalse(loaded.screens[0].elements[0].enabled)
        self.assertEqual(
            loaded.screens[0].elements[0].activation_event(),
            "launch_game",
        )
        self.assertEqual(loaded.screens[0].elements[0].focus_style, "corners")
        self.assertEqual(loaded.screens[0].elements[0].focus_color, 0xF81F)
        self.assertEqual(loaded.screens[0].elements[0].focus_thickness, 3)
        self.assertEqual(loaded.screens[0].elements[0].focus_padding, 5)
        self.assertEqual(loaded.connections[0].trigger, "launch_game")
        self.assertEqual(loaded.connections[0].source_element_id, element.id)
        self.assertEqual(
            loaded.connections[0].target_element_id,
            target_element.id,
        )
        self.assertEqual(loaded.format_version, 4)

    def test_generated_python_is_parseable(self) -> None:
        """Generate valid screen drawing and flow methods."""
        project = GuiProject.create("Pico Demo")
        project.screens[0].elements.append(GuiElement.create("label", 1))
        button = GuiElement.create("button", 2)
        button.name = "open_settings"
        button.focus_order = 1
        project.screens[0].elements.append(button)
        source = generate_python(project)
        ast.parse(source)
        self.assertIn("class Pico_Demo", source)
        self.assertIn("_fill_rectangle", source)
        self.assertIn("def move_focus", source)
        self.assertIn("('open_settings',)", source)

    def test_live_app_starts_on_active_unsaved_screen(self) -> None:
        """Generate a temporary app with the active screen as its initial state."""
        project = GuiProject.create("Live Demo")
        second = ScreenDesign.create("Details", 320, 320, 1)
        second.background_color = 0xF800
        project.screens.append(second)
        source = generate_live_app_python(project, second.id)
        ast.parse(source)
        self.assertIn("self.screen = 'Details'", source)
        self.assertIn("def start(view_manager):", source)
        self.assertIn("def run(view_manager):", source)
        self.assertEqual(project.start_screen_id, project.screens[0].id)

    def test_generated_element_relation_focuses_target_asset(self) -> None:
        """Generate activation events and destination focus from asset endpoints."""
        project = GuiProject.create("Asset Flow")
        source = GuiElement.create("button", 1)
        source.event_name = "open_details"
        project.screens[0].elements.append(source)
        target = ScreenDesign.create("Details", 320, 320, 1)
        first = GuiElement.create("button", 1)
        second = GuiElement.create("icon", 2)
        first.focus_order = 0
        second.focus_order = 1
        target.elements.extend((first, second))
        project.screens.append(target)
        project.connections.append(
            FlowConnection.create(
                project.screens[0].id,
                target.id,
                source.activation_event(),
                source.id,
                second.id,
            )
        )
        generated = generate_python(project)
        ast.parse(generated)
        self.assertIn("event == 'open_details'", generated)
        self.assertIn("self.focus_index = 1", generated)
        self.assertIn("('open_details',)", generated)

    def test_generated_focus_style_is_configurable(self) -> None:
        """Generate the selected element's configured focus appearance."""
        project = GuiProject.create("Focus Style")
        button = GuiElement.create("button", 1)
        button.focus_style = "underline"
        button.focus_color = 0xF81F
        button.focus_thickness = 3
        button.focus_padding = 1
        project.screens[0].elements.append(button)
        generated = generate_python(project)
        ast.parse(generated)
        self.assertIn("self._draw_focus()", generated)
        self.assertIn("def _draw_focus(self):", generated)
        self.assertIn(
            "self.draw._fill_rectangle(23, 61, 122, 3, 0xF81F)",
            generated,
        )

    def test_patch_replaces_managed_block(self) -> None:
        """Update one designer block without duplicating it."""
        project = GuiProject.create("Demo")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gui.py"
            path.write_text("VALUE = 1\n", encoding="utf-8")
            first = build_designer_patch(project, path)
            path.write_text(first.updated, encoding="utf-8")
            project.screens[0].name = "Home"
            second = build_designer_patch(project, path)
        self.assertEqual(second.updated.count("Pico GUI Designer begin"), 1)
        self.assertIn("def _draw_Home", second.updated)

    def test_export_rejects_duplicate_screen_names(self) -> None:
        """Reject ambiguous generated screen method names."""
        project = GuiProject.create("Demo")
        project.screens.append(ScreenDesign.create("Main", 320, 320, 1))
        with self.assertRaisesRegex(ValueError, "unique"):
            generate_python(project)


if __name__ == "__main__":
    unittest.main()
