# Picoware App Flow Standard v2

## Purpose

App Flow Standard v2 extends the structural v1 graph with explicit, allowlisted
runtime operations. Version 1 projects remain structural-only and load unchanged.
There is no implicit conversion of JSON properties into executable code.

## Executable node contract

Each executable `FlowNode` adds:

- `operation`: an ID from the shared operation registry;
- `binding`: stable screen, element, and event IDs for UI Event nodes;
- `component_ref`: optional recipe/component identity, version, and fingerprint;
- schema-validated `properties` and operation-specific typed ports.

Display names remain editable. Runtime relationships use stable IDs.

## Safety boundary

The runtime executes a bounded queue. It never evaluates Python expressions,
imports named modules from graph data, or calls methods outside the allowlisted
operation registry. UI, timer, storage, MQTT, and Wi-Fi capabilities are injected
services. Missing services, invalid properties, unknown operations, type errors,
and step-limit breaches stop with explicit diagnostics.

Custom Handler nodes call only stable functions from developer-owned
`behavior_handlers.py`. Generated export may add a reviewed missing stub, but it
never replaces an existing handler body.

## Runtime outcomes and tracing

Operations emit only declared output ports. Success, Error, and Cancel are separate
where the service contract supports them. Timers and asynchronous services resume
through event tokens rather than blocking the Picoware input loop.

Runtime trace records contain order, node ID, input/output port, outcome, bounded
payload summary, and duration. Password-, secret-, and token-like fields are
redacted. Breakpoints pause between nodes.

The editor debugger executes the same bounded operation semantics through
deterministic preview services. Start queues a selected entry without executing it;
Step processes exactly one node; Continue stops at completion or a node breakpoint;
Stop discards queued work. Preview MQTT, Wi-Fi, storage, timer, UI, and handler
services never perform external writes or network/device actions. This debug trace
is evidence of graph semantics, not a substitute for the final simulator run.

## Widget payload and typed outputs

A bound UI Event creates a structured payload from the generated UI's public
surface. It includes stable event, screen, element, and widget identities plus the
current `value`. Text controls add `text`, boolean controls add `checked`, and
selectable controls add `index` when their public widget API exposes it.

The Event port retains the structured payload for compatibility and branching.
UI Event nodes also expose Value, Text, Checked, and Index outputs; a connection is
only traversed when that field exists. Exact property tokens `$payload`, `$value`,
`$text`, `$checked`, `$index`, `$event_id`, `$screen_id`, `$element_id`, and
`$widget_type` resolve incoming data without evaluating expressions. Missing fields
fail explicitly and can use the node's Error output.

## Generated ownership

- `generated_behavior.py` is editor-owned and contains contracts, bindings, the
  bounded dispatcher, trace support, and `TEST_MANIFEST`.
- `behavior_handlers.py` is developer-owned and create-once/additive.
- `generated_ui.py` remains editor-owned presentation and exposes explicit public
  UI mutation methods used by standard operations.

## Compatibility

- Missing `operation`, `binding`, and `component_ref` fields load as structural.
- Flow Standard v1 loads and exports without executable reinterpretation.
- Unknown future standard versions and operation IDs are rejected.
- Moving to v2 occurs through a deliberate editor action such as creating a bound
  behavior or inserting a v2 recipe, followed by the normal reviewed save.
