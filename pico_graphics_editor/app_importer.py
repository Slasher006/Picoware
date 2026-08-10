"""Safe existing-application import and narrow source patching."""

from __future__ import annotations

import ast
import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .designer_model import (
    DEVICE_PROFILES,
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
    new_identifier,
)
from .source import (
    DRAW_SIGNATURES,
    TEXT_CALLS,
    SafeEvaluator,
    SourcePatch,
    UnknownValue,
    call_name,
    collect_constants,
    function_parameters,
)


SCREEN_WORDS = (
    "screen",
    "menu",
    "view",
    "page",
    "dialog",
    "scene",
    "render",
    "title",
    "leaderboard",
    "hud",
    "forecast",
    "settings",
    "help",
)
STATE_WORDS = ("screen", "view", "page", "scene", "mode", "state")
HELPER_WORDS = ("button", "icon", "list", "panel", "image", "sprite", "widget")


@dataclass
class AppImportResult:
    """Hold an imported project and its discovery report."""

    project: GuiProject
    warnings: list[str]
    editable_count: int
    locked_count: int
    files_scanned: int


@dataclass
class _SourceUnit:
    """Hold one parsed application source file."""

    path: Path
    source: str
    tree: ast.Module
    constants: dict[str, Any]


@dataclass
class _FunctionUnit:
    """Describe one module function or class method."""

    node: ast.FunctionDef | ast.AsyncFunctionDef
    class_name: str | None


@dataclass
class _ScreenCandidate:
    """Describe one drawable function or state branch."""

    unit: _SourceUnit
    function: _FunctionUnit
    name: str
    body: list[ast.stmt]
    line: int
    state_name: str = ""
    state_value: Any = None


@dataclass
class _Replacement:
    """Describe one exact imported-source replacement."""

    segment: str
    updated: str
    line: int


class ExistingAppImporter:
    """Discover editable GUI screens without executing application code."""

    def import_path(
        self,
        path: str | Path,
        profile: str = "PicoCalc 320x320",
        limit: int = 1200,
    ) -> AppImportResult:
        """Import recognized screens and flows from a Python file or folder."""
        source_path = Path(path).expanduser().resolve()
        paths = self._source_paths(source_path, limit)
        if not paths:
            raise ValueError(f"No Python source files found: {source_path}")
        warnings: list[str] = []
        units: list[_SourceUnit] = []
        for item in paths:
            try:
                source = item.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(item))
                units.append(
                    _SourceUnit(item, source, tree, collect_constants(item, tree))
                )
            except (OSError, SyntaxError, UnicodeError) as error:
                warnings.append(f"{item}: {error}")
        width, height = DEVICE_PROFILES.get(profile, (320, 320))
        project_name = source_path.stem if source_path.is_file() else source_path.name
        project = GuiProject.create(project_name or "Imported App", profile)
        project.width = width
        project.height = height
        project.screens.clear()
        project.import_root = str(source_path)
        project.imported_sources = {
            str(unit.path): _source_digest(unit.source) for unit in units
        }
        candidates: list[_ScreenCandidate] = []
        for unit in units:
            candidates.extend(self._screen_candidates(unit, warnings))
        used_names: set[str] = set()
        candidate_screens: list[tuple[_ScreenCandidate, ScreenDesign]] = []
        editable_count = 0
        locked_count = 0
        for index, candidate in enumerate(candidates):
            name = _unique_name(candidate.name, used_names)
            used_names.add(name)
            screen = ScreenDesign.create(name, width, height, index)
            screen.source_path = str(candidate.unit.path)
            screen.source_name = candidate.function.node.name
            screen.source_line = candidate.line
            screen.source_state = candidate.state_value
            elements, element_warnings = self._screen_elements(
                candidate, screen, width, height
            )
            screen.elements = elements
            warnings.extend(element_warnings)
            editable_count += sum(not element.locked for element in elements)
            locked_count += sum(element.locked for element in elements)
            project.screens.append(screen)
            candidate_screens.append((candidate, screen))
        if not project.screens:
            fallback = ScreenDesign.create("Main", width, height, 0)
            project.screens.append(fallback)
            warnings.append("No recognizable application screens were found.")
        project.start_screen_id = (
            self._infer_start_screen(units, candidate_screens) or project.screens[0].id
        )
        project.connections = self._infer_connections(units, candidate_screens)
        return AppImportResult(
            project,
            _unique_strings(warnings),
            editable_count,
            locked_count,
            len(units),
        )

    def _source_paths(self, path: Path, limit: int) -> list[Path]:
        """Return bounded Python source paths for an import target."""
        if path.is_file():
            return [path] if path.suffix.lower() == ".py" else []
        result: list[Path] = []
        for item in sorted(path.rglob("*.py")):
            if any(
                part.startswith(".") or part in {"__pycache__", "venv", ".venv"}
                for part in item.parts
            ):
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def _screen_candidates(
        self, unit: _SourceUnit, warnings: list[str]
    ) -> list[_ScreenCandidate]:
        """Discover state branches and standalone drawing screens."""
        candidates: list[_ScreenCandidate] = []
        functions = _functions(unit.tree)
        drawing_functions = _drawing_function_names(functions)
        function_lookup = {
            (function.class_name or "", function.node.name): function
            for function in functions
        }
        for function in functions:
            evaluator = _function_evaluator(unit, function.node)
            branches: list[_ScreenCandidate] = []
            for child in ast.walk(function.node):
                if not isinstance(child, ast.If) or not _body_has_drawing(
                    child.body, drawing_functions
                ):
                    continue
                state = _state_test(child.test, evaluator)
                if state is None:
                    continue
                state_name, state_value = state
                screen_function = function
                screen_body = child.body
                helper = _called_screen_helper(
                    child.body,
                    function.class_name,
                    function_lookup,
                    drawing_functions,
                )
                if helper is not None:
                    screen_function = helper
                    screen_body = helper.node.body
                branches.append(
                    _ScreenCandidate(
                        unit,
                        screen_function,
                        _display_state_name(state_value, state_name),
                        screen_body,
                        child.lineno,
                        state_name,
                        state_value,
                    )
                )
            if branches:
                candidates.extend(branches)
                continue
            if not _body_has_drawing(function.node.body, drawing_functions):
                continue
            lowered = function.node.name.lower()
            screen_named = any(word in lowered for word in SCREEN_WORDS) or lowered in {
                "draw",
                "draw_frame",
                "run",
                "start",
            }
            if not screen_named or len(_required_parameter_names(function.node)) > 1:
                continue
            candidates.append(
                _ScreenCandidate(
                    unit,
                    function,
                    _display_function_name(function, unit.path),
                    function.node.body,
                    function.node.lineno,
                )
            )
        if len(candidates) > 80:
            warnings.append(f"{unit.path}: limited screen discovery to 80 candidates")
            return candidates[:80]
        return candidates

    def _screen_elements(
        self,
        candidate: _ScreenCandidate,
        screen: ScreenDesign,
        width: int,
        height: int,
    ) -> tuple[list[GuiElement], list[str]]:
        """Convert recognized drawing calls into designer elements."""
        evaluator = _function_evaluator(candidate.unit, candidate.function.node)
        dynamic_names = _required_parameter_names(candidate.function.node)
        drawing_helpers = _drawing_function_names(_functions(candidate.unit.tree))
        warnings: list[str] = []
        elements: list[GuiElement] = []

        def visit(statements: Iterable[ast.stmt], force_locked: bool = False) -> None:
            """Visit drawable statements in source order."""
            for statement in statements:
                if isinstance(statement, ast.Expr) and isinstance(
                    statement.value, ast.Call
                ):
                    element = _element_from_call(
                        candidate.unit,
                        statement.value,
                        evaluator,
                        dynamic_names,
                        drawing_helpers,
                        len(elements) + 1,
                        force_locked,
                    )
                    if element is not None:
                        elements.append(element)
                    continue
                if isinstance(statement, ast.If):
                    condition = evaluator.evaluate(statement.test)
                    if isinstance(condition, UnknownValue):
                        branch = statement.body or statement.orelse
                        visit(branch, True)
                        warnings.append(
                            f"{candidate.unit.path}:{statement.lineno}: dynamic branch imported as locked"
                        )
                    else:
                        visit(
                            statement.body if condition else statement.orelse,
                            force_locked,
                        )
                    continue
                if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                    visit(statement.body, True)
                    warnings.append(
                        f"{candidate.unit.path}:{statement.lineno}: loop content imported as locked"
                    )
                    continue
                if isinstance(statement, ast.Try):
                    visit(statement.body, True)

        visit(candidate.body)
        if elements:
            first = elements[0]
            if (
                first.source_values.get("call_type") == "fill_rect"
                and first.x <= 0
                and first.y <= 0
                and first.width >= width
                and first.height >= height
            ):
                screen.background_color = first.fill_color
        return elements, warnings

    def _infer_start_screen(
        self,
        units: list[_SourceUnit],
        candidate_screens: list[tuple[_ScreenCandidate, ScreenDesign]],
    ) -> str | None:
        """Infer the initial state assignment from constructors."""
        state_map = _state_screen_map(candidate_screens)
        for unit in units:
            evaluator = SafeEvaluator(dict(unit.constants), [])
            for function in _functions(unit.tree):
                if function.node.name != "__init__":
                    continue
                for child in ast.walk(function.node):
                    assignment = _state_assignment(child, evaluator)
                    if assignment is None:
                        continue
                    name, value, _, _ = assignment
                    screen = _lookup_state_screen(
                        state_map,
                        unit.path,
                        function.class_name,
                        name,
                        value,
                    )
                    if screen is not None:
                        return screen.id
        return None

    def _infer_connections(
        self,
        units: list[_SourceUnit],
        candidate_screens: list[tuple[_ScreenCandidate, ScreenDesign]],
    ) -> list[FlowConnection]:
        """Infer screen transitions from state-aware input branches."""
        state_map = _state_screen_map(candidate_screens)
        function_map = {
            (
                str(candidate.unit.path),
                candidate.function.class_name or "",
                candidate.function.node.name,
            ): screen
            for candidate, screen in candidate_screens
            if candidate.state_name == ""
        }
        connections: list[FlowConnection] = []
        seen: set[tuple[str, str, str]] = set()
        for unit in units:
            evaluator = SafeEvaluator(dict(unit.constants), [])
            for function in _functions(unit.tree):
                initial = function_map.get(
                    (str(unit.path), function.class_name or "", function.node.name)
                )

                def visit(
                    statements: Iterable[ast.stmt], current: ScreenDesign | None
                ) -> None:
                    """Visit nested state and input conditions."""
                    for statement in statements:
                        if not isinstance(statement, ast.If):
                            continue
                        state = _state_test(statement.test, evaluator)
                        if state is not None:
                            state_name, state_value = state
                            active = _lookup_state_screen(
                                state_map,
                                unit.path,
                                function.class_name,
                                state_name,
                                state_value,
                            )
                            visit(statement.body, active or current)
                            visit(statement.orelse, current)
                            continue
                        target_info = _target_screen_assignment(
                            statement.body,
                            evaluator,
                            unit.path,
                            function.class_name,
                            state_map,
                        )
                        if current is not None and target_info is not None:
                            target, assignment, assignment_lhs = target_info
                            (
                                trigger,
                                trigger_lhs,
                                trigger_editable,
                                trigger_value,
                            ) = _trigger_info(statement.test, evaluator, unit.source)
                            key = (current.id, target.id, trigger)
                            if key not in seen:
                                connection = FlowConnection.create(
                                    current.id, target.id, trigger
                                )
                                connection.action = _action_names(statement.body)
                                connection.locked = not trigger_editable
                                connection.source_path = str(unit.path)
                                connection.source_line = statement.lineno
                                connection.source_trigger_segment = (
                                    ast.get_source_segment(unit.source, statement.test)
                                    or ""
                                )
                                connection.source_assignment_segment = (
                                    ast.get_source_segment(unit.source, assignment)
                                    or ""
                                )
                                connection.source_values = {
                                    "trigger": trigger,
                                    "trigger_value": trigger_value,
                                    "target_id": target.id,
                                    "trigger_lhs": trigger_lhs,
                                    "assignment_lhs": assignment_lhs,
                                }
                                connections.append(connection)
                                seen.add(key)
                        visit(statement.body, current)
                        visit(statement.orelse, current)

                visit(function.node.body, initial)
        return connections


def build_imported_app_patches(project: GuiProject) -> list[SourcePatch]:
    """Build narrow patches for edited source-backed GUI elements and flows."""
    replacements: dict[Path, list[_Replacement]] = {}
    for screen in project.screens:
        for element in screen.elements:
            if (
                element.locked
                or not element.source_path
                or not _element_changed(element)
            ):
                continue
            replacements.setdefault(Path(element.source_path), []).append(
                _Replacement(
                    element.source_segment,
                    _render_imported_element(element),
                    element.source_line,
                )
            )
    for connection in project.connections:
        if connection.locked or not connection.source_path:
            continue
        path = Path(connection.source_path)
        original_trigger = connection.source_values.get("trigger")
        if connection.trigger != original_trigger:
            lhs = str(connection.source_values.get("trigger_lhs", ""))
            if not lhs or not connection.source_trigger_segment:
                raise ValueError("Imported trigger has no editable source anchor")
            trigger_value = _coerce_trigger_value(
                connection.trigger,
                connection.source_values.get("trigger_value", original_trigger),
            )
            replacements.setdefault(path, []).append(
                _Replacement(
                    connection.source_trigger_segment,
                    f"{lhs} == {trigger_value!r}",
                    connection.source_line,
                )
            )
        original_target = connection.source_values.get("target_id")
        if connection.target_id != original_target:
            target = project.screen(connection.target_id)
            lhs = str(connection.source_values.get("assignment_lhs", ""))
            if (
                target is None
                or target.source_state is None
                or not lhs
                or not connection.source_assignment_segment
            ):
                raise ValueError("Selected target has no editable source state")
            replacements.setdefault(path, []).append(
                _Replacement(
                    connection.source_assignment_segment,
                    f"{lhs} = {target.source_state!r}",
                    connection.source_line,
                )
            )
    patches: list[SourcePatch] = []
    for path, items in sorted(replacements.items(), key=lambda item: str(item[0])):
        source = path.read_text(encoding="utf-8")
        expected = project.imported_sources.get(str(path))
        if not expected:
            raise ValueError(f"Missing imported source snapshot: {path}")
        if _source_digest(source) != expected:
            raise ValueError(f"Source changed after import: {path}")
        updated = _apply_replacements(source, items, path)
        ast.parse(updated, filename=str(path))
        diff = "".join(
            difflib.unified_diff(
                source.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
        if diff:
            patches.append(
                SourcePatch(
                    path,
                    source,
                    updated,
                    diff,
                    "imported-app",
                    len(items),
                )
            )
    return patches


def refresh_import_metadata(project: GuiProject, patches: list[SourcePatch]) -> None:
    """Refresh source snapshots after imported edits are applied."""
    updated_paths = {str(patch.path): patch.updated for patch in patches}
    for screen in project.screens:
        for element in screen.elements:
            if element.source_path not in updated_paths or element.locked:
                continue
            if _element_changed(element):
                call_type = element.source_values.get("call_type")
                extra_args = list(element.source_values.get("extra_args", []))
                element.source_segment = _render_imported_element(element)
                element.source_values = _element_snapshot(element, call_type)
                element.source_values["call_type"] = call_type
                element.source_values["extra_args"] = extra_args
    for connection in project.connections:
        if connection.source_path not in updated_paths or connection.locked:
            continue
        if connection.trigger != connection.source_values.get("trigger"):
            lhs = str(connection.source_values.get("trigger_lhs", ""))
            trigger_value = _coerce_trigger_value(
                connection.trigger,
                connection.source_values.get("trigger_value", connection.trigger),
            )
            connection.source_trigger_segment = f"{lhs} == {trigger_value!r}"
            connection.source_values["trigger_value"] = trigger_value
        if connection.target_id != connection.source_values.get("target_id"):
            target = project.screen(connection.target_id)
            lhs = str(connection.source_values.get("assignment_lhs", ""))
            if target is not None:
                connection.source_assignment_segment = (
                    f"{lhs} = {target.source_state!r}"
                )
        connection.source_values["trigger"] = connection.trigger
        connection.source_values["target_id"] = connection.target_id
    for path, source in updated_paths.items():
        project.imported_sources[path] = _source_digest(source)


def _functions(tree: ast.Module) -> list[_FunctionUnit]:
    """Return module functions and direct class methods."""
    result: list[_FunctionUnit] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append(_FunctionUnit(node, None))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result.append(_FunctionUnit(child, node.name))
    return result


def _drawing_function_names(functions: list[_FunctionUnit]) -> set[str]:
    """Return functions that draw directly or through drawing helpers."""
    names = {
        function.node.name
        for function in functions
        if _body_has_drawing(function.node.body)
    }
    changed = True
    while changed:
        changed = False
        for function in functions:
            if function.node.name in names:
                continue
            called = {
                call_name(child.func)
                for child in ast.walk(function.node)
                if isinstance(child, ast.Call)
            }
            if called & names:
                names.add(function.node.name)
                changed = True
    return names


def _called_screen_helper(
    statements: Iterable[ast.stmt],
    class_name: str | None,
    functions: dict[tuple[str, str], _FunctionUnit],
    drawing_names: set[str],
) -> _FunctionUnit | None:
    """Return a screen-like drawing helper called by one state branch."""
    for statement in statements:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Call):
                continue
            name = call_name(child.func)
            if name not in drawing_names:
                continue
            helper = functions.get((class_name or "", name)) or functions.get(
                ("", name)
            )
            if helper is not None:
                return helper
    return None


def _function_evaluator(
    unit: _SourceUnit, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> SafeEvaluator:
    """Build a safe evaluator for one function's preview defaults."""
    environment = dict(unit.constants)
    environment.update(function_parameters(node, unit.constants))
    return SafeEvaluator(environment, [])


def _required_parameter_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """Return parameters whose values depend on runtime callers."""
    arguments = list(node.args.posonlyargs) + list(node.args.args)
    if arguments and arguments[0].arg in {"self", "cls"}:
        arguments = arguments[1:]
    required_count = len(arguments) - len(node.args.defaults)
    return {argument.arg for argument in arguments[:required_count]}


def _body_has_drawing(
    statements: Iterable[ast.stmt], helper_names: set[str] | None = None
) -> bool:
    """Return whether statements contain supported drawing calls."""
    recognized = set(DRAW_SIGNATURES) | set(TEXT_CALLS) | (helper_names or set())
    return any(
        isinstance(child, ast.Call) and call_name(child.func) in recognized
        for statement in statements
        for child in ast.walk(statement)
    )


def _state_test(node: ast.AST, evaluator: SafeEvaluator) -> tuple[str, Any] | None:
    """Return a simple screen-state comparison name and value."""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    if not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1:
        return None
    name = _state_target_name(node.left)
    value_node = node.comparators[0]
    if name is None:
        name = _state_target_name(value_node)
        value_node = node.left
    if name is None:
        return None
    value = evaluator.evaluate(value_node)
    if isinstance(value, UnknownValue) or not isinstance(value, (str, int, bool)):
        return None
    return name, value


def _state_target_name(node: ast.AST) -> str | None:
    """Return a recognized screen-state target name."""
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    else:
        return None
    return name if any(word in name.lower() for word in STATE_WORDS) else None


def _state_assignment(
    node: ast.AST, evaluator: SafeEvaluator
) -> tuple[str, Any, ast.Assign | ast.AnnAssign, str] | None:
    """Return a simple state assignment and its target expression."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        value_node = node.value
    elif isinstance(node, ast.AnnAssign):
        target = node.target
        value_node = node.value
    else:
        return None
    name = _state_target_name(target)
    if name is None:
        return None
    value = evaluator.evaluate(value_node)
    if isinstance(value, UnknownValue) or not isinstance(value, (str, int, bool)):
        return None
    lhs = ast.unparse(target)
    return name, value, node, lhs


def _element_from_call(
    unit: _SourceUnit,
    call: ast.Call,
    evaluator: SafeEvaluator,
    dynamic_names: set[str],
    drawing_helpers: set[str],
    index: int,
    force_locked: bool,
) -> GuiElement | None:
    """Convert one recognized draw call into a source-backed element."""
    name = call_name(call.func)
    source_segment = ast.get_source_segment(unit.source, call) or ""
    source_call = ast.get_source_segment(unit.source, call.func) or name
    if name in DRAW_SIGNATURES:
        kind, signature = DRAW_SIGNATURES[name]
        values, static = _call_values(call, signature, evaluator, dynamic_names)
        element = _primitive_element(kind, values, index)
        if element is None:
            return None
        editable = kind in {"fill_rect", "rect"} and static
        element.locked = force_locked or not editable
        element.source_path = str(unit.path)
        element.source_line = call.lineno
        element.source_call = source_call
        element.source_segment = source_segment
        element.source_values = _element_snapshot(element, kind)
        element.source_values.update(
            {
                "call_type": kind,
                "extra_args": _extra_call_arguments(
                    call, len(signature), set(signature)
                ),
            }
        )
        return element
    if name in TEXT_CALLS:
        signature = ("x", "y", "text", "color")
        values, static = _call_values(call, signature, evaluator, dynamic_names)
        try:
            x = int(values["x"])
            y = int(values["y"])
            text = str(values["text"])
            color = int(values["color"]) & 0xFFFF
        except (KeyError, TypeError, ValueError):
            return _locked_helper_element(unit, call, index)
        element = GuiElement(
            new_identifier("element"),
            "label",
            f"text_line_{call.lineno}",
            x,
            y,
            max(20, min(320, len(text) * 8)),
            16,
            text,
            0x0000,
            color,
            color,
        )
        element.locked = force_locked or not static
        element.source_path = str(unit.path)
        element.source_line = call.lineno
        element.source_call = source_call
        element.source_segment = source_segment
        element.source_values = _element_snapshot(element, "text")
        element.source_values.update(
            {
                "call_type": "text",
                "extra_args": _extra_call_arguments(
                    call, len(signature), set(signature)
                ),
            }
        )
        return element
    if name in drawing_helpers or any(word in name.lower() for word in HELPER_WORDS):
        return _locked_helper_element(unit, call, index)
    return None


def _call_values(
    call: ast.Call,
    signature: tuple[str, ...],
    evaluator: SafeEvaluator,
    dynamic_names: set[str],
) -> tuple[dict[str, Any], bool]:
    """Return evaluated call fields and whether all are source-static."""
    fields: dict[str, Any] = {}
    static = True
    for field_name, argument in zip(signature, call.args):
        fields[field_name] = evaluator.evaluate(argument)
        static = static and _expression_is_static(argument, dynamic_names)
    for keyword in call.keywords:
        if keyword.arg in signature:
            fields[keyword.arg] = evaluator.evaluate(keyword.value)
            static = static and _expression_is_static(keyword.value, dynamic_names)
    if any(isinstance(value, UnknownValue) for value in fields.values()):
        static = False
    return fields, static


def _expression_is_static(node: ast.AST, dynamic_names: set[str]) -> bool:
    """Return whether an expression is independent of runtime state."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in dynamic_names:
            return False
        if isinstance(child, ast.Attribute):
            return False
        if isinstance(child, ast.Call):
            return False
    return True


def _primitive_element(
    kind: str, values: dict[str, Any], index: int
) -> GuiElement | None:
    """Create a visual element from evaluated primitive fields."""
    try:
        color = int(values.get("color", 0xFFFF)) & 0xFFFF
        if kind in {"fill_rect", "rect"}:
            x = int(values["x"])
            y = int(values["y"])
            width = max(1, int(values["width"]))
            height = max(1, int(values["height"]))
        elif kind == "line":
            x1, y1 = int(values["x1"]), int(values["y1"])
            x2, y2 = int(values["x2"]), int(values["y2"])
            x, y = min(x1, x2), min(y1, y2)
            width, height = abs(x2 - x1) + 1, abs(y2 - y1) + 1
        elif kind in {"circle", "fill_circle"}:
            radius = max(0, int(values["radius"]))
            x = int(values["x"]) - radius
            y = int(values["y"]) - radius
            width = height = radius * 2 + 1
        elif kind == "pixel":
            x, y, width, height = int(values["x"]), int(values["y"]), 1, 1
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    element_kind = "panel" if kind == "fill_rect" else "rectangle"
    return GuiElement(
        new_identifier("element"),
        element_kind,
        f"{kind}_{index}",
        x,
        y,
        width,
        height,
        "",
        color if kind in {"fill_rect", "fill_circle", "pixel"} else 0x0000,
        color,
        color,
    )


def _locked_helper_element(unit: _SourceUnit, call: ast.Call, index: int) -> GuiElement:
    """Create a locked placeholder for a dynamic helper call."""
    x = _literal_int(call.args[0]) if call.args else 16
    y = _literal_int(call.args[1]) if len(call.args) > 1 else 16 + index * 20
    name = call_name(call.func) or "code"
    element = GuiElement(
        new_identifier("element"),
        "icon",
        f"code_{name}_{call.lineno}",
        x,
        y,
        96,
        24,
        name,
        0x4208,
        0xFD20,
        0xFFFF,
        True,
        name,
        True,
        str(unit.path),
        call.lineno,
        ast.get_source_segment(unit.source, call.func) or name,
        ast.get_source_segment(unit.source, call) or "",
        {},
    )
    return element


def _literal_int(node: ast.AST) -> int:
    """Return a literal integer or a practical fallback."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return int(node.value)
    return 16


def _extra_call_arguments(call: ast.Call, used: int, used_names: set[str]) -> list[str]:
    """Return source text for preserved trailing call arguments."""
    extras = [ast.unparse(argument) for argument in call.args[used:]]
    extras.extend(
        f"{keyword.arg}={ast.unparse(keyword.value)}"
        for keyword in call.keywords
        if keyword.arg is not None and keyword.arg not in used_names
    )
    return extras


def _element_snapshot(
    element: GuiElement, call_type: str | None = None
) -> dict[str, Any]:
    """Return source-editable element values."""
    snapshot = {
        "x": element.x,
        "y": element.y,
        "visible": element.visible,
    }
    resolved_type = call_type or str(element.source_values.get("call_type", ""))
    if resolved_type == "text":
        snapshot.update({"text": element.text, "text_color": element.text_color})
    elif resolved_type in {"fill_rect", "rect"}:
        snapshot.update({"width": element.width, "height": element.height})
        if resolved_type == "fill_rect":
            snapshot["fill_color"] = element.fill_color
        else:
            snapshot["border_color"] = element.border_color
    return snapshot


def _element_changed(element: GuiElement) -> bool:
    """Return whether editable source-backed values changed."""
    snapshot = _element_snapshot(element)
    return any(snapshot[key] != element.source_values.get(key) for key in snapshot)


def _render_imported_element(element: GuiElement) -> str:
    """Render one edited source-backed drawing call."""
    call_type = element.source_values.get("call_type")
    extras = list(element.source_values.get("extra_args", []))
    if call_type == "fill_rect":
        arguments: list[Any] = [
            element.x,
            element.y,
            element.width,
            element.height,
            f"0x{element.fill_color:04X}",
        ]
    elif call_type == "rect":
        arguments = [
            element.x,
            element.y,
            element.width,
            element.height,
            f"0x{element.border_color:04X}",
        ]
    elif call_type == "text":
        arguments = [
            element.x,
            element.y,
            repr(element.text),
            f"0x{element.text_color:04X}",
        ]
    else:
        raise ValueError(f"Unsupported imported call: {call_type}")
    rendered = ", ".join(str(value) for value in arguments + extras)
    call = f"{element.source_call}({rendered})"
    return call if element.visible else f"if False: {call}"


def _apply_replacements(
    source: str, replacements: list[_Replacement], path: Path
) -> str:
    """Apply exact replacements from bottom to top in one source file."""
    located: list[tuple[int, int, str]] = []
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for replacement in replacements:
        if not replacement.segment:
            raise ValueError(f"Missing source anchor in {path}:{replacement.line}")
        approximate = offsets[min(max(0, replacement.line - 1), len(offsets) - 1)]
        start = source.find(
            replacement.segment,
            max(0, approximate - 200),
            min(len(source), approximate + len(replacement.segment) + 1000),
        )
        if start < 0 and source.count(replacement.segment) == 1:
            start = source.find(replacement.segment)
        if start < 0:
            raise ValueError(f"Source anchor changed in {path}:{replacement.line}")
        located.append((start, start + len(replacement.segment), replacement.updated))
    ordered = sorted(located)
    if any(current[0] < previous[1] for previous, current in zip(ordered, ordered[1:])):
        raise ValueError(f"Overlapping source edits detected in {path}")
    updated = source
    for start, end, replacement in sorted(located, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    return updated


def _state_screen_map(
    candidates: list[tuple[_ScreenCandidate, ScreenDesign]],
) -> dict[tuple[str, str, str, str], ScreenDesign]:
    """Map imported state values to screen objects."""
    return {
        (
            str(candidate.unit.path),
            candidate.function.class_name or "",
            candidate.state_name,
            repr(candidate.state_value),
        ): screen
        for candidate, screen in candidates
        if candidate.state_name
    }


def _lookup_state_screen(
    state_map: dict[tuple[str, str, str, str], ScreenDesign],
    path: Path,
    class_name: str | None,
    state_name: str,
    state_value: Any,
) -> ScreenDesign | None:
    """Find an exact or uniquely matching imported state screen."""
    exact = state_map.get((str(path), class_name or "", state_name, repr(state_value)))
    if exact is not None:
        return exact
    matches = [
        screen
        for (_, _, name, value), screen in state_map.items()
        if name == state_name and value == repr(state_value)
    ]
    unique = {screen.id: screen for screen in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _target_screen_assignment(
    statements: Iterable[ast.stmt],
    evaluator: SafeEvaluator,
    path: Path,
    class_name: str | None,
    state_map: dict[tuple[str, str, str, str], ScreenDesign],
) -> tuple[ScreenDesign, ast.Assign | ast.AnnAssign, str] | None:
    """Return the first target screen assignment in a branch."""
    for statement in statements:
        for child in ast.walk(statement):
            assignment = _state_assignment(child, evaluator)
            if assignment is None:
                continue
            name, value, node, lhs = assignment
            screen = _lookup_state_screen(state_map, path, class_name, name, value)
            if screen is not None:
                return screen, node, lhs
    return None


def _trigger_info(
    node: ast.AST, evaluator: SafeEvaluator, source: str
) -> tuple[str, str, bool, Any]:
    """Return trigger label, source expression, editability, and raw value."""
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
    ):
        value = evaluator.evaluate(node.comparators[0])
        if not isinstance(value, UnknownValue) and isinstance(value, (str, int, bool)):
            lhs = ast.get_source_segment(source, node.left) or ast.unparse(node.left)
            return str(value), lhs, True, value
    if isinstance(node, ast.Call):
        name = call_name(node.func)
        if node.args:
            value = evaluator.evaluate(node.args[0])
            if not isinstance(value, UnknownValue):
                return str(value), "", False, value
        return name or "event", "", False, None
    return ast.unparse(node), "", False, None


def _coerce_trigger_value(value: str, original: Any) -> str | int | bool:
    """Preserve the imported trigger value type after text editing."""
    if isinstance(original, bool):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
        raise ValueError(f"Trigger must remain boolean: {value}")
    if isinstance(original, int):
        try:
            return int(value, 0)
        except ValueError as error:
            raise ValueError(f"Trigger must remain an integer: {value}") from error
    return value


def _action_names(statements: Iterable[ast.stmt]) -> str:
    """Return non-drawing call names in a transition branch."""
    names: list[str] = []
    for statement in statements:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Call):
                continue
            name = call_name(child.func)
            if name and name not in DRAW_SIGNATURES and name not in TEXT_CALLS:
                names.append(name)
    return ", ".join(dict.fromkeys(names))


def _display_state_name(value: Any, state_name: str) -> str:
    """Return a readable screen name from one state value."""
    if isinstance(value, str):
        words = re.sub(r"[_-]+", " ", value).strip()
        return words.title() or state_name.title()
    return f"{state_name.title()} {value}"


def _display_function_name(function: _FunctionUnit, path: Path) -> str:
    """Return a readable screen name from a function name."""
    value = function.node.name.strip("_")
    value = re.sub(r"^(draw|render|show|paint)_?", "", value)
    value = re.sub(r"_?(screen|view|page)$", "", value)
    if not value or value in {"draw", "render", "show", "paint"}:
        value = function.class_name or path.stem
    return re.sub(r"[_-]+", " ", value).title()


def _unique_name(name: str, used: set[str]) -> str:
    """Return a screen name unique within one imported project."""
    if name not in used:
        return name
    index = 2
    while f"{name} {index}" in used:
        index += 1
    return f"{name} {index}"


def _source_digest(source: str) -> str:
    """Return a stable source change-detection digest."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _unique_strings(values: Iterable[str]) -> list[str]:
    """Return strings in order without duplicates."""
    return list(dict.fromkeys(values))
