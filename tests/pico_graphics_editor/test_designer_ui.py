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
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pico_graphics_editor.designer import (
    ELEMENT_MIME_TYPE,
    DesignerSession,
    ScreenDesignerWidget,
    ScreenFlowWidget,
)
from pico_graphics_editor.designer_model import (
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
)


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
        source_port = QPoint(source.node_x + 160, source.node_y + 35)
        target_port = QPoint(target.node_x, target.node_y + 35)
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
