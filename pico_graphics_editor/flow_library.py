"""Persist reusable Flow Standard v1 fragments independently of projects."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .designer_model import (
    BehaviorConnection,
    FlowGroup,
    FlowNode,
    GuiProject,
    behavior_connection_error,
    new_identifier,
)


FLOW_LIBRARY_VERSION = 2


def _fingerprint_payload(values: dict[str, Any]) -> str:
    """Return a deterministic digest for one fragment payload."""
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FlowFragment:
    """Store one portable group of behavior nodes and internal connections."""

    id: str
    name: str
    nodes: tuple[dict[str, Any], ...]
    connections: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...] = ()
    fingerprint: str = ""
    description: str = ""
    category: str = "Personal"
    tags: tuple[str, ...] = ()
    version: str = "1.0.0"
    minimum_flow_version: int = 1
    anchors: tuple[dict[str, Any], ...] = ()
    source: str = "personal"

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> FlowFragment:
        """Create and validate one JSON-compatible fragment record."""
        fragment = cls(
            str(values.get("id", "")),
            str(values.get("name", "")),
            tuple(dict(item) for item in values.get("nodes", [])),
            tuple(dict(item) for item in values.get("connections", [])),
            tuple(dict(item) for item in values.get("groups", [])),
            str(values.get("fingerprint", "")),
            str(values.get("description", "")),
            str(values.get("category", "Personal")),
            tuple(str(item) for item in values.get("tags", [])),
            str(values.get("version", "1.0.0")),
            int(values.get("minimum_flow_version", 1)),
            tuple(dict(item) for item in values.get("anchors", [])),
            str(values.get("source", "personal")),
        )
        fragment.validate()
        return fragment

    @classmethod
    def from_project(
        cls,
        fragment_id: str,
        name: str,
        project: GuiProject,
        node_ids: set[str],
    ) -> FlowFragment:
        """Capture selected behavior nodes plus their internal relationships."""
        nodes = [node for node in project.behavior_nodes if node.id in node_ids]
        if not nodes:
            raise ValueError("Select at least one behavior node to save a flow fragment")
        connections = [
            connection
            for connection in project.behavior_connections
            if connection.source_node_id in node_ids
            and connection.target_node_id in node_ids
        ]
        group_ids = {node.group_id for node in nodes if node.group_id}
        groups = [group for group in project.flow_groups if group.id in group_ids]
        payload = {
            "nodes": [asdict(node) for node in nodes],
            "connections": [asdict(connection) for connection in connections],
            "groups": [asdict(group) for group in groups],
        }
        fragment = cls(
            fragment_id,
            name.strip() or "Untitled Flow Fragment",
            tuple(payload["nodes"]),
            tuple(payload["connections"]),
            tuple(payload["groups"]),
            _fingerprint_payload(payload),
        )
        fragment.validate()
        return fragment

    def validate(self) -> None:
        """Reject malformed, externally connected, or damaged fragments."""
        if not self.id or not self.name or not self.nodes:
            raise ValueError("Flow fragments require an ID, name, and at least one node")
        nodes = [FlowNode.from_dict(item) for item in self.nodes]
        connections = [BehaviorConnection.from_dict(item) for item in self.connections]
        groups = [FlowGroup.from_dict(item) for item in self.groups]
        node_ids = [node.id for node in nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Flow fragment node IDs must be unique")
        group_ids = [group.id for group in groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("Flow fragment group IDs must be unique")
        if any(node.group_id and node.group_id not in group_ids for node in nodes):
            raise ValueError("Flow fragment references a missing group")
        for anchor in self.anchors:
            node_id = str(anchor.get("node_id", ""))
            port_id = str(anchor.get("port_id", ""))
            node = next((item for item in nodes if item.id == node_id), None)
            if node is None or node.port(port_id) is None:
                raise ValueError("Flow fragment anchor references a missing port")
        project = GuiProject.create("Fragment validation")
        project.behavior_nodes = nodes
        project.behavior_connections = connections
        project.flow_groups = groups
        for connection in connections:
            if behavior_connection_error(project, connection):
                raise ValueError("Flow fragment contains an invalid connection")
        payload = {
            "nodes": [asdict(node) for node in nodes],
            "connections": [asdict(connection) for connection in connections],
            "groups": [asdict(group) for group in groups],
        }
        if self.fingerprint and _fingerprint_payload(payload) != self.fingerprint:
            raise ValueError("Flow fragment fingerprint does not match its contents")

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-compatible fragment values."""
        return {
            "id": self.id,
            "name": self.name,
            "nodes": [dict(item) for item in self.nodes],
            "connections": [dict(item) for item in self.connections],
            "groups": [dict(item) for item in self.groups],
            "fingerprint": self.fingerprint,
            "description": self.description,
            "category": self.category,
            "tags": list(self.tags),
            "version": self.version,
            "minimum_flow_version": self.minimum_flow_version,
            "anchors": [dict(item) for item in self.anchors],
            "source": self.source,
        }


def _built_in_fragment(
    identifier: str,
    name: str,
    category: str,
    description: str,
    tags: tuple[str, ...],
    operations: tuple[str, ...],
) -> FlowFragment:
    """Build one deterministic read-only recipe from allowlisted operations."""
    project = GuiProject.create(name)
    event = FlowNode(
        f"{identifier}_event",
        "event",
        "Input event",
        80,
        100,
        ports=[],
    )
    event.ports = [
        # Keep a recipe entry structural so insertion does not invent a UI binding.
        FlowNode.create("event", 1).ports[0]
    ]
    project.behavior_nodes.append(event)
    previous = event
    previous_port = "event"
    for index, operation_id in enumerate(operations, 1):
        from .behavior_operations import operation_spec

        spec = operation_spec(operation_id)
        if spec is None:
            raise ValueError(operation_id)
        node = FlowNode(
            f"{identifier}_node_{index}",
            spec.kind,
            spec.label,
            80 + index * 260,
            100,
            ports=[],
        )
        node.set_operation(operation_id)
        if operation_id == "custom.handler":
            node.properties["handler"] = f"on_{identifier}"
        for field in spec.properties:
            if field.required and node.properties.get(field.id, "") == "":
                node.properties[field.id] = f"<{field.id}>"
        input_port = next((port for port in node.ports if port.direction == "in"), None)
        if input_port is None:
            continue
        project.behavior_nodes.append(node)
        project.behavior_connections.append(
            BehaviorConnection(
                f"{identifier}_edge_{index}",
                previous.id,
                previous_port,
                node.id,
                input_port.id,
            )
        )
        previous = node
        preferred = ("done", "success", "changed", "elapsed", "true")
        output = next(
            (node.port(port_id) for port_id in preferred if node.port(port_id)), None
        )
        if output is None:
            output = next((port for port in node.ports if port.direction == "out"), None)
        previous_port = output.id if output else ""
    fragment = FlowFragment.from_project(
        identifier,
        name,
        project,
        {node.id for node in project.behavior_nodes},
    )
    first = project.behavior_nodes[0]
    last = project.behavior_nodes[-1]
    return FlowFragment(
        fragment.id,
        fragment.name,
        fragment.nodes,
        fragment.connections,
        fragment.groups,
        fragment.fingerprint,
        description,
        category,
        tags,
        "1.0.0",
        2,
        (
            {"id": "input", "node_id": first.id, "port_id": "event", "label": "Input", "direction": "in", "data_type": "event"},
            {"id": "output", "node_id": last.id, "port_id": previous_port, "label": "Output", "direction": "out", "data_type": "event"},
        ),
        "built-in",
    )


def built_in_flow_fragments() -> tuple[FlowFragment, ...]:
    """Return tested read-only recipes for common application behavior."""
    definitions = (
        ("recipe_button_status", "Button → Action → Status", "UI", "Handle one activation and update visible status.", ("button", "status"), ("custom.handler", "ui.set_text")),
        ("recipe_form_save", "Form → Validate → Save → Back", "Forms", "Validate developer-owned form data, save settings, then return.", ("form", "settings"), ("custom.handler", "storage.save", "navigation.back")),
        ("recipe_confirm", "Confirm → Success / Cancel", "UI", "Handle a confirmation result with explicit outcome ports.", ("confirm", "cancel"), ("ui.alert",)),
        ("recipe_async", "Async task → Loading / Success / Error", "Lifecycle", "Run an asynchronous service operation with visible outcomes.", ("async", "loading", "error"), ("custom.handler", "ui.set_text")),
        ("recipe_timer", "Timer start / elapsed / stop", "Lifecycle", "Start a bounded non-blocking timer and handle elapsed state.", ("timer",), ("timer.start", "timer.cancel")),
        ("recipe_settings", "Settings load / edit / save", "Storage", "Load, edit, and save a named settings record.", ("settings", "storage"), ("storage.load", "custom.handler", "storage.save")),
        ("recipe_menu_state", "Menu selection → State update", "State", "Write one selected menu value into named state.", ("menu", "state"), ("state.set",)),
        ("recipe_mqtt_connect", "MQTT connect / connected / error", "MQTT", "Connect through injected broker settings and expose outcomes.", ("mqtt", "connect"), ("mqtt.connect",)),
        ("recipe_mqtt_publish", "MQTT publish / success / error", "MQTT", "Publish a QoS 0 message through the injected MQTT service.", ("mqtt", "publish"), ("mqtt.publish",)),
        ("recipe_mqtt_inbox", "MQTT message → inbox state / UI update", "MQTT", "Append an incoming MQTT message and update the inbox widget.", ("mqtt", "message", "inbox"), ("state.append", "ui.set_text")),
        ("recipe_wifi_retry", "Wi-Fi connect / retry / cancel", "Connectivity", "Connect to Wi-Fi with explicit retry and cancel outcomes.", ("wifi", "retry"), ("wifi.connect", "wifi.retry")),
    )
    return tuple(_built_in_fragment(*definition) for definition in definitions)


class FlowFragmentLibrary:
    """Load and atomically update one local personal flow-fragment file."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def fragments(self) -> tuple[FlowFragment, ...]:
        """Return stored fragments sorted by name and stable ID."""
        return tuple(sorted(self._load(), key=lambda item: (item.name.casefold(), item.id)))

    def all_fragments(self) -> tuple[FlowFragment, ...]:
        """Return built-in recipes and personal fragments without merging storage."""
        return tuple(
            sorted(
                (*built_in_flow_fragments(), *self._load()),
                key=lambda item: (item.source, item.name.casefold(), item.id),
            )
        )

    def add(
        self,
        name: str,
        project: GuiProject,
        node_ids: set[str],
    ) -> FlowFragment:
        """Save selected behavior nodes as an independent reusable fragment."""
        fragment = FlowFragment.from_project(
            f"flow_fragment_{uuid.uuid4().hex[:12]}", name, project, node_ids
        )
        fragments = self._load()
        fragments.append(fragment)
        self._save(fragments)
        return fragment

    def insert(
        self,
        fragment_id: str,
        project: GuiProject,
        x: int = 420,
        y: int = 120,
    ) -> list[str]:
        """Insert an independent clone and return its new node identifiers."""
        fragment = next(
            (item for item in self.all_fragments() if item.id == fragment_id), None
        )
        if fragment is None:
            raise KeyError(fragment_id)
        nodes = [FlowNode.from_dict(item) for item in fragment.nodes]
        groups = [FlowGroup.from_dict(item) for item in fragment.groups]
        connections = [
            BehaviorConnection.from_dict(item) for item in fragment.connections
        ]
        left = min(node.node_x for node in nodes)
        top = min(node.node_y for node in nodes)
        node_map = {node.id: new_identifier("node") for node in nodes}
        group_map = {group.id: new_identifier("group") for group in groups}
        for group in groups:
            group.id = group_map[group.id]
            group.node_x += x - left
            group.node_y += y - top
            project.flow_groups.append(group)
        inserted_ids: list[str] = []
        for node in nodes:
            old_id = node.id
            node.id = node_map[old_id]
            node.node_x += x - left
            node.node_y += y - top
            node.group_id = group_map.get(node.group_id, "")
            project.behavior_nodes.append(node)
            inserted_ids.append(node.id)
        for connection in connections:
            connection.id = new_identifier("behavior")
            connection.source_node_id = node_map[connection.source_node_id]
            connection.target_node_id = node_map[connection.target_node_id]
            project.behavior_connections.append(connection)
        return inserted_ids

    def rename(self, fragment_id: str, name: str) -> FlowFragment:
        """Rename a fragment without changing its stable identity."""
        label = name.strip()
        if not label:
            raise ValueError("Flow fragment name cannot be empty")
        fragments = self._load()
        for index, fragment in enumerate(fragments):
            if fragment.id != fragment_id:
                continue
            renamed = FlowFragment(
                fragment.id,
                label,
                fragment.nodes,
                fragment.connections,
                fragment.groups,
                fragment.fingerprint,
                fragment.description,
                fragment.category,
                fragment.tags,
                fragment.version,
                fragment.minimum_flow_version,
                fragment.anchors,
                fragment.source,
            )
            fragments[index] = renamed
            self._save(fragments)
            return renamed
        raise KeyError(fragment_id)

    def remove(self, fragment_id: str) -> bool:
        """Remove one stored fragment and report whether it existed."""
        fragments = self._load()
        retained = [item for item in fragments if item.id != fragment_id]
        if len(retained) == len(fragments):
            return False
        self._save(retained)
        return True

    def _load(self) -> list[FlowFragment]:
        """Read and validate the complete versioned flow library."""
        if not self.path.exists():
            return []
        values = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("Flow library root must be an object")
        version = int(values.get("format_version", 0))
        if version not in (1, FLOW_LIBRARY_VERSION):
            raise ValueError(f"Unsupported flow library format {version}")
        records = values.get("fragments", [])
        if not isinstance(records, list):
            raise ValueError("Flow library records must be a list")
        fragments = [FlowFragment.from_dict(record) for record in records]
        identifiers = [fragment.id for fragment in fragments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Flow library IDs must be unique")
        return fragments

    def _save(self, fragments: list[FlowFragment]) -> None:
        """Write the complete library through a flushed temporary sibling."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": FLOW_LIBRARY_VERSION,
            "fragments": [fragment.to_dict() for fragment in fragments],
        }
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temporary:
                json.dump(payload, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            Path(temporary_name).replace(self.path)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
