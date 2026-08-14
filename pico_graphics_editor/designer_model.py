"""Persisted GUI designer and screen-flow project model."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .behavior_operations import (
    PAYLOAD_REFERENCES,
    operation_spec,
    validate_operation_properties,
)
from .model import PixelArt
from .native_widgets import element_supports_ui_operation
from .source import SourcePatch


DEVICE_PROFILES = {
    "PicoCalc 320x320": (320, 320),
    "Cardputer 240x135": (240, 135),
    "Flipper Zero 128x64": (128, 64),
    "Round display 240x240": (240, 240),
    "Custom": (320, 320),
}
ELEMENT_KINDS = (
    "button",
    "label",
    "panel",
    "rectangle",
    "icon",
    "list",
    "progress",
    "native",
)
FLOW_STANDARD_VERSION = 2
SUPPORTED_FLOW_STANDARD_VERSIONS = (1, 2)
FLOW_NODE_KINDS = (
    "event",
    "condition",
    "action",
    "state",
    "timer",
    "data",
    "component",
    "comment",
)
DESIGNER_MARKER = re.compile(
    r"^# Pico GUI Designer (?P<edge>begin|end)\s*$", re.MULTILINE
)


def new_identifier(prefix: str) -> str:
    """Return a concise unique project identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def stable_identifier(prefix: str, value: str) -> str:
    """Return a deterministic lowercase identifier for migrated relationships."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


@dataclass
class GuiElement:
    """Describe one draggable screen element."""

    id: str
    kind: str
    name: str
    x: int
    y: int
    width: int
    height: int
    text: str = ""
    fill_color: int = 0x2104
    border_color: int = 0xFFFF
    text_color: int = 0xFFFF
    visible: bool = True
    asset_call: str = ""
    locked: bool = False
    source_path: str = ""
    source_line: int = 0
    source_call: str = ""
    source_segment: str = ""
    source_values: dict[str, Any] = field(default_factory=dict)
    editor_locked: bool = False
    focusable: bool = False
    focus_order: int = 0
    enabled: bool = True
    event_name: str = ""
    focus_style: str = "outline"
    focus_color: int = 0xFFE0
    focus_thickness: int = 2
    focus_padding: int = 2
    asset_width: int = 0
    asset_height: int = 0
    asset_runs: list[list[int]] = field(default_factory=list)
    asset_key: str = ""
    asset_qualified_name: str = ""
    asset_source_path: str = ""
    asset_absolute_fallback: str = ""
    asset_fingerprint: str = ""
    asset_link_state: str = "detached"
    asset_id: str = ""
    asset_frame: int = 0
    event_id: str = ""
    native_widget: str = ""
    widget_items: list[str] = field(default_factory=list)
    widget_item_states: list[bool] = field(default_factory=list)
    widget_selected_index: int = 0
    widget_state: bool = False

    @classmethod
    def create(cls, kind: str, index: int) -> GuiElement:
        """Create one element with practical defaults."""
        widths = {"label": 100, "icon": 32, "progress": 120, "native": 280}
        heights = {
            "label": 20,
            "icon": 32,
            "list": 90,
            "progress": 16,
            "native": 280,
        }
        labels = {
            "button": "Button",
            "label": "Label",
            "panel": "Panel",
            "rectangle": "",
            "icon": "Icon",
            "list": "Item 1\nItem 2\nItem 3",
            "progress": "",
            "native": "",
        }
        normalized = kind if kind in ELEMENT_KINDS else "rectangle"
        element = cls(
            new_identifier("element"),
            normalized,
            f"{normalized.title()} {index}",
            16 + (index % 5) * 8,
            16 + (index % 5) * 8,
            widths.get(normalized, 120),
            heights.get(normalized, 36),
            labels.get(normalized, ""),
        )
        element.focusable = normalized in {"button", "list", "icon", "native"}
        element.focus_order = index
        element.event_id = new_identifier("event")
        return element

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> GuiElement:
        """Create an element from persisted values."""
        values = dict(values)
        values["widget_items"] = [str(item) for item in values.get("widget_items", [])]
        values["widget_item_states"] = [
            bool(item) for item in values.get("widget_item_states", [])
        ]
        if values.get("asset_key") and not values.get("asset_qualified_name"):
            source_path, separator, qualified_name = str(
                values["asset_key"]
            ).rpartition("::")
            if separator:
                values["asset_qualified_name"] = qualified_name
                values["asset_absolute_fallback"] = source_path
                values["asset_source_path"] = source_path
                values["asset_link_state"] = "missing"
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in allowed})

    def activation_event(self) -> str:
        """Return the configured activation event or the element name fallback."""
        return self.event_name.strip() or self.name

    def activation_event_id(self) -> str:
        """Return the stable event identity used by generated applications."""
        return self.event_id


@dataclass
class ProjectAsset:
    """Store one canonical linked asset or detached project snapshot."""

    id: str
    name: str
    width: int
    height: int
    origin_x: int = 0
    origin_y: int = 0
    frames: list[list[int | None]] = field(default_factory=list)
    durations: list[int] = field(default_factory=list)
    source_path: str = ""
    absolute_fallback: str = ""
    qualified_name: str = ""
    fingerprint: str = ""
    link_state: str = "detached"

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ProjectAsset:
        """Create a project asset from JSON-compatible values."""
        data = dict(values)
        data["frames"] = [list(frame) for frame in values.get("frames", [])]
        data["durations"] = [int(value) for value in values.get("durations", [])]
        allowed = cls.__dataclass_fields__
        asset = cls(**{key: value for key, value in data.items() if key in allowed})
        asset.validate()
        return asset

    @classmethod
    def from_pixel_art(
        cls,
        asset_id: str,
        name: str,
        art: PixelArt,
        *,
        source_path: str = "",
        absolute_fallback: str = "",
        qualified_name: str = "",
        fingerprint: str = "",
        link_state: str = "detached",
    ) -> ProjectAsset:
        """Create one project-side asset without runtime compression."""
        asset = cls(
            asset_id,
            name,
            art.width,
            art.height,
            art.origin_x,
            art.origin_y,
            [list(art.pixels)],
            [],
            source_path,
            absolute_fallback,
            qualified_name,
            fingerprint,
            link_state,
        )
        asset.validate()
        return asset

    def validate(self) -> None:
        """Reject incomplete editable asset snapshots."""
        if not self.id:
            raise ValueError("Project assets require a stable ID")
        if self.width < 1 or self.height < 1:
            raise ValueError("Project asset dimensions must be positive")
        if not self.frames:
            raise ValueError("Project assets require at least one frame")
        expected = self.width * self.height
        for frame in self.frames:
            if len(frame) != expected:
                raise ValueError("Project asset frame dimensions do not match")
            for color in frame:
                if color is not None and (
                    type(color) is not int or not 0 <= color <= 0xFFFF
                ):
                    raise ValueError(
                        "Project asset pixels must be RGB565 or transparent"
                    )
        if self.durations and len(self.durations) != len(self.frames):
            raise ValueError("Project asset durations must match its frames")
        if any(type(value) is not int or value <= 0 for value in self.durations):
            raise ValueError("Project asset durations must be positive integers")

    def pixel_frames(self) -> list[PixelArt]:
        """Return lossless desktop frame objects for compact generation."""
        self.validate()
        return [
            PixelArt(
                self.width,
                self.height,
                self.origin_x,
                self.origin_y,
                list(pixels),
            )
            for pixels in self.frames
        ]


@dataclass
class ScreenDesign:
    """Describe one application screen and its elements."""

    id: str
    name: str
    width: int
    height: int
    background_color: int = 0x0000
    elements: list[GuiElement] = field(default_factory=list)
    node_x: int = 80
    node_y: int = 80
    source_path: str = ""
    source_name: str = ""
    source_line: int = 0
    source_state: Any = None

    @classmethod
    def create(cls, name: str, width: int, height: int, index: int) -> ScreenDesign:
        """Create one empty screen at a staggered graph position."""
        return cls(
            new_identifier("screen"),
            name,
            width,
            height,
            node_x=70 + (index % 4) * 300,
            node_y=70 + (index // 4) * 200,
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ScreenDesign:
        """Create a screen from persisted values."""
        data = dict(values)
        data["elements"] = [
            GuiElement.from_dict(item) for item in values.get("elements", [])
        ]
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class FlowConnection:
    """Describe one event-driven screen transition."""

    id: str
    source_id: str
    target_id: str
    trigger: str = "select"
    condition: str = ""
    action: str = ""
    transition: str = "replace"
    locked: bool = False
    source_path: str = ""
    source_line: int = 0
    source_trigger_segment: str = ""
    source_assignment_segment: str = ""
    source_values: dict[str, Any] = field(default_factory=dict)
    source_element_id: str = ""
    target_element_id: str = ""
    trigger_event_id: str = ""

    @classmethod
    def create(
        cls,
        source_id: str,
        target_id: str,
        trigger: str,
        source_element_id: str = "",
        target_element_id: str = "",
    ) -> FlowConnection:
        """Create one screen-flow connection."""
        connection = cls(new_identifier("flow"), source_id, target_id, trigger)
        connection.source_element_id = source_element_id
        connection.target_element_id = target_element_id
        connection.trigger_event_id = new_identifier("event")
        return connection

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> FlowConnection:
        """Create a connection from persisted values."""
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass
class FlowPort:
    """Describe one typed behavior-node input or output contract."""

    id: str
    name: str
    direction: str
    data_type: str = "event"
    required: bool = False
    multiple: bool = False

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> FlowPort:
        """Create one persisted typed port."""
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in allowed})


def default_flow_ports(kind: str) -> list[FlowPort]:
    """Return the quasi-standard ports for one behavior-node kind."""
    specifications = {
        "event": (("event", "Event", "out", "event", False, True),),
        "condition": (
            ("in", "Evaluate", "in", "event", True, False),
            ("true", "True", "out", "event", False, False),
            ("false", "False", "out", "event", False, False),
        ),
        "action": (
            ("in", "Run", "in", "event", True, False),
            ("done", "Done", "out", "event", False, False),
        ),
        "state": (
            ("set", "Set", "in", "data", False, False),
            ("changed", "Changed", "out", "data", False, True),
        ),
        "timer": (
            ("start", "Start", "in", "event", False, False),
            ("stop", "Stop", "in", "event", False, False),
            ("elapsed", "Elapsed", "out", "event", False, True),
        ),
        "data": (("value", "Value", "out", "data", False, True),),
        "component": (
            ("invoke", "Invoke", "in", "event", False, True),
            ("done", "Done", "out", "event", False, True),
        ),
        "comment": (),
    }
    normalized = kind if kind in FLOW_NODE_KINDS else "action"
    return [FlowPort(*values) for values in specifications[normalized]]


@dataclass
class FlowNode:
    """Describe one typed structural behavior node."""

    id: str
    kind: str
    name: str
    node_x: int
    node_y: int
    description: str = ""
    ports: list[FlowPort] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    group_id: str = ""
    locked: bool = False
    pinned: bool = False
    breakpoint: bool = False
    operation: str = ""
    binding: dict[str, str] = field(default_factory=dict)
    component_ref: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, kind: str, index: int, x: int = 360, y: int = 80) -> FlowNode:
        """Create one behavior node with stable default ports."""
        normalized = kind if kind in FLOW_NODE_KINDS else "action"
        label = normalized.title()
        return cls(
            new_identifier("node"),
            normalized,
            f"{label} {index}",
            x,
            y,
            ports=default_flow_ports(normalized),
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> FlowNode:
        """Create one persisted behavior node with migrated default ports."""
        data = dict(values)
        kind = str(data.get("kind", "action"))
        data["kind"] = kind if kind in FLOW_NODE_KINDS else "action"
        persisted_ports = data.get("ports", [])
        data["ports"] = (
            [FlowPort.from_dict(port) for port in persisted_ports]
            if persisted_ports
            else default_flow_ports(data["kind"])
        )
        data["properties"] = dict(data.get("properties", {}))
        data["binding"] = {
            str(key): str(value) for key, value in dict(data.get("binding", {})).items()
        }
        data["component_ref"] = {
            str(key): str(value)
            for key, value in dict(data.get("component_ref", {})).items()
        }
        allowed = cls.__dataclass_fields__
        node = cls(**{key: value for key, value in data.items() if key in allowed})
        if node.operation == "event.ui":
            operation = operation_spec(node.operation)
            if operation is not None:
                existing = {port.id for port in node.ports}
                node.ports.extend(
                    FlowPort(
                        port.id,
                        port.label,
                        port.direction,
                        port.data_type,
                        port.required,
                        port.multiple,
                    )
                    for port in operation.ports
                    if port.id not in existing
                )
        return node

    def port(self, port_id: str) -> FlowPort | None:
        """Return one typed port by its stable local identifier."""
        return next((port for port in self.ports if port.id == port_id), None)

    def set_operation(self, operation_id: str) -> None:
        """Apply one allowlisted operation and its typed port contract."""
        operation = operation_spec(operation_id)
        if operation is None:
            raise ValueError(f"Unknown behavior operation {operation_id!r}")
        if operation.kind != self.kind:
            raise ValueError(
                f"Operation {operation_id!r} requires a {operation.kind} node"
            )
        self.operation = operation.id
        self.ports = [
            FlowPort(
                port.id,
                port.label,
                port.direction,
                port.data_type,
                port.required,
                port.multiple,
            )
            for port in operation.ports
        ]
        known = {field.id for field in operation.properties}
        self.properties = {
            **{
                key: value for key, value in self.properties.items() if key not in known
            },
            **{
                field.id: self.properties.get(field.id, field.default)
                for field in operation.properties
            },
        }


@dataclass
class BehaviorConnection:
    """Connect two typed behavior-node ports without implementing their logic."""

    id: str
    source_node_id: str
    source_port_id: str
    target_node_id: str
    target_port_id: str
    label: str = ""
    condition: str = ""
    locked: bool = False

    @classmethod
    def create(
        cls,
        source_node_id: str,
        source_port_id: str,
        target_node_id: str,
        target_port_id: str,
        label: str = "",
    ) -> BehaviorConnection:
        """Create one typed behavior connection."""
        return cls(
            new_identifier("behavior"),
            source_node_id,
            source_port_id,
            target_node_id,
            target_port_id,
            label,
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> BehaviorConnection:
        """Create one persisted behavior connection."""
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass(frozen=True)
class FlowNodeKindChange:
    """Preview one node-kind change without mutating the project."""

    kind: str
    ports: tuple[FlowPort, ...]
    endpoint_updates: tuple[tuple[str, str, str], ...]
    removed_connection_ids: tuple[str, ...]
    locked_connection_ids: tuple[str, ...]


def preview_flow_node_kind_change(
    project: GuiProject,
    node: FlowNode,
    kind: str,
) -> FlowNodeKindChange:
    """Classify preserved, remappable, and removable edges for a kind change."""
    normalized = kind if kind in FLOW_NODE_KINDS else "action"
    return preview_flow_node_port_change(
        project, node, normalized, default_flow_ports(normalized)
    )


def preview_flow_node_port_change(
    project: GuiProject,
    node: FlowNode,
    kind: str,
    new_ports: list[FlowPort] | tuple[FlowPort, ...],
) -> FlowNodeKindChange:
    """Classify connection impact for an explicit typed port contract."""
    normalized = kind if kind in FLOW_NODE_KINDS else "action"
    ports = tuple(new_ports)

    def replacement(port_id: str, direction: str, data_type: str) -> str | None:
        same = next(
            (
                port
                for port in ports
                if port.id == port_id
                and port.direction == direction
                and (
                    port.data_type == data_type or "any" in {port.data_type, data_type}
                )
            ),
            None,
        )
        if same is not None:
            return same.id
        candidates = [
            port
            for port in ports
            if port.direction == direction
            and (port.data_type == data_type or "any" in {port.data_type, data_type})
        ]
        return candidates[0].id if len(candidates) == 1 else None

    updates: list[tuple[str, str, str]] = []
    removed: list[str] = []
    locked: list[str] = []
    for connection in project.behavior_connections:
        source_port_id = connection.source_port_id
        target_port_id = connection.target_port_id
        valid = True
        if connection.source_node_id == node.id:
            old_port = node.port(connection.source_port_id)
            mapped = (
                replacement(old_port.id, "out", old_port.data_type)
                if old_port is not None
                else None
            )
            if mapped is None:
                valid = False
            else:
                source_port_id = mapped
        if connection.target_node_id == node.id:
            old_port = node.port(connection.target_port_id)
            mapped = (
                replacement(old_port.id, "in", old_port.data_type)
                if old_port is not None
                else None
            )
            if mapped is None:
                valid = False
            else:
                target_port_id = mapped
        if not valid:
            removed.append(connection.id)
            if connection.locked:
                locked.append(connection.id)
            continue
        updates.append((connection.id, source_port_id, target_port_id))
        if connection.locked and (
            source_port_id != connection.source_port_id
            or target_port_id != connection.target_port_id
        ):
            locked.append(connection.id)
    return FlowNodeKindChange(
        normalized,
        ports,
        tuple(updates),
        tuple(removed),
        tuple(locked),
    )


@dataclass
class FlowGroup:
    """Organize related behavior nodes without changing runtime meaning."""

    id: str
    name: str
    node_x: int
    node_y: int
    width: int
    height: int
    color: str = "#455a64"
    collapsed: bool = False

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> FlowGroup:
        """Create one persisted visual behavior group."""
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass(frozen=True)
class FlowDiagnostic:
    """Report one navigable structural flow problem."""

    severity: str
    code: str
    message: str
    target_kind: str = "project"
    target_id: str = ""


@dataclass
class GuiProject:
    """Store editable screens and their navigation graph."""

    name: str
    profile: str
    width: int
    height: int
    screens: list[ScreenDesign]
    connections: list[FlowConnection] = field(default_factory=list)
    start_screen_id: str = ""
    format_version: int = 8
    import_root: str = ""
    imported_sources: dict[str, str] = field(default_factory=dict)
    project_id: str = ""
    assets: list[ProjectAsset] = field(default_factory=list)
    generated_app: dict[str, Any] = field(default_factory=dict)
    flow_standard_version: int = FLOW_STANDARD_VERSION
    behavior_nodes: list[FlowNode] = field(default_factory=list)
    behavior_connections: list[BehaviorConnection] = field(default_factory=list)
    flow_groups: list[FlowGroup] = field(default_factory=list)

    @classmethod
    def create(
        cls, name: str = "Untitled GUI", profile: str = "PicoCalc 320x320"
    ) -> GuiProject:
        """Create a project with one initial screen."""
        width, height = DEVICE_PROFILES.get(profile, (320, 320))
        screen = ScreenDesign.create("Main", width, height, 0)
        return cls(
            name=name,
            profile=profile,
            width=width,
            height=height,
            screens=[screen],
            connections=[],
            start_screen_id=screen.id,
            project_id=new_identifier("project"),
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> GuiProject:
        """Create a project from validated JSON-compatible values."""
        original_format = int(values.get("format_version", 1))
        flow_version = int(values.get("flow_standard_version", FLOW_STANDARD_VERSION))
        if flow_version not in SUPPORTED_FLOW_STANDARD_VERSIONS:
            raise ValueError(f"Unsupported flow standard {flow_version}")
        screens = [ScreenDesign.from_dict(item) for item in values.get("screens", [])]
        connections = [
            FlowConnection.from_dict(item) for item in values.get("connections", [])
        ]
        assets = [ProjectAsset.from_dict(item) for item in values.get("assets", [])]
        behavior_nodes = [
            FlowNode.from_dict(item) for item in values.get("behavior_nodes", [])
        ]
        behavior_connections = [
            BehaviorConnection.from_dict(item)
            for item in values.get("behavior_connections", [])
        ]
        flow_groups = [
            FlowGroup.from_dict(item) for item in values.get("flow_groups", [])
        ]
        project = cls(
            name=str(values.get("name", "Untitled GUI")),
            profile=str(values.get("profile", "Custom")),
            width=max(1, int(values.get("width", 320))),
            height=max(1, int(values.get("height", 320))),
            screens=screens,
            connections=connections,
            start_screen_id=str(values.get("start_screen_id", "")),
            format_version=8,
            import_root=str(values.get("import_root", "")),
            imported_sources={
                str(path): str(digest)
                for path, digest in values.get("imported_sources", {}).items()
            },
            project_id=str(values.get("project_id", "")),
            assets=assets,
            generated_app=dict(values.get("generated_app", {})),
            flow_standard_version=flow_version,
            behavior_nodes=behavior_nodes,
            behavior_connections=behavior_connections,
            flow_groups=flow_groups,
        )
        if not project.screens:
            project.screens.append(
                ScreenDesign.create("Main", project.width, project.height, 0)
            )
        if not project.screen(project.start_screen_id):
            project.start_screen_id = project.screens[0].id
        project._migrate_v8_relationships(original_format)
        return project

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible project values."""
        return asdict(self)

    def screen(self, screen_id: str) -> ScreenDesign | None:
        """Return a screen by identifier."""
        return next((screen for screen in self.screens if screen.id == screen_id), None)

    def element(self, screen_id: str, element_id: str) -> GuiElement | None:
        """Return one element when it belongs to the requested screen."""
        screen = self.screen(screen_id)
        if screen is None or not element_id:
            return None
        return next(
            (element for element in screen.elements if element.id == element_id),
            None,
        )

    def asset(self, asset_id: str) -> ProjectAsset | None:
        """Return one project asset by stable identifier."""
        return next((asset for asset in self.assets if asset.id == asset_id), None)

    def flow_node(self, node_id: str) -> FlowNode | None:
        """Return one behavior node by stable identifier."""
        return next((node for node in self.behavior_nodes if node.id == node_id), None)

    def behavior_connection(self, connection_id: str) -> BehaviorConnection | None:
        """Return one typed behavior connection by stable identifier."""
        return next(
            (
                connection
                for connection in self.behavior_connections
                if connection.id == connection_id
            ),
            None,
        )

    def flow_group(self, group_id: str) -> FlowGroup | None:
        """Return one visual flow group by stable identifier."""
        return next((group for group in self.flow_groups if group.id == group_id), None)

    def upsert_asset(self, asset: ProjectAsset) -> ProjectAsset:
        """Add or update one canonical project asset in place."""
        asset.validate()
        existing = self.asset(asset.id)
        if existing is None:
            self.assets.append(asset)
            return asset
        existing.__dict__.update(asset.__dict__)
        return existing

    def _migrate_v8_relationships(self, original_format: int) -> None:
        """Add stable v8 identities without writing the opened project file."""
        if not self.project_id:
            identity = json.dumps(
                [
                    self.name,
                    self.start_screen_id,
                    [screen.id for screen in self.screens],
                ],
                separators=(",", ":"),
            )
            self.project_id = stable_identifier("project", identity)

        for screen in self.screens:
            for element in screen.elements:
                if not element.event_id:
                    element.event_id = stable_identifier(
                        "event", f"{self.project_id}:{screen.id}:{element.id}"
                    )

        for screen in self.screens:
            alerts = [
                element
                for element in screen.elements
                if element.kind == "native" and element.native_widget == "alert"
            ]
            for alert in alerts:
                alert.focusable = True
                alert.enabled = True
            unbound = [
                connection
                for connection in self.connections
                if connection.source_id == screen.id
                and not connection.source_element_id
                and connection.trigger_event_id != "event_navigation_back_01"
            ]
            if len(alerts) == 1 and len(unbound) == 1:
                alert = alerts[0]
                connection = unbound[0]
                connection.source_element_id = alert.id
                connection.trigger = alert.activation_event()
                connection.trigger_event_id = alert.event_id

        migrated_by_key: dict[str, ProjectAsset] = {
            asset.id: asset for asset in self.assets
        }
        group_ids: dict[str, str] = {}
        for screen in self.screens:
            for element in screen.elements:
                if element.asset_id and element.asset_id in migrated_by_key:
                    continue
                if not (
                    element.kind == "icon"
                    and element.asset_width > 0
                    and element.asset_height > 0
                ):
                    continue
                linked = bool(
                    element.asset_key
                    or element.asset_source_path
                    or element.asset_absolute_fallback
                    or element.asset_qualified_name
                ) and element.asset_link_state not in {"detached", "draft"}
                if linked:
                    source_identity = (
                        element.asset_source_path
                        or element.asset_absolute_fallback
                        or element.asset_key
                    )
                    group_key = "linked:" + ":".join(
                        (
                            source_identity,
                            element.asset_qualified_name or element.asset_call,
                            element.asset_fingerprint,
                        )
                    )
                    asset_id = group_ids.setdefault(
                        group_key, stable_identifier("asset", group_key)
                    )
                else:
                    asset_id = _snapshot_identifier(element.id)
                element.asset_id = asset_id
                if asset_id in migrated_by_key:
                    continue
                art = _legacy_element_art(element)
                project_asset = ProjectAsset.from_pixel_art(
                    asset_id,
                    element.asset_call or element.name,
                    art,
                    source_path=element.asset_source_path,
                    absolute_fallback=element.asset_absolute_fallback,
                    qualified_name=element.asset_qualified_name or element.asset_call,
                    fingerprint=element.asset_fingerprint,
                    link_state=element.asset_link_state,
                )
                self.assets.append(project_asset)
                migrated_by_key[asset_id] = project_asset

        for connection in self.connections:
            if connection.trigger_event_id:
                continue
            source = self.screen(connection.source_id)
            source_element = self.element(
                connection.source_id, connection.source_element_id
            )
            if source_element is None and source is not None:
                source_element = next(
                    (
                        element
                        for element in source.elements
                        if element.activation_event() == connection.trigger
                    ),
                    None,
                )
            connection.trigger_event_id = (
                source_element.event_id
                if source_element is not None
                else stable_identifier(
                    "event",
                    f"{self.project_id}:{connection.source_id}:{connection.trigger}",
                )
            )

    def save(self, path: str | Path) -> None:
        """Atomically save the editable designer project."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, text=True
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temporary:
                json.dump(self.to_dict(), temporary, indent=2, sort_keys=True)
                temporary.write("\n")
            Path(temporary_name).replace(target)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: str | Path) -> GuiProject:
        """Load one editable designer project."""
        project_path = Path(path).resolve()
        values = json.loads(project_path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("GUI project root must be an object")
        project = cls.from_dict(values)
        for screen in project.screens:
            for element in screen.elements:
                if not element.asset_qualified_name:
                    continue
                relative = Path(element.asset_source_path)
                candidate = (
                    (project_path.parent / relative).resolve()
                    if element.asset_source_path and not relative.is_absolute()
                    else relative
                )
                fallback = Path(element.asset_absolute_fallback)
                source = candidate if candidate.is_file() else fallback
                if source.is_file():
                    element.asset_key = (
                        f"{source.resolve()}::{element.asset_qualified_name}"
                    )
                else:
                    element.asset_link_state = "missing"
        return project


def flow_stub_name(node: FlowNode) -> str:
    """Return a stable readable method name for a behavior contract stub."""
    label = re.sub(r"[^a-z0-9_]+", "_", node.name.lower()).strip("_")
    if not label:
        label = node.kind
    if label[0].isdigit():
        label = f"node_{label}"
    suffix = hashlib.sha256(node.id.encode("utf-8")).hexdigest()[:6]
    return f"on_{label}_{suffix}"


def behavior_connection_error(
    project: GuiProject,
    connection: BehaviorConnection,
) -> str:
    """Return an incompatibility explanation or an empty valid result."""
    source = project.flow_node(connection.source_node_id)
    target = project.flow_node(connection.target_node_id)
    if source is None or target is None:
        return "references a missing behavior node"
    source_port = source.port(connection.source_port_id)
    target_port = target.port(connection.target_port_id)
    if source_port is None or target_port is None:
        return "references a missing behavior port"
    if source_port.direction != "out" or target_port.direction != "in":
        return "must connect an output port to an input port"
    compatible = source_port.data_type == target_port.data_type or "any" in {
        source_port.data_type,
        target_port.data_type,
    }
    if not compatible:
        return f"cannot connect {source_port.data_type} to {target_port.data_type}"
    if not target_port.multiple:
        competing = [
            item
            for item in project.behavior_connections
            if item.id != connection.id
            and item.target_node_id == connection.target_node_id
            and item.target_port_id == connection.target_port_id
        ]
        if competing:
            return "targets a single-input port that is already connected"
    return ""


def flow_diagnostics(project: GuiProject) -> list[FlowDiagnostic]:
    """Return deterministic navigation and behavior-graph diagnostics."""
    diagnostics: list[FlowDiagnostic] = []
    screen_ids = {screen.id for screen in project.screens}
    reachable = {project.start_screen_id}
    queue = [project.start_screen_id]
    while queue:
        source_id = queue.pop(0)
        for connection in project.connections:
            if connection.source_id != source_id or connection.target_id in reachable:
                continue
            if connection.target_id in screen_ids:
                reachable.add(connection.target_id)
                queue.append(connection.target_id)
    for screen in project.screens:
        if screen.id not in reachable:
            diagnostics.append(
                FlowDiagnostic(
                    "warning",
                    "unreachable-screen",
                    f"Screen {screen.name!r} cannot be reached from the start screen.",
                    "screen",
                    screen.id,
                )
            )
        outgoing = [
            connection
            for connection in project.connections
            if connection.source_id == screen.id
        ]
        if screen.id != project.start_screen_id and not outgoing:
            diagnostics.append(
                FlowDiagnostic(
                    "info",
                    "terminal-screen",
                    f"Screen {screen.name!r} has no outgoing navigation.",
                    "screen",
                    screen.id,
                )
            )
    triggers: set[tuple[str, str]] = set()
    for connection in project.connections:
        source = project.screen(connection.source_id)
        target = project.screen(connection.target_id)
        relation = (
            f"{source.name if source else connection.source_id} -- "
            f"{connection.trigger or '(missing trigger)'} --> "
            f"{target.name if target else connection.target_id}"
        )
        key = (connection.source_id, connection.trigger.strip().casefold())
        if not connection.trigger.strip():
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "missing-trigger",
                    "A navigation relation has no trigger.",
                    "navigation-connection",
                    connection.id,
                )
            )
        elif key in triggers:
            diagnostics.append(
                FlowDiagnostic(
                    "warning",
                    "duplicate-trigger",
                    f"More than one navigation relation handles {connection.trigger!r}.",
                    "navigation-connection",
                    connection.id,
                )
            )
        triggers.add(key)
        if connection.condition.strip():
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "unsupported-navigation-condition",
                    f"Navigation {relation!r} uses unsupported Condition "
                    f"{connection.condition!r}. Convert it to a bound behavior node.",
                    "navigation-connection",
                    connection.id,
                )
            )
        if connection.action.strip():
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "unsupported-navigation-action",
                    f"Navigation {relation!r} uses unsupported Action "
                    f"{connection.action!r}. Convert it to a bound behavior node.",
                    "navigation-connection",
                    connection.id,
                )
            )

    node_ids = [node.id for node in project.behavior_nodes]
    if len(node_ids) != len(set(node_ids)):
        diagnostics.append(
            FlowDiagnostic(
                "error", "duplicate-node-id", "Behavior node IDs must be unique."
            )
        )
    connection_ids = [connection.id for connection in project.behavior_connections]
    if len(connection_ids) != len(set(connection_ids)):
        diagnostics.append(
            FlowDiagnostic(
                "error",
                "duplicate-behavior-connection-id",
                "Behavior connection IDs must be unique.",
            )
        )
    names: dict[str, str] = {}
    incoming: set[tuple[str, str]] = set()
    outgoing: set[tuple[str, str]] = set()
    for connection in project.behavior_connections:
        error = behavior_connection_error(project, connection)
        if error:
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "invalid-behavior-connection",
                    f"Behavior connection {connection.label or connection.id!r} {error}.",
                    "behavior-connection",
                    connection.id,
                )
            )
        if connection.condition.strip():
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "unsupported-behavior-condition",
                    "Behavior connection conditions are not executable. Add a "
                    "Condition node and connect its True and False ports instead.",
                    "behavior-connection",
                    connection.id,
                )
            )
        incoming.add((connection.target_node_id, connection.target_port_id))
        outgoing.add((connection.source_node_id, connection.source_port_id))
    group_ids = {group.id for group in project.flow_groups}
    behavior_adjacency: dict[str, set[str]] = {
        node.id: set() for node in project.behavior_nodes
    }
    for connection in project.behavior_connections:
        if connection.source_node_id in behavior_adjacency:
            behavior_adjacency[connection.source_node_id].add(connection.target_node_id)
    behavior_entries = {
        node.id
        for node in project.behavior_nodes
        if node.operation.startswith("event.") or node.kind == "event"
    }
    behavior_reachable = set(behavior_entries)
    behavior_queue = list(behavior_entries)
    while behavior_queue:
        source_id = behavior_queue.pop(0)
        for target_id in behavior_adjacency.get(source_id, set()):
            if target_id not in behavior_reachable:
                behavior_reachable.add(target_id)
                behavior_queue.append(target_id)
    if project.behavior_nodes and not behavior_entries:
        diagnostics.append(
            FlowDiagnostic(
                "warning",
                "missing-behavior-entry",
                "Behavior nodes exist, but no Event node starts the flow.",
            )
        )
    for node in project.behavior_nodes:
        if project.flow_standard_version >= 2 and not node.operation:
            diagnostics.append(
                FlowDiagnostic(
                    "warning",
                    "structural-only-node",
                    f"Behavior node {node.name!r} has no executable Operation.",
                    "node",
                    node.id,
                )
            )
        if node.operation:
            operation = operation_spec(node.operation)
            if operation is None:
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "unknown-behavior-operation",
                        f"Behavior node {node.name!r} uses unknown operation "
                        f"{node.operation!r}.",
                        "node",
                        node.id,
                    )
                )
            elif operation.kind != node.kind:
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "operation-kind-mismatch",
                        f"Operation {operation.label!r} requires a "
                        f"{operation.kind.title()} node.",
                        "node",
                        node.id,
                    )
                )
            else:
                for message in validate_operation_properties(
                    operation, node.properties
                ):
                    diagnostics.append(
                        FlowDiagnostic(
                            "error",
                            "invalid-operation-property",
                            f"{node.name}: {message}.",
                            "node",
                            node.id,
                        )
                    )
                all_element_ids = {
                    element.id
                    for screen in project.screens
                    for element in screen.elements
                }
                elements_by_id = {
                    element.id: element
                    for screen in project.screens
                    for element in screen.elements
                }
                for field in operation.properties:
                    value = str(node.properties.get(field.id, "") or "")
                    if value.startswith("$") and value not in PAYLOAD_REFERENCES:
                        diagnostics.append(
                            FlowDiagnostic(
                                "error",
                                "invalid-payload-reference",
                                f"{node.name}: {field.label} uses unsupported payload "
                                f"reference {value!r}.",
                                "node",
                                node.id,
                            )
                        )
                    missing_reference = (
                        field.value_type == "screen" and project.screen(value) is None
                    ) or (
                        field.value_type == "element" and value not in all_element_ids
                    )
                    if value and missing_reference:
                        diagnostics.append(
                            FlowDiagnostic(
                                "error",
                                "invalid-operation-reference",
                                f"{node.name}: {field.label} references missing ID "
                                f"{value!r}.",
                                "node",
                                node.id,
                            )
                        )
                    elif field.value_type == "element" and value:
                        target = elements_by_id[value]
                        if not element_supports_ui_operation(
                            operation.id,
                            target.kind,
                            target.native_widget,
                            focusable=target.focusable,
                        ):
                            diagnostics.append(
                                FlowDiagnostic(
                                    "error",
                                    "unsupported-operation-target",
                                    f"{node.name}: {operation.label} is not supported "
                                    f"by element {target.name!r}.",
                                    "node",
                                    node.id,
                                )
                            )
        if node.operation == "event.ui":
            screen_id = node.binding.get("screen_id", "")
            element_id = node.binding.get("element_id", "")
            event_id = node.binding.get("event_id", "")
            screen = project.screen(screen_id)
            element = (
                project.element(screen_id, element_id)
                if screen is not None and element_id
                else None
            )
            if screen is None or element is None or element.event_id != event_id:
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "invalid-ui-event-binding",
                        f"UI Event {node.name!r} does not resolve to its stable element event.",
                        "node",
                        node.id,
                    )
                )
        port_ids = [port.id for port in node.ports]
        if len(port_ids) != len(set(port_ids)):
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "duplicate-port-id",
                    f"Behavior node {node.name!r} has duplicate port IDs.",
                    "node",
                    node.id,
                )
            )
        for port in node.ports:
            if port.direction not in {"in", "out"}:
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "invalid-port-direction",
                        f"{node.name}.{port.name} has invalid direction {port.direction!r}.",
                        "node",
                        node.id,
                    )
                )
            if not port.data_type.strip():
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "missing-port-type",
                        f"{node.name}.{port.name} has no data type.",
                        "node",
                        node.id,
                    )
                )
        folded = node.name.strip().casefold()
        if not node.name.strip():
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "missing-node-name",
                    "A behavior node has no display name.",
                    "node",
                    node.id,
                )
            )
        elif folded in names:
            diagnostics.append(
                FlowDiagnostic(
                    "warning",
                    "duplicate-node-name",
                    f"Behavior node name {node.name!r} is used more than once.",
                    "node",
                    node.id,
                )
            )
        names[folded] = node.id
        if node.group_id and node.group_id not in group_ids:
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "missing-group",
                    f"Behavior node {node.name!r} references a missing group.",
                    "node",
                    node.id,
                )
            )
        for port in node.ports:
            if (
                port.direction == "in"
                and port.required
                and (node.id, port.id) not in incoming
            ):
                diagnostics.append(
                    FlowDiagnostic(
                        "warning",
                        "required-input-unconnected",
                        f"{node.name}.{port.name} requires an incoming connection.",
                        "node",
                        node.id,
                    )
                )
        connected_node_ids = {item[0] for item in incoming | outgoing}
        if node.kind in {"action", "state", "data", "component"} and (
            node.id not in connected_node_ids
        ):
            diagnostics.append(
                FlowDiagnostic(
                    "info",
                    "orphan-node",
                    f"Behavior node {node.name!r} is not connected.",
                    "node",
                    node.id,
                )
            )
        if (
            behavior_entries
            and node.id not in behavior_reachable
            and node.kind != "comment"
        ):
            diagnostics.append(
                FlowDiagnostic(
                    "warning",
                    "unreachable-behavior-node",
                    f"Behavior node {node.name!r} cannot be reached from an Event.",
                    "node",
                    node.id,
                )
            )
        if node.kind == "event" and not any(
            source_node_id == node.id for source_node_id, unused_port in outgoing
        ):
            diagnostics.append(
                FlowDiagnostic(
                    "warning",
                    "event-unconnected",
                    f"Event {node.name!r} has no outgoing behavior connection.",
                    "node",
                    node.id,
                )
            )
        if node.kind == "condition":
            for port_id in ("true", "false"):
                if (node.id, port_id) not in outgoing:
                    diagnostics.append(
                        FlowDiagnostic(
                            "warning",
                            "condition-branch-unconnected",
                            f"Condition {node.name!r} has no {port_id} branch.",
                            "node",
                            node.id,
                        )
                    )

    adjacency: dict[str, set[str]] = {node.id: set() for node in project.behavior_nodes}
    for connection in project.behavior_connections:
        if (
            connection.source_node_id in adjacency
            and connection.target_node_id in adjacency
        ):
            adjacency[connection.source_node_id].add(connection.target_node_id)
    visiting: set[str] = set()
    visited: set[str] = set()
    cycle_nodes: set[str] = set()

    def visit(node_id: str, path: list[str]) -> None:
        if node_id in visiting:
            cycle_nodes.update(path[path.index(node_id) :])
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        path.append(node_id)
        for target_id in adjacency.get(node_id, ()):
            visit(target_id, path)
        path.pop()
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in adjacency:
        visit(node_id, [])
    if cycle_nodes and not any(
        node.id in cycle_nodes and node.kind == "timer"
        for node in project.behavior_nodes
    ):
        first_id = sorted(cycle_nodes)[0]
        diagnostics.append(
            FlowDiagnostic(
                "warning",
                "unbounded-behavior-cycle",
                "Behavior cycle has no Timer node to describe an explicit boundary.",
                "node",
                first_id,
            )
        )
    return diagnostics


def _snapshot_identifier(element_id: str) -> str:
    """Return the documented per-element snapshot identity."""
    normalized = re.sub(r"[^a-z0-9_]+", "_", element_id.lower()).strip("_")
    return f"snapshot_{normalized or stable_identifier('element', element_id)}"


def asset_element_runtime_scale(
    element: GuiElement,
    asset: ProjectAsset | None,
) -> int | None:
    """Return a valid uniform integer runtime scale, or no valid scale."""
    if asset is None or element.width < 1 or element.height < 1:
        return None
    if element.width % asset.width or element.height % asset.height:
        return None
    scale_x = element.width // asset.width
    scale_y = element.height // asset.height
    return scale_x if scale_x == scale_y and scale_x >= 1 else None


def invalid_asset_scale_elements(
    project: GuiProject,
) -> list[tuple[ScreenDesign, GuiElement, ProjectAsset]]:
    """Return placed assets that cannot use the bounded integer renderer."""
    invalid: list[tuple[ScreenDesign, GuiElement, ProjectAsset]] = []
    for screen in project.screens:
        for element in screen.elements:
            if not element.asset_id:
                continue
            asset = project.asset(element.asset_id)
            if (
                asset is not None
                and asset_element_runtime_scale(element, asset) is None
            ):
                invalid.append((screen, element, asset))
    return invalid


def bake_asset_element(project: GuiProject, element: GuiElement) -> ProjectAsset:
    """Bake one placement to its current size as an independent nearest-neighbor asset."""
    source = project.asset(element.asset_id)
    if source is None:
        raise ValueError(f"Missing asset {element.asset_id}")
    target_width = int(element.width)
    target_height = int(element.height)
    if not 1 <= target_width <= 320 or not 1 <= target_height <= 320:
        raise ValueError("A baked asset must be between 1 and 320 pixels per side")
    baked_frames: list[list[int | None]] = []
    for source_pixels in source.frames:
        baked_frames.append(
            [
                source_pixels[
                    min(source.height - 1, y * source.height // target_height)
                    * source.width
                    + min(source.width - 1, x * source.width // target_width)
                ]
                for y in range(target_height)
                for x in range(target_width)
            ]
        )
    baked_id = f"{_snapshot_identifier(element.id)}_baked"
    baked = ProjectAsset(
        baked_id,
        f"{source.name} ({target_width}x{target_height} bake)",
        target_width,
        target_height,
        _scaled_signed_coordinate(source.origin_x, source.width, target_width),
        _scaled_signed_coordinate(source.origin_y, source.height, target_height),
        baked_frames,
        list(source.durations),
        link_state="detached",
    )
    baked.validate()
    project.upsert_asset(baked)
    element.asset_id = baked.id
    element.asset_key = ""
    element.asset_source_path = ""
    element.asset_absolute_fallback = ""
    element.asset_qualified_name = ""
    element.asset_fingerprint = ""
    element.asset_link_state = "detached"
    element.asset_width = baked.width
    element.asset_height = baked.height
    first = baked.pixel_frames()[0]
    blank = PixelArt(
        first.width,
        first.height,
        first.origin_x,
        first.origin_y,
    )
    element.asset_runs = [list(run) for run in first.horizontal_runs(blank)]
    return baked


def _scaled_signed_coordinate(value: int, source: int, target: int) -> int:
    """Scale one signed origin with deterministic nearest-integer rounding."""
    magnitude = (abs(value) * target + source // 2) // source
    return -magnitude if value < 0 else magnitude


def _legacy_element_art(element: GuiElement) -> PixelArt:
    """Reconstruct the lossless legacy one-frame element snapshot."""
    art = PixelArt(element.asset_width, element.asset_height)
    for run in element.asset_runs:
        if len(run) != 4:
            continue
        x, y, width, color = (int(value) for value in run)
        for pixel_x in range(max(0, x), min(art.width, x + max(0, width))):
            if 0 <= y < art.height:
                art.set_pixel(pixel_x, y, color & 0xFFFF)
    return art


def build_designer_patch(project: GuiProject, path: str | Path) -> SourcePatch:
    """Build a reviewed Python patch for one GUI project."""
    expanded_asset_runs = sum(
        len(element.asset_runs)
        for screen in project.screens
        for element in screen.elements
    )
    if expanded_asset_runs > 5_000:
        raise ValueError(
            "Legacy one-file export would expand "
            f"{expanded_asset_runs} image runs into Python statements. "
            "Use Export Generated App Structure v1 so imported images are written "
            "as streamed generated_assets.pga resources."
        )
    target = Path(path)
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    generated = generate_python(project)
    updated = _replace_designer_block(original, generated)
    ast.parse(updated, filename=str(target))
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target),
        )
    )
    element_count = sum(len(screen.elements) for screen in project.screens)
    return SourcePatch(target, original, updated, diff, "gui-designer", element_count)


def generate_python(project: GuiProject) -> str:
    """Generate a MicroPython-friendly GUI renderer class."""
    screen_names = [screen.name for screen in project.screens]
    if len(set(screen_names)) != len(screen_names):
        raise ValueError("Screen names must be unique before Python export")
    class_name = _python_name(project.name, "GeneratedGui")
    lines = [
        "# Pico GUI Designer begin\n",
        f"class {class_name}:\n",
        '    """Render generated screens and navigation."""\n',
        "\n",
        "    def __init__(self, draw):\n",
        '        """Initialize the generated GUI renderer."""\n',
        "        self.draw = draw\n",
        f"        self.screen = {project.screen(project.start_screen_id).name!r}\n",
        '        self.last_transition = "replace"\n',
        "        self.focus_index = 0\n",
        "\n",
        "    def render(self):\n",
        '        """Render the active generated screen."""\n',
    ]
    for index, screen in enumerate(project.screens):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"        {keyword} self.screen == {screen.name!r}:\n")
        lines.append(
            f"            self._draw_{_python_name(screen.name, 'screen')}()\n"
        )
    if not project.screens:
        lines.append("        pass\n")
    lines.append("        self._draw_focus()\n")
    lines.extend(
        [
            "\n",
            "    def handle_event(self, event):\n",
            '        """Apply one generated screen transition."""\n',
        ]
    )
    if project.connections:
        for index, connection in enumerate(project.connections):
            source = project.screen(connection.source_id)
            target = project.screen(connection.target_id)
            if source is None or target is None:
                continue
            keyword = "if" if index == 0 else "elif"
            lines.append(
                f"        {keyword} self.screen == {source.name!r} and event == {connection.trigger!r}:\n"
            )
            lines.append(
                f"            if self._can_transition({connection.condition!r}):\n"
            )
            if connection.action:
                lines.append(
                    f"                self._run_action({connection.action!r})\n"
                )
            lines.append(f"                self.screen = {target.name!r}\n")
            focus_index = _connection_focus_index(project, connection)
            lines.append(f"                self.focus_index = {focus_index}\n")
            lines.append(
                f"                self.last_transition = {connection.transition!r}\n"
            )
            lines.append("                return True\n")
        lines.append("        return False\n")
    else:
        lines.append("        return False\n")
    lines.extend(
        [
            "\n",
            "    def _can_transition(self, condition):\n",
            '        """Allow an application to evaluate a named condition."""\n',
            "        return True\n",
            "\n",
            "    def _run_action(self, action):\n",
            '        """Allow an application to handle a named action."""\n',
            "        return None\n",
            "\n",
            "    def focused_element(self):\n",
            '        """Return the focused element event name."""\n',
            "        elements = self._focusable_elements()\n",
            "        if not elements:\n",
            "            return None\n",
            "        self.focus_index %= len(elements)\n",
            "        return elements[self.focus_index]\n",
            "\n",
            "    def move_focus(self, step):\n",
            '        """Move keyboard focus and return its event name."""\n',
            "        elements = self._focusable_elements()\n",
            "        if not elements:\n",
            "            return None\n",
            "        self.focus_index = (self.focus_index + step) % len(elements)\n",
            "        return elements[self.focus_index]\n",
            "\n",
            "    def activate_focused(self):\n",
            '        """Dispatch the focused element event when available."""\n',
            "        event = self.focused_element()\n",
            "        return self.handle_event(event) if event else False\n",
            "\n",
            "    def _focusable_elements(self):\n",
            '        """Return ordered focusable names for the active screen."""\n',
        ]
    )
    for index, screen in enumerate(project.screens):
        keyword = "if" if index == 0 else "elif"
        focusable = [
            (element.focus_order, element_index, element.activation_event())
            for element_index, element in enumerate(screen.elements)
            if element.visible and element.enabled and element.focusable
        ]
        names = tuple(item[2] for item in sorted(focusable))
        lines.append(f"        {keyword} self.screen == {screen.name!r}:\n")
        lines.append(f"            return {names!r}\n")
    lines.append("        return ()\n")
    lines.extend(
        [
            "\n",
            "    def _draw_focus(self):\n",
            '        """Draw the configured active-element focus indicator."""\n',
        ]
    )
    if not project.screens:
        lines.append("        return\n")
    for screen_index, screen in enumerate(project.screens):
        keyword = "if" if screen_index == 0 else "elif"
        focusable = [
            (element.focus_order, element_index, element)
            for element_index, element in enumerate(screen.elements)
            if element.visible and element.enabled and element.focusable
        ]
        ordered = [item[2] for item in sorted(focusable)]
        lines.append(f"        {keyword} self.screen == {screen.name!r}:\n")
        if not ordered:
            lines.append("            return\n")
            continue
        lines.append(f"            focus_index = self.focus_index % {len(ordered)}\n")
        for focus_index, element in enumerate(ordered):
            focus_keyword = "if" if focus_index == 0 else "elif"
            lines.append(f"            {focus_keyword} focus_index == {focus_index}:\n")
            lines.extend(_focus_indicator_python(element, "                "))
            lines.append("                return\n")
    for screen in project.screens:
        lines.extend(_screen_python(screen))
    lines.append("# Pico GUI Designer end\n")
    return "".join(lines)


def generate_live_app_python(project: GuiProject, active_screen_id: str) -> str:
    """Generate a temporary Picoware app starting on the active design screen."""
    preview = GuiProject.from_dict(project.to_dict())
    active = preview.screen(active_screen_id)
    if active is None:
        active = preview.screen(preview.start_screen_id) or preview.screens[0]
    preview.start_screen_id = active.id
    for screen in preview.screens:
        for element in screen.elements:
            if element.kind == "icon" and element.asset_call:
                element.asset_call = ""
    class_name = _python_name(preview.name, "GeneratedGui")
    lines = [
        "from picoware.system.buttons import (\n",
        "    BUTTON_BACK,\n",
        "    BUTTON_CENTER,\n",
        "    BUTTON_DOWN,\n",
        "    BUTTON_LEFT,\n",
        "    BUTTON_RIGHT,\n",
        "    BUTTON_UP,\n",
        ")\n",
        "\n",
        generate_python(preview),
        "\n",
        "_live_gui = None\n",
        "\n",
        "\n",
        "def _redraw(view_manager):\n",
        '    """Render the current live design screen."""\n',
        "    draw = view_manager.draw\n",
        "    draw.clear()\n",
        "    _live_gui.render()\n",
        "    draw.swap()\n",
        "\n",
        "\n",
        "def start(view_manager):\n",
        '    """Start the temporary GUI designer preview."""\n',
        "    global _live_gui\n",
        f"    _live_gui = {class_name}(view_manager.draw)\n",
        "    _redraw(view_manager)\n",
        "    view_manager.input_manager.reset()\n",
        "    return True\n",
        "\n",
        "\n",
        "def run(view_manager):\n",
        '    """Handle navigation inside the live design preview."""\n',
        "    if _live_gui is None:\n",
        "        return\n",
        "    input_manager = view_manager.input_manager\n",
        "    button = input_manager.button\n",
        "    if button == -1:\n",
        "        return\n",
        "    if button == BUTTON_BACK:\n",
        "        input_manager.reset()\n",
        "        view_manager.back()\n",
        "        return\n",
        "    if button in (BUTTON_RIGHT, BUTTON_DOWN):\n",
        "        _live_gui.move_focus(1)\n",
        "    elif button in (BUTTON_LEFT, BUTTON_UP):\n",
        "        _live_gui.move_focus(-1)\n",
        "    elif button == BUTTON_CENTER:\n",
        "        _live_gui.activate_focused()\n",
        "    input_manager.reset()\n",
        "    _redraw(view_manager)\n",
        "\n",
        "\n",
        "def stop(view_manager):\n",
        '    """Release the temporary GUI designer preview."""\n',
        "    global _live_gui\n",
        "    _live_gui = None\n",
    ]
    source = "".join(lines)
    ast.parse(source, filename="GuiDesignerLive.py")
    return source


def _screen_python(screen: ScreenDesign) -> list[str]:
    """Generate one screen drawing method."""
    lines = [
        "\n",
        f"    def _draw_{_python_name(screen.name, 'screen')}(self):\n",
        '        """Draw one generated screen."""\n',
        f"        self.draw._fill_rectangle(0, 0, {screen.width}, {screen.height}, 0x{screen.background_color:04X})\n",
    ]
    for element in screen.elements:
        if element.visible:
            lines.extend(_element_python(element))
    return lines


def _connection_focus_index(
    project: GuiProject,
    connection: FlowConnection,
) -> int:
    """Return the destination focus index for one generated transition."""
    if not connection.target_element_id:
        return 0
    target = project.screen(connection.target_id)
    if target is None:
        return 0
    focusable = [
        (element.focus_order, index, element.id)
        for index, element in enumerate(target.elements)
        if element.visible and element.enabled and element.focusable
    ]
    ordered = [item[2] for item in sorted(focusable)]
    try:
        return ordered.index(connection.target_element_id)
    except ValueError:
        return 0


def _element_python(element: GuiElement) -> list[str]:
    """Generate drawing calls for one GUI element."""
    prefix = "        "
    fill = f"0x{element.fill_color:04X}"
    border = f"0x{element.border_color:04X}"
    text = f"0x{element.text_color:04X}"
    rectangle = (
        f"{prefix}self.draw._fill_rectangle({element.x}, {element.y}, "
        f"{element.width}, {element.height}, {fill})\n"
    )
    outline = (
        f"{prefix}self.draw._rectangle({element.x}, {element.y}, "
        f"{element.width}, {element.height}, {border})\n"
    )
    if element.kind == "icon" and (
        element.asset_runs or (element.asset_width > 0 and element.asset_height > 0)
    ):
        return _embedded_asset_python(element, prefix)
    if element.kind == "label":
        return [
            f"{prefix}self.draw._text({element.x}, {element.y}, {element.text!r}, {text})\n"
        ]
    if element.kind == "icon" and element.asset_call:
        call = _python_name(element.asset_call, "draw_icon")
        return [f"{prefix}self.{call}({element.x}, {element.y})\n"]
    lines = [rectangle]
    if element.kind in {"button", "panel", "list", "icon", "progress"}:
        lines.append(outline)
    if element.kind in {"button", "icon"} and element.text:
        lines.append(
            f"{prefix}self.draw._text({element.x + 4}, {element.y + 4}, {element.text!r}, {text})\n"
        )
    elif element.kind == "list":
        for index, item in enumerate(element.text.splitlines()):
            lines.append(
                f"{prefix}self.draw._text({element.x + 4}, {element.y + 4 + index * 14}, {item!r}, {text})\n"
            )
    elif element.kind == "progress":
        progress_width = max(1, element.width // 2)
        lines.append(
            f"{prefix}self.draw._fill_rectangle({element.x}, {element.y}, {progress_width}, {element.height}, {border})\n"
        )
    return lines


def _embedded_asset_python(element: GuiElement, prefix: str) -> list[str]:
    """Generate scaled drawing runs for one embedded pixel asset."""
    source_width = max(1, element.asset_width or element.width)
    source_height = max(1, element.asset_height or element.height)
    lines: list[str] = []
    for run in element.asset_runs:
        if len(run) != 4:
            continue
        run_x, run_y, run_width, color = (int(value) for value in run)
        left = round(run_x * element.width / source_width)
        right = round((run_x + run_width) * element.width / source_width)
        top = round(run_y * element.height / source_height)
        bottom = round((run_y + 1) * element.height / source_height)
        lines.append(
            f"{prefix}self.draw._fill_rectangle("
            f"{element.x + left}, {element.y + top}, "
            f"{max(1, right - left)}, {max(1, bottom - top)}, 0x{color & 0xFFFF:04X})\n"
        )
    return lines or [f"{prefix}pass\n"]


def _focus_indicator_python(element: GuiElement, prefix: str) -> list[str]:
    """Generate drawing calls for one element focus indicator."""
    style = (
        element.focus_style
        if element.focus_style
        in {
            "outline",
            "corners",
            "underline",
            "none",
        }
        else "outline"
    )
    if style == "none":
        return []
    color = f"0x{element.focus_color:04X}"
    thickness = max(1, min(6, int(element.focus_thickness)))
    padding = max(0, min(12, int(element.focus_padding)))
    if style == "underline":
        return [
            f"{prefix}self.draw._fill_rectangle("
            f"{element.x - padding}, {element.y + element.height + padding}, "
            f"{element.width + padding * 2}, {thickness}, {color})\n"
        ]
    if style == "corners":
        segment = max(3, min(10, min(element.width, element.height) // 3))
        lines: list[str] = []
        for offset in range(thickness):
            pad = padding + offset
            left = element.x - pad
            top = element.y - pad
            right = element.x + element.width + pad
            bottom = element.y + element.height + pad
            for x1, y1, x2, y2 in (
                (left, top, left + segment, top),
                (left, top, left, top + segment),
                (right - segment, top, right, top),
                (right, top, right, top + segment),
                (left, bottom, left + segment, bottom),
                (left, bottom - segment, left, bottom),
                (right - segment, bottom, right, bottom),
                (right, bottom - segment, right, bottom),
            ):
                lines.append(
                    f"{prefix}self.draw._line({x1}, {y1}, {x2}, {y2}, {color})\n"
                )
        return lines
    return [
        f"{prefix}self.draw._rectangle("
        f"{element.x - padding - offset}, {element.y - padding - offset}, "
        f"{element.width + (padding + offset) * 2}, "
        f"{element.height + (padding + offset) * 2}, {color})\n"
        for offset in range(thickness)
    ]


def _replace_designer_block(source: str, generated: str) -> str:
    """Replace or append the managed designer source block."""
    matches = list(DESIGNER_MARKER.finditer(source))
    if len(matches) >= 2:
        start = matches[0].start()
        end = matches[-1].end()
        if end < len(source) and source[end] == "\n":
            end += 1
        return source[:start] + generated + source[end:]
    separator = "" if not source or source.endswith("\n\n") else "\n"
    if source and not source.endswith("\n"):
        separator = "\n\n"
    return source + separator + generated


def _python_name(value: str, fallback: str) -> str:
    """Return a valid Python identifier from display text."""
    normalized = re.sub(r"\W+", "_", value.strip()).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"gui_{normalized}"
    return normalized


def backup_project(path: Path, backup_root: Path) -> Path:
    """Copy a designer project into the backup directory."""
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_root / f"{path.name}.{timestamp}.bak"
    shutil.copy2(path, backup_path)
    return backup_path
