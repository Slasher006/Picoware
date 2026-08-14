"""Generate and transactionally apply Picoware app structure v1."""

from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .asset_codegen import (
    GeneratedAudioEntry,
    GeneratedAssetResource,
    GeneratedRasterEntry,
    generate_asset_resource,
    generate_individual_asset_resources,
    parse_asset_resource_project,
    parse_individual_resource_marker_project,
)
from .designer_model import (
    SUPPORTED_FLOW_STANDARD_VERSIONS,
    FlowDiagnostic,
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
    asset_element_runtime_scale,
    flow_diagnostics,
    flow_stub_name,
)
from .native_widgets import NATIVE_WIDGET_IDS, native_widget_spec


GENERATED_STRUCTURE_VERSION = 1
DEFAULT_GENERATOR_VERSION = "1.1.0"
ASSET_STORAGE_COMBINED = "combined"
ASSET_STORAGE_INDIVIDUAL = "individual"
ASSET_STORAGE_MODES = (ASSET_STORAGE_COMBINED, ASSET_STORAGE_INDIVIDUAL)
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
CREATE_ONCE_NOTICE = (
    "# Picoware generated application scaffold.\n"
    "# This file is developer-owned after its first creation.\n"
)


class GeneratedAppError(ValueError):
    """Report a generation, collision, or transactional safety failure."""


@dataclass(frozen=True)
class GeneratedHeader:
    """Describe one recognized editor-owned module header."""

    structure: int
    role: str
    project_id: str
    generator_version: str


@dataclass(frozen=True)
class GeneratedAppPaths:
    """Hold all resolved runtime paths for one generated app."""

    root: Path
    display_name: str
    package_name: str
    entrypoint: Path
    package: Path
    package_init: Path
    app: Path
    behavior_handlers: Path
    generated_behavior: Path
    generated_ui: Path
    generated_assets: Path
    generated_asset_data: Path

    def all_files(self) -> tuple[Path, ...]:
        """Return files in the documented review order."""
        return (
            self.entrypoint,
            self.package_init,
            self.app,
            self.behavior_handlers,
            self.generated_behavior,
            self.generated_ui,
            self.generated_assets,
            self.generated_asset_data,
        )


@dataclass(frozen=True)
class GeneratedFilePatch:
    """Describe one reviewed output file and its planned action."""

    path: Path
    ownership: str
    role: str
    action: str
    original: str | bytes
    updated: str | bytes
    diff: str
    expected_fingerprint: str | None
    message: str = ""


@dataclass(frozen=True)
class GeneratedAppPatchSet:
    """Represent one complete code-and-resource generation transaction."""

    paths: GeneratedAppPaths
    patches: tuple[GeneratedFilePatch, ...]
    asset_resource: GeneratedAssetResource

    @property
    def blocked(self) -> bool:
        """Return whether any collision prevents an apply."""
        return any(
            patch.action in {"conflict", "unsupported-version"}
            for patch in self.patches
        )

    def changed_patches(self) -> tuple[GeneratedFilePatch, ...]:
        """Return every file mutation included in the reviewed transaction."""
        return tuple(
            patch
            for patch in self.patches
            if patch.action in {"create", "regenerate", "delete"}
        )


@dataclass(frozen=True)
class GeneratedApplyReport:
    """Report the durable result of one patch-set apply."""

    created: tuple[Path, ...]
    regenerated: tuple[Path, ...]
    deleted: tuple[Path, ...]
    preserved: tuple[Path, ...]
    backup_directory: Path | None


@dataclass(frozen=True)
class LivePreviewBundle:
    """Hold one temporary app and its package-local streamed resources."""

    files: tuple[tuple[str, str | bytes], ...]

    def source(self, name: str) -> str:
        """Return one text file from the bundle for review or tests."""
        for path, content in self.files:
            if path == name and isinstance(content, str):
                return content
        raise KeyError(name)


def sanitize_display_name(value: str) -> str:
    """Return a safe display filename stem while retaining ordinary spaces."""
    display = str(value).strip()
    display = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", display)
    display = display.strip(" .")
    return display or "Generated App"


def sanitize_package_name(value: str) -> str:
    """Return one deterministic lowercase ASCII package identifier."""
    normalized = unicodedata.normalize("NFKD", str(value).strip())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    package = re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_").lower()
    if not package:
        package = "generated_app"
    if package[0].isdigit():
        package = "_" + package
    return package


def _validate_project_id(project_id: object) -> str:
    """Require one bounded single-line identifier safe for generated headers."""
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise GeneratedAppError(
            "Project ID must start with an ASCII letter and contain only ASCII "
            "letters, digits, '_', '-', or '.', with at most 128 characters."
        )
    return project_id


def resolve_generated_app_paths(
    project_name: str,
    destination: str | Path,
) -> GeneratedAppPaths:
    """Resolve the fixed generated-app output paths without creating directories."""
    root = Path(destination).expanduser().resolve()
    display_name = sanitize_display_name(project_name)
    package_name = sanitize_package_name(project_name)
    package = root / package_name
    paths = GeneratedAppPaths(
        root,
        display_name,
        package_name,
        root / f"{display_name}.py",
        package,
        package / "__init__.py",
        package / "app.py",
        package / "behavior_handlers.py",
        package / "generated_behavior.py",
        package / "generated_ui.py",
        package / "generated_assets.py",
        package / "generated_assets.pga",
    )
    _validate_output_paths(paths)
    return paths


def generated_header(project_id: str, role: str, generator_version: str) -> str:
    """Return the exact editor-owned v1 header."""
    if role not in {"ui", "assets", "behavior"}:
        raise GeneratedAppError("Generated role must be ui, assets, or behavior")
    _validate_project_id(project_id)
    return (
        "# @picoware-generated structure=1\n"
        f"# @picoware-generated role={role}\n"
        f"# @picoware-generated project={project_id}\n"
        f"# @picoware-generator version={generator_version}\n"
        "# This file is editor-owned. Regenerate it instead of editing it manually.\n"
    )


def parse_generated_header(source: str) -> GeneratedHeader | None:
    """Parse an exact editor-owned header, or return None when unrecognized."""
    lines = source.splitlines()
    if not lines or not lines[0].startswith("# @picoware-generated structure="):
        return None
    if len(lines) < 4:
        return None
    prefixes = (
        "# @picoware-generated structure=",
        "# @picoware-generated role=",
        "# @picoware-generated project=",
        "# @picoware-generator version=",
    )
    if any(
        not lines[index].startswith(prefix) for index, prefix in enumerate(prefixes)
    ):
        return None
    try:
        structure = int(lines[0][len(prefixes[0]) :])
    except ValueError:
        return None
    return GeneratedHeader(
        structure,
        lines[1][len(prefixes[1]) :],
        lines[2][len(prefixes[2]) :],
        lines[3][len(prefixes[3]) :],
    )


def generate_entrypoint(display_name: str, package_name: str) -> str:
    """Generate the create-once Picoware lifecycle entrypoint."""
    del display_name
    return (
        CREATE_ONCE_NOTICE
        + "\n"
        + f"from {package_name}.app import Application\n"
        + "\n\n_application = None\n\n\n"
        + "def start(view_manager):\n"
        + '    """Start the generated application base."""\n'
        + "    global _application\n"
        + "    _application = Application()\n"
        + "    return _application.start(view_manager)\n\n\n"
        + "def run(view_manager):\n"
        + '    """Delegate one Picoware input cycle."""\n'
        + "    if _application is not None:\n"
        + "        _application.run(view_manager)\n\n\n"
        + "def stop(view_manager):\n"
        + '    """Stop the application and release its state."""\n'
        + "    global _application\n"
        + "    if _application is not None:\n"
        + "        _application.stop(view_manager)\n"
        + "    _application = None\n"
    )


def generate_package_init(display_name: str) -> str:
    """Generate the create-once minimal package marker."""
    return f"# {sanitize_display_name(display_name)} application package.\n"


def generate_app_scaffold() -> str:
    """Generate the create-once application behavior extension scaffold."""
    return (
        CREATE_ONCE_NOTICE
        + '''

from picoware.system.buttons import (
    BUTTON_BACK,
    BUTTON_CENTER,
    BUTTON_DOWN,
    BUTTON_LEFT,
    BUTTON_RIGHT,
    BUTTON_UP,
)

from . import behavior_handlers
from .generated_behavior import BehaviorRuntime
from .generated_ui import GeneratedUI


class Application:
    """Own user behavior around the generated presentation."""

    def __init__(self):
        self.view_manager = None
        self.ui = None
        self.behavior = None

    def start(self, view_manager):
        """Initialize the application base and show its start screen."""
        self.view_manager = view_manager
        self.ui = GeneratedUI(view_manager)
        self.behavior = BehaviorRuntime(
            self.ui,
            behavior_handlers,
            services={"ui": self.ui},
        )
        self.redraw()
        view_manager.input_manager.reset()
        return True

    def run(self, view_manager):
        """Handle structural navigation and delegate activation events."""
        if self.ui is None:
            return
        input_manager = view_manager.input_manager
        button = input_manager.button
        if button == -1:
            if self.ui.needs_idle_redraw():
                self.redraw()
            return

        event_id, consumed = self.ui.handle_input(button)
        if event_id is not None:
            self.handle_event(event_id)
        elif consumed:
            pass
        elif button in (BUTTON_RIGHT, BUTTON_DOWN):
            self.ui.move_focus(1)
        elif button in (BUTTON_LEFT, BUTTON_UP):
            self.ui.move_focus(-1)
        elif button == BUTTON_CENTER:
            event_id = self.ui.activate_focused()
            if event_id is not None:
                self.handle_event(event_id)
        elif button == BUTTON_BACK:
            event_id = "event_navigation_back_01"
            if self.ui.handle_navigation(event_id):
                self.handle_event(event_id)
            else:
                input_manager.reset()
                view_manager.back()
                return

        input_manager.reset()
        self.redraw()

    def redraw(self):
        """Render the active generated screen."""
        if self.view_manager is None or self.ui is None:
            return
        draw = self.view_manager.draw
        draw.clear()
        self.ui.render()
        draw.swap()

    def handle_event(self, event_id):
        """Dispatch one stable event through generated behavior bindings."""
        return self.behavior.dispatch_event(event_id) if self.behavior else False

    def stop(self, view_manager):
        """Release application-owned state."""
        self.ui = None
        self.behavior = None
        self.view_manager = None
'''
    )


def generate_behavior_handlers(project: GuiProject) -> str:
    """Generate the create-once developer handler module."""
    lines = [CREATE_ONCE_NOTICE, "\n"]
    handlers = [
        str(node.properties.get("handler") or flow_stub_name(node))
        for node in project.behavior_nodes
        if node.operation == "custom.handler"
    ]
    if not handlers:
        lines.append("# Add developer-owned behavior handlers here.\n")
    for handler in handlers:
        lines.extend(
            (
                f"\ndef {handler}(payload, runtime):\n",
                f'    """Implement {handler} without editing generated files."""\n',
                '    raise NotImplementedError("Behavior handler is not implemented")\n',
            )
        )
    source = "".join(lines)
    ast.parse(source, filename="behavior_handlers.py")
    return source


def _merge_missing_handler_stubs(original: str, generated: str) -> str:
    """Append only missing generated handler functions to a parseable module."""
    original_tree = ast.parse(original, filename="behavior_handlers.py")
    generated_tree = ast.parse(generated, filename="behavior_handlers.py")
    existing = {
        item.name
        for item in original_tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [
        item
        for item in generated_tree.body
        if isinstance(item, ast.FunctionDef) and item.name not in existing
    ]
    if not missing:
        return original
    blocks = []
    for item in missing:
        block = ast.get_source_segment(generated, item)
        if block:
            blocks.append(block.rstrip() + "\n")
    return original.rstrip() + "\n\n" + "\n".join(blocks)


def generate_behavior_module(
    project: GuiProject,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
) -> str:
    """Generate a bounded MicroPython-safe dispatcher for allowlisted operations."""
    nodes = tuple(
        (
            node.id,
            node.operation,
            flow_stub_name(node),
            dict(sorted(node.properties.items())),
            dict(sorted(node.binding.items())),
            node.breakpoint,
        )
        for node in project.behavior_nodes
        if node.operation
    )
    connections = _behavior_connection_records(project)
    manifest = {
        "bindings": tuple(
            (
                node.id,
                node.binding.get("event_id", ""),
                node.binding.get("screen_id", ""),
                node.binding.get("element_id", ""),
            )
            for node in project.behavior_nodes
            if node.operation == "event.ui"
        ),
        "handlers": tuple(
            (
                node.id,
                str(node.properties.get("handler") or flow_stub_name(node)),
            )
            for node in project.behavior_nodes
            if node.operation == "custom.handler"
        ),
        "services": tuple(
            sorted(
                {
                    node.operation.split(".", 1)[0]
                    for node in project.behavior_nodes
                    if node.operation
                    and node.operation.split(".", 1)[0]
                    in {"mqtt", "wifi", "storage", "timer"}
                }
            )
        ),
    }
    source = (
        generated_header(project.project_id, "behavior", generator_version)
        + f'''

NODES = {nodes!r}
CONNECTIONS = {connections!r}
TEST_MANIFEST = {manifest!r}

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
    result = {{
        "event_id": event_id,
        "screen_id": binding.get("screen_id", ""),
        "element_id": element_id,
        "widget_type": widget_type,
        "value": value,
    }}
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
        self.services = services or {{}}
        self.services.setdefault("ui", ui)
        self.state = self.services.setdefault("state", {{}})
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
                    output, result = "error", {{"error": str(error)}}
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
            safe = {{}}
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
            result = self.ui.navigate(values["screen_id"])
            if result is False:
                raise RuntimeError("UI rejected navigation target")
            return "done", result
        if operation == "navigation.back":
            result = self.ui.back()
            if result is False:
                raise RuntimeError("UI rejected back navigation")
            return "done", result
        if operation.startswith("ui."):
            method = operation.split(".", 1)[1]
            arguments = dict((key, _resolve_payload_reference(value, payload)) for key, value in values.items())
            if method in ("set_value", "set_text", "set_progress"):
                key = "text" if method == "set_text" else "value"
                if arguments.get(key, "") == "":
                    arguments[key] = payload
            result = getattr(self.ui, method)(**arguments)
            if method != "read_value" and result is False:
                raise RuntimeError("UI rejected " + operation + " target")
            return "done", result
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
'''
    )
    ast.parse(source, filename="generated_behavior.py")
    return source


def build_live_preview_bundle(
    project: GuiProject,
    active_screen_id: str,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
) -> LivePreviewBundle:
    """Build a temporary app using the same streamed assets as normal export."""
    preview = GuiProject.from_dict(project.to_dict())
    active = preview.screen(active_screen_id)
    if active is None:
        active = preview.screen(preview.start_screen_id) or preview.screens[0]
    preview.start_screen_id = active.id
    package_name = "gui_designer_live"
    resource = generate_project_asset_resource(
        preview,
        generator_version,
        storage_mode=_project_asset_storage(preview),
    )
    entrypoint = _generate_live_preview_entrypoint(package_name)
    files: tuple[tuple[str, str | bytes], ...] = (
        ("GuiDesignerLive.py", entrypoint),
        (f"{package_name}/__init__.py", "# Temporary GUI live preview package.\n"),
        (
            f"{package_name}/generated_ui.py",
            generate_ui_module(preview, generator_version),
        ),
        (f"{package_name}/generated_assets.py", resource.module_source),
        (
            f"{package_name}/generated_behavior.py",
            generate_behavior_module(preview, generator_version),
        ),
        (
            f"{package_name}/behavior_handlers.py",
            generate_behavior_handlers(preview),
        ),
        *((f"{package_name}/{name}", content) for name, content in resource.files),
    )
    return LivePreviewBundle(files)


def _generate_live_preview_entrypoint(package_name: str) -> str:
    """Generate the small Picoware lifecycle wrapper for a live design."""
    return f"""from picoware.system.buttons import (
    BUTTON_BACK,
    BUTTON_CENTER,
    BUTTON_DOWN,
    BUTTON_LEFT,
    BUTTON_RIGHT,
    BUTTON_UP,
)

from {package_name}.generated_ui import GeneratedUI
from {package_name} import behavior_handlers
from {package_name}.generated_behavior import BehaviorRuntime


_live_gui = None
_live_behavior = None


def _redraw(view_manager):
    draw = view_manager.draw
    draw.clear()
    _live_gui.render()
    draw.swap()


def start(view_manager):
    global _live_gui, _live_behavior
    _live_gui = GeneratedUI(view_manager)
    _live_behavior = BehaviorRuntime(
        _live_gui,
        behavior_handlers,
        services={{"ui": _live_gui}},
    )
    _redraw(view_manager)
    view_manager.input_manager.reset()
    return True


def run(view_manager):
    if _live_gui is None:
        return
    input_manager = view_manager.input_manager
    button = input_manager.button
    if button == -1:
        if _live_gui.needs_idle_redraw():
            _redraw(view_manager)
        return
    event_id, consumed = _live_gui.handle_input(button)
    if event_id is not None:
        _live_behavior.dispatch_event(event_id)
    elif consumed:
        pass
    elif button == BUTTON_BACK:
        event_id = "event_navigation_back_01"
        if _live_gui.handle_navigation(event_id):
            _live_behavior.dispatch_event(event_id)
        else:
            input_manager.reset()
            view_manager.back()
            return
    elif button in (BUTTON_RIGHT, BUTTON_DOWN):
        _live_gui.move_focus(1)
    elif button in (BUTTON_LEFT, BUTTON_UP):
        _live_gui.move_focus(-1)
    elif button == BUTTON_CENTER:
        event_id = _live_gui.activate_focused()
        if event_id is not None:
            _live_behavior.dispatch_event(event_id)
    input_manager.reset()
    _redraw(view_manager)


def stop(view_manager):
    global _live_gui, _live_behavior
    _live_gui = None
    _live_behavior = None
"""


def generate_ui_module(
    project: GuiProject,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
) -> str:
    """Generate the editor-owned presentation and structural navigation module."""
    _validate_project(project)
    screens = list(project.screens)
    native_elements = [
        element
        for screen in screens
        for element in screen.elements
        if element.kind == "native"
    ]
    has_alert_action = any(
        node.operation == "ui.alert" for node in project.behavior_nodes
    )
    element_default_values = {
        element.id: (
            50
            if element.kind == "progress"
            else element.text
            if element.kind in {"button", "label", "icon", "list"}
            else None
        )
        for screen in screens
        for element in screen.elements
    }
    widget_types = {
        element.id: (
            element.native_widget if element.kind == "native" else element.kind
        )
        for screen in screens
        for element in screen.elements
    }
    visibility_defaults = {
        element.id: bool(element.visible)
        for screen in screens
        for element in screen.elements
    }
    enabled_defaults = {
        element.id: bool(element.enabled)
        for screen in screens
        for element in screen.elements
    }
    loading_by_screen = {
        screen.id: tuple(
            element.id
            for element in screen.elements
            if element.kind == "native" and element.native_widget == "loading"
        )
        for screen in screens
    }
    lines = [
        generated_header(project.project_id, "ui", generator_version),
        "\nfrom .generated_assets import draw_asset\n",
        *_native_import_lines(native_elements, include_alert=has_alert_action),
        "\n\n",
        "class GeneratedUI:\n",
        '    """Render generated screens and structural navigation."""\n\n',
        f"    FLOW_STANDARD_VERSION = {project.flow_standard_version!r}\n",
        f"    FLOW_NODES = {_behavior_node_records(project)!r}\n",
        f"    FLOW_CONNECTIONS = {_behavior_connection_records(project)!r}\n",
        f"    FLOW_GROUPS = {_flow_group_records(project)!r}\n\n",
        f"    ELEMENT_IDS = {tuple(element.id for screen in screens for element in screen.elements)!r}\n",
        f"    ELEMENT_EVENTS = {dict((element.id, element.event_id) for screen in screens for element in screen.elements)!r}\n\n",
        f"    ELEMENT_DEFAULT_VALUES = {element_default_values!r}\n",
        f"    ELEMENT_WIDGET_TYPES = {widget_types!r}\n\n",
        f"    ELEMENT_VISIBLE_DEFAULTS = {visibility_defaults!r}\n",
        f"    ELEMENT_ENABLED_DEFAULTS = {enabled_defaults!r}\n\n",
        "    def __init__(self, context):\n",
        "        self.view_manager = context if hasattr(context, 'draw') else None\n",
        "        self.draw = context.draw if self.view_manager is not None else context\n",
        "        self._native_widgets = {}\n",
        "        self._element_values = {}\n",
        "        self._element_visibility = {}\n",
        "        self._element_enabled = {}\n",
        "        self._behavior_alert = None\n",
        f"        self.screen_id = {project.start_screen_id!r}\n",
        "        self.focus_index = 0\n",
        '        self.last_transition = "replace"\n\n',
        "    def render(self):\n",
        '        """Draw the active screen and focus indicator."""\n',
    ]
    for index, screen in enumerate(screens):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"        {keyword} self.screen_id == {screen.id!r}:\n")
        lines.append(f"            self.{_screen_method(screen)}()\n")
    if not screens:
        lines.append("        return\n")
    lines.extend(
        [
            "        self._draw_focus()\n",
            "        if self._behavior_alert is not None:\n",
            "            self._behavior_alert.draw('Alert')\n\n",
        ]
    )
    known_screens = tuple(screen.id for screen in screens)
    lines.extend(
        [
            "    def set_screen(self, screen_id):\n",
            '        """Select a known screen by stable ID."""\n',
            f"        if screen_id not in {known_screens!r}:\n",
            "            return False\n",
            "        self.screen_id = screen_id\n",
            "        self.focus_index = 0\n",
            "        return True\n\n",
            "    def focused_event(self):\n",
            '        """Return the focused element stable event ID."""\n',
            "        events = self._focusable_events()\n",
            "        if not events:\n",
            "            return None\n",
            "        self.focus_index %= len(events)\n",
            "        return events[self.focus_index]\n\n",
            "    def move_focus(self, step):\n",
            '        """Move focus within the active screen."""\n',
            "        if self._active_native_owns_screen():\n",
            "            return self._move_native(step)\n",
            "        events = self._focusable_events()\n",
            "        if not events:\n",
            "            return None\n",
            "        try:\n",
            "            step = int(step)\n",
            "        except (TypeError, ValueError):\n",
            "            step = 0\n",
            "        self.focus_index = (self.focus_index + step) % len(events)\n",
            "        return events[self.focus_index]\n\n",
            "    def activate_focused(self):\n",
            '        """Apply structural navigation and return the activation event."""\n',
            "        if self._active_native_id() is not None:\n",
            "            return self._activate_native()\n",
            "        event_id = self.focused_event()\n",
            "        if event_id is not None:\n",
            "            self.handle_navigation(event_id)\n",
            "        return event_id\n\n",
            "    def handle_navigation(self, event_id):\n",
            '        """Apply one declared screen-flow connection."""\n',
        ]
    )
    navigation_count = 0
    for connection in project.connections:
        if not _generatable_connection(project, connection):
            continue
        target_focus = _target_focus_index(project, connection)
        lines.extend(
            [
                "        if (\n",
                f"            self.screen_id == {connection.source_id!r}\n",
                f"            and event_id == {connection.trigger_event_id!r}\n",
                "        ):\n",
                f"            self.screen_id = {connection.target_id!r}\n",
                f"            self.focus_index = {target_focus}\n",
                f"            self.last_transition = {connection.transition!r}\n",
                "            return True\n",
            ]
        )
        navigation_count += 1
    del navigation_count
    lines.append("        return False\n\n")
    lines.extend(
        [
            "    def behavior_contracts(self):\n",
            '        """Return generated structural behavior contracts."""\n',
            "        return {\n",
            '            "standard": self.FLOW_STANDARD_VERSION,\n',
            '            "nodes": self.FLOW_NODES,\n',
            '            "connections": self.FLOW_CONNECTIONS,\n',
            '            "groups": self.FLOW_GROUPS,\n',
            "        }\n\n",
            "    def describe_behavior_contract(self, node_id, context=None):\n",
            '        """Describe one structural contract without executing it."""\n',
            "        context = {} if context is None else context\n",
            "        for record in self.FLOW_NODES:\n",
            "            if record[0] == node_id:\n",
            "                return {\n",
            '                    "node_id": node_id,\n',
            '                    "stub": record[3],\n',
            '                    "context": context,\n',
            '                    "implemented": False,\n',
            "                }\n",
            "        return None\n\n",
            "    def navigate(self, screen_id):\n",
            '        """Navigate to one stable screen ID."""\n',
            "        return self.set_screen(screen_id)\n\n",
            "    def back(self):\n",
            '        """Apply the declared Back navigation event."""\n',
            '        return self.handle_navigation("event_navigation_back_01")\n\n',
            "    def _is_visible(self, element_id):\n",
            "        return self._element_visibility.get(\n",
            "            element_id, self.ELEMENT_VISIBLE_DEFAULTS.get(element_id, False)\n",
            "        )\n\n",
            "    def _is_enabled(self, element_id):\n",
            "        return self._element_enabled.get(\n",
            "            element_id, self.ELEMENT_ENABLED_DEFAULTS.get(element_id, False)\n",
            "        )\n\n",
            "    def needs_idle_redraw(self):\n",
            '        """Return whether an active native widget needs animation ticks."""\n',
            f"        for element_id in {loading_by_screen!r}.get(self.screen_id, ()):\n",
            "            if self._is_visible(element_id) and self._is_enabled(element_id):\n",
            "                return True\n",
            "        return False\n\n",
            "    def set_value(self, element_id, value):\n",
            '        """Update one native widget through supported public surfaces."""\n',
            "        widget_type = self.ELEMENT_WIDGET_TYPES.get(element_id)\n",
            "        if widget_type is None:\n",
            "            return False\n",
            "        widget = self._ensure_native(element_id)\n",
            "        if widget is None:\n",
            "            if widget_type in ('button', 'label', 'icon', 'list'):\n",
            "                self._element_values[element_id] = str(value)\n",
            "                return True\n",
            "            if widget_type == 'progress' and isinstance(value, (int, float)):\n",
            "                self._element_values[element_id] = max(0, min(100, int(value)))\n",
            "                return True\n",
            "            return False\n",
            "        if widget_type in ('menu', 'list'):\n",
            "            items = widget.list.items if widget_type == 'menu' else widget.items\n",
            "            if isinstance(value, bool):\n",
            "                return False\n",
            "            if isinstance(value, int):\n",
            "                index = value\n",
            "            else:\n",
            "                try:\n",
            "                    index = items.index(value)\n",
            "                except ValueError:\n",
            "                    return False\n",
            "            if index < 0 or index >= len(items):\n",
            "                return False\n",
            "            widget.set_selected(index)\n",
            "        elif widget_type == 'textbox':\n",
            "            widget.current_text = str(value)\n",
            "        elif widget_type == 'toggle':\n",
            "            if value is not True and value is not False:\n",
            "                return False\n",
            "            widget.state = value\n",
            "        elif widget_type == 'toggle_list':\n",
            "            if value is not True and value is not False:\n",
            "                return False\n",
            "            if not widget.update_toggle(widget.selected_index, widget.current_text, value):\n",
            "                return False\n",
            "        elif widget_type == 'choice':\n",
            "            if isinstance(value, bool):\n",
            "                return False\n",
            "            if isinstance(value, int):\n",
            "                index = value\n",
            "            else:\n",
            "                try:\n",
            "                    index = widget.options.index(value)\n",
            "                except ValueError:\n",
            "                    return False\n",
            "            if index < 0 or index >= len(widget.options):\n",
            "                return False\n",
            "            widget.state = index\n",
            "        elif widget_type == 'keyboard':\n",
            "            widget.response = str(value)\n",
            "        else:\n",
            "            return False\n",
            "        return True\n\n",
            "    def set_text(self, element_id, text):\n",
            "        widget_type = self.ELEMENT_WIDGET_TYPES.get(element_id)\n",
            "        widget = self._ensure_native(element_id)\n",
            "        if widget is None:\n",
            "            if widget_type in ('button', 'label', 'icon', 'list'):\n",
            "                self._element_values[element_id] = str(text)\n",
            "                return True\n",
            "            return False\n",
            "        if widget_type == 'menu':\n",
            "            widget.title = str(text)\n",
            "        elif widget_type == 'textbox':\n",
            "            widget.current_text = str(text)\n",
            "        elif widget_type in ('toggle', 'loading', 'alert'):\n",
            "            widget.text = str(text)\n",
            "        elif widget_type == 'keyboard':\n",
            "            widget.title = str(text)\n",
            "        else:\n",
            "            return False\n",
            "        return True\n\n",
            "    def set_progress(self, element_id, value):\n",
            "        if self.ELEMENT_WIDGET_TYPES.get(element_id) != 'progress':\n",
            "            return False\n",
            "        if not isinstance(value, (int, float)) or isinstance(value, bool):\n",
            "            return False\n",
            "        self._element_values[element_id] = max(0, min(100, int(value)))\n",
            "        return True\n\n",
            "    def read_value(self, element_id):\n",
            "        value = self.native_value(element_id)\n",
            "        if value is None:\n",
            "            value = self.ELEMENT_DEFAULT_VALUES.get(element_id)\n",
            "        return self._element_values.get(element_id, value)\n\n",
            "    def read_index(self, element_id):\n",
            '        """Return a public selected index when the widget exposes one."""\n',
            "        widget = self._ensure_native(element_id)\n",
            "        if widget is None:\n",
            "            return None\n",
            "        widget_type = self.ELEMENT_WIDGET_TYPES.get(element_id)\n",
            "        if widget_type == 'choice':\n",
            "            return widget.state\n",
            "        if widget_type in ('menu', 'list', 'toggle_list'):\n",
            "            return widget.selected_index\n",
            "        return None\n\n",
            "    def widget_type(self, element_id):\n",
            "        return self.ELEMENT_WIDGET_TYPES.get(element_id, '')\n\n",
            "    def alert(self, message):\n",
            "        self._behavior_alert = PicowareAlert(self.draw, str(message))\n"
            if has_alert_action
            else "        return False\n",
            "        return True\n\n" if has_alert_action else "\n",
            "    def show(self, element_id):\n",
            "        if element_id not in self.ELEMENT_IDS:\n",
            "            return False\n",
            "        self._element_visibility[element_id] = True\n",
            "        return True\n\n",
            "    def hide(self, element_id):\n",
            "        if element_id not in self.ELEMENT_IDS:\n",
            "            return False\n",
            "        self._element_visibility[element_id] = False\n",
            "        return True\n\n",
            "    def enable(self, element_id, enabled=True):\n",
            "        if element_id not in self.ELEMENT_IDS:\n",
            "            return False\n",
            "        self._element_enabled[element_id] = bool(enabled)\n",
            "        widget = self._ensure_native(element_id)\n",
            "        if widget is None:\n",
            "            return True\n",
            '        if hasattr(widget, "enabled"):\n',
            "            widget.enabled = enabled\n",
            "        return True\n\n",
            "    def focus(self, element_id):\n",
            "        target_event = self.ELEMENT_EVENTS.get(element_id)\n",
            "        events = self._focusable_events()\n",
            "        for index, event_id in enumerate(events):\n",
            "            if event_id == target_event:\n",
            "                self.focus_index = index\n",
            "                return True\n",
            "        return False\n\n",
        ]
    )
    lines.extend(_generated_native_widget_lines(project, native_elements))
    lines.extend(
        [
            "    def _focusable_events(self):\n",
            '        """Return focusable events in configured order."""\n',
        ]
    )
    for index, screen in enumerate(screens):
        keyword = "if" if index == 0 else "elif"
        targets = tuple(
            (element.event_id, element.id)
            for element in _runtime_focusable_elements(screen)
        )
        lines.append(f"        {keyword} self.screen_id == {screen.id!r}:\n")
        lines.append(
            f"            targets = {targets!r}\n"
            "            return tuple(\n"
            "                event_id for event_id, element_id in targets\n"
            "                if self._is_visible(element_id) and self._is_enabled(element_id)\n"
            "            )\n"
        )
    lines.append("        return ()\n")
    for screen in screens:
        lines.extend(_generated_screen_lines(project, screen))
    lines.extend(_generated_focus_lines(screens))
    source = "".join(lines)
    ast.parse(source, filename="generated_ui.py")
    return source


def generate_project_assets_module(
    project: GuiProject,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
) -> str:
    """Generate the bounded-memory runtime manifest for project assets."""
    return generate_project_asset_resource(project, generator_version).module_source


def generate_project_asset_resource(
    project: GuiProject,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
    resource_name: str = "generated_assets.pga",
    *,
    wav_entries: Sequence[GeneratedAudioEntry] = (),
    storage_mode: str = ASSET_STORAGE_COMBINED,
) -> GeneratedAssetResource:
    """Generate referenced images and WAVs using the selected storage contract."""
    referenced_asset_ids = {
        element.asset_id
        for screen in project.screens
        for element in screen.elements
        if element.asset_id
    }
    entries: list[GeneratedRasterEntry | GeneratedAudioEntry] = [
        GeneratedRasterEntry(
            asset.id,
            asset.name,
            asset.pixel_frames(),
            tuple(asset.durations),
        )
        for asset in project.assets
        if asset.id in referenced_asset_ids
    ]
    entries.extend(wav_entries)
    if storage_mode == ASSET_STORAGE_COMBINED:
        return generate_asset_resource(
            project.project_id,
            generator_version,
            entries,
            resource_name,
        )
    if storage_mode == ASSET_STORAGE_INDIVIDUAL:
        return generate_individual_asset_resources(
            project.project_id,
            generator_version,
            entries,
        )
    raise ValueError(f"Unsupported asset storage mode: {storage_mode!r}")


def _project_asset_storage(project: GuiProject) -> str:
    """Return one validated persisted generated-asset storage mode."""
    mode = str(project.generated_app.get("asset_storage", ASSET_STORAGE_COMBINED))
    if mode not in ASSET_STORAGE_MODES:
        raise GeneratedAppError(f"Unsupported asset storage mode: {mode!r}")
    return mode


def build_generated_app_patchset(
    project: GuiProject,
    destination: str | Path,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
) -> GeneratedAppPatchSet:
    """Build and preflight all output artifacts without writing them."""
    paths = resolve_generated_app_paths(project.name, destination)
    asset_resource = generate_project_asset_resource(
        project,
        generator_version,
        storage_mode=_project_asset_storage(project),
    )
    handler_source = generate_behavior_handlers(project)
    if paths.behavior_handlers.is_file():
        try:
            handler_source = _merge_missing_handler_stubs(
                paths.behavior_handlers.read_text(encoding="utf-8"),
                handler_source,
            )
        except (OSError, SyntaxError) as error:
            raise GeneratedAppError(
                f"Cannot safely add behavior handlers: {error}"
            ) from error
    sources = {
        paths.entrypoint: (
            "create-once",
            "entrypoint",
            generate_entrypoint(paths.display_name, paths.package_name),
        ),
        paths.package_init: (
            "create-once",
            "package-init",
            generate_package_init(paths.display_name),
        ),
        paths.app: ("create-once", "app", generate_app_scaffold()),
        paths.behavior_handlers: (
            "developer-additive",
            "behavior-handlers",
            handler_source,
        ),
        paths.generated_behavior: (
            "editor-owned",
            "behavior",
            generate_behavior_module(project, generator_version),
        ),
        paths.generated_ui: (
            "editor-owned",
            "ui",
            generate_ui_module(project, generator_version),
        ),
        paths.generated_assets: (
            "editor-owned",
            "assets",
            asset_resource.module_source,
        ),
    }
    for relative_name, content in asset_resource.files:
        output_path = paths.package / relative_name
        role = (
            "asset-marker"
            if relative_name.endswith("/_picoware_assets.pgl")
            else "asset-data"
            if asset_resource.storage_mode == ASSET_STORAGE_COMBINED
            else "loose-asset"
        )
        sources[output_path] = ("editor-owned-binary", role, content)
    _validate_output_file_set(paths.root, tuple(sources))
    established_project = any(
        _belongs_to_generated_project(path, role, project.project_id)
        for path, (ownership, role, unused_source) in sources.items()
        if ownership == "editor-owned"
    )
    for path, (_, _, source) in sources.items():
        if isinstance(source, str):
            ast.parse(source, filename=str(path))
    marker_path = paths.package / "generated_assets" / "_picoware_assets.pgl"
    loose_owned = False
    if marker_path.is_file():
        try:
            loose_owned = (
                parse_individual_resource_marker_project(marker_path.read_bytes())
                == project.project_id
            )
        except OSError:
            loose_owned = False
    patches = [
        _classify_file(
            path,
            ownership,
            role,
            source,
            project.project_id,
            established_project,
            loose_owned,
        )
        for path, (ownership, role, source) in sources.items()
    ]
    if asset_resource.storage_mode == ASSET_STORAGE_INDIVIDUAL and loose_owned:
        patches.extend(
            _stale_individual_resource_patches(
                paths,
                tuple(sources),
                project.project_id,
            )
        )
    return GeneratedAppPatchSet(paths, tuple(patches), asset_resource)


def apply_generated_app_patchset(
    patchset: GeneratedAppPatchSet,
    backup_root: str | Path | None = None,
) -> GeneratedApplyReport:
    """Apply one reviewed patch set atomically, restoring on any failure."""
    if patchset.blocked:
        details = "; ".join(
            f"{patch.path}: {patch.message or patch.action}"
            for patch in patchset.patches
            if patch.action in {"conflict", "unsupported-version"}
        )
        raise GeneratedAppError(f"Generated app export is blocked: {details}")
    _validate_output_file_set(
        patchset.paths.root,
        tuple(patch.path for patch in patchset.patches),
    )
    for patch in patchset.patches:
        if not patch.path.exists():
            current = None
        elif isinstance(patch.updated, bytes):
            current = patch.path.read_bytes()
        else:
            current = patch.path.read_text(encoding="utf-8")
        current_fingerprint = _fingerprint(current) if current is not None else None
        if current_fingerprint != patch.expected_fingerprint:
            raise GeneratedAppError(f"Source changed after review: {patch.path}")

    changed = patchset.changed_patches()
    preserved = tuple(
        patch.path
        for patch in patchset.patches
        if patch.action in {"preserve", "unchanged"}
    )
    if not changed:
        return GeneratedApplyReport((), (), (), preserved, None)

    backup_base = (
        Path(backup_root).resolve()
        if backup_root is not None
        else patchset.paths.root / ".picoware-backups"
    )
    backup_directory = backup_base / f"generated-app-{uuid.uuid4().hex}"
    temporary_paths: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    created_directories: list[Path] = []
    initially_missing = {patch.path for patch in changed if not patch.path.exists()}
    try:
        _create_missing_directories(backup_base, created_directories)
        backup_directory.mkdir(exist_ok=False)
        for patch in changed:
            _validate_output_file_set(patchset.paths.root, (patch.path,))
            _create_missing_directories(patch.path.parent, created_directories)
            if patch.path.exists():
                backup = backup_directory / str(len(backups))
                shutil.copy2(patch.path, backup)
                backups[patch.path] = backup
            if patch.action == "delete":
                continue
            handle, temporary_name = tempfile.mkstemp(
                prefix=f".{patch.path.name}.",
                suffix=".tmp",
                dir=patch.path.parent,
                text=not isinstance(patch.updated, bytes),
            )
            if isinstance(patch.updated, bytes):
                with os.fdopen(handle, "wb") as temporary:
                    temporary.write(patch.updated)
                    temporary.flush()
                    os.fsync(temporary.fileno())
            else:
                with os.fdopen(
                    handle, "w", encoding="utf-8", newline="\n"
                ) as temporary:
                    temporary.write(patch.updated)
                    temporary.flush()
                    os.fsync(temporary.fileno())
            temporary_path = Path(temporary_name)
            if isinstance(patch.updated, str):
                ast.parse(
                    temporary_path.read_text(encoding="utf-8"),
                    filename=str(patch.path),
                )
            temporary_paths[patch.path] = temporary_path
        for patch in changed:
            if patch.action == "delete":
                replaced.append(patch.path)
                _delete_file(patch.path)
            else:
                _replace_file(temporary_paths[patch.path], patch.path)
                replaced.append(patch.path)
    except Exception as error:
        for path in reversed(replaced):
            backup = backups.get(path)
            if backup is not None:
                shutil.copy2(backup, path)
            elif path in initially_missing:
                path.unlink(missing_ok=True)
        for temporary_path in temporary_paths.values():
            temporary_path.unlink(missing_ok=True)
        if not replaced:
            shutil.rmtree(backup_directory, ignore_errors=True)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise GeneratedAppError(
            f"Generated app transaction rolled back: {error}"
        ) from error
    finally:
        for temporary_path in temporary_paths.values():
            temporary_path.unlink(missing_ok=True)

    return GeneratedApplyReport(
        tuple(patch.path for patch in changed if patch.action == "create"),
        tuple(patch.path for patch in changed if patch.action == "regenerate"),
        tuple(patch.path for patch in changed if patch.action == "delete"),
        preserved,
        backup_directory,
    )


def _replace_file(source: Path, target: Path) -> None:
    """Replace one target; isolated so tests can inject a mid-transaction failure."""
    source.replace(target)


def _delete_file(target: Path) -> None:
    """Delete one reviewed stale output; isolated for rollback tests."""
    target.unlink()


def _stale_individual_resource_patches(
    paths: GeneratedAppPaths,
    desired_paths: Sequence[Path],
    project_id: str,
) -> tuple[GeneratedFilePatch, ...]:
    """Return reviewed deletes for obsolete files in one owned resource folder."""
    resource_directory = paths.package / "generated_assets"
    if not resource_directory.is_dir():
        return ()
    desired = set(desired_paths)
    candidates = tuple(sorted(resource_directory.iterdir(), key=lambda path: path.name))
    _validate_output_file_set(paths.root, candidates)
    patches: list[GeneratedFilePatch] = []
    for path in candidates:
        if path in desired or not path.is_file() or path.suffix not in {".pga", ".wav"}:
            continue
        if not _is_portable_individual_resource_id(path.stem):
            continue
        original = path.read_bytes()
        patches.append(
            _patch(
                path,
                "editor-owned-binary",
                "stale-loose-asset",
                "delete",
                original,
                b"",
                _fingerprint(original),
                f"Remove stale individual resource owned by project {project_id}",
            )
        )
    return tuple(patches)


def _is_portable_individual_resource_id(asset_id: str) -> bool:
    """Match the filename contract used by individual asset generation."""
    if not asset_id or len(asset_id.encode("utf-8")) > 120:
        return False
    return asset_id[0].isalnum() and all(
        character.isascii() and (character.isalnum() or character in "_.-")
        for character in asset_id
    )


def _classify_file(
    path: Path,
    ownership: str,
    role: str,
    updated: str | bytes,
    project_id: str,
    established_project: bool,
    loose_owned: bool = False,
) -> GeneratedFilePatch:
    """Classify one existing path under the fixed ownership contract."""
    if not path.exists():
        original = b"" if isinstance(updated, bytes) else ""
        return _patch(path, ownership, role, "create", original, updated, None)
    if not path.is_file():
        return _patch(
            path,
            ownership,
            role,
            "conflict",
            b"" if isinstance(updated, bytes) else "",
            updated,
            None,
            "Output path is not a regular file",
        )
    if isinstance(updated, bytes):
        original = path.read_bytes()
        fingerprint = _fingerprint(original)
        recognized = (
            parse_asset_resource_project(original) == project_id
            if role == "asset-data"
            else parse_individual_resource_marker_project(original) == project_id
            if role == "asset-marker"
            else loose_owned
        )
        if not recognized:
            return _patch(
                path,
                ownership,
                role,
                "conflict",
                original,
                updated,
                fingerprint,
                "Binary asset output belongs to another project or format",
            )
        action = "unchanged" if original == updated else "regenerate"
        return _patch(
            path,
            ownership,
            role,
            action,
            original,
            updated,
            fingerprint,
        )
    original = path.read_text(encoding="utf-8")
    fingerprint = _fingerprint(original)
    if ownership == "developer-additive":
        if not original.startswith(CREATE_ONCE_NOTICE) and not established_project:
            return _patch(
                path,
                ownership,
                role,
                "conflict",
                original,
                updated,
                fingerprint,
                "Existing handler module is not a recognized generated scaffold",
            )
        action = "preserve" if original == updated else "regenerate"
        return _patch(path, ownership, role, action, original, updated, fingerprint)
    if ownership == "create-once":
        recognized = original.startswith(CREATE_ONCE_NOTICE) or (
            role == "package-init"
            and bool(original.splitlines())
            and "application package" in original.splitlines()[0]
        )
        if not recognized and not established_project:
            return _patch(
                path,
                ownership,
                role,
                "conflict",
                original,
                updated,
                fingerprint,
                "Existing file is not a recognized generated scaffold",
            )
        return _patch(
            path, ownership, role, "preserve", original, original, fingerprint
        )
    header = parse_generated_header(original)
    if header is None:
        return _patch(
            path,
            ownership,
            role,
            "conflict",
            original,
            updated,
            fingerprint,
            "Existing editor-owned file has no recognized header",
        )
    if header.structure != GENERATED_STRUCTURE_VERSION:
        return _patch(
            path,
            ownership,
            role,
            "unsupported-version",
            original,
            updated,
            fingerprint,
            f"Unsupported generated structure {header.structure}",
        )
    if header.project_id != project_id or header.role != role:
        return _patch(
            path,
            ownership,
            role,
            "conflict",
            original,
            updated,
            fingerprint,
            "Generated header belongs to another project or role",
        )
    action = "unchanged" if original == updated else "regenerate"
    return _patch(path, ownership, role, action, original, updated, fingerprint)


def _belongs_to_generated_project(path: Path, role: str, project_id: str) -> bool:
    """Return whether an existing module establishes this generated destination."""
    if not path.is_file():
        return False
    try:
        header = parse_generated_header(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    return bool(
        header
        and header.structure == GENERATED_STRUCTURE_VERSION
        and header.role == role
        and header.project_id == project_id
    )


def _create_missing_directories(path: Path, created: list[Path]) -> None:
    """Create parent directories while retaining cleanup order for rollback."""
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def _patch(
    path: Path,
    ownership: str,
    role: str,
    action: str,
    original: str | bytes,
    updated: str | bytes,
    expected_fingerprint: str | None,
    message: str = "",
) -> GeneratedFilePatch:
    """Build one immutable patch and its review diff."""
    diff = ""
    if action == "delete":
        original_size = len(original)
        diff = (
            f"Delete generated resource: {path}\n"
            f"  {original_size} bytes -> removed\n"
            f"  SHA-256: {_fingerprint(original)}\n"
        )
    elif action in {"create", "regenerate"}:
        if isinstance(updated, bytes):
            previous_size = len(original) if isinstance(original, bytes) else 0
            diff = (
                f"Binary generated resource: {path}\n"
                f"  {previous_size} bytes -> {len(updated)} bytes\n"
                f"  SHA-256: {_fingerprint(updated)}\n"
            )
        else:
            before = original if isinstance(original, str) else ""
            diff = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=str(path),
                    tofile=str(path),
                )
            )
    return GeneratedFilePatch(
        path,
        ownership,
        role,
        action,
        original,
        updated,
        diff,
        expected_fingerprint,
        message,
    )


def _validate_output_paths(paths: GeneratedAppPaths) -> None:
    """Reject path escapes and case-folding collisions."""
    _validate_output_file_set(paths.root, paths.all_files())


def _validate_output_file_set(root: Path, files: Sequence[Path]) -> None:
    """Reject escapes, symbolic-link traversal, and case-folding collisions."""
    root = root.resolve()
    for path in files:
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise GeneratedAppError(
                f"Output path escapes destination: {path}"
            ) from error
        current = root
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                raise GeneratedAppError(
                    f"Generated output path contains a symbolic link: {current}"
                )
            if not current.exists():
                break
        try:
            path.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise GeneratedAppError(
                f"Output path escapes destination: {path}"
            ) from error
    folded = [str(path.relative_to(root)).casefold() for path in files]
    if len(folded) != len(set(folded)):
        raise GeneratedAppError("Generated output paths collide when case-folded")


def _validate_project(project: GuiProject) -> None:
    """Validate all cross-file stable relationships before generation."""
    errors = [
        item
        for item in project_preflight_diagnostics(project)
        if item.severity == "error"
    ]
    if errors:
        raise GeneratedAppError(errors[0].message)


def project_preflight_diagnostics(project: GuiProject) -> list[FlowDiagnostic]:
    """Return every deterministic issue that can block or weaken generation."""
    diagnostics: list[FlowDiagnostic] = []
    if project.format_version != 8:
        diagnostics.append(
            FlowDiagnostic(
                "error",
                "project-format",
                "Generated app export requires a format-8 GUI project.",
            )
        )
    if not project.project_id:
        diagnostics.append(
            FlowDiagnostic(
                "error", "missing-project-id", "The project has no stable project ID."
            )
        )
    elif not isinstance(project.project_id, str) or not PROJECT_ID_PATTERN.fullmatch(
        project.project_id
    ):
        diagnostics.append(
            FlowDiagnostic(
                "error",
                "invalid-project-id",
                "The project ID is not a safe, bounded generated-header identifier.",
            )
        )
    if project.flow_standard_version not in SUPPORTED_FLOW_STANDARD_VERSIONS:
        diagnostics.append(
            FlowDiagnostic(
                "error",
                "flow-standard",
                "Generated app export requires a supported App Flow Standard.",
            )
        )
    screen_ids = [screen.id for screen in project.screens]
    if not screen_ids:
        diagnostics.append(
            FlowDiagnostic("error", "missing-screen", "The project has no screens.")
        )
    elif len(screen_ids) != len(set(screen_ids)):
        diagnostics.append(
            FlowDiagnostic(
                "error",
                "duplicate-screen-id",
                "Project screens require unique stable IDs.",
            )
        )
    if project.start_screen_id not in screen_ids:
        diagnostics.append(
            FlowDiagnostic(
                "error", "missing-start-screen", "The start screen ID is not present."
            )
        )
    asset_ids = [asset.id for asset in project.assets]
    if len(asset_ids) != len(set(asset_ids)):
        diagnostics.append(
            FlowDiagnostic(
                "error",
                "duplicate-asset-id",
                "Project assets require unique stable IDs.",
            )
        )
    known_assets = set(asset_ids)
    event_owners: dict[str, str] = {}
    for screen in project.screens:
        visible_native = [
            element
            for element in screen.elements
            if element.visible and element.kind == "native"
        ]
        screen_widgets = [
            element
            for element in visible_native
            if element.native_widget in NATIVE_WIDGET_IDS
            and native_widget_spec(element.native_widget).full_screen
        ]
        if len(screen_widgets) > 1:
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "multiple-screen-widgets",
                    f"Screen {screen.name!r} has more than one visible Picoware "
                    "screen widget. Use one screen-owning widget per screen.",
                    "screen",
                    screen.id,
                )
            )
        if (
            screen_widgets
            and len([item for item in screen.elements if item.visible]) > 1
        ):
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "screen-widget-with-layers",
                    f"Screen {screen.name!r} mixes a screen-owning Picoware widget "
                    "with other visible layers. Move inline controls and drawn elements "
                    "to a custom layout screen.",
                    "screen",
                    screen.id,
                )
            )
        for element in screen.elements:
            if element.kind == "native":
                if element.native_widget not in NATIVE_WIDGET_IDS:
                    diagnostics.append(
                        FlowDiagnostic(
                            "error",
                            "unknown-native-widget",
                            f"Element {element.name!r} has an unknown Picoware widget type.",
                            "element",
                            element.id,
                        )
                    )
                else:
                    spec = native_widget_spec(element.native_widget)
                    if spec.item_based and not element.widget_items:
                        diagnostics.append(
                            FlowDiagnostic(
                                "error",
                                "empty-native-widget",
                                f"Picoware {spec.name} {element.name!r} needs at least one item.",
                                "element",
                                element.id,
                            )
                        )
                    elif spec.item_based and not (
                        0 <= element.widget_selected_index < len(element.widget_items)
                    ):
                        diagnostics.append(
                            FlowDiagnostic(
                                "error",
                                "native-widget-selected-index",
                                f"Picoware {spec.name} {element.name!r} has a selected "
                                "index outside its item list.",
                                "element",
                                element.id,
                            )
                        )
                    if spec.full_screen and (
                        element.x != 0
                        or element.y != 0
                        or element.width != screen.width
                        or element.height != screen.height
                    ):
                        diagnostics.append(
                            FlowDiagnostic(
                                "warning",
                                "native-widget-geometry",
                                f"Picoware {spec.name} {element.name!r} normally owns the full screen.",
                                "element",
                                element.id,
                            )
                        )
            if element.event_id:
                if element.event_id in event_owners:
                    diagnostics.append(
                        FlowDiagnostic(
                            "error",
                            "duplicate-event-id",
                            f"Element event ID {element.event_id!r} is used more than once.",
                            "element",
                            element.id,
                        )
                    )
                else:
                    event_owners[element.event_id] = element.id
            if element.asset_id and element.asset_id not in known_assets:
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "missing-asset",
                        f"Element {element.name!r} references missing asset {element.asset_id!r}.",
                        "element",
                        element.id,
                    )
                )
            if element.visible and element.focusable and not element.event_id:
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "missing-event-id",
                        f"Focusable element {element.name!r} has no event ID.",
                        "element",
                        element.id,
                    )
                )
            if element.asset_id:
                asset = project.asset(element.asset_id)
                if asset is None:
                    continue
                if asset_element_runtime_scale(element, asset) is None:
                    diagnostics.append(
                        FlowDiagnostic(
                            "error",
                            "invalid-asset-scale",
                            f"Asset {element.name} ({element.id}) is {element.width} x "
                            f"{element.height}, but {asset.name} is {asset.width} x "
                            f"{asset.height}; it requires a uniform integer scale or bake. "
                            "Use natural size or Bake current size in App GUI.",
                            "element",
                            element.id,
                        )
                    )
    for connection in project.connections:
        if (
            connection.source_id not in screen_ids
            or connection.target_id not in screen_ids
        ):
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "missing-connection-screen",
                    f"Connection {connection.id!r} references a missing screen.",
                    "navigation-connection",
                    connection.id,
                )
            )
        if not connection.trigger_event_id:
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "missing-connection-event-id",
                    f"Connection {connection.id!r} has no stable event ID.",
                    "navigation-connection",
                    connection.id,
                )
            )
        source_element = project.element(
            connection.source_id, connection.source_element_id
        )
        if (
            connection.source_id in screen_ids
            and source_element is None
            and connection.trigger_event_id != "event_navigation_back_01"
        ):
            diagnostics.append(
                FlowDiagnostic(
                    "error",
                    "unreachable-navigation-event",
                    f"Connection {connection.id!r} has no element or Back event "
                    "that can emit its trigger.",
                    "navigation-connection",
                    connection.id,
                )
            )
    for screen in project.screens:
        alerts = [
            element
            for element in screen.elements
            if element.kind == "native" and element.native_widget == "alert"
        ]
        for alert in alerts:
            has_exit = any(
                connection.source_id == screen.id
                and (
                    connection.source_element_id == alert.id
                    or connection.trigger_event_id == "event_navigation_back_01"
                )
                for connection in project.connections
            )
            if not has_exit:
                diagnostics.append(
                    FlowDiagnostic(
                        "error",
                        "alert-without-dismiss-route",
                        f"Native Alert {alert.name!r} has no acknowledgement route.",
                        "element",
                        alert.id,
                    )
                )
    diagnostics.extend(flow_diagnostics(project))
    return diagnostics


def _behavior_node_records(project: GuiProject) -> tuple[tuple[object, ...], ...]:
    """Return deterministic structural node records for generated UI code."""
    return tuple(
        (
            node.id,
            node.kind,
            node.name,
            flow_stub_name(node),
            node.description,
            tuple(
                (
                    port.id,
                    port.name,
                    port.direction,
                    port.data_type,
                    port.required,
                    port.multiple,
                )
                for port in node.ports
            ),
            dict(sorted(node.properties.items())),
            node.breakpoint,
        )
        for node in project.behavior_nodes
    )


def _behavior_connection_records(
    project: GuiProject,
) -> tuple[tuple[object, ...], ...]:
    """Return deterministic typed behavior-edge records."""
    return tuple(
        (
            connection.id,
            connection.source_node_id,
            connection.source_port_id,
            connection.target_node_id,
            connection.target_port_id,
            connection.label,
            connection.condition,
        )
        for connection in project.behavior_connections
    )


def _flow_group_records(project: GuiProject) -> tuple[tuple[object, ...], ...]:
    """Return deterministic visual organization records."""
    return tuple(
        (
            group.id,
            group.name,
            group.color,
            group.collapsed,
        )
        for group in project.flow_groups
    )


def _native_import_lines(
    elements: list[GuiElement], include_alert: bool = False
) -> list[str]:
    """Return only the Picoware imports required by native project widgets."""
    widget_ids = {element.native_widget for element in elements}
    if include_alert:
        widget_ids.add("alert")
    lines: list[str] = []
    aliases = {
        "menu": ("picoware.gui.menu", "Menu", "PicowareMenu"),
        "list": ("picoware.gui.list", "List", "PicowareList"),
        "textbox": ("picoware.gui.textbox", "TextBox", "PicowareTextBox"),
        "toggle": ("picoware.gui.toggle", "Toggle", "PicowareToggle"),
        "toggle_list": (
            "picoware.gui.toggle_list",
            "ToggleList",
            "PicowareToggleList",
        ),
        "choice": ("picoware.gui.choice", "Choice", "PicowareChoice"),
        "search_bar": (
            "picoware.gui.search_bar",
            "SearchBar",
            "PicowareSearchBar",
        ),
        "loading": ("picoware.gui.loading", "Loading", "PicowareLoading"),
        "alert": ("picoware.gui.alert", "Alert", "PicowareAlert"),
    }
    for widget_id in sorted(widget_ids):
        record = aliases.get(widget_id)
        if record is not None:
            module, class_name, alias = record
            lines.append(f"from {module} import {class_name} as {alias}\n")
    if widget_ids & {"toggle", "choice"}:
        lines.append("from picoware.system.vector import Vector\n")
    if widget_ids:
        lines.append(
            "from picoware.system.buttons import (BUTTON_BACK, BUTTON_BACKSPACE, "
            "BUTTON_CENTER, BUTTON_DOWN, BUTTON_ESCAPE, BUTTON_LEFT, BUTTON_RIGHT, "
            "BUTTON_UP)\n"
        )
    return lines


def _generated_native_widget_lines(
    project: GuiProject, elements: list[GuiElement]
) -> list[str]:
    """Generate lazy native widget construction, rendering, input, and values."""
    valid = [
        element
        for element in elements
        if element.native_widget in NATIVE_WIDGET_IDS
    ]
    screen_widget_by_screen = {
        screen.id: tuple(
                element.id
                for element in screen.elements
                if element in valid
                and native_widget_spec(element.native_widget).full_screen
        )
        for screen in project.screens
    }
    inline_by_screen = {
        screen.id: tuple(
            (element.event_id, element.id)
            for element in _runtime_focusable_elements(screen)
            if element in valid
            and not native_widget_spec(element.native_widget).full_screen
        )
        for screen in project.screens
    }
    screen_widget_ids = tuple(
        element_id
        for element_ids in screen_widget_by_screen.values()
        for element_id in element_ids
    )
    alert_ids = tuple(
        element.id for element in valid if element.native_widget == "alert"
    )
    lines = [
        "\n    def _active_native_id(self):\n",
        '        """Return the screen widget or focused inline native control."""\n',
        f"        screen_widgets = {screen_widget_by_screen!r}.get(self.screen_id, ())\n",
        "        for screen_widget in screen_widgets:\n",
        "            if self._is_visible(screen_widget) and self._is_enabled(screen_widget):\n",
        "                return screen_widget\n",
        "        event_id = self.focused_event()\n",
        f"        for candidate_event, element_id in {inline_by_screen!r}.get(self.screen_id, ()):\n",
        "            if (candidate_event == event_id and self._is_visible(element_id)\n",
        "                    and self._is_enabled(element_id)):\n",
        "                return element_id\n",
        "        return None\n\n",
        "    def _active_native_owns_screen(self):\n",
        '        """Return whether the active native widget owns screen input."""\n',
        f"        return self._active_native_id() in {screen_widget_ids!r}\n\n",
        "    def _ensure_native(self, element_id):\n",
        '        """Create one Picoware widget lazily for its active screen."""\n',
        "        if element_id in self._native_widgets:\n",
        "            return self._native_widgets[element_id]\n",
        "        widget = None\n",
    ]
    for index, element in enumerate(valid):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"        {keyword} element_id == {element.id!r}:\n")
        lines.extend(_native_constructor_lines(element, "            "))
    lines.extend(
        [
            "        if widget is not None:\n",
            "            self._native_widgets[element_id] = widget\n",
            "        return widget\n\n",
            "    def _render_native(self, element_id):\n",
            '        """Render one real Picoware widget through its public API."""\n',
            "        widget = self._ensure_native(element_id)\n",
            "        if widget is None:\n",
            "            return\n",
        ]
    )
    for index, element in enumerate(valid):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"        {keyword} element_id == {element.id!r}:\n")
        lines.extend(_native_render_lines(element, "            "))
    if not valid:
        lines.append("        return\n")
    lines.extend(
        [
            "\n    def _move_native(self, step):\n",
            '        """Move selection inside the active native widget."""\n',
            "        element_id = self._active_native_id()\n",
            "        widget = self._ensure_native(element_id) if element_id else None\n",
            "        if widget is None:\n",
            "            return None\n",
        ]
    )
    selectable = {
        element.id: element
        for element in valid
        if element.native_widget in {"menu", "list", "choice"}
    }
    for index, element in enumerate(selectable.values()):
        keyword = "if" if index == 0 else "elif"
        lines.extend(
            [
                f"        {keyword} element_id == {element.id!r}:\n",
                "            if step < 0:\n",
                "                widget.scroll_up()\n",
                "            elif step > 0:\n",
                "                widget.scroll_down()\n",
                f"            return {element.event_id!r}\n",
            ]
        )
    lines.extend(
        [
            "        return None\n\n",
            "    def _activate_native(self):\n",
            '        """Activate the selected value of the active native widget."""\n',
            "        element_id = self._active_native_id()\n",
            "        widget = self._ensure_native(element_id) if element_id else None\n",
            "        if widget is None:\n",
            "            return None\n",
        ]
    )
    interactive = [
        element
        for element in valid
        if native_widget_spec(element.native_widget).interactive
    ]
    for index, element in enumerate(interactive):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"        {keyword} element_id == {element.id!r}:\n")
        if element.native_widget == "toggle":
            lines.append("            widget.state = not widget.state\n")
        lines.extend(
            [
                f"            event_id = {element.event_id!r}\n",
                "            self.handle_navigation(event_id)\n",
                "            return event_id\n",
            ]
        )
    lines.extend(
        [
            "        return None\n\n",
            "    def handle_input(self, button):\n",
            '        """Let a native widget consume one Picoware input value."""\n',
            "        if self._behavior_alert is not None:\n",
            "            self._behavior_alert = None\n",
            "            return None, True\n",
            "        element_id = self._active_native_id()\n",
            "        if element_id is None:\n",
            "            return None, False\n",
            "        widget = self._ensure_native(element_id)\n",
            "        if widget is None:\n",
            "            return None, False\n",
            f"        if button == BUTTON_BACK and element_id not in {alert_ids!r}:\n",
            "            return None, False\n",
        ]
    )
    for index, element in enumerate(valid):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"        {keyword} element_id == {element.id!r}:\n")
        lines.extend(_native_input_lines(element, "            "))
    lines.extend(
        [
            "        return None, True\n\n",
            "    def native_value(self, element_id):\n",
            '        """Return the current public value of one native widget."""\n',
            "        widget = self._ensure_native(element_id)\n",
            "        if widget is None:\n",
            "            return None\n",
        ]
    )
    for index, element in enumerate(valid):
        keyword = "if" if index == 0 else "elif"
        lines.append(f"        {keyword} element_id == {element.id!r}:\n")
        lines.extend(_native_value_lines(element, "            "))
    lines.extend(["        return None\n"])
    return lines


def _native_constructor_lines(element: GuiElement, prefix: str) -> list[str]:
    """Generate construction for one supported native widget."""
    widget = element.native_widget
    items = tuple(element.widget_items)
    colors = (
        f"0x{element.text_color:04X}",
        f"0x{element.fill_color:04X}",
        f"0x{element.border_color:04X}",
    )
    text, fill, border = colors
    if widget == "menu":
        lines = [
            f"{prefix}widget = PicowareMenu(self.draw, {element.text!r}, {element.y}, {element.height}, {text}, {fill}, {border}, {text}, 2)\n"
        ]
        lines.extend(f"{prefix}widget.add_item({item!r})\n" for item in items)
        lines.append(f"{prefix}widget.set_selected({element.widget_selected_index})\n")
        return lines
    if widget == "list":
        lines = [
            f"{prefix}widget = PicowareList(self.draw, {element.y}, {element.height}, {text}, {fill}, {border}, {text}, 2)\n"
        ]
        lines.extend(f"{prefix}widget.add_item({item!r})\n" for item in items)
        lines.append(f"{prefix}widget.set_selected({element.widget_selected_index})\n")
        return lines
    if widget == "textbox":
        return [
            f"{prefix}widget = PicowareTextBox(self.draw, {element.y}, {element.height}, {text}, {fill}, True)\n",
            f"{prefix}widget.current_text = {element.text!r}\n",
        ]
    if widget == "toggle":
        return [
            f"{prefix}widget = PicowareToggle(self.draw, Vector({element.x}, {element.y}), Vector({element.width}, {element.height}), {element.text!r}, {element.widget_state!r}, {text}, {fill}, {border}, {text}, 1)\n"
        ]
    if widget == "toggle_list":
        states = list(element.widget_item_states)
        lines = [
            f"{prefix}if self.view_manager is not None:\n",
            f"{prefix}    widget = PicowareToggleList(self.view_manager, {text}, {fill}, {border}, {text}, 1)\n",
        ]
        lines.extend(
            f"{prefix}    widget.add_toggle({item!r}, {(bool(states[index]) if index < len(states) else False)!r})\n"
            for index, item in enumerate(items)
        )
        return lines
    if widget == "choice":
        return [
            f"{prefix}widget = PicowareChoice(self.draw, Vector({element.x}, {element.y}), Vector({element.width}, {element.height}), {element.text!r}, {list(items)!r}, {element.widget_selected_index}, {text}, {fill})\n"
        ]
    if widget == "keyboard":
        return [
            f"{prefix}if self.view_manager is not None:\n",
            f"{prefix}    widget = self.view_manager.keyboard\n",
            f"{prefix}    widget.reset()\n",
            f"{prefix}    widget.title = {element.text!r}\n",
        ]
    if widget == "search_bar":
        return [
            f"{prefix}if self.view_manager is not None:\n",
            f"{prefix}    widget = PicowareSearchBar(self.view_manager, {list(items)!r}, None, {text}, {fill}, {border})\n",
        ]
    if widget == "loading":
        return [
            f"{prefix}widget = PicowareLoading(self.draw, {border}, {fill})\n",
            f"{prefix}widget.text = {element.text!r}\n",
        ]
    if widget == "alert":
        return [
            f"{prefix}widget = PicowareAlert(self.draw, {element.text!r}, {text}, {fill})\n"
        ]
    return [f"{prefix}widget = None\n"]


def _native_render_lines(element: GuiElement, prefix: str) -> list[str]:
    """Generate a public render call for one native widget."""
    widget = element.native_widget
    if widget in {"menu", "list", "choice", "toggle"}:
        return [f"{prefix}widget.draw()\n"]
    if widget == "textbox":
        return [f"{prefix}widget.refresh()\n"]
    if widget == "toggle_list":
        return [f"{prefix}widget.run()\n"]
    if widget == "keyboard":
        return [f"{prefix}widget.run(force=True)\n"]
    if widget == "search_bar":
        return [f"{prefix}widget.run(force=True)\n"]
    if widget == "loading":
        return [f"{prefix}widget.animate(False)\n"]
    if widget == "alert":
        return [f"{prefix}widget.draw({element.name!r})\n"]
    return [f"{prefix}return\n"]


def _native_input_lines(element: GuiElement, prefix: str) -> list[str]:
    """Generate input delegation for one native widget."""
    widget = element.native_widget
    event = element.event_id
    if widget in {"menu", "list"}:
        return [
            f"{prefix}if button in (BUTTON_UP, BUTTON_LEFT):\n",
            f"{prefix}    widget.scroll_up()\n",
            f"{prefix}elif button in (BUTTON_DOWN, BUTTON_RIGHT):\n",
            f"{prefix}    widget.scroll_down()\n",
            f"{prefix}elif button == BUTTON_CENTER:\n",
            f"{prefix}    event_id = {event!r}\n",
            f"{prefix}    self.handle_navigation(event_id)\n",
            f"{prefix}    return event_id, True\n",
            f"{prefix}return None, True\n",
        ]
    if widget == "choice":
        return [
            f"{prefix}if button == BUTTON_LEFT:\n",
            f"{prefix}    widget.scroll_up()\n",
            f"{prefix}elif button == BUTTON_RIGHT:\n",
            f"{prefix}    widget.scroll_down()\n",
            f"{prefix}elif button == BUTTON_CENTER:\n",
            f"{prefix}    event_id = {event!r}\n",
            f"{prefix}    self.handle_navigation(event_id)\n",
            f"{prefix}    return event_id, True\n",
            f"{prefix}else:\n",
            f"{prefix}    return None, False\n",
            f"{prefix}return None, True\n",
        ]
    if widget == "textbox":
        return [
            f"{prefix}if button in (BUTTON_UP, BUTTON_LEFT):\n",
            f"{prefix}    widget.scroll_up()\n",
            f"{prefix}elif button in (BUTTON_DOWN, BUTTON_RIGHT):\n",
            f"{prefix}    widget.scroll_down()\n",
            f"{prefix}return None, True\n",
        ]
    if widget == "toggle":
        return [
            f"{prefix}if button == BUTTON_CENTER:\n",
            f"{prefix}    widget.state = not widget.state\n",
            f"{prefix}    event_id = {event!r}\n",
            f"{prefix}    self.handle_navigation(event_id)\n",
            f"{prefix}    return event_id, True\n",
            f"{prefix}return None, False\n",
        ]
    if widget == "toggle_list":
        return [
            f"{prefix}before = widget.current_state\n",
            f"{prefix}widget.run()\n",
            f"{prefix}if button == BUTTON_CENTER and widget.current_state != before:\n",
            f"{prefix}    event_id = {event!r}\n",
            f"{prefix}    self.handle_navigation(event_id)\n",
            f"{prefix}    return event_id, True\n",
            f"{prefix}return None, True\n",
        ]
    if widget in {"keyboard", "search_bar"}:
        return [
            f"{prefix}widget.run()\n",
            f"{prefix}if getattr(widget, 'is_finished', False):\n",
            f"{prefix}    event_id = {event!r}\n",
            f"{prefix}    self.handle_navigation(event_id)\n",
            f"{prefix}    return event_id, True\n",
            f"{prefix}return None, True\n",
        ]
    if widget == "alert":
        return [
            f"{prefix}event_id = {event!r}\n",
            f"{prefix}if not self.handle_navigation(event_id):\n",
            f"{prefix}    self.handle_navigation('event_navigation_back_01')\n",
            f"{prefix}return event_id, True\n",
        ]
    return [f"{prefix}return None, True\n"]


def _native_value_lines(element: GuiElement, prefix: str) -> list[str]:
    """Generate access to one native widget's public current value."""
    widget = element.native_widget
    if widget in {"menu", "list"}:
        return [f"{prefix}return widget.current_item\n"]
    if widget == "choice":
        return [
            f"{prefix}return widget.options[widget.state] if widget.options else None\n"
        ]
    if widget == "toggle":
        return [f"{prefix}return widget.state\n"]
    if widget == "toggle_list":
        return [
            f"{prefix}return (widget.selected_index, widget.current_text, widget.current_state)\n"
        ]
    if widget == "textbox":
        return [f"{prefix}return widget.current_text\n"]
    if widget == "keyboard":
        return [f"{prefix}return widget.response\n"]
    if widget == "search_bar":
        return [f"{prefix}return widget.selected_item\n"]
    return [f"{prefix}return None\n"]


def _generated_screen_lines(project: GuiProject, screen: ScreenDesign) -> list[str]:
    """Generate one screen method without embedded asset records."""
    lines = [
        "\n",
        f"    def {_screen_method(screen)}(self):\n",
        f'        """Draw the {screen.name} screen."""\n',
        f"        self.draw._fill_rectangle(0, 0, {screen.width}, {screen.height}, 0x{screen.background_color:04X})\n",
    ]
    for element in screen.elements:
        lines.append(f"        if self._is_visible({element.id!r}):\n")
        lines.extend(_generated_element_lines(project, element, "            "))
    return lines


def _generated_element_lines(
    project: GuiProject,
    element: GuiElement,
    prefix: str = "        ",
) -> list[str]:
    """Generate presentation calls for one visible element."""
    fill = f"0x{element.fill_color:04X}"
    border = f"0x{element.border_color:04X}"
    text_color = f"0x{element.text_color:04X}"
    if element.kind == "native":
        return [f"{prefix}self._render_native({element.id!r})\n"]
    if element.asset_id:
        asset = project.asset(element.asset_id)
        if asset is None:
            raise GeneratedAppError(f"Missing asset {element.asset_id}")
        scale = asset_element_runtime_scale(element, asset)
        if scale is None:
            raise GeneratedAppError(
                f"Asset {element.name} ({element.id}) needs a device-safe size"
            )
        return [
            f"{prefix}draw_asset(\n",
            f"{prefix}    self.draw,\n",
            f"{prefix}    {element.asset_id!r},\n",
            f"{prefix}    {element.x},\n",
            f"{prefix}    {element.y},\n",
            f"{prefix}    frame={max(0, int(element.asset_frame))},\n",
            f"{prefix}    scale={scale},\n",
            f"{prefix})\n",
        ]
    if element.kind == "label":
        return [
            f"{prefix}self.draw._text({element.x}, {element.y}, self._element_values.get({element.id!r}, {element.text!r}), {text_color})\n"
        ]
    rectangle = (
        f"{prefix}self.draw._fill_rectangle({element.x}, {element.y}, "
        f"{element.width}, {element.height}, {fill})\n"
    )
    outline = (
        f"{prefix}self.draw._rectangle({element.x}, {element.y}, "
        f"{element.width}, {element.height}, {border})\n"
    )
    lines = [rectangle]
    if element.kind in {"button", "panel", "list", "icon", "progress"}:
        lines.append(outline)
    if element.kind in {"button", "icon"} and element.text:
        lines.append(
            f"{prefix}self.draw._text({element.x + 4}, {element.y + 4}, self._element_values.get({element.id!r}, {element.text!r}), {text_color})\n"
        )
    elif element.kind == "list":
        lines.extend(
            [
                f"{prefix}for item_index, item_text in enumerate(str(self._element_values.get({element.id!r}, {element.text!r})).splitlines()):\n",
                f"{prefix}    self.draw._text({element.x + 4}, {element.y + 4} + item_index * 14, item_text, {text_color})\n",
            ]
        )
    elif element.kind == "progress":
        lines.append(
            f"{prefix}self.draw._fill_rectangle({element.x}, {element.y}, max(1, min({element.width}, int(self._element_values.get({element.id!r}, 50)) * {element.width} // 100)), {element.height}, {border})\n"
        )
    return lines


def _generated_focus_lines(screens: list[ScreenDesign]) -> list[str]:
    """Generate the active element focus indicator."""
    lines = [
        "\n    def _draw_focus(self):\n",
        '        """Draw the configured focus indicator."""\n',
    ]
    for screen_index, screen in enumerate(screens):
        keyword = "if" if screen_index == 0 else "elif"
        elements = _runtime_focusable_elements(screen)
        lines.append(f"        {keyword} self.screen_id == {screen.id!r}:\n")
        if not elements:
            lines.append("            return\n")
            continue
        lines.append("            event_id = self.focused_event()\n")
        for index, element in enumerate(elements):
            focus_keyword = "if" if index == 0 else "elif"
            lines.append(
                f"            {focus_keyword} event_id == {element.event_id!r}:\n"
            )
            lines.extend(_focus_element_lines(element, "                "))
            lines.append("                return\n")
    if not screens:
        lines.append("        return\n")
    return lines


def _focus_element_lines(element: GuiElement, prefix: str) -> list[str]:
    """Generate one simple configured focus style."""
    if (
        element.kind == "native"
        and element.native_widget in NATIVE_WIDGET_IDS
        and native_widget_spec(element.native_widget).full_screen
    ):
        return [f"{prefix}pass\n"]
    if element.focus_style == "none":
        return [f"{prefix}pass\n"]
    color = f"0x{element.focus_color:04X}"
    thickness = max(1, min(6, int(element.focus_thickness)))
    padding = max(0, min(12, int(element.focus_padding)))
    if element.focus_style == "underline":
        return [
            f"{prefix}self.draw._fill_rectangle({element.x - padding}, "
            f"{element.y + element.height + padding}, {element.width + padding * 2}, "
            f"{thickness}, {color})\n"
        ]
    return [
        f"{prefix}self.draw._rectangle({element.x - padding - offset}, "
        f"{element.y - padding - offset}, "
        f"{element.width + (padding + offset) * 2}, "
        f"{element.height + (padding + offset) * 2}, {color})\n"
        for offset in range(thickness)
    ]


def _screen_method(screen: ScreenDesign) -> str:
    """Return a collision-free method name derived from a stable screen ID."""
    identifier = re.sub(r"[^A-Za-z0-9_]+", "_", screen.id).strip("_")
    if not identifier or identifier[0].isdigit():
        identifier = "screen_" + identifier
    return f"_draw_{identifier}"


def _focusable_elements(screen: ScreenDesign) -> list[GuiElement]:
    """Return visible enabled focus targets in configured stable order."""
    indexed = [
        (element.focus_order, index, element)
        for index, element in enumerate(screen.elements)
        if element.visible and element.enabled and element.focusable
    ]
    return [item[2] for item in sorted(indexed, key=lambda item: (item[0], item[1]))]


def _runtime_focusable_elements(screen: ScreenDesign) -> list[GuiElement]:
    """Return all focus targets whose runtime visibility or enabled state may change."""
    indexed = [
        (element.focus_order, index, element)
        for index, element in enumerate(screen.elements)
        if element.focusable
    ]
    return [item[2] for item in sorted(indexed, key=lambda item: (item[0], item[1]))]


def _generatable_connection(project: GuiProject, connection: FlowConnection) -> bool:
    """Allow only structural connections that require no invented behavior."""
    source_element = project.element(
        connection.source_id, connection.source_element_id
    )
    return bool(
        project.screen(connection.source_id)
        and project.screen(connection.target_id)
        and connection.trigger_event_id
        and (
            source_element is not None
            or connection.trigger_event_id == "event_navigation_back_01"
        )
        and not connection.condition.strip()
        and not connection.action.strip()
    )


def _target_focus_index(project: GuiProject, connection: FlowConnection) -> int:
    """Return the configured target focus index for one transition."""
    target = project.screen(connection.target_id)
    if target is None or not connection.target_element_id:
        return 0
    ids = [element.id for element in _focusable_elements(target)]
    try:
        return ids.index(connection.target_element_id)
    except ValueError:
        return 0


def _fingerprint(source: str | bytes) -> str:
    """Return the review-time SHA-256 content fingerprint."""
    data = source if isinstance(source, bytes) else source.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
