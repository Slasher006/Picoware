"""Python graphics discovery and source patching."""

from __future__ import annotations

import ast
import difflib
import hashlib
import operator
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from .model import PixelArt, Primitive, rasterize_primitives


TFT_COLORS = {
    "TFT_WHITE": 0xFFFF,
    "TFT_BLACK": 0x0000,
    "TFT_BLUE": 0x001F,
    "TFT_CYAN": 0x07FF,
    "TFT_RED": 0xF800,
    "TFT_LIGHTGREY": 0xD69A,
    "TFT_DARKGREY": 0x7BEF,
    "TFT_GREEN": 0x07E0,
    "TFT_DARKCYAN": 0x03EF,
    "TFT_DARKGREEN": 0x03E0,
    "TFT_SKYBLUE": 0x867D,
    "TFT_VIOLET": 0x915C,
    "TFT_BROWN": 0x9A60,
    "TFT_TRANSPARENT": 0x0120,
    "TFT_YELLOW": 0xFFE0,
    "TFT_ORANGE": 0xFDA0,
    "TFT_PINK": 0xFE19,
    "TFT_MAGENTA": 0xF81F,
}

DRAW_SIGNATURES = {
    "_fill_rectangle": ("fill_rect", ("x", "y", "width", "height", "color")),
    "fill_rectangle": ("fill_rect", ("x", "y", "width", "height", "color")),
    "_rectangle": ("rect", ("x", "y", "width", "height", "color")),
    "rectangle": ("rect", ("x", "y", "width", "height", "color")),
    "_line": ("line", ("x1", "y1", "x2", "y2", "color")),
    "line": ("line", ("x1", "y1", "x2", "y2", "color")),
    "_circle": ("circle", ("x", "y", "radius", "color")),
    "circle": ("circle", ("x", "y", "radius", "color")),
    "_fill_circle": ("fill_circle", ("x", "y", "radius", "color")),
    "fill_circle": ("fill_circle", ("x", "y", "radius", "color")),
    "_pixel": ("pixel", ("x", "y", "color")),
    "pixel": ("pixel", ("x", "y", "color")),
    "draw_pixel": ("pixel", ("x", "y", "color")),
    "_fill": ("fill_rect", ("x", "y", "width", "height", "color")),
    "_rect": ("rect", ("x", "y", "width", "height", "color")),
}

TEXT_CALLS = {"_text", "text", "draw_text", "fill_screen", "image_bytearray_path"}
GRAPHICS_WORDS = (
    "draw",
    "render",
    "icon",
    "tile",
    "sprite",
    "symbol",
    "ship",
    "enemy",
    "player",
    "brick",
    "wall",
    "floor",
    "screen",
    "frame",
)
MARKER_PATTERN = re.compile(
    r"^(?P<indent>\s*)# Pixel overlay (?P<key>[0-9a-f]{10}) (?P<edge>begin|end)\s*$"
)


class UnknownValue:
    """Represent an unresolved safe expression value."""

    def __bool__(self) -> bool:
        """Treat unresolved conditions as false."""
        return False

    def __int__(self) -> int:
        """Convert unresolved numbers to zero."""
        return 0

    def __index__(self) -> int:
        """Convert unresolved indexes to zero."""
        return 0

    def __getattr__(self, name: str) -> UnknownValue:
        """Return an unresolved child attribute."""
        return self

    def __getitem__(self, key: object) -> UnknownValue:
        """Return an unresolved indexed value."""
        return self

    def __iter__(self) -> Iterator[object]:
        """Iterate as an empty unresolved sequence."""
        return iter(())


UNKNOWN = UnknownValue()


@dataclass
class ObjectValues:
    """Provide safe attribute values without running project code."""

    values: dict[str, Any]

    def value(self, name: str) -> Any:
        """Return a configured attribute or an unknown value."""
        return self.values.get(name, UNKNOWN)


@dataclass
class FunctionRecord:
    """Describe one parsed Python function."""

    name: str
    qualified_name: str
    class_name: str | None
    node: ast.FunctionDef | ast.AsyncFunctionDef
    calls: set[str]
    direct_draw_count: int


@dataclass
class SourceAnchor:
    """Describe a source body suitable for an overlay block."""

    start_line: int
    end_line: int
    insert_line: int
    indent: str


@dataclass
class SourceDocument:
    """Hold one parsed source file and its graphics metadata."""

    path: Path
    source: str
    tree: ast.Module
    functions: dict[str, FunctionRecord]
    constants: dict[str, Any]
    generated_ranges: list[tuple[int, int, str]]


@dataclass
class GraphicsAsset:
    """Describe one graphics-producing Python function."""

    document: SourceDocument
    record: FunctionRecord
    category: str
    parameters: dict[str, Any]
    variants: dict[str, list[Any]]
    origin_x: str | None
    origin_y: str | None
    warnings: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Return a concise catalogue label."""
        return self.record.qualified_name

    @property
    def file_label(self) -> str:
        """Return the source filename."""
        return self.document.path.name


@dataclass
class TraceResult:
    """Hold traced primitives, surfaces, and patch location."""

    primitives: list[Primitive]
    base_art: PixelArt
    current_art: PixelArt
    anchor: SourceAnchor
    warnings: list[str]


@dataclass
class SourcePatch:
    """Hold a pending source-file modification."""

    path: Path
    original: str
    updated: str
    diff: str
    key: str
    run_count: int

    def apply(self, backup_root: Path) -> Path:
        """Back up the source and atomically apply the patch."""
        ast.parse(self.updated, filename=str(self.path))
        backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_root / f"{self.path.name}.{timestamp}.bak"
        shutil.copy2(self.path, backup_path)
        file_handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(file_handle, "w", encoding="utf-8") as temporary:
                temporary.write(self.updated)
            Path(temporary_name).replace(self.path)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        return backup_path


class SafeEvaluator:
    """Evaluate a small expression subset without executing source code."""

    def __init__(self, environment: dict[str, Any], warnings: list[str]):
        """Initialize evaluation values and warning collection."""
        self.environment = environment
        self.warnings = warnings

    def evaluate(self, node: ast.AST | None) -> Any:
        """Evaluate one supported syntax node safely."""
        if node is None:
            return None
        try:
            return self._evaluate(node)
        except Exception:
            self._warn(node, "Unsupported expression")
            return UNKNOWN

    def _evaluate(self, node: ast.AST) -> Any:
        """Dispatch one safe expression node."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.environment.get(node.id, UNKNOWN)
        if isinstance(node, ast.Tuple):
            return tuple(self.evaluate(item) for item in node.elts)
        if isinstance(node, ast.List):
            return [self.evaluate(item) for item in node.elts]
        if isinstance(node, ast.Dict):
            return {
                self.evaluate(key): self.evaluate(value)
                for key, value in zip(node.keys, node.values)
            }
        if isinstance(node, ast.Attribute):
            value = self.evaluate(node.value)
            if isinstance(value, ObjectValues):
                return value.value(node.attr)
            if isinstance(value, dict):
                return value.get(node.attr, UNKNOWN)
            return UNKNOWN
        if isinstance(node, ast.Subscript):
            value = self.evaluate(node.value)
            index = self.evaluate(node.slice)
            try:
                return value[index]
            except Exception:
                return UNKNOWN
        if isinstance(node, ast.UnaryOp):
            value = self.evaluate(node.operand)
            operations = {
                ast.UAdd: operator.pos,
                ast.USub: operator.neg,
                ast.Not: operator.not_,
                ast.Invert: operator.invert,
            }
            return operations[type(node.op)](value)
        if isinstance(node, ast.BinOp):
            left = self.evaluate(node.left)
            right = self.evaluate(node.right)
            operations = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv,
                ast.Mod: operator.mod,
                ast.Pow: operator.pow,
                ast.BitOr: operator.or_,
                ast.BitAnd: operator.and_,
                ast.BitXor: operator.xor,
                ast.LShift: operator.lshift,
                ast.RShift: operator.rshift,
            }
            return operations[type(node.op)](left, right)
        if isinstance(node, ast.BoolOp):
            values = [self.evaluate(value) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self.evaluate(node.left)
            for operation_node, comparator in zip(node.ops, node.comparators):
                right = self.evaluate(comparator)
                operations = {
                    ast.Eq: operator.eq,
                    ast.NotEq: operator.ne,
                    ast.Lt: operator.lt,
                    ast.LtE: operator.le,
                    ast.Gt: operator.gt,
                    ast.GtE: operator.ge,
                    ast.In: lambda first, second: first in second,
                    ast.NotIn: lambda first, second: first not in second,
                    ast.Is: operator.is_,
                    ast.IsNot: operator.is_not,
                }
                if not operations[type(operation_node)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self.evaluate(node.test) else node.orelse
            return self.evaluate(branch)
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue):
                    parts.append(str(self.evaluate(value.value)))
            return "".join(parts)
        if isinstance(node, ast.Call):
            return self._evaluate_call(node)
        if isinstance(node, ast.Slice):
            return slice(
                self.evaluate(node.lower),
                self.evaluate(node.upper),
                self.evaluate(node.step),
            )
        self._warn(node, "Unsupported expression")
        return UNKNOWN

    def _evaluate_call(self, node: ast.Call) -> Any:
        """Evaluate one allowlisted function call."""
        name = call_name(node.func)
        arguments = [self.evaluate(argument) for argument in node.args]
        keywords = {
            keyword.arg: self.evaluate(keyword.value)
            for keyword in node.keywords
            if keyword.arg
        }
        allowed = {
            "abs": abs,
            "bool": bool,
            "const": lambda value: value,
            "float": float,
            "int": int,
            "len": len,
            "max": max,
            "min": min,
            "range": range,
            "round": round,
            "str": str,
            "tuple": tuple,
        }
        if name == "getattr" and len(arguments) >= 2:
            value, attribute = arguments[:2]
            default = arguments[2] if len(arguments) > 2 else UNKNOWN
            if isinstance(value, ObjectValues):
                return value.values.get(str(attribute), default)
            return default
        function = allowed.get(name)
        if function is None:
            return UNKNOWN
        try:
            return function(*arguments, **keywords)
        except Exception:
            return UNKNOWN

    def _warn(self, node: ast.AST, message: str) -> None:
        """Add one unique evaluation warning."""
        warning = f"Line {getattr(node, 'lineno', '?')}: {message}"
        if warning not in self.warnings:
            self.warnings.append(warning)


class SourceScanner:
    """Discover graphics-producing functions in Python source."""

    def scan_file(self, path: str | Path) -> list[GraphicsAsset]:
        """Parse one Python file and return detected assets."""
        source_path = Path(path).resolve()
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        functions = collect_functions(tree)
        graphics_names = discover_graphics(functions)
        document = SourceDocument(
            source_path,
            source,
            tree,
            functions,
            collect_constants(source_path, tree),
            find_generated_ranges(source),
        )
        assets: list[GraphicsAsset] = []
        for qualified_name in sorted(
            graphics_names, key=lambda name: functions[name].node.lineno
        ):
            record = functions[qualified_name]
            if record.name in DRAW_SIGNATURES:
                continue
            parameters = function_parameters(record.node, document.constants)
            variants = discover_variants(record.node, parameters, document.constants)
            origin_x, origin_y = infer_origins(parameters)
            assets.append(
                GraphicsAsset(
                    document,
                    record,
                    classify_asset(record.name),
                    parameters,
                    variants,
                    origin_x,
                    origin_y,
                )
            )
        return assets

    def scan_folder(self, path: str | Path, limit: int = 2000) -> list[GraphicsAsset]:
        """Recursively scan Python files below one folder."""
        root = Path(path).resolve()
        assets: list[GraphicsAsset] = []
        count = 0
        for source_path in sorted(root.rglob("*.py")):
            if any(
                part.startswith(".") or part in {"__pycache__", "venv", ".venv"}
                for part in source_path.parts
            ):
                continue
            count += 1
            if count > limit:
                break
            try:
                assets.extend(self.scan_file(source_path))
            except (OSError, SyntaxError, UnicodeError):
                continue
        return assets


class TraceInterpreter:
    """Trace supported drawing calls into editable primitives."""

    def __init__(
        self,
        max_steps: int = 5000,
        max_primitives: int = 2500,
        max_loop_items: int = 128,
    ):
        """Configure strict tracing resource limits."""
        self.max_steps = max(100, max_steps)
        self.max_primitives = max(50, max_primitives)
        self.max_loop_items = max(8, max_loop_items)

    def render(
        self,
        asset: GraphicsAsset,
        variant_values: dict[str, Any] | None = None,
    ) -> TraceResult:
        """Render one asset with selected variant values."""
        variants = dict(asset.parameters)
        variants.update(variant_values or {})
        warnings: list[str] = []
        primitives: list[Primitive] = []
        anchors: list[SourceAnchor] = []
        self._steps = 0
        self._warnings = warnings
        self._primitives = primitives
        self._asset = asset
        self._variant_names = set(asset.variants)
        self._call_stack = {asset.record.qualified_name}
        self._environment = dict(asset.document.constants)
        self._environment.update(variants)
        self._environment["self"] = ObjectValues(
            {
                "width": 320,
                "height": 320,
                "cell": 22,
                "phase": variants.get("phase", 0),
                "origin_x": 0,
                "origin_y": 0,
                "draw": UNKNOWN,
            }
        )
        self._evaluator = SafeEvaluator(self._environment, warnings)
        function_anchor = make_anchor(asset.record.node, asset.document.source)
        anchors.append(function_anchor)
        self._execute_body(asset.record.node.body, function_anchor, anchors, 0)
        base_primitives = [
            primitive for primitive in primitives if not primitive.generated
        ]
        base_art = rasterize_primitives(base_primitives)
        current_art = rasterize_aligned(primitives, base_art)
        anchor = anchors[-1] if anchors else function_anchor
        return TraceResult(primitives, base_art, current_art, anchor, warnings)

    def _execute_body(
        self,
        statements: Iterable[ast.stmt],
        anchor: SourceAnchor,
        anchors: list[SourceAnchor],
        depth: int,
    ) -> tuple[bool, Any]:
        """Execute one supported statement body."""
        if depth > 16:
            self._warn(anchor.start_line, "Trace recursion limit reached")
            return False, None
        for statement in statements:
            self._steps += 1
            if self._steps > self.max_steps:
                self._warn(getattr(statement, "lineno", 0), "Trace step limit reached")
                return True, None
            if isinstance(statement, ast.Expr):
                if isinstance(statement.value, ast.Constant) and isinstance(
                    statement.value.value, str
                ):
                    continue
                if isinstance(statement.value, ast.Call):
                    self._execute_call(statement.value, anchor, anchors, depth)
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                target = (
                    statement.targets[0]
                    if isinstance(statement, ast.Assign)
                    else statement.target
                )
                value = self._evaluator.evaluate(statement.value)
                self._assign(target, value)
                continue
            if isinstance(statement, ast.AugAssign):
                current = self._evaluator.evaluate(statement.target)
                value = self._evaluator.evaluate(statement.value)
                expression = ast.BinOp(
                    ast.Constant(current), statement.op, ast.Constant(value)
                )
                self._assign(statement.target, self._evaluator.evaluate(expression))
                continue
            if isinstance(statement, ast.If):
                chosen = (
                    statement.body
                    if self._evaluator.evaluate(statement.test)
                    else statement.orelse
                )
                child_anchor = anchor
                if expression_names(statement.test) & self._variant_names and chosen:
                    child_anchor = make_body_anchor(chosen, self._asset.document.source)
                    anchors.append(child_anchor)
                stopped, value = self._execute_body(
                    chosen, child_anchor, anchors, depth + 1
                )
                if stopped:
                    return True, value
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                values = self._evaluator.evaluate(statement.iter)
                try:
                    items = list(values)[: self.max_loop_items]
                except Exception:
                    items = []
                for item in items:
                    self._assign(statement.target, item)
                    stopped, value = self._execute_body(
                        statement.body, anchor, anchors, depth + 1
                    )
                    if stopped:
                        return True, value
                continue
            if isinstance(statement, ast.Return):
                return True, self._evaluator.evaluate(statement.value)
            if isinstance(statement, ast.Try):
                stopped, value = self._execute_body(
                    statement.body, anchor, anchors, depth + 1
                )
                if stopped:
                    return True, value
                continue
            if isinstance(statement, (ast.Pass, ast.Break, ast.Continue)):
                continue
            self._warn(getattr(statement, "lineno", 0), "Unsupported statement skipped")
        return False, None

    def _execute_call(
        self,
        node: ast.Call,
        anchor: SourceAnchor,
        anchors: list[SourceAnchor],
        depth: int,
    ) -> None:
        """Trace one drawing or helper call."""
        name = call_name(node.func)
        if name in DRAW_SIGNATURES:
            self._append_primitive(node, name)
            return
        if name in TEXT_CALLS:
            return
        record = matching_function(
            self._asset.document.functions, self._asset.record.class_name, name
        )
        if record is None or record.qualified_name == self._asset.record.qualified_name:
            return
        if record.qualified_name in self._call_stack:
            self._warn(getattr(node, "lineno", 0), "Recursive graphics helper skipped")
            return
        values = [self._evaluator.evaluate(argument) for argument in node.args]
        keyword_values = {
            keyword.arg: self._evaluator.evaluate(keyword.value)
            for keyword in node.keywords
            if keyword.arg
        }
        previous = dict(self._environment)
        bound = bind_parameters(
            record.node, values, keyword_values, self._asset.document.constants
        )
        self._environment.update(bound)
        helper_anchor = make_anchor(record.node, self._asset.document.source)
        self._call_stack.add(record.qualified_name)
        try:
            self._execute_body(record.node.body, helper_anchor, anchors, depth + 1)
        finally:
            self._call_stack.remove(record.qualified_name)
        self._environment.clear()
        self._environment.update(previous)

    def _append_primitive(self, node: ast.Call, name: str) -> None:
        """Convert one supported draw call into a primitive."""
        if len(self._primitives) >= self.max_primitives:
            self._warn(getattr(node, "lineno", 0), "Primitive limit reached")
            return
        kind, signature = DRAW_SIGNATURES[name]
        values: dict[str, Any] = {}
        for field_name, argument in zip(signature, node.args):
            values[field_name] = self._evaluator.evaluate(argument)
        for keyword in node.keywords:
            if keyword.arg:
                values[keyword.arg] = self._evaluator.evaluate(keyword.value)
        if name == "_circle" and bool(values.get("fill", False)):
            kind = "fill_circle"
        try:
            color = int(values.get("color", 0xFFFF)) & 0xFFFF
            coordinates = tuple(
                int(values[field_name])
                for field_name in signature
                if field_name != "color"
            )
        except Exception:
            self._warn(getattr(node, "lineno", 0), "Unresolved draw arguments")
            return
        if not primitive_is_bounded(kind, coordinates):
            self._warn(getattr(node, "lineno", 0), "Oversized draw call skipped")
            return
        line = getattr(node, "lineno", 0)
        generated = line_in_ranges(line, self._asset.document.generated_ranges)
        self._primitives.append(Primitive(kind, coordinates, color, line, generated))

    def _assign(self, target: ast.AST, value: Any) -> None:
        """Assign a safe value into the trace environment."""
        if isinstance(target, ast.Name):
            self._environment[target.id] = value
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            try:
                items = list(value)
            except Exception:
                items = [UNKNOWN] * len(target.elts)
            for child, item in zip(target.elts, items):
                self._assign(child, item)

    def _warn(self, line: int, message: str) -> None:
        """Add one unique trace warning."""
        warning = f"Line {line}: {message}"
        if warning not in self._warnings:
            self._warnings.append(warning)


class SourceExporter:
    """Generate compact source overlays from edited pixels."""

    def build_patch(
        self,
        asset: GraphicsAsset,
        trace: TraceResult,
        edited: PixelArt,
        variant_values: dict[str, Any] | None = None,
    ) -> SourcePatch:
        """Create a source patch without writing the file."""
        changed = edited.changed_pixels(trace.base_art)
        if any(color is None for _, _, color in changed):
            raise ValueError("Transparent erasing cannot be written into draw calls")
        runs = edited.horizontal_runs(trace.base_art)
        variants = variant_values or {}
        identity = asset.record.qualified_name + repr(sorted(variants.items()))
        key = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
        updated = replace_overlay(asset, trace.anchor, edited, runs, key, variants)
        diff = "".join(
            difflib.unified_diff(
                asset.document.source.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=str(asset.document.path),
                tofile=str(asset.document.path),
            )
        )
        return SourcePatch(
            asset.document.path,
            asset.document.source,
            updated,
            diff,
            key,
            len(runs),
        )


def call_name(node: ast.AST) -> str:
    """Return the final identifier from a call target."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def collect_functions(tree: ast.Module) -> dict[str, FunctionRecord]:
    """Collect module functions and class methods."""
    records: dict[str, FunctionRecord] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = collect_call_names(node)
            records[node.name] = FunctionRecord(
                node.name,
                node.name,
                None,
                node,
                calls,
                sum(
                    1
                    for child in ast.walk(node)
                    if isinstance(child, ast.Call)
                    and call_name(child.func) in DRAW_SIGNATURES
                ),
            )
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                qualified = f"{node.name}.{child.name}"
                calls = collect_call_names(child)
                records[qualified] = FunctionRecord(
                    child.name,
                    qualified,
                    node.name,
                    child,
                    calls,
                    sum(
                        1
                        for item in ast.walk(child)
                        if isinstance(item, ast.Call)
                        and call_name(item.func) in DRAW_SIGNATURES
                    ),
                )
    return records


def collect_call_names(node: ast.AST) -> set[str]:
    """Return called identifier names below one node."""
    return {
        call_name(child.func) for child in ast.walk(node) if isinstance(child, ast.Call)
    }


def discover_graphics(functions: dict[str, FunctionRecord]) -> set[str]:
    """Find functions that draw directly or through helpers."""
    graphics = {
        qualified
        for qualified, record in functions.items()
        if record.direct_draw_count > 0
    }
    changed = True
    while changed:
        changed = False
        graphics_names = {functions[name].name for name in graphics}
        for qualified, record in functions.items():
            if qualified in graphics:
                continue
            if record.calls & graphics_names and any(
                word in record.name.lower() for word in GRAPHICS_WORDS
            ):
                graphics.add(qualified)
                changed = True
    return graphics


def matching_function(
    functions: dict[str, FunctionRecord],
    class_name: str | None,
    name: str,
) -> FunctionRecord | None:
    """Return the nearest function matching one called name."""
    qualified = f"{class_name}.{name}" if class_name else name
    if qualified in functions:
        return functions[qualified]
    return functions.get(name)


def classify_asset(name: str) -> str:
    """Classify a discovered asset from its function name."""
    lowered = name.lower()
    for category in (
        "tile",
        "icon",
        "sprite",
        "symbol",
        "enemy",
        "player",
        "ship",
        "screen",
        "animation",
    ):
        if category in lowered:
            return category.title()
    if any(word in lowered for word in ("floor", "brick", "wall", "solid")):
        return "Tile"
    if any(
        word in lowered for word in ("frame", "title", "menu", "map", "port", "harbor")
    ):
        return "Screen"
    return "Graphic"


def function_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef, constants: dict[str, Any]
) -> dict[str, Any]:
    """Return safe defaults for one function signature."""
    arguments = list(node.args.posonlyargs) + list(node.args.args)
    if arguments and arguments[0].arg in {"self", "cls"}:
        arguments = arguments[1:]
    defaults: dict[str, Any] = {}
    default_offset = len(arguments) - len(node.args.defaults)
    evaluator = SafeEvaluator(dict(constants), [])
    for index, argument in enumerate(arguments):
        if index >= default_offset:
            value = evaluator.evaluate(node.args.defaults[index - default_offset])
            defaults[argument.arg] = 0 if isinstance(value, UnknownValue) else value
        else:
            defaults[argument.arg] = heuristic_default(argument.arg)
    return defaults


def heuristic_default(name: str) -> Any:
    """Return a practical preview value for a required parameter."""
    lowered = name.lower()
    if lowered in {
        "selected",
        "filled",
        "elite",
        "small",
        "active",
    } or lowered.startswith("is_"):
        return False
    if lowered == "scale":
        return 1
    if lowered in {
        "game",
        "draw",
        "storage",
        "entity",
        "enemy",
        "player",
        "effect",
        "powerup",
    }:
        return UNKNOWN
    return 0


def discover_variants(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parameters: dict[str, Any],
    constants: dict[str, Any],
) -> dict[str, list[Any]]:
    """Infer editable variants from simple conditions."""
    variants: dict[str, set[Any]] = {}
    evaluator = SafeEvaluator(dict(constants), [])
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Compare)
            and isinstance(child.left, ast.Name)
            and child.left.id in parameters
        ):
            for comparator in child.comparators:
                value = evaluator.evaluate(comparator)
                if not isinstance(value, UnknownValue) and isinstance(
                    value, (bool, int, str)
                ):
                    variants.setdefault(child.left.id, set()).add(value)
        elif (
            isinstance(child, ast.If)
            and isinstance(child.test, ast.Name)
            and child.test.id in parameters
        ):
            variants.setdefault(child.test.id, set()).update({False, True})
        elif (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "self"
            and child.attr == "phase"
        ):
            variants.setdefault("phase", set()).update(range(8))
    for name, value in parameters.items():
        if isinstance(value, bool):
            variants.setdefault(name, set()).update({False, True})
        elif name in {"frame", "phase"}:
            variants.setdefault(name, set()).update(range(8))
        elif name == "animation_time":
            variants.setdefault(name, set()).update((0, 200, 400, 600))
    result: dict[str, list[Any]] = {}
    for name, values in variants.items():
        integer_values = [value for value in values if type(value) is int]
        if (
            name not in {"frame", "phase", "animation_time"}
            and integer_values
            and min(integer_values) == 0
            and max(integer_values) <= 16
        ):
            values.update(range(max(integer_values) + 2))
        values.add(parameters.get(name, 0))
        result[name] = sorted(values, key=lambda value: (str(type(value)), value))
    return result


def infer_origins(parameters: dict[str, Any]) -> tuple[str | None, str | None]:
    """Infer horizontal and vertical origin parameters."""
    names = list(parameters)
    horizontal = next(
        (name for name in names if name in {"px", "draw_x", "origin_x", "left", "x"}),
        None,
    )
    vertical = next(
        (name for name in names if name in {"py", "draw_y", "origin_y", "top", "y"}),
        None,
    )
    return horizontal, vertical


def collect_constants(path: Path, tree: ast.Module) -> dict[str, Any]:
    """Collect safe module and sibling-module constants."""
    constants: dict[str, Any] = dict(TFT_COLORS)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.level:
            module_path = path.parent / (node.module.replace(".", "/") + ".py")
            if module_path.exists():
                constants.update(read_constant_file(module_path, constants))
    constants.update(evaluate_assignments(tree.body, constants))
    return constants


def read_constant_file(path: Path, seed: dict[str, Any]) -> dict[str, Any]:
    """Read constants from one local dependency file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return {}
    return evaluate_assignments(tree.body, seed)


def evaluate_assignments(
    statements: Iterable[ast.stmt], seed: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate simple module-level assignments."""
    environment = dict(seed)
    discovered: dict[str, Any] = {}
    for _ in range(4):
        progress = False
        evaluator = SafeEvaluator(environment, [])
        for statement in statements:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            value = evaluator.evaluate(statement.value)
            if isinstance(value, UnknownValue):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if environment.get(target.id) != value:
                        environment[target.id] = value
                        discovered[target.id] = value
                        progress = True
        if not progress:
            break
    return discovered


def expression_names(node: ast.AST) -> set[str]:
    """Return identifiers referenced by one expression."""
    names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
    names.update(
        child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
    )
    return names


def bind_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    positional: list[Any],
    keywords: dict[str, Any],
    constants: dict[str, Any],
) -> dict[str, Any]:
    """Bind safe call values to a helper signature."""
    defaults = function_parameters(node, constants)
    names = list(defaults)
    for name, value in zip(names, positional):
        defaults[name] = value
    defaults.update(
        {name: value for name, value in keywords.items() if name in defaults}
    )
    return defaults


def make_anchor(
    node: ast.FunctionDef | ast.AsyncFunctionDef, source: str
) -> SourceAnchor:
    """Create an insertion anchor for a function body."""
    return make_body_anchor(
        node.body,
        source,
        node.col_offset + 4,
        node.lineno,
        node.end_lineno or node.lineno,
    )


def make_body_anchor(
    body: list[ast.stmt],
    source: str,
    fallback_indent: int | None = None,
    fallback_start: int | None = None,
    fallback_end: int | None = None,
) -> SourceAnchor:
    """Create an insertion anchor for a statement body."""
    lines = source.splitlines()
    if body:
        start_line = body[0].lineno
        end_line = body[-1].end_lineno or body[-1].lineno
        indent_count = body[0].col_offset
        insert_line = end_line
        if isinstance(body[-1], ast.Return):
            insert_line = body[-1].lineno - 1
    else:
        start_line = fallback_start or 1
        end_line = fallback_end or start_line
        indent_count = fallback_indent or 4
        insert_line = start_line
    indent = " " * indent_count
    if 0 < start_line <= len(lines):
        match = re.match(r"\s*", lines[start_line - 1])
        indent = match.group(0) if match else indent
    return SourceAnchor(start_line, end_line, insert_line, indent)


def find_generated_ranges(source: str) -> list[tuple[int, int, str]]:
    """Locate generated pixel overlay blocks."""
    open_markers: dict[str, int] = {}
    ranges: list[tuple[int, int, str]] = []
    for line_number, line in enumerate(source.splitlines(), 1):
        match = MARKER_PATTERN.match(line)
        if not match:
            continue
        key = match.group("key")
        if match.group("edge") == "begin":
            open_markers[key] = line_number
        elif key in open_markers:
            ranges.append((open_markers.pop(key), line_number, key))
    return ranges


def line_in_ranges(line: int, ranges: Iterable[tuple[int, int, str]]) -> bool:
    """Return whether a source line is generated."""
    return any(start <= line <= end for start, end, _ in ranges)


def rasterize_aligned(primitives: list[Primitive], baseline: PixelArt) -> PixelArt:
    """Rasterize primitives onto baseline coordinates."""
    art = PixelArt(
        baseline.width,
        baseline.height,
        baseline.origin_x,
        baseline.origin_y,
    )
    for primitive in primitives:
        x_offset = baseline.origin_x
        y_offset = baseline.origin_y
        if primitive.kind == "fill_rect":
            x, y, width, height = primitive.values
            art.draw_rectangle(
                x - x_offset, y - y_offset, width, height, primitive.color, True
            )
        elif primitive.kind == "rect":
            x, y, width, height = primitive.values
            art.draw_rectangle(
                x - x_offset, y - y_offset, width, height, primitive.color, False
            )
        elif primitive.kind == "line":
            x1, y1, x2, y2 = primitive.values
            art.draw_line(
                x1 - x_offset,
                y1 - y_offset,
                x2 - x_offset,
                y2 - y_offset,
                primitive.color,
            )
        elif primitive.kind in {"circle", "fill_circle"}:
            center_x, center_y, radius = primitive.values
            art.draw_circle(
                center_x - x_offset,
                center_y - y_offset,
                radius,
                primitive.color,
                primitive.kind == "fill_circle",
            )
        elif primitive.kind == "pixel":
            x, y = primitive.values
            art.set_pixel(x - x_offset, y - y_offset, primitive.color)
    return art


def primitive_is_bounded(kind: str, values: tuple[int, ...]) -> bool:
    """Return whether a primitive is safe to rasterize."""
    if any(abs(value) > 8192 for value in values):
        return False
    if kind in {"fill_rect", "rect"}:
        return len(values) == 4 and abs(values[2]) <= 1024 and abs(values[3]) <= 1024
    if kind in {"circle", "fill_circle"}:
        return len(values) == 3 and abs(values[2]) <= 512
    if kind == "line":
        return (
            len(values) == 4
            and abs(values[2] - values[0]) <= 4096
            and abs(values[3] - values[1]) <= 4096
        )
    return True


def replace_overlay(
    asset: GraphicsAsset,
    anchor: SourceAnchor,
    edited: PixelArt,
    runs: list[tuple[int, int, int, int]],
    key: str,
    variant_values: dict[str, Any],
) -> str:
    """Insert or replace one generated overlay block."""
    lines = asset.document.source.splitlines(keepends=True)
    existing = next(
        (item for item in asset.document.generated_ranges if item[2] == key), None
    )
    indent = anchor.indent
    block = build_overlay_lines(asset, edited, runs, key, indent, variant_values)
    if existing:
        start, end, _ = existing
        lines[start - 1 : end] = block
    elif block:
        lines[anchor.insert_line : anchor.insert_line] = block
    return "".join(lines)


def build_overlay_lines(
    asset: GraphicsAsset,
    edited: PixelArt,
    runs: list[tuple[int, int, int, int]],
    key: str,
    indent: str,
    variant_values: dict[str, Any],
) -> list[str]:
    """Build one compact generated overlay block."""
    if not runs:
        return []
    lines = [f"{indent}# Pixel overlay {key} begin\n"]
    conditions: list[str] = []
    for name, value in sorted(variant_values.items()):
        if isinstance(value, UnknownValue):
            continue
        expression = (
            "self.phase" if name == "phase" and name not in asset.parameters else name
        )
        conditions.append(f"{expression} == {value!r}")
    call_indent = indent
    if conditions:
        lines.append(f"{indent}if {' and '.join(conditions)}:\n")
        call_indent += "\t" if "\t" in indent else "    "
    use_wrapper = any(
        record.class_name == asset.record.class_name and record.name == "_fill"
        for record in asset.document.functions.values()
    )
    for x, y, width, color in runs:
        actual_x = edited.origin_x + x
        actual_y = edited.origin_y + y
        x_expression = offset_expression(asset.origin_x, actual_x)
        y_expression = offset_expression(asset.origin_y, actual_y)
        if use_wrapper:
            call = (
                f"self._fill({x_expression}, {y_expression}, {width}, 1, 0x{color:04X})"
            )
        elif asset.record.class_name:
            call = f"self.draw._fill_rectangle({x_expression}, {y_expression}, {width}, 1, 0x{color:04X})"
        else:
            call = f"draw._fill_rectangle({x_expression}, {y_expression}, {width}, 1, 0x{color:04X})"
        lines.append(f"{call_indent}{call}\n")
    lines.append(f"{indent}# Pixel overlay {key} end\n")
    return lines


def offset_expression(origin: str | None, offset: int) -> str:
    """Format one source coordinate expression."""
    if origin is None:
        return str(offset)
    if offset == 0:
        return origin
    operation = "+" if offset > 0 else "-"
    return f"{origin} {operation} {abs(offset)}"
