"""Metadata for Picoware-native widgets supported by App GUI projects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeWidgetSpec:
    """Describe one native Picoware widget without importing device modules."""

    id: str
    name: str
    summary: str
    module: str
    class_name: str
    item_based: bool = False
    stateful: bool = False
    interactive: bool = True
    emits_activation: bool = True
    full_screen: bool = True
    uses_text: bool = False
    uses_text_color: bool = True
    uses_fill_color: bool = True
    uses_border_color: bool = True
    supports_initial_selection: bool = False
    supports_boolean_state: bool = False
    supports_item_states: bool = False
    default_width: int = 300
    default_height: int = 42
    default_text: str = ""
    default_items: tuple[str, ...] = ()


NATIVE_WIDGET_SPECS: tuple[NativeWidgetSpec, ...] = (
    NativeWidgetSpec(
        "menu",
        "Menu",
        "A titled, keyboard-selectable Picoware menu.",
        "picoware.gui.menu",
        "Menu",
        item_based=True,
        uses_text=True,
        supports_initial_selection=True,
        default_text="Menu",
        default_items=("First item", "Second item", "Third item"),
    ),
    NativeWidgetSpec(
        "list",
        "Selectable List",
        "A native scrolling list without a separate title.",
        "picoware.gui.list",
        "List",
        item_based=True,
        supports_initial_selection=True,
        default_items=("First item", "Second item", "Third item"),
    ),
    NativeWidgetSpec(
        "textbox",
        "Text Viewer",
        "A native multiline, scrollable TextBox.",
        "picoware.gui.textbox",
        "TextBox",
        uses_text=True,
        emits_activation=False,
        uses_border_color=False,
        default_text="Replace this text with your content.",
    ),
    NativeWidgetSpec(
        "toggle",
        "Toggle",
        "A labeled native on/off switch.",
        "picoware.gui.toggle",
        "Toggle",
        stateful=True,
        full_screen=False,
        uses_text=True,
        supports_boolean_state=True,
        default_text="Setting",
    ),
    NativeWidgetSpec(
        "toggle_list",
        "Toggle List",
        "A keyboard-driven list of native Toggle widgets.",
        "picoware.gui.toggle_list",
        "ToggleList",
        item_based=True,
        stateful=True,
        supports_item_states=True,
        default_items=("First setting", "Second setting", "Third setting"),
    ),
    NativeWidgetSpec(
        "choice",
        "Choice Selector",
        "A native selector for one value from several options.",
        "picoware.gui.choice",
        "Choice",
        item_based=True,
        stateful=True,
        full_screen=False,
        uses_text=True,
        uses_border_color=False,
        supports_initial_selection=True,
        default_width=300,
        default_height=140,
        default_text="Choose",
        default_items=("Option A", "Option B", "Option C"),
    ),
    NativeWidgetSpec(
        "keyboard",
        "Keyboard Input",
        "Picoware's shared keyboard and text-entry workflow.",
        "picoware.gui.keyboard",
        "Keyboard",
        stateful=True,
        uses_text=True,
        uses_text_color=False,
        uses_fill_color=False,
        uses_border_color=False,
        default_text="Enter text",
    ),
    NativeWidgetSpec(
        "search_bar",
        "Search and Select",
        "Native text filtering with keyboard, D-pad, and touch support.",
        "picoware.gui.search_bar",
        "SearchBar",
        item_based=True,
        stateful=True,
        default_items=("Alpha", "Beta", "Gamma"),
    ),
    NativeWidgetSpec(
        "loading",
        "Loading",
        "Picoware's animated loading and elapsed-time display.",
        "picoware.gui.loading",
        "Loading",
        interactive=False,
        emits_activation=False,
        uses_text=True,
        uses_text_color=False,
        default_text="Loading...",
    ),
    NativeWidgetSpec(
        "alert",
        "Alert",
        "A native titled alert that acknowledges any Picoware input.",
        "picoware.gui.alert",
        "Alert",
        uses_text=True,
        uses_border_color=False,
        default_text="Replace this alert message.",
    ),
)

NATIVE_WIDGET_IDS = tuple(spec.id for spec in NATIVE_WIDGET_SPECS)


def native_widget_spec(widget_id: str) -> NativeWidgetSpec:
    """Return metadata for one supported native widget."""
    try:
        return next(spec for spec in NATIVE_WIDGET_SPECS if spec.id == widget_id)
    except StopIteration as error:
        raise KeyError(f"Unknown Picoware widget: {widget_id}") from error


NATIVE_VALUE_READERS = frozenset(
    {"menu", "list", "textbox", "toggle", "toggle_list", "choice", "keyboard", "search_bar"}
)
NATIVE_VALUE_WRITERS = frozenset(
    {"menu", "list", "textbox", "toggle", "toggle_list", "choice", "keyboard"}
)
NATIVE_TEXT_WRITERS = frozenset(
    {"menu", "textbox", "toggle", "keyboard", "loading", "alert"}
)
DRAWN_VALUE_READERS = frozenset({"button", "label", "icon", "list", "progress"})
DRAWN_VALUE_WRITERS = DRAWN_VALUE_READERS
DRAWN_TEXT_WRITERS = frozenset({"button", "label", "icon", "list"})


def element_supports_ui_operation(
    operation_id: str,
    element_kind: str,
    native_widget: str = "",
    *,
    focusable: bool = False,
) -> bool:
    """Return whether one element can satisfy a typed UI operation."""
    if operation_id in {"ui.show", "ui.hide", "ui.enable"}:
        return True
    if operation_id == "ui.focus":
        return focusable
    if element_kind == "native":
        if native_widget not in NATIVE_WIDGET_IDS:
            return False
        if operation_id == "ui.read_value":
            return native_widget in NATIVE_VALUE_READERS
        if operation_id == "ui.set_value":
            return native_widget in NATIVE_VALUE_WRITERS
        if operation_id == "ui.set_text":
            return native_widget in NATIVE_TEXT_WRITERS
        return False
    if operation_id == "ui.read_value":
        return element_kind in DRAWN_VALUE_READERS
    if operation_id == "ui.set_value":
        return element_kind in DRAWN_VALUE_WRITERS
    if operation_id == "ui.set_text":
        return element_kind in DRAWN_TEXT_WRITERS
    if operation_id == "ui.set_progress":
        return element_kind == "progress"
    return False
