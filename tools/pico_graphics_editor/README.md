# Pico Graphics Editor

Pico Graphics Editor is a standalone Qt6 pixel-art editor for graphics embedded in Python renderer code. It scans source without importing or executing the project, finds supported drawing functions, renders them into a mouse-editable pixel grid, and prepares narrow Python overlays for review.

## Run

Install the desktop dependency:

```bash
python3 -m pip install -r tools/pico_graphics_editor/requirements.txt
```

Open the editor:

```bash
python3 tools/pico-graphics-editor.py
```

Open a renderer or project immediately:

```bash
python3 tools/pico-graphics-editor.py builds/MicroPython/apps_unfrozen/games/pico_bomber/render.py
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

## Current limits

Arbitrary Python cannot always be reconstructed as editable graphics. Runtime game objects, native image decoders, text glyphs, and complex renderer state may produce partial previews. The editor exposes those functions but only writes the explicit pixel overlay that the user reviews.

Transparent erasing cannot remove an existing procedural draw operation. Set the eraser to the intended local background color instead. Large pixel changes can also generate substantial Python source, so the diff reports the number of compressed runs before applying.
