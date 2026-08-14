"""Tests for safe executable App Flow operations."""

import unittest

from pico_graphics_editor.behavior_operations import (
    operation_spec,
    validate_operation_properties,
)
from pico_graphics_editor.behavior_runtime import (
    BehaviorRuntime,
    BehaviorRuntimeError,
    _widget_event_payload,
)
from pico_graphics_editor.designer_model import BehaviorConnection, FlowNode


class RecordingMqtt:
    def __init__(self):
        self.published = []

    def publish(self, **values):
        self.published.append(values)
        return values


class ErrorMqtt:
    def publish(self, **values):
        return "error", {"reason": "offline", "topic": values["topic"]}


class RecordingTimer:
    def start(self, **values):
        self.callback = values["callback"]
        return "scheduled"

    def cancel(self, **values):
        return values["timer_id"]


class RecordingUi:
    def __init__(self, values, indexes=None, types=None):
        self.values = values
        self.indexes = indexes or {}
        self.types = types or {}

    def read_value(self, element_id):
        return self.values.get(element_id)

    def read_index(self, element_id):
        return self.indexes.get(element_id)

    def widget_type(self, element_id):
        return self.types.get(element_id, "")


class RejectedUi(RecordingUi):
    def set_text(self, element_id, text):
        return False


class BehaviorRuntimeTests(unittest.TestCase):
    def test_registry_configures_typed_operation_ports(self) -> None:
        node = FlowNode.create("action", 1)
        node.set_operation("mqtt.publish")
        self.assertEqual(node.operation, "mqtt.publish")
        self.assertEqual(
            [port.id for port in node.ports], ["in", "success", "error", "cancel"]
        )
        self.assertIsNotNone(operation_spec(node.operation))

    def test_runtime_executes_mqtt_then_state_with_ordered_redacted_trace(self) -> None:
        publish = FlowNode.create("action", 1)
        publish.set_operation("mqtt.publish")
        publish.properties.update(
            {"topic": "demo/test", "payload": "hello", "retain": False}
        )
        state = FlowNode.create("state", 2)
        state.set_operation("state.set")
        state.properties.update({"key": "status", "value": "published"})
        edge = BehaviorConnection.create(publish.id, "success", state.id, "in")
        mqtt = RecordingMqtt()
        runtime = BehaviorRuntime(
            [publish, state],
            [edge],
            {"mqtt": mqtt, "state": {}},
        )

        trace = runtime.dispatch(publish.id, {"password": "hidden"}, "in")

        self.assertEqual([entry.node_id for entry in trace], [publish.id, state.id])
        self.assertEqual(runtime.services["state"]["status"], "published")
        self.assertEqual(mqtt.published[0]["topic"], "demo/test")
        self.assertIn("redacted", trace[0].payload)
        self.assertIn("demo/test", trace[0].result)

    def test_service_error_uses_error_port_and_records_output_payload(self) -> None:
        """Make simulated service failures visible and follow their error branch."""
        publish = FlowNode.create("action", 1)
        publish.set_operation("mqtt.publish")
        publish.properties.update({"topic": "demo/test", "payload": "hello"})
        failed = FlowNode.create("state", 2)
        failed.set_operation("state.set")
        failed.properties.update({"key": "route", "value": "error"})
        edge = BehaviorConnection.create(publish.id, "error", failed.id, "in")
        runtime = BehaviorRuntime(
            [publish, failed], [edge], {"mqtt": ErrorMqtt(), "state": {}}
        )

        trace = runtime.dispatch(publish.id)

        self.assertEqual(trace[0].outcome, "error")
        self.assertEqual(trace[0].output_port, "error")
        self.assertIn("offline", trace[0].result)
        self.assertEqual(runtime.services["state"]["route"], "error")

    def test_rejected_ui_mutation_routes_through_error_output(self) -> None:
        """Never report Done when the selected widget rejects an operation."""
        update = FlowNode.create("action", 1)
        update.set_operation("ui.set_text")
        update.properties.update({"element_id": "choice", "text": "Wrong"})
        failed = FlowNode.create("state", 2)
        failed.set_operation("state.set")
        failed.properties.update({"key": "route", "value": "error"})
        edge = BehaviorConnection.create(update.id, "error", failed.id, "in")
        runtime = BehaviorRuntime(
            [update, failed],
            [edge],
            {"ui": RejectedUi({}), "state": {}},
        )

        trace = runtime.dispatch(update.id, input_port="in")

        self.assertEqual(trace[0].output_port, "error")
        self.assertEqual(trace[0].outcome, "error")
        self.assertEqual(runtime.services["state"]["route"], "error")

    def test_runtime_reports_missing_service_and_step_limit(self) -> None:
        publish = FlowNode.create("action", 1)
        publish.set_operation("mqtt.publish")
        publish.properties.update({"topic": "demo", "payload": "x"})
        runtime = BehaviorRuntime([publish], [])
        with self.assertRaisesRegex(BehaviorRuntimeError, "Missing mqtt service"):
            runtime.dispatch(publish.id)

        event = FlowNode.create("event", 2)
        event.set_operation("event.ui")
        loop = BehaviorConnection.create(event.id, "event", event.id, "event")
        bounded = BehaviorRuntime([event], [loop], step_limit=3)
        with self.assertRaisesRegex(BehaviorRuntimeError, "step limit"):
            bounded.dispatch(event.id)

    def test_timer_resumes_only_from_non_blocking_callback(self) -> None:
        timer_node = FlowNode.create("timer", 1)
        timer_node.set_operation("timer.start")
        timer_node.properties.update({"timer_id": "refresh", "milliseconds": 50})
        state = FlowNode.create("state", 2)
        state.set_operation("state.set")
        state.properties.update({"key": "elapsed", "value": True})
        edge = BehaviorConnection.create(timer_node.id, "elapsed", state.id, "in")
        timer = RecordingTimer()
        runtime = BehaviorRuntime(
            [timer_node, state], [edge], {"timer": timer, "state": {}}
        )

        runtime.dispatch(timer_node.id, input_port="start")
        self.assertNotIn("elapsed", runtime.services["state"])
        timer.callback("tick")
        self.assertTrue(runtime.services["state"]["elapsed"])

    def test_debugger_queue_steps_and_continues_at_node_boundaries(self) -> None:
        """Expose real single-step execution instead of status-only controls."""
        event = FlowNode.create("event", 1)
        event.set_operation("event.ui")
        first = FlowNode.create("state", 2)
        first.set_operation("state.set")
        first.properties.update({"key": "first", "value": 1})
        first.breakpoint = True
        second = FlowNode.create("state", 3)
        second.set_operation("state.set")
        second.properties.update({"key": "second", "value": 2})
        connections = [
            BehaviorConnection.create(event.id, "event", first.id, "in"),
            BehaviorConnection.create(first.id, "changed", second.id, "in"),
        ]
        runtime = BehaviorRuntime([event, first, second], connections, {"state": {}})

        runtime.queue(event.id, {"value": "start"})
        self.assertEqual(runtime.next_node_id, event.id)
        self.assertEqual(runtime.pending_count, 1)
        self.assertEqual(runtime.step_execution().node_id, event.id)
        self.assertEqual(runtime.next_node_id, first.id)

        runtime.continue_execution()

        self.assertTrue(runtime.paused)
        self.assertEqual(runtime.next_node_id, second.id)
        self.assertEqual(runtime.services["state"]["first"], 1)
        self.assertNotIn("second", runtime.services["state"])

        runtime.step_execution()

        self.assertEqual(runtime.services["state"]["second"], 2)
        self.assertEqual(runtime.pending_count, 0)

    def test_bound_widget_event_branches_on_choice_value(self) -> None:
        """Carry widget identity, selected value, text, and index into Compare."""
        event = FlowNode.create("event", 1)
        event.set_operation("event.ui")
        event.binding = {
            "screen_id": "screen_main",
            "element_id": "choice_mode",
            "event_id": "event_mode",
            "widget_type": "choice",
        }
        compare = FlowNode.create("condition", 2)
        compare.set_operation("logic.compare")
        compare.properties.update(
            {"field": "value", "comparison": "equal", "value": "Manual"}
        )
        state = FlowNode.create("state", 3)
        state.set_operation("state.set")
        state.properties.update({"key": "mode", "value": "$value"})
        edges = [
            BehaviorConnection.create(event.id, "event", compare.id, "in"),
            BehaviorConnection.create(compare.id, "true", state.id, "in"),
        ]
        ui = RecordingUi(
            {"choice_mode": "Manual"},
            {"choice_mode": 1},
            {"choice_mode": "choice"},
        )
        runtime = BehaviorRuntime(
            [event, compare, state], edges, {"ui": ui, "state": {}}
        )

        self.assertTrue(runtime.dispatch_event("event_mode"))

        self.assertEqual(runtime.services["state"]["mode"], "Manual")
        self.assertIn("'index': 1", runtime.trace[0].payload)
        self.assertIn("'text': 'Manual'", runtime.trace[0].payload)

    def test_widget_payload_matrix_uses_one_contract(self) -> None:
        """Describe every readable native/custom widget with stable common fields."""
        values = {
            "menu": "First",
            "list": "Second",
            "choice": "Manual",
            "toggle": True,
            "toggle_list": (2, "Retain", False),
            "keyboard": "broker.local",
            "search_bar": "sensor/temperature",
            "textbox": "Log text",
            "button": "Publish",
            "label": "Connected",
            "icon": "Refresh",
            "custom_list": "One\nTwo",
            "progress": 75,
        }
        indexes = {"menu": 0, "list": 1, "choice": 1, "toggle_list": 2}
        ui = RecordingUi(values, indexes, {key: key for key in values})

        payloads = {
            key: _widget_event_payload(
                ui,
                {
                    "screen_id": "main",
                    "element_id": key,
                    "widget_type": key,
                },
                "event_" + key,
            )
            for key in values
        }

        self.assertEqual(payloads["choice"]["index"], 1)
        self.assertEqual(payloads["keyboard"]["text"], "broker.local")
        self.assertIs(payloads["toggle"]["checked"], True)
        self.assertEqual(payloads["toggle_list"]["text"], "Retain")
        self.assertIs(payloads["toggle_list"]["checked"], False)
        self.assertEqual(payloads["progress"]["value"], 75)

    def test_widget_fields_route_into_mqtt_properties_without_expressions(self) -> None:
        """Resolve keyboard text and toggle state through exact safe tokens."""
        publish = FlowNode.create("action", 1)
        publish.set_operation("mqtt.publish")
        publish.properties.update(
            {"topic": "demo/widget", "payload": "$text", "retain": "$checked"}
        )
        mqtt = RecordingMqtt()
        runtime = BehaviorRuntime([publish], [], {"mqtt": mqtt})

        runtime.dispatch(publish.id, {"text": "hello", "checked": True}, "in")

        self.assertEqual(
            mqtt.published,
            [{"topic": "demo/widget", "payload": "hello", "retain": True}],
        )
        self.assertEqual(
            validate_operation_properties(
                operation_spec("mqtt.publish"), publish.properties
            ),
            (),
        )

    def test_typed_ui_event_output_routes_scalar_directly(self) -> None:
        """Connect a widget Text port directly without a Read Value node."""
        event = FlowNode.create("event", 1)
        event.set_operation("event.ui")
        event.binding = {
            "screen_id": "main",
            "element_id": "keyboard",
            "event_id": "submitted",
            "widget_type": "keyboard",
        }
        state = FlowNode.create("state", 2)
        state.set_operation("state.set")
        state.properties.update({"key": "broker", "value": ""})
        edge = BehaviorConnection.create(event.id, "text", state.id, "in")
        runtime = BehaviorRuntime(
            [event, state],
            [edge],
            {
                "ui": RecordingUi({"keyboard": "broker.local"}),
                "state": {},
            },
        )

        runtime.dispatch_event("submitted")

        self.assertEqual(runtime.services["state"]["broker"], "broker.local")

    def test_get_payload_field_extracts_toggle_list_members(self) -> None:
        """Expose one Toggle List member as a scalar payload for downstream nodes."""
        extract = FlowNode.create("action", 1)
        extract.set_operation("data.get_field")
        extract.properties["field"] = "checked"
        state = FlowNode.create("state", 2)
        state.set_operation("state.set")
        state.properties.update({"key": "retain", "value": ""})
        edge = BehaviorConnection.create(extract.id, "done", state.id, "in")
        runtime = BehaviorRuntime([extract, state], [edge], {"state": {}})

        runtime.dispatch(extract.id, {"index": 2, "text": "Retain", "checked": True})

        self.assertIs(runtime.services["state"]["retain"], True)

    def test_missing_payload_reference_is_a_visible_runtime_error(self) -> None:
        """Never treat an unavailable field token as literal user data."""
        publish = FlowNode.create("action", 1)
        publish.set_operation("mqtt.publish")
        publish.properties.update(
            {"topic": "demo/widget", "payload": "$text", "retain": False}
        )
        runtime = BehaviorRuntime([publish], [], {"mqtt": RecordingMqtt()})

        with self.assertRaisesRegex(BehaviorRuntimeError, "Payload field 'text'"):
            runtime.dispatch(publish.id, {"value": 3}, "in")


if __name__ == "__main__":
    unittest.main()
