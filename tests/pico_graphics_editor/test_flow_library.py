"""Tests for built-in recipes and personal flow-library compatibility."""

import json
import tempfile
import unittest
from pathlib import Path

from pico_graphics_editor.designer_model import FlowNode, GuiProject
from pico_graphics_editor.flow_library import (
    FlowFragmentLibrary,
    built_in_flow_fragments,
)


class FlowLibraryTests(unittest.TestCase):
    def test_required_built_in_recipes_have_metadata_and_valid_anchors(self) -> None:
        recipes = built_in_flow_fragments()
        self.assertEqual(len(recipes), 11)
        self.assertIn("MQTT publish / success / error", {item.name for item in recipes})
        for recipe in recipes:
            recipe.validate()
            self.assertEqual(recipe.source, "built-in")
            self.assertTrue(recipe.description)
            self.assertTrue(recipe.tags)
            self.assertEqual(len(recipe.anchors), 2)

    def test_recipe_insertions_receive_independent_ids(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            library = FlowFragmentLibrary(Path(folder) / "flows.json")
            project = GuiProject.create("Recipe target")
            first = library.insert("recipe_mqtt_publish", project, 100, 100)
            second = library.insert("recipe_mqtt_publish", project, 500, 100)
            self.assertTrue(set(first).isdisjoint(second))

    def test_reading_v1_personal_library_does_not_rewrite_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "flows.json"
            project = GuiProject.create("Legacy fragment")
            project.behavior_nodes.append(FlowNode.create("action", 1))
            temporary = FlowFragmentLibrary(path)
            temporary.add("Legacy", project, {project.behavior_nodes[0].id})
            values = json.loads(path.read_text(encoding="utf-8"))
            values["format_version"] = 1
            for fragment in values["fragments"]:
                for field in (
                    "description",
                    "category",
                    "tags",
                    "version",
                    "minimum_flow_version",
                    "anchors",
                    "source",
                ):
                    fragment.pop(field, None)
            path.write_text(json.dumps(values, indent=2), encoding="utf-8")
            before = path.read_bytes()

            fragments = FlowFragmentLibrary(path).fragments()

            self.assertEqual(fragments[0].name, "Legacy")
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
