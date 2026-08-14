# Pixel Art Workflow Remediation Plan

Status: implemented and covered by automated tests.

## Objective

Make Pixel Art an asset-first workspace. Creating or importing graphics must produce an
immediately editable, lossless desktop document. Python, the Personal Asset Library,
App GUI, and PNG are explicit destinations; none is a prerequisite for drawing.

## Standard workflow

1. Start with **New Blank Asset**, **Import Image as Asset**, or **Open Python Graphics**.
2. Edit the actual RGB565 pixels shown on the canvas.
3. For animations, navigate and edit the complete ordered frame set with preserved
   durations.
4. Choose an explicit destination:
   - **Save to Library** stores or updates the editable reusable master.
   - **Place on Current Screen** embeds an independent App GUI project copy.
   - **Generate Python** creates a reviewed drawing function.
   - **Export PNG** writes the visible frame.
5. Continue editing the selected master. Generated PGA resources remain deployment
   output and are never modified in place.

## Phase 1: Correctness blockers

- Convert imported images immediately and display the real RGB565 result.
- Never present a tracing reference as saved pixel content.
- Save the visible imported pixels, transparency, frames, origins, and durations to the
  Personal Asset Library.
- Permit PNG export for drafts, library assets, and project assets.
- Keep Python source generation behind the established exact-diff, backup, validation,
  and atomic-write workflow.

Acceptance:

- A non-empty imported image produces a non-empty library asset without an extra hidden
  conversion step.
- `Ctrl+Shift+S` opens PNG export for every visible pixel document.
- A tracing reference is visibly labelled and never changes saved pixels unless the
  user chooses **Convert to editable pixels**.

## Phase 2: Draw-first authoring

- Change **New Asset** from a source-writing wizard into immediate in-memory creation.
- Ask only for asset name, dimensions, and starting content.
- Keep the new document recoverable before it receives a library or Python destination.
- Make `Ctrl+S` store a new draft in the Personal Asset Library; subsequent saves update
  that stable library record.
- Add a separate **Generate Python Asset** action available at any time.

Acceptance:

- Creating a blank asset never opens a destination chooser or writes a file.
- The user can draw, undo, resize, save to Library, place in App GUI, generate Python,
  or export PNG in any sensible order.

## Phase 3: Complete animation round-trip

- Open every frame of Personal Asset Library and project assets in Pixel Art.
- Preserve per-frame durations.
- Support frame selection, editing, add, duplicate, delete, reorder, playback, onion
  skin, and imported-frame append operations for portable assets.
- Update library assets atomically under their stable ID.
- Update project assets in memory and require the normal GUI-project save afterward.

Acceptance:

- Editing one animation frame retains every untouched frame and duration.
- **Update Library Asset** replaces the complete animation in one transaction.
- **Update Project Asset** updates the complete project animation without touching PGA.

## Phase 4: Discoverability and visual hierarchy

- Replace the empty source catalogue with three central first-step actions.
- Hide the unused catalogue until Python source is open.
- Disable painting, palette, and zoom controls when no document exists.
- Keep simulator controls visible in GUI-oriented workspaces but remove them from the
  Pixel Art document strip.
- Rename ambiguous controls: **Import Image as Pixel Asset**, **Add Tracing Reference**,
  **Select in App GUI**, **Generate Python**, and **Edit complete asset in Pixel Art**.
- Report **Erase Transparent** for managed assets and an RGB565 color only for
  source-backed erase overlays.

Acceptance:

- The idle workspace explains how to start without requiring menus or tooltips.
- At 1366×768 the entry actions, canvas, inspector, and primary destinations remain
  visible and do not overlap.

## Phase 5: Recovery and regression proof

- Extend pixel autosave recovery to unsaved portable static and animated assets.
- Retain the existing source-linked recovery format.
- Add tests for immediate blank creation, real imported pixels, reliable PNG export,
  atomic full-animation library updates, portable recovery, idle-state hierarchy, and
  existing source/project behavior.
- Run compile checks, whitespace checks, and the complete editor suite.

## Non-goals and boundaries

- PGA files are not edited in place.
- Importing PGA2/PGA3 recovers images, not WAV entries, screens, or the complete GUI project.
- Generated Python remains reviewed output, not the editable master for new drafts.
- No physical-device performance claim is made by desktop workflow tests.
