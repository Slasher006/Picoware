"""Tests for GUI designer persistence and Python generation."""

# ruff: noqa: E402

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.designer_model import (
    BehaviorConnection,
    FlowConnection,
    FlowNode,
    GuiElement,
    GuiProject,
    ProjectAsset,
    ScreenDesign,
    asset_element_runtime_scale,
    bake_asset_element,
    build_designer_patch,
    flow_diagnostics,
    generate_live_app_python,
    generate_python,
    invalid_asset_scale_elements,
    preview_flow_node_kind_change,
)
from pico_graphics_editor.model import PixelArt


class DesignerModelTests(unittest.TestCase):
    """Verify project round trips and generated source."""

    def test_kind_change_preview_remaps_or_reports_connections_without_mutation(
        self,
    ) -> None:
        """Preview endpoint migration before the editor changes a node contract."""
        project = GuiProject.create("Kind preview")
        event = FlowNode.create("event", 1)
        action = FlowNode.create("action", 2)
        project.behavior_nodes.extend((event, action))
        edge = BehaviorConnection.create(event.id, "event", action.id, "in")
        project.behavior_connections.append(edge)

        component = preview_flow_node_kind_change(project, action, "component")
        self.assertEqual(component.removed_connection_ids, ())
        self.assertEqual(component.endpoint_updates, ((edge.id, "event", "invoke"),))
        self.assertEqual(action.kind, "action")
        self.assertEqual(edge.target_port_id, "in")

        data = preview_flow_node_kind_change(project, action, "data")
        self.assertEqual(data.removed_connection_ids, (edge.id,))
        edge.locked = True
        locked = preview_flow_node_kind_change(project, action, "data")
        self.assertEqual(locked.locked_connection_ids, (edge.id,))

    def test_flow_preflight_rejects_incompatible_widget_operation_target(self) -> None:
        """Catch unsupported widget actions before generation or simulation."""
        project = GuiProject.create("Typed target")
        choice = GuiElement.create("native", 1)
        choice.name = "Mode"
        choice.native_widget = "choice"
        choice.widget_items = ["A", "B"]
        project.screens[0].elements.append(choice)
        action = FlowNode.create("action", 1)
        action.set_operation("ui.set_text")
        action.properties.update({"element_id": choice.id, "text": "Invalid"})
        project.behavior_nodes.append(action)

        diagnostics = flow_diagnostics(project)

        self.assertIn(
            "unsupported-operation-target",
            {item.code for item in diagnostics},
        )

    def test_existing_native_alert_exit_migrates_to_its_acknowledgement_event(
        self,
    ) -> None:
        """Repair the previously generated disabled and unreachable Alert topology."""
        project = GuiProject.create("Legacy alert")
        alert_screen = ScreenDesign.create("Alert", 320, 320, 1)
        alert = GuiElement.create("native", 1)
        alert.native_widget = "alert"
        alert.focusable = False
        alert.enabled = False
        alert_screen.elements.append(alert)
        project.screens.append(alert_screen)
        route = FlowConnection.create(alert_screen.id, project.screens[0].id, "Back")
        route.source_element_id = ""
        route.trigger_event_id = "event_unreachable"
        project.connections.append(route)

        migrated = GuiProject.from_dict(project.to_dict())
        migrated_alert = migrated.screens[1].elements[0]
        migrated_route = migrated.connections[0]

        self.assertTrue(migrated_alert.enabled)
        self.assertTrue(migrated_alert.focusable)
        self.assertEqual(migrated_route.source_element_id, migrated_alert.id)
        self.assertEqual(migrated_route.trigger_event_id, migrated_alert.event_id)
        self.assertEqual(migrated_route.trigger, migrated_alert.activation_event())

    def test_asset_size_bake_creates_an_independent_device_safe_snapshot(self) -> None:
        """Resolve arbitrary placement geometry without changing the source asset."""
        project = GuiProject.create("Bake")
        first = PixelArt(2, 2, -1, 0, [0xF800, 0x07E0, 0x001F, None])
        second = PixelArt(2, 2, -1, 0, [0xFFFF, 0x0000, None, 0xFFE0])
        source = ProjectAsset(
            "asset-source",
            "Source",
            2,
            2,
            -1,
            0,
            [list(first.pixels), list(second.pixels)],
            [100, 200],
            source_path="source.py",
            link_state="current",
        )
        project.assets.append(source)
        element = GuiElement.create("icon", 1)
        element.asset_id = source.id
        element.asset_key = "source.py::icon"
        element.asset_link_state = "current"
        element.width = 3
        element.height = 1
        project.screens[0].elements.append(element)
        self.assertIsNone(asset_element_runtime_scale(element, source))
        self.assertEqual(len(invalid_asset_scale_elements(project)), 1)

        baked = bake_asset_element(project, element)

        self.assertEqual((baked.width, baked.height), (3, 1))
        self.assertEqual(baked.frames[0], [0xF800, 0xF800, 0x07E0])
        self.assertEqual(baked.frames[1], [0xFFFF, 0xFFFF, 0x0000])
        self.assertEqual(baked.durations, [100, 200])
        self.assertEqual(baked.origin_x, -2)
        self.assertEqual(element.asset_id, baked.id)
        self.assertEqual(element.asset_link_state, "detached")
        self.assertEqual(element.asset_key, "")
        self.assertEqual(asset_element_runtime_scale(element, baked), 1)
        self.assertEqual(invalid_asset_scale_elements(project), [])
        self.assertEqual(source.frames[0], first.pixels)
        self.assertEqual(source.source_path, "source.py")

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
        target_element.asset_call = "draw_badge"
        target_element.asset_key = "/tmp/assets.py::draw_badge"
        target_element.asset_width = 2
        target_element.asset_height = 2
        target_element.asset_runs = [[0, 0, 1, 0xF800], [1, 1, 1, 0x07E0]]
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
        loaded_target = loaded.screens[1].elements[0]
        self.assertEqual(loaded_target.asset_call, "draw_badge")
        self.assertEqual(loaded_target.asset_key, "/tmp/assets.py::draw_badge")
        self.assertEqual(
            (loaded_target.asset_width, loaded_target.asset_height), (2, 2)
        )
        self.assertEqual(
            loaded_target.asset_runs,
            [[0, 0, 1, 0xF800], [1, 1, 1, 0x07E0]],
        )
        self.assertEqual(loaded.format_version, 8)
        self.assertTrue(loaded.connections[0].trigger_event_id)
        self.assertTrue(loaded.project_id)
        self.assertTrue(loaded_target.asset_id)
        self.assertIsNotNone(loaded.asset(loaded_target.asset_id))
        self.assertEqual(loaded_target.asset_link_state, "missing")
        self.assertEqual(loaded_target.asset_qualified_name, "draw_badge")

    def test_project_round_trip_preserves_native_widget_configuration(self) -> None:
        """Keep native widget type, values, and state in the editable project."""
        project = GuiProject.create("Native")
        element = GuiElement.create("native", 1)
        element.native_widget = "choice"
        element.text = "Choose a mode"
        element.widget_items = ["Automatic", "Manual"]
        element.widget_item_states = [True, False]
        element.widget_selected_index = 1
        element.widget_state = True
        project.screens[0].elements.append(element)

        restored = GuiProject.from_dict(project.to_dict())

        loaded = restored.screens[0].elements[0]
        self.assertEqual(loaded.kind, "native")
        self.assertEqual(loaded.native_widget, "choice")
        self.assertEqual(loaded.widget_items, ["Automatic", "Manual"])
        self.assertEqual(loaded.widget_item_states, [True, False])
        self.assertEqual(loaded.widget_selected_index, 1)
        self.assertTrue(loaded.widget_state)

    def test_version_six_asset_migrates_without_losing_embedded_pixels(self) -> None:
        """Migrate legacy absolute keys while retaining the portable snapshot."""
        project = GuiProject.create("Legacy")
        values = project.to_dict()
        values["format_version"] = 6
        values["screens"][0]["elements"] = [
            {
                "id": "legacy_icon",
                "kind": "icon",
                "name": "Legacy Icon",
                "x": 4,
                "y": 5,
                "width": 2,
                "height": 2,
                "asset_key": "/missing/assets.py::draw_badge",
                "asset_width": 2,
                "asset_height": 2,
                "asset_runs": [[0, 0, 2, 0xF800]],
            }
        ]
        migrated = GuiProject.from_dict(values)
        icon = migrated.screens[0].elements[0]
        self.assertEqual(migrated.format_version, 8)
        self.assertEqual(icon.asset_link_state, "missing")
        self.assertEqual(icon.asset_runs, [[0, 0, 2, 0xF800]])
        self.assertTrue(icon.event_id)
        self.assertIsNotNone(migrated.asset(icon.asset_id))

    def test_new_format_eight_project_ids_survive_display_renames(self) -> None:
        """Keep relationship identities independent from user-facing names."""
        project = GuiProject.create("Original")
        button = GuiElement.create("button", 1)
        project.screens[0].elements.append(button)
        identities = (project.project_id, project.screens[0].id, button.event_id)
        project.name = "Renamed"
        project.screens[0].name = "Other screen"
        button.name = "Other button"
        self.assertEqual(
            identities, (project.project_id, project.screens[0].id, button.event_id)
        )
        self.assertEqual(project.format_version, 8)

    def test_format_seven_asset_migration_groups_only_matching_links(self) -> None:
        """Share equal linked snapshots while separating changed fingerprints."""
        project = GuiProject.create("Legacy links")
        values = project.to_dict()
        values.pop("project_id")
        values.pop("assets")
        values["format_version"] = 7
        base = {
            "kind": "icon",
            "name": "Badge",
            "x": 0,
            "y": 0,
            "width": 2,
            "height": 1,
            "asset_key": "/missing/assets.py::draw_badge",
            "asset_width": 2,
            "asset_height": 1,
            "asset_runs": [[0, 0, 1, 0xF800]],
            "asset_source_path": "assets.py",
            "asset_qualified_name": "draw_badge",
            "asset_link_state": "missing",
        }
        values["screens"][0]["elements"] = [
            {**base, "id": "one", "asset_fingerprint": "same"},
            {**base, "id": "two", "asset_fingerprint": "same"},
            {**base, "id": "three", "asset_fingerprint": "changed"},
        ]
        migrated = GuiProject.from_dict(values)
        first, second, third = migrated.screens[0].elements
        self.assertEqual(first.asset_id, second.asset_id)
        self.assertNotEqual(first.asset_id, third.asset_id)
        self.assertEqual(len(migrated.assets), 2)
        repeated = GuiProject.from_dict(values)
        self.assertEqual(repeated.project_id, migrated.project_id)
        self.assertEqual(
            [item.asset_id for item in repeated.screens[0].elements],
            [item.asset_id for item in migrated.screens[0].elements],
        )

    def test_detached_and_draft_migration_create_independent_snapshots(self) -> None:
        """Key portable snapshots to their owning element rather than source text."""
        project = GuiProject.create("Snapshots")
        values = project.to_dict()
        values["format_version"] = 7
        values["assets"] = []
        values["screens"][0]["elements"] = [
            {
                "id": identity,
                "kind": "icon",
                "name": identity,
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
                "asset_width": 1,
                "asset_height": 1,
                "asset_runs": [[0, 0, 1, 0x0000]],
                "asset_link_state": state,
            }
            for identity, state in (
                ("detached_one", "detached"),
                ("draft_two", "draft"),
            )
        ]
        migrated = GuiProject.from_dict(values)
        identifiers = [item.asset_id for item in migrated.screens[0].elements]
        self.assertEqual(identifiers, ["snapshot_detached_one", "snapshot_draft_two"])
        self.assertEqual(len(set(identifiers)), 2)
        self.assertEqual(migrated.assets[0].frames[0], [0x0000])

    def test_format_eight_asset_round_trip_and_load_do_not_rewrite(self) -> None:
        """Persist all catalogue fields while keeping open a read-only operation."""
        project = GuiProject.create("Round trip")
        art = PixelArt(2, 1, -2, 3)
        art.set_pixel(1, 0, 0x001F)
        asset = ProjectAsset.from_pixel_art(
            "asset_blue",
            "Blue",
            art,
            source_path="assets.py",
            absolute_fallback="/tmp/assets.py",
            qualified_name="draw_blue",
            fingerprint="fingerprint",
            link_state="current",
        )
        asset.durations = [120]
        project.assets.append(asset)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "round.picogui.json"
            project.save(path)
            before = path.read_bytes()
            loaded = GuiProject.load(path)
            after = path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(loaded.to_dict(), project.to_dict())

    def test_relative_asset_link_survives_project_folder_move(self) -> None:
        """Resolve a portable source link relative to the relocated project."""
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original"
            moved = Path(folder) / "moved"
            original.mkdir()
            (original / "assets.py").write_text("def draw_badge():\n    pass\n")
            project = GuiProject.create("Portable")
            icon = GuiElement.create("icon", 1)
            icon.asset_key = f"{original / 'assets.py'}::draw_badge"
            icon.asset_qualified_name = "draw_badge"
            icon.asset_source_path = "assets.py"
            icon.asset_absolute_fallback = str(original / "assets.py")
            icon.asset_link_state = "current"
            icon.asset_runs = [[0, 0, 1, 0xF800]]
            project.screens[0].elements.append(icon)
            project.save(original / "portable.picogui.json")
            shutil.move(original, moved)
            loaded = GuiProject.load(moved / "portable.picogui.json")
        loaded_icon = loaded.screens[0].elements[0]
        self.assertEqual(loaded_icon.asset_key, f"{moved / 'assets.py'}::draw_badge")
        self.assertEqual(loaded_icon.asset_runs, [[0, 0, 1, 0xF800]])

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

    def test_generated_python_embeds_pixel_asset_runs(self) -> None:
        """Generate portable drawing calls for a placed pixel asset."""
        project = GuiProject.create("Pixel Assets")
        icon = GuiElement.create("icon", 1)
        icon.x = 10
        icon.y = 20
        icon.width = 4
        icon.height = 4
        icon.asset_call = "draw_badge"
        icon.asset_width = 2
        icon.asset_height = 2
        icon.asset_runs = [[0, 0, 1, 0xF800], [1, 1, 1, 0x07E0]]
        project.screens[0].elements.append(icon)
        source = generate_live_app_python(project, project.start_screen_id)
        ast.parse(source)
        self.assertIn(
            "self.draw._fill_rectangle(10, 20, 2, 2, 0xF800)",
            source,
        )
        self.assertIn(
            "self.draw._fill_rectangle(12, 22, 2, 2, 0x07E0)",
            source,
        )
        self.assertNotIn("self.draw_badge(", source)

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

    def test_legacy_patch_rejects_image_statement_explosion(self) -> None:
        """Direct dense imports to the streamed generated-app resource format."""
        project = GuiProject.create("Dense")
        icon = GuiElement.create("icon", 1)
        icon.asset_width = 100
        icon.asset_height = 100
        icon.asset_runs = [
            [index % 100, index // 100, 1, 0xFFFF] for index in range(5_001)
        ]
        project.screens[0].elements.append(icon)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.py"
            with self.assertRaisesRegex(ValueError, "generated_assets.pga"):
                build_designer_patch(project, path)
            self.assertFalse(path.exists())

    def test_old_ui_event_ports_gain_typed_outputs_when_loaded(self) -> None:
        """Migrate persisted Event-only UI nodes without changing their binding."""
        node = FlowNode.from_dict(
            {
                "id": "node_event",
                "kind": "event",
                "name": "Choice confirmed",
                "node_x": 10,
                "node_y": 20,
                "operation": "event.ui",
                "binding": {"event_id": "event_choice"},
                "ports": [
                    {
                        "id": "event",
                        "name": "Event",
                        "direction": "out",
                        "data_type": "event",
                        "multiple": True,
                    }
                ],
            }
        )

        self.assertEqual(
            [port.id for port in node.ports],
            ["event", "value", "text", "checked", "index"],
        )
        self.assertEqual(node.binding["event_id"], "event_choice")


if __name__ == "__main__":
    unittest.main()
