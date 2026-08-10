# Pico Graphics Editor UX Stabilization Plan

## Purpose

Implement a usability-focused `0.10.0` release for the standalone Qt6 Pico Graphics and GUI Designer. The current `0.9.0` editor has the required core capabilities, but its layout, save behavior, asset-link state, and cross-workspace workflow are difficult to understand and can produce inconsistent results.

This document is an implementation handoff. Follow it phase by phase. Do not add unrelated features while doing this work.

## Current baseline

- Repository branch: `feature/pico-graphics-editor`
- Baseline commit: `e3f67052 Complete managed pixel asset workflow`
- Editor version: `0.9.0`
- GUI project format: version `6`
- Current automated editor suite: `94` passing tests
- User-owned untracked file: repository-root `graphics.py`

The untracked `graphics.py` must not be edited, deleted, staged, or committed.

Before each phase, inspect `git status --short`. Preserve unrelated user changes. Do not push, merge, rebase, or update `origin/dev` unless the user explicitly requests it.

## Relevant files

- `pico_graphics_editor/window.py`
  - Main window, Pixel Art workspace, top-level actions, save behavior, asset handoff, recovery
- `pico_graphics_editor/canvas.py`
  - Pixel canvas, selection, clipboard, zoom, panning, transforms
- `pico_graphics_editor/designer.py`
  - App GUI workspace, hierarchy, properties, Screen Flow, preview controls
- `pico_graphics_editor/designer_model.py`
  - GUI project, elements, links, serialization, generated Python
- `pico_graphics_editor/live_simulator.py`
  - Shared live-simulator process and framebuffer behavior
- `pico_graphics_editor/source.py`
  - Managed/source-backed graphic detection and safe Python patching
- `pico_graphics_editor/README.md`
  - User-facing workflow documentation
- `tests/pico_graphics_editor/`
  - Editor tests; extend these with every phase

## Non-negotiable safety rules

1. Never import or execute scanned project Python in the Pixel Art scanner or existing-app importer.
2. Keep handwritten Python source-backed and conservative. Do not turn arbitrary source into a fully regenerated managed block.
3. Always show the exact Python diff before source writes.
4. Keep timestamped backups and atomic writes.
5. Never silently refresh embedded GUI assets from source.
6. Never silently discard pixel or GUI project edits.
7. Preserve embedded asset pixels when a source link is missing or broken.
8. Maintain backward compatibility with existing `*.picogui.json` projects.
9. Keep live simulator execution isolated in the existing child process.
10. Follow repository `AGENTS.md`, including concise Python docstrings for all new or edited functions.

## Confirmed UX problems

### Application shell

- The window requests `1480x900`, but the current pages force a minimum height of roughly `1241` pixels.
- The App GUI property column is not independently scrollable.
- Hidden workspace pages affect the whole window's minimum size.
- `Ctrl+S` applies the current Pixel Art source edit, while GUI projects use `Ctrl+Alt+S`.
- The current save target and project path are not persistently visible.
- Menus and global actions do not clearly follow the active workspace.

### Pixel Art

- The right inspector contains too many unrelated sections at once.
- Reference-image controls dominate ordinary pixel editing.
- Save/apply controls can be below the fold.
- Terminology is inconsistent: New Asset, Create New Python Graphic, Apply to Python, and Save Managed Asset.
- There is no Fit Canvas or one-to-one zoom action.
- The asset catalogue is a flat list without useful grouping or mode filters.
- Managed and source-backed modes are explained, but their visual editing layers are not clearly distinguished.
- Selection copy/paste looks like a system clipboard operation but currently uses only an internal pixel clipboard.
- Animation controls behave like a form instead of a visual frame timeline.
- Reference conversion is mixed into the normal editing inspector.

### App GUI

- Project controls are overcrowded in the top row.
- Screens, pixel assets, and reference controls compete in one narrow left column.
- Every property is shown for every element, including irrelevant disabled fields.
- The hierarchy is a long text row rather than a readable layer list.
- Click-added elements can overlap at the same default position.
- The asset list is short, truncated, and lacks search and grouping.
- The current GUI project path and save state are not prominent.
- Live preview requires moving to Screen Flow.
- Linked asset state is invisible: the user cannot see current, stale, missing, draft, or detached state.

### Pixel Art to App GUI handoff

- `Use in App GUI` only selects an asset; it does not clearly place it.
- Unsaved Pixel Art can be embedded in a GUI even when it is later discarded from Python.
- GUI asset links use absolute paths and have no relink workflow.
- The editor has no stale-source fingerprint or changed-source badge.
- A refresh that changes dimensions has no explicit geometry decision.

### Screen Flow and preview

- Mouse connection creation and the manual From/To form are presented equally.
- A navigation simulator and a live/designer preview are shown as separate concepts.
- Technical simulator settings are always visible.
- Ports rely mainly on color and have weak direction/meaning labels.
- Designing a screen, connecting it, and running it require frequent workspace switching.

## Target user workflow

The finished workflow should be understandable as one continuous sequence:

1. Open or create a pixel asset.
2. Edit and save it with an always-visible Save action.
3. Place it directly onto an application screen.
4. Configure only properties relevant to that element.
5. Connect its activation to another screen using the mouse.
6. Preview or run the current design without searching another workspace for controls.
7. Save the active workspace with `Ctrl+S` and always see the destination path.

## Phase 1: command routing and persistent state

### Goal

Make save behavior predictable before rearranging the interface.

### Required changes

- Introduce one active-workspace save dispatcher.
- `Ctrl+S` must perform:
  - Pixel Art: review and save/apply the current dirty pixel asset.
  - App GUI: save the GUI project.
  - Screen Flow: save the same GUI project.
- If the active workspace has no dirty document, `Ctrl+S` must not save or apply an unrelated hidden document.
- Keep `Ctrl+Shift+S` for context-appropriate Save As or export only if clearly labeled.
- Remove the special `Ctrl+Alt+S` requirement from normal GUI project saving.
- Add a persistent document strip containing:
  - Active workspace name
  - Current source/project filename
  - Full path tooltip
  - Saved/modified state
  - Primary Save button
- Make File/Edit menus active-workspace aware. Hide or disable unrelated source actions.
- Standardize user-facing terminology:
  - `New Asset`
  - `Save Asset`
  - `Export PNG`
  - `New GUI Project`
  - `Save GUI Project`
  - `Apply Edits to Existing App`

### Tests

- `Ctrl+S` in each workspace targets only that workspace.
- A dirty hidden Pixel Art document cannot be applied from App GUI by `Ctrl+S`.
- Screen Flow and App GUI save the same GUI project path.
- Document strip text updates after open, Save As, save, discard, and source rescan.

### Acceptance criteria

- The user can always identify what `Ctrl+S` will save.
- The target path and dirty state are visible without opening a menu.

## Phase 2: responsive application shell

### Goal

Make the complete application usable at `1366x768`.

### Required changes

- Ensure each workspace can shrink independently inside the main tab widget.
- Put long Pixel Art and GUI property panels inside independent `QScrollArea` widgets.
- Prevent hidden tabs from forcing a `1241`-pixel minimum height.
- Make left and right sidebars collapsible.
- Add sensible minimum widths to the center canvas without forcing the whole window larger than the display.
- Store splitter sizes and collapsed state with `QSettings`.
- Keep primary actions outside scrolling property content.
- Test Qt scaling at `100%`, `125%`, and `150%` where practical.

### Tests

- Instantiate the main window at `1366x768` offscreen.
- Assert that the window does not grow beyond the requested screen-sized geometry.
- Assert that Save, Fit, Preview, and workspace tabs remain reachable.
- Assert that each inspector has its own scrollbar when its content is taller than the viewport.

### Acceptance criteria

- No required control is outside a `1366x768` screen.
- The application window never depends on a display taller than `768` pixels.

## Phase 3: Pixel Art information architecture

### Goal

Make ordinary painting the default experience and move import/reference work out of the way.

### Layout

- Left: asset browser
- Center: canvas and persistent canvas controls
- Right: contextual inspector
- Top document strip: Save, undo/redo, selected tool, zoom, Fit, 1:1

### Asset browser

- Group assets by source file.
- Provide filters for:
  - Managed
  - Source-backed
  - Static
  - Animated
- Retain text search.
- Show compact mode badges instead of repeating category text below every thumbnail.
- Preserve the selected asset and filters after rescan when possible.
- Show the source path in a tooltip and details area.

### Canvas controls

- Add `Fit Canvas`, `1:1`, and `Center Canvas` actions.
- Keep mouse-wheel zoom and middle-button panning.
- Make the selected drawing tool visually prominent.
- Add selection handles or a clearly visible move cursor/state.
- Keep transforms undoable.
- Use the Qt system clipboard:
  - Store an application-specific lossless pixel format.
  - Also place a PNG representation on the clipboard.
  - Accept the application format first and PNG as a fallback.
- Clipboard behavior must work between assets and separate editor windows.

### Managed versus source-backed display

- Keep the existing safety restrictions.
- Add a compact persistent mode badge near the asset title.
- For source-backed assets, visually distinguish:
  - Original source rendering
  - Generated overlay edits
  - Composite preview
- Provide an Original/Composite/Edits view control or an equivalent clear representation.
- Do not imply that transparent deletion can remove procedural source operations.

### Contextual inspector

Only show sections relevant to the current selection and asset:

- Asset details
- Selection
- Palette
- Animation, only for animated assets
- Source notes, collapsed by default
- Export

Move image conversion into a dedicated `Import from Image...` dialog or workflow.

### Acceptance criteria

- Save, Fit, zoom, and active mode are visible without inspector scrolling.
- Reference controls do not occupy the ordinary painting workspace.
- The user can distinguish original source pixels from overlay edits.

## Phase 4: visual animation timeline

### Goal

Replace the form-like frame editor with a direct timeline.

### Required changes

- Add a horizontal frame strip with thumbnails.
- Highlight the active frame clearly.
- Support drag-to-reorder.
- Provide Add, Duplicate, Delete, Previous, Next, Play, and Onion Skin controls.
- Show a dirty marker per frame.
- Mark protected source-backed frames with a lock badge.
- Prevent deleting protected handwritten source frames.
- Managed animations must save every frame in timeline order.
- Keep imported sprite-sheet/GIF workflows, but launch them from the timeline or Import flow.

### Tests

- Drag reordering changes managed save order.
- Per-frame edits and structural changes mark the correct dirty state.
- Protected source-backed frames cannot be deleted.
- Add, duplicate, delete, reorder, undo, save, reopen, and playback retain expected content.

## Phase 5: App GUI restructuring

### Goal

Make screen composition primary and hide irrelevant detail.

### Project header

- Show project name, filename, dirty state, device profile, Save, and Preview.
- Move custom dimensions and advanced profile settings into a project-settings dialog or expandable section.

### Left sidebar

- Separate Screens and Assets into tabs or collapsible sections.
- Keep screen thumbnails.
- Add pixel-asset search, file grouping, and link-state filters.
- Keep screen references in a screen-specific command rather than permanent sidebar space.

### Element palette and placement

- Keep drag-and-drop as the primary behavior.
- Show a placement ghost while dragging.
- Retain click-to-add as a secondary accessible behavior.
- Cascade click-added elements so they never initially overlap.
- Select and focus the new element after placement.
- Use meaningful default names.

### Hierarchy

Each row should show:

- Type icon
- Element name
- Visibility toggle
- Lock toggle
- Focusable indicator
- Asset-link state when applicable

Normal hierarchy use must not require horizontal scrolling.

### Contextual properties

Place the property inspector in a scroll area and divide it into:

- Basics
- Geometry
- Appearance
- Interaction
- Focus
- Advanced/source

Only show applicable fields. Examples:

- Label: text and appearance; no pixel-asset refresh controls.
- Panel: geometry and colors; no activation event unless interaction is enabled.
- Icon with a link: asset status, Refresh, Edit Source, Relink, and Detach.
- Focus settings: visible only when keyboard focus is enabled.

### Acceptance criteria

- Adding three elements by clicking produces three non-overlapping selections.
- A label does not display irrelevant asset-link controls.
- The project path and save state remain visible.
- Asset search works with large source folders.

## Phase 6: safe and portable asset links

### Goal

Prevent GUI/source divergence while keeping GUI projects portable.

### Data model

Advance the GUI project format from `6` to `7` only when the new fields are implemented.

Store asset-link information separately from the embedded pixel snapshot:

- Function/qualified name
- Source path relative to the GUI project or known scan root when possible
- Optional absolute fallback path
- Source/content fingerprint
- Link state
- Embedded pixel snapshot

Supported states:

- `current`: embedded pixels match the known source fingerprint
- `modified`: source or editor pixels differ from the embedded snapshot
- `missing`: source cannot be located
- `draft`: pixels came from an unsaved Pixel Art document
- `detached`: embedded pixels intentionally have no source link

### Handoff behavior

Replace ambiguous `Use in App GUI` behavior with explicit actions:

- `Place on Current Screen`
- `Select in Asset Library`

If the Pixel Art document is dirty, ask:

- `Save and Place`
- `Embed Detached Draft`
- `Cancel`

Do not silently keep a source link on an embedded draft that was never saved.

### Refresh behavior

- Never refresh automatically.
- Show stale/missing badges.
- Provide Refresh Selected and Refresh All.
- If dimensions change, ask:
  - Keep element geometry
  - Resize element to asset
  - Cancel
- Provide Relink and Detach actions.
- Preserve embedded pixels after broken links or project relocation.

### Migration

- Load version-6 `asset_key` values.
- Extract the old absolute path and qualified name where possible.
- Keep existing `asset_runs` as the authoritative embedded fallback.
- Mark unresolved legacy links as `missing`, never discard them.

### Tests

- Saved asset placement produces `current` state.
- Dirty handoff offers all three decisions.
- Detached drafts survive save/reopen.
- Moving a GUI project resolves relative links.
- Missing links preserve embedded rendering.
- Relink updates metadata without changing pixels until Refresh is chosen.
- Version-6 projects load without data loss.

## Phase 7: shared preview and simplified Screen Flow

### Goal

Make screen design, navigation, and live testing feel like one workflow.

### Shared preview

- Reuse one preview controller and one live-simulator child process.
- Expose a compact Preview panel in App GUI and Screen Flow.
- Provide Designer, Live, and Compare modes.
- Preserve active screen and simulator state when switching workspaces.
- App GUI must be able to start the current unsaved design directly.
- Keep keyboard and touch forwarding behavior.

### Screen Flow

- Make port dragging the primary way to create relations.
- Add direction labels/tooltips and non-color-only indicators.
- Show a rubber-band connection preview and invalid-target feedback.
- Selecting an edge opens a contextual relation inspector.
- Move manual From/To relation creation behind an `Advanced` or `Add Manually` action.
- Merge event testing into the shared Preview panel.
- Remove the separate always-visible navigation simulator panel.
- Hide board, launch target, reload, and capture controls until Live mode is selected.
- Keep Fit All Nodes, middle-button panning, and wide zoom range.
- Provide direct `Open Screen in Designer` from nodes and relation destinations.

### Tests

- App GUI and Screen Flow share one simulator lifecycle.
- Switching tabs does not restart live preview unnecessarily.
- Mouse-created relations remain the default path.
- Manual creation remains available under Advanced.
- Ports remain understandable without relying only on blue/green colors.

## Phase 8: visual polish and accessibility

- Use consistent spacing, button sizes, and visual hierarchy.
- Style primary actions consistently.
- Reduce large blocks of disabled controls.
- Add practical empty states with one recommended next action.
- Keep tooltips short and include `Example:` when helpful.
- Make every operation keyboard accessible.
- Add visible keyboard focus throughout the desktop UI.
- Do not use color alone for modes, ports, dirty state, or errors.
- Confirm readable behavior in light and dark desktop palettes.
- Keep status messages concise and include the affected filename when relevant.

## Testing requirements

Run targeted tests after each phase, then the complete editor suite:

```bash
ruff format pico_graphics_editor tests/pico_graphics_editor
ruff check pico_graphics_editor tests/pico_graphics_editor
python3 -m compileall -q pico_graphics_editor
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests/pico_graphics_editor -p 'test_*.py'
```

Add tests for every new behavior. Do not weaken existing source-safety, backup, recovery, animation, GUI, graph, or simulator tests.

## Manual acceptance matrix

Complete these workflows at both `1366x768` and `1920x1080`:

1. Create a blank managed asset, draw transparent pixels, save, close, and reopen.
2. Edit a source-backed Pico Bomber graphic and verify that only the reviewed overlay changes.
3. Create an animation, reorder frames by dragging, delete one, save, and reopen.
4. Copy pixels between two assets using the system clipboard.
5. Place a saved asset directly on a GUI screen.
6. Place an unsaved draft using each handoff decision.
7. Change the source asset and verify stale status, explicit refresh, and dimension handling.
8. Move a GUI project and verify relative-link resolution and missing-link fallback.
9. Add several GUI elements by click and drag without initial overlap.
10. Configure focus and activation without irrelevant property fields being visible.
11. Connect two screens with the mouse and edit the selected relation.
12. Start live preview from App GUI, switch to Screen Flow, and keep the same process and active screen.
13. Save from all three workspaces with `Ctrl+S` and verify the exact destination.
14. Recover an autosaved pixel draft and GUI project.

## Recommended commit sequence

Use small, reviewable commits in this order:

1. `Route save commands by active workspace`
2. `Make editor workspaces responsive`
3. `Restructure pixel editing workflow`
4. `Add visual animation timeline`
5. `Restructure GUI designer workflow`
6. `Add portable explicit asset links`
7. `Unify designer and flow previews`
8. `Polish editor accessibility and guidance`
9. `Document and validate UX stabilization release`

Run the complete editor test suite before every commit that changes serialization, source output, recovery, or simulator behavior.

## Definition of done

The work is complete only when:

- The full editor fits and remains usable at `1366x768`.
- `Ctrl+S` always saves the active workspace and never a hidden dirty document.
- The current save path and dirty state are always visible.
- Pixel Art's primary controls require no inspector scrolling.
- App GUI shows only context-relevant element properties.
- Click-added elements do not initially overlap.
- Unsaved Pixel Art cannot silently masquerade as a saved linked GUI asset.
- Linked assets clearly report current, modified, missing, draft, or detached state.
- Existing version-6 GUI projects load without losing embedded pixels.
- App GUI can launch and retain the same live preview used by Screen Flow.
- Mouse relation creation is the obvious default.
- All automated tests pass.
- The complete manual acceptance matrix passes.
- Documentation matches the final interface.
- `graphics.py` remains untouched and uncommitted.

Do not call the release finished because the controls merely exist. Validate the complete paths above from creation through save, reopen, refresh, live preview, and recovery.
