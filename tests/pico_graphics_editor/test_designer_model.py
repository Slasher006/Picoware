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
    generate_python,
)


class DesignerModelTests(unittest.TestCase):
    """Verify project round trips and generated source."""

    def test_project_round_trip_preserves_flow(self) -> None:
        """Save and reload screens, elements, and navigation."""
        project = GuiProject.create("Demo")
        project.screens[0].elements.append(GuiElement.create("button", 1))
        second = ScreenDesign.create("Game", 320, 320, 1)
        project.screens.append(second)
        project.connections.append(
            FlowConnection.create(project.screens[0].id, second.id, "start")
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "demo.picogui.json"
            project.save(path)
            loaded = GuiProject.load(path)
        self.assertEqual(loaded.name, "Demo")
        self.assertEqual(loaded.screens[0].elements[0].kind, "button")
        self.assertEqual(loaded.connections[0].trigger, "start")

    def test_generated_python_is_parseable(self) -> None:
        """Generate valid screen drawing and flow methods."""
        project = GuiProject.create("Pico Demo")
        project.screens[0].elements.append(GuiElement.create("label", 1))
        source = generate_python(project)
        ast.parse(source)
        self.assertIn("class Pico_Demo", source)
        self.assertIn("_fill_rectangle", source)

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
