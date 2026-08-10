"""Tests for the embedded Picoware live simulator bridge."""

# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QFocusEvent, QImage, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pico_graphics_editor.designer import (
    DesignerSession,
    ScreenDesignerWidget,
    ScreenFlowWidget,
)
from pico_graphics_editor.live_simulator import (
    LiveSimulatorConfig,
    LiveSimulatorController,
    LiveSimulatorView,
    rgb565_frame_image,
)
from pico_graphics_editor.designer_model import (
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
    generate_live_app_python,
)


class LiveSimulatorTests(unittest.TestCase):
    """Verify framebuffer decoding, input forwarding, and process lifecycle."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the shared offscreen Qt application."""
        cls.application = QApplication.instance() or QApplication([])

    def test_rgb565_frame_decodes_primary_colors(self) -> None:
        """Decode little-endian RGB565 pixels into a Qt image."""
        image = rgb565_frame_image(
            b"\x00\xf8\xe0\x07\x1f\x00\xff\xff",
            2,
            2,
        )
        self.assertEqual(image.size().toTuple(), (2, 2))
        self.assertGreater(image.pixelColor(0, 0).red(), 240)
        self.assertGreater(image.pixelColor(1, 0).green(), 240)
        self.assertGreater(image.pixelColor(0, 1).blue(), 240)

    def test_live_view_forwards_keyboard_and_touch(self) -> None:
        """Map Qt keyboard and mouse input into simulator protocol values."""
        view = LiveSimulatorView()
        view.resize(240, 240)
        image = QImage(2, 2, QImage.Format.Format_RGB16)
        image.fill(QColor("red"))
        view.set_frame(image)
        keys: list[tuple[int, bool, bool]] = []
        touches: list[tuple[int, int, int]] = []
        view.key_event.connect(lambda *values: keys.append(values))
        view.touch_event.connect(lambda *values: touches.append(values))
        QTest.keyPress(view, Qt.Key.Key_Up)
        QTest.keyRelease(view, Qt.Key.Key_Up)
        QTest.keyPress(view, Qt.Key.Key_F10)
        QTest.keyRelease(view, Qt.Key.Key_F10)
        QTest.keyPress(view, Qt.Key.Key_Tab)
        QTest.keyRelease(view, Qt.Key.Key_Tab)
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(120, 120))
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(120, 120))
        self.assertEqual(
            keys,
            [
                (0xB5, True, False),
                (0xB5, False, False),
                (0x90, True, False),
                (0x90, False, False),
                (9, True, False),
                (9, False, False),
            ],
        )
        self.assertEqual(touches[-1], (0, 0, 0))
        self.assertGreaterEqual(touches[0][0], 0)
        self.assertGreaterEqual(touches[0][1], 0)
        view.close()

    def test_live_view_reserves_and_releases_picoware_keys(self) -> None:
        """Keep simulator shortcuts local and release held keys on focus loss."""
        view = LiveSimulatorView()
        keys: list[tuple[int, bool, bool]] = []
        view.key_event.connect(lambda *values: keys.append(values))
        shortcut = QKeyEvent(
            QEvent.Type.ShortcutOverride,
            Qt.Key.Key_F5,
            Qt.KeyboardModifier.NoModifier,
        )
        self.application.sendEvent(view, shortcut)
        self.assertTrue(shortcut.isAccepted())
        QTest.keyPress(view, Qt.Key.Key_Right)
        view.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertEqual(keys, [(0xB7, True, False), (0xB7, False, False)])
        view.close()

    def test_imported_game_infers_live_launch_target(self) -> None:
        """Keep current design default while suggesting an imported Game route."""
        with tempfile.TemporaryDirectory() as folder:
            games = Path(folder) / "games"
            games.mkdir()
            launcher = games / "Pico Bomber.py"
            launcher.write_text("VALUE = 1\n", encoding="utf-8")
            session = DesignerSession()
            session.project.import_root = str(launcher)
            widget = ScreenFlowWidget(session)
            self.assertEqual(
                widget.live_target_kind_combo.currentText(),
                "Current design",
            )
            self.assertEqual(widget.live_target_edit.text(), "Pico Bomber")
            self.assertIn("Suggested Game", widget.live_target_edit.placeholderText())
            widget.shutdown_live_simulator()
            widget.close()

    def test_live_capture_is_kept_per_screen(self) -> None:
        """Associate a live framebuffer with one transient screen preview."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        image = QImage(320, 320, QImage.Format.Format_RGB16)
        image.fill(QColor("red"))
        session.set_live_screen_image(session.active_screen_id, image)
        self.application.processEvents()
        self.assertFalse(session.live_screen_images[session.active_screen_id].isNull())
        thumbnail = widget.screen_list.item(0).icon().pixmap(76, 64).toImage()
        self.assertGreater(
            thumbnail.pixelColor(
                thumbnail.width() // 2,
                thumbnail.height() // 2,
            ).red(),
            240,
        )
        session.clear_live_screen_image(session.active_screen_id)
        self.assertEqual(session.live_screen_images, {})
        widget.close()

    def test_live_start_uses_current_active_design(self) -> None:
        """Build live source from the selected screen and unsaved project state."""
        session = DesignerSession()
        active = ScreenDesign.create("Selected", 320, 320, 1)
        active.background_color = 0x07E0
        session.project.screens.append(active)
        session.set_active_screen(active.id)
        widget = ScreenFlowWidget(session)
        configs: list[LiveSimulatorConfig] = []
        widget.live_controller.start = configs.append
        widget._start_live_simulator()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].target_kind, "Current design")
        self.assertIn("self.screen = 'Selected'", configs[0].design_source)
        self.assertIn("0x07E0", configs[0].design_source)
        widget.shutdown_live_simulator()
        widget.close()

    def test_running_live_preview_receives_keyboard_focus(self) -> None:
        """Move keyboard focus into the framebuffer when live mode starts."""
        widget = ScreenFlowWidget(DesignerSession())
        focus_reasons: list[Qt.FocusReason] = []
        widget.live_preview.setFocus = focus_reasons.append
        widget.preview_mode_combo.setCurrentIndex(
            widget.preview_mode_combo.findData("live")
        )
        widget._live_running_changed(True)
        self.assertEqual(focus_reasons, [Qt.FocusReason.OtherFocusReason])
        widget.shutdown_live_simulator()
        widget.close()

    def test_controller_launches_headless_bridge(self) -> None:
        """Launch the real simulator and receive its first live framebuffer."""
        controller = LiveSimulatorController()
        frames: list[QImage] = []
        controller.frame_ready.connect(lambda image: frames.append(image.copy()))
        try:
            controller.start(LiveSimulatorConfig(auto_reload=False))
            for unused in range(120):
                if frames:
                    break
                QTest.qWait(50)
            self.assertTrue(frames)
            self.assertEqual(frames[-1].size().toTuple(), (320, 320))
            controller.send_key(0xB5, True)
            controller.send_key(0xB5, False)
            QTest.qWait(50)
            commands = controller.keys_path.read_text(encoding="utf-8")
            self.assertIn("down 181 0", commands)
            self.assertIn("up 181 0", commands)
        finally:
            controller.shutdown()

    def test_controller_launches_active_gui_design(self) -> None:
        """Render the selected in-memory GUI screen through the real simulator."""
        project = GuiProject.create("Live Test")
        active = ScreenDesign.create("Active", 320, 320, 1)
        active.background_color = 0xF800
        button = GuiElement.create("button", 1)
        button.name = "next"
        button.focus_style = "underline"
        button.focus_color = 0xFFFF
        button.focus_thickness = 3
        button.focus_padding = 1
        active.elements.append(button)
        destination = ScreenDesign.create("Destination", 320, 320, 2)
        destination.background_color = 0x07E0
        first_destination = GuiElement.create("button", 1)
        first_destination.name = "unused"
        finish_button = GuiElement.create("button", 2)
        finish_button.name = "finish"
        finish_button.focus_style = "corners"
        finish_button.focus_color = 0xF81F
        destination.elements.extend((first_destination, finish_button))
        finished = ScreenDesign.create("Finished", 320, 320, 3)
        finished.background_color = 0x001F
        project.screens.append(active)
        project.screens.append(destination)
        project.screens.append(finished)
        project.connections.append(
            FlowConnection.create(
                active.id,
                destination.id,
                "next",
                button.id,
                finish_button.id,
            )
        )
        project.connections.append(
            FlowConnection.create(
                destination.id,
                finished.id,
                "finish",
                finish_button.id,
            )
        )
        source = generate_live_app_python(project, active.id)
        controller = LiveSimulatorController()
        statuses: list[str] = []
        controller.status_changed.connect(statuses.append)
        try:
            controller.start(
                LiveSimulatorConfig(
                    auto_reload=False,
                    design_source=source,
                )
            )
            for unused in range(160):
                if any("app_GuiDesignerLive" in status for status in statuses):
                    break
                QTest.qWait(50)
            self.assertTrue(
                any("app_GuiDesignerLive" in status for status in statuses),
                statuses[-3:],
            )
            QTest.qWait(150)
            image = controller.current_frame()
            self.assertFalse(image.isNull())
            self.assertGreater(image.pixelColor(160, 160).red(), 240)
            self.assertLess(image.pixelColor(160, 160).green(), 20)
            initial_focus = image.pixelColor(24, 62)
            self.assertGreater(initial_focus.red(), 240)
            self.assertGreater(initial_focus.green(), 240)
            self.assertGreater(initial_focus.blue(), 240)
            controller.send_key(13, True)
            controller.send_key(13, False)
            for unused in range(80):
                image = controller.current_frame()
                if image.pixelColor(160, 160).green() > 240:
                    break
                QTest.qWait(50)
            self.assertGreater(image.pixelColor(160, 160).green(), 240)
            self.assertLess(image.pixelColor(160, 160).red(), 20)
            target_focus = image.pixelColor(30, 30)
            self.assertGreater(target_focus.red(), 240)
            self.assertLess(target_focus.green(), 20)
            self.assertGreater(target_focus.blue(), 240)
            controller.send_key(13, True)
            controller.send_key(13, False)
            for unused in range(80):
                image = controller.current_frame()
                if image.pixelColor(160, 160).blue() > 240:
                    break
                QTest.qWait(50)
            self.assertGreater(image.pixelColor(160, 160).blue(), 240)
            self.assertLess(image.pixelColor(160, 160).green(), 20)
        finally:
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()
