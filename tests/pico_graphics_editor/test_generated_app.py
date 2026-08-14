"""Tests for generated app structure v1 and its atomic patch set."""

# ruff: noqa: E402

import ast
import hashlib
import importlib
import io
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch


REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from pico_graphics_editor import generated_app
from pico_graphics_editor.app_presets import APP_PRESETS, build_app_preset
from pico_graphics_editor.asset_codegen import (
    GeneratedAudioEntry,
    decode_asset_resource,
)
from pico_graphics_editor.designer_model import (
    BehaviorConnection,
    FlowConnection,
    FlowNode,
    GuiElement,
    GuiProject,
    ProjectAsset,
    ScreenDesign,
)
from pico_graphics_editor.generated_app import (
    ASSET_STORAGE_INDIVIDUAL,
    GeneratedAppError,
    apply_generated_app_patchset,
    build_generated_app_patchset,
    build_live_preview_bundle,
    generate_app_scaffold,
    generate_behavior_handlers,
    generate_behavior_module,
    generate_entrypoint,
    generate_package_init,
    generate_project_asset_resource,
    generate_project_assets_module,
    generate_ui_module,
    parse_generated_header,
    project_preflight_diagnostics,
    resolve_generated_app_paths,
    sanitize_display_name,
    sanitize_package_name,
)
from pico_graphics_editor.model import PixelArt


class RecordingDraw:
    """Record the small graphics surface used by generated modules."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def _fill_rectangle(self, *args: object) -> None:
        self.calls.append(("fill", args))

    def _rectangle(self, *args: object) -> None:
        self.calls.append(("rectangle", args))

    def _text(self, *args: object) -> None:
        self.calls.append(("text", args))

    def _bytearray(self, *args: object) -> None:
        self.calls.append(("bytearray", args))

    def clear(self) -> None:
        self.calls.append(("clear", ()))

    def swap(self) -> None:
        self.calls.append(("swap", ()))


class RecordingNativeWidget:
    """Stand in for Picoware widgets while exercising generated delegation."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.items: list[str] = []
        self.list = self
        self.options: list[str] = []
        self.selected_index = 0
        self.state = False
        self.current_text = ""
        self.current_state = False
        self.response = ""
        self.selected_item = None
        self.is_finished = False
        self.calls: list[str] = []

    @property
    def current_item(self) -> str | None:
        if not self.items:
            return None
        return self.items[self.selected_index]

    def add_item(self, item: str) -> None:
        self.items.append(item)

    def add_toggle(self, item: str, state: bool = False) -> None:
        self.items.append(item)
        self.current_state = state

    def update_toggle(self, index: int, text: str, state: bool) -> bool:
        if index < 0 or index >= len(self.items):
            return False
        self.current_text = text
        self.current_state = state
        return True

    def set_selected(self, index: int) -> None:
        self.selected_index = index

    def scroll_up(self) -> None:
        self.calls.append("up")
        if self.items:
            self.selected_index = (self.selected_index - 1) % len(self.items)

    def scroll_down(self) -> None:
        self.calls.append("down")
        if self.items:
            self.selected_index = (self.selected_index + 1) % len(self.items)

    def draw(self, *args: object, **kwargs: object) -> None:
        self.calls.append("draw")

    def refresh(self) -> None:
        self.calls.append("refresh")

    def run(self, *args: object, **kwargs: object) -> bool:
        self.calls.append("run")
        return True

    def animate(self, *args: object, **kwargs: object) -> None:
        self.calls.append("animate")

    def reset(self) -> None:
        self.calls.append("reset")


class RecordingVector:
    """Minimal Vector accepted by generated native widget constructors."""

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


def native_picoware_modules() -> dict[str, types.ModuleType]:
    """Return importable stand-ins for every supported Picoware widget module."""
    modules: dict[str, types.ModuleType] = {}
    picoware = types.ModuleType("picoware")
    picoware.__path__ = []
    gui = types.ModuleType("picoware.gui")
    gui.__path__ = []
    system = types.ModuleType("picoware.system")
    system.__path__ = []
    modules.update(
        {"picoware": picoware, "picoware.gui": gui, "picoware.system": system}
    )
    classes = {
        "menu": "Menu",
        "list": "List",
        "textbox": "TextBox",
        "toggle": "Toggle",
        "toggle_list": "ToggleList",
        "choice": "Choice",
        "search_bar": "SearchBar",
        "loading": "Loading",
        "alert": "Alert",
    }
    for module_name, class_name in classes.items():
        module = types.ModuleType(f"picoware.gui.{module_name}")
        setattr(module, class_name, RecordingNativeWidget)
        modules[module.__name__] = module
    buttons = types.ModuleType("picoware.system.buttons")
    for name, value in (
        ("BUTTON_UP", 1),
        ("BUTTON_DOWN", 2),
        ("BUTTON_LEFT", 3),
        ("BUTTON_RIGHT", 4),
        ("BUTTON_CENTER", 5),
        ("BUTTON_BACK", 6),
        ("BUTTON_BACKSPACE", 73),
        ("BUTTON_ESCAPE", 77),
    ):
        setattr(buttons, name, value)
    vector = types.ModuleType("picoware.system.vector")
    vector.Vector = RecordingVector
    modules[buttons.__name__] = buttons
    modules[vector.__name__] = vector
    return modules


def golden_project() -> GuiProject:
    """Return a stable two-screen project with linked and animated assets."""
    project = GuiProject.create("Status Demo")
    project.project_id = "project_status_demo_01"
    home = project.screens[0]
    home.id = "screen_home_01"
    home.name = "Home"
    project.start_screen_id = home.id
    settings = ScreenDesign(
        "screen_settings_01", "Settings", 320, 320, background_color=0x0000
    )
    project.screens.append(settings)

    badge_art = PixelArt(4, 3)
    badge_art.draw_rectangle(0, 0, 2, 1, 0xF800, True)
    badge_art.set_pixel(3, 0, 0xFFFF)
    badge_art.draw_rectangle(1, 1, 2, 1, 0x07E0, True)
    badge_art.draw_rectangle(0, 2, 4, 1, 0x0000, True)
    badge = ProjectAsset.from_pixel_art(
        "asset_status_badge_01",
        "Status Badge",
        badge_art,
        link_state="current",
    )
    spinner_a = PixelArt(3, 3)
    spinner_a.draw_rectangle(0, 0, 3, 1, 0xFFE0, True)
    spinner_b = PixelArt(3, 3)
    spinner_b.draw_rectangle(0, 2, 3, 1, 0x4208, True)
    spinner = ProjectAsset(
        "asset_activity_spinner_01",
        "Activity Spinner",
        3,
        3,
        frames=[spinner_a.pixels, spinner_b.pixels],
        durations=[250, 250],
        link_state="current",
    )
    project.assets = [badge, spinner]

    for index, x in enumerate((20, 80)):
        icon = GuiElement.create("icon", index + 1)
        icon.id = f"element_badge_{index + 1}"
        icon.asset_id = badge.id
        icon.width = 8
        icon.height = 6
        icon.x = x
        icon.y = 52
        icon.focusable = False
        home.elements.append(icon)
    settings_button = GuiElement.create("button", 3)
    settings_button.id = "element_settings"
    settings_button.event_id = "event_open_settings_01"
    settings_button.text = "Settings"
    settings_button.x = 16
    settings_button.y = 100
    settings_button.width = 136
    home.elements.append(settings_button)
    refresh = GuiElement.create("button", 4)
    refresh.id = "element_refresh"
    refresh.event_id = "event_refresh_status_01"
    refresh.text = "Refresh Status"
    refresh.x = 16
    refresh.y = 148
    refresh.width = 136
    home.elements.append(refresh)

    spinner_element = GuiElement.create("icon", 1)
    spinner_element.id = "element_spinner"
    spinner_element.asset_id = spinner.id
    spinner_element.width = 9
    spinner_element.height = 9
    spinner_element.x = 20
    spinner_element.y = 52
    spinner_element.focusable = False
    settings.elements.append(spinner_element)
    back = GuiElement.create("button", 2)
    back.id = "element_back"
    back.event_id = "event_navigation_back_01"
    back.text = "Back"
    back.x = 16
    back.y = 100
    back.width = 136
    settings.elements.append(back)

    open_settings = FlowConnection.create(
        home.id, settings.id, "Open Settings", settings_button.id
    )
    open_settings.trigger_event_id = settings_button.event_id
    go_back = FlowConnection.create(settings.id, home.id, "Back", back.id)
    go_back.trigger_event_id = back.event_id
    project.connections = [open_settings, go_back]
    return project


def execute_generated_ui(project: GuiProject) -> tuple[object, types.ModuleType]:
    """Execute generated assets and UI under a temporary package namespace."""
    package_name = "_generated_test_package"
    package = types.ModuleType(package_name)
    package.__path__ = []
    assets = types.ModuleType(f"{package_name}.generated_assets")
    resource = generate_project_asset_resource(project)
    assets.__file__ = "generated_assets.py"
    assets.__dict__["open"] = lambda unused_path, unused_mode: io.BytesIO(resource.data)
    exec(resource.module_source, assets.__dict__)
    ui_module = types.ModuleType(f"{package_name}.generated_ui")
    ui_module.__package__ = package_name
    old_modules = {
        name: sys.modules.get(name)
        for name in (package_name, assets.__name__, ui_module.__name__)
    }
    try:
        sys.modules[package_name] = package
        sys.modules[assets.__name__] = assets
        sys.modules[ui_module.__name__] = ui_module
        exec(generate_ui_module(project), ui_module.__dict__)
        return ui_module.GeneratedUI, assets
    finally:
        for name, previous in old_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class GeneratedAppTests(unittest.TestCase):
    """Verify generation boundaries and generated runtime behavior."""

    def test_name_sanitization_and_resolved_paths(self) -> None:
        """Handle spaces, punctuation, Unicode, leading digits, and empty names."""
        self.assertEqual(sanitize_package_name(" My App! "), "my_app")
        self.assertEqual(sanitize_package_name("Über Café"), "uber_cafe")
        self.assertEqual(sanitize_package_name("123"), "_123")
        self.assertEqual(sanitize_package_name("你好"), "generated_app")
        self.assertEqual(sanitize_display_name(" Demo:/App? "), "Demo_App_")
        paths = resolve_generated_app_paths("My App", "/tmp/generated-root")
        self.assertEqual(paths.entrypoint.name, "My App.py")
        self.assertEqual(paths.package.name, "my_app")

    def test_generated_headers_are_exact_and_parseable(self) -> None:
        """Mark only editor-owned modules with fixed v1 metadata."""
        project = golden_project()
        ui = generate_ui_module(project)
        assets = generate_project_assets_module(project)
        self.assertEqual(parse_generated_header(ui).role, "ui")
        self.assertEqual(parse_generated_header(assets).role, "assets")
        self.assertEqual(parse_generated_header(ui).project_id, project.project_id)
        self.assertIsNone(parse_generated_header(generate_app_scaffold()))

    def test_export_rejects_a_project_id_that_can_break_generated_headers(self) -> None:
        """Stop malformed hand-edited identities before creating any output."""
        project = golden_project()
        project.project_id = "project_bad\n# injected"
        diagnostics = project_preflight_diagnostics(project)
        self.assertIn(
            "invalid-project-id",
            {item.code for item in diagnostics if item.severity == "error"},
        )
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(GeneratedAppError, "Project ID must start"):
                build_generated_app_patchset(project, folder)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_generated_ui_focus_navigation_assets_and_unknown_events(self) -> None:
        """Run the public stable UI API against the two-screen golden project."""
        project = golden_project()
        ui_class, assets = execute_generated_ui(project)
        draw = RecordingDraw()
        ui = ui_class(draw)
        self.assertEqual(ui.screen_id, "screen_home_01")
        ui.render()
        self.assertNotIn("_ASSETS", assets.__dict__)
        self.assertTrue(assets.has_asset("asset_status_badge_01"))
        self.assertTrue(assets.has_asset("asset_activity_spinner_01"))
        self.assertEqual(ui.focused_event(), "event_open_settings_01")
        self.assertEqual(ui.move_focus(1), "event_refresh_status_01")
        self.assertEqual(ui.activate_focused(), "event_refresh_status_01")
        self.assertEqual(ui.screen_id, "screen_home_01")
        ui.move_focus(-1)
        self.assertEqual(ui.activate_focused(), "event_open_settings_01")
        self.assertEqual(ui.screen_id, "screen_settings_01")
        self.assertFalse(ui.handle_navigation("unknown"))
        self.assertTrue(ui.handle_navigation("event_navigation_back_01"))
        self.assertEqual(ui.screen_id, "screen_home_01")
        self.assertFalse(ui.set_screen("missing"))

    def test_all_native_starters_generate_real_picoware_widget_imports(self) -> None:
        """Generate native classes instead of flattening starters into draw calls."""
        expected_imports = {
            "menu": "from picoware.gui.menu import Menu as PicowareMenu",
            "list": "from picoware.gui.list import List as PicowareList",
            "textbox": "from picoware.gui.textbox import TextBox as PicowareTextBox",
            "toggle": "from picoware.gui.toggle import Toggle as PicowareToggle",
            "toggle_list": (
                "from picoware.gui.toggle_list import ToggleList as PicowareToggleList"
            ),
            "choice": "from picoware.gui.choice import Choice as PicowareChoice",
            "search_bar": (
                "from picoware.gui.search_bar import SearchBar as PicowareSearchBar"
            ),
            "loading": "from picoware.gui.loading import Loading as PicowareLoading",
            "alert": "from picoware.gui.alert import Alert as PicowareAlert",
        }
        seen: set[str] = set()
        for preset in APP_PRESETS:
            project = build_app_preset(preset.id)
            source = generate_ui_module(project)
            ast.parse(source)
            for screen in project.screens:
                for element in screen.elements:
                    if element.kind != "native":
                        continue
                    seen.add(element.native_widget)
                    expected = expected_imports.get(element.native_widget)
                    if expected is not None:
                        self.assertIn(expected, source)
                    self.assertIn(f"self._render_native({element.id!r})", source)
        self.assertEqual(
            seen,
            {
                "menu",
                "list",
                "textbox",
                "toggle",
                "toggle_list",
                "choice",
                "keyboard",
                "search_bar",
                "loading",
                "alert",
            },
        )

    def test_circular_toggle_uses_the_supported_coordinate_draw_api(self) -> None:
        """Render the circular ON state without calling Vector-only fill_circle."""
        system_module = types.ModuleType("picoware.system.system")

        class CircularSystem:
            is_circular = True

        system_module.System = CircularSystem
        modules = native_picoware_modules()
        modules[system_module.__name__] = system_module
        spec = importlib.util.spec_from_file_location(
            "audited_picoware_toggle",
            REPOSITORY_PATH / "src/MicroPython/picoware/gui/toggle.py",
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)

        class CircularDraw:
            use_lvgl = False
            size = RecordingVector(240, 240)
            font_size = RecordingVector(8, 8)

            def __init__(self) -> None:
                self.circles: list[tuple[object, ...]] = []

            def clear(self, *args: object) -> None:
                pass

            def swap(self) -> None:
                pass

            def len(self, text: str) -> int:
                return len(text) * 8

            def _text(self, *args: object) -> None:
                pass

            def _fill_circle(self, *args: object) -> None:
                self.circles.append(args)

            def _circle(self, *args: object) -> None:
                self.circles.append(args)

        draw = CircularDraw()
        with patch.dict(sys.modules, modules):
            toggle = module.Toggle(
                draw,
                RecordingVector(10, 20),
                RecordingVector(200, 40),
                "Wi-Fi",
                True,
                should_clear=False,
                use_lvgl=False,
            )
            toggle.draw()

        self.assertEqual(len(draw.circles), 2)

    def test_generated_menu_delegates_render_input_event_and_value(self) -> None:
        """Exercise a generated native widget through the stable UI boundary."""
        project = build_app_preset("sensor_monitor")
        element = project.screens[0].elements[0]
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        draw = RecordingDraw()
        view_manager = types.SimpleNamespace(draw=draw)
        ui = ui_class(view_manager)

        ui.render()
        widget = ui._native_widgets[element.id]
        self.assertIsInstance(widget, RecordingNativeWidget)
        self.assertIn("draw", widget.calls)
        self.assertEqual(ui.native_value(element.id), "Battery")

        event_id, consumed = ui.handle_input(2)
        self.assertIsNone(event_id)
        self.assertTrue(consumed)
        self.assertEqual(ui.native_value(element.id), "Temperature")
        event_id, consumed = ui.handle_input(5)
        self.assertEqual(event_id, element.event_id)
        self.assertTrue(consumed)
        self.assertEqual(ui.screen_id, project.screens[1].id)

    def test_inline_native_controls_share_the_ordinary_focus_order(self) -> None:
        """Move between drawn and inline controls without trapping D-pad input."""
        project = GuiProject.create("Inline controls")
        button = GuiElement.create("button", 1)
        choice = GuiElement.create("native", 2)
        choice.native_widget = "choice"
        choice.widget_items = ["Automatic", "Manual"]
        choice.focus_order = 1
        toggle = GuiElement.create("native", 3)
        toggle.native_widget = "toggle"
        toggle.widget_state = True
        toggle.focus_order = 2
        project.screens[0].elements.extend((button, choice, toggle))

        self.assertFalse(
            any(
                item.severity == "error"
                for item in project_preflight_diagnostics(project)
            )
        )
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        ui = ui_class(types.SimpleNamespace(draw=RecordingDraw()))
        ui.render()

        self.assertEqual(ui.focused_event(), button.event_id)
        self.assertEqual(ui.move_focus(1), choice.event_id)
        self.assertEqual(ui.handle_input(2), (None, False))
        self.assertEqual(ui.move_focus(1), toggle.event_id)
        widget = ui._native_widgets[toggle.id]
        self.assertTrue(widget.args[4])
        self.assertEqual(ui.handle_input(5), (toggle.event_id, True))
        self.assertTrue(widget.state)

    def test_generated_keyboard_submission_applies_declared_screen_flow(self) -> None:
        """Advance a compact workflow when the shared keyboard finishes."""
        project = build_app_preset("quick_note")
        element = project.screens[0].elements[0]
        keyboard = RecordingNativeWidget()
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        view_manager = types.SimpleNamespace(draw=RecordingDraw(), keyboard=keyboard)
        ui = ui_class(view_manager)
        ui.render()
        keyboard.is_finished = True

        event_id, consumed = ui.handle_input(5)

        self.assertEqual(event_id, element.event_id)
        self.assertTrue(consumed)
        self.assertEqual(ui.screen_id, project.screens[1].id)
        self.assertTrue(ui.handle_navigation("event_navigation_back_01"))
        self.assertEqual(ui.screen_id, project.screens[0].id)

    def test_generated_behavior_and_handler_modules_use_stable_bindings(self) -> None:
        """Generate executable bindings separately from developer handler bodies."""
        project = GuiProject.create("Behavior package")
        button = GuiElement.create("button", 1)
        button.focusable = True
        project.screens[0].elements.append(button)
        event = FlowNode.create("event", 1)
        event.set_operation("event.ui")
        event.binding = {
            "screen_id": project.screens[0].id,
            "element_id": button.id,
            "event_id": button.event_id,
        }
        handler = FlowNode.create("action", 2)
        handler.set_operation("custom.handler")
        handler.properties["handler"] = generated_app.flow_stub_name(handler)
        project.behavior_nodes.extend((event, handler))
        project.behavior_connections.append(
            BehaviorConnection.create(event.id, "event", handler.id, "in")
        )

        behavior_source = generate_behavior_module(project)
        handler_source = generate_behavior_handlers(project)

        ast.parse(behavior_source)
        ast.parse(handler_source)
        self.assertIn(button.event_id, behavior_source)
        self.assertIn(handler.properties["handler"], handler_source)
        self.assertIn("TEST_MANIFEST", behavior_source)
        self.assertIn("'bindings'", behavior_source)
        self.assertNotIn("PySide6", behavior_source)

    def test_generated_behavior_routes_structured_widget_payload(self) -> None:
        """Keep generated MicroPython behavior equivalent to the desktop executor."""
        project = GuiProject.create("Widget behavior")
        choice = GuiElement.create("native", 1)
        choice.native_widget = "choice"
        choice.widget_items = ["Automatic", "Manual"]
        project.screens[0].elements.append(choice)
        event = FlowNode.create("event", 1)
        event.set_operation("event.ui")
        event.binding = {
            "screen_id": project.screens[0].id,
            "element_id": choice.id,
            "event_id": choice.event_id,
            "widget_type": "choice",
        }
        state = FlowNode.create("state", 2)
        state.set_operation("state.set")
        state.properties.update({"key": "mode", "value": "$value"})
        project.behavior_nodes.extend((event, state))
        project.behavior_connections.append(
            BehaviorConnection.create(event.id, "event", state.id, "in")
        )
        namespace = {}
        exec(generate_behavior_module(project), namespace)

        class Ui:
            def read_value(self, element_id):
                return "Manual"

            def read_index(self, element_id):
                return 1

            def widget_type(self, element_id):
                return "choice"

        runtime = namespace["BehaviorRuntime"](
            Ui(), types.SimpleNamespace(), services={"state": {}}
        )

        self.assertTrue(runtime.dispatch_event(choice.event_id))
        self.assertEqual(runtime.state["mode"], "Manual")
        payload = namespace["_widget_event_payload"](
            runtime.ui, event.binding, choice.event_id
        )
        self.assertEqual(payload["index"], 1)
        self.assertEqual(payload["text"], "Manual")

    def test_live_preview_dispatches_button_alert_behavior(self) -> None:
        """Package and display a button-bound alert in Run current design."""
        project = GuiProject.create("Alert preview")
        button = GuiElement.create("button", 1)
        button.text = "About"
        project.screens[0].elements.append(button)
        event = FlowNode.create("event", 1)
        event.set_operation("event.ui")
        event.binding = {
            "screen_id": project.screens[0].id,
            "element_id": button.id,
            "event_id": button.event_id,
            "widget_type": "button",
        }
        alert = FlowNode.create("action", 2)
        alert.set_operation("ui.alert")
        alert.properties["message"] = "Hello from the simulator"
        project.behavior_nodes.extend((event, alert))
        project.behavior_connections.append(
            BehaviorConnection.create(event.id, "event", alert.id, "in")
        )

        files = dict(build_live_preview_bundle(project, project.start_screen_id).files)
        self.assertIn("gui_designer_live/generated_behavior.py", files)
        self.assertIn("gui_designer_live/behavior_handlers.py", files)
        self.assertIn(
            "_live_behavior.dispatch_event(event_id)", files["GuiDesignerLive.py"]
        )

        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        del unused_assets
        ui = ui_class(RecordingDraw())
        self.assertTrue(ui.alert(alert.properties["message"]))
        overlay = ui._behavior_alert
        self.assertIsInstance(overlay, RecordingNativeWidget)
        self.assertEqual(overlay.args[1], "Hello from the simulator")

        ui.render()
        self.assertIn("draw", overlay.calls)
        self.assertEqual(ui.handle_input(1), (None, True))
        self.assertIsNone(ui._behavior_alert)
        self.assertTrue(ui.alert(alert.properties["message"]))
        self.assertEqual(ui.handle_input(73), (None, True))
        self.assertIsNone(ui._behavior_alert)

    def test_preflight_rejects_ambiguous_or_incomplete_native_widgets(self) -> None:
        """Report unsafe native layouts before generation or live preview."""
        project = GuiProject.create("Invalid native")
        first = GuiElement.create("native", 1)
        first.native_widget = "menu"
        first.widget_items = []
        first.widget_selected_index = 4
        second = GuiElement.create("native", 2)
        second.native_widget = "unknown"
        project.screens[0].elements.extend((first, second))

        diagnostics = project_preflight_diagnostics(project)
        codes = {item.code for item in diagnostics if item.severity == "error"}

        self.assertIn("screen-widget-with-layers", codes)
        self.assertIn("empty-native-widget", codes)
        self.assertIn("unknown-native-widget", codes)

        second.native_widget = "alert"
        diagnostics = project_preflight_diagnostics(project)
        codes = {item.code for item in diagnostics if item.severity == "error"}
        self.assertIn("multiple-screen-widgets", codes)

        first.widget_items = ["Only item"]
        second.native_widget = "unknown"
        diagnostics = project_preflight_diagnostics(project)
        codes = {item.code for item in diagnostics if item.severity == "error"}
        self.assertIn("native-widget-selected-index", codes)

    def test_preflight_blocks_navigation_fields_that_generation_would_omit(
        self,
    ) -> None:
        """Never accept a relation that generated navigation silently skips."""
        project = GuiProject.create("Navigation contracts")
        target = ScreenDesign.create("Target", 320, 320, 2)
        project.screens.append(target)
        relation = FlowConnection.create(
            project.screens[0].id,
            target.id,
            "select",
        )
        relation.condition = "is_ready"
        relation.action = "record_visit"
        project.connections.append(relation)

        diagnostics = project_preflight_diagnostics(project)
        codes = {item.code for item in diagnostics if item.severity == "error"}

        self.assertIn("unsupported-navigation-condition", codes)
        self.assertIn("unsupported-navigation-action", codes)
        with self.assertRaises(GeneratedAppError):
            generate_ui_module(project)

    def test_asset_resource_excludes_unreferenced_project_assets(self) -> None:
        """Deploy screen dependencies without copying the whole project catalogue."""
        project = golden_project()
        unused_art = PixelArt(320, 320)
        unused = ProjectAsset.from_pixel_art("asset-unused", "Unused", unused_art)
        project.assets.append(unused)
        resource = generate_project_asset_resource(project)
        self.assertEqual(resource.asset_count, 2)
        self.assertEqual(resource.frame_count, 3)
        self.assertNotIn(b"asset-unused", resource.data)
        self.assertNotIn("asset-unused", resource.module_source)

    def test_project_resource_accepts_explicit_wav_dependencies(self) -> None:
        """Let app generation add validated WAV files without expanding the model."""
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(1)
            target.setframerate(8000)
            target.writeframes(b"\x80" * 80)
        resource = generate_project_asset_resource(
            golden_project(),
            wav_entries=(GeneratedAudioEntry("wav-click", "Click", output.getvalue()),),
        )
        decoded = decode_asset_resource(resource.data)
        self.assertEqual(resource.audio_count, 1)
        self.assertEqual(decoded.audio_assets[0].asset_id, "wav-click")

    def test_generated_ui_contains_asset_ids_but_no_compact_records(self) -> None:
        """Keep every pixel rectangle in generated_assets rather than screen code."""
        project = golden_project()
        source = generate_ui_module(project)
        self.assertEqual(source.count("'asset_status_badge_01'"), 2)
        self.assertIn("draw_asset(", source)
        self.assertNotIn("(0, 0, 2, 1, 1)", source)
        self.assertNotIn("picoware.system.buttons", source)
        self.assertNotIn("PySide6", source)
        ast.parse(source)
        self.assertEqual(source, generate_ui_module(project))

    def test_generated_ui_preserves_back_to_front_element_order(self) -> None:
        """Emit later hierarchy layers after earlier layers so overlap is deterministic."""
        project = GuiProject.create("Layers")
        back = GuiElement.create("rectangle", 1)
        back.name = "Back"
        back.x, back.y, back.width, back.height = 11, 12, 30, 31
        back.fill_color = 0x001F
        front = GuiElement.create("rectangle", 2)
        front.name = "Front"
        front.x, front.y, front.width, front.height = 21, 22, 30, 31
        front.fill_color = 0xF800
        project.screens[0].elements = [back, front]
        source = generate_ui_module(project)
        back_call = "_fill_rectangle(11, 12, 30, 31, 0x001F)"
        front_call = "_fill_rectangle(21, 22, 30, 31, 0xF800)"
        self.assertLess(source.index(back_call), source.index(front_call))

        project.screens[0].elements = [front, back]
        reversed_source = generate_ui_module(project)
        self.assertLess(
            reversed_source.index(front_call), reversed_source.index(back_call)
        )

    def test_generated_ui_public_mutation_updates_custom_element_rendering(
        self,
    ) -> None:
        """Let safe UI operations update custom labels, progress, and visibility."""
        project = GuiProject.create("Mutable UI")
        label = GuiElement.create("label", 1)
        label.text = "Idle"
        progress = GuiElement.create("progress", 2)
        progress.width = 100
        project.screens[0].elements.extend((label, progress))
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        del unused_assets
        draw = RecordingDraw()
        ui = ui_class(draw)

        self.assertEqual(ui.read_value(label.id), "Idle")
        self.assertEqual(ui.read_value(progress.id), 50)

        self.assertTrue(ui.set_text(label.id, "Connected"))
        self.assertTrue(ui.set_progress(progress.id, 75))
        self.assertTrue(ui.hide(label.id))
        ui.render()

        self.assertNotIn(
            (label.x, label.y, "Connected", label.text_color),
            [args for name, args in draw.calls if name == "text"],
        )
        self.assertIn(
            (progress.x, progress.y, 75, progress.height, progress.border_color),
            [args for name, args in draw.calls if name == "fill"],
        )

    def test_runtime_visibility_and_enabled_state_control_render_and_focus(self) -> None:
        """Let initially hidden controls become visible without retaining stale focus."""
        project = GuiProject.create("Dynamic controls")
        hidden = GuiElement.create("button", 1)
        hidden.text = "Hidden"
        hidden.visible = False
        hidden.enabled = False
        hidden.focusable = True
        hidden.focus_order = 0
        shown = GuiElement.create("button", 2)
        shown.text = "Shown"
        shown.focusable = True
        shown.focus_order = 1
        project.screens[0].elements.extend((hidden, shown))
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        del unused_assets
        draw = RecordingDraw()
        ui = ui_class(draw)

        self.assertEqual(ui.focused_event(), shown.event_id)
        self.assertTrue(ui.show(hidden.id))
        self.assertEqual(ui.focused_event(), shown.event_id)
        self.assertTrue(ui.enable(hidden.id, True))
        self.assertEqual(ui.focused_event(), hidden.event_id)
        ui.render()
        self.assertIn(
            (hidden.x + 4, hidden.y + 4, "Hidden", hidden.text_color),
            [args for name, args in draw.calls if name == "text"],
        )
        self.assertTrue(ui.hide(hidden.id))
        self.assertEqual(ui.focused_event(), shown.event_id)
        self.assertTrue(ui.enable(shown.id, False))
        self.assertIsNone(ui.focused_event())

    def test_native_value_mutation_is_typed_and_rejects_unsupported_widgets(
        self,
    ) -> None:
        """Mutate public widget state without corrupting unrelated attributes."""
        project = GuiProject.create("Typed native values")
        choice = GuiElement.create("native", 1)
        choice.native_widget = "choice"
        choice.widget_items = ["Automatic", "Manual"]
        toggle = GuiElement.create("native", 2)
        toggle.native_widget = "toggle"
        toggle.text = "Enabled"
        search = GuiElement.create("native", 3)
        search.native_widget = "search_bar"
        search.widget_items = ["Alpha", "Beta"]
        search.visible = False
        project.screens[0].elements.extend((choice, toggle, search))
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        del unused_assets
        ui = ui_class(types.SimpleNamespace(draw=RecordingDraw()))
        choice_widget = ui._ensure_native(choice.id)
        choice_widget.options = list(choice.widget_items)
        toggle_widget = ui._ensure_native(toggle.id)
        toggle_widget.text = toggle.text

        self.assertTrue(ui.set_value(choice.id, "Manual"))
        self.assertEqual(choice_widget.state, 1)
        self.assertFalse(ui.set_value(choice.id, "Missing"))
        self.assertTrue(ui.set_value(toggle.id, True))
        self.assertTrue(toggle_widget.state)
        self.assertFalse(ui.set_value(toggle.id, "true"))
        self.assertEqual(toggle_widget.text, "Enabled")
        self.assertFalse(ui.set_value(search.id, "Beta"))
        self.assertFalse(ui.set_text(choice.id, "Wrong surface"))

    def test_native_mutation_contract_covers_every_widget_type(self) -> None:
        """Keep all ten native widgets on explicit supported or rejected paths."""
        expected = {
            "menu": (True, True),
            "list": (True, False),
            "textbox": (True, True),
            "toggle": (True, True),
            "toggle_list": (True, False),
            "choice": (True, False),
            "keyboard": (True, True),
            "search_bar": (False, False),
            "loading": (False, True),
            "alert": (False, True),
        }
        for index, (widget_type, outcomes) in enumerate(expected.items(), 1):
            with self.subTest(widget=widget_type):
                project = GuiProject.create(f"Mutate {widget_type}")
                element = GuiElement.create("native", index)
                element.native_widget = widget_type
                element.text = "Title"
                element.widget_items = ["First", "Second"]
                project.screens[0].elements.append(element)
                if widget_type == "alert":
                    target = ScreenDesign.create("After alert", 320, 320, 1)
                    project.screens.append(target)
                    route = FlowConnection.create(
                        project.screens[0].id,
                        target.id,
                        element.activation_event(),
                        element.id,
                    )
                    route.trigger_event_id = element.event_id
                    project.connections.append(route)
                with patch.dict(sys.modules, native_picoware_modules()):
                    ui_class, unused_assets = execute_generated_ui(project)
                del unused_assets
                keyboard = RecordingNativeWidget()
                ui = ui_class(
                    types.SimpleNamespace(draw=RecordingDraw(), keyboard=keyboard)
                )
                widget = ui._ensure_native(element.id)
                if widget_type == "choice":
                    widget.options = list(element.widget_items)
                value = True if widget_type in {"toggle", "toggle_list"} else "Second"

                self.assertEqual(ui.set_value(element.id, value), outcomes[0])
                self.assertEqual(ui.set_text(element.id, "Updated"), outcomes[1])

    def test_loading_ticks_only_while_visible_and_enabled(self) -> None:
        """Request idle redraws for active Loading animation without global repainting."""
        project = GuiProject.create("Loading ticks")
        loading = GuiElement.create("native", 1)
        loading.native_widget = "loading"
        loading.visible = False
        project.screens[0].elements.append(loading)
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        del unused_assets
        ui = ui_class(RecordingDraw())

        self.assertFalse(ui.needs_idle_redraw())
        self.assertTrue(ui.show(loading.id))
        self.assertTrue(ui.needs_idle_redraw())
        self.assertTrue(ui.enable(loading.id, False))
        self.assertFalse(ui.needs_idle_redraw())

    def test_native_alert_acknowledgement_emits_and_navigates(self) -> None:
        """Treat a screen Alert as an acknowledging widget, not a dead display."""
        project = GuiProject.create("Native alert")
        alert = GuiElement.create("native", 1)
        alert.native_widget = "alert"
        alert.name = "Warning"
        alert.text = "Continue?"
        alert.focusable = True
        alert.enabled = True
        project.screens[0].elements.append(alert)
        target = ScreenDesign.create("Done", 320, 320, 1)
        project.screens.append(target)
        route = FlowConnection.create(
            project.screens[0].id,
            target.id,
            alert.activation_event(),
            alert.id,
        )
        route.trigger_event_id = alert.event_id
        project.connections.append(route)
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        del unused_assets
        ui = ui_class(RecordingDraw())

        event_id, consumed = ui.handle_input(73)

        self.assertEqual(event_id, alert.event_id)
        self.assertTrue(consumed)
        self.assertEqual(ui.screen_id, target.id)

    def test_toggle_list_emits_only_when_center_changes_state(self) -> None:
        """Do not treat moving Toggle List selection as activating its behavior."""
        project = GuiProject.create("Toggle list events")
        element = GuiElement.create("native", 1)
        element.native_widget = "toggle_list"
        element.widget_items = ["One", "Two"]
        project.screens[0].elements.append(element)
        with patch.dict(sys.modules, native_picoware_modules()):
            ui_class, unused_assets = execute_generated_ui(project)
        del unused_assets
        ui = ui_class(types.SimpleNamespace(draw=RecordingDraw()))
        widget = ui._ensure_native(element.id)
        widget.run = lambda: setattr(widget, "selected_index", 1) or True

        event_id, consumed = ui.handle_input(2)

        self.assertIsNone(event_id)
        self.assertTrue(consumed)
        widget.run = lambda: setattr(widget, "current_state", True) or True
        event_id, consumed = ui.handle_input(5)
        self.assertEqual(event_id, element.event_id)
        self.assertTrue(consumed)

    def test_screen_without_focus_and_unsupported_connection_blocks_generation(
        self,
    ) -> None:
        """Report a conditioned relation instead of silently omitting it."""
        project = GuiProject.create("Empty")
        second = ScreenDesign.create("Other", 320, 320, 1)
        project.screens.append(second)
        connection = FlowConnection.create(project.screens[0].id, second.id, "open")
        button = GuiElement.create("button", 1)
        button.event_id = "event_open"
        project.screens[0].elements.append(button)
        connection.source_element_id = button.id
        connection.trigger_event_id = button.event_id
        connection.condition = "is_ready"
        project.connections.append(connection)
        with self.assertRaisesRegex(GeneratedAppError, "unsupported Condition"):
            execute_generated_ui(project)

    def test_arbitrary_asset_resize_requires_an_explicit_bake(self) -> None:
        """Reject silent fractional or distorted runtime scaling."""
        project = golden_project()
        project.screens[0].elements[0].width = 7
        with self.assertRaisesRegex(GeneratedAppError, "scale or bake"):
            generate_ui_module(project)

    def test_preflight_collects_multiple_generator_errors_in_one_report(self) -> None:
        """Return actionable IDs for all known blockers instead of failing at the first."""
        project = golden_project()
        project.start_screen_id = "missing-screen"
        project.screens[0].elements[0].width = 7
        project.screens[0].elements[0].height = 5
        project.screens[0].elements[1].event_id = ""
        project.screens[0].elements[1].focusable = True
        diagnostics = project_preflight_diagnostics(project)
        codes = {item.code for item in diagnostics if item.severity == "error"}
        self.assertIn("missing-start-screen", codes)
        self.assertIn("invalid-asset-scale", codes)
        self.assertIn("missing-event-id", codes)
        self.assertTrue(
            any(
                item.target_kind == "element" and item.target_id
                for item in diagnostics
            )
        )

    def test_preflight_rejects_unreachable_screen_level_navigation_event(self) -> None:
        """Do not generate a relation whose trigger no runtime input can emit."""
        project = GuiProject.create("Unreachable route")
        target = ScreenDesign.create("Target", 320, 320, 1)
        project.screens.append(target)
        route = FlowConnection.create(project.screens[0].id, target.id, "Magic")
        route.trigger_event_id = "event_magic"
        project.connections.append(route)

        codes = {
            item.code
            for item in project_preflight_diagnostics(project)
            if item.severity == "error"
        }

        self.assertIn("unreachable-navigation-event", codes)

    def test_create_once_scaffolds_parse_and_have_no_invented_branches(self) -> None:
        """Generate only reusable lifecycle plumbing and one behavior extension point."""
        entrypoint = generate_entrypoint("Status Demo", "status_demo")
        package_init = generate_package_init("Status Demo")
        app = generate_app_scaffold()
        for source in (entrypoint, package_init, app):
            ast.parse(source)
        self.assertIn("from status_demo.app import Application", entrypoint)
        self.assertEqual(package_init, "# Status Demo application package.\n")
        self.assertEqual(app.count("def handle_event"), 1)
        self.assertNotIn("event_refresh_status", app)


class GeneratedPatchSetTests(unittest.TestCase):
    """Verify generated-file ownership, preflight, and rollback semantics."""

    def test_first_apply_creates_eight_and_second_generation_is_empty(self) -> None:
        """Create code plus binary assets, then produce no regeneration diff."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            first = build_generated_app_patchset(project, folder)
            self.assertEqual([patch.action for patch in first.patches], ["create"] * 8)
            report = apply_generated_app_patchset(first)
            self.assertEqual(len(report.created), 8)
            second = build_generated_app_patchset(project, folder)
            self.assertEqual(
                [patch.action for patch in second.patches],
                [
                    "preserve",
                    "preserve",
                    "preserve",
                    "preserve",
                    "unchanged",
                    "unchanged",
                    "unchanged",
                    "unchanged",
                ],
            )
            self.assertEqual(second.changed_patches(), ())

    def test_later_handler_stub_is_added_without_rewriting_existing_body(self) -> None:
        """Append missing stubs while preserving developer implementation text."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            first_handler = FlowNode.create("action", 1)
            first_handler.set_operation("custom.handler")
            first_handler.properties["handler"] = "on_first_handler"
            project.behavior_nodes.append(first_handler)
            apply_generated_app_patchset(build_generated_app_patchset(project, folder))
            paths = resolve_generated_app_paths(project.name, folder)
            original = paths.behavior_handlers.read_text(encoding="utf-8")
            implemented = original.replace(
                'raise NotImplementedError("Behavior handler is not implemented")',
                'return {"implemented": True}',
            )
            paths.behavior_handlers.write_text(implemented, encoding="utf-8")
            second_handler = FlowNode.create("action", 2)
            second_handler.set_operation("custom.handler")
            second_handler.properties["handler"] = "on_second_handler"
            project.behavior_nodes.append(second_handler)

            patchset = build_generated_app_patchset(project, folder)
            patch = next(
                item
                for item in patchset.patches
                if item.path == paths.behavior_handlers
            )

            self.assertEqual(patch.action, "regenerate")
            self.assertIn('return {"implemented": True}', patch.updated)
            self.assertIn("def on_second_handler", patch.updated)

    def test_individual_asset_mode_writes_one_file_per_resource(self) -> None:
        """Deploy readable replaceable resources without a combined PGA file."""
        project = golden_project()
        project.generated_app["asset_storage"] = ASSET_STORAGE_INDIVIDUAL
        with tempfile.TemporaryDirectory() as folder:
            first = build_generated_app_patchset(project, folder)
            self.assertEqual(first.asset_resource.storage_mode, "individual")
            relative_paths = {
                patch.path.relative_to(first.paths.package).as_posix()
                for patch in first.patches
                if patch.path.is_relative_to(first.paths.package)
            }
            self.assertIn("generated_assets/_picoware_assets.pgl", relative_paths)
            self.assertIn("generated_assets/asset_status_badge_01.pga", relative_paths)
            self.assertNotIn("generated_assets.pga", relative_paths)
            report = apply_generated_app_patchset(first)
            self.assertTrue(report.created)
            self.assertFalse(first.paths.generated_asset_data.exists())

            namespace = {
                "__file__": str(first.paths.generated_assets),
            }
            exec(first.paths.generated_assets.read_text(), namespace)
            self.assertTrue(namespace["has_asset"]("asset_status_badge_01"))
            self.assertEqual(namespace["asset_size"]("asset_status_badge_01"), (4, 3))
            second = build_generated_app_patchset(project, folder)
            self.assertEqual(second.changed_patches(), ())

    def test_individual_mode_reviews_and_deletes_removed_resources(self) -> None:
        """Keep owned loose output synchronized without hiding destructive cleanup."""
        project = golden_project()
        project.generated_app["asset_storage"] = ASSET_STORAGE_INDIVIDUAL
        with tempfile.TemporaryDirectory() as folder:
            first = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(first)
            asset_id = project.assets[0].id
            resource = first.paths.package / "generated_assets" / f"{asset_id}.pga"
            self.assertTrue(resource.is_file())
            for screen in project.screens:
                for element in screen.elements:
                    if element.asset_id == asset_id:
                        element.asset_id = ""

            second = build_generated_app_patchset(project, folder)
            deletion = next(
                patch for patch in second.patches if patch.action == "delete"
            )
            self.assertEqual(deletion.path, resource)
            self.assertIn("Delete generated resource", deletion.diff)
            report = apply_generated_app_patchset(second)
            self.assertEqual(report.deleted, (resource,))
            self.assertFalse(resource.exists())

            namespace = {"__file__": str(first.paths.generated_assets)}
            exec(first.paths.generated_assets.read_text(), namespace)
            self.assertFalse(namespace["has_asset"](asset_id))
            self.assertEqual(
                build_generated_app_patchset(project, folder).changed_patches(), ()
            )

    def test_stale_resource_delete_is_restored_when_the_transaction_fails(self) -> None:
        """Include reviewed removals in the same backup and rollback guarantee."""
        project = golden_project()
        project.generated_app["asset_storage"] = ASSET_STORAGE_INDIVIDUAL
        with tempfile.TemporaryDirectory() as folder:
            first = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(first)
            asset_id = project.assets[0].id
            resource = first.paths.package / "generated_assets" / f"{asset_id}.pga"
            before = hashlib.sha256(resource.read_bytes()).hexdigest()
            for screen in project.screens:
                for element in screen.elements:
                    if element.asset_id == asset_id:
                        element.asset_id = ""
            second = build_generated_app_patchset(project, folder)

            def delete_then_fail(target: Path) -> None:
                target.unlink()
                raise OSError("injected delete failure")

            with patch.object(generated_app, "_delete_file", delete_then_fail):
                with self.assertRaisesRegex(GeneratedAppError, "rolled back"):
                    apply_generated_app_patchset(second)
            self.assertEqual(hashlib.sha256(resource.read_bytes()).hexdigest(), before)

    def test_output_package_symlink_is_rejected_before_review(self) -> None:
        """Never let a generated package escape through an existing directory link."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "destination"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            (destination / "status_demo").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(GeneratedAppError, "symbolic link"):
                build_generated_app_patchset(golden_project(), destination)
            self.assertEqual(list(outside.iterdir()), [])

    def test_output_package_symlink_added_after_review_blocks_apply(self) -> None:
        """Recheck path containment immediately before the reviewed transaction."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "destination"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            patchset = build_generated_app_patchset(golden_project(), destination)
            patchset.paths.package.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(GeneratedAppError, "symbolic link"):
                apply_generated_app_patchset(patchset, root / "backups")
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse(patchset.paths.entrypoint.exists())

    def test_live_preview_uses_the_persisted_individual_asset_mode(self) -> None:
        """Exercise the selected storage mode in Simulator as well as export."""
        project = golden_project()
        project.generated_app["asset_storage"] = ASSET_STORAGE_INDIVIDUAL
        bundle = build_live_preview_bundle(project, project.start_screen_id)
        names = {name for name, unused_content in bundle.files}
        self.assertIn(
            "gui_designer_live/generated_assets/asset_status_badge_01.pga", names
        )
        self.assertNotIn("gui_designer_live/generated_assets.pga", names)

    def test_individual_asset_mode_does_not_adopt_an_unmarked_folder(self) -> None:
        """Require the project marker before replacing existing loose files."""
        project = golden_project()
        project.generated_app["asset_storage"] = ASSET_STORAGE_INDIVIDUAL
        with tempfile.TemporaryDirectory() as folder:
            paths = resolve_generated_app_paths(project.name, folder)
            collision = paths.package / "generated_assets" / "asset_status_badge_01.pga"
            collision.parent.mkdir(parents=True)
            collision.write_bytes(b"unrelated")
            patchset = build_generated_app_patchset(project, folder)
            self.assertTrue(patchset.blocked)
            blocked = {patch.path: patch for patch in patchset.patches}
            self.assertEqual(blocked[collision].action, "conflict")

    def test_developer_owned_files_are_preserved_byte_for_byte(self) -> None:
        """Retain user edits to all three create-once surfaces."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            first = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(first)
            paths = first.paths
            changed = {
                paths.entrypoint: paths.entrypoint.read_text() + "\n# integration\n",
                paths.package_init: paths.package_init.read_text() + "# init edit\n",
                paths.app: paths.app.read_text() + "\n# user behavior\n",
            }
            for path, source in changed.items():
                path.write_text(source, encoding="utf-8")
            second = build_generated_app_patchset(project, folder)
            self.assertEqual(
                [patch.action for patch in second.patches[:3]], ["preserve"] * 3
            )
            apply_generated_app_patchset(second)
            for path, source in changed.items():
                self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_established_project_preserves_fully_rewritten_create_once_files(
        self,
    ) -> None:
        """Use the generated module identity after scaffold notices are removed."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            first = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(first)
            replacements = {
                first.paths.entrypoint: "# custom entrypoint\nvalue = 1\n",
                first.paths.package_init: "# custom package initialization\n",
                first.paths.app: "# custom application behavior\nvalue = 2\n",
            }
            for path, source in replacements.items():
                path.write_text(source, encoding="utf-8")
            second = build_generated_app_patchset(project, folder)
            self.assertEqual(
                [item.action for item in second.patches[:3]], ["preserve"] * 3
            )
            apply_generated_app_patchset(second)
            for path, source in replacements.items():
                self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_unrecognized_collision_blocks_every_write(self) -> None:
        """Refuse to adopt a preexisting unrelated output file."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            collision = root / "Status Demo.py"
            collision.write_text("# unrelated\n", encoding="utf-8")
            patchset = build_generated_app_patchset(golden_project(), root)
            self.assertTrue(patchset.blocked)
            self.assertEqual(patchset.patches[0].action, "conflict")
            with self.assertRaises(GeneratedAppError):
                apply_generated_app_patchset(patchset)
            self.assertEqual(list(root.iterdir()), [collision])

    def test_unknown_generated_structure_blocks_regeneration(self) -> None:
        """Do not rewrite a future editor-owned module version."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            first = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(first)
            path = first.paths.generated_ui
            source = path.read_text(encoding="utf-8").replace(
                "structure=1", "structure=99", 1
            )
            path.write_text(source, encoding="utf-8")
            second = build_generated_app_patchset(project, folder)
            ui_patch = next(item for item in second.patches if item.role == "ui")
            self.assertEqual(ui_patch.action, "unsupported-version")
            with self.assertRaises(GeneratedAppError):
                apply_generated_app_patchset(second)

    def test_unrecognized_binary_asset_resource_blocks_regeneration(self) -> None:
        """Never overwrite an unrelated sidecar that merely has the expected name."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            first = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(first)
            first.paths.generated_asset_data.write_bytes(b"unrelated binary data")
            second = build_generated_app_patchset(project, folder)
            resource_patch = next(
                item for item in second.patches if item.role == "asset-data"
            )
            self.assertEqual(resource_patch.action, "conflict")
            with self.assertRaises(GeneratedAppError):
                apply_generated_app_patchset(second)
            self.assertEqual(
                first.paths.generated_asset_data.read_bytes(),
                b"unrelated binary data",
            )

    def test_source_changed_after_review_blocks_apply(self) -> None:
        """Require review fingerprints to still match immediately before writing."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            patchset = build_generated_app_patchset(project, folder)
            patchset.paths.root.mkdir(parents=True, exist_ok=True)
            patchset.paths.entrypoint.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(GeneratedAppError, "changed after review"):
                apply_generated_app_patchset(patchset)

    def test_mid_replacement_failure_restores_complete_prior_set(self) -> None:
        """Restore every replaced module when a later replacement fails."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            first = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(first)
            before = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in first.paths.all_files()
            }
            project.screens[0].background_color = 0x001F
            project.assets[0].frames[0][0] = 0x07E0
            second = build_generated_app_patchset(project, folder)
            real_replace = generated_app._replace_file
            calls = 0

            def fail_second(source: Path, target: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected replacement failure")
                real_replace(source, target)

            with patch.object(generated_app, "_replace_file", fail_second):
                with self.assertRaisesRegex(GeneratedAppError, "rolled back"):
                    apply_generated_app_patchset(second)
            after = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in first.paths.all_files()
            }
            self.assertEqual(after, before)

    def test_failure_before_first_replacement_leaves_destination_empty(self) -> None:
        """Remove prepared files, package directories, and transaction directories."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "destination"
            destination.mkdir()
            patchset = build_generated_app_patchset(golden_project(), destination)
            with patch.object(
                generated_app,
                "_replace_file",
                side_effect=OSError("injected pre-replacement failure"),
            ):
                with self.assertRaisesRegex(GeneratedAppError, "rolled back"):
                    apply_generated_app_patchset(patchset, root / "backups")
            self.assertEqual(list(destination.iterdir()), [])
            self.assertFalse((root / "backups").exists())

    def test_generated_files_import_and_scaffold_lifecycle_runs(self) -> None:
        """Prove the generated base starts, navigates, tolerates, and stops."""
        with tempfile.TemporaryDirectory() as folder:
            project = golden_project()
            patchset = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(patchset)
            for path in patchset.paths.all_files()[:-1]:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertGreater(patchset.paths.generated_asset_data.stat().st_size, 4)

            buttons = types.ModuleType("picoware.system.buttons")
            for index, name in enumerate(
                (
                    "BUTTON_BACK",
                    "BUTTON_CENTER",
                    "BUTTON_DOWN",
                    "BUTTON_LEFT",
                    "BUTTON_RIGHT",
                    "BUTTON_UP",
                )
            ):
                setattr(buttons, name, index)
            picoware = types.ModuleType("picoware")
            picoware.__path__ = []
            system = types.ModuleType("picoware.system")
            system.__path__ = []
            saved_modules = {
                name: sys.modules.get(name)
                for name in ("picoware", "picoware.system", "picoware.system.buttons")
            }
            old_path = list(sys.path)
            try:
                sys.modules["picoware"] = picoware
                sys.modules["picoware.system"] = system
                sys.modules["picoware.system.buttons"] = buttons
                sys.path.insert(0, folder)
                app_module = importlib.import_module("status_demo.app")

                class Input:
                    button = -1

                    def reset(self) -> None:
                        self.button = -1

                class View:
                    def __init__(self) -> None:
                        self.draw = RecordingDraw()
                        self.input_manager = Input()
                        self.back_calls = 0

                    def back(self) -> None:
                        self.back_calls += 1

                view = View()
                application = app_module.Application()
                self.assertTrue(application.start(view))
                application.ui.move_focus(1)
                self.assertEqual(
                    application.ui.activate_focused(), "event_refresh_status_01"
                )
                application.ui.move_focus(-1)
                self.assertEqual(
                    application.ui.activate_focused(), "event_open_settings_01"
                )
                self.assertEqual(application.ui.screen_id, "screen_settings_01")
                self.assertTrue(
                    application.ui.handle_navigation("event_navigation_back_01")
                )
                application.stop(view)
                self.assertIsNone(application.ui)
            finally:
                sys.path[:] = old_path
                for name in list(sys.modules):
                    if name == "status_demo" or name.startswith("status_demo."):
                        sys.modules.pop(name, None)
                for name, previous in saved_modules.items():
                    if previous is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous


if __name__ == "__main__":
    unittest.main()
