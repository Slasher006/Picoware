"""Tests for GUI designer and screen-flow widgets."""

# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
)

from pico_graphics_editor.designer import (
    ELEMENT_MIME_TYPE,
    PIXEL_ASSET_MIME_TYPE,
    DesignerSession,
    GuiPixelAsset,
    ScreenDesignerWidget,
    ScreenFlowWidget,
)
from pico_graphics_editor.asset_library import LibraryAsset
from pico_graphics_editor.designer_model import (
    BehaviorConnection,
    FlowNode,
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
)
from pico_graphics_editor.flow_library import FlowFragmentLibrary
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

    def test_screen_designer_separates_screen_widgets_from_inline_controls(
        self,
    ) -> None:
        """Allow several inline controls but keep screen widgets exclusive."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        widget._add_native_widget("menu")
        element = session.current_screen().elements[0]
        self.assertEqual(element.kind, "native")
        self.assertEqual(element.native_widget, "menu")
        self.assertEqual(
            (element.x, element.y, element.width, element.height),
            (0, 0, 320, 320),
        )
        self.assertEqual(
            element.widget_items, ["First item", "Second item", "Third item"]
        )
        self.assertTrue(widget.native_properties_group.isVisibleTo(widget))

        widget.native_type_combo.setCurrentIndex(
            widget.native_type_combo.findData("choice")
        )
        widget.widget_items_edit.setPlainText("Automatic\nManual")
        widget.widget_selected_combo.setCurrentIndex(1)
        widget._element_properties_changed()
        self.assertEqual(element.native_widget, "choice")
        self.assertEqual(element.widget_items, ["Automatic", "Manual"])
        self.assertEqual(element.widget_selected_index, 1)
        self.assertLess(element.width, session.current_screen().width)

        widget._add_native_widget("toggle")
        self.assertEqual(len(session.current_screen().elements), 2)

        with patch.object(QMessageBox, "information") as information:
            widget._add_native_widget("alert")
        information.assert_called_once()
        self.assertEqual(len(session.current_screen().elements), 2)
        widget.close()

    def test_layer_controls_reorder_overlapping_elements_back_to_front(self) -> None:
        """Move one or several selected layers while preserving their relative order."""
        session = DesignerSession()
        screen = session.current_screen()
        elements = []
        for index, name in enumerate(("A", "B", "C", "D")):
            element = GuiElement.create("rectangle", index + 1)
            element.name = name
            element.x = 20
            element.y = 20
            element.width = 40
            element.height = 40
            elements.append(element)
        screen.elements = elements
        session.mark_changed()
        widget = ScreenDesignerWidget(session)
        widget.selected_element_ids = {elements[1].id, elements[2].id}
        widget.selected_element_id = elements[2].id
        widget.refresh()

        widget._reorder_selected_elements("front")
        self.assertEqual([item.name for item in screen.elements], ["A", "D", "B", "C"])
        self.assertEqual(widget.canvas._element_at(QPointF(30, 30)).name, "C")
        self.assertFalse(widget.bring_front_button.isEnabled())
        self.assertTrue(widget.move_backward_button.isEnabled())

        widget._reorder_selected_elements("backward")
        self.assertEqual([item.name for item in screen.elements], ["A", "B", "C", "D"])
        widget._reorder_selected_elements("back")
        self.assertEqual([item.name for item in screen.elements], ["B", "C", "A", "D"])
        self.assertFalse(widget.send_back_button.isEnabled())
        widget._reorder_selected_elements("forward")
        self.assertEqual([item.name for item in screen.elements], ["A", "B", "C", "D"])
        self.assertEqual(
            [
                widget.element_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(widget.element_list.count())
            ],
            [item.id for item in screen.elements],
        )
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
        self.assertEqual(
            widget.pixel_asset_list.item(0).text(), "fixture.py / draw_badge"
        )
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
        self.assertEqual(element.asset_link_state, "current")
        self.assertEqual(element.asset_fingerprint, asset.fingerprint)
        self.assertTrue(element.asset_id)
        self.assertEqual(len(session.project.assets), 1)
        self.assertEqual(session.project.assets[0].frames[0], art.pixels)
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
        requested: list[tuple[str, int]] = []
        widget.project_asset_edit_requested.connect(
            lambda asset_id, frame: requested.append((asset_id, frame))
        )
        menu = widget._element_context_menu()
        open_action = next(
            action
            for action in menu.actions()
            if action.text() == "Open Asset in Pixel Editor"
        )
        self.assertTrue(open_action.isEnabled())
        widget._edit_selected_pixel_asset()
        self.assertEqual(requested, [(element.asset_id, 0)])
        widget.close()

    def test_invalid_asset_size_is_visible_and_can_be_baked_from_properties(
        self,
    ) -> None:
        """Expose and repair the exact state that blocks current-design simulation."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        art = PixelArt(4, 3)
        art.set_pixel(0, 0, 0xF800)
        asset = GuiPixelAsset("fixture::icon", "Icon", "fixture.py", "icon", art)
        element = widget.place_pixel_asset(asset)
        element.width = 7
        element.height = 5
        session.mark_changed()
        widget.selected_element_id = element.id
        widget.selected_element_ids = {element.id}
        widget.refresh()
        self.assertIn("Cannot run", widget.asset_size_status_label.text())
        self.assertTrue(widget.asset_bake_size_button.isEnabled())

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            widget._bake_selected_asset_size()

        baked = session.project.asset(element.asset_id)
        self.assertIsNotNone(baked)
        self.assertEqual((baked.width, baked.height), (7, 5))
        self.assertEqual(baked.link_state, "detached")
        self.assertEqual(element.asset_key, "")
        self.assertIn("Ready", widget.asset_size_status_label.text())
        widget.close()

    def test_personal_library_import_preserves_animation_as_detached_snapshot(
        self,
    ) -> None:
        """Import a reusable asset without linking the new project to library storage."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        first = PixelArt(2, 1)
        first.set_pixel(0, 0, 0x0000)
        second = PixelArt(2, 1)
        second.set_pixel(1, 0, 0x07E0)
        asset = GuiPixelAsset(
            "library::library_spinner",
            "Spinner",
            "Personal Asset Library",
            "Spinner",
            first,
            frames=(first, second),
            durations=(100, 200),
        )
        widget.set_library_assets([asset])
        self.assertEqual(widget.library_asset_list.count(), 1)
        self.assertTrue(widget.library_empty_label.isHidden())
        widget._add_selected_library_asset()
        element = session.current_screen().elements[0]
        widget.quick_assets_group.setChecked(True)
        self.assertTrue(widget.library_save_selected_button.isEnabled())
        save_requests: list[str] = []
        widget.library_element_save_requested.connect(save_requests.append)
        widget.library_save_selected_button.click()
        self.assertEqual(save_requests, [element.id])
        stored = session.project.asset(element.asset_id)
        self.assertEqual(element.asset_link_state, "detached")
        self.assertTrue(element.asset_id.startswith("snapshot_"))
        self.assertEqual(stored.pixel_frames(), [first, second])
        self.assertEqual(stored.durations, [100, 200])
        widget.close()

    def test_quick_assets_keep_records_lazy_until_selected_for_insertion(self) -> None:
        """Do not materialize every library animation while Quick Assets is hidden."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        first = PixelArt(2, 1)
        first.set_pixel(0, 0, 0xF800)
        second = PixelArt(2, 1)
        second.set_pixel(1, 0, 0x07E0)
        record = LibraryAsset.from_frames(
            "library_spinner", "Spinner", (first, second), (90, 180)
        )

        widget.set_library_records((record,))
        self.assertEqual(widget.library_assets, {})
        self.assertEqual(len(widget.library_records), 1)
        self.assertTrue(widget.library_asset_list.item(0).icon().isNull())

        widget._add_selected_library_asset()
        stored = session.project.assets[-1]
        self.assertEqual(stored.pixel_frames(), [first, second])
        self.assertEqual(stored.durations, [90, 180])
        widget.close()

    def test_app_gui_context_menus_expose_common_screen_element_and_library_actions(
        self,
    ) -> None:
        """Keep common App GUI operations one right-click away."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        self.assertEqual(
            widget.asset_tabs.currentIndex(), widget.reusable_assets_tab_index
        )
        widget.set_library_assets([])
        self.assertFalse(widget.library_empty_label.isHidden())
        self.assertIn("No reusable assets", widget.library_empty_label.text())
        self.assertIn("Right-click", widget.canvas_context_hint.text())
        widget._add_element("button")
        screen_actions = {
            action.text() for action in widget._screen_context_menu().actions()
        }
        element_actions = {
            action.text() for action in widget._element_context_menu().actions()
        }
        library_actions = {
            action.text() for action in widget._library_context_menu().actions()
        }
        self.assertIn("Duplicate screen", screen_actions)
        self.assertIn("Preview Layout (Safe)", screen_actions)
        self.assertIn("Run current design in Simulator", screen_actions)
        self.assertIn("Delete selected", element_actions)
        self.assertIn("Save selected asset to personal library", element_actions)
        self.assertIn("Add to current screen", library_actions)
        widget.close()

    def test_app_gui_uses_beginner_first_asset_and_property_sections(self) -> None:
        """Lead with reusable assets and keep deployment and interaction details folded."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        art = PixelArt(2, 2)
        record = LibraryAsset.from_frames("home", "Home", (art,))

        widget.set_library_records((record,))

        self.assertEqual(
            widget.asset_tabs.currentIndex(), widget.reusable_assets_tab_index
        )
        self.assertEqual(widget.asset_tabs.tabText(0), "Library (1)")
        self.assertEqual(widget.asset_tabs.tabText(1), "Python (0)")
        self.assertTrue(widget.library_empty_label.isHidden())
        self.assertFalse(widget.output_settings_group.isChecked())
        self.assertTrue(widget.preview_button.isHidden())
        self.assertEqual(widget.design_preview_button.text(), "Preview Layout")

        widget._add_element("button")
        widget.refresh()
        self.assertTrue(widget.content_properties_group.isVisibleTo(widget))
        self.assertTrue(widget.layout_properties_group.isVisibleTo(widget))
        self.assertFalse(widget.interaction_properties_group.isChecked())
        self.assertTrue(widget.asset_properties_group.isHidden())
        self.assertEqual(widget.element_flow_label.text(), "No interactions yet.")
        self.assertEqual(
            widget.open_flow_button.text(), "Add interaction in Screen Flow..."
        )
        widget.close()

    def test_screen_flow_context_menus_expose_graph_and_relation_actions(self) -> None:
        """Expose layout, navigation, and relation cleanup from Screen Flow."""
        session = DesignerSession()
        widget = ScreenFlowWidget(session)
        graph_actions = {
            action.text() for action in widget._graph_context_menu().actions()
        }
        relation_actions = {
            action.text() for action in widget._relation_context_menu().actions()
        }
        self.assertIn("Fit all nodes", graph_actions)
        self.assertIn("Auto-layout graph", graph_actions)
        self.assertIn("Set selected screen as start", graph_actions)
        self.assertIn("Add behavior node", graph_actions)
        self.assertIn("Debug selected behavior", graph_actions)
        self.assertIn("Insert Action into behavior connection", graph_actions)
        self.assertIn("Run current design", graph_actions)
        self.assertIn("Open Device Simulator", graph_actions)
        self.assertIn("Right-click", widget.graph_hint.text())
        self.assertEqual(
            [
                widget.flow_inspector_tabs.tabText(index)
                for index in range(widget.flow_inspector_tabs.count())
            ],
            ["Node", "Connect", "Issues", "Recipes"],
        )
        self.assertEqual(widget.open_simulator_button.text(), "Open Device Simulator")
        self.assertEqual(widget.run_simulator_button.text(), "▶ Run current design")
        self.assertEqual(widget.flow_test_group.title(), "Flow test")
        self.assertTrue(widget.source_combo.isHidden())
        widget.manual_relation_group.setChecked(True)
        self.assertFalse(widget.source_combo.isHidden())
        self.assertEqual(
            relation_actions,
            {"Update selected relation", "Delete selected relation"},
        )
        widget.close()

    def test_typed_behavior_ports_connect_and_trace_with_mouse(self) -> None:
        """Connect typed nodes directly and trace structure through a breakpoint."""
        project = GuiProject.create("Behavior Mouse Flow")
        event_node = FlowNode.create("event", 1, 120, 420)
        action_node = FlowNode.create("action", 2, 460, 420)
        action_node.breakpoint = True
        project.behavior_nodes.extend((event_node, action_node))
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.show()
        self.application.processEvents()
        source = widget.graph._behavior_port_position(
            event_node, event_node.port("event")
        ).toPoint()
        target = widget.graph._behavior_port_position(
            action_node, action_node.port("in")
        ).toPoint()
        QTest.mousePress(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            source,
        )
        QTest.mouseMove(widget.graph, target)
        QTest.mouseRelease(
            widget.graph,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target,
        )
        self.assertEqual(len(project.behavior_connections), 1)
        self.assertEqual(project.behavior_connections[0].source_port_id, "event")
        self.assertEqual(project.behavior_connections[0].target_port_id, "in")
        widget.graph.selected_behavior_node_ids = {event_node.id}
        widget.graph.primary_behavior_node_id = event_node.id
        widget._behavior_node_selected(event_node.id)
        self.assertIn("on_event_", widget.behavior_stub_label.text())
        widget._trace_selected_behavior()
        self.assertEqual(
            widget.graph.active_trace_node_ids, {event_node.id, action_node.id}
        )
        self.assertIn("BREAKPOINT", widget.simulator_result_label.text())
        widget.close()

    def test_data_behavior_edge_paints_without_opening_a_dialog(self) -> None:
        """Keep rendering pure for valid non-event behavior connections."""
        project = GuiProject.create("Data paint")
        data = FlowNode.create("data", 1, 120, 420)
        state = FlowNode.create("state", 2, 460, 420)
        project.behavior_nodes.extend((data, state))
        project.behavior_connections.append(
            BehaviorConnection.create(data.id, "value", state.id, "set")
        )
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        with patch.object(QMessageBox, "information") as information:
            widget.show()
            widget.graph.update()
            self.application.processEvents()
            information.assert_not_called()
        widget.close()

    def test_kind_change_cancel_preserves_node_edges_and_dirty_state(self) -> None:
        """Require confirmation before a node kind removes an existing edge."""
        project = GuiProject.create("Kind cancel")
        event = FlowNode.create("event", 1)
        action = FlowNode.create("action", 2)
        project.behavior_nodes.extend((event, action))
        edge = BehaviorConnection.create(event.id, "event", action.id, "in")
        project.behavior_connections.append(edge)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph.selected_behavior_node_ids = {action.id}
        widget.graph.primary_behavior_node_id = action.id
        widget._refresh_behavior_inspector()
        widget._restore_combo(widget.behavior_kind_combo, "data")

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            widget._apply_behavior_node()

        self.assertEqual(action.kind, "action")
        self.assertEqual(project.behavior_connections, [edge])
        self.assertFalse(session.dirty)
        widget.close()

    def test_kind_change_remaps_unambiguous_event_input(self) -> None:
        """Preserve an edge when the new node kind has one compatible endpoint."""
        project = GuiProject.create("Kind remap")
        event = FlowNode.create("event", 1)
        action = FlowNode.create("action", 2)
        project.behavior_nodes.extend((event, action))
        edge = BehaviorConnection.create(event.id, "event", action.id, "in")
        project.behavior_connections.append(edge)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph.selected_behavior_node_ids = {action.id}
        widget.graph.primary_behavior_node_id = action.id
        widget._refresh_behavior_inspector()
        widget._restore_combo(widget.behavior_kind_combo, "component")

        widget._apply_behavior_node()

        self.assertEqual(action.kind, "component")
        self.assertEqual(edge.target_port_id, "invoke")
        self.assertTrue(session.dirty)
        widget.close()

    def test_create_behavior_from_element_binds_stable_event_in_one_operation(
        self,
    ) -> None:
        """Create Event, Action, and edge without manual stable-ID copying."""
        project = GuiProject.create("Guided behavior")
        button = GuiElement.create("button", 1)
        button.name = "Publish"
        button.focusable = True
        project.screens[0].elements.append(button)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)

        created = widget.create_behavior_from_element(
            project.screens[0].id, button.id, "custom.handler"
        )

        self.assertTrue(created)
        self.assertEqual(len(project.behavior_nodes), 2)
        event, action = project.behavior_nodes
        self.assertEqual(event.operation, "event.ui")
        self.assertEqual(event.binding["event_id"], button.event_id)
        self.assertEqual(action.operation, "custom.handler")
        self.assertTrue(action.properties["handler"].startswith("on_handle_publish_"))
        self.assertEqual(len(project.behavior_connections), 1)
        self.assertTrue(session.dirty)
        widget.close()

    def test_guided_widget_value_flow_creates_read_and_handler_chain(self) -> None:
        """Create the complete scalar-value handoff without manual node wiring."""
        project = GuiProject.create("Guided widget value")
        choice = GuiElement.create("native", 1)
        choice.name = "Mode"
        choice.native_widget = "choice"
        choice.widget_items = ["Automatic", "Manual"]
        project.screens[0].elements.append(choice)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)

        created = widget.create_behavior_from_element(
            project.screens[0].id, choice.id, "guided.handle_value"
        )

        self.assertTrue(created)
        self.assertEqual(
            [node.operation for node in project.behavior_nodes],
            ["event.ui", "ui.read_value", "custom.handler"],
        )
        self.assertEqual(project.behavior_nodes[0].binding["widget_type"], "choice")
        self.assertEqual(project.behavior_nodes[1].properties["element_id"], choice.id)
        self.assertEqual(len(project.behavior_connections), 2)
        widget.close()

    def test_guided_toggle_branch_uses_checked_payload_field(self) -> None:
        """Configure a useful boolean branch directly from a Toggle element."""
        project = GuiProject.create("Guided toggle")
        toggle = GuiElement.create("native", 1)
        toggle.native_widget = "toggle"
        project.screens[0].elements.append(toggle)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)

        self.assertTrue(
            widget.create_behavior_from_element(
                project.screens[0].id, toggle.id, "guided.branch_value"
            )
        )

        compare = project.behavior_nodes[-1]
        self.assertEqual(compare.operation, "logic.compare")
        self.assertEqual(compare.properties["field"], "checked")
        self.assertEqual(compare.properties["comparison"], "true")
        widget.close()

    def test_guided_value_flow_rejects_non_readable_display_widget(self) -> None:
        """Do not fabricate values for Loading or Alert widgets."""
        project = GuiProject.create("Display only")
        loading = GuiElement.create("native", 1)
        loading.native_widget = "loading"
        project.screens[0].elements.append(loading)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)

        self.assertFalse(
            widget.create_behavior_from_element(
                project.screens[0].id, loading.id, "guided.handle_value"
            )
        )
        self.assertEqual(project.behavior_nodes, [])
        widget.close()

    def test_textbox_cannot_offer_an_activation_behavior_it_never_emits(self) -> None:
        """Keep scroll-only TextBox interaction out of element event flows."""
        project = GuiProject.create("Text viewer")
        textbox = GuiElement.create("native", 1)
        textbox.native_widget = "textbox"
        project.screens[0].elements.append(textbox)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)

        self.assertFalse(
            widget.create_behavior_from_element(
                project.screens[0].id, textbox.id, "custom.handler"
            )
        )
        self.assertEqual(project.behavior_nodes, [])
        widget.close()

    def test_operation_element_picker_lists_only_compatible_targets(self) -> None:
        """Prevent Set text from targeting Choice or Progress in the inspector."""
        project = GuiProject.create("Filtered targets")
        label = GuiElement.create("label", 1)
        choice = GuiElement.create("native", 2)
        choice.native_widget = "choice"
        choice.widget_items = ["A", "B"]
        progress = GuiElement.create("progress", 3)
        project.screens[0].elements.extend((label, choice, progress))
        node = FlowNode.create("action", 1)
        node.set_operation("ui.set_text")
        project.behavior_nodes.append(node)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph.selected_behavior_node_ids = {node.id}
        widget.graph.primary_behavior_node_id = node.id
        widget._refresh_behavior_inspector()
        target = widget.behavior_operation_fields["element_id"]

        target_ids = {target.itemData(index) for index in range(target.count())}

        self.assertIn(label.id, target_ids)
        self.assertNotIn(choice.id, target_ids)
        self.assertNotIn(progress.id, target_ids)
        widget.close()

    def test_operation_form_updates_known_fields_and_preserves_unknown_json(
        self,
    ) -> None:
        """Use node-specific controls while retaining future properties."""
        project = GuiProject.create("Operation form")
        node = FlowNode.create("action", 1)
        node.set_operation("mqtt.publish")
        node.properties.update(
            {"topic": "old/topic", "payload": "hello", "future": "keep"}
        )
        project.behavior_nodes.append(node)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph.selected_behavior_node_ids = {node.id}
        widget.graph.primary_behavior_node_id = node.id
        widget._refresh_behavior_inspector()
        topic = widget.behavior_operation_fields["topic"]
        self.assertIsInstance(topic, QLineEdit)
        for field in widget.behavior_operation_fields.values():
            self.assertIn("Example:", field.toolTip())
            self.assertNotIn("Enter Status Badge", field.toolTip())
        self.assertIn("publish", topic.toolTip().lower())
        topic.setText("new/topic")

        widget._apply_behavior_node()

        self.assertEqual(node.properties["topic"], "new/topic")
        self.assertEqual(node.properties["future"], "keep")
        widget.close()

    def test_keyboard_nudge_and_selected_layout_preserve_pinned_nodes(self) -> None:
        """Speed graph arrangement without moving locked or pinned contracts."""
        project = GuiProject.create("Fast layout")
        first = FlowNode.create("action", 1, 100, 400)
        second = FlowNode.create("action", 2, 300, 520)
        pinned = FlowNode.create("action", 3, 700, 700)
        pinned.pinned = True
        project.behavior_nodes.extend((first, second, pinned))
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph.selected_behavior_node_ids = {first.id, second.id, pinned.id}
        widget.graph.primary_behavior_node_id = first.id

        QTest.keyClick(widget.graph, Qt.Key.Key_Right)
        self.assertEqual(first.node_x, 101)
        self.assertEqual(second.node_x, 301)
        self.assertEqual(pinned.node_x, 701)
        pinned.node_x = 700
        widget._layout_selected_behavior_nodes(True)

        self.assertEqual(first.node_y, second.node_y)
        self.assertEqual((pinned.node_x, pinned.node_y), (700, 700))
        widget.close()

    def test_connection_drag_target_filter_rejects_incompatible_input(self) -> None:
        """Expose only compatible target ports during a typed connection gesture."""
        project = GuiProject.create("Compatible targets")
        data = FlowNode.create("data", 1, 100, 400)
        action = FlowNode.create("action", 2, 400, 400)
        state = FlowNode.create("state", 3, 700, 400)
        project.behavior_nodes.extend((data, action, state))
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph._behavior_connection_source = (data.id, "value")

        incompatible = widget.graph._behavior_port_position(action, action.port("in"))
        compatible = widget.graph._behavior_port_position(state, state.port("set"))

        self.assertIsNone(widget.graph._behavior_input_at(incompatible))
        self.assertEqual(widget.graph._behavior_input_at(compatible)[0].id, state.id)
        widget.close()

    def test_manual_connection_form_filters_to_compatible_targets(self) -> None:
        """Make the inspector guide the same typed choices as direct dragging."""
        project = GuiProject.create("Compatible inspector")
        data = FlowNode.create("data", 1, 100, 400)
        action = FlowNode.create("action", 2, 400, 400)
        state = FlowNode.create("state", 3, 700, 400)
        project.behavior_nodes.extend((data, action, state))
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.behavior_source_node_combo.setCurrentIndex(
            widget.behavior_source_node_combo.findData(data.id)
        )
        widget._refresh_behavior_source_ports()

        targets = {
            widget.behavior_target_node_combo.itemData(index)
            for index in range(widget.behavior_target_node_combo.count())
        }
        self.assertIn(state.id, targets)
        self.assertNotIn(action.id, targets)
        self.assertTrue(widget.add_behavior_connection_button.isEnabled())
        widget.close()

    def test_visibility_modes_hide_non_active_graph_lanes_and_edges(self) -> None:
        """Separate navigation and executable behavior without invisible hit targets."""
        project = GuiProject.create("Graph lanes")
        target = ScreenDesign.create("Target", 320, 320, 1)
        project.screens.append(target)
        project.connections.append(
            FlowConnection.create(project.screens[0].id, target.id, "open")
        )
        event = FlowNode.create("event", 1, 100, 500)
        action = FlowNode.create("action", 2, 400, 500)
        project.behavior_nodes.extend((event, action))
        project.behavior_connections.append(
            BehaviorConnection.create(event.id, "event", action.id, "in")
        )
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)

        widget.flow_visibility_combo.setCurrentIndex(
            widget.flow_visibility_combo.findData("screens")
        )
        self.assertEqual(widget.graph._visible_behavior_nodes(), [])
        self.assertIsNone(widget.graph._behavior_connection_at(QPointF(250, 520)))

        widget.flow_visibility_combo.setCurrentIndex(
            widget.flow_visibility_combo.findData("behavior")
        )
        self.assertEqual(widget.graph._visible_screens(), [])
        self.assertIsNone(widget.graph._connection_at(QPointF(250, 120)))
        widget.close()

    def test_runtime_trace_view_renders_bounded_execution_records(self) -> None:
        """Keep runtime execution visibly separate from structural tracing."""
        from pico_graphics_editor.behavior_runtime import RuntimeTraceEntry

        widget = ScreenFlowWidget(DesignerSession())
        widget.set_runtime_trace(
            [
                RuntimeTraceEntry(
                    1,
                    "node_a",
                    "mqtt.publish",
                    "in",
                    "success",
                    "success",
                    "{'token': '<redacted>'}",
                    2,
                )
            ]
        )
        self.assertEqual(widget.runtime_trace_list.count(), 1)
        self.assertIn("node_a", widget.runtime_trace_list.item(0).text())
        self.assertIn("redacted", widget.runtime_trace_list.item(0).text())
        widget.close()

    def test_live_validator_guides_and_marks_problem_nodes(self) -> None:
        """Expose validation in the main workflow instead of hiding it in a tab."""
        project = GuiProject.create("Assisted validation")
        action = FlowNode.create("action", 1)
        action.set_operation("ui.set_text")
        action.properties.update({"element_id": "missing", "text": "Hello"})
        project.behavior_nodes.append(action)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)

        self.assertIn(action.id, widget.graph.node_diagnostic_severity)
        self.assertEqual(widget.graph.node_diagnostic_severity[action.id], "error")
        self.assertIn("Flow blocked", widget.flow_assistant_banner.text())
        self.assertIn("errors", widget.flow_diagnostic_summary_label.text())

        widget._show_flow_validation()

        self.assertEqual(widget.flow_inspector_tabs.currentIndex(), 2)
        widget.flow_diagnostics_list.setCurrentRow(0)
        self.assertIn("Suggested fix", widget.flow_diagnostic_detail.text())
        widget.close()

    def test_flow_debugger_steps_continues_and_inspects_payload(self) -> None:
        """Drive actual bounded execution through the visible debugger controls."""
        project = GuiProject.create("Visible debugger")
        button = GuiElement.create("button", 1)
        button.name = "Start"
        button.text = "Run"
        project.screens[0].elements.append(button)
        event = FlowNode.create("event", 1)
        event.set_operation("event.ui")
        event.binding = {
            "screen_id": project.screens[0].id,
            "element_id": button.id,
            "event_id": button.event_id,
            "widget_type": "button",
        }
        state = FlowNode.create("state", 2)
        state.set_operation("state.set")
        state.properties.update({"key": "status", "value": "$value"})
        project.behavior_nodes.extend((event, state))
        project.behavior_connections.append(
            BehaviorConnection.create(event.id, "event", state.id, "in")
        )
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph.selected_behavior_node_ids = {event.id}
        widget.graph.primary_behavior_node_id = event.id
        widget._behavior_node_selected(event.id)

        widget._start_flow_debugger()

        self.assertIsNotNone(widget._debug_runtime)
        self.assertEqual(widget._debug_runtime.pending_count, 1)
        self.assertTrue(widget.runtime_step_button.isEnabled())

        widget._step_flow_debugger()
        self.assertEqual(widget.runtime_trace_list.count(), 1)
        self.assertIn("'value': 'Run'", widget.runtime_payload_view.toPlainText())

        widget._continue_flow_debugger()
        self.assertEqual(widget.runtime_trace_list.count(), 2)
        self.assertEqual(widget._debug_runtime.services["state"]["status"], "Run")
        self.assertIn("Debugger complete", widget.runtime_trace_limit_label.text())
        widget.close()

    def test_flow_debugger_simulates_service_error_and_timer_callback(self) -> None:
        """Exercise failure branches and delayed callbacks without real I/O."""
        project = GuiProject.create("Debugger scenarios")
        publish = FlowNode.create("action", 1)
        publish.set_operation("mqtt.publish")
        publish.properties.update({"topic": "demo/test", "payload": "hello"})
        failed = FlowNode.create("state", 2)
        failed.set_operation("state.set")
        failed.properties.update({"key": "route", "value": "error"})
        project.behavior_nodes.extend((publish, failed))
        project.behavior_connections.append(
            BehaviorConnection.create(publish.id, "error", failed.id, "in")
        )
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph.selected_behavior_node_ids = {publish.id}
        widget.graph.primary_behavior_node_id = publish.id
        widget.runtime_outcome_combo.setCurrentIndex(
            widget.runtime_outcome_combo.findData("error")
        )
        widget.runtime_service_response_edit.setText('{"reason": "offline"}')

        widget._start_flow_debugger()
        widget._continue_flow_debugger()

        self.assertEqual(widget._debug_runtime.trace[0].outcome, "error")
        self.assertEqual(widget._debug_runtime.services["state"]["route"], "error")
        widget.runtime_trace_list.setCurrentRow(0)
        self.assertIn("offline", widget.runtime_payload_view.toPlainText())
        widget.close()

        timer_project = GuiProject.create("Timer scenario")
        timer = FlowNode.create("timer", 1)
        timer.set_operation("timer.start")
        timer.properties.update({"timer_id": "refresh", "milliseconds": 50})
        elapsed = FlowNode.create("state", 2)
        elapsed.set_operation("state.set")
        elapsed.properties.update({"key": "elapsed", "value": True})
        timer_project.behavior_nodes.extend((timer, elapsed))
        timer_project.behavior_connections.append(
            BehaviorConnection.create(timer.id, "elapsed", elapsed.id, "in")
        )
        timer_session = DesignerSession()
        timer_session.set_project(timer_project)
        timer_widget = ScreenFlowWidget(timer_session)
        timer_widget.graph.selected_behavior_node_ids = {timer.id}
        timer_widget.graph.primary_behavior_node_id = timer.id

        timer_widget._start_flow_debugger()
        timer_widget._continue_flow_debugger()
        self.assertTrue(timer_widget.runtime_fire_timer_button.isEnabled())
        timer_widget._fire_debug_timers()
        timer_widget._continue_flow_debugger()

        self.assertTrue(timer_widget._debug_runtime.services["state"]["elapsed"])
        timer_widget.close()

    def test_connection_feedback_explains_incompatible_port_types(self) -> None:
        """Give a textual reason in addition to red/green port feedback."""
        project = GuiProject.create("Connection help")
        data = FlowNode.create("data", 1)
        action = FlowNode.create("action", 2)
        project.behavior_nodes.extend((data, action))
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.graph._behavior_connection_source = (data.id, "value")

        issue = widget.graph._behavior_input_issue(action, action.port("in"))
        widget.graph.interaction_feedback.emit(issue, "error")

        self.assertIn("Cannot connect", issue)
        self.assertIn("Cannot connect", widget.flow_assistant_banner.text())
        self.assertIn("#4a1f1f", widget.flow_assistant_banner.styleSheet())
        widget.close()

    def test_flow_fragment_ui_reuses_independent_node_copies(self) -> None:
        """Save and insert selected node structures through the visible library tab."""
        with tempfile.TemporaryDirectory() as folder:
            project = GuiProject.create("Reusable Flow")
            event_node = FlowNode.create("event", 1, 120, 420)
            action_node = FlowNode.create("action", 2, 460, 420)
            project.behavior_nodes.extend((event_node, action_node))
            session = DesignerSession()
            session.set_project(project)
            library = FlowFragmentLibrary(Path(folder) / "flows.json")
            widget = ScreenFlowWidget(session, flow_library=library)
            widget.graph.selected_behavior_node_ids = {
                event_node.id,
                action_node.id,
            }
            widget.graph.primary_behavior_node_id = event_node.id
            widget._refresh_behavior_inspector()
            with patch.object(QInputDialog, "getText", return_value=("Startup", True)):
                widget._save_flow_fragment()
            self.assertEqual(widget.flow_fragment_list.count(), 1)
            original_ids = {event_node.id, action_node.id}
            widget._insert_flow_fragment()
            inserted_ids = {node.id for node in project.behavior_nodes} - original_ids
            self.assertEqual(len(inserted_ids), 2)
            self.assertTrue(original_ids.isdisjoint(inserted_ids))
            widget.close()

    def test_click_added_elements_do_not_initially_overlap(self) -> None:
        """Cascade accessible click placement into unoccupied screen regions."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        for _ in range(3):
            widget._add_element("button")
        elements = session.current_screen().elements
        for index, element in enumerate(elements):
            for other in elements[index + 1 :]:
                separated = (
                    element.x + element.width <= other.x
                    or other.x + other.width <= element.x
                    or element.y + element.height <= other.y
                    or other.y + other.height <= element.y
                )
                self.assertTrue(separated)
        self.assertEqual(widget.selected_element_id, elements[-1].id)
        widget.close()

    def test_label_hides_irrelevant_asset_and_focus_rows(self) -> None:
        """Only expose properties that apply to a selected label."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        widget._add_element("label")
        widget.refresh()
        self.assertTrue(widget.asset_properties_group.isHidden())
        self.assertTrue(widget.interaction_properties_group.isHidden())
        self.assertTrue(
            widget.content_property_form.isRowVisible(widget.element_text_edit)
        )
        widget.close()

    def test_detached_draft_placement_preserves_pixels_without_source_link(
        self,
    ) -> None:
        """Embed an unsaved draft explicitly without masquerading as linked."""
        session = DesignerSession()
        widget = ScreenDesignerWidget(session)
        art = PixelArt(2, 1)
        art.set_pixel(0, 0, 0xF800)
        asset = GuiPixelAsset(
            "/tmp/assets.py::draw_draft",
            "draw_draft",
            "/tmp/assets.py",
            "draw_draft",
            art,
        )
        element = widget.place_pixel_asset(asset, "draft")
        self.assertEqual(element.asset_link_state, "draft")
        self.assertEqual(element.asset_key, "")
        self.assertEqual(element.asset_runs, [[0, 0, 1, 0xF800]])
        self.assertTrue(element.asset_id.startswith("snapshot_"))
        self.assertIsNotNone(session.project.asset(element.asset_id))
        restored = GuiProject.from_dict(session.project.to_dict())
        restored_element = restored.screens[0].elements[0]
        self.assertEqual(restored_element.asset_link_state, "draft")
        self.assertEqual(restored_element.asset_runs, [[0, 0, 1, 0xF800]])
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

    def test_device_profile_change_migrates_complete_layout_proportionally(self) -> None:
        """Keep placed content composed instead of cropping it at new edges."""
        project = GuiProject.create("Responsive layout")
        second_screen = ScreenDesign.create("Second", 320, 320, 1)
        project.screens.append(second_screen)

        background = GuiElement.create("rectangle", 1)
        background.x, background.y = 0, 0
        background.width, background.height = 320, 320
        button = GuiElement.create("button", 2)
        button.x, button.y = 64, 80
        button.width, button.height = 160, 64
        icon = GuiElement.create("icon", 3)
        icon.x, icon.y = 256, 256
        icon.width, icon.height = 32, 32
        full_screen_widget = GuiElement.create("native", 4)
        full_screen_widget.native_widget = "menu"
        full_screen_widget.x, full_screen_widget.y = 0, 0
        full_screen_widget.width, full_screen_widget.height = 320, 320
        project.screens[0].elements = [background, button, icon]
        second_screen.elements = [full_screen_widget]

        session = DesignerSession()
        session.set_project(project)
        widget = ScreenDesignerWidget(session)
        widget.profile_combo.setCurrentText("Cardputer 240x135")

        self.assertEqual((project.width, project.height), (240, 135))
        self.assertTrue(
            all((screen.width, screen.height) == (240, 135) for screen in project.screens)
        )
        self.assertEqual(
            (background.x, background.y, background.width, background.height),
            (0, 0, 240, 135),
        )
        self.assertEqual(
            (button.x, button.y, button.width, button.height),
            (48, 34, 120, 27),
        )
        self.assertEqual(
            (icon.x, icon.y, icon.width, icon.height),
            (197, 108, 14, 14),
        )
        self.assertEqual(
            (
                full_screen_widget.x,
                full_screen_widget.y,
                full_screen_widget.width,
                full_screen_widget.height,
            ),
            (0, 0, 240, 135),
        )
        for screen in project.screens:
            for element in screen.elements:
                self.assertGreaterEqual(element.x, 0)
                self.assertGreaterEqual(element.y, 0)
                self.assertLessEqual(element.x + element.width, screen.width)
                self.assertLessEqual(element.y + element.height, screen.height)

        session.undo()
        restored = session.project
        self.assertEqual(restored.profile, "PicoCalc 320x320")
        restored_button = restored.screens[0].elements[1]
        self.assertEqual(
            (
                restored_button.x,
                restored_button.y,
                restored_button.width,
                restored_button.height,
            ),
            (64, 80, 160, 64),
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

    def test_navigation_relations_require_nodes_for_conditions_and_actions(
        self,
    ) -> None:
        """Keep unsupported relation text visible only as removable legacy data."""
        project = GuiProject.create("Navigation semantics")
        target = ScreenDesign.create("Target", 320, 320, 1)
        project.screens.append(target)
        legacy = FlowConnection.create(project.screens[0].id, target.id, "open")
        legacy.condition = "is_ready"
        legacy.action = "prepare"
        project.connections.append(legacy)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.connection_list.setCurrentRow(0)

        self.assertFalse(widget.legacy_navigation_logic_group.isHidden())
        self.assertFalse(widget.condition_edit.isEnabled())
        self.assertFalse(widget.action_edit.isEnabled())
        widget._clear_selected_navigation_logic()
        self.assertEqual((legacy.condition, legacy.action), ("", ""))
        self.assertTrue(widget.legacy_navigation_logic_group.isHidden())

        widget.condition_edit.setText("must_not_be_copied")
        widget.action_edit.setText("must_not_be_copied")
        widget.source_combo.setCurrentIndex(0)
        widget.target_combo.setCurrentIndex(1)
        widget.trigger_edit.setText("next")
        widget._add_relation()
        created = project.connections[-1]
        self.assertEqual((created.condition, created.action), ("", ""))
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
        self.assertEqual(connection.trigger_event_id, button.event_id)
        widget.preview.setFocus()
        QTest.keyClick(widget.preview, Qt.Key.Key_Return)
        self.assertEqual(widget.simulated_screen_id, target.id)
        self.assertEqual(widget.preview.focused_element_id, icon.id)
        widget.close()

    def test_element_output_drop_on_empty_space_opens_action_palette(self) -> None:
        """Keep the fast button-to-action authoring gesture available."""
        project = GuiProject.create("Button action drop")
        button = GuiElement.create("button", 1)
        button.name = "Publish"
        project.screens[0].elements.append(button)
        session = DesignerSession()
        session.set_project(project)
        widget = ScreenFlowWidget(session)
        widget.show()
        self.application.processEvents()
        source_port = widget.graph._element_output_port(
            project.screens[0], button
        ).toPoint()
        destination = QPoint(520, 360)

        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            QTest.mousePress(
                widget.graph,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                source_port,
            )
            QTest.mouseMove(widget.graph, destination)
            QTest.mouseRelease(
                widget.graph,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                destination,
            )

        self.assertEqual(
            [node.operation for node in project.behavior_nodes],
            ["event.ui", "custom.handler"],
        )
        event_node, action_node = project.behavior_nodes
        self.assertEqual(event_node.binding["element_id"], button.id)
        self.assertEqual((action_node.node_x, action_node.node_y), (520, 360))
        self.assertEqual(len(project.behavior_connections), 1)
        self.assertEqual(project.behavior_connections[0].source_node_id, event_node.id)
        self.assertEqual(project.behavior_connections[0].target_node_id, action_node.id)
        self.assertIn("Added Custom handler", widget.flow_assistant_banner.text())
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
        self.assertEqual(widget.element_flow_label.text(), "1 interaction.")
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

    def test_drag_transaction_serializes_only_at_gesture_boundaries(self) -> None:
        """Keep pointer-rate changes cheap while retaining one undo snapshot."""
        project = GuiProject.create("Transaction")
        element = GuiElement.create("button", 1)
        project.screens[0].elements.append(element)
        session = DesignerSession()
        session.set_project(project)

        with patch.object(
            session.project,
            "to_dict",
            wraps=session.project.to_dict,
        ) as serialize:
            session.begin_transaction()
            self.assertEqual(serialize.call_count, 1)
            for position in range(1, 101):
                element.x = position
                session.mark_changed(False)
            self.assertEqual(serialize.call_count, 1)
            session.end_transaction()
            self.assertEqual(serialize.call_count, 2)

        self.assertTrue(session.dirty)
        self.assertTrue(session.can_undo())
        session.undo()
        self.assertEqual(session.current_screen().elements[0].x, 24)

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
