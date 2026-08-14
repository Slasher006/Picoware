# Generated App Structure v1 Implementation Plan

## Status and authority

This document is the implementation handoff for the approved Picoware Generated App Structure v1. It does not authorize deployment, publishing, device synchronization, or removal of the legacy exporter.

Implementation amendment (2026-08-11): real-simulator proof showed that a dense
300×320 imported image expanded the legacy live app to 1.71 MB and exhausted the
MicroPython parser heap. The approved runtime representation is therefore the
required streamed companion resource documented below. A later memory-hardening pass
upgraded that sidecar to indexed `PGA2`: per-asset metadata moved out of Python, and
only screen-referenced assets are deployed. Older palette/run and `PGA1` steps remain
useful as desktop canonicalization and migration history, but are not current runtime
output.

The implementation must follow these documents:

- [GENERATED_APP_STRUCTURE.md](./GENERATED_APP_STRUCTURE.md)
- [GENERATION_BLUEPRINT_V1.md](./GENERATION_BLUEPRINT_V1.md)
- [GOLDEN_GENERATED_APP_EXAMPLE.md](./GOLDEN_GENERATED_APP_EXAMPLE.md)

If implementation reveals a conflict between those documents, stop and update the documentation through review before choosing behavior in code.

## Objective

Add an explicit **Export Generated App Structure v1** workflow to the Pico Graphics and GUI Designer. It must generate a thin Picoware entrypoint and a seven-artifact package, keep user behavior separate from editor-owned presentation, encode assets once in a streamed binary resource, and safely regenerate only editor-owned artifacts.

The generated application is a runnable base structure. It must not invent application-specific functionality.

## Required output

For `My App`, the exporter creates:

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

Ownership is fixed:

- `My App.py`, `my_app/__init__.py`, and `my_app/app.py` are create-once developer-owned files.
- `my_app/behavior_handlers.py` is developer-owned and receives only reviewed missing-handler additions.
- `my_app/generated_behavior.py`, `my_app/generated_ui.py`, `my_app/generated_assets.py`, and `my_app/generated_assets.pga` are versioned editor-owned artifacts.
- The `.picogui.json` project remains the design source and is not a runtime dependency.

## Existing implementation map

The current code has useful pieces, but they implement a legacy single-file export:

| Existing area | Current responsibility | V1 treatment |
| --- | --- | --- |
| `pico_graphics_editor/designer_model.py` | Project schema, persistence, single-file GUI generation | Retain schema ownership; add v1 model fields and route new generation to dedicated modules |
| `generate_python()` | Generates one GUI renderer class | Preserve for legacy export during transition |
| `_embedded_asset_python()` | Expands asset runs into screen drawing statements | Do not use in v1 output |
| `build_designer_patch()` | Builds one reviewed source patch | Preserve for legacy export |
| `pico_graphics_editor/window.py::_export_gui_python()` | Single-file export dialog and apply flow | Keep as explicitly labeled legacy workflow |
| `SourcePatch` | Represents one file replacement | Reuse per file where practical; add a multi-file transaction abstraction |
| `MultiPatchDialog` | Reviews several source patches | Reuse or generalize for the eight-artifact review |
| `GuiPixelAsset` and `asset_runs` | Carry one rendered asset snapshot into the GUI project | Migrate to a project asset catalogue with stable asset IDs |
| `generate_live_app_python()` | Builds the legacy temporary one-file simulator preview | Preserve for compatibility; route current-design preview through the generated package and streamed resource |

The root `graphics.py`, root `generated_gui.py`, and unrelated dirty files are not inputs to this implementation and must remain untouched unless the user explicitly changes scope.

## Proposed desktop module boundaries

### New: `pico_graphics_editor/asset_codegen.py`

Pure desktop-side asset encoding and generated asset-module creation:

- V1 editable/canonical asset record model.
- Deterministic RGB565 plus one-bit-opacity resource encoding.
- Resource header, frame-offset, and row-length validation.
- Static and animation frame encoding.
- Canonical fingerprint serialization.
- `generated_assets.py` manifest/streaming-runtime generation.
- `generated_assets.pga` resource generation.
- Golden-record reconstruction helpers used only by tests.

This module must not import Qt or write files.

### New: `pico_graphics_editor/generated_app.py`

Pure generation orchestration:

- Display-name and Python-name sanitization.
- Generated header creation and parsing.
- Create-once entrypoint template.
- Create-once `__init__.py` template.
- Create-once `app.py` template.
- `generated_ui.py` generation.
- Cross-file validation.
- Output-path resolution.
- Six-artifact patch-set construction, including binary fingerprints and rollback.
- Regeneration classification.

This module must not show dialogs and must not write until an explicit apply operation.

### Existing: `pico_graphics_editor/designer_model.py`

Retain project persistence and legacy generation, but add the v1 project data needed by the new generators:

- Stable project ID.
- Stable project asset catalogue.
- Stable event IDs separate from display names.
- Element-to-asset ID references.
- Explicit migration from format 7 to format 8.

Do not place compact encoding algorithms back into this already large module.

### Existing: `pico_graphics_editor/designer.py`

Update editor behavior around the new project asset catalogue:

- Add or refresh one canonical project asset.
- Insert elements that reference a project asset ID.
- Create one snapshot asset when detaching or placing a draft.
- Keep current/modified/missing/draft/detached states visible.
- Avoid copying canonical run arrays into every linked element.

### Existing: `pico_graphics_editor/window.py`

Add the user-facing export workflow:

- New explicit v1 export action.
- Destination selection and resolved output preview.
- Preflight conflict reporting.
- Multi-file diff review.
- Transactional apply and rollback reporting.
- Success summary distinguishing created, preserved, and regenerated files.

Keep legacy export available and clearly labeled during the transition.

## Project schema version 8

The current designer project format is 7. V1 requires format 8 because linked assets and stable events need first-class identities.

### New project fields

- `project_id`: stable project identifier created once.
- `assets`: ordered project asset catalogue.
- `generated_app`: optional export metadata containing the last selected destination, sanitized package name, structure version, and last-known generated-file fingerprints.

### New project asset record

Add a JSON-compatible desktop model, tentatively named `ProjectAsset`, containing:

- `id`: stable asset ID.
- `name`: human-readable name.
- `source_path`: relocatable project-relative source path when linked.
- `absolute_fallback`: last known absolute source path for recovery only.
- `qualified_name`: source graphic name when linked.
- `fingerprint`: canonical content fingerprint.
- `link_state`: current, modified, missing, detached, or draft.
- `width`, `height`, `origin_x`, and `origin_y`.
- `frames`: lossless project-side frame pixels or compact records.
- `durations`: optional ordered frame durations.

The persistence representation must favor correctness and migration clarity. Runtime compactness applies to generated output, not necessarily to the editable JSON project.

### Element changes

Add:

- `asset_id`: stable reference into `GuiProject.assets`.
- `event_id`: stable activation event identity.

Retain human-facing `event_name` as a label or semantic alias. Screen-flow connections must reference stable event IDs, not display labels.

Legacy element asset fields remain readable during migration but are no longer authoritative after a successful format-8 conversion.

### Connection changes

Add `trigger_event_id` while retaining the existing human-readable trigger text for display and imported-source compatibility.

Generated v1 navigation uses `trigger_event_id`. Legacy generation may continue using the existing trigger behavior until its eventual retirement.

## Format 7 to 8 migration

Migration must be deterministic, additive, and testable.

1. Create `project_id` if absent.
2. Create stable `event_id` values for activatable elements.
3. Match each connection to its source element where possible and copy that element's event ID into `trigger_event_id`.
4. Group linked element assets by resolved source key and fingerprint.
5. Create one canonical `ProjectAsset` per group.
6. Point every grouped element to the canonical asset ID.
7. Convert each detached or draft element into its own snapshot asset keyed from the element ID.
8. Preserve missing links as project assets with their last known compact snapshot and a missing state.
9. Preserve all legacy fields while migration is in memory so rollback or older export remains possible.
10. Write format 8 only through the existing reviewed project-save path.

Migration must not silently alter a project merely because it was opened. Mark the in-memory project as migration-needed, show the change when saving, and retain the original file backup.

If two legacy elements have the same source key but different fingerprints, do not merge them. Treat them as separate modified snapshots until the user refreshes or resolves them.

## Multi-file transaction model

V1 cannot safely apply five unrelated writes one at a time without a transaction boundary.

Add a patch-set abstraction containing:

- Resolved root destination.
- One patch record per output file.
- Ownership classification: create-once or editor-owned.
- Planned action: create, preserve, regenerate, conflict, or unsupported-version.
- Original and updated bytes or text.
- Unified diff for review.
- Expected pre-write fingerprint.

### Preflight

Before review:

- Resolve all paths without creating directories.
- Reject paths escaping the selected destination.
- Detect case-folding filename collisions.
- Parse recognized editor-owned headers.
- Reject unknown structure versions.
- Classify existing create-once files as preserve.
- Treat unrecognized collisions as conflicts.
- Build every new file in memory.
- Parse every generated Python module.
- Validate cross-file references.

### Apply

After review acceptance:

1. Re-read and fingerprint every existing target.
2. Stop if any target changed after review.
3. Create a transaction backup directory.
4. Write each new file to a temporary sibling.
5. Flush and validate temporary content.
6. Replace targets only after all temporary files are ready.
7. On failure, restore every replaced target from the transaction backup.
8. Report exact created, preserved, restored, and unresolved paths.

Create-once files are written only when absent. They must never appear as modified content in a normal regeneration patch.

## Phase plan

### Phase 0: Freeze contracts and baseline

Scope:

- Treat the three approved documentation files as normative.
- Record the current targeted and complete editor test baseline.
- Inventory current dirty paths without staging or cleaning them.
- Confirm legacy single-file export behavior before changing generation code.

No production change belongs in this phase.

Exit criteria:

- Baseline results are recorded.
- Existing failures, if any, are separated from planned work.
- The implementation branch/worktree strategy preserves all unrelated changes.

### Phase 1: Pure compact asset encoder

Files:

- Add `pico_graphics_editor/asset_codegen.py`.
- Add `tests/pico_graphics_editor/test_asset_codegen.py`.

Implement:

- Palette index zero transparency.
- Visible black preservation.
- Deterministic palette ordering.
- Horizontal run extraction.
- Exact vertical merging.
- Frame and duration validation.
- Canonical fingerprint input.
- Record reconstruction for proof.

Tests:

- Fully transparent image.
- Visible black next to transparency.
- One-color rectangle merges vertically.
- Nonmatching rows do not merge.
- Multiple colors and sparse pixels.
- Negative origin metadata.
- Static and animated frame order.
- Determinism across repeated encoding.
- Invalid dimensions, palette references, rectangles, and durations.
- Round-trip equality for representative `PixelArt` fixtures.

Stop condition:

- Do not generate runtime Python until the encoder reconstructs every fixture exactly.

### Phase 2: Generated asset module

Files:

- Extend `asset_codegen.py`.
- Extend `test_asset_codegen.py`.
- Add golden source fixtures under `tests/pico_graphics_editor/fixtures/` only if inline expectations become unreadable.

Implement:

- Exact v1 headers.
- Private record table.
- `has_asset`, `asset_size`, `frame_count`, and `draw_asset`.
- Stable asset ordering.
- Canonical and snapshot records through the same renderer.

Tests:

- Generated source parses on CPython.
- Expected header metadata is exact.
- Unknown asset returns false safely.
- Invalid frame falls back to zero.
- Integer scale produces exact rectangle calls.
- A transparent asset succeeds without drawing black.
- One linked asset used several times exists once in the table.
- Generated source matches the approved golden shape.
- Generated module runs against a recording draw stub.

Stop condition:

- Do not connect the encoder to the GUI project until standalone generated-module behavior is proven.

### Phase 3: Schema 8 and project asset catalogue

Files:

- Update `pico_graphics_editor/designer_model.py`.
- Update `pico_graphics_editor/designer.py`.
- Update `tests/pico_graphics_editor/test_designer_model.py`.
- Update `tests/pico_graphics_editor/test_designer_ui.py`.

Implement:

- `project_id`.
- `ProjectAsset` catalogue.
- Element `asset_id` and `event_id`.
- Connection `trigger_event_id`.
- Format-7 migration.
- Canonical linked assets and per-element snapshot assets.
- Backward read compatibility.

Tests:

- New projects start as format 8 with stable IDs.
- Format-7 project migration preserves screens, elements, geometry, focus, and links.
- Duplicate linked placements share one project asset.
- Different fingerprints are not merged.
- Detached and draft placements receive independent snapshots.
- Missing links retain their last known pixels.
- Renaming a display name does not change an ID.
- Save/load round trip preserves every new field.
- Opening alone does not rewrite the project file.

Stop condition:

- Do not remove or stop reading legacy asset fields in v1.

### Phase 4: Generated UI module

Files:

- Add `pico_graphics_editor/generated_app.py`.
- Add `tests/pico_graphics_editor/test_generated_app.py`.
- Use data from `designer_model.py`; do not replace legacy `generate_python()`.

Implement:

- Exact generated UI header.
- Stable screen/event/asset IDs.
- Screen rendering through `draw_asset`.
- Focus operations.
- Structural navigation.
- Safe unknown screen and event behavior.
- No application service calls.

Tests:

- Two-screen golden project.
- Stable start screen.
- Focus ordering and wrapping.
- Screen without focusable elements.
- Navigation uses stable IDs after display-name changes.
- Activation returns the event even when navigation handles it.
- Linked and snapshot asset renderer calls.
- No asset rectangle tuples or expanded asset calls inside screen methods.
- Exact deterministic regeneration.
- Generated source parses and runs against recording stubs.

Stop condition:

- Generated UI must not import buttons, storage, networking, Qt, Pillow, or the project JSON.

### Phase 5: Create-once scaffolds

Files:

- Extend `generated_app.py`.
- Extend `test_generated_app.py`.

Implement:

- Entrypoint generation.
- Minimal `__init__.py` generation.
- User-owned `app.py` generation.
- Sanitized names and collision handling.
- Exact create-once notices.

Tests:

- Names with spaces, punctuation, Unicode, leading digits, and empty sanitized output.
- Correct relative imports.
- Lifecycle start/run/stop delegation.
- Directional focus and center activation.
- Structural Back navigation and fallback application exit.
- No invented event branches.
- Scaffold matches the golden example's contract.
- Existing create-once content is classified as preserve byte-for-byte.

Stop condition:

- Regeneration must never propose changes to an existing create-once file.

### Phase 6: Patch set and transactional apply

Files:

- Extend `generated_app.py` or add a narrowly focused `generated_patchset.py` if transaction code would make generation responsibilities unclear.
- Add transaction tests to `test_generated_app.py` or `test_generated_patchset.py`.

Implement:

- Eight-artifact patch set.
- Ownership/action classification.
- Header parser.
- Unsupported-version stop.
- Changed-after-review detection.
- Temporary writes, backups, replacement, and rollback.

Tests:

- Empty destination creates eight artifacts.
- Second unchanged generation produces no diff.
- Modified `app.py` is preserved exactly.
- Modified entrypoint and `__init__.py` are preserved exactly.
- Editor-owned changes produce only the applicable generated behavior, UI, and asset diffs.
- Unrecognized collision blocks all writes.
- Unknown structure version blocks regeneration.
- Simulated failure before replacement changes nothing.
- Simulated mid-replacement failure restores the complete prior set.
- Source changed after review blocks apply.

Stop condition:

- No GUI integration until failure injection proves rollback behavior.

### Phase 7: Editor export workflow

Files:

- Update `pico_graphics_editor/window.py`.
- Possibly add a focused dialog module if the window would otherwise grow substantially.
- Update `tests/pico_graphics_editor/test_window.py`.

Implement:

- **Export Generated App Structure v1...** action.
- Destination directory selection.
- Output path and ownership summary.
- Conflict and unsupported-version messages.
- Multi-file diff review.
- Explicit Apply/Cancel.
- Transaction success/failure report.
- Remember last destination only in editor settings or project export metadata after success.

Keep:

- Existing **Export GUI to Python...** as a legacy action.
- Existing live simulator generation unchanged.

Tests:

- New action is visible in GUI workspaces only.
- Cancel at destination, review, or apply leaves disk unchanged.
- Review lists all create/regenerate actions and preserved files.
- Active workspace save does not trigger export.
- Successful export reports exact paths.
- Conflict does not partially create a package.
- Legacy export still follows its existing path.

Stop condition:

- Do not relabel or remove legacy export in this phase.

### Phase 8: End-to-end golden application proof

Create the Status Demo project entirely in a temporary test location and generate the eight runtime artifacts.

Proof:

- Compare structural output with the golden documentation.
- Parse all seven Python files.
- Import the package using a MicroPython-compatible test environment or stubs.
- Start, render Home, move focus, enter Settings, go Back, tolerate Refresh Status, and stop.
- Confirm two badge placements use one canonical record.
- Confirm visible black and transparency render distinctly.
- Confirm frame zero fallback and integer scale.
- Modify user `app.py`, regenerate presentation, and prove its hash is unchanged.
- Regenerate without changes and prove an empty diff.

No physical-device claim is allowed from this phase alone.

### Phase 9: Documentation and migration handoff

Files:

- Update `pico_graphics_editor/README.md`.
- Update the three approved v1 documents only when implementation differs through an explicitly reviewed decision.

Document:

- How to create and export a v1 base application.
- Which files the developer may edit.
- How stable event IDs map to behavior.
- How linked and detached assets differ.
- How to regenerate safely.
- How legacy single-file export differs.
- Deployment file list.

Exit criteria:

- Documentation reflects tested behavior, not planned behavior.
- No legacy output is deleted automatically.

## Required test commands

Run focused tests after each authorized phase, then the complete editor suite before handoff:

```text
ruff check pico_graphics_editor tests/pico_graphics_editor
python3 -m compileall -q pico_graphics_editor
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests/pico_graphics_editor -p 'test_*.py'
git diff --check
```

When generated MicroPython output is introduced, add syntax/compatibility validation using the repository's available MicroPython executable or `mpy-cross` without adding `.mpy` artifacts to the requested source diff.

Do not run simulator, firmware, device, deployment, or publishing operations unless separately authorized.

## Review checkpoints

Pause for review after:

1. Compact asset record and renderer shape.
2. Format-8 schema and migration diff.
3. Generated UI public API.
4. Create-once scaffold text.
5. Transaction failure/rollback evidence.
6. First complete golden application diff.

These checkpoints prevent a lower-level implementation choice from silently changing the approved quasi-standard.

## Explicit exclusions

- No finished application behavior generation.
- No automatic network, storage, media, sensor, or timer logic.
- No opaque or unreviewed binary asset writes; the required `PGA2` resource is fingerprinted, budgeted, backed up, and applied in the same transaction.
- No arbitrary fractional runtime scaling.
- No automatic animation timer.
- No deletion of root `generated_gui.py` or `graphics.py`.
- No conversion of handwritten Picoware apps to this structure.
- No removal of legacy export.
- No automatic project migration write on open.
- No device copy, `.mpy` generation, firmware update, Git commit, push, or PR.

## Final acceptance checklist

- [x] All eight artifacts follow their ownership rules.
- [ ] User-owned files survive regeneration byte-for-byte.
- [ ] Editor-owned modules carry exact v1 headers.
- [ ] Format-7 projects migrate predictably to format 8 through reviewed save.
- [ ] Stable project, screen, element, event, and asset IDs survive renames.
- [ ] Linked placements share one canonical asset record.
- [ ] Detached/draft placements use compact snapshot records.
- [ ] Visible black is distinct from transparency.
- [ ] Static and animated assets reconstruct exactly.
- [ ] Integer scaling is crisp and deterministic.
- [ ] Generated screens contain no expanded asset pixels.
- [ ] Generated UI contains no application business logic.
- [ ] Unknown events and assets fail safely.
- [ ] Legacy export and live preview remain functional.
- [ ] Multi-file application is atomic and rollback-tested.
- [ ] Unchanged generation produces no diff.
- [ ] Complete editor suite passes cleanly.
- [ ] Documentation matches actual tested output.

## Recommended implementation order

Implement phases 1 through 9 in order. Do not start with the export button: the GUI is the final integration layer, not the place to discover the asset format, schema migration, or transaction semantics.

The first authorized coding slice should contain only Phase 1, the pure compact asset encoder and its focused tests.
