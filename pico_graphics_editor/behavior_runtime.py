"""Bounded executor for allowlisted App Flow behavior operations."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .behavior_operations import (
    PAYLOAD_FIELDS,
    PAYLOAD_REFERENCES,
    operation_spec,
    validate_operation_properties,
)


@dataclass(frozen=True)
class RuntimeTraceEntry:
    """Describe one redacted behavior execution step."""

    order: int
    node_id: str
    operation: str
    input_port: str
    output_port: str
    outcome: str
    payload: str
    duration_ms: int
    result: str = ""


class BehaviorRuntimeError(RuntimeError):
    """Report a safe behavior execution failure."""


def _payload_field(payload: Any, field: str) -> Any:
    """Read one declared payload field without expressions or attribute access."""
    if not field:
        return payload
    if field not in PAYLOAD_FIELDS:
        raise BehaviorRuntimeError(f"Unsupported payload field {field!r}")
    if not isinstance(payload, dict):
        scalar_matches = {
            "value": True,
            "text": isinstance(payload, str),
            "checked": isinstance(payload, bool),
            "index": isinstance(payload, int) and not isinstance(payload, bool),
        }
        if scalar_matches.get(field, False):
            return payload
    if not isinstance(payload, dict) or field not in payload:
        raise BehaviorRuntimeError(f"Payload field {field!r} is unavailable")
    return payload[field]


def _resolve_payload_reference(value: Any, payload: Any) -> Any:
    """Resolve an exact allowlisted payload token and leave literals unchanged."""
    if not isinstance(value, str) or value not in PAYLOAD_REFERENCES:
        return value
    return payload if value == "$payload" else _payload_field(payload, value[1:])


def _widget_event_payload(
    ui: Any, binding: dict[str, str], event_id: str
) -> dict[str, Any]:
    """Build the common public payload for one bound UI element event."""
    element_id = binding.get("element_id", "")
    value = ui.read_value(element_id) if ui is not None and element_id else None
    widget_type = binding.get("widget_type", "")
    type_reader = getattr(ui, "widget_type", None) if ui is not None else None
    if not widget_type and callable(type_reader) and element_id:
        widget_type = type_reader(element_id) or ""
    result: dict[str, Any] = {
        "event_id": event_id,
        "screen_id": binding.get("screen_id", ""),
        "element_id": element_id,
        "widget_type": widget_type,
        "value": value,
    }
    if isinstance(value, tuple) and len(value) >= 3:
        result.update(index=value[0], text=value[1], checked=bool(value[2]))
    elif isinstance(value, bool):
        result["checked"] = value
    elif isinstance(value, str):
        result["text"] = value
    index_reader = getattr(ui, "read_index", None) if ui is not None else None
    if callable(index_reader) and element_id:
        index = index_reader(element_id)
        if index is not None:
            result["index"] = index
    return result


def _payload_summary(value: Any) -> str:
    """Return a bounded payload summary without exposing secret-like fields."""
    if isinstance(value, dict):
        safe = {
            key: (
                "<redacted>"
                if any(
                    word in str(key).lower() for word in ("password", "secret", "token")
                )
                else item
            )
            for key, item in value.items()
        }
        text = repr(safe)
    else:
        text = repr(value)
    return text[:157] + "..." if len(text) > 160 else text


class BehaviorRuntime:
    """Execute a typed graph through injected services and a bounded queue."""

    def __init__(
        self,
        nodes: list[Any],
        connections: list[Any],
        services: dict[str, Any] | None = None,
        handlers: Any = None,
        step_limit: int = 100,
        trace_limit: int = 250,
    ):
        self.nodes = {node.id: node for node in nodes}
        self.connections = list(connections)
        self.services = dict(services or {})
        self.handlers = handlers
        self.step_limit = max(1, int(step_limit))
        self.trace_limit = max(1, int(trace_limit))
        self.trace: list[RuntimeTraceEntry] = []
        self.paused = False
        self._pending: list[tuple[str, str, Any]] = []

    def dispatch(
        self, node_id: str, payload: Any = None, input_port: str = "event"
    ) -> list[RuntimeTraceEntry]:
        """Run from one node and return the trace entries created by this dispatch."""
        before = len(self.trace)
        self._pending.append((node_id, input_port, payload))
        self.continue_execution()
        return self.trace[before:]

    @property
    def pending_count(self) -> int:
        """Return the number of queued node executions for debugger controls."""
        return len(self._pending)

    @property
    def next_node_id(self) -> str:
        """Return the next queued node ID without mutating execution state."""
        return self._pending[0][0] if self._pending else ""

    def queue(
        self, node_id: str, payload: Any = None, input_port: str = "event"
    ) -> None:
        """Queue one debugger entry point without immediately executing it."""
        if node_id not in self.nodes:
            raise BehaviorRuntimeError(f"Unknown behavior node {node_id}")
        self._pending.append((node_id, input_port, payload))
        self.paused = True

    def dispatch_service_event(self, operation: str, payload: Any = None) -> bool:
        """Dispatch an allowlisted MQTT, Wi-Fi, timer, or application event node."""
        matched = False
        for node in self.nodes.values():
            if node.operation == operation and node.kind == "event":
                self._pending.append((node.id, "event", payload))
                matched = True
        if matched:
            self.continue_execution()
        return matched

    def dispatch_event(self, event_id: str, payload: Any = None) -> bool:
        """Dispatch every UI Event node bound to one stable element event."""
        matched = False
        ui = self.services.get("ui")
        for node in self.nodes.values():
            if (
                node.operation == "event.ui"
                and node.binding.get("event_id") == event_id
            ):
                event_payload = (
                    _widget_event_payload(ui, node.binding, event_id)
                    if payload is None
                    else payload
                )
                self._pending.append((node.id, "event", event_payload))
                matched = True
        if matched:
            self.continue_execution()
        return matched

    def continue_execution(self) -> None:
        """Process queued emissions until complete, paused, or bounded."""
        self.paused = False
        steps = 0
        while self._pending and not self.paused:
            if steps >= self.step_limit:
                raise BehaviorRuntimeError("Behavior step limit reached")
            self.step_execution()
            steps += 1

    def step_execution(self) -> RuntimeTraceEntry | None:
        """Execute exactly one queued node and return its new trace record."""
        if not self._pending:
            self.paused = False
            return None
        self.paused = False
        node_id, input_port, payload = self._pending.pop(0)
        node = self.nodes.get(node_id)
        if node is None:
            raise BehaviorRuntimeError(f"Unknown behavior node {node_id}")
        output_port, result, outcome, duration = self._execute(node, payload)
        self._record(node, input_port, output_port, outcome, payload, result, duration)
        if output_port:
            self._queue_output(node.id, output_port, result)
            if node.operation == "event.ui" and isinstance(result, dict):
                for field in ("value", "text", "checked", "index"):
                    if field in result:
                        self._queue_output(node.id, field, result[field])
        if node.breakpoint:
            self.paused = True
        return self.trace[-1]

    def emit(self, node_id: str, output_port: str, payload: Any = None) -> None:
        """Resume a timer or service callback through one declared output port."""
        self._queue_output(node_id, output_port, payload)
        if not self.paused:
            self.continue_execution()

    def _queue_output(self, node_id: str, output_port: str, payload: Any) -> None:
        """Queue targets connected to one declared node output."""
        for connection in self.connections:
            if (
                connection.source_node_id == node_id
                and connection.source_port_id == output_port
            ):
                self._pending.append(
                    (connection.target_node_id, connection.target_port_id, payload)
                )

    def stop(self) -> None:
        """Stop the current bounded dispatch without clearing trace history."""
        self._pending.clear()
        self.paused = False

    def clear_trace(self) -> None:
        """Clear retained runtime trace entries."""
        self.trace.clear()

    def _record(
        self,
        node: Any,
        input_port: str,
        output_port: str,
        outcome: str,
        payload: Any,
        result: Any,
        duration_ms: int,
    ) -> None:
        self.trace.append(
            RuntimeTraceEntry(
                len(self.trace) + 1,
                node.id,
                node.operation,
                input_port,
                output_port,
                outcome,
                _payload_summary(payload),
                duration_ms,
                _payload_summary(result),
            )
        )
        del self.trace[: -self.trace_limit]

    def _execute(self, node: Any, payload: Any) -> tuple[str, Any, str, int]:
        started = time.monotonic()
        operation = operation_spec(node.operation)
        if operation is None:
            raise BehaviorRuntimeError(f"Unknown operation {node.operation!r}")
        errors = validate_operation_properties(operation, node.properties)
        if errors:
            raise BehaviorRuntimeError(f"{node.name}: {errors[0]}")
        try:
            output, result = self._call(node, payload)
            outcome = output if output in {"error", "cancel"} else "success"
        except BehaviorRuntimeError:
            raise
        except Exception as error:
            if any(port.id == "error" for port in node.ports):
                output, result, outcome = "error", {"error": str(error)}, "error"
            else:
                raise BehaviorRuntimeError(f"{node.name}: {error}") from error
        duration = int((time.monotonic() - started) * 1000)
        return output, result, outcome, duration

    def _service(self, name: str) -> Any:
        service = self.services.get(name)
        if service is None:
            raise BehaviorRuntimeError(f"Missing {name} service")
        return service

    def _call(self, node: Any, payload: Any) -> tuple[str, Any]:
        operation = node.operation
        values = node.properties
        if operation.startswith("event."):
            return "event", payload
        if operation == "custom.handler":
            handler = values["handler"]
            callable_handler = (
                self.handlers.get(handler)
                if isinstance(self.handlers, dict)
                else getattr(self.handlers, handler, None)
            )
            if not callable(callable_handler):
                raise BehaviorRuntimeError(f"Missing custom handler {handler}")
            result = callable_handler(payload, self)
            return "done", payload if result is None else result
        if operation == "navigation.navigate":
            result = self._service("ui").navigate(values["screen_id"])
            if result is False:
                raise RuntimeError("UI rejected navigation target")
            return "done", result
        if operation == "navigation.back":
            result = self._service("ui").back()
            if result is False:
                raise RuntimeError("UI rejected back navigation")
            return "done", result
        if operation.startswith("ui."):
            method = operation.split(".", 1)[1]
            arguments = {
                key: _resolve_payload_reference(value, payload)
                for key, value in values.items()
            }
            if method in {"set_value", "set_text", "set_progress"}:
                key = "text" if method == "set_text" else "value"
                arguments[key] = (
                    arguments.get(key) if arguments.get(key) != "" else payload
                )
            result = getattr(self._service("ui"), method)(**arguments)
            if method != "read_value" and result is False:
                raise RuntimeError(f"UI rejected {operation} target")
            return "done", result
        if operation.startswith("state."):
            method = operation.split(".", 1)[1]
            state = self.services.setdefault("state", {})
            key = values["key"]
            value = values.get("value", "")
            value = payload if value == "" else value
            value = _resolve_payload_reference(value, payload)
            if method == "get":
                result = state.get(key)
            elif method == "set":
                state[key] = value
                result = value
            elif method == "clear":
                result = state.pop(key, None)
            elif method == "increment":
                state[key] = state.get(key, 0) + (
                    value if isinstance(value, (int, float)) else 1
                )
                result = state[key]
            elif method == "append":
                state.setdefault(key, []).append(value)
                result = state[key]
            else:
                state[key] = not bool(state.get(key))
                result = state[key]
            return "changed", result
        if operation == "logic.compare":
            comparison = values["comparison"]
            candidate = _payload_field(payload, values.get("field", ""))
            right = _resolve_payload_reference(values.get("value"), payload)
            if comparison == "equal":
                matched = candidate == right
            elif comparison == "not_equal":
                matched = candidate != right
            elif comparison == "less":
                matched = candidate < right
            elif comparison == "greater":
                matched = candidate > right
            elif comparison == "empty":
                matched = not candidate
            elif comparison == "non_empty":
                matched = bool(candidate)
            elif comparison == "true":
                matched = bool(candidate)
            else:
                matched = not bool(candidate)
            return ("true" if matched else "false"), payload
        if operation == "data.get_field":
            return "done", _payload_field(payload, values["field"])
        if operation == "data.value":
            return "value", values.get("value")
        if operation.startswith("timer."):
            method = operation.split(".", 1)[1]
            timer = self._service("timer")
            if method == "start":
                result = timer.start(
                    timer_id=_resolve_payload_reference(values["timer_id"], payload),
                    milliseconds=_resolve_payload_reference(
                        values["milliseconds"], payload
                    ),
                    callback=lambda value=None: self.emit(node.id, "elapsed", value),
                )
                return "", result
            return "done", timer.cancel(
                timer_id=_resolve_payload_reference(values["timer_id"], payload)
            )
        for service_name in ("storage", "mqtt", "wifi"):
            prefix = service_name + "."
            if operation.startswith(prefix):
                method = operation[len(prefix) :]
                arguments = {
                    key: _resolve_payload_reference(value, payload)
                    for key, value in values.items()
                }
                result = getattr(self._service(service_name), method)(**arguments)
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and result[0] in {"success", "error", "cancel"}
                ):
                    return result
                return "success", result
        raise BehaviorRuntimeError(f"Operation {operation!r} has no executor")
