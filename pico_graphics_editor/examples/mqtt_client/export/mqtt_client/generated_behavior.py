# @picoware-generated structure=1
# @picoware-generated role=behavior
# @picoware-generated project=project_13106341
# @picoware-generator version=1.1.0
# This file is editor-owned. Regenerate it instead of editing it manually.


NODES = (('node_2ef5c21c', 'event.ui', 'on_connect_requested_7adb31', {'event': 'connect_toggle'}, {'element_id': 'element_9e44b9b1', 'event_id': 'event_9fb789e4', 'screen_id': 'screen_f508214b'}, False), ('node_7c884667', 'custom.handler', 'on_open_mqtt_session_800acc', {'handler': 'connect_or_disconnect', 'timeout_ms': 3000}, {}, False), ('node_64a88fd3', 'event.ui', 'on_publish_requested_d46cd4', {'event': 'publish_test'}, {'element_id': 'element_0eca0acd', 'event_id': 'event_38127f9e', 'screen_id': 'screen_f508214b'}, False), ('node_762c8c46', 'mqtt.publish', 'on_publish_test_payload_fda0b0', {'payload': '{"client":"picoware","seq":1}', 'retain': False, 'topic': 'demo/picoware/test'}, {}, False), ('node_ac2f9816', 'ui.set_text', 'on_refresh_dashboard_status_79e012', {'element_id': 'element_0efdd39b', 'text': 'Connected'}, {}, False), ('node_3029dc8e', 'state.append', 'on_record_received_message_20c5c5', {'key': 'messages', 'value': ''}, {}, False), ('node_mqtt_inbox_ui', 'ui.set_text', 'on_update_message_inbox_35c0e8', {'element_id': 'element_2072e2d3', 'text': ''}, {}, False))
CONNECTIONS = (('behavior_e2cc4e2f', 'node_2ef5c21c', 'event', 'node_7c884667', 'in', 'connect', ''), ('behavior_8cc07038', 'node_64a88fd3', 'event', 'node_762c8c46', 'in', 'publish', ''), ('behavior_a4b59d53', 'node_7c884667', 'done', 'node_ac2f9816', 'in', 'status', ''), ('behavior_60491876', 'node_762c8c46', 'success', 'node_3029dc8e', 'in', 'message', ''), ('behavior_mqtt_inbox_update', 'node_3029dc8e', 'changed', 'node_mqtt_inbox_ui', 'in', 'update inbox', ''))
TEST_MANIFEST = {'bindings': (('node_2ef5c21c', 'event_9fb789e4', 'screen_f508214b', 'element_9e44b9b1'), ('node_64a88fd3', 'event_38127f9e', 'screen_f508214b', 'element_0eca0acd')), 'handlers': (('node_7c884667', 'connect_or_disconnect'),), 'services': ('mqtt',)}

PAYLOAD_FIELDS = ("value", "text", "checked", "index", "event_id", "screen_id", "element_id", "widget_type")
PAYLOAD_REFERENCES = ("$payload", "$value", "$text", "$checked", "$index", "$event_id", "$screen_id", "$element_id", "$widget_type")


def _payload_field(payload, field):
    if not field:
        return payload
    if field not in PAYLOAD_FIELDS:
        raise RuntimeError("Unsupported payload field " + repr(field))
    if not isinstance(payload, dict):
        if field == "value":
            return payload
        if field == "text" and isinstance(payload, str):
            return payload
        if field == "checked" and isinstance(payload, bool):
            return payload
        if field == "index" and isinstance(payload, int) and not isinstance(payload, bool):
            return payload
    if not isinstance(payload, dict) or field not in payload:
        raise RuntimeError("Payload field " + repr(field) + " is unavailable")
    return payload[field]


def _resolve_payload_reference(value, payload):
    if not isinstance(value, str) or value not in PAYLOAD_REFERENCES:
        return value
    return payload if value == "$payload" else _payload_field(payload, value[1:])


def _widget_event_payload(ui, binding, event_id):
    element_id = binding.get("element_id", "")
    value = ui.read_value(element_id) if ui is not None and element_id else None
    widget_type = binding.get("widget_type", "")
    type_reader = getattr(ui, "widget_type", None) if ui is not None else None
    if not widget_type and callable(type_reader) and element_id:
        widget_type = type_reader(element_id) or ""
    result = {
        "event_id": event_id,
        "screen_id": binding.get("screen_id", ""),
        "element_id": element_id,
        "widget_type": widget_type,
        "value": value,
    }
    if isinstance(value, tuple) and len(value) >= 3:
        result["index"], result["text"], result["checked"] = value[0], value[1], bool(value[2])
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


class BehaviorRuntime:
    """Execute generated allowlisted behavior through injected services."""

    def __init__(self, ui, handlers, services=None, step_limit=100, trace_limit=250):
        self.ui = ui
        self.handlers = handlers
        self.services = services or {}
        self.services.setdefault("ui", ui)
        self.state = self.services.setdefault("state", {})
        self.step_limit = max(1, int(step_limit))
        self.trace_limit = max(1, int(trace_limit))
        self.trace = []
        self.paused = False
        self.pending = []

    def dispatch_event(self, event_id, payload=None):
        matched = False
        for node in NODES:
            binding = node[4]
            if node[1] == "event.ui" and binding.get("event_id") == event_id:
                event_payload = _widget_event_payload(self.ui, binding, event_id) if payload is None else payload
                self.pending.append((node[0], "event", event_payload))
                matched = True
        if matched:
            self.continue_execution()
        return matched

    def dispatch_service_event(self, operation, payload=None):
        matched = False
        for node in NODES:
            if node[1] == operation and operation.startswith("event."):
                self.pending.append((node[0], "event", payload))
                matched = True
        if matched:
            self.continue_execution()
        return matched

    def continue_execution(self):
        self.paused = False
        steps = 0
        while self.pending and not self.paused:
            if steps >= self.step_limit:
                raise RuntimeError("Behavior step limit reached")
            node_id, input_port, payload = self.pending.pop(0)
            node = self._node(node_id)
            try:
                output, result = self._execute(node, payload)
            except Exception as error:
                if any(edge[1] == node_id and edge[2] == "error" for edge in CONNECTIONS):
                    output, result = "error", {"error": str(error)}
                else:
                    raise
            self.trace.append((len(self.trace) + 1, node_id, input_port, output, self._summary(payload), self._summary(result)))
            if len(self.trace) > self.trace_limit:
                del self.trace[:-self.trace_limit]
            if node[5]:
                self.paused = True
            if output:
                self._queue_output(node_id, output, result)
                if node[1] == "event.ui" and isinstance(result, dict):
                    for field in ("value", "text", "checked", "index"):
                        if field in result:
                            self._queue_output(node_id, field, result[field])
            steps += 1

    def emit(self, node_id, output, payload=None):
        self._queue_output(node_id, output, payload)
        if not self.paused:
            self.continue_execution()

    def _queue_output(self, node_id, output, payload):
        for edge in CONNECTIONS:
            if edge[1] == node_id and edge[2] == output:
                self.pending.append((edge[3], edge[4], payload))

    def stop(self):
        self.pending = []
        self.paused = False

    def clear_trace(self):
        self.trace = []

    def _node(self, node_id):
        for node in NODES:
            if node[0] == node_id:
                return node
        raise RuntimeError("Unknown behavior node " + str(node_id))

    def _service(self, name):
        service = self.services.get(name)
        if service is None:
            raise RuntimeError("Missing " + name + " service")
        return service

    def _summary(self, payload):
        if isinstance(payload, dict):
            safe = {}
            for key, value in payload.items():
                lowered = str(key).lower()
                safe[key] = "<redacted>" if any(word in lowered for word in ("password", "secret", "token")) else value
            payload = safe
        text = repr(payload)
        return text[:160]

    def _execute(self, node, payload):
        operation, handler, values = node[1], node[2], node[3]
        if operation.startswith("event."):
            return "event", payload
        if operation == "custom.handler":
            callback = getattr(self.handlers, values.get("handler") or handler, None)
            if callback is None:
                raise RuntimeError("Missing custom handler " + handler)
            result = callback(payload, self)
            return "done", payload if result is None else result
        if operation == "navigation.navigate":
            return "done", self.ui.navigate(values["screen_id"])
        if operation == "navigation.back":
            return "done", self.ui.back()
        if operation.startswith("ui."):
            method = operation.split(".", 1)[1]
            arguments = dict((key, _resolve_payload_reference(value, payload)) for key, value in values.items())
            if method in ("set_value", "set_text", "set_progress"):
                key = "text" if method == "set_text" else "value"
                if arguments.get(key, "") == "":
                    arguments[key] = payload
            return "done", getattr(self.ui, method)(**arguments)
        if operation.startswith("state."):
            method = operation.split(".", 1)[1]
            key = values["key"]
            value = values.get("value", "")
            if value == "":
                value = payload
            value = _resolve_payload_reference(value, payload)
            if method == "get":
                result = self.state.get(key)
            elif method == "set":
                self.state[key] = value
                result = value
            elif method == "clear":
                result = self.state.pop(key, None)
            elif method == "increment":
                self.state[key] = self.state.get(key, 0) + (value if isinstance(value, (int, float)) else 1)
                result = self.state[key]
            elif method == "append":
                self.state.setdefault(key, []).append(value)
                result = self.state[key]
            else:
                self.state[key] = not bool(self.state.get(key))
                result = self.state[key]
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
        prefix, method = operation.split(".", 1)
        service = self._service(prefix)
        if operation == "timer.start":
            result = service.start(
                timer_id=_resolve_payload_reference(values["timer_id"], payload),
                milliseconds=_resolve_payload_reference(values["milliseconds"], payload),
                callback=lambda value=None: self.emit(node[0], "elapsed", value),
            )
            return "", result
        if operation == "timer.cancel":
            return "done", service.cancel(timer_id=_resolve_payload_reference(values["timer_id"], payload))
        arguments = dict((key, _resolve_payload_reference(value, payload)) for key, value in values.items())
        result = getattr(service, method)(**arguments)
        if isinstance(result, tuple) and len(result) == 2 and result[0] in ("success", "error", "cancel"):
            return result
        return "success", result
