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

from PySide6.QtWidgets import QApplication

from pico_graphics_editor.designer import (
    DesignerSession,
    ScreenDesignerWidget,
    ScreenFlowWidget,
)
from pico_graphics_editor.designer_model import GuiProject, ScreenDesign


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


if __name__ == "__main__":
    unittest.main()
