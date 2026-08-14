# Node Editor App-Creation Implementation Plan

## Status and authority

This document is the implementation handoff for the complete node-editor audit.
It authorizes local editor, generator, example, documentation, and test changes only.
It does **not** authorize committing, pushing, publishing, deploying to PicoCalc, or
changing unrelated files.

Implement one work package at a time in the order below. Do not combine broad
refactors with a work package. Preserve the existing dirty worktree and, in
particular, do not modify the repository-root `graphics.py`.

The current MQTT example and tutorial are the end-to-end acceptance fixture:

- `pico_graphics_editor/examples/mqtt_client/`
- `pico_graphics_editor/MQTT_CLIENT_TUTORIAL.md`
- `examples/mqtt_client_editor_test/`

Do not run the MQTT app in the simulator until the editor workflow and design
changes in work packages 0 through 6 are complete. Pure model, generator, and Qt
unit tests may be run after each package.

## Objective

Turn Screen Flow from a structural diagrammer into a fast, safe app-creation
workflow while retaining a clear escape hatch for developer-written behavior.

The completed workflow must let a new user:

1. Select an interactive screen element.
2. Create a bound behavior in one guided operation.
3. Compose common behavior from typed, allowlisted nodes or a reusable recipe.
4. Generate reviewed editor-owned runtime code and developer-owned handler stubs.
5. See which nodes are structurally valid, executable, implemented, or incomplete.
6. Trace behavior in the editor and validate the finished app in the simulator.

For a simple button-to-action flow, reduce the current approximate 12–18 UI
interactions plus manual wiring/code discovery to 3–5 guided choices before code
review.

## Confirmed baseline

Do not redesign these working foundations:

- `designer_model.py` owns `FlowNode`, `FlowPort`, `BehaviorConnection`, groups,
  typed connection validation, persistence, and diagnostics.
- `designer.py` owns the Screen Flow canvas, inspector, graph interactions,
  structural trace, and fragment UI.
- `flow_library.py` owns fingerprinted, atomically written personal flow fragments.
- `generated_app.py` owns generated package structure, preflight, reviewed patch
  sets, transactional apply, generated UI, and structural behavior records.
- App Flow Standard v1 intentionally records contracts but does not execute them.
- Existing node kinds are Event, Condition, Action, State, Timer, Data, Component,
  and Comment.
- Existing strengths to preserve include typed ports, stable IDs, mouse wiring,
  copy/paste/duplicate, grouping, collapse, pin, lock, breakpoint, search, fit,
  zoom, pan, minimap, auto-layout, diagnostics, trace, and the personal fragment
  library.

The following defects and gaps are in scope:

1. Valid data edges can show a modal dialog from `paintEvent()` and terminate Qt.
2. Navigation relations with Action or Condition text are accepted but silently
   omitted from generated navigation.
3. Changing a behavior node kind can silently delete incompatible connections.
4. The inspector exposes raw JSON instead of node-specific controls.
5. A screen element event cannot be directly bound to a behavior Event node.
6. “Generated stub” is only a suggested name; it does not generate a method.
7. Personal fragments have no built-in recipes, metadata/search, preview,
   external anchors, cursor insertion, or useful Component linkage.
8. Graph manipulation lacks marquee selection, keyboard movement, selected-scope
   layout, and compatible-target filtering.
9. Structural trace does not show real runtime execution or values.
10. The generator does not produce behavior-focused tests.

## Non-negotiable design rules

- Never open a dialog, mutate the project, or perform validation side effects from
  a paint method.
- Never silently drop a node, connection, relation, property, or generated branch.
- Never execute arbitrary Python, expressions, imports, or `eval()` content from a
  graph property.
- Keep display names separate from stable IDs. Renames must not break bindings.
- Keep editor-owned generated files separate from developer-owned files.
- Regeneration must never overwrite a developer handler body.
- Every executable operation must be allowlisted, schema-validated, deterministic
  where possible, and compatible with MicroPython.
- Network, storage, timers, UI, and MQTT must be injected runtime services so the
  simulator can use deterministic fakes.
- Do not persist broker passwords or other secrets inside recipes, generated files,
  screenshots, or tutorial fixtures. Nodes may reference a settings key.
- App Flow Standard v1 files must continue to load. Introduce an explicit version
  transition for executable semantics; do not reinterpret old graphs silently.
- Preserve the legacy structural-only workflow for projects that do not opt into
  executable behavior.

## Target architecture

### Model and operation registry

Keep the eight visual node kinds, but give executable nodes an allowlisted
`operation` contract. Add a Qt-free registry module, tentatively
`pico_graphics_editor/behavior_operations.py`, containing immutable definitions:

- operation ID and compatible node kind;
- human label, category, short description, and search terms;
- property schema with type, default, required state, choices, and help text;
- operation-specific typed ports;
- execution capability: structural-only, built-in runtime, or custom handler;
- supported targets: desktop trace, simulator, and MicroPython device runtime.

Do not scatter operation-specific `if` trees across the inspector, generator, and
runtime. All three must consume the same registry.

Add explicit first-class bindings rather than hiding them in free-form properties.
A behavior Event binding must contain stable `screen_id`, optional `element_id`, and
`event_id`. A Component reference must contain a stable recipe/fragment identity,
version, and fingerprint or be an explicit detached copy.

### Generated runtime ownership

Extend Generated App Structure with:

```text
<package>/
├── app.py                 # developer-owned, create once
├── behavior_handlers.py   # developer-owned, create once
├── generated_behavior.py  # editor-owned, regenerated
├── generated_ui.py        # editor-owned, regenerated
├── generated_assets.py    # editor-owned, regenerated
└── generated_assets.pga   # editor-owned, regenerated
```

`generated_behavior.py` must contain contracts, bindings, implementation status,
the safe operation dispatcher, and the execution trace API. It must accept injected
UI, state, timer, storage, and MQTT services.

`behavior_handlers.py` must contain developer-owned handler stubs keyed by stable
stub names. Generate the file once. Later exports report missing handlers and offer
a reviewed additive patch or copyable stub text; they must not rewrite existing
functions.

New `app.py` scaffolds wire the runtime and handlers together. Existing create-once
`app.py` files must be preserved. For an old export, show an explicit migration
diagnostic and reviewed wiring snippet instead of editing it silently.

`GeneratedUI.dispatch_behavior()` must no longer claim to execute behavior. Either
delegate to the injected behavior runtime or retain an explicitly named
`describe_behavior_contract()` compatibility API. Do not leave two ambiguous
dispatchers.

## Work package 0 — Safety and correctness

Complete this package before adding features.

### 0.1 Make behavior-edge painting pure

Current fault: `BehaviorFlowCanvas._draw_behavior_connection()` rejects non-event
ports with `QMessageBox.information()` even though data-to-data edges are valid.

Implementation:

- Remove all dialog calls from `_draw_behavior_connection()`.
- Resolve both ports once.
- Return quietly only for missing nodes/ports.
- Draw valid event and data edges. Keep labels and arrowheads; use the existing
  event/data visual language and red only for `behavior_connection_error()`.
- Keep “Insert Action” eligibility checks in the user-triggered insertion command,
  where an informational message is safe.

Acceptance:

- Event and Data-to-State edges paint repeatedly without a dialog.
- Invalid persisted edges paint as invalid without crashing.
- A paint test patches `QMessageBox.information` and proves it is never called.

### 0.2 Block silent navigation omission

Current fault: `generated_app._generatable_connection()` returns false when
`FlowConnection.action` or `.condition` is non-empty, while preflight accepts it.

Implementation:

- Until executable navigation hooks exist, add error diagnostics named
  `unsupported-navigation-condition` and `unsupported-navigation-action`.
- Target each diagnostic at the relation ID and name the source, trigger, target,
  and unsupported field.
- Make reviewed export impossible while either error exists.
- Keep `_generatable_connection()` defensive, but add a test proving every relation
  that passes preflight produces exactly one navigation branch.
- When work package 5 supplies an executable equivalent, migrate through explicit
  bound Condition/Action nodes; do not start interpreting the old text as code.

Acceptance:

- No valid preflight can result in a silently missing navigation branch.
- Diagnostics double-click to select the exact relation.

### 0.3 Prevent destructive kind changes

Current fault: `_apply_behavior_node()` resets ports and removes incompatible edges.

Implementation:

- Add a pure model helper that previews a kind change: new ports, preserved edges,
  remappable edges, and edges that would be removed.
- If the change is lossless, apply it normally.
- If an edge is remappable by unambiguous direction and type, show the proposed
  endpoint mapping.
- If anything would be removed, require a confirmation listing every affected edge.
- Cancel must leave kind, ports, connections, selection, and dirty state unchanged.
- Add one undoable editor command if the editor already has an undo-command path;
  otherwise keep the change as one atomic mutation and document the limitation.

Acceptance:

- A node kind can never delete a connection without explicit confirmation.
- Tests cover cancel, lossless change, unambiguous remap, confirmed removal, locked
  edges, and dirty-state behavior.

### 0.4 Correct misleading language

- Rename “Generated stub” to “Suggested handler” until real stub generation lands.
- After work package 2, show “Handler” plus status: Built in, Implemented, Missing,
  Structural only, or Invalid.

## Work package 1 — One-click behavior creation

Add **Create behavior from this element…** to the element inspector and relevant
canvas/hierarchy context menus.

The guided dialog must:

1. Show the selected element and stable activation event.
2. Offer common outcomes: Run action, Navigate, Check condition, Insert recipe, or
   Custom handler.
3. Create and bind an Event node automatically.
4. Create the chosen downstream node(s), connect compatible ports, give useful
   names, place them near an uncluttered graph position, select them, and switch to
   Screen Flow.
5. Reuse an existing bound Event node after explicit confirmation instead of
   silently creating duplicates.

Navigation creation must build the stable screen relation and its corresponding
behavior binding as one atomic command. The relation remains the source of screen
transition truth; do not create two independently editable navigation definitions.

Also support dragging a behavior connection into empty graph space. On release,
show a searchable palette filtered to operations with a compatible input port.
Choosing an entry creates and connects the node at the pointer position. Escape or
dismissal makes no change.

Acceptance:

- A button-to-custom-handler graph takes at most five deliberate choices.
- A button-to-screen navigation takes at most four.
- Element rename and screen rename do not break bindings.
- Deleting a bound UI element reports and safely removes or detaches its bindings.
- Duplicate binding diagnostics identify both Event nodes.

## Work package 2 — Reviewed handler generation

### Generator changes

- Add `generated_behavior.py` to path resolution, patch-set validation,
  fingerprinting, transactional apply, rollback, and success reporting.
- Add create-once `behavior_handlers.py` to the ownership classifier.
- Parse every generated Python artifact before review.
- Validate that every Custom Handler node has a stable handler name and every name
  is collision-free after sanitization.
- Report existing, missing, and built-in implementations during preflight.

### Developer file behavior

Initial `behavior_handlers.py` must contain a small handler class or module with one
stub per current Custom Handler node. Each stub must have a stable signature and a
clear `NotImplementedError` or explicit unimplemented result.

For later nodes:

- Never regenerate the file wholesale.
- Detect functions by stable generated name through `ast`, not substring matching.
- Present missing stubs in the multi-file review as a separate additive patch that
  the user may accept.
- Refuse automatic insertion if the existing file does not parse.
- Preserve imports, comments, formatting, and all handler bodies outside the exact
  insertion point.

Acceptance:

- Export, edit a handler body, add another Custom Handler node, and export again.
  The first body remains byte-for-byte unchanged and the second stub is offered.
- Existing generated packages remain runnable after the documented wiring step.
- Implementation badges agree with generator preflight.

## Work package 3 — Node-specific property forms

Replace raw JSON as the default editor with registry-driven forms.

Required UI behavior:

- Show common fields first: name, description, operation, binding/component target,
  pin, lock, and breakpoint.
- Render property controls by schema: line edit, integer/float spin box, checkbox,
  choice combo, stable screen/element/widget selector, state-key selector, or
  settings-key selector.
- Put advanced JSON behind an explicit expandable section.
- Keep form and JSON views synchronized through one validated model update.
- Preserve unknown properties from newer versions and label them read-only; never
  discard them on save.
- Show short tooltips with an `Example:` sentence for non-obvious fields.
- Changing an operation must use the same connection-impact preview as a kind
  change.

Acceptance:

- Common nodes require no JSON editing.
- Invalid property values are rejected before project mutation.
- Round-trip tests prove unknown property preservation.
- Keyboard tab order follows the visible form and advanced controls stay out of the
  default path.

## Work package 4 — Built-in recipes and improved fragments

Keep personal fragments separate from read-only bundled recipes.

### Metadata and storage

Add a backward-compatible library migration with:

- description, category, tags, author/source, semantic version, and minimum flow
  standard version;
- optional preview metadata;
- named external input/output anchors with node ID, port ID, label, and type;
- optional Component identity and dependency metadata.

Do not mutate the personal library merely by reading version 1. Migrate only during
an explicit reviewed save/update.

### Library UI

- One combined browser with Built in and Personal source filters.
- Search name, description, category, and tags.
- Show a compact node/edge preview, required services, inputs, outputs, and
  compatibility state before insertion.
- Insert at pointer position or visible viewport center, not a fixed coordinate.
- After insertion, select all inserted nodes and highlight unconnected anchors.
- Allow a Component node to reference, open, detach, or expand a compatible recipe.
- Missing or changed referenced personal components must produce diagnostics, not
  silent substitution.

### Required bundled recipes

1. Button → Action → Status
2. Form → Validate → Save → Back
3. Confirm → Success / Cancel
4. Async task → Loading / Success / Error
5. Timer start / elapsed / stop
6. Settings load / edit / save
7. Menu selection → State update
8. MQTT connect / connected / error
9. MQTT publish / success / error
10. MQTT message → inbox state / UI update
11. Wi-Fi connect / retry / cancel

Each recipe needs a one-paragraph explanation, tags, visible anchors, and tests that
insert it twice with independent IDs.

## Work package 5 — Safe executable standard nodes

This package introduces the next explicit App Flow Standard version. Update
`APP_FLOW_STANDARD_V1.md` only to clarify compatibility; add a new standard document
for executable semantics.

### Execution model

- Start from a bound event and process a bounded queue of typed emissions.
- Pass a small context object containing event ID, payload, state view, and trace ID.
- Validate inputs and operation properties before execution.
- Emit only declared output ports.
- Stop with an explicit diagnostic on an unknown operation, missing required
  service, type mismatch, step-limit breach, or unimplemented custom handler.
- Use a configurable small step limit to prevent unbounded synchronous cycles.
- Timer and asynchronous service callbacks resume through explicit event tokens;
  do not block the UI loop.
- Record trace entries without serializing secrets.

### Minimum built-in operation set

Navigation and UI:

- navigate to screen; back; focus element;
- read widget value; set widget value;
- set label/status text; set progress value; show/hide/enable element;
- show Alert and emit success/cancel.

State and logic:

- get, set, clear, increment, append, and toggle state;
- compare equal/not-equal/less/greater, empty/non-empty, and boolean;
- map a named value into a declared output without arbitrary expressions.

Lifecycle and storage:

- start/cancel one-shot timer;
- load/save/delete a named settings key;
- convert service outcomes into success/error/cancel ports.

Connectivity:

- MQTT connect, disconnect, subscribe, unsubscribe, publish;
- MQTT connected, disconnected, message, and error events;
- Wi-Fi connect/status/retry/cancel when an injected service supports it.

Custom Handler remains the escape hatch for behavior outside this allowlist.

### Runtime service contracts

Define minimal protocol-like contracts without requiring `typing.Protocol` at
MicroPython runtime. Provide desktop fakes for tests and adapter implementations for
the current generated app surface. MQTT operations must work with the deterministic
mock transport in the example; device transport remains developer-owned.

Acceptance:

- The same graph produces the same ordered trace with fake services.
- Missing services fail visibly and do not crash the UI loop.
- Error and cancel paths are separate and testable.
- Generated runtime parses with CPython and `mpy-cross`.
- No generated module imports PySide6 or desktop-only packages.

## Work package 6 — Fast graph manipulation and observability

### Editing speed

- Drag empty canvas space for marquee selection; modifier keys add/remove selection.
- Arrow keys nudge selected unlocked nodes by one graph unit; Shift+Arrow uses a
  larger grid step.
- Add layout-selected horizontally and vertically. Existing whole-graph layout must
  remain available and pinned nodes must remain fixed.
- During connection drag, dim incompatible ports and highlight compatible targets.
- Reject a drop with one concise status message after the gesture, never a paint
  dialog.
- Add duplicate-and-connect for the primary selection where type-safe.
- Keep all actions keyboard reachable and expose shortcuts in menus/tooltips.

### Runtime trace

- Keep structural trace and label it “Structural trace”.
- Add “Runtime trace” fed by generated behavior trace records.
- Show timestamp/order, node, input port, redacted payload summary, emitted port,
  duration, and outcome.
- Breakpoints pause dispatch between nodes, not inside paint or device callbacks.
- Provide Step, Continue, Stop, and Clear Trace.
- Cap retained entries and make the cap visible.

### Generated tests

Generate an editor-owned test manifest or test module that verifies:

- all bound UI event IDs resolve;
- every required handler exists or is explicitly expected to be missing;
- all operation properties and service requirements are valid;
- declared default condition branches are traceable;
- bundled recipe graphs retain their expected ports and connections.

Do not generate assertions for user business outcomes that the graph does not
declare.

## Work package 7 — MQTT example and tutorial conversion

Only begin after work packages 0 through 6 are complete and their focused tests pass.

### Editor example

- Open the bundled MQTT project through the real editor path.
- Convert its handwritten structural contracts into bound Event nodes, standard
  MQTT/state/UI operations, and Custom Handler nodes only where necessary.
- Use the bundled MQTT recipes so a new user can see how reuse works.
- Preserve system widgets, custom widgets, the MQTT pixel asset, navigation, and the
  developer-owned device transport.
- Keep deterministic simulator broker behavior; do not require internet access.
- Regenerate through the reviewed export path. Do not hand-edit editor-owned files.

### Tutorial

Rewrite the tutorial as a new-user path that starts with opening the example and
then explains:

1. Screen Flow versus Behavior Flow.
2. Creating a bound behavior from a Dashboard button.
3. Inserting and connecting the MQTT Publish recipe.
4. Editing node-specific properties without JSON.
5. Understanding Built in, Implemented, Missing, and Structural only statuses.
6. Reviewing generated behavior and adding a Custom Handler safely.
7. Using structural trace during design.
8. Running the completed app in the simulator and reading runtime trace.
9. What the simulator proves and what still needs PicoCalc hardware validation.

Every tutorial control label must match the real UI exactly. Add or update Help and
File menu entries only if their current actions remain backward compatible.

### Workflow profiling

Instrument or manually record completed interaction groups without making unit
tests depend on wall-clock speed. Capture:

- create bound button behavior;
- insert a recipe;
- configure MQTT publish;
- add a missing custom handler through reviewed generation;
- locate and fix one diagnostic;
- trace the publish path;
- regenerate the app.

Report interaction count, modal count, elapsed time, mistakes/recovery, and whether
the user had to edit JSON or search generated IDs manually. Compare with the prior
MQTT workflow audit. The target is 3–5 choices for a simple bound action, zero raw
JSON edits for standard nodes, zero manual stable-ID copying, and no modal dialogs
during graph drawing or dragging.

## Work package 8 — Universal widget functionality and value routing

Apply one behavior contract to native Picoware widgets and custom drawn elements.
Widget events must carry a safe structured payload instead of requiring users to
discover generated IDs or write glue code before they can use a value.

### Widget event payload

Every bound `event.ui` dispatch must provide these stable fields when available:

- `event_id`, `screen_id`, `element_id`, and `widget_type`;
- `value`, containing the widget's ordinary public value;
- `text` for text, menu, list, choice, keyboard, and search values;
- `checked` for Toggle and Toggle List state;
- `index` for widgets with an exposed selected index.

The payload must be assembled by the runtime through the generated UI public
boundary. Explicit service-event payloads remain unchanged. Old graphs that use
`Read widget value` continue to receive the scalar value after that node.

### Safe payload references and extraction

Add a `Get payload field` operation with an allowlisted field selector. Add an
optional payload field to Compare so it can branch on `value`, `text`, `checked`,
or `index` without an extraction node.

Allow exact, non-evaluated property references: `$payload`, `$value`, `$text`,
`$checked`, `$index`, `$event_id`, `$screen_id`, `$element_id`, and
`$widget_type`. These are tokens, not expressions or interpolation. Resolve them
before calling runtime services. A missing reference must use Error or raise a
visible runtime error. Typed properties accept reference tokens without weakening
literal validation.

### Widget-specific guided creation

Extend `Create behavior from this element...` with:

- **Handle widget value**: Event -> Read widget value -> Custom Handler;
- **Branch by current value**: Event -> Compare using the widget's primary field;
- existing direct actions for event-only controls.

The Event binding records `widget_type`; stable screen, element, and event IDs stay
authoritative. Guided creation reuses a bound Event and mutates the graph atomically.

### Supported widget matrix

- Menu, Selectable List, and Choice: selected text/value and index where exposed.
- Toggle: boolean `value` and `checked`.
- Toggle List: `value` plus `index`, `text`, and `checked`.
- Keyboard Input: submitted response as `value` and `text`.
- Search and Select: selected result as `value` and `text`.
- Text Viewer: readable current text, but no fabricated activation event.
- Loading and Alert: action/display targets with no fabricated readable value.
- Custom Button and Icon: activation payload and mutable display text.
- Custom Label and Progress: readable runtime/design value and mutable value.
- Custom List: activation and readable text only; no invented row selection.
- Panel and Rectangle: visibility/enabled targets only.

### Acceptance

- Desktop and generated MicroPython runtimes produce equivalent widget payloads.
- Choice can branch without a handler; Keyboard can publish through `$text`;
  Toggle can drive MQTT retain through `$checked`; Toggle List fields extract.
- Existing Event -> Read widget value graphs retain scalar behavior.
- Invalid references never execute code and follow Error when connected.
- Guided creation works for every readable widget and applicable custom element.
- Tests cover widget payloads, reference validation, runtime parity, persistence,
  guided creation, generation, and the finished deterministic simulator flow.

## Work package 9 — Assisted flow authoring, live validator, and debugger

Make the node editor explain the next useful action and expose problems directly on
the canvas instead of requiring users to discover separate expert tools.

### Assisted editing and visual feedback

- Add a persistent assistant banner above the graph with current validity and the
  next actionable step.
- Show Error, Warning, and Information state on affected nodes with a visible badge
  and severity outline that does not rely on color alone.
- Give typed ports distinct colors for Event, Any, String, Boolean, Integer, and
  Data values while retaining text labels.
- During a connection drag, enlarge and highlight a compatible target, mark an
  incompatible target red, change the preview edge, and state the exact rejection
  reason in the assistant banner.
- Releasing on empty space retains the compatible-operation palette. Releasing on
  an incompatible port must not incorrectly open that palette.

### Live flow validator

- Revalidate automatically after project mutations and expose error/warning/info
  counts in both the assistant and Diagnostics inspector.
- Add severity filtering, Next issue, Go to issue, one-click Validate flow, and a
  concise suggested fix for each known diagnostic code.
- Detect missing Event entry points, unconnected Events, structural-only v2 nodes,
  behavior nodes unreachable from an Event, and misspelled payload references in
  addition to the existing port, binding, branch, cycle, property, and navigation
  checks.
- Center the exact target from validator navigation and keep the canvas badge map
  synchronized with the current findings.

### Deterministic flow debugger

- Replace placeholder Step/Continue status buttons with actual bounded runtime
  control: Start, Step one node, Continue until completion or breakpoint, Stop, and
  Clear Trace.
- Use deterministic editor-only UI, state, timer, storage, MQTT, Wi-Fi, and custom
  handler services. The debugger must not perform network, device, or file writes.
- Automatically build a widget payload for a bound UI Event, with an optional JSON
  override for testing alternate values and service events.
- Show ordered trace rows, selected entry node, next queued node, output port,
  outcome, duration, and redacted payload detail. Selecting a trace row centers its
  node and highlights the traversed graph path.
- Block debugger start on validator errors and point the user to Diagnostics.

### Acceptance

- A new empty graph tells the user how to create its first bound behavior.
- Invalid and incomplete nodes are visibly identifiable without opening an inspector.
- Compatible and rejected drag targets provide both visual and textual feedback.
- Validator findings filter, explain, and navigate to their exact targets.
- Step executes exactly one node; Continue respects breakpoints; Stop clears queued
  work; Clear removes trace and graph highlights.
- Debug services remain deterministic and offline, and secret-like payload fields
  remain redacted.
- Focused Qt/model/runtime tests and the complete editor suite pass at 1366x768.

## Test map

Add focused tests near the owning layer:

| Area | Existing or new test module |
| --- | --- |
| Ports, bindings, kind/operation migration, diagnostics | `tests/pico_graphics_editor/test_designer_model.py` |
| Canvas painting, guided creation, forms, selection, palette, trace UI | `tests/pico_graphics_editor/test_designer_ui.py` |
| Fragment migration, anchors, search metadata | new `tests/pico_graphics_editor/test_flow_library.py` or the existing UI/model split |
| Operation registry and executor | new `tests/pico_graphics_editor/test_behavior_runtime.py` |
| Generated files, ownership, additive stubs, preflight | `tests/pico_graphics_editor/test_generated_app.py` |
| Standard/version compatibility | `tests/pico_graphics_editor/test_flow_standard.py` |
| Window menus and example/tutorial actions | `tests/pico_graphics_editor/test_window.py` |
| MQTT end-to-end simulator flows | existing scripts under `examples/mqtt_client_editor_test/` |

For each package, first add a regression test that fails for the confirmed fault,
then implement the smallest owning-layer change, then run that focused module.
Avoid brittle screenshot pixel assertions when a state or signal assertion proves the
same behavior. Use offscreen Qt for automated UI tests.

## Validation sequence

Run in this order and stop on the first failure:

1. `python -m unittest` for the focused module changed by the current package.
2. All `tests/pico_graphics_editor/test_*` unit tests.
3. `python -m py_compile` for generated desktop and example Python files.
4. `mpy-cross` for every generated runtime and MQTT example Python file.
5. Project preflight; require zero errors and explain every remaining warning/info.
6. `git diff --check` on only the implementation paths.
7. Open the real editor, complete the documented design workflow, save, close, and
   reopen the project to prove persistence.
8. Only now run the deterministic MQTT connect/publish/receive simulator flow.
9. Run the native Menu/TextBox navigation simulator flow.
10. Compare generated files before and after a second export to prove deterministic
    regeneration and developer-file preservation.

Simulator success does not prove live broker, Wi-Fi, TLS, QoS 1/2, retained-message,
packet-loss recovery, timing, memory, or PicoCalc hardware behavior. State this in
the final audit.

## Implementation checkpoints

After each work package, record:

- files changed and why;
- schema/format changes and migration behavior;
- tests added and exact focused result;
- known limitations;
- whether the next package can begin without revising this plan.

Stop and request review if any of these occur:

- a generated/developer ownership rule must change;
- an old project would be reinterpreted instead of explicitly migrated;
- a standard operation needs arbitrary code or unrestricted imports;
- an existing developer-owned file would need automatic replacement;
- the real editor exposes different control labels or behavior than this handoff;
- the MQTT example requires live network access to pass.

## Definition of done

The complete effort is done only when:

- all ten audit findings are fixed or explicitly documented as deferred with a
  reason and a safe current behavior;
- no paint path can show dialogs or mutate project state;
- no connection or navigation branch can disappear silently;
- bound element events survive renames and project save/reload;
- standard nodes are configured with forms and run only allowlisted operations;
- handler generation preserves all developer code;
- bundled recipes are searchable, previewable, connectable, and independently
  reusable;
- graph editing supports marquee, nudge, selected layout, and compatible-port cues;
- structural and runtime traces are clearly distinct;
- generated tests and diagnostics agree on missing behavior;
- the updated MQTT tutorial is reproducible by a new user using exact UI labels;
- the finished MQTT app passes both deterministic simulator flows after design is
  complete;
- the final audit reports UX interaction counts, validation evidence, remaining
  hardware risk, and every deferred item.

## 2026-08-12 Screen Flow audit and remediation record

This pass audited the complete Screen Flow surface rather than one widget path. The
following findings are implementation-complete and protected by focused tests:

| Finding | Resolution |
| --- | --- |
| Behavior-edge Condition text was displayed but ignored by both runtimes | New graphs cannot create it; legacy values are read-only, explicitly clearable, and produce `unsupported-behavior-condition` errors directing users to a Condition node. |
| Navigation Condition/Action text was accepted but omitted by generation | New relations always leave both fields empty; legacy values are read-only and clearable; existing preflight diagnostics remain blocking. |
| Four node-creation paths exposed inconsistent subsets | Toolbar and graph menus now share the complete operation registry; element-guided creation and empty-space drops include every compatible executable operation. |
| Combined screen and behavior graphs became unreadable at fit scale | Added Screens + behavior, Screens only, and Behavior only modes; Fit visible respects the lane; Zoom selection returns to a readable scale; the legend names both layers. |
| Low-zoom ports were hard to acquire | Port hit targets keep a minimum physical size independently of graph zoom. |
| Collapsed groups and direct-drag geometry became stale or difficult to recover | Double-click and context menu expand/collapse groups; moved members resynchronize group bounds and minimap geometry. |
| Manual typed connections defaulted to invalid endpoints | Source selection filters compatible target nodes and inputs; Connect typed ports remains disabled until all four endpoint IDs are valid. |
| Debugger external services always succeeded and timers never fired | Added deterministic success/error/cancel scenarios, optional JSON service response, and explicit Fire timer callback queuing. |
| Trace inspection showed only input data | Desktop and generated trace records retain separate bounded, redacted input and output summaries plus the actual output port/outcome. |
| Tabs and panels did not explain structural versus executable testing | Tabs are Navigation, Debugger, and Preview; Preview carries a permanent structural-only notice; the workspace uses a resizable three-panel splitter and wrapped lists. |

Compatibility notes:

- No project-format bump was needed. Legacy fields remain round-trippable until the
  user chooses their explicit cleanup action.
- `RuntimeTraceEntry.result` is a trailing defaulted field, so older positional
  construction remains valid.
- The right-click **Add behavior node** action remains as a compatibility alias for
  the operation selected in the toolbar; **Add operation** is the complete catalogue.
- Manual wheel zoom retains its 5% lower bound. **Fit visible** may use a smaller
  overview scale when necessary, while **Zoom selection** restores an editing scale.

Focused regression evidence is owned by `test_flow_standard.py`,
`test_behavior_runtime.py`, and `test_designer_ui.py`. The final audit must append
the complete-suite, real-editor visual, and post-design simulator evidence rather
than treating focused tests as completion.
