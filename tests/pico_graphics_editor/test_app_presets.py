"""Tests for small Picoware-native App GUI starters."""

from __future__ import annotations

import unittest

from pico_graphics_editor.app_presets import (
    APP_PRESETS,
    app_preset,
    build_app_preset,
)
from pico_graphics_editor.designer_model import DEVICE_PROFILES, GuiProject
from pico_graphics_editor.generated_app import project_preflight_diagnostics


class AppPresetTests(unittest.TestCase):
    """Verify every starter is small, portable, and independently editable."""

    def test_catalog_has_ten_unique_beginner_starters(self) -> None:
        """Keep the promised catalog small, stable, and unambiguous."""
        self.assertEqual(len(APP_PRESETS), 10)
        self.assertEqual(len({preset.id for preset in APP_PRESETS}), 10)
        self.assertEqual(len({preset.name for preset in APP_PRESETS}), 10)
        for preset in APP_PRESETS:
            self.assertTrue(preset.summary)
            self.assertTrue(preset.description)
            self.assertGreaterEqual(len(preset.screens), 1)
            self.assertLessEqual(len(preset.screens), 2)
            self.assertTrue(preset.capabilities)

    def test_all_presets_are_valid_for_every_device_profile(self) -> None:
        """Scale each starter project without breaking bounds or generation."""
        for preset in APP_PRESETS:
            for profile, dimensions in DEVICE_PROFILES.items():
                with self.subTest(preset=preset.id, profile=profile):
                    project = build_app_preset(preset.id, profile=profile)
                    self.assertEqual((project.width, project.height), dimensions)
                    self.assertEqual(project.start_screen_id, project.screens[0].id)
                    self.assertEqual(project.generated_app["starter_id"], preset.id)
                    self.assertEqual(
                        project.generated_app["starter_capabilities"],
                        list(preset.capabilities),
                    )
                    self.assertLessEqual(len(project.screens), 2)
                    for screen in project.screens:
                        self.assertLessEqual(len(screen.elements), 1)
                        self.assertEqual(
                            (screen.width, screen.height),
                            (project.width, project.height),
                        )
                        focus_orders = [
                            element.focus_order
                            for element in screen.elements
                            if element.focusable
                        ]
                        self.assertEqual(
                            focus_orders, list(range(1, len(focus_orders) + 1))
                        )
                        for element in screen.elements:
                            self.assertGreaterEqual(element.x, 0)
                            self.assertGreaterEqual(element.y, 0)
                            self.assertLessEqual(
                                element.x + element.width, screen.width
                            )
                            self.assertLessEqual(
                                element.y + element.height, screen.height
                            )
                    errors = [
                        diagnostic
                        for diagnostic in project_preflight_diagnostics(project)
                        if diagnostic.severity == "error"
                    ]
                    self.assertEqual(errors, [])
                    restored = GuiProject.from_dict(project.to_dict())
                    self.assertEqual(restored.to_dict(), project.to_dict())

    def test_routes_bind_to_real_source_elements_and_events(self) -> None:
        """Make every starter Screen Flow relation immediately editable."""
        for preset in APP_PRESETS:
            project = build_app_preset(preset.id)
            with self.subTest(preset=preset.id):
                self.assertEqual(len(project.connections), len(preset.routes))
                for connection in project.connections:
                    self.assertIsNotNone(project.screen(connection.source_id))
                    self.assertIsNotNone(project.screen(connection.target_id))
                    if not connection.source_element_id:
                        self.assertEqual(
                            connection.trigger_event_id,
                            "event_navigation_back_01",
                        )
                        continue
                    element = project.element(
                        connection.source_id, connection.source_element_id
                    )
                    self.assertIsNotNone(element)
                    assert element is not None
                    self.assertEqual(connection.trigger, element.event_name)
                    self.assertEqual(connection.trigger_event_id, element.event_id)

    def test_starters_use_native_widgets_and_builds_do_not_share_state(self) -> None:
        """Create widget examples without bundled app content or shared state."""
        first = build_app_preset("quick_note")
        second = build_app_preset("quick_note")
        self.assertNotEqual(first.project_id, second.project_id)
        self.assertEqual(first.assets, [])
        self.assertEqual(second.assets, [])
        self.assertEqual(first.screens[0].elements[0].kind, "native")
        self.assertEqual(first.screens[0].elements[0].native_widget, "keyboard")
        original_second_name = second.screens[0].elements[0].name
        first.screens[0].elements[0].name = "Changed locally"
        self.assertEqual(second.screens[0].elements[0].name, original_second_name)

    def test_two_screen_starters_include_forward_and_back_navigation(self) -> None:
        """Make compact workflows usable without turning Back into app exit."""
        for preset in APP_PRESETS:
            if len(preset.screens) != 2:
                continue
            project = build_app_preset(preset.id)
            with self.subTest(starter=preset.id):
                self.assertEqual(len(project.connections), 2)
                self.assertEqual(
                    {
                        connection.trigger_event_id
                        for connection in project.connections
                        if not connection.source_element_id
                    },
                    {"event_navigation_back_01"},
                )

    def test_catalog_covers_the_supported_native_widget_set(self) -> None:
        """Expose every supported native widget once across the starter catalog."""
        widget_ids = {
            element.native_widget
            for preset in APP_PRESETS
            for screen in build_app_preset(preset.id).screens
            for element in screen.elements
            if element.kind == "native"
        }
        self.assertEqual(
            widget_ids,
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

    def test_unknown_preset_is_rejected(self) -> None:
        """Fail clearly instead of silently choosing an unrelated workflow."""
        with self.assertRaisesRegex(KeyError, "Unknown app starter"):
            app_preset("missing")


if __name__ == "__main__":
    unittest.main()
