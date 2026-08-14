"""Tests for Flow Standard v1 contracts, validation, reuse, and generation."""

# ruff: noqa: E402

import ast
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor.designer_model import (
    BehaviorConnection,
    FlowGroup,
    FlowNode,
    GuiProject,
    behavior_connection_error,
    flow_diagnostics,
)
from pico_graphics_editor.flow_library import FlowFragmentLibrary
from pico_graphics_editor.generated_app import GeneratedAppError, generate_ui_module


def complete_behavior_project() -> GuiProject:
    """Return one valid event-condition-two-action structural flow."""
    project = GuiProject.create("Behavior Demo")
    event = FlowNode.create("event", 1, 100, 400)
    condition = FlowNode.create("condition", 2, 380, 400)
    first = FlowNode.create("action", 3, 660, 350)
    second = FlowNode.create("action", 4, 660, 500)
    condition.properties["default_branch"] = "true"
    project.behavior_nodes.extend((event, condition, first, second))
    project.behavior_connections.extend(
        (
            BehaviorConnection.create(event.id, "event", condition.id, "in"),
            BehaviorConnection.create(condition.id, "true", first.id, "in"),
            BehaviorConnection.create(condition.id, "false", second.id, "in"),
        )
    )
    group = FlowGroup("group_decision", "Decision", 350, 320, 550, 320)
    project.flow_groups.append(group)
    for node in (condition, first, second):
        node.group_id = group.id
    return project


class FlowStandardTests(unittest.TestCase):
    """Verify behavior contracts remain structural, portable, and deterministic."""

    def test_v1_project_loads_without_executable_reinterpretation(self) -> None:
        """Keep structural v1 semantics until an explicit v2 editor action."""
        project = complete_behavior_project()
        values = project.to_dict()
        values["flow_standard_version"] = 1
        for node in values["behavior_nodes"]:
            node.pop("operation", None)
            node.pop("binding", None)
            node.pop("component_ref", None)

        restored = GuiProject.from_dict(values)

        self.assertEqual(restored.flow_standard_version, 1)
        self.assertTrue(all(not node.operation for node in restored.behavior_nodes))

    def test_project_round_trip_preserves_typed_nodes_ports_edges_and_groups(
        self,
    ) -> None:
        """Persist Flow Standard v1 beside unchanged format-8 navigation data."""
        project = complete_behavior_project()
        project.behavior_nodes[0].breakpoint = True
        restored = GuiProject.from_dict(project.to_dict())
        self.assertEqual(restored.format_version, 8)
        self.assertEqual(restored.flow_standard_version, 2)
        self.assertEqual(len(restored.behavior_nodes), 4)
        self.assertEqual(len(restored.behavior_connections), 3)
        self.assertEqual(restored.behavior_nodes[0].port("event").data_type, "event")
        self.assertTrue(restored.behavior_nodes[0].breakpoint)
        self.assertEqual(restored.flow_groups[0].name, "Decision")
        self.assertFalse(
            any(item.severity == "error" for item in flow_diagnostics(restored))
        )

    def test_validation_rejects_incompatible_or_future_contracts(self) -> None:
        """Detect typed-port mismatches and unknown future standards."""
        project = GuiProject.create("Invalid")
        data = FlowNode.create("data", 1)
        action = FlowNode.create("action", 2)
        project.behavior_nodes.extend((data, action))
        connection = BehaviorConnection.create(data.id, "value", action.id, "in")
        project.behavior_connections.append(connection)
        self.assertIn(
            "cannot connect data to event",
            behavior_connection_error(project, connection),
        )
        self.assertTrue(
            any(item.severity == "error" for item in flow_diagnostics(project))
        )
        values = project.to_dict()
        values["flow_standard_version"] = 99
        with self.assertRaisesRegex(ValueError, "Unsupported flow standard"):
            GuiProject.from_dict(values)

    def test_validator_blocks_legacy_behavior_connection_conditions(self) -> None:
        """Never imply that an edge condition executes when runtimes ignore it."""
        project = complete_behavior_project()
        project.behavior_connections[0].condition = "is_connected"

        diagnostics = flow_diagnostics(project)

        issue = next(
            item
            for item in diagnostics
            if item.code == "unsupported-behavior-condition"
        )
        self.assertEqual(issue.severity, "error")
        self.assertIn("Condition node", issue.message)

    def test_diagnostics_report_malformed_ports_and_unbounded_cycles(self) -> None:
        """Explain malformed contracts and cycles without explicit timer boundaries."""
        project = GuiProject.create("Cycle")
        first = FlowNode.create("action", 1)
        second = FlowNode.create("action", 2)
        first.ports[0].direction = "sideways"
        first.ports[0].data_type = ""
        project.behavior_nodes.extend((first, second))
        project.behavior_connections.extend(
            (
                BehaviorConnection.create(first.id, "done", second.id, "in"),
                BehaviorConnection.create(second.id, "done", first.id, "in"),
            )
        )
        codes = {item.code for item in flow_diagnostics(project)}
        self.assertIn("invalid-port-direction", codes)
        self.assertIn("missing-port-type", codes)
        self.assertIn("unbounded-behavior-cycle", codes)

    def test_personal_flow_library_clones_internal_contracts_atomically(self) -> None:
        """Reuse a selected flow without retaining source project identities."""
        with tempfile.TemporaryDirectory() as folder:
            library = FlowFragmentLibrary(Path(folder) / "flow-library.json")
            project = complete_behavior_project()
            selected = {node.id for node in project.behavior_nodes}
            fragment = library.add("Decision Flow", project, selected)
            self.assertEqual(len(fragment.nodes), 4)
            self.assertEqual(len(fragment.connections), 3)
            self.assertEqual(library.fragments()[0].name, "Decision Flow")

            target = GuiProject.create("Target")
            inserted = library.insert(fragment.id, target, 900, 200)
            self.assertEqual(len(inserted), 4)
            self.assertTrue(selected.isdisjoint(inserted))
            self.assertEqual(len(target.behavior_connections), 3)
            self.assertEqual(len(target.flow_groups), 1)
            self.assertFalse(
                any(item.severity == "error" for item in flow_diagnostics(target))
            )
            library.rename(fragment.id, "Renamed Flow")
            self.assertEqual(library.fragments()[0].name, "Renamed Flow")
            self.assertTrue(library.remove(fragment.id))
            self.assertEqual(library.fragments(), ())

    def test_damaged_flow_fragment_fingerprint_is_rejected(self) -> None:
        """Reject modified portable flow records rather than guessing at them."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "flow-library.json"
            library = FlowFragmentLibrary(path)
            project = complete_behavior_project()
            library.add(
                "Decision Flow",
                project,
                {node.id for node in project.behavior_nodes},
            )
            values = json.loads(path.read_text(encoding="utf-8"))
            values["fragments"][0]["nodes"][0]["name"] = "Tampered"
            path.write_text(json.dumps(values), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                library.fragments()

    def test_generated_ui_exposes_unimplemented_behavior_contracts(self) -> None:
        """Generate stable stubs and records without inventing application behavior."""
        project = complete_behavior_project()
        source = generate_ui_module(project)
        ast.parse(source)
        self.assertIn("FLOW_STANDARD_VERSION = 2", source)
        self.assertIn("describe_behavior_contract", source)

        package_name = "_flow_standard_generated_test"
        package = types.ModuleType(package_name)
        package.__path__ = []
        assets = types.ModuleType(f"{package_name}.generated_assets")
        assets.draw_asset = lambda *args, **kwargs: None
        ui_module = types.ModuleType(f"{package_name}.generated_ui")
        ui_module.__package__ = package_name
        sys.modules[package_name] = package
        sys.modules[assets.__name__] = assets
        try:
            exec(source, ui_module.__dict__)
            ui = ui_module.GeneratedUI(object())
            contracts = ui.behavior_contracts()
            self.assertEqual(contracts["standard"], 2)
            self.assertEqual(len(contracts["nodes"]), 4)
            result = ui.describe_behavior_contract(
                project.behavior_nodes[0].id, {"source": "test"}
            )
            self.assertFalse(result["implemented"])
            self.assertEqual(result["context"], {"source": "test"})
        finally:
            sys.modules.pop(package_name, None)
            sys.modules.pop(assets.__name__, None)

    def test_generation_blocks_invalid_typed_behavior_edges(self) -> None:
        """Keep malformed behavior contracts out of editor-owned generated modules."""
        project = GuiProject.create("Invalid Generation")
        data = FlowNode.create("data", 1)
        action = FlowNode.create("action", 2)
        project.behavior_nodes.extend((data, action))
        project.behavior_connections.append(
            BehaviorConnection.create(data.id, "value", action.id, "in")
        )
        with self.assertRaises(GeneratedAppError):
            generate_ui_module(project)

    def test_validator_reports_missing_entry_unreachable_and_bad_payload_token(
        self,
    ) -> None:
        """Turn confusing non-executable graph states into actionable findings."""
        project = GuiProject.create("Assisted validator")
        publish = FlowNode.create("action", 1)
        publish.set_operation("mqtt.publish")
        publish.properties.update(
            {"topic": "demo/test", "payload": "$typo", "retain": False}
        )
        status = FlowNode.create("action", 2)
        project.behavior_nodes.extend((publish, status))

        diagnostics = flow_diagnostics(project)
        codes = {diagnostic.code for diagnostic in diagnostics}

        self.assertIn("missing-behavior-entry", codes)
        self.assertIn("invalid-payload-reference", codes)
        self.assertIn("structural-only-node", codes)


if __name__ == "__main__":
    unittest.main()
