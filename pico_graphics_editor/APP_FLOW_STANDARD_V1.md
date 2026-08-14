# Picoware App Flow Standard v1

## Purpose

App Flow Standard v1 is the editor's quasi-standard representation of application behavior. It describes **what may happen and how parts are connected**, while deliberately leaving the implementation to the developer.

The editor may generate stable records and handler names from the graph. It does not invent business rules, network calls, storage code, game mechanics, or state mutations. A graph can therefore be useful as architecture, validation input, a reusable template, and a generation contract before functional application code exists.

This standard complements [Generated App Structure v1](./GENERATED_APP_STRUCTURE.md). Screen navigation remains compatible with existing `format_version: 8` GUI projects; behavior data is added beside it under `flow_standard_version: 1`.

## Two graph layers

Screen Flow contains two related layers:

1. **Navigation flow** connects screens or focusable screen elements using triggers, optional condition/action names, and transitions.
2. **Behavior flow** connects typed nodes using explicit input and output ports.

Navigation answers “which screen becomes active?” Behavior flow answers “which structural event, decision, action, state, timer, data source, or reusable component participates?” Neither layer executes arbitrary application code in the designer.

## Behavior node kinds

| Kind | Default inputs | Default outputs | Intended contract |
| --- | --- | --- | --- |
| Event | None | `Event: event` | Entry point raised by UI, device, or application input |
| Condition | `Evaluate: event` | `True: event`, `False: event` | Named decision with explicit branches |
| Action | `Run: event` | `Done: event` | Developer-implemented operation |
| State | `Set: data` | `Changed: data` | Named application state or value boundary |
| Timer | `Start: event`, `Stop: event` | `Elapsed: event` | Explicit timing/lifecycle boundary |
| Data | None | `Value: data` | Source or description of data |
| Component | `Invoke: event` | `Done: event` | Reusable subflow or developer-owned component |
| Comment | None | None | Documentation placed directly in the graph |

Every node stores a stable ID, kind, display name, position, description, typed ports, JSON properties, optional group ID, and `locked`, `pinned`, and `breakpoint` flags. Renaming a node does not change its identity or generated handler name suffix.

Properties are open structural metadata. Examples include:

```json
{"default_branch": "true"}
```

for deterministic condition tracing, or:

```json
{"value": "idle"}
```

for a state contract. Properties do not become executable expressions.

## Typed ports and connections

A behavior connection stores:

- Stable connection ID
- Source node ID and output port ID
- Target node ID and input port ID
- Optional label and condition contract
- Optional locked state

Connections must run from `out` to `in`. Port types must match; the special type `any` may connect to any type. A single-input port rejects competing incoming connections unless its contract explicitly permits multiples. The graph validates a connection before accepting it, whether it was created by dragging or through the inspector.

The canvas uses labels and arrowheads in addition to color. Drag an output port onto a compatible input port to connect nodes. Select an edge to reconnect, relabel, condition, delete it, or insert an intermediate Action when it is an event edge.

## Inspector and organization

The Node inspector exposes the selected node's stable ID, kind, name, description, JSON properties, port contract, generated stub name, pin, lock, and breakpoint. It also provides explicit typed-endpoint controls for keyboard-friendly connection creation.

The graph supports:

- Ctrl-click multi-selection, copy/paste, duplicate, and Delete
- Group, ungroup, collapse, and expand
- Horizontal or vertical auto-layout
- Pinned nodes that auto-layout must not move
- Locked nodes and connections that destructive editor actions must preserve
- Align-left and vertical-distribution context actions
- Search, fit-to-view, zoom, middle-button pan, and a clickable minimap

Groups are visual organization only. They do not generate hidden control flow.

## Diagnostics

The Diagnostics tab reports deterministic structural findings. Errors block Generated App Structure export; warnings and information remain visible but do not pretend that incomplete behavior is implemented.

Checks include:

- Missing or duplicate navigation triggers
- Unreachable and terminal screens
- Duplicate node, connection, or port IDs
- Missing node names, port types, groups, or required inputs
- Invalid port directions and incompatible typed connections
- Duplicate node names and orphan action/state/data/component nodes
- Missing true or false condition branches
- Behavior cycles without an explicit Timer boundary

Double-click a diagnostic to locate its screen, behavior node, or connection.

## Structural simulation

Navigation simulation sends named events through screen relations and maintains Back/Forward history. Behavior tracing starts at the selected node and follows one deterministic structural path without calling application code.

- Conditions use the `default_branch` property, defaulting to `true`.
- State nodes display a declared `value` property when present.
- A breakpoint pauses the trace at that node.
- A repeated node is reported as a cycle.
- Visited nodes and edges are highlighted on the canvas.

This is a contract trace, not proof that the eventual application behavior works.

## Personal flow-fragment library

Select one or more behavior nodes and use **Fragments > Save selection as fragment** to store a reusable subflow outside the current project. Internal typed connections and referenced visual groups are included. External connections are intentionally excluded.

Inserting a fragment into another project creates independent node, connection, and group IDs. Later edits or deletion of the library entry do not change copies already inserted into projects. The library is versioned, fingerprinted, validated on read, and written atomically.

This is separate from the Personal Asset Library: the asset library stores reusable graphics; the flow library stores reusable behavior structure.

## Generated contract

`generated_ui.py` exposes the behavior design as editor-owned data:

- `FLOW_STANDARD_VERSION`
- `FLOW_NODES`
- `FLOW_CONNECTIONS`
- `FLOW_GROUPS`
- `behavior_contracts()`
- `dispatch_behavior(node_id, context=None)`

`dispatch_behavior` returns an explicit `implemented: False` result. It is a stable integration boundary, not generated business logic. The developer-owned `app.py` remains responsible for implementing application behavior and may map stable node IDs or generated stub names to real handlers.

Generation is rejected when the project uses an unsupported flow standard or contains error-level diagnostics. Warnings remain documentation for the developer.

## Minimal example

```text
Event: Start pressed
    Event(event) -> Evaluate(event)
Condition: Has saved game?
    True(event)  -> Run(event) Action: Continue game
    False(event) -> Run(event) Action: Open new-game setup
```

The generated result records this structure and stable identities. The developer still writes how a save is detected, how a game is continued, and how setup is opened.

## Compatibility and migration

- Projects without behavior fields load as an empty Flow Standard v1 graph.
- Existing screen and element navigation remains unchanged.
- Current saves include `flow_standard_version: 1` and the three behavior collections.
- Unknown future flow-standard versions are rejected instead of guessed.
- Reusable fragments have their own library format version and cannot silently import damaged or incompatible data.

## Current boundary

Version 1 intentionally does not execute arbitrary expressions, synthesize application logic, infer behavior from prose, or guarantee device-runtime semantics. Those capabilities would require a separately reviewed standard revision rather than hidden interpretation of existing projects.

Flow Standard v1 remains supported as a structural-only project format. Opening a
v1 project does not reinterpret its properties as executable behavior. New
allowlisted execution, stable UI bindings, and operation schemas are defined only
by [App Flow Standard v2](./APP_FLOW_STANDARD_V2.md) and require an explicit v2 save.
