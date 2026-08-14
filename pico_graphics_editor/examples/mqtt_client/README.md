# MQTT Client editor example

This is the complete companion project for **Help > MQTT Client Tutorial**.

## Contents

- `MQTT Client.picogui.json`: editable five-screen editor project
- `export/`: complete generated-app structure plus developer MQTT behavior
- `simulator-connect-publish.script`: deterministic connect/publish check
- `simulator-native-navigation.script`: Menu and Inbox navigation check
- `editor-dashboard.png`: App GUI design reference
- `screen-flow.png`: navigation and behavior graph reference
- `simulator-connected-publish.png`: successful Dashboard runtime reference
- `simulator-inbox.png`: received-message TextBox reference
- `WIDGET_FLOW_AUDIT.md`: widget payload, UX, validation, and limitation audit

Use **File > Open > Open MQTT Client Example** instead of opening the bundled JSON
directly. The command creates an unsaved copy and prevents accidental changes to this
reference project.

The editor-generated files are `generated_ui.py`, `generated_assets.py`, and
`generated_assets.pga`. The application behavior in `app.py` and
`mqtt_transport.py` is developer-owned.

The simulator uses deterministic offline loopback. The device transport is an example
MQTT 3.1.1 QoS 0 socket implementation and has not been validated on PicoCalc hardware
or against a live broker as part of this example.
