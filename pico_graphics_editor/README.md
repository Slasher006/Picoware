# Pico Graphics and GUI Designer

Pico Graphics and GUI Designer is a standalone Qt6 tool with five workspaces:

- **App GUI** creates complete application screens with draggable visual elements.
- **Screen Flow** connects screens and typed behavior contracts, validates structure, previews navigation, and debugs executable behavior with offline services.
- **Simulator** runs the current in-memory project in an isolated MicroPython process with framebuffer input, logs, comparison, and capture tools.
- **Pixel Art** discovers graphics embedded in Python renderer code and makes them mouse-editable.
- **Asset Library** manages reusable static and animated pixel assets independently from opened source folders and GUI projects.

Python source is scanned without importing or executing the project. Every generated source change is presented as an exact diff before writing.

The editor starts in **App GUI**, the primary application-building workspace. Opening a
`*.picogui.json` project stays there; opening a Python file or source folder switches
directly to **Pixel Art**.

Version 0.10.0 keeps the active document name, full-path tooltip, and saved or modified state visible above every workspace. `Ctrl+S` saves only the active workspace: a Pixel Art asset in Pixel Art, or the shared GUI project in App GUI, Screen Flow, and Simulator. It never applies a dirty document hidden on another tab. `Ctrl+Shift+S` exports PNG in Pixel Art and opens Save GUI Project As in a GUI-project workspace.

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

## Main menus and shortcuts

The menu bar follows the active workspace. **File** owns New, contextual Open,
Open Recent, Close Active Document, contextual Save and Save As, Import, Export, and
Quit. `Ctrl+O` opens Python graphics from Pixel Art, a GUI project from App GUI,
Screen Flow, or Simulator, and imports an image from Asset Library. `Ctrl+W` closes
only the visible Pixel Art document/source or GUI project after the normal dirty-state
confirmation. It never closes a hidden source scope.

**View > Workspaces** switches directly with `Ctrl+1` through `Ctrl+5`: **App GUI**,
**Screen Flow**, **Simulator**, **Pixel Art**, and **Asset Library**, in that order. The
next menu is contextual: **Pixel Art**, **App GUI**, **Screen Flow**, or **Asset Library**. These
menus expose the same primary operations as the visible buttons and right-click menus,
including animation frames, tracing references, layer ordering, flow auto-layout, and
complete Library management. **Project** contains recovery, existing-app source sync,
generated output, and **Validate / Preflight Project**. **Simulator** stays globally
visible. **Help** opens the bundled workflows, the step-by-step MQTT Client tutorial,
format documents, shortcut reference, and version information.

The recent list stores at most eight successfully opened Python files, source folders,
or GUI projects. Clearing it removes only menu history; it never deletes a source or
project file.

## Workflow

1. Open a Python file or project folder.
2. Select a detected graphic from the thumbnail catalogue.
3. Choose any inferred parameter variants.
4. Draw with the select, pencil, eraser, fill, line, rectangle, or picker tool.
5. Review the actual-size preview.
6. Select **Save Python Asset** and review the exact source diff.
7. Inspect the exact source diff before applying it.

The active Python source scope is shown beside **Detected graphics**. Use its visible
**Close** button or **File > Close Pixel Art Document** to leave an opened folder or
file. Closing only clears the scanned Pixel Art catalogue; it never deletes files and
does not close or reset the current GUI project or Personal Asset Library.

The inspector labels every selected graphic as either a **managed asset** or a **source-backed asset**. Assets created by this editor are managed: pixels can be transparent and the complete generated block can be resized or rewritten. Handwritten Python remains source-backed: edits are emitted as conservative overlays, so destructive transparency, pasting transparent pixels, and document resizing stay disabled.

Use **Select** or press `S` to drag a rectangular selection. A selection can be moved with the mouse, cut, copied, pasted, deleted, flipped, rotated, or cropped. **Resize Canvas** preserves pixel size and optionally centers the existing art; **Scale Artwork** uses nearest-neighbor scaling. All pixel-changing operations participate in undo and redo. For source-backed graphics, selection and copy remain available without rewriting arbitrary drawing logic.

The eraser clears to transparency for managed assets and paints with the configured RGB565 background color for source-backed assets. Use the **Picker** tool to sample an existing canvas color. Right-click the canvas or detected-asset catalogue for a menu containing the most-used save, selection, clipboard, export, GUI-placement, and personal-library actions. Use the mouse wheel over the canvas to zoom in or out, and hold the middle mouse button while dragging to pan a zoomed canvas. **Fit**, **1:1**, and **Center** stay beside the toolbar zoom control. Selection copy writes both a lossless Pico pixel payload and PNG to the system clipboard, so pixels can be pasted between assets and separate editor windows.

Interactive editor controls provide short tooltips with a concrete `Example:` line. This includes the Pixel Art controls, App GUI properties and lists, Screen Flow relation and simulator controls, and dynamically created variant selectors. The help describes the current control without requiring a separate manual for ordinary operations.

Source-backed assets offer **Composite**, **Original**, and **Edits** views. Original and Edits are read-only comparisons; return to Composite to paint. Transparent overlay pixels still cannot erase procedural source drawing operations.

Leaving a changed asset opens a **Save**, **Discard**, or **Cancel** prompt. Dirty pixel work is also atomically autosaved after a short delay and can be reopened from **Pixel Art > Recover Autosaved Pixel Asset...**. A successful save or an explicit discard removes that recovery draft.

## New and imported pixel assets

The idle Pixel Art workspace presents three explicit starting points: **New Blank
Asset**, **Import Image as Asset**, and **Open Python Graphics**. Blank assets open as
unsaved transparent canvases immediately; no filename or Python write is required before
drawing. Imported PNG, JPEG, WebP, BMP, GIF, and animated WebP files are converted
locally to actual editable RGB565 pixels immediately. The canvas therefore shows the
content that Library, App GUI, Python generation, and PNG export will receive.

`Ctrl+S` stores a new in-memory asset in the Personal Asset Library. Once stored, it
updates that stable library record. **Generate Python** remains a separate reviewed
destination and keeps exact diff, validation, backup, and atomic-write safeguards.
`Ctrl+Shift+S` exports the visible frame as PNG for source, draft, project, and library
assets.

## Tracing reference images

Expand **Tracing reference…** only after opening an asset. A reference is visibly
separate from editable pixels and can be placed behind or above them with adjustable
opacity, fit mode, scale, offset, rotation, and horizontal or vertical flipping. It is
never saved until **Convert to editable pixels** is explicitly chosen.

**Convert to editable pixels** performs deterministic local conversion with:

- Configurable palette size
- RGB565 output colors
- Optional Floyd-Steinberg dithering
- Transparent-image support
- Undoable application to the pixel canvas

No AI conversion is used.

**New Asset** in the Pixel Art toolbar creates the in-memory editable master first. It
can start blank, from the current canvas or tracing reference, or from an animation file,
sprite sheet, or already imported frames. **Generate Python** later creates a marked
module-level drawing function and automatically reopens that function as a source-linked
asset after the reviewed write.

## Animation frames

Functions with inferred `frame`, `phase`, or `animation_time` variants get a horizontal thumbnail timeline. Select a thumbnail or use **Previous** and **Next** to edit each state, drag thumbnails to reorder managed save output, use **Play** to preview the sequence, and enable **Show previous frame** for an onion-skin drawing guide. A dot marks a drafted frame and a lock marks a protected handwritten-source frame.

Animated GIF and WebP files can be imported directly. Static sprite sheets can be divided by frame width, height, outer margin, and spacing. Frames can be added, duplicated, reordered, and previewed at an adjustable interval. Managed animation frames can also be deleted, and their complete saved order is written back together. Frames inferred from handwritten source remain protected; only imported additions can be removed there.

Save or discard an edited frame before selecting another frame. A managed animation save preserves every frame, while a source-backed edit generates an overlay limited to the selected frame. The exact source diff is always shown before writing.

## App GUI workspace

GUI projects are stored as editable `*.picogui.json` files independently from generated Python. Built-in device profiles include PicoCalc, Cardputer, Flipper Zero, and round displays. The **Custom** profile accepts arbitrary width and height values.

Choose **File > New > New from Picoware Starter...** (also available from **App GUI**)
to compare compact workflow shells visually before creating a project. The dialog previews
every included screen, summarizes its navigation links, separates what the starter includes
from the application logic you must implement, and lets you choose the project name and
target device. The ten
built-in starting points are **Quick Note**, **Field Checklist**, **Focus Timer**,
**Device Settings**, **Item Browser**, **Pocket Converter**, **Command Search**,
**Sensor Monitor**, **Confirm Action**, and **Quick Control**.

### Bundled MQTT Client example

Choose **File > Open > Open MQTT Client Example** to load a safe, unsaved copy of a
complete five-screen example. It combines a custom dashboard, native Toggle, Keyboard,
Menu, TextBox, and Alert widgets, editable Screen Flow relations, typed behavior
contracts, and a Pixel Art MQTT icon. Saving the copy cannot overwrite the bundled
reference.

Open **Help > MQTT Client Tutorial** for a beginner walkthrough covering layout,
system-widget ownership, focus order, pixel assets, Screen Flow, validation, reviewed
generation, developer-owned behavior, and deterministic simulator testing. The complete
runtime and simulator scripts are stored in
`pico_graphics_editor/examples/mqtt_client/`. The editor's in-memory design runner
tests generated presentation and navigation; the tutorial separately runs the bundled
developer MQTT logic through the repository simulator.

Each starter creates a normal unsaved format-8 GUI project with one or two screens.
Screen-owning Picoware widgets remain exclusive, while inline Toggle and Choice controls
can share a custom-layout screen. Two-screen starters include forward and Back
relations, so they behave as useful workflow shells rather than disconnected widget
demos. They supply layout, initial values, navigation, and native input delegation—not
a finished application. Business logic such as storage, countdowns, conversions,
sensors, GPIO, networking, and command handlers remains developer-owned and is named
explicitly in the starter description.

Screens support these visual elements:

- Buttons and labels
- Panels and rectangles
- Icons with optional Python asset-function bindings
- Lists
- Progress indicators
- Native Picoware Menu, List, TextBox, Toggle, ToggleList, Choice, Keyboard,
  SearchBar, Loading, and Alert widgets

App GUI presents a short beginner path above the canvas: add elements, adjust Content and Layout, preview the layout, and then connect interactions in Screen Flow. An empty screen offers direct choices for a screen widget, a custom layout, or a workflow starter. The native-widget chooser labels each selection as a **Screen widget** or **Inline control**. Menu, List, TextBox, ToggleList, Keyboard, SearchBar, Loading, and Alert own an otherwise empty screen; Toggle and Choice can be combined with drawn elements and other inline controls. Generation preflight enforces that distinction instead of applying one blanket limit to every native widget. The canvas renders an editable desktop approximation and marks screen-widget previews clearly, while exported and live-preview code instantiates the real Picoware classes.

The canvas starts in responsive **Fit** mode instead of a fixed zoom, and manual zoom automatically leaves Fit mode. Optional alignment tools and the **Assets** browser stay collapsed until needed, preserving room for the canvas on smaller desktop windows. Drag buttons, labels, panels, rectangles, icons, lists, and progress indicators from the element palette directly onto the canvas, or click a palette button for automatic non-overlapping placement. The Assets browser includes the immediately searchable Built-in and Personal libraries, with theme and asset-type filters for the larger catalogue. The separate **Python** tab contains the advanced source-linked catalogue and its link-state filter only when Python graphics have been opened. Placed RGB565 pixels are embedded in the GUI project so previews, saved projects, Python exports, and the live simulator keep the same appearance without importing the original drawing function. Elements can then be selected, dragged, resized, renamed, hidden, recolored, and positioned numerically. Mouse-wheel zoom, an optional grid, configurable snapping, alignment, and even-spacing controls make precise layouts faster.

The right inspector separates the ordinary **Content & appearance** and **Layout** sections from collapsed **Interaction & focus** and context-only **Advanced asset link** controls. Native controls show only capabilities their Picoware class supports: for example, Choice exposes its selected option, Toggle exposes its initial state, and ToggleList exposes per-item initial states. Screen widgets hide meaningless geometry and generic focus-style controls. An interactive element exposes **Add interaction in Screen Flow...**, which opens the relation editor with that element already selected as the source. Asset packaging is likewise kept inside collapsed **Advanced project output** instead of occupying the normal project header.

Use **Place on Current Screen** to insert any active Pixel Art document directly as a
project copy. **Select in App GUI** switches there with a Python source asset selected
and ready to drag. Unsaved and library documents are embedded as independent detached
project assets and never masquerade as source links.

### Personal asset library

Click the persistent **Save to Library** button beside **Save Asset**, or choose **Save Asset to Personal Library...** from the Pixel Art menu or its right-click menu, to retain one reusable asset outside the current source file and GUI project. **Save Asset** creates or updates Python source; **Save to Library** creates the project-independent reusable copy. A selected App GUI asset can also be saved through the visible library-panel button or the canvas and hierarchy right-click menu. The library keeps:

- Natural width, height, and signed origin
- RGB565 pixels with transparency separate from visible black
- One static frame or a complete ordered animation
- Optional frame durations
- A stable library ID and verified content fingerprint

The versioned library is stored in the editor's operating-system application-data directory and written atomically. It does not import or execute the original Python source. An unknown future library version or damaged fingerprint is rejected rather than guessed or rewritten.

The dedicated **Asset Library** workspace is available even when no Python folder or GUI project file is open. It starts with 300 read-only, editable-as-a-copy RGB565 assets: the original 50 starter icons plus 50-item Industrial, Creative, Playful, Feminine, and Masculine systems. Every theme contains 20 icons, 12 button skins, 12 widget skins, and 6 backgrounds sized for PicoCalc 320×320, Cardputer 240×135, Flipper Zero 128×64, and round 240×240 displays. Collection, theme, and type filters keep the catalogue focused without deleting anything. Its searchable catalogue supports persistent compact, medium, large, and list displays; Ctrl-click and Shift-click selection; file-manager-style rubber-band selection; full checkerboard preview; stored-duration animation playback and direct frame selection; selectable identity metadata; direct image/GIF/WebP import; **Import PGA images**; complete static-or-animation editing in Pixel Art, replace, duplicate, PNG export, rename, confirmed deletion, and **Add to current App GUI**. Add, duplicate, export, and delete operate on the complete selection; multiple project copies are arranged in a grid and recorded as one Undo step. Built-in originals can be added, exported, or opened as safe unlinked Pixel Art copies, but never renamed, replaced, or deleted. Image Import and Replace show the source beside the exact converted RGB565 result and expose target dimensions, palette size, dithering, and either preserved source frame timing or an explicit uniform interval before the automatic library write. Visible and final animation frames convert in background workers with progress and cancellation. Pixel Art preserves the complete ordered frame set and durations; **Update Library Asset** atomically replaces the stable record, while **Save Copy to Library** creates a new identity. If that master is deleted or changed elsewhere while open, its canvas is retained as a safe unlinked copy instead of overwriting the newer library. Session Undo and Redo use content revisions and are cleared when Refresh detects an external write, so a stale snapshot cannot erase concurrently added assets. Display names are unique across Built-in and Personal collections without regard to letter case. Imports, copies, saves, and renames that reuse an occupied name receive the next numeric suffix, for example `Home 2` and `Home 3`. PGA2/PGA3 import validates the complete generated resource before one atomic library write, recovers every contained image with its name, lossless RGB565 frames, transparency, origin, and animation durations, and assigns independent library IDs without modifying the resource. PGA3 WAV entries remain in the source resource because the Personal Asset Library currently manages pixel images. Replacing an asset preserves its stable library ID while existing project snapshots remain unchanged.

Library add, batch import, edit, replace, duplicate, rename, and delete operations retain bounded complete snapshots for session Undo/Redo. The store rejects duplicate Personal names and Personal IDs that collide with Built-in records during normal writes, external reloads, and snapshot restoration. New writes also reserve Built-in names; a legacy Personal collision remains readable and receives an explicit `· Built-in` display label without silently rewriting the old file. Storage failures remain visible and disable writes instead of masquerading as an empty library. Searches clear hidden selections, and completed import or duplicate operations select their new record. Double-click opens the complete reusable master in Pixel Art; **Add to current App GUI**, Enter, or the right-click menu creates an independent project copy. Multi-asset PNG export numbers collisions against both the selected frames and existing destination files, stages the complete batch, and leaves no partial output after a failure. Library-scoped shortcuts are `Ctrl+F` for search, Enter to add a copy, `F2` to rename, Delete for confirmed removal, and Space for animation play/pause. Management and technical metadata remain collapsed until requested.

The visible **Library** asset tab in App GUI provides fast placement while designing a screen. Double-click an entry, use **Add selected asset to screen**, save a selected placed asset, or open the full Asset Library manager. Importing creates an independent detached project snapshot, so the new project no longer depends on the source Python file or the personal-library file. Renaming, replacing, or deleting the library entry therefore does not alter copies already placed in projects. Library deletion always asks for confirmation.

Right-click menus expose the common operations for each development workspace:

- **Pixel Art:** undo/redo, selection and clipboard operations, save, library storage, GUI placement, PNG export, new asset, and rescan.
- **App GUI:** add/duplicate/delete screens, add/duplicate/lock/show/hide/delete elements, save placed assets to the personal library, preview layout safely, run the current design, and import/rename/delete library assets.
- **Screen Flow:** add typed behavior nodes, duplicate/group/trace/delete selections, insert an Action into an event edge, align/distribute nodes, open or mark a screen as start, delete a relation, fit or auto-layout the graph, reset the Flow test, run the current design, and update or delete relations.
- **Simulator framebuffer:** run, restart, stop, capture the current frame, and copy the last error.

Persistent hints beside the Pixel Art canvas, App GUI canvas and lists, and Screen Flow graph make these context menus discoverable without requiring tooltip exploration.

GUI project format 8 adds a stable project ID, stable element event IDs, and a project-level asset catalogue. Reusing one linked asset on several screens therefore stores one canonical asset instead of copying the pixels into every placement. Detached and draft placements receive independent snapshot assets. Links visibly report `current`, `modified`, `missing`, `draft`, or `detached`; missing sources never discard their last pixels. Relative paths are written when possible, with an absolute recovery fallback. Refresh is always explicit. **Refresh pixel asset**, **Refresh All Linked**, **Relink**, and **Detach** are available for icons; a size-changing refresh asks whether to keep element geometry or resize it to the asset.

Older projects remain readable. Format-7 linked placements are grouped only when their source identity and fingerprint agree, so two modified snapshots are never silently merged. Legacy detached and draft placements become per-element snapshots. This migration happens only in memory when opening a project; the original file is not rewritten until the normal reviewed save path is used, which also creates the existing project backup.

Shift-click or drag a selection rectangle to edit several elements together. Arrow keys move the selection by one pixel, Shift+Arrow moves it by ten, Ctrl+D duplicates it, and Delete removes design-only elements. The hierarchy acts as a layers panel: its top row is the backmost element and its bottom row is the frontmost. Drag rows or use **Bring to Front**, **Move Forward**, **Move Backward**, and **Send to Back**; the same actions are in the canvas and hierarchy right-click menu and support multi-selection without changing relative order. Lock and visibility remain independent of drawing depth. Screen rows include live thumbnails of their actual contents and can also be reordered by dragging.

Focusable elements have an explicit focus order. Enable **Focus order** above the canvas to see numbered badges. Each element can also be input-enabled or disabled and can define an **Activation event** independently from its display name. Configure its focus indicator as an outline, corner brackets, underline, or hidden state, with an individual RGB565 color, thickness, and spacing. The navigation preview and live simulator use the same focus appearance. The preview supports Tab or arrow-key focus, Enter or Space activation, and direct mouse activation. Generated GUI classes expose `focused_element`, `move_focus`, and `activate_focused` helpers for Pico keyboard integration.

Each screen has its own background color and can use a temporary full-screen reference image. Screens can be added, duplicated, deleted, and opened directly from the flow graph. GUI edits use complete-project undo and redo. Dirty projects are atomically autosaved for manual recovery from **Project > Recover Autosaved GUI Project...**. Every successful explicit save, including the first one, also creates a timestamped independent safety copy in the application-data `backups/gui-projects` directory. Use **Project > Recover Saved GUI Backup...** to open one without overwriting the original project path.

Use **File** to create, open, close, or save a project and **Project** for validation, recovery, source synchronization, and generated output. **Export Generated App Structure v1...** is the recommended base-application export. **Export GUI to Python (Legacy)...** retains the earlier one-file marked renderer for compatibility; it is not silently converted or deleted. The legacy exporter refuses image-heavy projects whose pixels would expand beyond 5,000 Python draw statements and directs them to the streamed `.pga` workflow.

### Generated App Structure v1

For a project named `My App`, the v1 exporter reviews and creates this runtime shape:

```text
My App.py
my_app/
├── __init__.py
├── app.py
├── behavior_handlers.py
├── generated_behavior.py
├── generated_ui.py
├── generated_assets.py
└── generated_assets.pga
```

The files have deliberately different owners:

- `My App.py`, `my_app/__init__.py`, and `my_app/app.py` are created once and then preserved byte-for-byte.
- `behavior_handlers.py` is developer-owned. The exporter may offer a reviewed additive missing-handler stub, but never replaces an existing function body.
- `generated_behavior.py` is the editor-owned bounded App Flow v2 dispatcher, stable binding table, redacted trace surface, and generated test manifest.
- `generated_ui.py` is editor-owned presentation: screens, drawing, focus, and declared structural navigation.
- `generated_assets.py` is the editor-owned constant-size PGA3 index reader and bounded-memory image/WAV streamer; it contains no per-resource Python table.
- `generated_assets.pga` is the editor-owned typed PGA3 resource. It stores indexed RGB565 images and complete PCM WAV files without embedding their payloads in Python.
- `My App.picogui.json` remains the editable design source. It is saved separately and is not imported on the device.

The **Assets** selector in the App GUI project header can instead choose **Individual
files**. That mode replaces the combined resource in new output with a
`generated_assets/` directory containing one readable `<asset-id>.pga` per image and
the unchanged `<asset-id>.wav` per sound. The generated drawing/audio API is identical,
and the selected mode is also used by Live Simulator. Existing output from a previously
selected mode is ignored rather than automatically deleted; this avoids removing files
from an export directory without a separate reviewed cleanup operation. While individual
mode remains selected, obsolete project-owned `.pga` and `.wav` files are listed as
deletions in the export review and are backed up and rolled back with the other changes.

Focusable elements emit their stored stable `event_id`, not their label, display text, or current element name. Renaming a screen or button therefore does not break application behavior. `activate_focused()` returns the event ID even when the generated UI also follows a declared screen transition. Unknown events are safe and remain for developer-owned `handle_event()` to interpret.

Linked placements refer to one canonical asset ID. Detached or unsaved draft placements refer to their own compact snapshot ID. Both are drawn through `draw_asset`; generated screen methods contain no expanded pixel rectangles. Imported PNG, GIF, WebP, and other desktop images are converted to RGB565 plus a one-bit opacity mask during generation. The renderer reads one row at a time, keeping its temporary allocation bounded even for a dense 320×320 image. The runtime supports natural size and positive integer scaling. If an icon has an arbitrary or distorted size, export stops and asks for an explicit nearest-neighbour bake instead of inventing fractional device rendering. Animation frames and durations are stored, but the generated base does not invent a timer.

Right-click a placed image in App GUI and choose **Open Asset in Pixel Editor**.
Source-linked Python graphics open through the existing reviewed source-editing workflow.
Detached, baked, missing-source, and library-derived placements open from the lossless
project asset. **Update Project Asset** returns pixel changes to every placement sharing
that stable asset ID and marks the GUI project dirty; save the GUI project to persist it.

For a selected image element, **Device size** in App GUI now shows whether its current
geometry can run. Use **Use natural asset size** to restore the canonical dimensions or
**Bake current size...** to create an independent nearest-neighbour copy without
changing the linked or library original. The same actions are available from the
right-click element menu. If an older project reaches Simulator with invalid image
geometry, **Bake Invalid Assets and Run** repairs the affected placements explicitly
and continues the launch instead of leaving only an opaque element-ID error.

Generation deploys only assets referenced by project screen elements. Assets that are
unused in the project or remain only in the personal Asset Library are not copied into
the Pico package. A hash-sorted binary index makes lookup scale without compiling a
Python dictionary containing hundreds of strings and tuples. The export review shows
the referenced asset count, frame count, `.pga` storage size, and maximum streamed row
size. WAV reads are capped at 4096 bytes and extraction uses 1024-byte chunks.
Catalogue growth therefore primarily consumes SD/flash storage; drawing still holds
only one opacity mask and one RGB565 row at a time, and WAV access never loads the
complete sound into Python RAM.

`generated_assets.pga` is deployment output, not the editable master. Do not edit it in
place. Reopen the accompanying `.picogui.json` project to edit its lossless project
assets and regenerate the resource. Use **Import PGA images…** in the **Asset Library**
tab or **Asset Library > Import Images from PGA…** to recover independent reusable
image copies. PGA2 and PGA3 contain enough pixel and animation data for this lossless
image import, but they do not
contain screens, placements, source links, library ownership, or the rest of the editor
project and therefore cannot reconstruct or replace the GUI project by itself.
Standalone `PGA1` resources cannot be imported because their required metadata lived in
the companion generated Python file. PGA3 additionally accepts complete uncompressed
PCM WAV files only; see [PGA3_FORMAT.md](PGA3_FORMAT.md) for the binary layout, limits,
bounded-memory API, and extraction/playback example.

The export dialog resolves all eight artifacts and labels every file as create, preserve, regenerate, conflict, or unsupported before anything is written. It parses all proposed Python, displays complete Python diffs plus the binary resource size and SHA-256, rechecks every text and binary fingerprint after review, prepares temporary siblings, backs up replaced generated files, and rolls back the complete set if a replacement fails. An unrecognized collision or future structure version blocks the entire export. Later exports preserve create-once developer files, add only reviewed missing handler stubs, and regenerate editor-owned behavior, UI, and asset output. An unchanged export has no generated diff.

To deploy the result, copy the top-level entrypoint and the complete package directory. Do not copy the desktop-only `.picogui.json` unless you want a separate design backup. This workflow makes no physical-device performance claim; use the normal Picoware device validation process for hardware proof.

The detailed quasi-standard and exact contracts are documented in:

- [Generated App Structure v1](./GENERATED_APP_STRUCTURE.md)
- [App Flow Standard v1](./APP_FLOW_STANDARD_V1.md)
- [App Flow Standard v2](./APP_FLOW_STANDARD_V2.md)
- [Generation Blueprint v1](./GENERATION_BLUEPRINT_V1.md)
- [Golden Generated App Example](./GOLDEN_GENERATED_APP_EXAMPLE.md)
- [Implementation and validation plan](./GENERATED_APP_IMPLEMENTATION_PLAN.md)

### Editing existing applications

Choose **Project > Import Existing App...** and select one Python file or an application folder. The importer safely parses the source, discovers drawing functions and state-based screens, and infers simple navigation relations across files without importing or running the application.

Direct rectangle and text calls with static arguments become draggable source-backed elements. Runtime-dependent loops, branches, helpers, positions, and other expressions remain visible as orange `[code]` elements. These locked elements cannot be moved or rewritten, so application logic is preserved.

Editable inferred relations can change a simple event value or target state. Relations based on complex runtime conditions are marked `[locked]`. New screens, elements, and relations may still be added to the designer project, but only items with an imported source anchor are written back to the existing application.

Use **Project > Apply Edits to Existing App...** to review a combined multi-file diff. The editor refuses to apply if any source file changed since import, parses every proposed result, rechecks the files after the review dialog, and creates timestamped backups before writing. Save the companion `*.picogui.json` project to retain layout and import metadata between sessions.

## Screen Flow workspace

Screen Flow has two compatible layers: the original screen/element navigation graph and the typed **App Flow Standard** behavior graph. Use **Visibility** to show both layers, **Screens only**, or **Behavior only**. The searchable operation chooser and the graph's **Add operation** menu expose every built-in operation plus advanced Component and Comment nodes through one consistent catalogue. Behavior nodes have visible typed input/output ports and can be connected by dragging, while the right inspector exposes names, descriptions, operation-specific controls, advanced JSON, stable IDs, port definitions, generated stub names, pin/lock state, and breakpoints.

Every application screen is represented as a draggable node containing a live rendering of that screen. Focusable buttons, icons, lists, and other configured elements appear as rows below their screen with individual green input and blue output ports. Directed relations define:

- Source and target screens or elements
- Event trigger
- `replace`, `push`, `modal`, or `back` transition behavior

Conditions and actions are executable behavior nodes, not navigation-edge text. New relations therefore leave legacy Condition and Action fields empty. If an old project contains them, the editor shows a read-only legacy panel, validation blocks execution/export, and **Clear legacy fields** removes them explicitly. One screen is marked as the start screen. Double-click a graph node to open it in the GUI designer. The **Navigation** test accepts event names, follows matching relations, and highlights the resulting screen without running Pico hardware code.

Drag from an **OUT** screen or element port to an **IN** screen or element port. Labels and arrowheads communicate direction without relying on color alone. An element source automatically uses its configured activation event. Connecting to a destination element opens that element's screen and gives it initial keyboard focus. Manual From/To creation remains under **Advanced: add relation manually**. Screen-only relations from older projects continue to work unchanged. Click a relation line to select and edit it, or press Delete to remove a design-only relation. **Auto-layout graph** arranges screens by navigation depth. Nodes remain independently draggable. Use the mouse wheel to zoom from 5% to 200%, hold the middle mouse button and drag to pan the view, choose **Fit visible** for the active graph layer, or **Zoom selection** to return to a readable editing scale.

Behavior connections are validated before creation: output must connect to input, port data types must match, and single-input contracts reject competing edges. A bound **UI event** exposes the complete Event payload plus typed **Value**, **Text**, **Checked**, and **Index** outputs when the widget provides them. Connect these directly to compatible operations, use **Get payload field**, or configure **Compare** with a Payload field. Exact tokens such as `$value`, `$text`, and `$checked` safely route incoming values into operation properties without expressions. **Create behavior from this element...** includes **Handle widget value** and **Branch by current value** guided chains. Ctrl-click selects multiple nodes; copy/paste and context actions support duplication, grouping, collapse/expand, alignment, distribution, and insertion of an Action into an event edge. Auto-layout can run horizontally or vertically, preserves pinned nodes, and the searchable graph plus clickable minimap keep larger flows navigable.

The assistant banner above the graph continuously reports whether the flow is valid and suggests the next useful action. Nodes with findings receive a visible `!` badge and severity outline. Event, Any, String, Boolean, Integer, and Data ports have distinct colors plus text labels. During a connection drag, compatible inputs enlarge and turn green; rejected targets turn red and the banner explains the exact type or occupancy problem. Dropping a behavior output on empty space opens the compatible-operation palette. The same fast path works from a button or widget row: drop on a screen input to navigate, or drop on empty space to choose and create a bound action.

The **Connect** inspector keeps typed endpoint creation and edge maintenance visible without scrolling through node properties. Selecting a source output filters target nodes and ports to compatible inputs, and **Connect typed ports** remains disabled until the endpoint contract is complete. Behavior-edge conditions from older files appear only in a read-only legacy panel and must be replaced with a Condition node. The live **Issues** inspector reports broken connections, malformed ports, missing Event entry points, unconnected Events, structural-only v2 nodes, unreachable behavior nodes and screens, invalid payload tokens, unsupported legacy logic, missing required inputs or condition branches, duplicate triggers, and unbounded behavior cycles. It shows severity counts, filters, suggested fixes, and **Next issue** / **Go to issue** navigation. Error-level findings block generation and debugger start.

The **Navigation** tab keeps Back/Forward history and a separate structural trace. The **Debugger** tab is an offline deterministic flow debugger: select a node, choose **Start**, then **Step** one node or **Continue** until completion or a breakpoint. Choose whether external services succeed, return an error, or are cancelled; an optional JSON response supplies deterministic result data. **Fire timer** queues retained callbacks at an explicit node boundary. **Stop** discards queued work and **Clear Trace** removes graph highlights. Optional JSON input tests alternate event values; every trace row shows separate redacted input and output payloads, output port, outcome, and duration, and centers its executed node when selected. Editor debug services record UI, state, timer, storage, MQTT, Wi-Fi, and handler calls without network, device, or file writes. The **Preview** tab is labeled as structural-only. **Open Device Simulator** and **Run current design** remain the final executable checks after design and validation are complete.

The **Recipes** inspector saves selected behavior nodes, their internal connections, and visual groups as a fingerprinted personal flow fragment. Inserting a recipe creates independent IDs, so it can be reused in new projects without linking those projects to the library file. This is deliberately distinct from the Asset Library for graphics.

The complete node, edge, diagnostics, trace, reuse, persistence, migration, and generation contracts are documented in [App Flow Standard v1](./APP_FLOW_STANDARD_V1.md).

## Simulator workspace

The dedicated **Simulator** workspace supports **Device simulator**, **Design preview**, and **Compare** views. Open it with its top-level tab, **Run > Open Device Simulator**, or `F6`. App GUI exposes one **Preview Layout** button for safe rendering; executable testing uses the persistent **Run current design** button above the workspaces, the **Run** menu, or `Ctrl+Enter`. Screen Flow retains its contextual run button. Switching workspaces keeps the same controller, process, active screen, and framebuffer, while a `Simulator ● Running` badge remains visible in the document strip and workspace tab.

Device mode launches Picoware's real MicroPython simulator as an isolated child process and embeds its RGB565 framebuffer directly in Qt; it does not open the separate SDL viewer window. **Current design** is the primary launch mode: running builds a temporary app from the in-memory project and opens the currently active screen, including unsaved designer edits, without changing application source. The primary row contains only Run, Restart, and Stop. Target route, board, and reload options are separated under **Advanced launch**; framebuffer association is under **Capture**; raw runtime telemetry is under **Runtime details**.

The temporary app uses the same `generated_assets.py` plus `generated_assets.pga` contract as normal Generated App Structure export. Dense imported images are never expanded into thousands of `_fill_rectangle` statements. The temporary binary payload is isolated with the simulator process and removed with its temporary workspace.

For imported applications, the editor infers an Application or Game route from the imported path. The launch type, target name, and board remain editable under **Advanced launch**, so external or unusual projects can be configured manually. When **Reload when imported source changes** is enabled, saved Python changes restart the simulator automatically.

The live framebuffer receives keyboard focus automatically when the simulator starts. Click it to return focus after using another editor control; a cyan frame shows that Picoware input is active. Arrow keys, Enter, Escape, Tab, function keys, printable text, and touch/mouse input are forwarded through the simulator's normal input protocol. Network access is offline and audio is silent for predictable editor sessions.

Use **Capture current frame** to associate the current real framebuffer with a selected designer screen. The captured frame appears in that screen's list thumbnail and graph node until it is cleared or another GUI project is opened. Captures are intentionally transient and are not written into application source or the `*.picogui.json` project.

Device mode executes application code. Failures remain contained in the simulator process and open an actionable error panel with **Copy error**, **Show details**, and **Restart**. Compare view remains available beside the failed or partial runtime view, and the global simulator badge continues to show the error after switching workspaces.

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
