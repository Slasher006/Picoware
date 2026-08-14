"""Small Picoware-native starting points for the App GUI workflow."""

from __future__ import annotations

from dataclasses import dataclass

from .designer_model import (
    DEVICE_PROFILES,
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
)
from .native_widgets import native_widget_spec


@dataclass(frozen=True)
class PresetElement:
    """Describe one workflow-starter element on a 320 by 320 logical canvas."""

    kind: str
    name: str
    x: int
    y: int
    width: int
    height: int
    text: str = ""
    event: str = ""
    native_widget: str = ""
    items: tuple[str, ...] = ()
    selected_index: int = 0
    state: bool = False
    focusable: bool | None = None


@dataclass(frozen=True)
class PresetScreen:
    """Describe one intentionally small starter screen."""

    name: str
    elements: tuple[PresetElement, ...] = ()
    background_color: int = 0x0000


@dataclass(frozen=True)
class PresetRoute:
    """Describe an optional starter navigation example."""

    source_screen: str
    event: str
    target_screen: str
    transition: str = "replace"
    trigger_event_id: str = ""


@dataclass(frozen=True)
class AppPreset:
    """Describe one compact workflow shell rather than a finished application."""

    id: str
    name: str
    summary: str
    description: str
    capabilities: tuple[str, ...]
    icon: str
    screens: tuple[PresetScreen, ...]
    routes: tuple[PresetRoute, ...] = ()


def _native(
    widget_id: str,
    *,
    text: str = "",
    items: tuple[str, ...] = (),
    event: str = "",
    state: bool = False,
) -> PresetElement:
    """Create one native widget specification with practical starter geometry."""
    spec = native_widget_spec(widget_id)
    if spec.full_screen:
        x, y, width, height = 0, 0, 320, 320
    else:
        width = min(300, spec.default_width)
        height = min(300, spec.default_height)
        x, y = (320 - width) // 2, (320 - height) // 2
    return PresetElement(
        "native",
        spec.name,
        x,
        y,
        width,
        height,
        text or spec.default_text,
        event,
        widget_id,
        items or spec.default_items,
        state=state,
        focusable=spec.interactive,
    )


APP_PRESETS: tuple[AppPreset, ...] = (
    AppPreset(
        "quick_note",
        "Quick Note",
        "Capture a short note, then move to a readable note screen.",
        "The keyboard and navigation are ready. Application code still stores the submitted text and supplies it to the viewer.",
        ("2 screens", "Text input", "Needs storage"),
        "",
        (
            PresetScreen(
                "Write note",
                (_native("keyboard", text="Write a note", event="note_submitted"),),
            ),
            PresetScreen(
                "Read note",
                (
                    _native(
                        "textbox",
                        text="The submitted note can be displayed here.",
                    ),
                ),
            ),
        ),
        (
            PresetRoute("Write note", "note_submitted", "Read note"),
            PresetRoute(
                "Read note",
                "Back",
                "Write note",
                trigger_event_id="event_navigation_back_01",
            ),
        ),
    ),
    AppPreset(
        "field_checklist",
        "Field Checklist",
        "A compact checklist for a repeatable job or trip.",
        "Toggle interaction is ready. Application code decides whether the states are temporary or saved.",
        ("1 screen", "Toggle states", "Optional storage"),
        "",
        (
            PresetScreen(
                "Checklist",
                (
                    _native(
                        "toggle_list",
                        items=("Pack charger", "Check batteries", "Bring cable"),
                        event="checklist_changed",
                    ),
                ),
            ),
        ),
    ),
    AppPreset(
        "focus_timer",
        "Focus Timer",
        "Choose a session length, then open a focused running view.",
        "The selection and screen flow are ready. Application code owns countdown timing, pause behavior, and completion.",
        ("2 screens", "Duration choice", "Needs timer logic"),
        "",
        (
            PresetScreen(
                "Choose duration",
                (
                    _native(
                        "choice",
                        text="Focus length",
                        items=("15 minutes", "25 minutes", "45 minutes"),
                        event="duration_chosen",
                    ),
                ),
            ),
            PresetScreen(
                "Focus session",
                (_native("loading", text="Focus session"),),
            ),
        ),
        (
            PresetRoute("Choose duration", "duration_chosen", "Focus session"),
            PresetRoute(
                "Focus session",
                "Back",
                "Choose duration",
                trigger_event_id="event_navigation_back_01",
            ),
        ),
    ),
    AppPreset(
        "device_settings",
        "Device Settings",
        "A small settings page with familiar on/off options.",
        "Navigation and toggling are ready. Application code applies and persists each setting.",
        ("1 screen", "Toggle list", "Needs persistence"),
        "",
        (
            PresetScreen(
                "Settings",
                (
                    _native(
                        "toggle_list",
                        items=("Sound", "Wi-Fi", "Show hints"),
                        event="setting_changed",
                    ),
                ),
            ),
        ),
    ),
    AppPreset(
        "item_browser",
        "Item Browser",
        "Select an item from a list, then open a detail view.",
        "The browser flow is ready. Application code provides the real catalogue and selected-item details.",
        ("2 screens", "Selection flow", "Needs item data"),
        "",
        (
            PresetScreen(
                "Browse items",
                (
                    _native(
                        "list",
                        items=("Documents", "Tools", "Games"),
                        event="item_selected",
                    ),
                ),
            ),
            PresetScreen(
                "Item details",
                (_native("textbox", text="Selected item details appear here."),),
            ),
        ),
        (
            PresetRoute("Browse items", "item_selected", "Item details"),
            PresetRoute(
                "Item details",
                "Back",
                "Browse items",
                trigger_event_id="event_navigation_back_01",
            ),
        ),
    ),
    AppPreset(
        "pocket_converter",
        "Pocket Converter",
        "Choose a conversion, then enter the value to convert.",
        "Input and navigation are ready. Application code parses the value, performs the calculation, and shows a result.",
        ("2 screens", "Choice and input", "Needs conversion logic"),
        "",
        (
            PresetScreen(
                "Choose conversion",
                (
                    _native(
                        "choice",
                        text="Convert",
                        items=(
                            "Celsius to Fahrenheit",
                            "Kilometres to miles",
                            "Kilograms to pounds",
                        ),
                        event="conversion_chosen",
                    ),
                ),
            ),
            PresetScreen(
                "Enter value",
                (_native("keyboard", text="Enter value", event="value_submitted"),),
            ),
        ),
        (
            PresetRoute("Choose conversion", "conversion_chosen", "Enter value"),
            PresetRoute(
                "Enter value",
                "Back",
                "Choose conversion",
                trigger_event_id="event_navigation_back_01",
            ),
        ),
    ),
    AppPreset(
        "command_search",
        "Command Search",
        "Search a compact command catalogue and confirm the selected command.",
        "Filtering and selection are ready. Application code maps the selected text to safe command handlers.",
        ("2 screens", "Search flow", "Needs command handlers"),
        "",
        (
            PresetScreen(
                "Search commands",
                (
                    _native(
                        "search_bar",
                        items=("Open notes", "Start focus timer", "Show system status"),
                        event="command_selected",
                    ),
                ),
            ),
            PresetScreen(
                "Command selected",
                (
                    _native(
                        "alert", text="Connect the selected command to its handler."
                    ),
                ),
            ),
        ),
        (
            PresetRoute("Search commands", "command_selected", "Command selected"),
            PresetRoute(
                "Command selected",
                "Back",
                "Search commands",
                trigger_event_id="event_navigation_back_01",
            ),
        ),
    ),
    AppPreset(
        "sensor_monitor",
        "Sensor Monitor",
        "Choose a device metric, then open a readable status screen.",
        "The selection flow is ready. Application code reads the hardware or system provider and updates the status text.",
        ("2 screens", "Metric selection", "Needs sensor provider"),
        "",
        (
            PresetScreen(
                "Choose metric",
                (
                    _native(
                        "menu",
                        text="System status",
                        items=("Battery", "Temperature", "Storage"),
                        event="metric_selected",
                    ),
                ),
            ),
            PresetScreen(
                "Metric details",
                (
                    _native(
                        "textbox",
                        text="Read the selected metric and show its value here.",
                    ),
                ),
            ),
        ),
        (
            PresetRoute("Choose metric", "metric_selected", "Metric details"),
            PresetRoute(
                "Metric details",
                "Back",
                "Choose metric",
                trigger_event_id="event_navigation_back_01",
            ),
        ),
    ),
    AppPreset(
        "confirm_action",
        "Confirm Action",
        "Ask for a decision, then show a clear result or next-step message.",
        "The decision flow is ready. Application code handles Save, Discard, and Cancel without invented side effects.",
        ("2 screens", "Decision flow", "Needs action handler"),
        "",
        (
            PresetScreen(
                "Make decision",
                (
                    _native(
                        "choice",
                        text="Save changes?",
                        items=("Save", "Discard", "Cancel"),
                        event="answer_selected",
                    ),
                ),
            ),
            PresetScreen(
                "Decision result",
                (
                    _native(
                        "alert",
                        text="Handle the selected answer, then replace this message.",
                    ),
                ),
            ),
        ),
        (
            PresetRoute("Make decision", "answer_selected", "Decision result"),
            PresetRoute(
                "Decision result",
                "Back",
                "Make decision",
                trigger_event_id="event_navigation_back_01",
            ),
        ),
    ),
    AppPreset(
        "quick_control",
        "Quick Control",
        "A single large on/off control for one device feature.",
        "The boolean event is ready. Application code connects it to a relay, radio, light, or another safe handler.",
        ("1 screen", "Boolean event", "Needs device handler"),
        "",
        (
            PresetScreen(
                "Control",
                (_native("toggle", text="Device enabled", event="control_changed"),),
            ),
        ),
    ),
)


def app_preset(preset_id: str) -> AppPreset:
    """Return one built-in starter by stable ID."""
    try:
        return next(preset for preset in APP_PRESETS if preset.id == preset_id)
    except StopIteration as error:
        raise KeyError(f"Unknown app starter: {preset_id}") from error


def build_app_preset(
    preset_id: str,
    project_name: str | None = None,
    profile: str = "PicoCalc 320x320",
) -> GuiProject:
    """Build one independent editable project from a compact workflow starter."""
    preset = app_preset(preset_id)
    normalized_profile = profile if profile in DEVICE_PROFILES else "PicoCalc 320x320"
    project = GuiProject.create(project_name or preset.name, normalized_profile)
    project.screens.clear()
    project.connections.clear()
    project.assets.clear()
    project.generated_app = {
        "asset_storage": "combined",
        "starter_id": preset.id,
        "starter_capabilities": list(preset.capabilities),
    }
    screens: dict[str, ScreenDesign] = {}
    event_elements: dict[tuple[str, str], GuiElement] = {}

    for screen_index, screen_spec in enumerate(preset.screens):
        screen = ScreenDesign.create(
            screen_spec.name, project.width, project.height, screen_index
        )
        screen.background_color = screen_spec.background_color
        screens[screen.name] = screen
        project.screens.append(screen)
        focus_order = 0
        for element_spec in screen_spec.elements:
            element = GuiElement.create(element_spec.kind, len(screen.elements) + 1)
            element.name = element_spec.name
            element.text = element_spec.text
            element.x, element.y, element.width, element.height = _scaled_geometry(
                element_spec, project.width, project.height
            )
            element.event_name = element_spec.event
            element.native_widget = element_spec.native_widget
            element.widget_items = list(element_spec.items)
            element.widget_item_states = [False] * len(element.widget_items)
            element.widget_selected_index = element_spec.selected_index
            element.widget_state = element_spec.state
            element.focusable = (
                element_spec.focusable
                if element_spec.focusable is not None
                else bool(element_spec.event)
            )
            element.enabled = element.focusable
            if element.focusable:
                focus_order += 1
                element.focus_order = focus_order
            else:
                element.focus_order = 0
            screen.elements.append(element)
            if element_spec.event:
                event_elements[(screen.name, element_spec.event)] = element

    project.start_screen_id = project.screens[0].id
    for route_spec in preset.routes:
        source = screens[route_spec.source_screen]
        target = screens[route_spec.target_screen]
        source_element = event_elements.get(
            (route_spec.source_screen, route_spec.event)
        )
        if source_element is None and not route_spec.trigger_event_id:
            continue
        connection = FlowConnection.create(
            source.id,
            target.id,
            route_spec.event,
            source_element.id if source_element is not None else "",
        )
        connection.transition = route_spec.transition
        connection.trigger_event_id = route_spec.trigger_event_id or (
            source_element.event_id if source_element is not None else ""
        )
        project.connections.append(connection)
    return project


def _scaled_geometry(
    spec: PresetElement, screen_width: int, screen_height: int
) -> tuple[int, int, int, int]:
    """Scale starter geometry to the selected device profile."""
    scale_x = screen_width / 320
    scale_y = screen_height / 320
    width = min(screen_width, max(1, round(spec.width * scale_x)))
    height = min(screen_height, max(1, round(spec.height * scale_y)))
    x = min(max(0, round(spec.x * scale_x)), max(0, screen_width - width))
    y = min(max(0, round(spec.y * scale_y)), max(0, screen_height - height))
    return x, y, width, height
