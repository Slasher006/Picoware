# LM Studio ToolGuard current-time tool overlay

This self-contained plugin source upgrades
`local/toolguard-current-time` so it can be selected by Picoware's LM Studio
MCP provider.

The included prompt preprocessor remains active. The tools provider
exposes `get_current_time`, which returns the host's current local time, UTC
time, IANA time zone, and Unix timestamp whenever the model calls it.

Copy this directory into the installed plugin or use LM Studio's development
install flow, then rebuild its `.lmstudio/production.js` bundle. The
package declares `"type": "module"` because LM Studio's production bundle is
ESM; this prevents Node's `MODULE_TYPELESS_PACKAGE_JSON` reparsing warning.
