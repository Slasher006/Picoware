# Main Menu Reorganization Plan

Status: implemented and covered by automated tests.

## Objective

Make the menu bar describe the editor's five-workspace product model instead of its
implementation history. Global document commands must follow the active workspace,
important operations must not require right-click discovery, and project validation
must be available before simulation or generation.

## Target information architecture

The stable global menus are **File**, **Edit**, **View**, **Project**, **Simulator**,
and **Help**. One additional contextual menu occupies a stable position and changes
between **Pixel Art**, **App GUI**, **Screen Flow**, and **Asset Library**.

- **File** owns New, Open, Recent, Close Active Document, Save, Save As, Import,
  Export, and Quit. `Ctrl+O`, `Ctrl+W`, `Ctrl+S`, and `Ctrl+Shift+S` operate only on
  the active workspace.
- **Edit** owns shared history and context-sensitive selection operations.
- **View** owns workspace navigation and non-destructive view controls.
- **Project** owns validation, recovery, source synchronization, and generated-app
  outputs for App GUI, Screen Flow, and Simulator.
- The contextual workspace menu exposes the important commands also available from
  local buttons and right-click menus.
- **Simulator** remains globally visible so running and troubleshooting are never
  hidden.
- **Help** opens bundled workflow, generation, PGA3, and shortcut documentation.

## Phase 1: Context and shortcut correctness

- Route `Ctrl+O` to Python graphics in Pixel Art and GUI projects in project
  workspaces.
- Route `Ctrl+W` to the active Pixel Art document/source or GUI project, never to a
  hidden source scope.
- Give the active Save and Save As actions precise workspace-dependent labels.
- Remove duplicate Save and Export entries from top-level menus.
- Preserve dirty-state confirmations, reviewed Python diffs, backups, and atomic
  writes.

## Phase 2: Complete menu coverage

- Add File submenus for New, Open, Open Recent, Import, and Export.
- Add View navigation for all five workspaces using `Ctrl+1` through `Ctrl+5`.
- Expose Pixel Art canvas, frame, tracing, and destination operations.
- Expose App GUI screen, element, layer, asset, and preview operations.
- Expose Screen Flow node, relation, layout, trace, and test operations.
- Expose all Personal Asset Library management operations.
- Rename Run to Simulator and use project-specific wording.
- Hide or disable commands that cannot affect the active workspace or selection.

## Phase 3: Project preflight

- Add **Project > Validate / Preflight**.
- Report every deterministic project, screen, event, asset-scale, navigation, and
  behavior-flow issue in one selectable report.
- Distinguish blocking errors from warnings and informational observations.
- Use the same validation contract for generated output so a passing preflight has
  useful predictive value.

## Phase 4: Help and recent documents

- Persist a bounded, de-duplicated list of successfully opened source files, source
  folders, and GUI projects.
- Remove missing paths from the visible Recent list without touching user files.
- Add bundled documentation entries for Pixel Art, App Flow, generated applications,
  and PGA3.
- Add a keyboard-shortcuts reference and About/version dialog.
- Do not add an empty Preferences command before meaningful settings exist.

## Phase 5: Regression proof

- Test the exact visible menu set in every workspace.
- Test contextual Open, Close, Save, Save As, workspace shortcuts, recent paths, and
  Library/App GUI/Screen Flow action routing.
- Test passing and failing preflight reports, including non-integer asset scaling.
- Run formatting, compilation, the focused window suite, the complete editor suite,
  and whitespace validation.

## Acceptance criteria

- No active shortcut mutates a hidden workspace.
- A new user can find every primary workflow without right-clicking.
- Asset Library image and PGA2/PGA3 image recovery are available from the Library menu.
- Layer ordering and flow layout are available from the menu bar.
- Simulator access stays visible from every workspace.
- Preflight finds generator-blocking issues before a simulator/export attempt.
- Existing dirty checkout content, including root `graphics.py`, remains untouched.
