"""Allowlisted behavior operations shared by editor, generator, and runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PropertySpec:
    """Describe one validated operation property."""

    id: str
    label: str
    value_type: str = "string"
    default: Any = ""
    required: bool = False
    choices: tuple[str, ...] = ()
    help_text: str = ""


@dataclass(frozen=True)
class PortSpec:
    """Describe one operation-specific typed port."""

    id: str
    label: str
    direction: str
    data_type: str = "event"
    required: bool = False
    multiple: bool = False


@dataclass(frozen=True)
class OperationSpec:
    """Describe one safe behavior operation."""

    id: str
    kind: str
    label: str
    category: str
    description: str
    capability: str
    ports: tuple[PortSpec, ...]
    properties: tuple[PropertySpec, ...] = ()
    search_terms: tuple[str, ...] = ()
    targets: tuple[str, ...] = ("desktop", "simulator", "micropython")


EVENT_OUT = (PortSpec("event", "Event", "out", multiple=True),)
UI_EVENT_OUT = (
    *EVENT_OUT,
    PortSpec("value", "Value", "out", "any", multiple=True),
    PortSpec("text", "Text", "out", "string", multiple=True),
    PortSpec("checked", "Checked", "out", "boolean", multiple=True),
    PortSpec("index", "Index", "out", "integer", multiple=True),
)
ACTION_PORTS = (
    PortSpec("in", "Run / value", "in", "any", required=True),
    PortSpec("done", "Done", "out", multiple=True),
    PortSpec("error", "Error", "out", multiple=True),
)
ASYNC_PORTS = (
    PortSpec("in", "Run / value", "in", "any", required=True),
    PortSpec("success", "Success", "out", multiple=True),
    PortSpec("error", "Error", "out", multiple=True),
    PortSpec("cancel", "Cancel", "out", multiple=True),
)

PAYLOAD_FIELDS = (
    "value",
    "text",
    "checked",
    "index",
    "event_id",
    "screen_id",
    "element_id",
    "widget_type",
)
PAYLOAD_REFERENCES = ("$payload",) + tuple(f"${field}" for field in PAYLOAD_FIELDS)


def _property(
    identifier: str,
    label: str,
    value_type: str = "string",
    default: Any = "",
    required: bool = False,
    choices: tuple[str, ...] = (),
    help_text: str = "",
) -> PropertySpec:
    return PropertySpec(
        identifier, label, value_type, default, required, choices, help_text
    )


def _operation(
    identifier: str,
    kind: str,
    label: str,
    category: str,
    description: str,
    capability: str = "built-in",
    ports: tuple[PortSpec, ...] = ACTION_PORTS,
    properties: tuple[PropertySpec, ...] = (),
    search_terms: tuple[str, ...] = (),
) -> OperationSpec:
    return OperationSpec(
        identifier,
        kind,
        label,
        category,
        description,
        capability,
        ports,
        properties,
        search_terms,
    )


OPERATIONS = (
    _operation(
        "event.ui",
        "event",
        "UI event",
        "Events",
        "Starts behavior from one stable screen-element event.",
        ports=UI_EVENT_OUT,
    ),
    _operation(
        "event.mqtt_message",
        "event",
        "MQTT message",
        "Connectivity",
        "Starts behavior when the MQTT service receives a message.",
        ports=EVENT_OUT,
    ),
    _operation(
        "custom.handler",
        "action",
        "Custom handler",
        "Developer",
        "Calls a developer-owned handler for behavior outside the allowlist.",
        capability="custom-handler",
        properties=(
            _property(
                "handler",
                "Handler",
                required=True,
                help_text="Stable generated function name. Example: on_publish_message_ab12cd",
            ),
        ),
    ),
    _operation(
        "navigation.navigate",
        "action",
        "Navigate to screen",
        "Navigation",
        "Activates a screen through its stable identifier.",
        properties=(_property("screen_id", "Screen", "screen", required=True),),
    ),
    _operation(
        "navigation.back",
        "action",
        "Back",
        "Navigation",
        "Returns to the previous screen.",
    ),
    _operation(
        "ui.read_value",
        "action",
        "Read widget value",
        "UI",
        "Reads a native widget value into the event payload.",
        properties=(_property("element_id", "Element", "element", required=True),),
    ),
    _operation(
        "ui.set_value",
        "action",
        "Set widget value",
        "UI",
        "Sets a native widget value from a literal or incoming payload.",
        properties=(
            _property("element_id", "Element", "element", required=True),
            _property("value", "Value"),
        ),
    ),
    _operation(
        "ui.set_text",
        "action",
        "Set status text",
        "UI",
        "Updates a label, status, or TextBox value.",
        properties=(
            _property("element_id", "Element", "element", required=True),
            _property("text", "Text"),
        ),
    ),
    _operation(
        "ui.set_progress",
        "action",
        "Set progress",
        "UI",
        "Updates a progress element.",
        properties=(
            _property("element_id", "Element", "element", required=True),
            _property("value", "Value", "integer", 0),
        ),
    ),
    *(
        _operation(
            f"ui.{operation}",
            "action",
            label,
            "UI",
            description,
            properties=(
                _property("element_id", "Element", "element", required=True),
                *(
                    (_property("enabled", "Enabled", "boolean", True),)
                    if operation == "enable"
                    else ()
                ),
            ),
        )
        for operation, label, description in (
            ("show", "Show element", "Makes an element visible."),
            ("hide", "Hide element", "Makes an element hidden."),
            ("enable", "Set element enabled", "Enables or disables an element."),
            ("focus", "Focus element", "Moves focus to an element."),
        )
    ),
    _operation(
        "ui.alert",
        "action",
        "Show alert",
        "UI",
        "Shows a message and emits Done or Error.",
        properties=(_property("message", "Message", required=True),),
    ),
    *(
        _operation(
            f"state.{operation}",
            "state" if operation in {"get", "set"} else "action",
            label,
            "State",
            description,
            ports=(
                PortSpec("in", "Run / value", "in", "any", required=True),
                PortSpec("changed", "Changed", "out", multiple=True),
                PortSpec("error", "Error", "out", multiple=True),
            ),
            properties=(
                _property("key", "State key", "state-key", required=True),
                _property("value", "Value", default=""),
            ),
        )
        for operation, label, description in (
            ("get", "Get state", "Reads a named state value."),
            ("set", "Set state", "Writes a named state value."),
            ("clear", "Clear state", "Removes a named state value."),
            ("increment", "Increment state", "Adds a number to named state."),
            ("append", "Append state", "Appends a value to a named list."),
            ("toggle", "Toggle state", "Toggles a named boolean state."),
        )
    ),
    _operation(
        "logic.compare",
        "condition",
        "Compare",
        "Logic",
        "Routes through True or False using a fixed comparison.",
        ports=(
            PortSpec("in", "Evaluate", "in", "any", required=True),
            PortSpec("true", "True", "out", multiple=True),
            PortSpec("false", "False", "out", multiple=True),
            PortSpec("error", "Error", "out", multiple=True),
        ),
        properties=(
            _property(
                "field",
                "Payload field",
                "choice",
                "",
                choices=("",) + PAYLOAD_FIELDS,
                help_text="Read one widget event field before comparing. Example: checked",
            ),
            _property(
                "comparison",
                "Comparison",
                "choice",
                "equal",
                choices=(
                    "equal",
                    "not_equal",
                    "less",
                    "greater",
                    "empty",
                    "non_empty",
                    "true",
                    "false",
                ),
            ),
            _property("value", "Compare with"),
        ),
    ),
    _operation(
        "data.get_field",
        "action",
        "Get payload field",
        "Data",
        "Extracts one allowlisted field from a widget or service payload.",
        properties=(
            _property(
                "field",
                "Field",
                "choice",
                "value",
                True,
                PAYLOAD_FIELDS,
                "Select a structured event field. Example: text",
            ),
        ),
        search_terms=("extract", "widget value", "payload"),
    ),
    _operation(
        "data.value",
        "data",
        "Constant value",
        "Data",
        "Declares a fixed value without evaluating an expression.",
        ports=(PortSpec("value", "Value", "out", "data", multiple=True),),
        properties=(_property("value", "Value"),),
    ),
    _operation(
        "timer.start",
        "timer",
        "Start timer",
        "Lifecycle",
        "Starts one non-blocking timer.",
        ports=(
            PortSpec("start", "Start", "in", required=True),
            PortSpec("elapsed", "Elapsed", "out", multiple=True),
            PortSpec("error", "Error", "out", multiple=True),
        ),
        properties=(
            _property("timer_id", "Timer ID", required=True),
            _property("milliseconds", "Milliseconds", "integer", 1000, True),
        ),
    ),
    _operation(
        "timer.cancel",
        "action",
        "Cancel timer",
        "Lifecycle",
        "Cancels a named timer.",
        properties=(_property("timer_id", "Timer ID", required=True),),
    ),
    *(
        _operation(
            f"storage.{operation}",
            "action",
            label,
            "Storage",
            description,
            ports=ASYNC_PORTS,
            properties=(
                _property("key", "Settings key", "settings-key", required=True),
                _property("value", "Value"),
            ),
        )
        for operation, label, description in (
            ("load", "Load setting", "Loads one named settings value."),
            ("save", "Save setting", "Saves one named settings value."),
            ("delete", "Delete setting", "Deletes one named settings value."),
        )
    ),
    *(
        _operation(
            f"mqtt.{operation}",
            "action",
            label,
            "MQTT",
            description,
            ports=ASYNC_PORTS,
            properties=properties,
        )
        for operation, label, description, properties in (
            (
                "connect",
                "MQTT connect",
                "Connects through the injected MQTT service.",
                (
                    _property(
                        "settings_key",
                        "Broker settings key",
                        "settings-key",
                        "mqtt",
                        True,
                    ),
                ),
            ),
            ("disconnect", "MQTT disconnect", "Disconnects the MQTT service.", ()),
            (
                "subscribe",
                "MQTT subscribe",
                "Subscribes to a topic.",
                (_property("topic", "Topic", required=True),),
            ),
            (
                "unsubscribe",
                "MQTT unsubscribe",
                "Unsubscribes from a topic.",
                (_property("topic", "Topic", required=True),),
            ),
            (
                "publish",
                "MQTT publish",
                "Publishes a QoS 0 message.",
                (
                    _property("topic", "Topic", required=True),
                    _property("payload", "Payload"),
                    _property("retain", "Retain", "boolean", False),
                ),
            ),
        )
    ),
    *(
        _operation(
            f"wifi.{operation}",
            "action",
            label,
            "Wi-Fi",
            description,
            ports=ASYNC_PORTS,
            properties=(
                _property("settings_key", "Wi-Fi settings key", "settings-key", "wifi"),
            ),
        )
        for operation, label, description in (
            (
                "connect",
                "Wi-Fi connect",
                "Connects through the injected Wi-Fi service.",
            ),
            ("status", "Wi-Fi status", "Reads connection status."),
            ("retry", "Wi-Fi retry", "Retries a connection."),
            ("cancel", "Wi-Fi cancel", "Cancels a connection attempt."),
        )
    ),
)

OPERATION_BY_ID = {operation.id: operation for operation in OPERATIONS}


def operation_spec(operation_id: str) -> OperationSpec | None:
    """Return one allowlisted operation definition."""
    return OPERATION_BY_ID.get(operation_id)


def operations_for_kind(kind: str) -> tuple[OperationSpec, ...]:
    """Return operations compatible with a visual node kind."""
    return tuple(operation for operation in OPERATIONS if operation.kind == kind)


def validate_operation_properties(
    operation: OperationSpec,
    values: dict[str, Any],
) -> tuple[str, ...]:
    """Return deterministic validation errors without discarding unknown values."""
    errors: list[str] = []
    for field in operation.properties:
        value = values.get(field.id, field.default)
        if field.required and (value is None or value == ""):
            errors.append(f"{field.label} is required")
            continue
        if value in (None, "") and not field.required:
            continue
        if isinstance(value, str) and value in PAYLOAD_REFERENCES:
            continue
        if field.value_type == "integer" and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            errors.append(f"{field.label} must be an integer")
        elif field.value_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{field.label} must be true or false")
        elif field.value_type == "choice" and value not in field.choices:
            errors.append(f"{field.label} must be one of {', '.join(field.choices)}")
    return tuple(errors)
