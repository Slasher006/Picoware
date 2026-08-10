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
4. Draw with the pencil, eraser, fill, line, rectangle, or picker tool.
5. Review the actual-size preview.
6. Select **Review and apply to Python**.
7. Inspect the exact source diff before applying it.

The eraser paints with its configured RGB565 background color. Right-click the canvas to pick an existing color.
Use the mouse wheel over the canvas to zoom in or out. The toolbar zoom value follows the wheel automatically.

## Reference images

The **Reference image** panel supports PNG, JPEG, WebP, BMP, and GIF files. A reference can be placed behind or above the active pixels with adjustable opacity, fit mode, scale, offset, rotation, and horizontal or vertical flipping.

**Convert to editable pixels** performs deterministic local conversion with:

- Configurable palette size
- RGB565 output colors
- Optional Floyd-Steinberg dithering
- Transparent-image support
- Undoable application to the pixel canvas

No AI conversion is used.

**Create new Python graphic** creates a new module-level drawing function from the positioned reference or imported animation frames. The resulting RGB565 runs are reviewed before insertion and can be scanned back into the pixel editor for further work.

## Animation frames

Functions with inferred `frame`, `phase`, or `animation_time` variants get a dedicated **Animation frames** panel. Use **Previous** and **Next** or the frame selector to edit each state, **Play** to preview the sequence, and **Show previous frame** for an onion-skin drawing guide.

Animated GIF and WebP files can be imported directly. Static sprite sheets can be divided by frame width, height, outer margin, and spacing. Frames can be added, duplicated, removed when imported, reordered for playback, and previewed at an adjustable interval.

Apply an edited frame to Python before selecting another frame. The generated overlay is limited to the selected frame and the exact source diff is still shown before writing.

## App GUI workspace

GUI projects are stored as editable `*.picogui.json` files independently from generated Python. Built-in device profiles include PicoCalc, Cardputer, Flipper Zero, and round displays. The **Custom** profile accepts arbitrary width and height values.

Screens support these visual elements:

- Buttons and labels
- Panels and rectangles
- Icons with optional Python asset-function bindings
- Lists
- Progress indicators

Drag buttons, labels, panels, rectangles, icons, lists, and progress indicators from the element palette directly onto the canvas. Palette buttons can still be clicked for quick insertion. Elements can then be selected, dragged, resized, renamed, hidden, recolored, and positioned numerically. Each screen has its own background color and can use a temporary full-screen reference image. Screens can be added, duplicated, deleted, and opened directly from the flow graph.

Use **GUI Project** in the menu bar to create, open, save, or export a project. Python export generates one marked renderer class containing screen draw methods and navigation handling. Re-export replaces only that marked block.

### Editing existing applications

Choose **GUI Project > Import Existing App...** and select one Python file or an application folder. The importer safely parses the source, discovers drawing functions and state-based screens, and infers simple navigation relations across files without importing or running the application.

Direct rectangle and text calls with static arguments become draggable source-backed elements. Runtime-dependent loops, branches, helpers, positions, and other expressions remain visible as orange `[code]` elements. These locked elements cannot be moved or rewritten, so application logic is preserved.

Editable inferred relations can change a simple event value or target state. Relations based on complex runtime conditions are marked `[locked]`. New screens, elements, and relations may still be added to the designer project, but only items with an imported source anchor are written back to the existing application.

Use **GUI Project > Apply Edits to Existing App...** to review a combined multi-file diff. The editor refuses to apply if any source file changed since import, parses every proposed result, rechecks the files after the review dialog, and creates timestamped backups before writing. Save the companion `*.picogui.json` project to retain layout and import metadata between sessions.

## Screen Flow workspace

Every application screen is represented as a draggable node. Directed relations define:

- Source and target screens
- Event trigger
- Optional named condition
- Optional named action
- `replace`, `push`, `modal`, or `back` transition behavior

One screen is marked as the start screen. Double-click a graph node to open it in the GUI designer. The navigation simulator accepts event names, follows matching relations, reports conditions and actions, and renders the resulting screen without running Pico hardware code.

Each graph node has a green input port and blue output port. Drag from the blue port of one node to the green port or body of another node to create a relation with the values currently shown in the relation form. Nodes remain independently draggable, and mouse-wheel graph zoom continues to work.

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
