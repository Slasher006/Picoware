# Picoware Generated App Structure v1

## Status

This document defines a quasi-standard project structure for applications created by the Pico Graphics and GUI Designer. It is an editor convention, not a requirement imposed on handwritten Picoware applications.

The convention gives generated applications a predictable foundation while leaving application behavior under the developer's control. The editor generates structure, presentation, navigation, and assets. It does not invent business logic or silently overwrite user-written behavior.

Companion documents:

- [GENERATION_BLUEPRINT_V1.md](./GENERATION_BLUEPRINT_V1.md) defines the exact generation contract.
- [APP_FLOW_STANDARD_V1.md](./APP_FLOW_STANDARD_V1.md) defines typed, reusable, non-executable behavior contracts.
- [APP_FLOW_STANDARD_V2.md](./APP_FLOW_STANDARD_V2.md) defines bound, allowlisted executable behavior.
- [GOLDEN_GENERATED_APP_EXAMPLE.md](./GOLDEN_GENERATED_APP_EXAMPLE.md) shows the complete expected output for one example project.
- [GENERATED_APP_IMPLEMENTATION_PLAN.md](./GENERATED_APP_IMPLEMENTATION_PLAN.md) provides the phased implementation and validation handoff.

## Goals

- Keep generated presentation code separate from user-written application logic.
- Make regeneration safe and predictable.
- Give every generated application the same Picoware lifecycle shape.
- Store pixel assets once and reference them from screens without duplicating drawing code.
- Preserve RGB565 colors, transparency, animation frames, and crisp pixel scaling.
- Keep the generated application deployable without requiring the desktop editor.
- Allow a developer to understand where new behavior belongs immediately.

## Non-goals

- The editor does not generate a finished application's business rules.
- The convention does not replace Picoware's existing application API.
- Existing handwritten applications do not need to adopt this structure.
- Generated output must not require a new Picoware runtime dependency; its small
  streaming decoder is generated with the application.
- Generated output must not depend on Qt, Pillow, or any desktop-only package.

## Recommended project layout

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

The editor project remains separate:

```text
My App.picogui.json
```

The `.picogui.json` file is the editable design source. It is not required on the device and should not be imported by the application at runtime.

### `My App.py`

The top-level file is the Picoware application entrypoint. It exposes the normal `start(view_manager)`, `run(view_manager)`, and `stop(view_manager)` lifecycle functions and delegates them to the application object in `my_app/app.py`.

The entrypoint must remain thin. It must not contain generated screens, expanded asset pixels, or application-specific behavior.

The editor creates this file when the application is first generated. Subsequent exports preserve it unless the developer explicitly requests a replacement. This makes it safe to add small integration details when a particular application needs them.

### `my_app/app.py`

This is the user-owned behavior layer. It is created once and is not regenerated.

Its responsibilities are:

- Own application state.
- Receive Picoware lifecycle calls from the entrypoint.
- Ask the generated UI to render the current screen.
- Translate Picoware input into focus, navigation, and activation operations.
- Handle stable event names emitted by generated elements.
- Implement application-specific behavior, data access, services, and cleanup.

The initial file may contain documented no-op handlers so that the generated base application can start and display its UI. These placeholders are structural extension points, not invented functionality.

The primary behavior boundary is a single event handler. Generated UI elements emit stable event identifiers such as `settings.open` or `player.pause`; `app.py` decides what those events do.

### `my_app/__init__.py`

This file marks the application directory as an importable MicroPython package. It is created once and preserved by later exports. It should remain empty apart from an optional developer-owned package comment unless the application deliberately needs package initialization.

### `my_app/behavior_handlers.py`

This is the developer-owned escape hatch for operations outside the v2 allowlist.
It is created once. Later exports may offer a reviewed additive function stub when a
new Custom Handler node has no matching function. Existing imports, comments, and
function bodies are never regenerated.

### `my_app/generated_behavior.py`

This is the editor-owned bounded App Flow v2 dispatcher. It contains stable UI-event
bindings, allowlisted operation records, typed connections, redacted runtime tracing,
service injection, and a generated `TEST_MANIFEST`. It does not evaluate expressions
or import code named by graph properties.

### `my_app/generated_ui.py`

This is the editor-owned presentation layer. The editor may regenerate the complete file.

It contains:

- Screen definitions.
- Element placement and appearance.
- Focus order and focus indicators.
- Screen-flow connections.
- Navigation rules.
- Stable event names.
- References to assets by asset ID.
- Flow Standard version, typed behavior-node, connection, and group records.
- A structural behavior-contract accessor. Executable dispatch belongs to
  `generated_behavior.py`.
- Lazy construction and input delegation for native Picoware widgets selected in App GUI.

It does not contain:

- Business logic.
- Network access.
- Storage operations.
- Application-specific state mutation.
- Expanded copies of asset drawing calls.

The generated UI may request an event, but only the user-owned application layer handles it.

`GeneratedUI` receives the Picoware `view_manager`, retaining its drawing context and
input manager. Custom elements still generate direct draw calls. Native elements import
and instantiate the corresponding Picoware `Menu`, `List`, `TextBox`, `Toggle`,
`ToggleList`, `Choice`, shared `Keyboard`, `SearchBar`, `Loading`, or `Alert` class.
The user-owned scaffold calls `handle_input()` before generic focus handling so a native
widget can consume D-pad, keyboard, or touch-backed input and emit its stable event ID.
Only one screen-owning native widget is supported on an otherwise empty screen. Inline
`Toggle` and `Choice` controls can share a custom-layout screen and participate in the
ordinary stable focus order.

An `app.py` created by an editor version before native-widget support remains
developer-owned and is not silently rewritten. Before adding a native widget to such
an existing generated application, update its construction call from
`GeneratedUI(view_manager.draw)` to `GeneratedUI(view_manager)` and delegate the
current button to `handle_input()` before generic focus handling. Newly created apps
and live previews already contain this bridge.

When a project contains App Flow Standard v1 data, `generated_ui.py` serializes that
structure as stable records and `describe_behavior_contract()` reports it without
execution. Version 1 remains structural-only. Version 2 execution is generated only
for explicit allowlisted operations and stable bindings. Error-level flow diagnostics
block generation, while warnings remain visible design guidance.

### `my_app/generated_assets.py`

This is the editor-owned asset reader and renderer. The editor may regenerate the complete file.

It contains no per-asset Python table. Screens use stable asset IDs, and the reader resolves metadata from the companion binary index before seeking to the selected frame. It reads only one mask-and-pixel row at a time.

### `my_app/generated_assets.pga`

This is the editor-owned typed PGA3 resource. It begins with the `PGA3` magic and owning project ID, followed by a fixed hash index, typed metadata records, and contiguous payloads. Image rows store a one-bit opacity mask followed by little-endian RGB565 pixels. WAV records store metadata plus a complete validated PCM RIFF/WAVE byte stream. The file contains no executable code and is deployed with the rest of the package.

Projects may alternatively select **Individual files**. The generated package then
contains `generated_assets/<stable-id>.pga` for each one-image PGA3 resource and
`generated_assets/<stable-id>.wav` for each unchanged WAV. A small ownership marker
protects transactional regeneration but is never parsed by the device. The generated
Python module exposes the same drawing and audio functions in either mode. When the
project stays in individual mode, obsolete marker-owned resources are reviewed as
deletions and participate in the same backup and rollback transaction. Switching modes
does not implicitly remove the previous mode's output.

The `.pga` file is a regenerable deployment artifact, not an authoring document. The
`.picogui.json` project remains the editable source of truth and retains project assets,
screens, placements, links, and editor metadata. **Import Images from PGA** strictly
decodes PGA2/PGA3 graphics into independent Personal Asset Library copies while
preserving names, RGB565 pixels, transparency, origins, frames, and durations. It never
modifies the generated resource. WAV entries remain in PGA3 rather than entering the
pixel-image library. Loading a PGA resource alone cannot reconstruct the project
information that was deliberately not deployed. See [PGA3_FORMAT.md](PGA3_FORMAT.md)
for the complete resource and WAV streaming contract.

## Ownership and regeneration rules

| File | Owner | Regeneration policy |
| --- | --- | --- |
| `My App.py` | Developer after creation | Create once; preserve by default |
| `my_app/__init__.py` | Developer after creation | Create once; preserve by default |
| `my_app/app.py` | Developer | Create once; never overwrite automatically |
| `my_app/generated_ui.py` | Editor | May be regenerated completely |
| `my_app/generated_assets.py` | Editor | May be regenerated completely |
| `my_app/generated_assets.pga` | Editor | May be regenerated completely |
| `My App.picogui.json` | Editor project | Saved independently; not a runtime file |

The editor must never place user-editable behavior inside an editor-owned file and must never require the developer to edit a generated region to complete ordinary application behavior.

Before replacing an editor-owned file, the editor must:

1. Build the complete new content without changing the destination.
2. Validate that the generated Python is syntactically valid.
3. Show the exact diff for review.
4. Create a recoverable backup when replacing an existing file.
5. Write the accepted output atomically.

If an editor-owned file was changed outside the editor after it was loaded, regeneration must stop and ask the user to rescan or explicitly resolve the conflict.

## Generated file identity

Every editor-owned file should begin with machine-readable comments containing:

- Structure name: `Picoware Generated App Structure`.
- Format version: initially `1`.
- Project identifier.
- Generation role: `ui` or `assets`.
- Generator version.

The project identifier distinguishes unrelated applications that happen to use the same package or screen names. The format version allows future editors to migrate old generated projects deliberately instead of guessing their structure.

## Stable identifiers and names

Human-readable names and internal identities serve different purposes.

- Project, screen, element, event, and asset IDs must remain stable across renames.
- Display names may change without breaking links.
- Python module and package names use sanitized `snake_case` names.
- The editor project stores the stable IDs and their current display names.
- Generated files may include readable names for diagnostics, but relationships use stable IDs.

An asset renamed from `status_badge` to `connection_status` therefore remains linked to the same GUI elements. Renaming changes its label, not its identity.

## Asset record contract

Each managed asset record represents one static image or animation and contains:

- Stable asset ID.
- Display name.
- Source width and height.
- Origin or anchor point when it is not the upper-left corner.
- RGB565 palette.
- Explicit transparent palette index.
- Compact drawing data for each frame.
- Frame order and optional frame-duration metadata.
- Asset-format version.

Transparency is separate from color. RGB565 value `0x0000` is black and must remain available as an ordinary visible color; it must never implicitly mean transparent.

### Compact drawing data

Generated assets should be stored as data interpreted by one shared renderer, not as hundreds of repeated drawing statements.

The desktop project may use palette-backed rectangles internally for fingerprints and
lossless editing, but runtime pixels are never serialized as Python tuples or drawing
statements. Generation converts every frame into deterministic rows containing:

- `ceil(width / 8)` bytes of opacity bits, most-significant bit first.
- `width * 2` bytes of little-endian RGB565 pixels.
- No special transparent color; alpha is represented only by the mask.

The resource begins with `PGA3`, a two-byte little-endian project-ID length, the UTF-8
project ID, resource count, payload boundary, total size, and a fixed eight-byte entry
per referenced resource. Entries are sorted by FNV-1a ID hash for binary search; the
complete UTF-8 ID in the pointed-to record is always checked to make collisions
harmless. Typed records contain image geometry/frame spans or PCM WAV metadata/data
spans. The generated Python reader contains no resource catalogue, so dense images,
large WAV files, and hundreds of records do not create a proportional Python object
graph.

### Shared asset renderer

`generated_assets.py` owns one renderer that accepts an asset ID, position, frame, and scale. Individual assets do not generate dedicated drawing instructions.

The renderer is responsible for:

- Looking up the canonical asset record.
- Selecting a valid animation frame.
- Opening and seeking within the companion resource.
- Reading one opacity mask and RGB565 row at a time.
- Blitting contiguous opaque spans at natural scale.
- Drawing same-color runs for positive integer scaling.
- Applying supported pixel scaling.

Using a common renderer also gives later format versions one place to add optimizations without changing every generated screen.

### Bounded-memory rule

The renderer must never allocate a complete imported image. Its largest normal-scale
pixel read is one RGB565 row and its largest alpha read is one one-bit mask row. Fully
opaque rows use the same format, keeping one deterministic code path for opaque and
transparent imports.

Only assets referenced by screen elements are exported. Unused project assets and
personal-library entries remain desktop-side. Resource lookup uses bounded temporary
objects and binary search; the number of catalogue entries affects flash/SD storage and
lookup depth, not the size of the imported Python metadata table. WAV reads are capped
at 4096 bytes and extraction copies 1024 bytes at a time. `PGA1` resources are
recognized only for ownership-safe replacement, PGA2 images remain importable, and
PGA3 is the current generated image-and-WAV format.

## Asset linking and snapshots

Managed assets are linked by default.

A linked GUI element stores:

- Stable asset ID.
- Expected asset fingerprint.
- Placement and scaling information.

It does not store a second expanded copy of the asset pixels. When the asset changes, the editor can report the link as current or modified and refresh the GUI preview without changing the element's identity.

A detached element is an intentional snapshot. Detaching copies the asset's compact records into the GUI project and removes the link to the canonical asset. Draft placement uses the same snapshot concept but is clearly marked as an unsaved draft.

For exported code:

- Linked elements call the canonical generated asset renderer.
- Detached or draft elements use compact snapshot data through the same rendering contract.
- Neither form expands pixels into repeated statements inside a screen method.

## Scaling policy

Linked pixel assets use their natural dimensions by default.

The v1 runtime scaling contract supports positive integer scale factors. Integer scaling preserves crisp pixel edges and keeps rectangle coordinates deterministic.

When a designer requests arbitrary dimensions, the editor should offer an explicit nearest-neighbour bake operation. Baking creates new pixel dimensions before generation instead of making the device perform ambiguous fractional scaling on every frame.

This distinction should remain visible in the editor:

- **Scale** changes the runtime integer multiplier.
- **Resize/Bake** creates a new nearest-neighbour asset size.

The App GUI inspector reports the selected placement's device-size validity and exposes
**Use natural asset size** plus **Bake current size...**. Baking creates a detached
placement-specific asset and never modifies the canonical project or personal-library
source. Current-design Simulator launch performs the same preflight and may offer an
explicit batch bake before generation; cancelling leaves the project unchanged.

## Animation policy

An animated asset remains one asset with ordered frames.

- Every frame uses the same canvas dimensions and anchor contract.
- Frame records are stored in timeline order.
- A frame may reuse data from another frame when a later format supports deduplication.
- The application supplies or advances the active frame; generated UI does not invent animation behavior.
- Optional frame-duration metadata describes presentation timing but does not automatically start a timer.
- Invalid frame values resolve predictably to the first frame unless the application deliberately requests another policy.

The GUI project links to the animated asset, not to a single silently captured frame. An element may specify its initial frame while user-owned application logic controls subsequent frame changes.

## UI element contract

Each generated UI element contains structural information only:

- Stable element ID.
- Element kind.
- Position and dimensions.
- Visibility and enabled state.
- Focusable state, order, and focus style.
- Text and presentation colors where applicable.
- Asset ID and frame/scale settings for asset elements.
- Stable activation event name.

Screen methods describe composition. They clear the screen, draw structural elements, and request assets from the asset renderer. They do not contain copied asset pixel records or application behavior.

## Event contract

Generated elements emit stable string event identifiers. The event identifier should describe intent without implementing it.

Examples include:

- `navigation.back`
- `settings.open`
- `player.pause`
- `item.activate`

The generated layer may handle focus movement and declared screen-to-screen navigation. All other behavior is delegated to `app.py`.

Unknown events must be safe. The generated base application may ignore them or return an unhandled result; it must not crash because the developer has not implemented a placeholder yet.

## Lifecycle flow

The generated base application follows this sequence:

1. Picoware calls `start(view_manager)` in the top-level entrypoint.
2. The entrypoint creates or starts the user-owned application object.
3. The application initializes its state and generated UI.
4. The generated UI renders its configured start screen.
5. Picoware calls `run(view_manager)` for input processing.
6. Focus and declared navigation are handled structurally.
7. Activation emits a stable event to the user-owned application handler.
8. Picoware calls `stop(view_manager)` for cleanup.

The generated base must remain runnable when the event handler contains only no-op placeholders. This proves the structure and presentation without pretending the editor generated a finished application.

## Deployment contract

A v1 generated application is deployed as one entrypoint plus its package:

```text
/sd/picoware/apps/My App.py
/sd/picoware/apps/my_app/__init__.py
/sd/picoware/apps/my_app/app.py
/sd/picoware/apps/my_app/generated_ui.py
/sd/picoware/apps/my_app/generated_assets.py
/sd/picoware/apps/my_app/generated_assets.pga
```

The exact frozen or unfrozen destination may vary by the developer's normal Picoware workflow, but the relative imports and package layout remain consistent.

The `.picogui.json` project, editor recovery files, source backups, reference images, and desktop thumbnails are development artifacts and are not included in the device runtime package unless the developer explicitly chooses to ship them.

## Validation requirements

Before a generated application is considered valid, the editor should verify:

- All generated Python parses successfully.
- Every linked asset ID exists.
- Every screen and element ID is unique.
- Every navigation target exists.
- Package and module names are valid Python identifiers after sanitization.
- Asset dimensions, frames, resource offsets, row lengths, and opacity masks are valid.
- Transparent regions do not generate visible black pixels.
- Dense 320×320 imported images keep the generated Python manifest bounded and load
  in the real simulator without a parser `MemoryError`.
- Generated modules have no desktop-only imports.
- Regenerating an unchanged project produces no diff.
- The application base can start, render, accept an unhandled event safely, and stop.

Hardware-specific performance claims still require PicoCalc or target-board evidence. Desktop and simulator checks establish generation correctness and parity, not final device performance.

## Compatibility and migration

Version 1 establishes the ownership boundaries and contracts above. It does not require the asset encoding to remain frozen forever.

Future versions may introduce:

- Shared palettes across assets.
- Frame deduplication.
- Board-specific opaque blit optimization.
- Compression inside the versioned binary resource.

Such changes must increment the asset or structure format version. The editor should recognize older generated projects and offer an explicit reviewed migration. It must not silently reinterpret unknown data.

## Summary of v1 decisions

- Generated applications use a thin entrypoint and a seven-artifact package.
- User behavior and generated presentation have separate ownership.
- Python contains only asset metadata and the bounded renderer; RGB565 pixels live in
  the required `generated_assets.pga` package resource.
- Linked assets are canonical and are not duplicated in generated screens.
- Detached assets remain compact snapshots.
- Row-streamed RGB565 plus explicit opacity masks are the portable runtime encoding.
- Natural dimensions and integer scaling are the runtime default.
- Arbitrary resizing is baked with nearest-neighbour conversion.
- Generated files are versioned, validated, reviewed, backed up, and written atomically.
- The generated base is runnable but deliberately contains no invented application functionality.
