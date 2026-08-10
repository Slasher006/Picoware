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

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
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
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(120, 120))
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(120, 120))
        self.assertEqual(keys, [(0xB5, True, False), (0xB5, False, False)])
        self.assertEqual(touches[-1], (0, 0, 0))
        self.assertGreaterEqual(touches[0][0], 0)
        self.assertGreaterEqual(touches[0][1], 0)
        view.close()

    def test_imported_game_infers_live_launch_target(self) -> None:
        """Infer a Game route from an imported launcher under games."""
        with tempfile.TemporaryDirectory() as folder:
            games = Path(folder) / "games"
            games.mkdir()
            launcher = games / "Pico Bomber.py"
            launcher.write_text("VALUE = 1\n", encoding="utf-8")
            session = DesignerSession()
            session.project.import_root = str(launcher)
            widget = ScreenFlowWidget(session)
            self.assertEqual(widget.live_target_kind_combo.currentText(), "Game")
            self.assertEqual(widget.live_target_edit.text(), "Pico Bomber")
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


if __name__ == "__main__":
    unittest.main()
