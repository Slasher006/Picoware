# Universal Widget Flow Audit

Date: 2026-08-12
Target: PicoCalc 320x320
Validation: real editor model/Qt surfaces, generated CPython and MicroPython code,
and deterministic offline simulator

## Outcome

All readable native widgets and applicable custom elements now use one behavior
payload contract. A bound UI Event keeps its structured Event output and exposes
Value, Text, Checked, and Index outputs when those fields exist. Existing projects
gain the ports during load without changing their stable binding IDs.

The Screen Flow editor now assists authoring instead of requiring users to infer
valid graph structure. It continuously summarizes flow health, marks affected nodes,
explains rejected connections, navigates findings, and provides a deterministic
node-by-node debugger with breakpoints and redacted payload inspection.

The complete Screen Flow audit also removed silent edge logic, unified operation
creation, added navigation/behavior visibility modes, filtered manual connection
targets, made collapsed groups recoverable, and added success/error/cancel plus
timer scenarios to the debugger. The final compact inspector labels are **Node**,
**Connect**, **Issues**, and **Recipes**.

## Widget coverage

| Element | Value fields | Event behavior |
| --- | --- | --- |
| Menu / Selectable List / Choice | value, text, index | confirmation |
| Toggle | value, checked | state changed |
| Toggle List | value, index, text, checked | selection/state changed |
| Keyboard Input | value, text | input submitted |
| Search and Select | value, text | selection submitted |
| Text Viewer | value, text | readable from another event; no fabricated activation |
| Loading / Alert | none | display/action target only |
| Custom Button / Icon | value, text | activation |
| Custom Label / List | value, text | List has activation; Label is read from another event |
| Custom Progress | value | read/write target |
| Panel / Rectangle | none | visibility/enabled target only |

## UX profile

The common widget paths require no raw JSON and no stable-ID copying:

| Goal | Deliberate choices | Result |
| --- | ---: | --- |
| Handle a widget value | 4 | bound Event, Read Value, Handler, and two edges |
| Branch on current value | 4 | bound Event, configured Compare, and edge |
| Route one typed output | 2 | drag output, select compatible operation |
| Extract Toggle List field | 2 | connect Event and select Get payload field |
| Find and repair a flow issue | 2 | Next issue, apply the suggested fix |
| Inspect one execution step | 3 | Debug selected, Step, select trace row |

The four-choice guided path is: select element, choose **Create behavior from this
element...**, choose the outcome, and confirm. It remains within the prior 3-5
choice target. Standard fields use node forms. Exact tokens such as `$text` and
`$checked` replace handler glue without enabling expressions.

## Validation evidence

- Full editor suite: 321 tests passed in 17.56 seconds.
- Assisted-flow focused runtime, standard, and Qt behavior tests: 76 passed.
- Qt visual QA at 1366x768: 300/696/340 workspace splitter, 680x434 graph
  viewport, all four inspector tabs visible, all three test tabs visible, all 39
  operation/structural creation entries available, and no workspace overflow.
- Generated MQTT project preflight: zero errors and zero warnings; two existing
  informational terminal-screen notices.
- CPython parsing: editor modules and all generated MQTT Python files passed.
- MicroPython parsing: all eight exported MQTT Python files passed `mpy-cross`.
- Deterministic regeneration: zero changed files after the reviewed regeneration.
- Post-design simulator connect/publish/loopback: exit 0 with
  `RX demo/picoware/test`; repeated after the final generated trace update.
- Post-design simulator native Menu/TextBox navigation: exit 0 with
  `demo/picoware/test {"client":"picoware","seq":1}`.

## Remaining limits

- Text Viewer, Label, Progress, Panel, and Rectangle do not invent user events;
  another event must initiate reads or updates.
- Custom List still exposes its text block, not a selected-row model.
- Typed UI Event ports are a common superset. A field that does not exist for the
  current widget emits no typed branch; the structured Event payload remains usable.
- Exact payload references are deliberately not interpolation. Formatting several
  fields into one string still needs a future Format Text node or a Custom Handler.
- The deterministic editor debugger records external-service intent; only the device
  simulator or hardware can prove real service integration.
- The simulator does not prove PicoCalc timing/memory, Wi-Fi, DNS, TLS,
  authentication, QoS 1/2, retained-message behavior against a live broker, or
  reconnect behavior under packet loss.
