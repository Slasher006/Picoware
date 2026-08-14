# Build Your First Picoware App: MQTT Client

This tutorial uses the bundled **MQTT Client** to teach the Pico Graphics Editor's
complete design-to-behavior workflow. The example combines custom layers, real
Picoware widgets, a hand-drawn RGB565 asset, App Flow Standard v2 behavior, and a
deterministic offline MQTT transport.

No PicoCalc hardware, credentials, or live broker is required.

## 1. Open a safe copy

1. Start the editor with `./pico-graphics-editor.sh`.
2. Choose **File > Open > Open MQTT Client Example**.
3. The editor opens **MQTT Client (unsaved)** in **App GUI**.
4. Choose **File > Save As** before keeping changes.

The bundled project is never overwritten by the menu action.

## 2. Understand the workspaces

- **App GUI** builds screens, custom layers, and Picoware widgets.
- **Screen Flow** connects navigation and executable behavior.
- **Simulator** runs the completed generated package.
- **Pixel Art** edits RGB565 assets.
- **Asset Library** stores reusable project graphics.

Use `Ctrl+1` through `Ctrl+5` to switch workspaces.

## 3. Inspect the visual design

Select **Dashboard**. It contains custom Rectangle, Label, Progress, and Button
elements plus the inline Picoware Toggle named **Retain Publish**. The other screens
each demonstrate one screen-owning system widget:

- **Broker Settings**: Keyboard
- **Topic Manager**: Menu
- **Message Inbox**: TextBox
- **About**: Alert

A screen-owning widget stays alone on its screen. An inline widget such as Toggle
can share a custom layout.

Select **MQTT Network Mark**, then use **Open Asset in Pixel Editor** to inspect its
16 x 16 transparent RGB565 artwork. The Dashboard places it at 2x scale.

Use **Preview Layout** for appearance checks. This preview does not execute MQTT or
developer handlers.

## 4. Screen Flow versus Behavior Flow

Switch to **Screen Flow**, keep **Visibility: Screens + behavior**, and choose
**Fit visible**. Use **Screens only** or **Behavior only** when one layer becomes
too dense, then choose **Zoom selection** before editing ports.

Navigation relations answer “which screen becomes active?” The example connects the
Dashboard to Broker Settings, Topic Manager, Message Inbox, and About. Relation
**Action** and **Condition** fields must remain empty; unsupported text now blocks
export instead of silently removing a navigation branch.

Behavior nodes answer “what happens after an event?” The MQTT graph contains:

- bound **UI event** nodes for Connect and Publish Test;
- a **Custom handler** for the app-specific connect/disconnect policy;
- built-in **MQTT publish**, **State append**, and **Set status text** operations;
- a built-in inbox update linked to the MQTT inbox recipe.

The Node inspector shows **Handler: Built in**, **Handler: Missing**, or
**Handler: Structural only**. “Missing” means a developer-owned function is still
required; it is not a generator failure.

## 5. Create a bound behavior from a button

1. Return to **App GUI** and select a focusable Button.
2. Expand **Interaction & focus**.
3. Click **Create behavior from this element...**.
4. Choose **Handle widget value** to pass a Menu, Choice, Toggle, Keyboard, Search,
   List, Text Viewer, or applicable custom-widget value to a handler; choose
   **Branch by current value** to create a Compare node; or choose **Run custom
   handler**, **Navigate to screen**, **Set status text**, or **Publish MQTT
   message** for the direct event path.
5. Confirm the dialog.

The editor switches to Screen Flow and atomically creates a bound Event node, the
chosen operation, and their typed connection. It copies stable screen, element, and
event IDs automatically. Renaming the button later does not break the binding.

Dragging an output port into empty graph space opens a compatible operation palette.
Only nodes with a matching input type are offered.

The **Connect** tab offers the same guided contract: select a source node and
output, then choose among only compatible target nodes and inputs. **Connect typed
ports** stays disabled until the connection is complete.

Bound UI Event nodes expose **Value**, **Text**, **Checked**, and **Index** outputs.
For example, connect Keyboard **Text** to an MQTT Publish node and set Payload to
`$text`, or connect Toggle **Checked** and set Retain to `$checked`. A Choice branch
uses **Payload field: value** and compares it with the exact entry text. Toggle List
fields can be separated with **Get payload field**.

## 6. Insert the MQTT Publish recipe

1. Open the **Recipes** inspector tab.
2. Change the source filter from **Personal** to **Built in**.
3. Search for `mqtt publish`.
4. Select **MQTT publish / success / error**.
5. Review its description, version, and Input/Output anchors.
6. Click **Insert fragment**.

The recipe is inserted at the visible viewport center and selected. A second insert
creates independent node and connection IDs. Editing one copy never changes the
bundled recipe or another copy.

## 7. Configure MQTT without JSON

Select an **MQTT publish** node. The Node inspector provides normal controls for:

- **Topic**
- **Payload**
- **Retain**

Use **Advanced properties JSON** only for forward-compatible metadata. Standard
operations require no raw JSON editing, and unknown future fields are preserved.
Passwords and tokens do not belong in graph properties. Reference a settings key
from an appropriate operation instead.

## 8. Review generated behavior safely

Before generation, open **Flow test > Debugger** and select the MQTT Publish node.
Choose **Service succeeds**, enter an optional JSON response, and use **Start** then
**Continue**. Repeat with **Service returns error** and **Service is cancelled** to
prove the connected branches. Trace rows show separate redacted input and output
payloads. For timer flows, run through the Timer node, choose **Fire timer**, then
continue from the queued callback. These editor scenarios are deterministic and do
not contact a broker, Wi-Fi network, filesystem, or Pico device.

Use **Flow test > Preview** only for structural focus and navigation. Finish the
design and resolve every validator error before choosing **Run current design** in
the Device Simulator.

Choose **Project > Validate / Preflight Project**. The bundled project should have
zero errors and only two informational terminal-screen notices for Message Inbox
and About. The developer application owns their Back behavior.

Then choose **File > Export > Export Generated App Structure v1...** and review the
complete patch set. The structure name remains v1; its behavior graph is App Flow
Standard v2.

```text
MQTT Client.py                 developer-owned, create once
mqtt_client/
|-- __init__.py               developer-owned, create once
|-- app.py                    developer-owned, create once
|-- behavior_handlers.py      developer-owned, additive stubs only
|-- generated_behavior.py     editor-owned behavior runtime and manifest
|-- generated_ui.py           editor-owned presentation
|-- generated_assets.py       editor-owned resource reader
`-- generated_assets.pga      editor-owned RGB565 resource
```

The exporter never replaces an existing handler body. If a new Custom Handler node
needs a function, review the additive `behavior_handlers.py` patch. If that file no
longer parses, automatic insertion stops instead of guessing.

The completed example retains its older developer-owned `app.py`, which implements
dynamic payload sequence numbers, broker editing, subscriptions, bounded inbox
history, and the mock/device transport selection. Regeneration preserves it.

## 9. Validate and debug before running

The assistant banner above the graph updates while the project changes. Choose
**Validate flow** for a complete report or **Next issue** to jump directly to the
next affected node. The Issues inspector separates errors, warnings, and
information and includes a suggested fix. Nodes with findings receive a severity
outline and a visible `!` badge.

When connecting ports, compatible inputs enlarge and turn green. An incompatible or
already occupied input turns red, and the assistant explains why the edge cannot be
created. Dropping on empty graph space opens only operations compatible with the
dragged output. For a button or widget row, drag its right-hand output onto another
screen to navigate, or release it on empty space to reopen the searchable action
chooser and create the bound UI Event automatically.

Select a behavior Event node and choose **Debug selected**. In **Runtime trace**:

1. **Start** queues the selected node with its normal widget payload, or with the
   optional JSON payload entered in the debugger.
2. **Step** executes exactly one node.
3. **Continue** runs until completion or a node marked **Pause debugger after this
   node**.
4. Select a trace row to center its node and inspect the redacted payload.
5. **Stop** discards queued work; **Clear Trace** also removes graph highlights.

The editor debugger is deterministic and offline. UI, state, timer, storage, MQTT,
Wi-Fi, and custom-handler calls are recorded without network, device, or file writes.
It proves the declared runtime path, but it does not substitute for the final
simulator run.

## 10. Run only after design is complete

From the repository root:

```bash
mqtt_example_sd=$(mktemp -d /tmp/picoware-mqtt-example.XXXXXX)
micropython simulator/run.py \
  --headless \
  --frames 180 \
  --speed unlimited \
  --network offline \
  --audio silent \
  --sd "$mqtt_example_sd" \
  --sd-profile clean \
  --apps-source pico_graphics_editor/examples/mqtt_client/export \
  --script pico_graphics_editor/examples/mqtt_client/simulator-connect-publish.script \
  --assert-text "RX demo/picoware/test"
```

Expected result: exit status 0. The script opens MQTT Client, connects the mock
transport, publishes one deterministic JSON message, and verifies its loopback text.

For the native Menu and TextBox path, use:

```text
pico_graphics_editor/examples/mqtt_client/simulator-native-navigation.script
```

## 11. Useful editing shortcuts

- Drag empty graph space for marquee selection.
- Use Arrow keys to nudge selected unlocked nodes by one graph unit.
- Use Shift+Arrow for a ten-unit nudge.
- Right-click and choose **Layout selected →** or **Layout selected ↓**.
- Pinned nodes remain fixed during layout.
- Compatible ports turn green and incompatible or occupied ports turn red during a
  connection drag; the assistant banner explains the result.

## 12. What the simulator does not prove

The deterministic tests prove UI layout, navigation, focus, native-widget delegation,
asset streaming, mock connect/publish/receive behavior, and developer-file
preservation. They do not prove PicoCalc timing or memory, Wi-Fi association, DNS,
a live public broker, TLS, authentication, QoS 1/2, retained-message semantics, or
reconnect behavior under packet loss. Those require separate hardware and live
integration testing after the editor design is stable.
