# Picoware generated application scaffold.
# This file is developer-owned after its first creation.


from picoware.system.buttons import (
    BUTTON_BACK,
    BUTTON_CENTER,
    BUTTON_DOWN,
    BUTTON_LEFT,
    BUTTON_RIGHT,
    BUTTON_UP,
)

from .generated_ui import GeneratedUI
from .mqtt_transport import create_transport


EVENT_CONNECT = "event_9fb789e4"
EVENT_PUBLISH = "event_38127f9e"
EVENT_RETAIN = "event_f4bb9f04"
EVENT_BROKER_SUBMITTED = "event_69a37a41"
EVENT_TOPIC_ACTION = "event_6ce5fe7f"

DASHBOARD_SCREEN = "screen_f508214b"
BROKER_KEYBOARD = "element_8c2e7db4"
TOPIC_MENU = "element_b589ab8f"
INBOX_TEXT = "element_2072e2d3"


def _simulator_active():
    """Return True only inside Picoware's host simulator."""
    try:
        import sim_runtime

        return sim_runtime is not None
    except ImportError:
        return False


def _text(value):
    """Decode bounded MQTT values for the dashboard and inbox."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except (UnicodeError, ValueError):
            value = repr(value)
    return str(value)


class Application:
    """Own user behavior around the generated presentation."""

    def __init__(self):
        self.view_manager = None
        self.ui = None
        self.transport = None
        self.connected = False
        self.simulator = _simulator_active()
        self.broker_host = "test.mosquitto.org"
        self.broker_port = 1883
        self.current_topic = "demo/picoware/test"
        self.subscriptions = [self.current_topic]
        self.messages = []
        self.retain = False
        self.publish_count = 0
        self.status = "OFFLINE · MOCK" if self.simulator else "OFFLINE · MQTT"
        self.needs_redraw = False

    def start(self, view_manager):
        """Initialize the application base and show its start screen."""
        self.view_manager = view_manager
        self.ui = GeneratedUI(view_manager)
        self.transport = create_transport(self._on_message, self.simulator)
        self.redraw()
        view_manager.input_manager.reset()
        return True

    def run(self, view_manager):
        """Handle structural navigation and delegate activation events."""
        if self.ui is None:
            return
        input_manager = view_manager.input_manager
        if self.connected and self.transport is not None:
            try:
                self.transport.poll()
            except OSError as error:
                self.connected = False
                self.status = "CONNECTION LOST · " + _text(error)[:18]
                self.needs_redraw = True

        button = input_manager.button
        if button == -1:
            if self.needs_redraw:
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
            if self.ui.screen_id != DASHBOARD_SCREEN:
                self.ui.set_screen(DASHBOARD_SCREEN)
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
        if self.ui.screen_id == DASHBOARD_SCREEN:
            self._draw_dashboard_status(draw)
        draw.swap()
        self.needs_redraw = False

    def handle_event(self, event_id):
        """Implement application behavior for stable generated event IDs."""
        if event_id == EVENT_CONNECT:
            return self._toggle_connection()
        if event_id == EVENT_PUBLISH:
            return self._publish_test()
        if event_id == EVENT_RETAIN:
            self.retain = bool(self.ui.native_value("element_40989fb8"))
            self.status = "RETAIN · " + ("ON" if self.retain else "OFF")
            return True
        if event_id == EVENT_BROKER_SUBMITTED:
            return self._apply_broker(self.ui.native_value(BROKER_KEYBOARD))
        if event_id == EVENT_TOPIC_ACTION:
            return self._apply_topic_action(self.ui.native_value(TOPIC_MENU))
        return False

    def _toggle_connection(self):
        """Connect or disconnect the selected MQTT broker."""
        if self.connected:
            self.transport.disconnect()
            self.connected = False
            self.status = "DISCONNECTED"
            return True
        try:
            self.transport.connect(
                self.broker_host,
                self.broker_port,
                "picoware-mqtt-client",
            )
            self.connected = True
            for topic in self.subscriptions:
                self.transport.subscribe(topic)
            self.status = (
                "CONNECTED · MOCK"
                if getattr(self.transport, "is_mock", False)
                else "CONNECTED · " + self.broker_host[:18]
            )
            return True
        except (OSError, ValueError) as error:
            self.connected = False
            self.status = "CONNECT ERROR · " + _text(error)[:16]
            return False

    def _publish_test(self):
        """Publish a deterministic JSON test message to the active topic."""
        if not self.connected:
            self.status = "CONNECT FIRST"
            return False
        self.publish_count += 1
        payload = '{"client":"picoware","seq":%d}' % self.publish_count
        before = len(self.messages)
        try:
            self.transport.publish(self.current_topic, payload, self.retain)
        except (OSError, ValueError) as error:
            self.status = "PUBLISH ERROR · " + _text(error)[:16]
            return False
        if len(self.messages) == before:
            self.status = "PUBLISHED · " + self.current_topic[:18]
        return True

    def _apply_broker(self, value):
        """Apply a host or host:port response from the native Keyboard."""
        value = _text(value or "").strip()
        if not value:
            self.status = "BROKER UNCHANGED"
            return False
        host = value
        port = 1883
        if ":" in value:
            host, raw_port = value.rsplit(":", 1)
            try:
                port = int(raw_port)
            except ValueError:
                self.status = "INVALID BROKER PORT"
                return False
        if not host or port < 1 or port > 65535:
            self.status = "INVALID BROKER"
            return False
        if self.connected:
            self.transport.disconnect()
            self.connected = False
        self.broker_host = host
        self.broker_port = port
        self.status = "BROKER · " + host[:20]
        return True

    def _apply_topic_action(self, value):
        """Subscribe to a preset topic or clear the active subscription set."""
        action = _text(value or "")
        if action == "Clear subscriptions":
            for topic in tuple(self.subscriptions):
                if self.connected:
                    self.transport.unsubscribe(topic)
            self.subscriptions = []
            self.status = "SUBSCRIPTIONS CLEARED"
            return True
        prefix = "Subscribe "
        if not action.startswith(prefix):
            self.status = "UNKNOWN TOPIC ACTION"
            return False
        topic = action[len(prefix) :].strip()
        if not topic:
            return False
        self.current_topic = topic
        if topic not in self.subscriptions:
            self.subscriptions.append(topic)
            if self.connected:
                self.transport.subscribe(topic)
        self.status = "SUBSCRIBED · " + topic[:18]
        return True

    def _on_message(self, topic, payload):
        """Record one bounded received message and refresh the native inbox."""
        topic_text = _text(topic)
        payload_text = _text(payload)
        self.messages.append(topic_text + " · " + payload_text)
        self.messages = self.messages[-8:]
        self.status = "RX " + topic_text[:22]
        self.needs_redraw = True
        if self.ui is not None:
            widget = self.ui._ensure_native(INBOX_TEXT)
            if widget is not None:
                widget.current_text = "\n".join(self.messages)

    def _draw_dashboard_status(self, draw):
        """Overlay the runtime-owned MQTT status inside the editor-designed header."""
        draw._fill_rectangle(54, 34, 250, 24, 0x08A4)
        draw._text(58, 38, self.status[:32], 0x16B9)
        draw._fill_rectangle(16, 64, 288, 10, 0x08A4)
        activity = 18 if not self.connected else min(288, 54 + self.publish_count * 28)
        draw._fill_rectangle(16, 64, activity, 10, 0x16B9)

    def stop(self, view_manager):
        """Release application-owned state."""
        if self.transport is not None:
            self.transport.disconnect()
        self.transport = None
        self.connected = False
        self.ui = None
        self.view_manager = None
