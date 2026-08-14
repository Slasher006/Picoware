# Golden Generated App Example

## Purpose

This document shows the complete expected shape of one application produced under Picoware Generated App Structure v1. It is a review fixture for the generation contract, not an implementation committed to the Picoware runtime.

The snippets are intentionally small and readable. They demonstrate ownership, imports, lifecycle delegation, event boundaries, linked assets, transparency, animation, and regeneration behavior.

The normative rules remain in:

- [GENERATED_APP_STRUCTURE.md](./GENERATED_APP_STRUCTURE.md)
- [GENERATION_BLUEPRINT_V1.md](./GENERATION_BLUEPRINT_V1.md)

## Example project

| Property | Value |
| --- | --- |
| Display name | Status Demo |
| Stable project ID | `project_status_demo_01` |
| Package | `status_demo` |
| Screen size | 320 × 320 |
| Start screen | Home |
| Screens | Home, Settings |
| Managed assets | Status Badge, Activity Spinner |

The Status Badge is a transparent static asset used twice. The Activity Spinner is a two-frame animated asset. Reusing the Status Badge demonstrates that linked assets remain canonical instead of being copied into each screen.

## Expected files

```text
Status Demo.py
status_demo/
├── __init__.py
├── app.py
├── generated_ui.py
├── generated_assets.py
└── generated_assets.pga
```

The editor project is stored separately as `Status Demo.picogui.json` and is not part of the runtime package.

## `Status Demo.py`

Ownership: developer-owned after first creation.

This file is deliberately thin. It owns only the application instance needed to delegate Picoware lifecycle calls.

```python
# Picoware generated application scaffold.
# This file is developer-owned after its first creation.

from status_demo.app import Application


_application = None


def start(view_manager):
    """Start the generated application base."""
    global _application
    _application = Application()
    return _application.start(view_manager)


def run(view_manager):
    """Delegate one Picoware input cycle."""
    if _application is not None:
        _application.run(view_manager)


def stop(view_manager):
    """Stop the application and release its state."""
    global _application
    if _application is not None:
        _application.stop(view_manager)
    _application = None
```

Later editor exports preserve this file byte-for-byte.

## `status_demo/__init__.py`

Ownership: developer-owned after first creation.

This package marker is intentionally minimal:

```python
# Status Demo application package.
```

Later editor exports preserve it byte-for-byte. Application initialization belongs in `app.py` unless the developer deliberately chooses otherwise.

## `status_demo/app.py`

Ownership: developer-owned after first creation.

This scaffold supplies ordinary lifecycle and input plumbing so the application base can run. `handle_event()` is the explicit extension point for real functionality.

```python
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


class Application:
    """Own user behavior around the generated presentation."""

    def __init__(self):
        self.view_manager = None
        self.ui = None

    def start(self, view_manager):
        """Initialize the application base and show its start screen."""
        self.view_manager = view_manager
        self.ui = GeneratedUI(view_manager)
        self.redraw()
        view_manager.input_manager.reset()
        return True

    def run(self, view_manager):
        """Handle structural navigation and delegate activation events."""
        if self.ui is None:
            return
        input_manager = view_manager.input_manager
        button = input_manager.button
        if button == -1:
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
            event_id = "event_navigation_back_01"
            if self.ui.handle_navigation(event_id):
                self.handle_event(event_id)
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
        draw.swap()

    def handle_event(self, event_id):
        """Implement application behavior for stable generated event IDs."""
        # Add application-specific behavior here.
        return False

    def stop(self, view_manager):
        """Release application-owned state."""
        self.ui = None
        self.view_manager = None
```

The editor does not later add event branches to `handle_event()`. The developer owns that method completely.

The scaffold implements only reusable structural behavior:

- Directional focus movement
- Native Picoware widget input delegation
- Center-button activation
- Declared screen navigation
- Back-button application exit when no structural back connection exists
- Redraw and lifecycle cleanup

It does not invent what Refresh Status or other application events should do.

## `status_demo/generated_assets.py`

Ownership: editor-owned and completely regenerable.

Implementation amendment: the inline tuple listing below is a rejected historical
prototype, not current generated output. Current `PGA3` puts the hash index, stable
IDs, typed image/WAV metadata, and payloads in `generated_assets.pga`;
`generated_assets.py` is a constant-size binary reader and streamer. Refer to
`PGA3_FORMAT.md` for the exact layout. The old listing remains only to show
why per-asset Python records were removed after parser- and catalogue-memory review.

```python
# @picoware-generated structure=1
# @picoware-generated role=assets
# @picoware-generated project=project_status_demo_01
# @picoware-generator version=1.1.0
# This file is editor-owned. Regenerate it instead of editing it manually.


_NAME = 0
_WIDTH = 1
_HEIGHT = 2
_ORIGIN_X = 3
_ORIGIN_Y = 4
_PALETTE = 5
_FRAMES = 6
_DURATIONS = 7


_ASSETS = {
    "asset_activity_spinner_01": (
        "Activity Spinner",
        3,
        3,
        0,
        0,
        (None, 0xFFE0, 0x4208),
        (
            (
                (0, 0, 3, 1, 1),
                (1, 1, 1, 1, 1),
                (0, 2, 3, 1, 2),
            ),
            (
                (0, 0, 3, 1, 2),
                (1, 1, 1, 1, 1),
                (0, 2, 3, 1, 1),
            ),
        ),
        (250, 250),
    ),
    "asset_status_badge_01": (
        "Status Badge",
        4,
        3,
        0,
        0,
        (None, 0xF800, 0xFFFF, 0x07E0, 0x0000),
        (
            (
                (0, 0, 2, 1, 1),
                (3, 0, 1, 1, 2),
                (1, 1, 2, 1, 3),
                (0, 2, 4, 1, 4),
            ),
        ),
        (),
    ),
}


def has_asset(asset_id):
    """Return whether a generated asset exists."""
    return asset_id in _ASSETS


def asset_size(asset_id):
    """Return an asset's natural dimensions."""
    asset = _ASSETS.get(asset_id)
    if asset is None:
        return None
    return asset[_WIDTH], asset[_HEIGHT]


def frame_count(asset_id):
    """Return the number of frames in a generated asset."""
    asset = _ASSETS.get(asset_id)
    return len(asset[_FRAMES]) if asset is not None else 0


def draw_asset(draw, asset_id, x, y, frame=0, scale=1):
    """Draw one compact generated asset."""
    asset = _ASSETS.get(asset_id)
    if asset is None:
        return False

    frames = asset[_FRAMES]
    try:
        frame = int(frame)
    except (TypeError, ValueError):
        frame = 0
    if frame < 0 or frame >= len(frames):
        frame = 0

    try:
        scale = max(1, int(scale))
    except (TypeError, ValueError):
        scale = 1

    origin_x = asset[_ORIGIN_X]
    origin_y = asset[_ORIGIN_Y]
    palette = asset[_PALETTE]
    for rect_x, rect_y, rect_width, rect_height, color_index in frames[frame]:
        draw._fill_rectangle(
            x + (origin_x + rect_x) * scale,
            y + (origin_y + rect_y) * scale,
            rect_width * scale,
            rect_height * scale,
            palette[color_index],
        )
    return True
```

Important properties demonstrated here:

- The status badge contains transparent gaps without treating black as transparent.
- Animation timing is metadata only.
- Invalid frame values return to frame zero.
- Integer scaling happens in one shared renderer.
- Neither asset owns a large dedicated drawing function.

## `status_demo/generated_ui.py`

Ownership: editor-owned and completely regenerable.

The generated UI imports only the shared asset renderer. It refers to assets by stable ID and never embeds their rectangle records.

```python
# @picoware-generated structure=1
# @picoware-generated role=ui
# @picoware-generated project=project_status_demo_01
# @picoware-generator version=1.1.0
# This file is editor-owned. Regenerate it instead of editing it manually.

from .generated_assets import draw_asset


class GeneratedUI:
    """Render generated screens and structural navigation."""

    def __init__(self, draw):
        self.draw = draw
        self.screen_id = "screen_home_01"
        self.focus_index = 0
        self.last_transition = "replace"

    def render(self):
        """Draw the active screen and focus indicator."""
        if self.screen_id == "screen_home_01":
            self._draw_home()
        elif self.screen_id == "screen_settings_01":
            self._draw_settings()
        self._draw_focus()

    def set_screen(self, screen_id):
        """Select a known screen by stable ID."""
        if screen_id not in ("screen_home_01", "screen_settings_01"):
            return False
        self.screen_id = screen_id
        self.focus_index = 0
        return True

    def focused_event(self):
        """Return the focused element's stable event ID."""
        events = self._focusable_events()
        if not events:
            return None
        self.focus_index %= len(events)
        return events[self.focus_index]

    def move_focus(self, step):
        """Move focus within the active screen."""
        events = self._focusable_events()
        if not events:
            return None
        self.focus_index = (self.focus_index + int(step)) % len(events)
        return events[self.focus_index]

    def activate_focused(self):
        """Apply structural navigation and return the activation event."""
        event_id = self.focused_event()
        if event_id is not None:
            self.handle_navigation(event_id)
        return event_id

    def handle_navigation(self, event_id):
        """Apply one declared screen-flow connection."""
        if (
            self.screen_id == "screen_home_01"
            and event_id == "event_open_settings_01"
        ):
            self.screen_id = "screen_settings_01"
            self.focus_index = 0
            self.last_transition = "replace"
            return True
        if (
            self.screen_id == "screen_settings_01"
            and event_id == "event_navigation_back_01"
        ):
            self.screen_id = "screen_home_01"
            self.focus_index = 0
            self.last_transition = "replace"
            return True
        return False

    def _focusable_events(self):
        """Return focusable events in configured order."""
        if self.screen_id == "screen_home_01":
            return (
                "event_open_settings_01",
                "event_refresh_status_01",
            )
        if self.screen_id == "screen_settings_01":
            return ("event_navigation_back_01",)
        return ()

    def _draw_home(self):
        """Draw the Home screen."""
        self.draw._fill_rectangle(0, 0, 320, 320, 0x0000)
        self.draw._text(16, 16, "Status Demo", 0xFFFF)
        draw_asset(
            self.draw,
            "asset_status_badge_01",
            20,
            52,
            scale=2,
        )
        draw_asset(
            self.draw,
            "asset_status_badge_01",
            80,
            52,
            scale=2,
        )
        self.draw._rectangle(16, 100, 136, 36, 0xFFFF)
        self.draw._text(20, 108, "Settings", 0xFFFF)
        self.draw._rectangle(16, 148, 136, 36, 0xFFFF)
        self.draw._text(20, 156, "Refresh Status", 0xFFFF)

    def _draw_settings(self):
        """Draw the Settings screen."""
        self.draw._fill_rectangle(0, 0, 320, 320, 0x0000)
        self.draw._text(16, 16, "Settings", 0xFFFF)
        draw_asset(
            self.draw,
            "asset_activity_spinner_01",
            20,
            52,
            frame=0,
            scale=3,
        )
        self.draw._rectangle(16, 100, 136, 36, 0xFFFF)
        self.draw._text(20, 108, "Back", 0xFFFF)

    def _draw_focus(self):
        """Draw the configured focus outline."""
        if self.screen_id == "screen_home_01":
            if self.focus_index % 2 == 0:
                self.draw._rectangle(14, 98, 140, 40, 0xFFE0)
            else:
                self.draw._rectangle(14, 146, 140, 40, 0xFFE0)
        elif self.screen_id == "screen_settings_01":
            self.draw._rectangle(14, 98, 140, 40, 0xFFE0)
```

The same Status Badge is drawn twice, but its manifest and resource spans occur only once.

## Conceptual source project relationships

The `.picogui.json` project retains richer editor information than the runtime modules. Conceptually, its important relationships are:

```text
project_status_demo_01
├── screen_home_01
│   ├── element_badge_primary_01 → asset_status_badge_01
│   ├── element_badge_secondary_01 → asset_status_badge_01
│   ├── element_settings_button_01 → event_open_settings_01
│   └── element_refresh_button_01 → event_refresh_status_01
├── screen_settings_01
│   ├── element_spinner_01 → asset_activity_spinner_01
│   └── element_back_button_01 → event_navigation_back_01
└── connections
    ├── screen_home_01 + event_open_settings_01 → screen_settings_01
    └── screen_settings_01 + event_navigation_back_01 → screen_home_01
```

This structure is explanatory rather than a replacement JSON schema. The existing project model remains authoritative until a separately reviewed schema migration is proposed.

## Expected runtime behavior

Without adding application functionality, the golden base must:

1. Start on Home.
2. Render two instances of one canonical Status Badge asset.
3. Move focus between Settings and Refresh Status.
4. Open Settings through the declared screen-flow connection.
5. Render frame zero of the Activity Spinner.
6. Return Home through the declared back connection.
7. Safely ignore the unimplemented Refresh Status event.
8. Exit through Picoware Back when the active screen has no matching structural back connection.
9. Stop without retaining its application or UI object.

The spinner does not animate automatically. Advancing frames is application behavior and remains for the developer to implement.

## Regeneration example

Assume the developer implements Refresh Status in `app.py`, then changes the Home background color and Status Badge pixels in the editor.

The next export must:

- Preserve `Status Demo.py` byte-for-byte.
- Preserve `status_demo/__init__.py` byte-for-byte.
- Preserve `status_demo/app.py` byte-for-byte, including the new behavior.
- Replace only the reviewed content of `generated_ui.py`, `generated_assets.py`, and `generated_assets.pga`.
- Keep both badge elements linked to the same asset ID.
- Produce one changed canonical badge resource span.
- Create backups of replaced editor-owned files.
- Produce no diff on an immediate unchanged export.

## Detached snapshot example

If the second badge is intentionally detached, the runtime shape changes as follows:

- `generated_assets.py` keeps `asset_status_badge_01` for the linked first badge.
- It adds snapshot metadata such as `snapshot_element_badge_secondary_01`, with pixels in `generated_assets.pga`.
- `generated_ui.py` changes only the second renderer call to use the snapshot ID.
- No rectangle data is copied into `_draw_home()`.
- Later edits to the canonical badge affect the first badge but not the detached snapshot.

## What this example deliberately excludes

- Network, storage, media, or sensor behavior
- Automatically generated application state
- Automatically generated timer management
- Optional or untracked asset sidecars; `generated_assets.pga` is a required reviewed package artifact
- Fractional runtime asset scaling
- Silent migration of legacy `generated_gui.py`
- Device-performance claims

These exclusions keep the example faithful to an application base structure rather than pretending to be a finished application.

## Golden review checklist

- [ ] Create-once files contain no editor-owned marker.
- [ ] Editor-owned files contain all required v1 header fields.
- [ ] User behavior has one obvious extension point.
- [ ] Generated UI contains no business logic.
- [ ] Generated UI contains asset IDs but no asset rectangle data.
- [ ] Transparency and visible black are distinct.
- [ ] A static asset has one frame.
- [ ] An animation has ordered frames and optional duration metadata.
- [ ] The same linked asset can be drawn multiple times from one record.
- [ ] Structural navigation uses stable screen and event IDs.
- [ ] Unknown events remain safe.
- [ ] Integer scaling is explicit.
- [ ] Arbitrary scaling is not silently generated.
- [ ] Regeneration preserves developer-owned files.
- [ ] Unchanged regeneration is deterministic.
