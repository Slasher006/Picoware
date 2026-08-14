# Picoware Generated App Structure v1: Generation Blueprint

## Purpose

This blueprint turns the architectural rules in [GENERATED_APP_STRUCTURE.md](./GENERATED_APP_STRUCTURE.md) into an exact generation contract. It defines what the editor is expected to produce before implementation work begins.

The phased coding and validation sequence is defined in [GENERATED_APP_IMPLEMENTATION_PLAN.md](./GENERATED_APP_IMPLEMENTATION_PLAN.md).

The contract is intentionally narrow. It describes a runnable application base with generated presentation and assets, but it does not generate application-specific functionality.

## Output set

For a project displayed as `My App`, with sanitized package name `my_app`, the v1 exporter produces:

```text
My App.py
my_app/
├── __init__.py
├── app.py
├── generated_ui.py
├── generated_assets.py
└── generated_assets.pga
```

The editor separately saves the design source as `My App.picogui.json`. That project file is not imported at runtime and is not part of the device deployment set.

## File ownership contract

| Output | Initial action | Later exports | May contain user behavior |
| --- | --- | --- | --- |
| `My App.py` | Create | Preserve unless replacement is explicitly approved | Only integration edits |
| `my_app/__init__.py` | Create | Preserve | Only deliberate package initialization |
| `my_app/app.py` | Create | Never overwrite automatically | Yes |
| `my_app/generated_ui.py` | Create | Replace after validation and review | No |
| `my_app/generated_assets.py` | Create | Replace after validation and review | No |
| `my_app/generated_assets.pga` | Create | Replace after validation and review | No |

An existing unrecognized file is never adopted or overwritten automatically. The editor must stop and offer another destination, an explicit reviewed replacement, or cancellation.

## Naming rules

### Display name

The project display name remains exactly as entered by the user except for leading and trailing whitespace removal. It may contain spaces and mixed case.

### Entrypoint filename

The entrypoint uses the display name with filesystem-invalid characters removed or replaced. The `.py` suffix is added once.

### Package and Python identifiers

Package, class, and helper identifiers use deterministic sanitization:

1. Trim whitespace.
2. Replace every non-alphanumeric run with one underscore.
3. Convert package and module names to lowercase.
4. Remove leading and trailing underscores.
5. Prefix an underscore when the result begins with a digit.
6. Use a documented fallback when the result is empty.

The recommended fallbacks are `generated_app` for packages and `GeneratedUI` for the UI class.

### Stable IDs

Project, screen, element, event, and asset relationships use IDs stored in the `.picogui.json` project. Display names and Python names are not relationship keys.

A v1 stable ID:

- Is created once.
- Uses lowercase ASCII letters, digits, and underscores.
- Is unique within its entity type and project.
- Does not change when its display name changes.
- Is safe as a dictionary string key.

The editor may use readable prefixes such as `project_`, `screen_`, `element_`, and `asset_`, followed by a stable random or hash-derived suffix.

## Generated headers

Every editor-owned module begins with these machine-readable comment fields:

```text
# @picoware-generated structure=1
# @picoware-generated role=<ui-or-assets>
# @picoware-generated project=<stable-project-id>
# @picoware-generator version=<editor-version>
# This file is editor-owned. Regenerate it instead of editing it manually.
```

The first four lines are parsed as exact key/value metadata. Field order is fixed. Unknown future fields may be ignored, but an unknown structure version must not be rewritten without an explicit migration.

Create-once files use a different notice:

```text
# Picoware generated application scaffold.
# This file is developer-owned after its first creation.
```

They do not carry the editor-owned marker.

## `generated_assets.py` contract

### Public surface

The generated asset module exposes only these stable v1 operations to generated UI code:

- `has_asset(asset_id)` returns whether an ID is present.
- `asset_size(asset_id)` returns `(width, height)` or `None`.
- `frame_count(asset_id)` returns the number of frames or zero.
- `draw_asset(draw, asset_id, x, y, frame=0, scale=1)` draws an asset and returns success as a boolean.

Generated UI must not inspect resource records directly. This keeps the binary layout private to the asset-format version.

### Record layout

`generated_assets.py` contains the shared reader only. It contains no per-asset Python
dictionary or tuple table. Metadata for canonical linked assets and detached snapshots
lives in `generated_assets.pga`, so catalogue size consumes storage but does not become
MicroPython parser or import-heap cost.

### `generated_assets.pga` layout

The current binary resource is PGA3: a typed, hash-indexed container for lossless
RGB565 images and complete PCM WAV files. Its constant-size generated Python reader
binary-searches the fixed index, verifies the complete stable ID, and streams only the
selected image row or WAV chunk. PGA2 image files remain desktop-decodable for library
recovery, while PGA1 is recognized only for ownership-safe replacement.

The complete normative byte layout, WAV-only contract, validation rules, compatibility
table, runtime functions, and filename-based playback example are documented in
[PGA3_FORMAT.md](PGA3_FORMAT.md).

Every frame contains exactly `height` rows. Every row contains:

1. `ceil(width / 8)` opacity bytes, most-significant bit first.
2. `width * 2` little-endian RGB565 pixel bytes.

An unset opacity bit means transparent, regardless of the corresponding RGB bytes. A
set bit with RGB565 `0x0000` means visible black. This avoids a color-key ambiguity and
preserves imported alpha losslessly after RGB565 conversion.

### Frames

- A static asset has exactly one frame.
- An animated asset has two or more ordered frames.
- All frames in an asset use the same width, height, and origin.
- Every frame has an independent validated resource span.
- An out-of-range runtime frame request resolves to frame zero.
- Frame deduplication may be added only in a later asset-format version.

### Durations

- An empty duration tuple means timing is controlled entirely by the application.
- A non-empty duration tuple contains one positive integer millisecond value per frame.
- Duration metadata never starts a timer automatically.

### Rendering

The v1 renderer:

- Rejects unknown asset IDs without raising an ordinary drawing-time exception.
- Converts frame and scale to integers defensively.
- Resolves invalid frames to frame zero.
- Clamps scale to a minimum of one.
- Seeks directly to the selected frame offset.
- Reads only one opacity mask and one RGB565 row at a time.
- Blits contiguous opaque spans at natural scale.
- Uses same-color rectangle runs for explicit positive integer scaling.
- Returns `True` after a known asset is rendered, including a fully transparent asset.

The renderer does not allocate a full image buffer per draw and does not import desktop packages.

Generation includes only assets referenced by project screen elements. Unused project
assets and items that remain only in the personal Asset Library are not deployed. The
export review reports referenced asset count, frame count, `.pga` byte size, and the
largest streamed mask-plus-pixel row.

### Fingerprints

The editor project stores a canonical asset fingerprint calculated from:

- Asset-format version.
- Width, height, and origin.
- Ordered palette values, including the transparent slot.
- Ordered frame rectangles.
- Ordered duration metadata.

The recommended algorithm is SHA-256 over a deterministic desktop-side serialization. Runtime modules do not need to calculate the fingerprint. It is used by the editor to classify links as current, modified, or missing.

### Detached snapshots

A detached or draft GUI element receives a stable snapshot ID derived from its element ID. Its metadata is emitted into `generated_assets.py` and its pixels into `generated_assets.pga` alongside canonical assets.

The UI still calls `draw_asset`; it does not inline snapshot pixels. The `.picogui.json` project records whether the source is linked, detached, or draft.

## `generated_ui.py` contract

### Public surface

The generated UI class exposes:

- `render()` draws the active screen and its focus indicator.
- `screen_id` stores the active stable screen ID.
- `set_screen(screen_id)` changes to a valid screen and resets focus.
- `focused_event()` returns the active element event or `None`.
- `move_focus(step)` changes focus and returns the newly focused event or `None`.
- `activate_focused()` applies declared structural navigation and returns the event ID or `None`.
- `handle_navigation(event_id)` applies a declared screen-flow connection and returns whether a transition occurred.

The generated UI does not call user application services directly.

### Screen generation

Each visible screen becomes one private drawing method. The method:

1. Draws the screen background.
2. Draws visible elements in project order.
3. Requests linked and snapshot assets through `draw_asset`.

Asset pixels and resource records never appear in a screen method.

### Focus generation

- Focusable elements are ordered by configured focus order, with project order as the stable tie-breaker.
- Focus state is stored as an index within the active screen's focusable event tuple.
- Moving focus wraps within the active screen.
- A screen without focusable elements returns `None` safely.
- Focus indicators remain generated presentation, not user behavior.

### Navigation generation

Declared screen-flow connections are structural and may be generated. A connection matches stable source screen and event IDs and identifies a stable target screen ID.

On a successful transition, generated UI:

- Changes `screen_id`.
- Resets or assigns the configured target focus index.
- Records the declared transition style for future render integrations.
- Returns success.

Conditions and actions that require application knowledge are not silently treated as real functionality. The generated UI reports their stable identifiers to the user-owned layer or leaves them unhandled until the developer implements them.

### Event generation

- Every focusable or activatable element has a stable event ID.
- Display text is never used as the event key.
- `activate_focused()` always returns the event ID even when generated navigation also handles it.
- Unknown events are safe and return an unhandled result.

## `app.py` create-once scaffold

The initial user-owned scaffold provides:

- An application object holding the view manager and generated UI.
- Lifecycle methods for start, run, redraw, and stop.
- Standard directional focus handling.
- Center-button activation.
- Back-button structural navigation or application exit.
- One documented `handle_event(event_id)` extension point.

The initial event handler returns an unhandled result. It does not invent storage, network, media, game, or business behavior.

The scaffold may be edited freely after creation. Later exports must not append newly discovered event stubs or otherwise rewrite it. New event IDs remain visible through the project and generated UI documentation.

## Entrypoint create-once scaffold

The top-level entrypoint contains only lifecycle delegation to the package application. It does not import generated assets or generated UI directly.

The entrypoint maintains the minimum state necessary to delegate `start`, `run`, and `stop`. Application-specific imports and behavior belong in `app.py`.

## Deterministic output rules

An unchanged project must produce byte-for-byte identical editor-owned modules.

- Stable IDs determine asset and screen ordering.
- Project order is used only where visual stacking or focus tie-breaking requires it.
- Numeric colors use uppercase four-digit RGB565 hexadecimal notation.
- Strings use the generator's one deterministic quoting policy.
- Newlines are LF and every generated file ends with one newline.
- No timestamps, absolute desktop paths, random values, or machine-specific data appear in generated runtime files.

## Destination and regeneration flow

### First generation

1. Resolve and display all output paths.
2. Check for collisions before building any patch.
3. Build all eight runtime artifacts in memory.
4. Parse every Python file.
5. Validate cross-file asset, screen, and event relationships.
6. Present the complete multi-file diff.
7. Write all accepted files atomically as one logical operation.

### Later generation

1. Load the project and resolve the same output set.
2. Verify the editor-owned headers and supported structure version.
3. Compare on-disk content with the last known source fingerprints.
4. Stop on unreviewed external changes.
5. Preserve the entrypoint, `__init__.py`, and `app.py` byte-for-byte.
6. Build and validate both editor-owned modules.
7. Present their exact diffs.
8. Back up replaced files and write atomically.

Failure while writing one file must not leave a mixed generation. The writer must restore the previous complete generated set or retain recoverable backups and report exactly which paths require attention.

## Legacy export migration

The current single-file `generated_gui.py` export is treated as legacy output. V1 must not silently reinterpret or split it.

The initial implementation should offer an explicit **Export Generated App Structure v1** path. Migration from a legacy export requires:

- A selected `.picogui.json` project as the authoritative design source.
- A new or explicitly approved destination.
- A preview of all new files.
- No automatic deletion of the legacy file.

Once v1 is proven, the editor may make it the default new-project export while retaining a clearly labeled legacy single-file export for compatibility during a transition period.

## Validation matrix

The generator must eventually prove these cases:

| Case | Required result |
| --- | --- |
| Empty base project | Runnable scaffold with one valid screen |
| Static transparent asset | Black remains visible and transparent pixels remain absent |
| Animated asset | Correct frame order, invalid frame falls back to zero |
| Linked asset used twice | One canonical resource span and two renderer calls |
| Detached asset | One snapshot resource span and no source link requirement |
| Dense 300×320 import | Small Python manifest, bounded row reads, no parser memory failure |
| Integer scale | Exact nearest-neighbour scaling |
| Arbitrary requested size | Explicit bake required before export |
| Project/asset rename | Stable links remain valid |
| External edit to generated file | Regeneration stops for review |
| External edit to `app.py` | File is preserved byte-for-byte |
| Unchanged regeneration | Empty diff |
| Unknown event | Safe unhandled result |
| Missing asset ID | Safe failed draw and validation error before export |

## V1 acceptance criteria

- The output matches the eight-artifact ownership model.
- The application base starts, renders, handles focus, tolerates unhandled events, and stops.
- No generated screen contains expanded asset rectangles.
- Linked assets appear once in the manifest and binary resource regardless of placement count.
- Static and animated transparent assets reconstruct exactly.
- Generated runtime files contain no desktop dependencies or absolute development paths.
- User-owned files are never changed during regeneration.
- Editor-owned output is deterministic, versioned, reviewed, backed up, and atomic.
- The golden example in [GOLDEN_GENERATED_APP_EXAMPLE.md](./GOLDEN_GENERATED_APP_EXAMPLE.md) remains the human-readable reference for the contract.
