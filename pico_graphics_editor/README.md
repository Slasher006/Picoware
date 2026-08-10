# Pico Graphics and GUI Designer

Pico Graphics and GUI Designer is a standalone Qt6 tool with three workspaces:

- **Pixel Art** discovers graphics embedded in Python renderer code and makes them mouse-editable.
- **App GUI** creates complete application screens with draggable visual elements.
- **Screen Flow** connects screens through event-driven relationships and previews navigation.

Python source is scanned without importing or executing the project. Every generated source change is presented as an exact diff before writing.

## Run

Install the desktop dependency:

```bash
python3 -m pip install -r pico_graphics_editor/requirements.txt
```

The embedded live view additionally requires the Unix `micropython` executable used by Picoware's simulator. The designer and source-safe previews remain available when it is not installed.

Open the editor:

```bash
./pico-graphics-editor.sh
```

Open a renderer or project immediately:

```bash
./pico-graphics-editor.sh builds/MicroPython/apps_unfrozen/games/pico_bomber/render.py
```

## Workflow

1. Open a Python file or project folder.
2. Select a detected graphic from the thumbnail catalogue.
3. Choose any inferred parameter variants.
4. Draw with the select, pencil, eraser, fill, line, rectangle, or picker tool.
5. Review the actual-size preview.
6. Select **Review and apply to Python**.
7. Inspect the exact source diff before applying it.

The inspector labels every selected graphic as either a **managed asset** or a **source-backed asset**. Assets created by this editor are managed: pixels can be transparent and the complete generated block can be resized or rewritten. Handwritten Python remains source-backed: edits are emitted as conservative overlays, so destructive transparency, pasting transparent pixels, and document resizing stay disabled.

Use **Select** or press `S` to drag a rectangular selection. A selection can be moved with the mouse, cut, copied, pasted, deleted, flipped, rotated, or cropped. **Resize Canvas** preserves pixel size and optionally centers the existing art; **Scale Artwork** uses nearest-neighbor scaling. All pixel-changing operations participate in undo and redo. For source-backed graphics, selection and copy remain available without rewriting arbitrary drawing logic.

The eraser clears to transparency for managed assets and paints with the configured RGB565 background color for source-backed assets. Right-click the canvas to pick an existing color. Use the mouse wheel over the canvas to zoom in or out, and hold the middle mouse button while dragging to pan a zoomed canvas. The toolbar zoom value follows the wheel automatically.

Leaving a changed asset opens a **Save**, **Discard**, or **Cancel** prompt. Dirty pixel work is also atomically autosaved after a short delay and can be reopened from **Pixel Art > Recover Autosaved Pixel Asset...**. A successful save or an explicit discard removes that recovery draft.

## Reference images

The **Reference image** panel supports PNG, JPEG, WebP, BMP, and GIF files. A reference can be placed behind or above the active pixels with adjustable opacity, fit mode, scale, offset, rotation, and horizontal or vertical flipping.

**Convert to editable pixels** performs deterministic local conversion with:

- Configurable palette size
- RGB565 output colors
- Optional Floyd-Steinberg dithering
- Transparent-image support
- Undoable application to the pixel canvas

No AI conversion is used.

**New Asset** in the Pixel Art toolbar creates a blank transparent asset, a copy of the current canvas, an asset from the current reference, or an animation from an image file, sprite sheet, or already imported frames. The destination is a marked module-level Python drawing function. After the reviewed RGB565 runs are written, the exact generated function is automatically loaded into the pixel editor and appears immediately in the App GUI pixel-asset catalogue. Further mouse edits use **Review and save managed asset** to rewrite only that editor-owned block.

## Animation frames

Functions with inferred `frame`, `phase`, or `animation_time` variants get a dedicated **Animation frames** panel. Use **Previous** and **Next** or the frame selector to edit each state, **Play** to preview the sequence, and **Show previous frame** for an onion-skin drawing guide.

Animated GIF and WebP files can be imported directly. Static sprite sheets can be divided by frame width, height, outer margin, and spacing. Frames can be added, duplicated, reordered, and previewed at an adjustable interval. Managed animation frames can also be deleted, and their complete saved order is written back together. Frames inferred from handwritten source remain protected; only imported additions can be removed there.

Save or discard an edited frame before selecting another frame. A managed animation save preserves every frame, while a source-backed edit generates an overlay limited to the selected frame. The exact source diff is always shown before writing.

## App GUI workspace

GUI projects are stored as editable `*.picogui.json` files independently from generated Python. Built-in device profiles include PicoCalc, Cardputer, Flipper Zero, and round displays. The **Custom** profile accepts arbitrary width and height values.

Screens support these visual elements:

- Buttons and labels
- Panels and rectangles
- Icons with optional Python asset-function bindings
- Lists
- Progress indicators

Drag buttons, labels, panels, rectangles, icons, lists, and progress indicators from the element palette directly onto the canvas. The **Pixel assets** catalogue also shows graphics discovered in the currently opened Python file or folder; drag one onto the screen, double-click it, or use **Add selected asset**. Its RGB565 pixels are embedded in the GUI project so designer previews, saved projects, Python exports, and the live simulator keep the same appearance without importing the original drawing function. Palette buttons can still be clicked for quick insertion. Elements can then be selected, dragged, resized, renamed, hidden, recolored, and positioned numerically. Mouse-wheel zoom, an optional grid, configurable snapping, alignment, and even-spacing controls make precise layouts faster.

Use **Use in App GUI** from Pixel Art to switch workspaces with the active asset selected and ready to drag. Icons inserted this way retain their source-asset link. Select a linked icon and choose **Refresh pixel asset** to pull in its latest saved pixels, or **Edit in Pixel Art** to reopen the original graphic. Refresh is explicit, so an existing GUI design never changes silently after a source rescan.

Shift-click or drag a selection rectangle to edit several elements together. Arrow keys move the selection by one pixel, Shift+Arrow moves it by ten, Ctrl+D duplicates it, and Delete removes design-only elements. The hierarchy acts as a layers panel: drag rows to reorder drawing depth, or use the lock and visibility controls. Screen rows include live thumbnails of their actual contents and can also be reordered by dragging.

Focusable elements have an explicit focus order. Enable **Focus order** above the canvas to see numbered badges. Each element can also be input-enabled or disabled and can define an **Activation event** independently from its display name. Configure its focus indicator as an outline, corner brackets, underline, or hidden state, with an individual RGB565 color, thickness, and spacing. The navigation preview and live simulator use the same focus appearance. The preview supports Tab or arrow-key focus, Enter or Space activation, and direct mouse activation. Generated GUI classes expose `focused_element`, `move_focus`, and `activate_focused` helpers for Pico keyboard integration.

Each screen has its own background color and can use a temporary full-screen reference image. Screens can be added, duplicated, deleted, and opened directly from the flow graph. GUI edits use complete-project undo and redo. Dirty projects are atomically autosaved for manual recovery from **GUI Project > Recover Autosaved GUI Project...**. Every successful explicit save, including the first one, also creates a timestamped independent safety copy in the application-data `backups/gui-projects` directory. Use **GUI Project > Recover Saved GUI Backup...** to open one without overwriting the original project path.

Use **GUI Project** in the menu bar to create, open, save, or export a project. Python export generates one marked renderer class containing screen draw methods and navigation handling. Re-export replaces only that marked block.

### Editing existing applications

Choose **GUI Project > Import Existing App...** and select one Python file or an application folder. The importer safely parses the source, discovers drawing functions and state-based screens, and infers simple navigation relations across files without importing or running the application.

Direct rectangle and text calls with static arguments become draggable source-backed elements. Runtime-dependent loops, branches, helpers, positions, and other expressions remain visible as orange `[code]` elements. These locked elements cannot be moved or rewritten, so application logic is preserved.

Editable inferred relations can change a simple event value or target state. Relations based on complex runtime conditions are marked `[locked]`. New screens, elements, and relations may still be added to the designer project, but only items with an imported source anchor are written back to the existing application.

Use **GUI Project > Apply Edits to Existing App...** to review a combined multi-file diff. The editor refuses to apply if any source file changed since import, parses every proposed result, rechecks the files after the review dialog, and creates timestamped backups before writing. Save the companion `*.picogui.json` project to retain layout and import metadata between sessions.

## Screen Flow workspace

Every application screen is represented as a draggable node containing a live rendering of that screen. Focusable buttons, icons, lists, and other configured elements appear as rows below their screen with individual green input and blue output ports. Directed relations define:

- Source and target screens or elements
- Event trigger
- Optional named condition
- Optional named action
- `replace`, `push`, `modal`, or `back` transition behavior

One screen is marked as the start screen. Double-click a graph node to open it in the GUI designer. The navigation simulator accepts event names, follows matching relations, reports conditions and actions, and renders the resulting screen without running Pico hardware code.

Drag from any blue screen or element port to a green screen or element port. An element source automatically uses its configured activation event. Connecting to a destination element opens that element's screen and gives it initial keyboard focus. The relation form also lists all available screen and element endpoints for precise selection. Screen-only relations from older projects continue to work unchanged. Click a relation line to select and edit it, or press Delete to remove a design-only relation. **Auto-layout graph** arranges screens by navigation depth. Nodes remain independently draggable. Use the mouse wheel to zoom from 5% to 200%, hold the middle mouse button and drag to pan the view, or select **Fit all nodes** for an automatic overview.

### Live simulator preview

The Screen Flow preview supports **Designer**, **Live simulator**, and **Compare** views. Live mode launches Picoware's real MicroPython simulator as an isolated child process and embeds its RGB565 framebuffer directly in the Qt workspace; it does not open the separate SDL viewer window. **Current design** is the default launch mode: starting or restarting live view builds a temporary app from the in-memory project and opens the currently active screen, including unsaved designer edits, without changing application source.

For imported applications, the editor infers an Application or Game route from the imported path. The launch type, target name, and board remain editable, so external or unusual projects can be configured manually. Start, stop, or restart the child process from the preview toolbar. When **Reload on source changes** is enabled, saved Python changes restart the simulator automatically.

The live framebuffer receives keyboard focus automatically when the simulator starts. Click it to return focus after using another editor control; a cyan frame shows that Picoware input is active. Arrow keys, Enter, Escape, Tab, function keys, printable text, and touch/mouse input are forwarded through the simulator's normal input protocol. Network access is offline and audio is silent for predictable editor sessions.

Use **Capture live frame for screen** to associate the current real framebuffer with a selected designer screen. The captured frame appears in that screen's list thumbnail and graph node until it is cleared or another GUI project is opened. Captures are intentionally transient and are not written into application source or the `*.picogui.json` project.

Live mode executes application code. Failures remain contained in the simulator process and are shown below the preview; switch to **Compare** to keep the safe designer-rendered fallback visible beside the failed or partial runtime view.

## Supported source patterns

The scanner recognizes common Picoware and embedded-renderer primitives:

- Filled and outlined rectangles
- Filled and outlined circles
- Lines and individual pixels
- Picoware helpers such as `_fill`, `_rect`, `_line`, and `_circle`
- Functions that call other discovered graphics helpers
- Simple assignments, conditions, loops, arithmetic, and parameter variants

The profile works with direct calls such as `self.draw._fill_rectangle(...)` and wrapper-based renderers. Imported project modules are parsed only for simple constants.

## Source safety

- Project Python is parsed but never imported or executed.
- Unsupported expressions remain unresolved and are listed in Scanner notes.
- Pixel edits are written as compact horizontal RGB565 runs.
- Generated runs are isolated inside marked blocks.
- Only editor-managed marked blocks are fully regenerated.
- Existing renderer logic remains in place below the overlay.
- The complete diff is shown before writing.
- Every applied edit creates a timestamped backup in the operating system's application-data folder.
- The resulting Python is parsed before it replaces the source file.
- Existing-app edits replace only exact imported source anchors.
- Dynamic existing-app code remains locked and unchanged.
- GUI projects remain separate from generated Python, allowing safe regeneration.
- New Python destinations are written atomically after review.

## Current limits

Arbitrary Python cannot always be reconstructed as editable graphics. Runtime game objects, native image decoders, text glyphs, and complex renderer state may produce partial previews. The editor exposes those functions but only writes the explicit pixel overlay that the user reviews.

Transparent erasing cannot remove an existing procedural draw operation. Set the eraser to the intended local background color instead. Large pixel changes can also generate substantial Python source, so the diff reports the number of compressed runs before applying.

Existing arbitrary application Python cannot always be reconstructed into draggable GUI elements. The existing-app importer edits only static supported draw calls and simple inferred state transitions; everything else remains a locked visual placeholder. The GUI workspace keeps its JSON project as the complete editable design source.
