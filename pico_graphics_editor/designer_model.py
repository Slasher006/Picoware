"""Persisted GUI designer and screen-flow project model."""

from __future__ import annotations

import ast
import difflib
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
)
DESIGNER_MARKER = re.compile(
    r"^# Pico GUI Designer (?P<edge>begin|end)\s*$", re.MULTILINE
)


def new_identifier(prefix: str) -> str:
    """Return a concise unique project identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


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

    @classmethod
    def create(cls, kind: str, index: int) -> GuiElement:
        """Create one element with practical defaults."""
        widths = {"label": 100, "icon": 32, "progress": 120}
        heights = {"label": 20, "icon": 32, "list": 90, "progress": 16}
        labels = {
            "button": "Button",
            "label": "Label",
            "panel": "Panel",
            "rectangle": "",
            "icon": "Icon",
            "list": "Item 1\nItem 2\nItem 3",
            "progress": "",
        }
        normalized = kind if kind in ELEMENT_KINDS else "rectangle"
        return cls(
            new_identifier("element"),
            normalized,
            f"{normalized}_{index}",
            16 + (index % 5) * 8,
            16 + (index % 5) * 8,
            widths.get(normalized, 120),
            heights.get(normalized, 36),
            labels.get(normalized, ""),
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> GuiElement:
        """Create an element from persisted values."""
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in allowed})


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
            node_x=70 + (index % 4) * 220,
            node_y=70 + (index // 4) * 150,
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

    @classmethod
    def create(cls, source_id: str, target_id: str, trigger: str) -> FlowConnection:
        """Create one screen-flow connection."""
        return cls(new_identifier("flow"), source_id, target_id, trigger)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> FlowConnection:
        """Create a connection from persisted values."""
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in allowed})


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
    format_version: int = 1
    import_root: str = ""
    imported_sources: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls, name: str = "Untitled GUI", profile: str = "PicoCalc 320x320"
    ) -> GuiProject:
        """Create a project with one initial screen."""
        width, height = DEVICE_PROFILES.get(profile, (320, 320))
        screen = ScreenDesign.create("Main", width, height, 0)
        return cls(name, profile, width, height, [screen], [], screen.id)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> GuiProject:
        """Create a project from validated JSON-compatible values."""
        screens = [ScreenDesign.from_dict(item) for item in values.get("screens", [])]
        connections = [
            FlowConnection.from_dict(item) for item in values.get("connections", [])
        ]
        project = cls(
            str(values.get("name", "Untitled GUI")),
            str(values.get("profile", "Custom")),
            max(1, int(values.get("width", 320))),
            max(1, int(values.get("height", 320))),
            screens,
            connections,
            str(values.get("start_screen_id", "")),
            int(values.get("format_version", 1)),
            str(values.get("import_root", "")),
            {
                str(path): str(digest)
                for path, digest in values.get("imported_sources", {}).items()
            },
        )
        if not project.screens:
            project.screens.append(
                ScreenDesign.create("Main", project.width, project.height, 0)
            )
        if not project.screen(project.start_screen_id):
            project.start_screen_id = project.screens[0].id
        return project

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible project values."""
        return asdict(self)

    def screen(self, screen_id: str) -> ScreenDesign | None:
        """Return a screen by identifier."""
        return next((screen for screen in self.screens if screen.id == screen_id), None)

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
        values = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("GUI project root must be an object")
        return cls.from_dict(values)


def build_designer_patch(project: GuiProject, path: str | Path) -> SourcePatch:
    """Build a reviewed Python patch for one GUI project."""
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
        ]
    )
    for screen in project.screens:
        lines.extend(_screen_python(screen))
    lines.append("# Pico GUI Designer end\n")
    return "".join(lines)


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
