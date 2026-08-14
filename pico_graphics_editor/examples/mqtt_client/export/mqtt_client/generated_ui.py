# @picoware-generated structure=1
# @picoware-generated role=ui
# @picoware-generated project=project_13106341
# @picoware-generator version=1.1.0
# This file is editor-owned. Regenerate it instead of editing it manually.

from .generated_assets import draw_asset
from picoware.gui.alert import Alert as PicowareAlert
from picoware.gui.menu import Menu as PicowareMenu
from picoware.gui.textbox import TextBox as PicowareTextBox
from picoware.gui.toggle import Toggle as PicowareToggle
from picoware.system.vector import Vector
from picoware.system.buttons import (BUTTON_BACK, BUTTON_CENTER, BUTTON_DOWN, BUTTON_LEFT, BUTTON_RIGHT, BUTTON_UP)


class GeneratedUI:
    """Render generated screens and structural navigation."""

    FLOW_STANDARD_VERSION = 2
    FLOW_NODES = (('node_2ef5c21c', 'event', 'Connect requested', 'on_connect_requested_7adb31', 'Stable dashboard event requests an MQTT session transition.', (('event', 'Event', 'out', 'event', False, True), ('value', 'Value', 'out', 'any', False, True), ('text', 'Text', 'out', 'string', False, True), ('checked', 'Checked', 'out', 'boolean', False, True), ('index', 'Index', 'out', 'integer', False, True)), {'event': 'connect_toggle'}, False), ('node_7c884667', 'action', 'Open MQTT session', 'on_open_mqtt_session_800acc', 'Developer logic opens a real socket on device or deterministic mock transport in the simulator.', (('in', 'Run', 'in', 'event', True, False), ('done', 'Done', 'out', 'event', False, False)), {'handler': 'connect_or_disconnect', 'timeout_ms': 3000}, False), ('node_64a88fd3', 'event', 'Publish requested', 'on_publish_requested_d46cd4', 'Stable dashboard event requests one MQTT test publication.', (('event', 'Event', 'out', 'event', False, True), ('value', 'Value', 'out', 'any', False, True), ('text', 'Text', 'out', 'string', False, True), ('checked', 'Checked', 'out', 'boolean', False, True), ('index', 'Index', 'out', 'integer', False, True)), {'event': 'publish_test'}, False), ('node_762c8c46', 'action', 'Publish test payload', 'on_publish_test_payload_fda0b0', 'Developer logic publishes JSON to the active topic and records loopback messages.', (('in', 'Run', 'in', 'event', True, False), ('success', 'Success', 'out', 'event', False, True), ('error', 'Error', 'out', 'event', False, True), ('cancel', 'Cancel', 'out', 'event', False, True)), {'payload': '{"client":"picoware","seq":1}', 'retain': False, 'topic': 'demo/picoware/test'}, False), ('node_ac2f9816', 'action', 'Refresh dashboard status', 'on_refresh_dashboard_status_79e012', 'Developer logic redraws connection, broker, topic, and last-message state.', (('in', 'Run', 'in', 'event', True, False), ('done', 'Done', 'out', 'event', False, False)), {'element_id': 'element_0efdd39b', 'text': 'Connected'}, False), ('node_3029dc8e', 'action', 'Record received message', 'on_record_received_message_20c5c5', 'Developer logic stores the bounded message history and updates the native inbox.', (('in', 'Run', 'in', 'event', True, False), ('changed', 'Changed', 'out', 'event', False, True), ('error', 'Error', 'out', 'event', False, True)), {'key': 'messages', 'value': ''}, False), ('node_mqtt_inbox_ui', 'action', 'Update message inbox', 'on_update_message_inbox_35c0e8', 'Built-in UI operation updates the native Message Inbox TextBox.', (('in', 'Run', 'in', 'event', True, False), ('done', 'Done', 'out', 'event', False, True), ('error', 'Error', 'out', 'event', False, True)), {'element_id': 'element_2072e2d3', 'text': ''}, False))
    FLOW_CONNECTIONS = (('behavior_e2cc4e2f', 'node_2ef5c21c', 'event', 'node_7c884667', 'in', 'connect', ''), ('behavior_8cc07038', 'node_64a88fd3', 'event', 'node_762c8c46', 'in', 'publish', ''), ('behavior_a4b59d53', 'node_7c884667', 'done', 'node_ac2f9816', 'in', 'status', ''), ('behavior_60491876', 'node_762c8c46', 'success', 'node_3029dc8e', 'in', 'message', ''), ('behavior_mqtt_inbox_update', 'node_3029dc8e', 'changed', 'node_mqtt_inbox_ui', 'in', 'update inbox', ''))
    FLOW_GROUPS = ()

    ELEMENT_IDS = ('element_40989fb8', 'element_d3ad5375', 'element_061228c0', 'element_0efdd39b', 'element_3ae5edb4', 'element_9e44b9b1', 'element_0eca0acd', 'element_3a3002b8', 'element_c530b095', 'element_27c67d64', 'element_7687afcd', 'element_820dce61', 'element_8c2e7db4', 'element_b589ab8f', 'element_2072e2d3', 'element_e8210d68')
    ELEMENT_EVENTS = {'element_40989fb8': 'event_f4bb9f04', 'element_d3ad5375': 'event_0f7a2132', 'element_061228c0': 'event_60291787', 'element_0efdd39b': 'event_f2f74b28', 'element_3ae5edb4': 'event_df018a45', 'element_9e44b9b1': 'event_9fb789e4', 'element_0eca0acd': 'event_38127f9e', 'element_3a3002b8': 'event_737e713e', 'element_c530b095': 'event_997e0c73', 'element_27c67d64': 'event_221ec78f', 'element_7687afcd': 'event_d709ea79', 'element_820dce61': 'event_fd893251', 'element_8c2e7db4': 'event_69a37a41', 'element_b589ab8f': 'event_6ce5fe7f', 'element_2072e2d3': 'event_80e56693', 'element_e8210d68': 'event_62df3cb5'}

    ELEMENT_DEFAULT_VALUES = {'element_40989fb8': None, 'element_d3ad5375': None, 'element_061228c0': 'MQTT POCKET', 'element_0efdd39b': 'OFFLINE · mock broker', 'element_3ae5edb4': 50, 'element_9e44b9b1': 'CONNECT', 'element_0eca0acd': 'PUBLISH TEST', 'element_3a3002b8': 'BROKER', 'element_c530b095': 'TOPICS', 'element_27c67d64': 'INBOX', 'element_7687afcd': 'ABOUT', 'element_820dce61': '', 'element_8c2e7db4': None, 'element_b589ab8f': None, 'element_2072e2d3': None, 'element_e8210d68': None}
    ELEMENT_WIDGET_TYPES = {'element_40989fb8': 'toggle', 'element_d3ad5375': 'rectangle', 'element_061228c0': 'label', 'element_0efdd39b': 'label', 'element_3ae5edb4': 'progress', 'element_9e44b9b1': 'button', 'element_0eca0acd': 'button', 'element_3a3002b8': 'button', 'element_c530b095': 'button', 'element_27c67d64': 'button', 'element_7687afcd': 'button', 'element_820dce61': 'icon', 'element_8c2e7db4': 'keyboard', 'element_b589ab8f': 'menu', 'element_2072e2d3': 'textbox', 'element_e8210d68': 'alert'}

    def __init__(self, context):
        self.view_manager = context if hasattr(context, 'draw') else None
        self.draw = context.draw if self.view_manager is not None else context
        self._native_widgets = {}
        self._element_values = {}
        self._element_visibility = {}
        self._element_enabled = {}
        self.screen_id = 'screen_f508214b'
        self.focus_index = 0
        self.last_transition = "replace"

    def render(self):
        """Draw the active screen and focus indicator."""
        if self.screen_id == 'screen_f508214b':
            self._draw_screen_f508214b()
        elif self.screen_id == 'screen_3fa8175f':
            self._draw_screen_3fa8175f()
        elif self.screen_id == 'screen_5a6979b6':
            self._draw_screen_5a6979b6()
        elif self.screen_id == 'screen_6997f9f9':
            self._draw_screen_6997f9f9()
        elif self.screen_id == 'screen_ae260ecc':
            self._draw_screen_ae260ecc()
        self._draw_focus()

    def set_screen(self, screen_id):
        """Select a known screen by stable ID."""
        if screen_id not in ('screen_f508214b', 'screen_3fa8175f', 'screen_5a6979b6', 'screen_6997f9f9', 'screen_ae260ecc'):
            return False
        self.screen_id = screen_id
        self.focus_index = 0
        return True

    def focused_event(self):
        """Return the focused element stable event ID."""
        events = self._focusable_events()
        if not events:
            return None
        self.focus_index %= len(events)
        return events[self.focus_index]

    def move_focus(self, step):
        """Move focus within the active screen."""
        if self._active_native_owns_screen():
            return self._move_native(step)
        events = self._focusable_events()
        if not events:
            return None
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = 0
        self.focus_index = (self.focus_index + step) % len(events)
        return events[self.focus_index]

    def activate_focused(self):
        """Apply structural navigation and return the activation event."""
        if self._active_native_id() is not None:
            return self._activate_native()
        event_id = self.focused_event()
        if event_id is not None:
            self.handle_navigation(event_id)
        return event_id

    def handle_navigation(self, event_id):
        """Apply one declared screen-flow connection."""
        if (
            self.screen_id == 'screen_f508214b'
            and event_id == 'event_737e713e'
        ):
            self.screen_id = 'screen_3fa8175f'
            self.focus_index = 0
            self.last_transition = 'replace'
            return True
        if (
            self.screen_id == 'screen_3fa8175f'
            and event_id == 'event_69a37a41'
        ):
            self.screen_id = 'screen_f508214b'
            self.focus_index = 0
            self.last_transition = 'replace'
            return True
        if (
            self.screen_id == 'screen_f508214b'
            and event_id == 'event_997e0c73'
        ):
            self.screen_id = 'screen_5a6979b6'
            self.focus_index = 0
            self.last_transition = 'replace'
            return True
        if (
            self.screen_id == 'screen_5a6979b6'
            and event_id == 'event_6ce5fe7f'
        ):
            self.screen_id = 'screen_f508214b'
            self.focus_index = 0
            self.last_transition = 'replace'
            return True
        if (
            self.screen_id == 'screen_f508214b'
            and event_id == 'event_221ec78f'
        ):
            self.screen_id = 'screen_6997f9f9'
            self.focus_index = 0
            self.last_transition = 'replace'
            return True
        if (
            self.screen_id == 'screen_f508214b'
            and event_id == 'event_d709ea79'
        ):
            self.screen_id = 'screen_ae260ecc'
            self.focus_index = 0
            self.last_transition = 'replace'
            return True
        return False

    def behavior_contracts(self):
        """Return generated structural behavior contracts."""
        return {
            "standard": self.FLOW_STANDARD_VERSION,
            "nodes": self.FLOW_NODES,
            "connections": self.FLOW_CONNECTIONS,
            "groups": self.FLOW_GROUPS,
        }

    def describe_behavior_contract(self, node_id, context=None):
        """Describe one structural contract without executing it."""
        context = {} if context is None else context
        for record in self.FLOW_NODES:
            if record[0] == node_id:
                return {
                    "node_id": node_id,
                    "stub": record[3],
                    "context": context,
                    "implemented": False,
                }
        return None

    def navigate(self, screen_id):
        """Navigate to one stable screen ID."""
        return self.set_screen(screen_id)

    def back(self):
        """Apply the declared Back navigation event."""
        return self.handle_navigation("event_navigation_back_01")

    def set_value(self, element_id, value):
        """Update one native widget through supported public surfaces."""
        widget = self._ensure_native(element_id)
        if widget is None and element_id in self.ELEMENT_IDS:
            self._element_values[element_id] = value
            return True
        if widget is None:
            return False
        setter = getattr(widget, "set_value", None)
        if callable(setter):
            setter(value)
        elif hasattr(widget, "text"):
            widget.text = value
        elif hasattr(widget, "value"):
            widget.value = value
        elif hasattr(widget, "state"):
            widget.state = value
        else:
            return False
        return True

    def set_text(self, element_id, text):
        return self.set_value(element_id, text)

    def set_progress(self, element_id, value):
        return self.set_value(element_id, value)

    def read_value(self, element_id):
        value = self.native_value(element_id)
        if value is None:
            value = self.ELEMENT_DEFAULT_VALUES.get(element_id)
        return self._element_values.get(element_id, value)

    def read_index(self, element_id):
        """Return a public selected index when the widget exposes one."""
        widget = self._ensure_native(element_id)
        if widget is None:
            return None
        widget_type = self.ELEMENT_WIDGET_TYPES.get(element_id)
        if widget_type == 'choice':
            return widget.state
        if widget_type in ('menu', 'list', 'toggle_list'):
            return widget.selected_index
        return None

    def widget_type(self, element_id):
        return self.ELEMENT_WIDGET_TYPES.get(element_id, '')

    def alert(self, message):
        for element_id in self._native_widgets:
            widget = self._native_widgets[element_id]
            if hasattr(widget, "message"):
                widget.message = message
                return True
        return False

    def show(self, element_id):
        if element_id not in self.ELEMENT_IDS:
            return False
        self._element_visibility[element_id] = True
        return True

    def hide(self, element_id):
        if element_id not in self.ELEMENT_IDS:
            return False
        self._element_visibility[element_id] = False
        return True

    def enable(self, element_id, enabled=True):
        if element_id not in self.ELEMENT_IDS:
            return False
        self._element_enabled[element_id] = bool(enabled)
        widget = self._ensure_native(element_id)
        if widget is None:
            return True
        if hasattr(widget, "enabled"):
            widget.enabled = enabled
        return True

    def focus(self, element_id):
        target_event = self.ELEMENT_EVENTS.get(element_id)
        events = self._focusable_events()
        for index, event_id in enumerate(events):
            if event_id == target_event:
                self.focus_index = index
                return True
        return False


    def _active_native_id(self):
        """Return the screen widget or focused inline native control."""
        screen_widget = {'screen_f508214b': None, 'screen_3fa8175f': 'element_8c2e7db4', 'screen_5a6979b6': 'element_b589ab8f', 'screen_6997f9f9': 'element_2072e2d3', 'screen_ae260ecc': None}.get(self.screen_id)
        if screen_widget is not None:
            return screen_widget
        event_id = self.focused_event()
        for candidate_event, element_id in {'screen_f508214b': (('event_f4bb9f04', 'element_40989fb8'),), 'screen_3fa8175f': (), 'screen_5a6979b6': (), 'screen_6997f9f9': (), 'screen_ae260ecc': ()}.get(self.screen_id, ()):
            if candidate_event == event_id:
                return element_id
        return None

    def _active_native_owns_screen(self):
        """Return whether the active native widget owns screen input."""
        return self._active_native_id() in ('element_8c2e7db4', 'element_b589ab8f', 'element_2072e2d3')

    def _ensure_native(self, element_id):
        """Create one Picoware widget lazily for its active screen."""
        if element_id in self._native_widgets:
            return self._native_widgets[element_id]
        widget = None
        if element_id == 'element_40989fb8':
            widget = PicowareToggle(self.draw, Vector(16, 274), Vector(288, 34), 'Retain publishes', False, 0xFFFF, 0x0948, 0x16B9, 0xFFFF, 1)
        elif element_id == 'element_8c2e7db4':
            if self.view_manager is not None:
                widget = self.view_manager.keyboard
                widget.reset()
                widget.title = 'Broker host:port'
        elif element_id == 'element_b589ab8f':
            widget = PicowareMenu(self.draw, 'Subscriptions', 0, 320, 0xFFFF, 0x08A4, 0x16B9, 0xFFFF, 2)
            widget.add_item('Subscribe demo/sensor')
            widget.add_item('Subscribe demo/alerts')
            widget.add_item('Clear subscriptions')
            widget.set_selected(0)
        elif element_id == 'element_2072e2d3':
            widget = PicowareTextBox(self.draw, 0, 320, 0xFFFF, 0x08A4, True)
            widget.current_text = 'No MQTT messages yet. Connect and publish a test message from Dashboard.'
        elif element_id == 'element_e8210d68':
            widget = PicowareAlert(self.draw, 'MQTT Pocket uses MQTT 3.1.1 QoS 0. The simulator runs a deterministic loopback broker.', 0xFFFF, 0x08A4)
        if widget is not None:
            self._native_widgets[element_id] = widget
        return widget

    def _render_native(self, element_id):
        """Render one real Picoware widget through its public API."""
        widget = self._ensure_native(element_id)
        if widget is None:
            return
        if element_id == 'element_40989fb8':
            widget.draw()
        elif element_id == 'element_8c2e7db4':
            widget.run(force=True)
        elif element_id == 'element_b589ab8f':
            widget.draw()
        elif element_id == 'element_2072e2d3':
            widget.refresh()
        elif element_id == 'element_e8210d68':
            widget.draw('About Alert')

    def _move_native(self, step):
        """Move selection inside the active native widget."""
        element_id = self._active_native_id()
        widget = self._ensure_native(element_id) if element_id else None
        if widget is None:
            return None
        if element_id == 'element_b589ab8f':
            if step < 0:
                widget.scroll_up()
            elif step > 0:
                widget.scroll_down()
            return 'event_6ce5fe7f'
        return None

    def _activate_native(self):
        """Activate the selected value of the active native widget."""
        element_id = self._active_native_id()
        widget = self._ensure_native(element_id) if element_id else None
        if widget is None:
            return None
        if element_id == 'element_40989fb8':
            widget.state = not widget.state
            event_id = 'event_f4bb9f04'
            self.handle_navigation(event_id)
            return event_id
        elif element_id == 'element_8c2e7db4':
            event_id = 'event_69a37a41'
            self.handle_navigation(event_id)
            return event_id
        elif element_id == 'element_b589ab8f':
            event_id = 'event_6ce5fe7f'
            self.handle_navigation(event_id)
            return event_id
        elif element_id == 'element_2072e2d3':
            event_id = 'event_80e56693'
            self.handle_navigation(event_id)
            return event_id
        return None

    def handle_input(self, button):
        """Let a native widget consume one Picoware input value."""
        element_id = self._active_native_id()
        if element_id is None:
            return None, False
        widget = self._ensure_native(element_id)
        if widget is None:
            return None, False
        if button == BUTTON_BACK:
            return None, False
        if element_id == 'element_40989fb8':
            if button == BUTTON_CENTER:
                widget.state = not widget.state
                event_id = 'event_f4bb9f04'
                self.handle_navigation(event_id)
                return event_id, True
            return None, False
        elif element_id == 'element_8c2e7db4':
            running = widget.run()
            if not running and getattr(widget, 'is_finished', False):
                event_id = 'event_69a37a41'
                self.handle_navigation(event_id)
                return event_id, True
            return None, True
        elif element_id == 'element_b589ab8f':
            if button in (BUTTON_UP, BUTTON_LEFT):
                widget.scroll_up()
            elif button in (BUTTON_DOWN, BUTTON_RIGHT):
                widget.scroll_down()
            elif button == BUTTON_CENTER:
                event_id = 'event_6ce5fe7f'
                self.handle_navigation(event_id)
                return event_id, True
            return None, True
        elif element_id == 'element_2072e2d3':
            if button in (BUTTON_UP, BUTTON_LEFT):
                widget.scroll_up()
            elif button in (BUTTON_DOWN, BUTTON_RIGHT):
                widget.scroll_down()
            return None, True
        elif element_id == 'element_e8210d68':
            return None, True
        return None, True

    def native_value(self, element_id):
        """Return the current public value of one native widget."""
        widget = self._ensure_native(element_id)
        if widget is None:
            return None
        if element_id == 'element_40989fb8':
            return widget.state
        elif element_id == 'element_8c2e7db4':
            return widget.response
        elif element_id == 'element_b589ab8f':
            return widget.current_item
        elif element_id == 'element_2072e2d3':
            return widget.current_text
        elif element_id == 'element_e8210d68':
            return None
        return None
    def _focusable_events(self):
        """Return focusable events in configured order."""
        if self.screen_id == 'screen_f508214b':
            return ('event_9fb789e4', 'event_38127f9e', 'event_737e713e', 'event_997e0c73', 'event_221ec78f', 'event_d709ea79', 'event_f4bb9f04')
        elif self.screen_id == 'screen_3fa8175f':
            return ('event_69a37a41',)
        elif self.screen_id == 'screen_5a6979b6':
            return ('event_6ce5fe7f',)
        elif self.screen_id == 'screen_6997f9f9':
            return ('event_80e56693',)
        elif self.screen_id == 'screen_ae260ecc':
            return ()
        return ()

    def _draw_screen_f508214b(self):
        """Draw the Dashboard screen."""
        self.draw._fill_rectangle(0, 0, 320, 320, 0x08A4)
        if self._element_visibility.get('element_40989fb8', True):
            self._render_native('element_40989fb8')
        if self._element_visibility.get('element_d3ad5375', True):
            self.draw._fill_rectangle(8, 8, 304, 76, 0x0948)
        if self._element_visibility.get('element_061228c0', True):
            self.draw._text(58, 16, self._element_values.get('element_061228c0', 'MQTT POCKET'), 0xFFFF)
        if self._element_visibility.get('element_0efdd39b', True):
            self.draw._text(58, 38, self._element_values.get('element_0efdd39b', 'OFFLINE · mock broker'), 0x16B9)
        if self._element_visibility.get('element_3ae5edb4', True):
            self.draw._fill_rectangle(16, 64, 288, 10, 0x08A4)
            self.draw._rectangle(16, 64, 288, 10, 0x16B9)
            self.draw._fill_rectangle(16, 64, max(1, min(288, int(self._element_values.get('element_3ae5edb4', 50)) * 288 // 100)), 10, 0x16B9)
        if self._element_visibility.get('element_9e44b9b1', True):
            self.draw._fill_rectangle(16, 96, 138, 42, 0x09AB)
            self.draw._rectangle(16, 96, 138, 42, 0x16B9)
            self.draw._text(20, 100, self._element_values.get('element_9e44b9b1', 'CONNECT'), 0xFFFF)
        if self._element_visibility.get('element_0eca0acd', True):
            self.draw._fill_rectangle(166, 96, 138, 42, 0x09AB)
            self.draw._rectangle(166, 96, 138, 42, 0x16B9)
            self.draw._text(170, 100, self._element_values.get('element_0eca0acd', 'PUBLISH TEST'), 0xFFFF)
        if self._element_visibility.get('element_3a3002b8', True):
            self.draw._fill_rectangle(16, 154, 138, 42, 0x09AB)
            self.draw._rectangle(16, 154, 138, 42, 0x16B9)
            self.draw._text(20, 158, self._element_values.get('element_3a3002b8', 'BROKER'), 0xFFFF)
        if self._element_visibility.get('element_c530b095', True):
            self.draw._fill_rectangle(166, 154, 138, 42, 0x09AB)
            self.draw._rectangle(166, 154, 138, 42, 0x16B9)
            self.draw._text(170, 158, self._element_values.get('element_c530b095', 'TOPICS'), 0xFFFF)
        if self._element_visibility.get('element_27c67d64', True):
            self.draw._fill_rectangle(16, 212, 138, 42, 0x09AB)
            self.draw._rectangle(16, 212, 138, 42, 0x16B9)
            self.draw._text(20, 216, self._element_values.get('element_27c67d64', 'INBOX'), 0xFFFF)
        if self._element_visibility.get('element_7687afcd', True):
            self.draw._fill_rectangle(166, 212, 138, 42, 0x09AB)
            self.draw._rectangle(166, 212, 138, 42, 0x16B9)
            self.draw._text(170, 216, self._element_values.get('element_7687afcd', 'ABOUT'), 0xFFFF)
        if self._element_visibility.get('element_820dce61', True):
            draw_asset(
                self.draw,
                'snapshot_element_820dce61',
                16,
                18,
                frame=0,
                scale=2,
            )

    def _draw_screen_3fa8175f(self):
        """Draw the Broker Settings screen."""
        self.draw._fill_rectangle(0, 0, 320, 320, 0x08A4)
        if self._element_visibility.get('element_8c2e7db4', True):
            self._render_native('element_8c2e7db4')

    def _draw_screen_5a6979b6(self):
        """Draw the Topic Manager screen."""
        self.draw._fill_rectangle(0, 0, 320, 320, 0x08A4)
        if self._element_visibility.get('element_b589ab8f', True):
            self._render_native('element_b589ab8f')

    def _draw_screen_6997f9f9(self):
        """Draw the Message Inbox screen."""
        self.draw._fill_rectangle(0, 0, 320, 320, 0x08A4)
        if self._element_visibility.get('element_2072e2d3', True):
            self._render_native('element_2072e2d3')

    def _draw_screen_ae260ecc(self):
        """Draw the About screen."""
        self.draw._fill_rectangle(0, 0, 320, 320, 0x08A4)
        if self._element_visibility.get('element_e8210d68', True):
            self._render_native('element_e8210d68')

    def _draw_focus(self):
        """Draw the configured focus indicator."""
        if self.screen_id == 'screen_f508214b':
            focus_index = self.focus_index % 7
            if focus_index == 0:
                self.draw._rectangle(14, 94, 142, 46, 0xFEA9)
                self.draw._rectangle(13, 93, 144, 48, 0xFEA9)
                return
            elif focus_index == 1:
                self.draw._rectangle(164, 94, 142, 46, 0xFEA9)
                self.draw._rectangle(163, 93, 144, 48, 0xFEA9)
                return
            elif focus_index == 2:
                self.draw._rectangle(14, 152, 142, 46, 0xFEA9)
                self.draw._rectangle(13, 151, 144, 48, 0xFEA9)
                return
            elif focus_index == 3:
                self.draw._rectangle(164, 152, 142, 46, 0xFEA9)
                self.draw._rectangle(163, 151, 144, 48, 0xFEA9)
                return
            elif focus_index == 4:
                self.draw._rectangle(14, 210, 142, 46, 0xFEA9)
                self.draw._rectangle(13, 209, 144, 48, 0xFEA9)
                return
            elif focus_index == 5:
                self.draw._rectangle(164, 210, 142, 46, 0xFEA9)
                self.draw._rectangle(163, 209, 144, 48, 0xFEA9)
                return
            elif focus_index == 6:
                self.draw._rectangle(14, 272, 292, 38, 0xFEA9)
                self.draw._rectangle(13, 271, 294, 40, 0xFEA9)
                return
        elif self.screen_id == 'screen_3fa8175f':
            focus_index = self.focus_index % 1
            if focus_index == 0:
                pass
                return
        elif self.screen_id == 'screen_5a6979b6':
            focus_index = self.focus_index % 1
            if focus_index == 0:
                pass
                return
        elif self.screen_id == 'screen_6997f9f9':
            focus_index = self.focus_index % 1
            if focus_index == 0:
                pass
                return
        elif self.screen_id == 'screen_ae260ecc':
            return
