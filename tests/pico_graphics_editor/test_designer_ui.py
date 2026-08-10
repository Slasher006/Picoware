"""Tests for GUI designer and screen-flow widgets."""

# ruff: noqa: E402

import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pico_graphics_editor.designer import (
    ELEMENT_MIME_TYPE,
    PIXEL_ASSET_MIME_TYPE,
    DesignerSession,
    GuiPixelAsset,
    ScreenDesignerWidget,
    ScreenFlowWidget,
)
from pico_graphics_editor.designer_model import (
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
)
from pico_graphics_editor.model import PixelArt


class DesignerUiTests(unittest.TestCase):
    """Verify visual designer model interactions."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the shared offscreen Qt application."""
        cls.application = QApplication.instance() or QApplication([])

    def test_screen_designer_adds_editable_elements(self) -> None:
        """Add a button and update it through property controls."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        widget._add_element("button")
        element = session.current_screen().elements[0]
        widget.element_name_edit.setText("start_button")
        widget.element_text_edit.setText("Start")
        widget._element_properties_changed()
        self.assertEqual(element.name, "start_button")
        self.assertEqual(element.text, "Start")
        self.assertTrue(session.dirty)
        widget.close()

    def test_palette_element_drops_at_canvas_position(self) -> None:
        """Create and select an element from a palette drop."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        widget.canvas.set_zoom(100)
        mime = QMimeData()
        mime.setData(ELEMENT_MIME_TYPE, b"button")
        event = QDropEvent(
            QPointF(180, 140),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.canvas.dropEvent(event)
        element = session.current_screen().elements[0]
        self.assertEqual(element.kind, "button")
        self.assertEqual((element.x, element.y), (120, 122))
        self.assertEqual(widget.selected_element_id, element.id)
        self.assertTrue(event.isAccepted())
        widget.close()

    def test_pixel_asset_is_available_and_drops_as_embedded_icon(self) -> None:
        """Offer a traced asset and embed its pixels on canvas drop."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        widget.canvas.set_zoom(100)
        art = PixelArt(4, 3)
        art.set_pixel(0, 0, 0xF800)
        art.set_pixel(1, 0, 0xF800)
        art.set_pixel(3, 2, 0x07E0)
        asset = GuiPixelAsset(
            "fixture::draw_badge",
            "draw_badge",
            "/tmp/fixture.py",
            "draw_badge",
            art,
        )
        widget.set_pixel_assets([asset])
        self.assertEqual(widget.pixel_asset_list.count(), 1)
        self.assertEqual(widget.pixel_asset_list.item(0).text(), "draw_badge")
        mime = QMimeData()
        mime.setData(PIXEL_ASSET_MIME_TYPE, asset.key.encode("utf-8"))
        event = QDropEvent(
            QPointF(180, 140),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.canvas.dropEvent(event)
        element = session.current_screen().elements[0]
        self.assertEqual(element.kind, "icon")
        self.assertEqual(element.asset_call, "draw_badge")
        self.assertEqual(element.asset_key, asset.key)
        self.assertEqual((element.asset_width, element.asset_height), (4, 3))
        self.assertEqual(
            element.asset_runs,
            [[0, 0, 2, 0xF800], [3, 2, 1, 0x07E0]],
        )
        self.assertEqual((element.width, element.height), (4, 3))
        self.assertEqual((element.x, element.y), (178, 139))
        self.assertEqual(widget.selected_element_id, element.id)
        self.assertTrue(event.isAccepted())
        refreshed_art = PixelArt(4, 3)
        refreshed_art.set_pixel(2, 1, 0x001F)
        widget.upsert_pixel_asset(
            GuiPixelAsset(
                asset.key,
                asset.name,
                asset.source_path,
                asset.function_name,
                refreshed_art,
            )
        )
        widget._refresh_selected_pixel_asset()
        self.assertEqual(element.asset_runs, [[2, 1, 1, 0x001F]])
        requested: list[str] = []
        widget.pixel_asset_edit_requested.connect(requested.append)
        widget._edit_selected_pixel_asset()
        self.assertEqual(requested, [asset.key])
        widget.close()

    def test_custom_profile_changes_all_screen_sizes(self) -> None:
        """Apply custom dimensions across a designer project."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        widget.profile_combo.setCurrentText("Custom")
        widget.project_width_spin.setValue(480)
        widget.project_height_spin.setValue(272)
        self.assertEqual((session.project.width, session.project.height), (480, 272))
        self.assertEqual(
            (session.current_screen().width, session.current_screen().height),
            (480, 272),
        )
        widget.close()

    def test_flow_relation_drives_navigation_preview(self) -> None:
        """Create a relation and dispatch its simulator event."""
        project = GuiProject.create("Flow Demo")
        target = ScreenDesign.create("Game", 320, 320, 1)
        project.screens.append(target)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.source_combo.setCurrentIndex(0)
        widget.target_combo.setCurrentIndex(1)
        widget.trigger_edit.setText("start")
        widget._add_relation()
        widget.simulator_event_edit.setText("start")
        widget._send_simulator_event()
        self.assertEqual(len(project.connections), 1)
        self.assertEqual(widget.simulated_screen_id, target.id)
        widget.close()

    def test_node_ports_create_relation_with_mouse(self) -> None:
        """Drag between graph ports to create a navigation relation."""
        project = GuiProject.create("Mouse Flow")
        target = ScreenDesign.create("Game", 320, 320, 1)
        project.screens.append(target)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.trigger_edit.setText("open")
        widget.show()
        self.application.processEvents()
        source = project.screens[0]
        source_port = QPoint(
            source.node_x + widget.graph.NODE_WIDTH,
            source.node_y + widget.graph.NODE_HEIGHT // 2,
        )
        target_port = QPoint(
            target.node_x,
            target.node_y + widget.graph.NODE_HEIGHT // 2,
        )
        QTest.mousePress(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            source_port,
        )
        QTest.mouseMove(widget.graph, target_port)
        QTest.mouseRelease(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target_port,
        )
        self.assertEqual(len(project.connections), 1)
        connection = project.connections[0]
        self.assertEqual(connection.source_id, source.id)
        self.assertEqual(connection.target_id, target.id)
        self.assertEqual(connection.trigger, "open")
        widget.close()

    def test_element_ports_create_asset_to_asset_relation(self) -> None:
        """Drag a button output to a focused asset on another screen."""
        project = GuiProject.create("Asset Flow")
        button = GuiElement.create("button", 1)
        button.name = "play_button"
        button.event_name = "play"
        project.screens[0].elements.append(button)
        target = ScreenDesign.create("Game", 320, 320, 1)
        icon = GuiElement.create("icon", 1)
        icon.name = "game_icon"
        target.elements.append(icon)
        project.screens.append(target)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.show()
        self.application.processEvents()
        source_port = widget.graph._element_output_port(
            project.screens[0],
            button,
        ).toPoint()
        target_port = widget.graph._element_input_port(target, icon).toPoint()
        QTest.mousePress(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            source_port,
        )
        QTest.mouseMove(widget.graph, target_port)
        QTest.mouseRelease(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target_port,
        )
        self.assertEqual(len(project.connections), 1)
        connection = project.connections[0]
        self.assertEqual(connection.source_element_id, button.id)
        self.assertEqual(connection.target_element_id, icon.id)
        self.assertEqual(connection.trigger, "play")
        widget.preview.setFocus()
        QTest.keyClick(widget.preview, Qt.Key.Key_Return)
        self.assertEqual(widget.simulated_screen_id, target.id)
        self.assertEqual(widget.preview.focused_element_id, icon.id)
        widget.close()

    def test_element_behavior_updates_connected_relations(self) -> None:
        """Configure an element event and keep its graph relations synchronized."""
        project = GuiProject.create("Configurable")
        button = GuiElement.create("button", 1)
        project.screens[0].elements.append(button)
        target = ScreenDesign.create("Settings", 320, 320, 1)
        project.screens.append(target)
        connection = FlowConnection.create(
            project.screens[0].id,
            target.id,
            button.activation_event(),
            button.id,
        )
        project.connections.append(connection)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenDesignerWidget(session)
        widget._select_element(button.id)
        widget.event_name_edit.setText("open_settings")
        widget.enabled_check.setChecked(False)
        widget._element_properties_changed()
        self.assertEqual(button.activation_event(), "open_settings")
        self.assertFalse(button.enabled)
        self.assertEqual(connection.trigger, "open_settings")
        self.assertIn("1 connected relation", widget.element_flow_label.text())
        widget.close()

    def test_element_focus_appearance_is_configurable(self) -> None:
        """Edit the style and geometry of an element focus indicator."""
        session = DesignerSession()
        button = GuiElement.create("button", 1)
        session.current_screen().elements.append(button)
        widget = ScreenDesignerWidget(session)
        widget._select_element(button.id)
        widget.focus_style_combo.setCurrentIndex(
            widget.focus_style_combo.findData("corners")
        )
        widget.focus_thickness_spin.setValue(4)
        widget.focus_padding_spin.setValue(6)
        widget._element_properties_changed()
        self.assertEqual(button.focus_style, "corners")
        self.assertEqual(button.focus_thickness, 4)
        self.assertEqual(button.focus_padding, 6)
        self.assertTrue(widget.focus_color_button.isEnabled())
        widget.focus_style_combo.setCurrentIndex(
            widget.focus_style_combo.findData("none")
        )
        widget._element_properties_changed()
        self.assertFalse(widget.focus_color_button.isEnabled())
        widget.close()

    def test_deleting_element_removes_its_asset_relations(self) -> None:
        """Remove graph relations that reference a deleted design element."""
        project = GuiProject.create("Delete Asset")
        button = GuiElement.create("button", 1)
        project.screens[0].elements.append(button)
        target = ScreenDesign.create("Target", 320, 320, 1)
        project.screens.append(target)
        project.connections.append(
            FlowConnection.create(
                project.screens[0].id,
                target.id,
                button.activation_event(),
                button.id,
            )
        )
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenDesignerWidget(session)
        widget._select_element(button.id)
        widget._delete_element()
        self.assertEqual(project.screens[0].elements, [])
        self.assertEqual(project.connections, [])
        widget.close()

    def test_screen_list_thumbnail_renders_screen_content(self) -> None:
        """Render actual screen colors inside the screen list thumbnail."""
        session = DesignerSession()
        session.current_screen().background_color = 0xF800
        widget = ScreenDesignerWidget(session)
        image = widget.screen_list.item(0).icon().pixmap(76, 64).toImage()
        center = image.pixelColor(image.width() // 2, image.height() // 2)
        self.assertGreater(center.red(), 200)
        self.assertLess(center.green(), 80)
        widget.close()

    def test_multi_selection_aligns_nudges_and_undoes(self) -> None:
        """Align and move multiple selected elements with shared history."""
        project = GuiProject.create("Layout")
        first = GuiElement.create("button", 1)
        second = GuiElement.create("button", 2)
        first.y = 20
        second.y = 80
        project.screens[0].elements.extend((first, second))
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenDesignerWidget(session)
        widget._canvas_selection_changed({first.id, second.id})
        widget._align_selection("top")
        self.assertEqual((first.y, second.y), (20, 20))
        widget.canvas.setFocus()
        QTest.keyClick(widget.canvas, Qt.Key.Key_Right)
        self.assertEqual((first.x, second.x), (25, 33))
        session.undo()
        current = session.current_screen().elements
        self.assertEqual((current[0].x, current[1].x), (24, 32))
        session.undo()
        current = session.current_screen().elements
        self.assertEqual((current[0].y, current[1].y), (20, 80))
        session.redo()
        current = session.current_screen().elements
        self.assertEqual((current[0].y, current[1].y), (20, 20))
        widget.close()

    def test_preview_keyboard_activates_focused_element(self) -> None:
        """Navigate by keyboard and activate a named screen relation."""
        project = GuiProject.create("Focus Flow")
        button = GuiElement.create("button", 1)
        button.name = "open_game"
        project.screens[0].elements.append(button)
        target = ScreenDesign.create("Game", 320, 320, 1)
        project.screens.append(target)
        project.connections.append(
            FlowConnection.create(project.screens[0].id, target.id, button.name)
        )
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        QTest.keyClick(widget.preview, Qt.Key.Key_Return)
        self.assertEqual(widget.simulated_screen_id, target.id)
        self.assertEqual(widget.simulator_event_edit.text(), "open_game")
        widget.close()

    def test_graph_edge_can_be_selected_and_deleted(self) -> None:
        """Select a graph relation line and remove it with Delete."""
        project = GuiProject.create("Editable Flow")
        target = ScreenDesign.create("Game", 320, 320, 1)
        project.screens.append(target)
        connection = FlowConnection.create(project.screens[0].id, target.id, "open")
        project.connections.append(connection)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        source_point = widget.graph._output_port(project.screens[0])
        target_point = widget.graph._input_port(target)
        path, unused = widget.graph._connection_path(source_point, target_point)
        midpoint = path.pointAtPercent(0.5).toPoint()
        QTest.mouseClick(widget.graph, Qt.MouseButton.LeftButton, pos=midpoint)
        self.assertEqual(widget.graph.selected_connection_id, connection.id)
        QTest.keyClick(widget.graph, Qt.Key.Key_Delete)
        self.assertEqual(session.project.connections, [])
        widget.close()

    def test_node_body_remains_mouse_draggable(self) -> None:
        """Move a node by its body without starting a connection."""
        session = DesignerSession()
        widget = ScreenFlowWidget(session)
        widget.show()
        self.application.processEvents()
        screen = session.current_screen()
        start = QPoint(screen.node_x + 80, screen.node_y + 35)
        destination = start + QPoint(40, 30)
        QTest.mousePress(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        QTest.mouseMove(widget.graph, destination)
        QTest.mouseRelease(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            destination,
        )
        self.assertEqual((screen.node_x, screen.node_y), (110, 100))
        self.assertEqual(session.project.connections, [])
        widget.close()

    def test_node_graph_pans_with_middle_mouse_drag(self) -> None:
        """Pan the scrollable node view while holding the middle mouse button."""
        session = DesignerSession()
        widget = ScreenFlowWidget(session)
        widget.resize(1000, 800)
        widget.show()
        self.application.processEvents()
        horizontal = widget.graph_scroll.horizontalScrollBar()
        vertical = widget.graph_scroll.verticalScrollBar()
        horizontal.setValue(250)
        vertical.setValue(180)
        start = QPoint(horizontal.value() + 260, vertical.value() + 220)
        destination = start - QPoint(70, 50)
        before = (horizontal.value(), vertical.value())
        QTest.mousePress(
            widget.graph,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        QTest.mouseMove(widget.graph, destination)
        QTest.mouseRelease(
            widget.graph,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
            destination,
        )
        self.assertGreater(horizontal.value(), before[0])
        self.assertGreater(vertical.value(), before[1])
        widget.close()

    def test_node_graph_zooms_out_and_fits_all_nodes(self) -> None:
        """Allow a large graph to fit into one node-editor viewport."""
        project = GuiProject.create("Large Flow")
        distant = ScreenDesign.create("Distant", 320, 320, 1)
        distant.node_x = 9000
        distant.node_y = 4200
        project.screens.append(distant)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.resize(1100, 850)
        widget.show()
        self.application.processEvents()
        for unused in range(15):
            event = QWheelEvent(
                QPointF(300, 200),
                QPointF(300, 200),
                QPoint(),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.ScrollUpdate,
                False,
            )
            widget.graph.wheelEvent(event)
        self.assertEqual(widget.graph.zoom, widget.graph.MIN_ZOOM)
        self.assertEqual(widget.graph_zoom_label.text(), "Zoom: 5%")
        widget.graph.set_zoom(1.0)
        widget._fit_graph_nodes()
        viewport = widget.graph_scroll.viewport().size()
        graph_width = (
            distant.node_x + widget.graph.NODE_WIDTH - project.screens[0].node_x
        )
        graph_height = (
            distant.node_y
            + widget.graph._node_height(distant)
            - project.screens[0].node_y
        )
        self.assertLess(widget.graph.zoom, 0.2)
        self.assertLessEqual(graph_width * widget.graph.zoom, viewport.width() - 50)
        self.assertLessEqual(graph_height * widget.graph.zoom, viewport.height() - 50)
        widget.close()

    def test_locked_imported_element_is_read_only(self) -> None:
        """Expose preserved dynamic code without movable properties."""
        session = DesignerSession()
        element = GuiElement.create("icon", 1)
        element.locked = True
        element.source_path = "/tmp/existing_app.py"
        element.source_line = 42
        session.current_screen().elements.append(element)
        widget = ScreenDesignerWidget(session)
        widget._select_element(element.id)
        self.assertFalse(widget.property_group.isEnabled())
        self.assertIn("Locked dynamic code", widget.source_notice_label.text())
        widget.close()

    def test_source_text_exposes_only_patchable_properties(self) -> None:
        """Disable preview-only size and unrelated source text colors."""
        session = DesignerSession()
        element = GuiElement.create("label", 1)
        element.source_path = "/tmp/existing_app.py"
        element.source_line = 12
        element.source_values = {"call_type": "text"}
        session.current_screen().elements.append(element)
        widget = ScreenDesignerWidget(session)
        widget._select_element(element.id)
        self.assertFalse(widget.width_spin.isEnabled())
        self.assertFalse(widget.height_spin.isEnabled())
        self.assertTrue(widget.element_text_edit.isEnabled())
        self.assertFalse(widget.fill_color_button.isEnabled())
        self.assertFalse(widget.border_color_button.isEnabled())
        self.assertTrue(widget.text_color_button.isEnabled())
        widget.close()

    def test_imported_screen_copy_drops_source_anchors(self) -> None:
        """Create a project-only copy without duplicate patch anchors."""
        session = DesignerSession()
        screen = session.current_screen()
        screen.source_path = "/tmp/existing_app.py"
        editable = GuiElement.create("label", 1)
        editable.source_path = screen.source_path
        editable.source_segment = "draw.text(...)"
        editable.source_values = {"x": editable.x}
        locked = GuiElement.create("icon", 2)
        locked.locked = True
        locked.source_path = screen.source_path
        screen.elements.extend((editable, locked))
        widget = ScreenDesignerWidget(session)
        widget._duplicate_screen()
        duplicate = session.current_screen()
        self.assertEqual(duplicate.source_path, "")
        self.assertEqual(len(duplicate.elements), 1)
        self.assertEqual(duplicate.elements[0].source_path, "")
        self.assertEqual(duplicate.elements[0].source_values, {})
        widget.close()

    def test_source_relation_exposes_only_patchable_fields(self) -> None:
        """Disable imported relation fields that cannot update source."""
        project = GuiProject.create("Imported Flow")
        target = ScreenDesign.create("Game", 320, 320, 1)
        target.source_state = "game"
        project.screens.append(target)
        connection = FlowConnection.create(project.screens[0].id, target.id, "ENTER")
        connection.source_path = "/tmp/existing_app.py"
        project.connections.append(connection)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.connection_list.setCurrentRow(0)
        self.assertFalse(widget.source_combo.isEnabled())
        self.assertTrue(widget.target_combo.isEnabled())
        self.assertTrue(widget.trigger_edit.isEnabled())
        self.assertFalse(widget.condition_edit.isEnabled())
        self.assertFalse(widget.action_edit.isEnabled())
        self.assertFalse(widget.transition_combo.isEnabled())
        original_source = connection.source_id
        widget.source_combo.setCurrentIndex(1)
        widget._update_relation()
        self.assertEqual(connection.source_id, original_source)
        widget.close()


if __name__ == "__main__":
    unittest.main()
