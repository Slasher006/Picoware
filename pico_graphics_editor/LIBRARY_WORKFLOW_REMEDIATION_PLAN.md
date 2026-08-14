# Asset Library Workflow Remediation Plan

Status: implemented and validated

## Objective

Make the Personal Asset Library safe and predictable for daily use. Every visible
selection must be actionable, imports must show the exact RGB565 result before an
automatic write, destructive changes must be recoverable, and the primary workflows
must remain obvious at 1366 x 768.

## Phase 1: Safe catalogue states

- Distinguish empty library, no search results, and unavailable/corrupt storage.
- Clear a selection when its item becomes hidden and disable record actions.
- Keep storage errors visible until a successful refresh.
- Disable imports and mutations while storage is unavailable; retain Retry and Copy
  Path so the user can diagnose the problem.

Acceptance:

- `0 of N assets` never leaves a hidden asset editable or deletable.
- A read failure is never presented as an empty library.
- Refreshing a repaired library restores the normal workspace.

## Phase 2: Reviewed image conversion

- Add one Library Image Import Review dialog after source selection.
- Show source and exact converted RGB565 previews side by side.
- Expose target dimensions, palette size, dithering, and animation interval locally.
- Report source dimensions, converted dimensions, frame count, transparency, and
  downscaling before Import or Replace is allowed.
- Use those reviewed settings rather than hidden Pixel Art inspector values.

Acceptance:

- Import and Replace store the exact previewed frames.
- Cancelling review does not write the library.
- Animation timing and downscaling are explicit.

## Phase 3: Recoverable mutations

- Capture the complete validated library snapshot before add, batch import, replace,
  rename, duplicate, or delete.
- Keep a bounded in-memory undo stack and expose Undo Last Library Change.
- Restore snapshots atomically without changing project copies.
- Describe the recoverable operation in the workspace and status bar.

Acceptance:

- Delete and Replace can be undone during the current editor session.
- Undo restores stable IDs, names, pixels, frames, origins, and durations.
- A failed write never consumes the undo entry.

## Phase 4: Clear workflow hierarchy

- Make Add to App GUI and Edit in Pixel Art the two primary selected-asset actions.
- Move replace, duplicate, export, rename, delete, IDs, fingerprint, and storage path
  into collapsible management and technical-detail sections.
- Remove repeated workspace headings where the document strip and tab already identify
  the library.
- Make double-click open the complete asset in Pixel Art; keep insertion explicit by
  button, Enter, context menu, or drag-and-drop.
- Select and scroll to newly imported or duplicated records.

Acceptance:

- The primary actions remain visible at 1366 x 768 without technical clutter.
- A completed create/import/duplicate operation visibly selects its result.
- Double-click never mutates the GUI project.

## Phase 5: Efficient repeated use

- Persist catalogue display mode and splitter position.
- Add Ctrl+F search focus, Enter add-to-project, F2 rename, Delete confirmed delete,
  Space play/pause, and Ctrl+Z library undo while the Library workspace is active.
- Add animation play/pause with the stored per-frame durations and direct frame choice.
- Keep context menus and menu actions synchronized with visible selection and error
  state.

Acceptance:

- Preferences survive a new MainWindow instance.
- Animation playback advances with stored durations and stops on selection/workspace
  changes.
- Shortcuts never target a hidden workspace.

## Phase 6: Concurrent-change and long-animation safety

- Bind every undo/redo snapshot to a canonical content revision and discard history
  when Refresh detects a write from another editor instance.
- Disable panel, menu, and global history actions while storage is unavailable.
- Preserve an open Pixel Art master as an unlinked copy if its stored record is
  deleted or its pixels change elsewhere.
- Read supported GIF/WebP frame delays and default the review to original timing,
  with uniform timing as an explicit alternative.
- Convert visible and final animation frames in background workers with progress and
  cancellation instead of blocking the interface.
- Keep display names unambiguous with case-insensitive numeric suffixes.
- Render management sections as disclosure rows, show catalogue shortcut hints, and
  expose both Undo and Redo in the workspace state panel.

Acceptance:

- Undo never removes a concurrently added record, with or without a preceding Refresh.
- A deleted or replaced open master cannot overwrite a missing or newer record.
- Long animation conversion leaves the dialog responsive and can be cancelled.
- Variable source frame timing survives import unless the user selects uniform timing.

## Phase 7: File-manager selection and beginner starter assets

- Enable extended catalogue selection: Ctrl-click toggles individual assets, Shift-click
  selects ranges, and dragging over empty catalogue space draws a rubber-band selection.
- Keep one selection count visible and synchronize buttons, shortcuts, context menus,
  and workspace-menu actions with the complete selection.
- Add, duplicate, export, and delete selected assets as batches. Batch insertion arranges
  project copies in a compact grid and creates one project Undo entry; batch library
  mutations create one library Undo entry.
- Protect mixed selections: built-in records can be added, edited as unlinked copies,
  duplicated into Personal, and exported, but never renamed, replaced, or deleted.
- Ship 50 deterministic 16 x 16 RGB565 icons as a read-only Built-in collection and
  expose All, Built-in, and Personal filters. The icons are available without creating
  or modifying the personal-library file.
- Keep built-ins usable if personal-library storage is damaged, while disabling actions
  that would write to the damaged store.
- Reserve every visible name case-insensitively across Built-in and Personal. Imports,
  copies, saves, and renames use the next plain number (`Home 2`, `Home 3`) instead of
  creating an indistinguishable duplicate.
- Configure reserved names and IDs once on the store, and validate the same policy on
  normal writes, external loads, and Undo/Redo snapshot restoration.
- Preflight every batch-export filename against other selected frames and existing
  directory entries. Stage the complete PNG batch before publishing it, roll back on
  failure, and never overwrite an existing file.

Acceptance:

- Ctrl-click and rubber-band selection both select multiple catalogue cards.
- Enter or Add places the whole selection without overlapping every icon at one point.
- Batch duplicate/export/delete handles the complete selection and never mutates a
  built-in original.
- A fresh profile shows exactly 50 distinct, transparent, nonempty standard icons.
- The Quick Assets shelf and full Library both expose the same standard collection.
- No successful write can introduce a duplicate visible name.
- Invalid external files and history snapshots cannot introduce duplicate names or a
  personal record that impersonates a Built-in ID.
- Batch export leaves either every newly numbered PNG or no partial output.

## Validation

- Unit tests for hidden-selection safety, unavailable-state recovery, reviewed
  conversion settings, revision-guarded mutation undo, open-master conflicts, source
  timing, background conversion, unique names, result selection, double-click
  semantics, persistence, shortcuts, animation playback, Ctrl/rubber-band selection,
  atomic batch actions, read-only built-ins, and the exact 50-icon starter set.
- Existing Asset Library, Pixel Art, App GUI, generated-app, and simulator tests.
- Ruff format/check, compileall, `git diff --check`, and offscreen screenshots at
  1366 x 768 and 1024 x 768.
