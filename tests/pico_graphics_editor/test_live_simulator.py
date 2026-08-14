"""Tests for the embedded Picoware live simulator bridge."""

# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


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
    SimulatorWorkspace,
)
from pico_graphics_editor.live_simulator import (
    LiveSimulatorConfig,
    LiveSimulatorController,
    LiveSimulatorView,
    rgb565_frame_image,
)
from pico_graphics_editor.designer_model import (
    BehaviorConnection,
    FlowConnection,
    FlowNode,
    GuiElement,
    GuiProject,
    ProjectAsset,
    ScreenDesign,
    generate_live_app_python,
)
from pico_graphics_editor.generated_app import (
    apply_generated_app_patchset,
    build_generated_app_patchset,
    build_live_preview_bundle,
)
from pico_graphics_editor.model import PixelArt


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
        QTest.keyPress(view, Qt.Key.Key_Backspace)
        QTest.keyRelease(view, Qt.Key.Key_Backspace)
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
                (8, True, False),
                (8, False, False),
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
            widget = SimulatorWorkspace(session)
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
        widget = SimulatorWorkspace(session)
        configs: list[LiveSimulatorConfig] = []
        widget.live_controller.start = configs.append
        widget._start_live_simulator()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].target_kind, "Current design")
        files = dict(configs[0].design_files)
        self.assertIn("GuiDesignerLive.py", files)
        generated_ui = files["gui_designer_live/generated_ui.py"]
        self.assertIsInstance(generated_ui, str)
        self.assertIn(f"self.screen_id = {active.id!r}", generated_ui)
        self.assertIn("0x07E0", generated_ui)
        self.assertTrue(
            bytes(files["gui_designer_live/generated_assets.pga"]).startswith(b"PGA3")
        )
        widget.shutdown_live_simulator()
        widget.close()

    def test_live_start_can_explicitly_bake_invalid_asset_sizes_and_continue(self) -> None:
        """Recover from arbitrary image geometry in the simulator launch workflow."""
        session = DesignerSession()
        art = PixelArt(2, 2)
        art.set_pixel(0, 0, 0xF800)
        asset = ProjectAsset.from_pixel_art("asset-image", "Image", art)
        session.project.assets.append(asset)
        element = GuiElement.create("icon", 1)
        element.asset_id = asset.id
        element.width = 3
        element.height = 3
        session.current_screen().elements.append(element)
        widget = SimulatorWorkspace(session)
        configs: list[LiveSimulatorConfig] = []
        widget.live_controller.start = configs.append
        with patch("pico_graphics_editor.designer.QMessageBox") as message_class:
            message = message_class.return_value
            bake_button = MagicMock()
            natural_button = MagicMock()
            message.addButton.side_effect = [
                bake_button,
                natural_button,
                MagicMock(),
            ]
            message.clickedButton.return_value = bake_button
            self.assertTrue(widget._start_live_simulator())
        self.assertEqual(len(configs), 1)
        baked = session.project.asset(element.asset_id)
        self.assertIsNotNone(baked)
        self.assertEqual((baked.width, baked.height), (3, 3))
        self.assertEqual(element.asset_link_state, "detached")
        widget.shutdown_live_simulator()
        widget.close()

    def test_running_live_preview_receives_keyboard_focus(self) -> None:
        """Move keyboard focus into the framebuffer when live mode starts."""
        widget = SimulatorWorkspace(DesignerSession())
        focus_reasons: list[Qt.FocusReason] = []
        widget.live_preview.setFocus = focus_reasons.append
        widget.preview_mode_combo.setCurrentIndex(
            widget.preview_mode_combo.findData("live")
        )
        widget._live_running_changed(True)
        self.assertEqual(focus_reasons, [Qt.FocusReason.OtherFocusReason])
        widget.shutdown_live_simulator()
        widget.close()

    def test_dedicated_workspace_prioritizes_run_and_actionable_errors(self) -> None:
        """Separate primary execution from advanced launch, capture, and diagnostics."""
        widget = SimulatorWorkspace(DesignerSession())
        self.assertEqual(widget.start_live_button.text(), "▶ Run current design")
        self.assertEqual(
            [
                widget.details_tabs.tabText(index)
                for index in range(widget.details_tabs.count())
            ],
            ["Runtime details", "Capture", "Advanced launch"],
        )
        self.assertGreaterEqual(widget.live_preview.minimumWidth(), 320)
        self.assertGreaterEqual(widget.live_preview.minimumHeight(), 320)
        self.assertTrue(widget.error_panel.isHidden())
        widget._live_error_changed("Import failed\nmissing generated asset")
        self.assertFalse(widget.error_panel.isHidden())
        self.assertEqual(widget.error_summary_label.text(), "Import failed")
        self.assertEqual(widget.preview_mode_combo.currentData(), "compare")
        self.assertIn("missing generated asset", widget.runtime_log.toPlainText())
        widget.copy_error()
        self.assertEqual(
            QApplication.clipboard().text(),
            "Import failed\nmissing generated asset",
        )
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
            for unused in range(160):
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

    def test_controller_displays_button_alert_behavior(self) -> None:
        """Exercise button to Show alert through the real simulator bridge."""
        project = GuiProject.create("Live Alert")
        project.screens[0].background_color = 0xF800
        button = GuiElement.create("button", 1)
        button.text = "Show alert"
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
        alert.properties["message"] = "Simulator alert is visible"
        project.behavior_nodes.extend((event, alert))
        project.behavior_connections.append(
            BehaviorConnection.create(event.id, "event", alert.id, "in")
        )
        bundle = build_live_preview_bundle(project, project.start_screen_id)
        controller = LiveSimulatorController()
        live_view = LiveSimulatorView()
        live_view.key_event.connect(controller.send_key)
        frames: list[QImage] = []
        errors: list[str] = []
        controller.frame_ready.connect(lambda image: frames.append(image.copy()))
        controller.error_changed.connect(errors.append)
        try:
            controller.start(
                LiveSimulatorConfig(auto_reload=False, design_files=bundle.files)
            )
            for unused in range(160):
                if (
                    frames
                    and frames[-1].pixelColor(300, 300).red() > 240
                    and frames[-1].pixelColor(20, 160).green() < 20
                ):
                    break
                QTest.qWait(50)
            self.assertTrue(frames)
            self.assertGreater(frames[-1].pixelColor(300, 300).red(), 240)
            self.assertLess(frames[-1].pixelColor(20, 160).green(), 20)

            controller.send_key(13, True)
            controller.send_key(13, False)
            for unused in range(160):
                if frames and frames[-1].pixelColor(20, 160).green() > 240:
                    break
                QTest.qWait(50)

            self.assertEqual(errors, [])
            self.assertGreater(
                frames[-1].pixelColor(20, 160).green(),
                240,
                f"errors={errors!r} output={controller._output_tail!r}",
            )

            live_view.set_frame(frames[-1])
            live_view.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(live_view, Qt.Key.Key_Backspace)
            for unused in range(160):
                if frames and frames[-1].pixelColor(20, 160).green() < 20:
                    break
                QTest.qWait(50)

            self.assertEqual(errors, [])
            self.assertLess(
                frames[-1].pixelColor(20, 160).green(),
                20,
                f"errors={errors!r} output={controller._output_tail!r}",
            )

            QTest.keyClick(live_view, Qt.Key.Key_Return)
            for unused in range(160):
                if frames and frames[-1].pixelColor(20, 160).green() > 240:
                    break
                QTest.qWait(50)
            self.assertGreater(frames[-1].pixelColor(20, 160).green(), 240)

            QTest.keyClick(live_view, Qt.Key.Key_Escape)
            for unused in range(160):
                if frames and frames[-1].pixelColor(20, 160).green() < 20:
                    break
                QTest.qWait(50)
            self.assertLess(
                frames[-1].pixelColor(20, 160).green(),
                20,
                f"errors={errors!r} output={controller._output_tail!r}",
            )
        finally:
            live_view.close()
            controller.shutdown()

    def test_legacy_native_alert_dismisses_with_editor_backspace(self) -> None:
        """Migrate and dismiss a native Alert through the actual editor key path."""
        project = GuiProject.create("Legacy Native Alert")
        project.screens[0].background_color = 0xF800
        button = GuiElement.create("button", 1)
        button.text = "Show alert"
        project.screens[0].elements.append(button)

        alert_screen = ScreenDesign.create("Alert", 320, 320, 1)
        alert = GuiElement.create("native", 1)
        alert.native_widget = "alert"
        alert.name = "Warning"
        alert.text = "Press Backspace to dismiss"
        alert.text_color = 0x07E0
        alert.focusable = False
        alert.enabled = False
        alert_screen.elements.append(alert)
        project.screens.append(alert_screen)

        open_alert = FlowConnection.create(
            project.screens[0].id,
            alert_screen.id,
            button.activation_event(),
            button.id,
        )
        open_alert.trigger_event_id = button.event_id
        close_alert = FlowConnection.create(
            alert_screen.id,
            project.screens[0].id,
            "Back",
        )
        close_alert.source_element_id = ""
        close_alert.trigger_event_id = "event_legacy_unreachable"
        project.connections.extend((open_alert, close_alert))

        migrated = GuiProject.from_dict(project.to_dict())
        migrated_alert = migrated.screens[1].elements[0]
        migrated_close = migrated.connections[1]
        self.assertTrue(migrated_alert.enabled)
        self.assertTrue(migrated_alert.focusable)
        self.assertEqual(migrated_close.source_element_id, migrated_alert.id)
        self.assertEqual(migrated_close.trigger_event_id, migrated_alert.event_id)

        bundle = build_live_preview_bundle(migrated, migrated.screens[1].id)
        controller = LiveSimulatorController()
        live_view = LiveSimulatorView()
        live_view.key_event.connect(controller.send_key)
        frames: list[QImage] = []
        errors: list[str] = []
        statuses: list[str] = []
        controller.frame_ready.connect(lambda image: frames.append(image.copy()))
        controller.error_changed.connect(errors.append)
        controller.status_changed.connect(statuses.append)
        try:
            controller.start(
                LiveSimulatorConfig(auto_reload=False, design_files=bundle.files)
            )
            for unused in range(160):
                if frames and frames[-1].pixelColor(20, 160).green() > 240:
                    break
                QTest.qWait(50)
            self.assertTrue(frames)
            self.assertGreater(
                frames[-1].pixelColor(20, 160).green(),
                240,
                f"errors={errors!r} output={controller._output_tail!r}",
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

            live_view.set_frame(frames[-1])
            live_view.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(live_view, Qt.Key.Key_Backspace)
            for unused in range(160):
                if frames and frames[-1].pixelColor(300, 300).red() > 240:
                    break
                QTest.qWait(50)
            self.assertEqual(errors, [])
            self.assertGreater(
                frames[-1].pixelColor(300, 300).red(),
                240,
                f"errors={errors!r} output={controller._output_tail!r}",
            )
            self.assertLess(frames[-1].pixelColor(20, 160).green(), 20)
        finally:
            live_view.close()
            controller.shutdown()

    def test_controller_keeps_loading_widget_animating_without_input(self) -> None:
        """Drive Loading frames from the app loop instead of requiring a key press."""
        project = GuiProject.create("Live Loading")
        loading = GuiElement.create("native", 1)
        loading.native_widget = "loading"
        loading.text = "Working"
        project.screens[0].elements.append(loading)
        bundle = build_live_preview_bundle(project, project.start_screen_id)
        controller = LiveSimulatorController()
        frames: list[QImage] = []
        errors: list[str] = []
        controller.frame_ready.connect(lambda image: frames.append(image.copy()))
        controller.error_changed.connect(errors.append)
        try:
            controller.start(
                LiveSimulatorConfig(auto_reload=False, design_files=bundle.files)
            )
            for unused in range(160):
                if frames or errors:
                    break
                QTest.qWait(50)
            self.assertEqual(errors, [])
            self.assertTrue(frames)
            initial_count = len(frames)

            QTest.qWait(500)

            self.assertEqual(errors, [])
            self.assertGreater(len(frames), initial_count)
        finally:
            controller.shutdown()

    def test_dense_imported_image_loads_through_streamed_resource(self) -> None:
        """Keep a 300x320 imported raster out of the MicroPython parser heap."""
        project = GuiProject.create("Dense Imported Image")
        art = PixelArt(300, 320)
        colors = (0x0000, 0xF800, 0x07E0, 0x001F, 0xFFFF)
        for y in range(art.height):
            for x in range(art.width):
                art.set_pixel(x, y, colors[(x + y) % len(colors)])
        asset = ProjectAsset.from_pixel_art("asset_dense", "Dense", art)
        project.assets.append(asset)
        element = GuiElement.create("icon", 1)
        element.asset_id = asset.id
        element.width = art.width
        element.height = art.height
        element.x = 10
        element.y = 0
        element.focusable = False
        project.screens[0].elements.append(element)
        bundle = build_live_preview_bundle(project, project.start_screen_id)
        files = dict(bundle.files)
        self.assertLess(
            len(str(files["gui_designer_live/generated_assets.py"]).encode()),
            12_500,
        )
        self.assertGreater(
            len(bytes(files["gui_designer_live/generated_assets.pga"])),
            190_000,
        )

        controller = LiveSimulatorController()
        statuses: list[str] = []
        errors: list[str] = []
        controller.status_changed.connect(statuses.append)
        controller.error_changed.connect(errors.append)
        try:
            controller.start(
                LiveSimulatorConfig(
                    auto_reload=False,
                    design_files=bundle.files,
                )
            )
            for unused in range(240):
                if errors or any("app_GuiDesignerLive" in item for item in statuses):
                    break
                QTest.qWait(50)
            self.assertEqual(errors, [])
            self.assertTrue(
                any("app_GuiDesignerLive" in item for item in statuses),
                statuses[-3:],
            )
            image = controller.current_frame()
            for unused in range(40):
                if image.pixelColor(11, 0).red() > 240:
                    break
                QTest.qWait(50)
                image = controller.current_frame()
            self.assertFalse(image.isNull())
            rendered_red = image.pixelColor(11, 0)
            self.assertGreater(rendered_red.red(), 240)
            self.assertLess(rendered_red.green(), 20)
            self.assertLess(rendered_red.blue(), 20)
        finally:
            controller.shutdown()

        with tempfile.TemporaryDirectory() as folder:
            patchset = build_generated_app_patchset(project, folder)
            apply_generated_app_patchset(patchset)
            exported = LiveSimulatorController()
            exported_errors: list[str] = []
            exported_statuses: list[str] = []
            exported.error_changed.connect(exported_errors.append)
            exported.status_changed.connect(exported_statuses.append)
            try:
                exported.start(
                    LiveSimulatorConfig(
                        target_kind="Application",
                        target_name=patchset.paths.display_name,
                        apps_source=folder,
                        auto_reload=False,
                    )
                )
                for unused in range(240):
                    if exported_errors or any(
                        f"app_{patchset.paths.display_name}" in item
                        for item in exported_statuses
                    ):
                        break
                    QTest.qWait(50)
                self.assertEqual(exported_errors, [])
                self.assertTrue(
                    any(
                        f"app_{patchset.paths.display_name}" in item
                        for item in exported_statuses
                    ),
                    exported_statuses[-3:],
                )
            finally:
                exported.shutdown()


if __name__ == "__main__":
    unittest.main()
