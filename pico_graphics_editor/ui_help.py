"""Install concise, semantic description-plus-example help on editor controls."""

from __future__ import annotations

from dataclasses import dataclass
import re

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QSlider,
    QSpinBox,
    QTabWidget,
    QWidget,
)


@dataclass(frozen=True)
class ControlHelp:
    """One useful control explanation and a concrete usage example."""

    description: str
    example: str

    def text(self) -> str:
        """Return the shared two-part tooltip format."""
        return f"{self.description}\nExample: {self.example}"


# Ambiguous and high-impact controls need exact product knowledge. Less ambiguous
# controls use the type-aware fallback below, which includes their actual field,
# range, value, and owning workspace instead of repeating the visible label.
CONTROL_HELP: dict[str, ControlHelp] = {
    "workspace_tabs": ControlHelp(
        "Switches between App GUI, Screen Flow, Simulator, Pixel Art, and Asset Library without discarding work.",
        "Open Screen Flow to wire a button, then return to App GUI to adjust its layout.",
    ),
    "project_name_edit": ControlHelp(
        "Sets the editable GUI project name used in project metadata and generated output.",
        "Enter Weather Panel before generating the application files.",
    ),
    "profile_combo": ControlHelp(
        "Changes the target display and proportionally migrates every existing screen layout in one undoable step.",
        "Switch from PicoCalc to Cardputer, inspect the migrated screens, and use Undo to restore the exact old layout.",
    ),
    "asset_storage_combo": ControlHelp(
        "Chooses whether generated images and audio share one PGA3 resource or remain individually replaceable files.",
        "Choose Individual files when one deployed icon must be replaceable without rebuilding a PGA3 file.",
    ),
    "project_width_spin": ControlHelp(
        "Sets a Custom display width and proportionally migrates placed elements to the new horizontal coordinate space.",
        "Change 320 to 480 to widen every Custom-profile screen without bunching elements at the right edge.",
    ),
    "project_height_spin": ControlHelp(
        "Sets a Custom display height and proportionally migrates placed elements to the new vertical coordinate space.",
        "Change 320 to 240 to shorten every Custom-profile screen while keeping elements inside its lower edge.",
    ),
    "screen_list": ControlHelp(
        "Selects and reorders application screens; its context menu provides screen-specific actions.",
        "Select Settings to edit it, or drag it below Main to reorder the project.",
    ),
    "screen_name_edit": ControlHelp(
        "Renames the active screen without changing its stable generated identifier.",
        "Rename Screen 2 to Settings so flow relations are easier to read.",
    ),
    "screen_background_button": ControlHelp(
        "Chooses the RGB565 background color cleared behind the active screen.",
        "Set a dark blue background before placing white labels on the screen.",
    ),
    "delete_screen_button": ControlHelp(
        "Deletes the selected screen after confirmation and removes relations that reference it; Undo can restore the edit.",
        "Delete an unused Splash screen only after checking its incoming relations.",
    ),
    "start_screen_button": ControlHelp(
        "Makes the selected screen the first screen shown when the generated application starts.",
        "Select Main, then set it as start so testing begins on the home screen.",
    ),
    "element_list": ControlHelp(
        "Selects screen layers and controls their drawing order; Shift selects multiple elements.",
        "Move a Background panel below a Status label so the label remains visible.",
    ),
    "element_name_edit": ControlHelp(
        "Sets the editor and flow name for the selected element; it also supplies the default activation-event name.",
        "Name a button Open Settings before creating its navigation relation.",
    ),
    "element_text_edit": ControlHelp(
        "Sets the text rendered by the selected element; list rows are separated with \\n.",
        "Enter Wi-Fi\\nDisplay\\nAbout to create three list rows.",
    ),
    "kind_combo": ControlHelp(
        "Changes the selected custom-drawn element type and therefore its supported properties and behavior operations.",
        "Change a Rectangle to Button when it must receive keyboard focus and emit an event.",
    ),
    "native_type_combo": ControlHelp(
        "Selects which real Picoware native widget is added to an otherwise empty screen.",
        "Choose Alert for a full-screen message that closes on input.",
    ),
    "native_widget_combo": ControlHelp(
        "Changes the selected native element's Picoware widget implementation and available settings.",
        "Choose Toggle List for several independently switchable settings.",
    ),
    "enabled_check": ControlHelp(
        "Controls whether the selected element accepts simulator and device input; disabled elements can still remain visible.",
        "Disable a Submit button until the required value has been entered.",
    ),
    "focusable_check": ControlHelp(
        "Includes the selected element in keyboard focus traversal and gives it a stable activation target.",
        "Enable this for a custom button that must be reached with the arrow keys.",
    ),
    "visible_check": ControlHelp(
        "Controls the selected element's initial visibility; behavior nodes may show or hide it later.",
        "Start an error label hidden, then show it from a failed-service branch.",
    ),
    "focus_order_spin": ControlHelp(
        "Sets where the element appears in keyboard focus traversal; lower numbers are visited first.",
        "Give Name field order 1 and Save button order 2.",
    ),
    "focus_order_visible_check": ControlHelp(
        "Overlays focus-order numbers on the design canvas without changing generated output.",
        "Enable it while checking that Back follows Submit in keyboard order.",
    ),
    "focus_style_combo": ControlHelp(
        "Selects how keyboard focus is drawn around the element in the generated application.",
        "Choose Corners when a full outline would obscure a small icon.",
    ),
    "focus_thickness_spin": ControlHelp(
        "Sets the focus-indicator line thickness in device pixels.",
        "Use 2 pixels for a visible outline on a 320 x 320 screen.",
    ),
    "focus_padding_spin": ControlHelp(
        "Sets the gap in device pixels between the element and its focus indicator.",
        "Use 2 pixels so the focus outline does not touch the button border.",
    ),
    "event_name_edit": ControlHelp(
        "Sets the readable activation-event name; the generated runtime still routes through its stable event ID.",
        "Enter open_settings for a button that navigates to the Settings screen.",
    ),
    "widget_selected_combo": ControlHelp(
        "Chooses the native widget row or option selected when its screen first opens.",
        "Start a settings menu on Wi-Fi instead of its first row.",
    ),
    "widget_state_check": ControlHelp(
        "Sets the selected Toggle or Choice widget's initial state.",
        "Enable On when a new Sound setting should initially be active.",
    ),
    "widget_item_states_list": ControlHelp(
        "Sets the initial On or Off state of every row in a native Toggle List.",
        "Select Bluetooth and mark it On while leaving Airplane mode Off.",
    ),
    "asset_call_edit": ControlHelp(
        "Stores an optional source graphics-function reference for imported compatibility; new portable assets normally use an asset ID instead.",
        "Keep icon_wifi only when the element remains linked to that source function.",
    ),
    "refresh_pixel_asset_button": ControlHelp(
        "Reloads the selected linked asset from its source while preserving the placement.",
        "Refresh a linked battery icon after editing its Python source.",
    ),
    "refresh_all_pixel_assets_button": ControlHelp(
        "Reloads every linked screen asset whose source changed; detached snapshots are not modified.",
        "Refresh all after updating several icons in graphics.py.",
    ),
    "detach_pixel_asset_button": ControlHelp(
        "Converts the selected linked asset into an independent project snapshot that no longer follows source changes.",
        "Detach a logo before customizing it only for this application.",
    ),
    "relink_pixel_asset_button": ControlHelp(
        "Chooses a replacement source for a missing or outdated linked asset.",
        "Relink icon_home after moving graphics.py to another folder.",
    ),
    "asset_natural_size_button": ControlHelp(
        "Restores the selected placement to the source asset's device-safe dimensions.",
        "Restore a distorted icon to its original 16 x 16 pixels.",
    ),
    "asset_bake_size_button": ControlHelp(
        "Creates an independent nearest-neighbor asset at the placement's current size without changing the library original.",
        "Bake a 16 x 16 icon at 32 x 32 before generation.",
    ),
    "source_combo": ControlHelp(
        "Selects the screen or element that emits a navigation relation's event.",
        "Choose Main · Settings Button as the source of a Settings transition.",
    ),
    "target_combo": ControlHelp(
        "Selects the screen, and optionally initial element, reached by a navigation relation.",
        "Choose Settings · Wi-Fi so navigation opens Settings with Wi-Fi focused.",
    ),
    "trigger_edit": ControlHelp(
        "Shows the readable event used by a screen-level relation; element relations derive it from their selected source.",
        "Use Back only for a relation driven by the standard Back event.",
    ),
    "transition_combo": ControlHelp(
        "Records how navigation changes the current screen in generated flow metadata.",
        "Use replace for ordinary movement from Main to Settings.",
    ),
    "condition_edit": ControlHelp(
        "Displays a legacy relation condition that generation cannot execute; move the logic to a typed Condition node.",
        "Replace checked == true with a Condition node and True-port connection.",
    ),
    "action_edit": ControlHelp(
        "Displays a legacy relation action that generation cannot execute; move the work to a typed Action node.",
        "Replace show alert with a UI Alert action node.",
    ),
    "clear_navigation_logic_button": ControlHelp(
        "Clears unsupported legacy Condition and Action text from the selected navigation relation; it does not create replacement behavior.",
        "Create equivalent typed nodes first, then clear the legacy fields.",
    ),
    "connection_list": ControlHelp(
        "Lists structural screen-navigation relations and selects one for inspection or editing.",
        "Select Main -- Open Settings --> Settings to change its destination.",
    ),
    "behavior_connection_list": ControlHelp(
        "Lists typed behavior-node connections, including their exact source and target ports.",
        "Select Event.event -> Alert.in before reconnecting it.",
    ),
    "behavior_operation_combo": ControlHelp(
        "Selects the typed operation executed by the current behavior node and rebuilds its property editor.",
        "Choose UI · Show alert for an action node that displays a message.",
    ),
    "behavior_kind_combo": ControlHelp(
        "Changes the selected node category and its available input and output ports; incompatible edges are reviewed first.",
        "Change an Action node to Condition only after reviewing connections that would be removed.",
    ),
    "behavior_breakpoint_check": ControlHelp(
        "Pauses the structural debugger immediately before this node executes.",
        "Set a breakpoint on Navigate to inspect the event payload first.",
    ),
    "behavior_pinned_check": ControlHelp(
        "Keeps this behavior node fixed when automatic graph layout runs.",
        "Pin the main UI event node at the left edge before auto-layout.",
    ),
    "behavior_locked_check": ControlHelp(
        "Prevents editing and movement of this behavior node until unlocked.",
        "Lock a reviewed service-call node to avoid accidental changes.",
    ),
    "flow_diagnostic_filter": ControlHelp(
        "Filters the findings list by severity without changing the project or hiding problems from generation.",
        "Choose Errors to focus on issues that block export.",
    ),
    "flow_diagnostics_list": ControlHelp(
        "Lists validation findings for navigation, behavior, assets, and generated-runtime compatibility.",
        "Select an unreachable event finding, then use Go to issue.",
    ),
    "runtime_outcome_combo": ControlHelp(
        "Chooses the simulated result returned by service operations during structural debugging.",
        "Choose Service fails to exercise the error branch without making a network request.",
    ),
    "runtime_service_response_edit": ControlHelp(
        "Provides optional JSON returned by the simulated service or timer during structural debugging.",
        "Enter {\"connected\": true} to test a condition reading connected.",
    ),
    "simulator_event_edit": ControlHelp(
        "Enters a stable or readable event for the structural flow simulator; this does not send a physical key.",
        "Enter open_settings to test the declared navigation path.",
    ),
    "preview_mode_combo": ControlHelp(
        "Chooses safe layout preview, the real device simulator, or a side-by-side comparison.",
        "Use Compare when checking whether runtime rendering matches the design.",
    ),
    "live_target_kind_combo": ControlHelp(
        "Chooses whether the isolated simulator launches the unsaved current design or an imported app, game, or library.",
        "Keep Current design while testing unsaved screen-flow changes.",
    ),
    "live_target_edit": ControlHelp(
        "Names the imported application, game, or library launched when Current design is not selected.",
        "Enter Pico Bomber after choosing Game.",
    ),
    "live_board_combo": ControlHelp(
        "Selects the simulated Picoware board, display size, and input mapping.",
        "Choose picocalc-pico2w to reproduce PicoCalc keyboard behavior.",
    ),
    "live_auto_reload_check": ControlHelp(
        "Restarts an imported-source simulation when watched Python files change; unsaved current-design runs are rebuilt manually.",
        "Enable it while editing an imported app outside the GUI editor.",
    ),
    "capture_screen_combo": ControlHelp(
        "Chooses which design screen owns the next captured runtime framebuffer for Compare view.",
        "Choose Settings before capturing the running Settings screen.",
    ),
    "start_live_button": ControlHelp(
        "Builds the current in-memory GUI project and starts it in the isolated Picoware simulator, including unsaved changes.",
        "Change an Alert message, then run immediately without saving the project.",
    ),
    "restart_live_button": ControlHelp(
        "Stops and rebuilds the active simulator target with the same launch settings.",
        "Restart after changing a generated input route.",
    ),
    "stop_live_button": ControlHelp(
        "Stops only the isolated simulator process; the editor project and captured frame remain available.",
        "Stop after finishing a keyboard-navigation test.",
    ),
    "launch_selected_button": ControlHelp(
        "Starts the selected imported app, game, or library with the chosen board settings.",
        "Choose Game, enter Pico Bomber, then run the selected target.",
    ),
    "capture_live_button": ControlHelp(
        "Stores the current runtime framebuffer against the selected design screen for comparison.",
        "Capture Settings after navigating there in the simulator.",
    ),
    "clear_live_capture_button": ControlHelp(
        "Removes the stored comparison frame for the selected screen without changing project elements.",
        "Clear an outdated Main capture before recording a new runtime frame.",
    ),
    "grid_check": ControlHelp(
        "Shows or hides the Pixel Art grid without changing image pixels.",
        "Enable the grid while editing a 16 x 16 icon at high zoom.",
    ),
    "grid_visible_check": ControlHelp(
        "Shows or hides the App GUI placement grid without changing element positions.",
        "Show the grid while aligning several buttons.",
    ),
    "snap_check": ControlHelp(
        "Snaps moved and resized App GUI elements to the configured grid spacing.",
        "Enable Snap with an 8-pixel grid to align button edges.",
    ),
    "grid_size_spin": ControlHelp(
        "Sets App GUI snap and grid spacing in device pixels.",
        "Use 8 pixels for a compact PicoCalc layout rhythm.",
    ),
    "zoom_spin": ControlHelp(
        "Sets editor magnification only; generated dimensions and pixels are unchanged.",
        "Use 400% to edit individual pixels in a small icon.",
    ),
    "reference_opacity_slider": ControlHelp(
        "Controls reference-image transparency over the pixel canvas without modifying pixels.",
        "Use about 45% to trace an icon while keeping drawn pixels visible.",
    ),
    "reference_opacity_spin": ControlHelp(
        "Sets reference-image transparency over the App GUI canvas without affecting generated output.",
        "Use 40% while matching a supplied screen mockup.",
    ),
    "reference_fit_combo": ControlHelp(
        "Chooses how the reference image is fitted into the canvas before offset, scale, rotation, and flips.",
        "Choose Contain to keep the entire reference visible without cropping.",
    ),
    "reference_dither_check": ControlHelp(
        "Applies Floyd-Steinberg error diffusion when converting the reference to the limited RGB565 palette.",
        "Enable it for a photograph with gradients; leave it off for crisp flat-color icons.",
    ),
    "reference_foreground_check": ControlHelp(
        "Draws the reference overlay above editable pixels instead of behind them; it does not change exported art.",
        "Enable it briefly to compare edge alignment, then disable it for painting.",
    ),
    "onion_skin_check": ControlHelp(
        "Overlays the previous animation frame while editing the current frame without storing the overlay.",
        "Enable it when shifting a walking sprite by one pixel per frame.",
    ),
    "frame_interval_spin": ControlHelp(
        "Sets the current animation frame duration in milliseconds.",
        "Use 100 ms for a ten-frames-per-second animation.",
    ),
    "colors_spin": ControlHelp(
        "Limits colors during image-to-RGB565 conversion; fewer colors simplify art but lose detail.",
        "Use 16 colors for a compact retro icon preview.",
    ),
    "dither_check": ControlHelp(
        "Uses Floyd-Steinberg dithering during library conversion to approximate gradients with the selected palette.",
        "Enable it for a shaded photo and disable it for a flat logo.",
    ),
    "timing_mode_combo": ControlHelp(
        "Chooses whether an imported animation keeps per-frame timing or uses one uniform interval.",
        "Keep original timing for a GIF whose pause frame is intentionally longer.",
    ),
    "interval_spin": ControlHelp(
        "Sets the uniform duration assigned to every imported animation frame.",
        "Use 250 ms per frame for a four-frame-per-second animation.",
    ),
    "collection_combo": ControlHelp(
        "Filters the Asset Library between personal, built-in, and combined collections without deleting anything.",
        "Choose Personal before renaming an asset you created.",
    ),
    "display_mode_combo": ControlHelp(
        "Changes Asset Library thumbnail size and information density without modifying assets.",
        "Choose Large thumbnails when comparing similar icons visually.",
    ),
    "delete_button": ControlHelp(
        "Deletes the selected personal asset after confirmation; built-in assets cannot be removed.",
        "Delete an obsolete personal draft only after checking which projects use copies of it.",
    ),
    "clear_canvas_button": ControlHelp(
        "Clears editable pixels after confirmation; the operation is available to Undo and does not delete the library asset.",
        "Clear a failed sketch, then use Undo if the wrong canvas was selected.",
    ),
}

# Action controls whose labels alone do not convey scope, side effects, or the
# expected setup. Keeping these in one catalogue makes reviews and tests much
# easier than scattering strings through signal wiring.
CONTROL_HELP.update(
    {
        "apply_button": ControlHelp(
            "Reviews the exact pending Pixel Art destination change before writing it.",
            "Inspect the Python diff or asset update summary, then apply only if the destination is correct.",
        ),
        "add_screen_button": ControlHelp(
            "Adds a blank design-owned screen to the current GUI project and selects it for editing.",
            "Add a Settings screen, rename it, then connect it from Main in Screen Flow.",
        ),
        "duplicate_screen_button": ControlHelp(
            "Copies the selected screen and its elements with new stable IDs; navigation relations are not duplicated implicitly.",
            "Duplicate a settings page as a starting point for an Advanced page.",
        ),
        "duplicate_element_button": ControlHelp(
            "Copies the selected element on the same screen with a new stable element and event identity.",
            "Duplicate one menu button, then rename and reposition the copy.",
        ),
        "delete_element_button": ControlHelp(
            "Removes the selected screen element and relations or behavior bindings that can no longer target it; Undo restores the edit.",
            "Delete an obsolete button after checking its Screen Flow connections.",
        ),
        "lock_element_button": ControlHelp(
            "Locks or unlocks the selected element against accidental canvas movement and property editing.",
            "Lock a full-screen background before arranging controls above it.",
        ),
        "visibility_element_button": ControlHelp(
            "Toggles the selected element's initial visibility without deleting it.",
            "Hide a debug label while preserving it for a later UI Show action.",
        ),
        "fit_canvas_button": ControlHelp(
            "Fits the complete device screen into the available App GUI canvas area; element geometry is unchanged.",
            "Fit a 320 x 320 screen after narrowing the properties panel.",
        ),
        "design_preview_button": ControlHelp(
            "Opens a safe design preview for layout, focus, and declared navigation without running application services.",
            "Preview keyboard focus order before starting the device simulator.",
        ),
        "preview_button": ControlHelp(
            "Runs the current in-memory GUI project in the Device Simulator, including unsaved layout and flow edits.",
            "Change a button label and run it immediately to verify device rendering.",
        ),
        "add_native_widget_button": ControlHelp(
            "Adds the selected real Picoware widget; full-screen widgets require an otherwise empty screen.",
            "Choose Alert, add it to a new screen, then connect its acknowledgement route.",
        ),
        "empty_custom_layout_button": ControlHelp(
            "Keeps the empty screen as a custom-drawn layout so ordinary elements can be placed on it.",
            "Build a custom layout before adding a label and two buttons.",
        ),
        "open_flow_button": ControlHelp(
            "Opens Screen Flow with the selected element prepared as an event source.",
            "Select Open Settings, then create its relation to the Settings screen.",
        ),
        "bring_front_button": ControlHelp(
            "Moves selected elements above every other layer on the active screen.",
            "Bring a status label in front of an overlapping panel.",
        ),
        "move_forward_button": ControlHelp(
            "Moves selected elements one drawing layer toward the front.",
            "Move an icon forward once so it appears above its card background.",
        ),
        "move_backward_button": ControlHelp(
            "Moves selected elements one drawing layer toward the back.",
            "Move a highlight behind the button text without sending it behind the page background.",
        ),
        "send_back_button": ControlHelp(
            "Moves selected elements behind every other layer on the active screen.",
            "Send a full-screen background image behind all controls.",
        ),
        "library_asset_search": ControlHelp(
            "Filters reusable built-in and personal assets as you type; it never changes or deletes assets.",
            "Enter battery to show only matching battery icons.",
        ),
        "pixel_asset_search": ControlHelp(
            "Filters source-linked graphics available to the current GUI project without changing placements.",
            "Enter wifi to find a discovered Wi-Fi icon function.",
        ),
        "search_edit": ControlHelp(
            "Filters the current asset or graphics list as you type without modifying stored items.",
            "Enter badge to narrow the list to matching assets.",
        ),
        "flow_fragment_search": ControlHelp(
            "Filters reusable flow recipes and personal fragments without modifying the library.",
            "Enter alert to find fragments that display or acknowledge an alert.",
        ),
        "flow_search_combo": ControlHelp(
            "Chooses the graph search result that should be selected and centered.",
            "Choose Screen · Settings to jump to its node.",
        ),
        "add_relation_button": ControlHelp(
            "Creates a structural screen-navigation relation from the selected source event to the selected destination.",
            "Connect Main · Settings Button to Screen · Settings.",
        ),
        "update_relation_button": ControlHelp(
            "Applies the edited endpoints and transition to the selected navigation relation while preserving its stable identity.",
            "Retarget an Open Settings relation from the old screen to the replacement Settings screen.",
        ),
        "delete_relation_button": ControlHelp(
            "Removes the selected structural navigation relation; the source and target screens remain intact and Undo restores it.",
            "Remove a duplicate Back route after confirming another valid exit remains.",
        ),
        "add_behavior_connection_button": ControlHelp(
            "Connects the selected compatible output port to the selected input port; incompatible types and occupied single-input ports are rejected.",
            "Connect UI Event.event to Show Alert.in.",
        ),
        "update_behavior_connection_button": ControlHelp(
            "Reconnects the selected behavior edge to the chosen typed ports without creating a duplicate edge.",
            "Move a condition's True branch from one navigation action to another.",
        ),
        "delete_behavior_connection_button": ControlHelp(
            "Removes only the selected behavior edge; its source and target nodes remain and Undo can restore the edge.",
            "Delete an incorrect Event.event -> Timer.stop edge before reconnecting it.",
        ),
        "add_behavior_button": ControlHelp(
            "Adds the operation selected in the behavior toolbar as a new typed node on the graph.",
            "Select UI · Show alert, then add the operation and connect its input.",
        ),
        "apply_behavior_button": ControlHelp(
            "Applies the selected node's name, operation, properties, pin, lock, and breakpoint settings.",
            "Change an Alert message, then apply the node before debugging the event.",
        ),
        "duplicate_behavior_button": ControlHelp(
            "Copies selected behavior nodes with new stable IDs; external connections are not silently duplicated.",
            "Duplicate a Navigate action, then choose a different destination screen.",
        ),
        "delete_behavior_button": ControlHelp(
            "Removes selected behavior nodes and their attached edges; Undo restores the graph edit.",
            "Delete an unused service node after checking both its success and error branches.",
        ),
        "group_behavior_button": ControlHelp(
            "Places selected behavior nodes in a visual group without changing execution order.",
            "Group the Wi-Fi request and its success/error handlers as Network setup.",
        ),
        "ungroup_behavior_button": ControlHelp(
            "Removes selected nodes from their visual group without deleting nodes or edges.",
            "Ungroup one shared navigation node before moving it elsewhere.",
        ),
        "collapse_behavior_group_button": ControlHelp(
            "Collapses or expands the selected visual group; hidden member nodes still execute normally.",
            "Collapse a reviewed network branch to make the main flow easier to inspect.",
        ),
        "validate_flow_button": ControlHelp(
            "Rechecks navigation, typed behavior, asset references, and generated-runtime compatibility without changing the project.",
            "Validate after reconnecting an Alert exit and resolve every error before export.",
        ),
        "next_issue_button": ControlHelp(
            "Selects and centers the next validation finding that passes the current severity filter.",
            "Filter to Errors, then step through each generation blocker.",
        ),
        "go_to_diagnostic_button": ControlHelp(
            "Opens and selects the screen, element, node, or connection referenced by the current finding.",
            "Jump from an unreachable-event finding to its broken relation.",
        ),
        "auto_layout_button": ControlHelp(
            "Repositions unpinned graph nodes for readability without changing behavior or screen navigation.",
            "Pin the entry event, then auto-layout the remaining nodes.",
        ),
        "fit_graph_button": ControlHelp(
            "Fits all currently visible graph nodes into the Screen Flow viewport without moving them.",
            "Fit the graph after expanding a large behavior group.",
        ),
        "fit_selection_button": ControlHelp(
            "Centers and zooms the graph around the current selection without changing node positions.",
            "Select an error branch, then zoom to that selection.",
        ),
        "debug_selected_button": ControlHelp(
            "Starts structural debugging from the selected event or node using simulated services rather than external calls.",
            "Select a UI Event node and debug it with Service fails selected.",
        ),
        "runtime_start_button": ControlHelp(
            "Starts a fresh structural debug run from the selected entry and clears prior execution state.",
            "Start at Open Settings to inspect its complete branch.",
        ),
        "runtime_step_button": ControlHelp(
            "Executes exactly the next queued behavior node, then pauses again.",
            "Step once to inspect the payload leaving a Condition node.",
        ),
        "runtime_continue_button": ControlHelp(
            "Continues structural execution until completion or the next enabled breakpoint.",
            "Continue after inspecting a service result at a breakpoint.",
        ),
        "runtime_stop_button": ControlHelp(
            "Stops the structural debug session without modifying the behavior graph.",
            "Stop a paused run before choosing a different entry node.",
        ),
        "runtime_fire_timer_button": ControlHelp(
            "Fires the selected pending simulated timer immediately instead of waiting for wall-clock time.",
            "Fire refresh_timer to test its elapsed branch.",
        ),
        "runtime_clear_button": ControlHelp(
            "Clears the structural debugger trace while leaving nodes, breakpoints, and project data unchanged.",
            "Clear the trace before comparing a success run with a failure run.",
        ),
        "save_flow_fragment_button": ControlHelp(
            "Saves the selected behavior subgraph as a reusable personal fragment without removing it from this project.",
            "Save the reviewed confirmation branch as Confirm and navigate.",
        ),
        "insert_flow_fragment_button": ControlHelp(
            "Inserts a copy of the selected recipe or personal fragment with new project-local node IDs.",
            "Insert an Alert acknowledgement recipe near the selected screen.",
        ),
        "delete_flow_fragment_button": ControlHelp(
            "Deletes the selected personal flow fragment from the library; built-in recipes remain read-only.",
            "Delete an obsolete personal draft after inserting any copy still needed by the project.",
        ),
        "copy_id_button": ControlHelp(
            "Copies the selected asset's stable library ID to the system clipboard.",
            "Copy the ID when documenting which asset a generated screen uses.",
        ),
        "copy_fingerprint_button": ControlHelp(
            "Copies the selected asset's content fingerprint to the system clipboard for exact revision comparison.",
            "Compare fingerprints before and after replacing a library image.",
        ),
        "copy_path_button": ControlHelp(
            "Copies the personal library storage path to the system clipboard.",
            "Copy the path when creating a backup of the asset catalogue.",
        ),
        "import_button": ControlHelp(
            "Opens an image for reviewed RGB565 conversion before adding it to the Personal Asset Library.",
            "Import a PNG, compare source and stored previews, then confirm the asset.",
        ),
        "replace_button": ControlHelp(
            "Reviews a new image conversion before replacing the selected personal asset while keeping its stable ID.",
            "Replace a logo only after checking every animation frame in the stored preview.",
        ),
        "duplicate_button": ControlHelp(
            "Creates an independent personal copy of the selected asset with a new stable ID.",
            "Duplicate a built-in Home icon before editing its pixels.",
        ),
        "rename_button": ControlHelp(
            "Renames the selected personal asset; its stable ID and pixels remain unchanged.",
            "Rename Imported Image to Warning Badge for easier searching.",
        ),
        "export_button": ControlHelp(
            "Exports the selected asset frames as PNG files without changing the library copy.",
            "Export an animated icon to numbered PNG frames for external review.",
        ),
        "close_source_button": ControlHelp(
            "Closes the current Python graphics source view after handling any unsaved Pixel Art changes; it does not delete the source file.",
            "Close graphics.py when finished, choosing the appropriate save response if prompted.",
        ),
        "empty_new_button": ControlHelp(
            "Creates a new unsaved Pixel Art canvas with reviewed dimensions and source mode.",
            "Create a blank 32 x 32 Badge before choosing a save destination.",
        ),
        "new_graphic_button": ControlHelp(
            "Creates a new editable Pixel Art asset from a blank canvas, current pixels, reference, or animation source.",
            "Create an asset from the current reference after aligning and scaling it.",
        ),
        "place_in_gui_button": ControlHelp(
            "Places an independent copy of the current Pixel Art asset on the active App GUI screen.",
            "Place a finished Warning Badge on the selected Alert screen.",
        ),
        "use_in_gui_button": ControlHelp(
            "Switches to App GUI and selects the screen element linked to the current project asset when one exists.",
            "Select the icon's placement in App GUI after updating its pixels.",
        ),
        "empty_starter_button": ControlHelp(
            "Opens workflow starters that create a small set of screens and declared navigation for further editing.",
            "Choose Confirm action to begin with a question and acknowledgement flow.",
        ),
        "edit_pixel_asset_button": ControlHelp(
            "Opens the selected linked source or project snapshot in Pixel Art with its correct reviewed write-back path.",
            "Edit a detached icon, then update the project asset and save the GUI project.",
        ),
        "focus_color_button": ControlHelp(
            "Chooses the RGB565 color used by the selected element's keyboard-focus indicator.",
            "Use yellow focus corners on a dark screen for strong contrast.",
        ),
        "fill_color_button": ControlHelp(
            "Chooses the selected element's RGB565 fill or native-widget background color.",
            "Set a button fill to dark blue before choosing white text.",
        ),
        "border_color_button": ControlHelp(
            "Chooses the selected element's RGB565 border or native-widget accent color where supported.",
            "Use white for a high-contrast panel outline.",
        ),
        "text_color_button": ControlHelp(
            "Chooses the selected element's RGB565 text or foreground color.",
            "Use white text over a dark button fill.",
        ),
        "reset_simulator_button": ControlHelp(
            "Resets structural navigation simulation to the project's declared start screen and clears its history.",
            "Reset after testing a deep Back path, then replay it from Main.",
        ),
        "refresh_diagnostics_button": ControlHelp(
            "Runs validation again against the current in-memory flow without modifying nodes or relations.",
            "Validate now after repairing an unreachable Alert connection.",
        ),
        "rename_flow_fragment_button": ControlHelp(
            "Renames the selected personal flow fragment without changing its stored nodes or inserting it into the project.",
            "Rename Draft 1 to Confirm and navigate for easier reuse.",
        ),
        "show_details_button": ControlHelp(
            "Opens the full simulator status or error details while preserving the current run state.",
            "Show details after a launch error before deciding whether to restart.",
        ),
        "error_restart_button": ControlHelp(
            "Rebuilds and restarts the failed simulator target with the same board and launch settings.",
            "Review the error first, fix the project, then restart the target.",
        ),
        "refresh_button": ControlHelp(
            "Reloads the Personal Asset Library catalogue from storage without altering asset content.",
            "Refresh after another editor instance adds a personal asset.",
        ),
        "retry_button": ControlHelp(
            "Retries loading the Personal Asset Library after the displayed storage error.",
            "Correct the library path or permissions, then retry loading.",
        ),
        "edit_copy_button": ControlHelp(
            "Opens an editable personal copy of the selected asset in Pixel Art; built-in originals remain unchanged.",
            "Edit a copy of the built-in Home icon, then save it under a personal name.",
        ),
        "behavior_name_edit": ControlHelp(
            "Sets the readable behavior-node name shown in the graph; the stable node ID and operation remain separate.",
            "Rename Action 3 to Show connection error before wiring its input.",
        ),
        "behavior_connection_label_edit": ControlHelp(
            "Adds an optional readable label to the selected behavior edge without changing its typed ports or execution.",
            "Label a True-branch edge connected to the success screen as Connected.",
        ),
        "behavior_connection_condition_edit": ControlHelp(
            "Shows an unsupported legacy edge condition; generated execution requires an explicit typed Condition node instead.",
            "Create a Compare condition, reconnect its True and False ports, then clear this legacy text.",
        ),
        "frame_combo": ControlHelp(
            "Selects the animation frame displayed for inspection or editing; choosing a frame does not change its pixels or duration.",
            "Choose Frame 2 to inspect it before changing its timing.",
        ),
        "reference_rotation_combo": ControlHelp(
            "Rotates only the reference overlay in 90-degree steps; editable pixels remain unchanged until conversion.",
            "Choose 90 degrees when a portrait reference was stored sideways.",
        ),
        "reference_flip_horizontal": ControlHelp(
            "Mirrors only the reference overlay horizontally without changing editable pixels.",
            "Enable it to trace a left-facing version of a right-facing sprite.",
        ),
        "reference_flip_vertical": ControlHelp(
            "Mirrors only the reference overlay vertically without changing editable pixels.",
            "Enable it to align an upside-down scanned reference before tracing.",
        ),
        "pixel_asset_state_filter": ControlHelp(
            "Filters source assets by current, changed, missing, detached, or draft link state without modifying them.",
            "Choose Missing to find placements that need relinking.",
        ),
        "add_behavior_kind_combo": ControlHelp(
            "Chooses the structural behavior-node kind added when no concrete operation is required.",
            "Choose Comment to document a branch without adding executable behavior.",
        ),
        "add_behavior_operation_combo": ControlHelp(
            "Searches and selects an allowlisted executable operation for the next behavior node.",
            "Choose UI · Show alert before clicking Add operation.",
        ),
        "flow_visibility_combo": ControlHelp(
            "Controls whether the graph shows screens, behavior nodes, or both; hidden nodes still remain in the project.",
            "Choose Screens + behavior while checking an event-to-navigation path.",
        ),
        "layout_direction_combo": ControlHelp(
            "Chooses the direction used by automatic graph layout without changing execution order.",
            "Choose Layout right for a left-to-right event flow.",
        ),
        "behavior_source_node_combo": ControlHelp(
            "Selects the behavior node that emits the new or edited typed connection.",
            "Choose UI Event as the source before selecting its Event output port.",
        ),
        "behavior_source_port_combo": ControlHelp(
            "Selects the typed output port that emits the behavior connection.",
            "Choose True on a Condition node for its matching branch.",
        ),
        "behavior_target_node_combo": ControlHelp(
            "Selects the behavior node that receives the new or edited typed connection.",
            "Choose Show Alert as the target of a failed-service branch.",
        ),
        "behavior_target_port_combo": ControlHelp(
            "Selects the compatible typed input port that receives the behavior connection.",
            "Choose In on an Action node after selecting the source Event port.",
        ),
        "flow_fragment_source_combo": ControlHelp(
            "Filters the fragment browser between built-in recipes and writable personal fragments.",
            "Choose Personal before renaming or deleting one of your saved fragments.",
        ),
        "asset_list": ControlHelp(
            "Selects an asset and updates its preview, frames, metadata, and available actions without modifying it.",
            "Select Warning Badge and inspect every frame before editing or exporting it.",
        ),
        "simulator_history_list": ControlHelp(
            "Lists visited structural-simulation states and lets Back or Forward revisit them without editing Screen Flow.",
            "Select the Settings history entry to inspect which event reached it.",
        ),
        "runtime_trace_list": ControlHelp(
            "Shows each executed behavior node, port, and payload in order during structural debugging.",
            "Select the Compare entry to inspect the value that chose its True branch.",
        ),
        "flow_fragment_list": ControlHelp(
            "Selects a built-in recipe or personal flow fragment for preview, insertion, or permitted library management.",
            "Select Alert acknowledgement and review its nodes before inserting it.",
        ),
    }
)


ACTION_HELP: dict[str, ControlHelp] = {
    "Undo": ControlHelp("Reverts the most recent reversible edit in the active workspace.", "Undo an accidental screen-element move."),
    "Redo": ControlHelp("Reapplies the most recently undone edit in the active workspace.", "Redo the element move after checking its result."),
    "Save GUI Project": ControlHelp("Writes the complete editable GUI project, including screens, assets, and flow metadata.", "Save before closing after repairing an Alert relation."),
    "Delete Screen": CONTROL_HELP["delete_screen_button"],
    "Run current design": CONTROL_HELP["start_live_button"],
    "Export Generated App": ControlHelp("Reviews and writes the generated multi-file Picoware application without overwriting unmanaged code silently.", "Export after validation reports no blocking errors."),
    "Clear canvas": CONTROL_HELP["clear_canvas_button"],
}


_OLD_GENERIC_PREFIXES = (
    "Use ",
    "Enter ",
    "Choose one available option",
    "Set the numeric value",
    "Select an item; right-click",
    "Adjust this value by moving",
    "Run this action for the active workspace",
)
_OLD_GENERIC_EXAMPLES = (
    "Click ",
    "Enter Status Badge",
    "Choose the first option",
    "Set the value, then review the canvas",
    "Select one item and right-click it",
    "Move the handle and inspect the preview",
    "Choose App GUI to arrange a screen",
    "Choose this action from a menu or toolbar",
)


def set_collapsible_group_expanded(group, expanded: bool) -> None:
    """Fully hide or show a checkable group's contents without clipped slivers."""
    layout = group.layout()
    if layout is not None:
        _set_layout_visible(layout, expanded)
    group.setFlat(not expanded)
    group.setStyleSheet(
        ""
        if expanded
        else (
            "QGroupBox { border: 0; margin-top: 0; padding: 0; } "
            "QGroupBox::title { subcontrol-origin: margin; "
            "subcontrol-position: top left; padding: 0 2px; }"
        )
    )
    group.setMaximumHeight(16777215 if expanded else group.fontMetrics().height() + 6)


def _set_layout_visible(layout, visible: bool) -> None:
    """Recursively apply visibility to widgets owned by one layout."""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.setVisible(visible)
        elif child_layout is not None:
            _set_layout_visible(child_layout, visible)


INTERACTIVE_WIDGETS = (
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QSlider,
    QTabWidget,
)


def install_widget_tooltips(root: QWidget) -> None:
    """Install semantic help on application controls, excluding Qt internals."""
    named: dict[QWidget, str] = {}
    for name, value in vars(root).items():
        _collect_named_controls(named, name, value)

    # Explicitly assigned controls carry product meaning. Local dialog buttons
    # are also included because their visible role is unambiguous. Spin-box line
    # edits, tab scrollers, menu overflow buttons, and other Qt implementation
    # children are deliberately excluded.
    controls: list[tuple[QWidget, str]] = list(named.items())
    for widget in root.findChildren(QAbstractButton):
        if widget in named or _is_qt_internal(widget):
            continue
        label = _clean_label(widget.text())
        if label:
            controls.append((widget, _key_from_label(label)))

    seen: set[int] = set()
    for widget, name in controls:
        identity = id(widget)
        if identity in seen:
            continue
        seen.add(identity)
        set_widget_tooltip(widget, name, root)


def _collect_named_controls(
    controls: dict[QWidget, str], name: str, value: object
) -> None:
    """Collect explicitly owned controls, including dynamic control mappings."""
    if isinstance(value, INTERACTIVE_WIDGETS):
        controls[value] = name
        if not value.objectName():
            value.setObjectName(name)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _collect_named_controls(controls, f"{name}_{key}", child)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _collect_named_controls(controls, f"{name}_{index}", child)


def set_widget_tooltip(
    widget: QWidget,
    name: str,
    root: QWidget,
    description: str = "",
) -> None:
    """Install semantic help on one static or dynamically created control."""
    semantic = _semantic_widget_help(name, widget, root)
    current = description.strip() or widget.toolTip().strip()
    if current and not _is_generated_help(current):
        current_description, example = _split_tooltip(current)
        if not example or _is_generic_example(example):
            example = semantic.example
        widget.setToolTip(ControlHelp(current_description, example).text())
    else:
        widget.setToolTip(semantic.text())


def install_action_tooltips(actions: list[QAction] | tuple[QAction, ...]) -> None:
    """Install semantic help on named menu and toolbar actions only."""
    for action in actions:
        label = _clean_label(action.text())
        if not label or action.isSeparator():
            # Do not expose nonsense help on separators and internal QWidgetActions.
            if _is_generated_help(action.toolTip().strip()):
                action.setToolTip("")
            continue
        semantic = ACTION_HELP.get(label) or _semantic_action_help(label)
        current = action.toolTip().strip()
        if current and not _is_generated_help(current):
            description, example = _split_tooltip(current)
            if not example or _is_generic_example(example):
                example = semantic.example
            action.setToolTip(ControlHelp(description, example).text())
        else:
            action.setToolTip(semantic.text())


def _semantic_widget_help(name: str, widget: QWidget, root: QWidget) -> ControlHelp:
    """Return field-aware help for one explicitly owned application control."""
    if name in CONTROL_HELP:
        return CONTROL_HELP[name]
    label = _widget_label(widget)
    if label in ACTION_HELP:
        return ACTION_HELP[label]
    context = _owner_context(root)
    subject = _humanize_control_name(name, label)
    if isinstance(widget, QDialogButtonBox):
        return ControlHelp(
            f"Confirms or cancels the reviewed {context} operation.",
            "Choose Cancel to close without applying the reviewed changes.",
        )
    if isinstance(widget, QCheckBox):
        return ControlHelp(
            f"Controls whether {subject.lower()} is enabled for {context}.",
            f"Toggle it and verify the resulting {context} preview before saving.",
        )
    if isinstance(widget, QSpinBox):
        suffix = widget.suffix().strip()
        unit = f" {suffix}" if suffix else ""
        return ControlHelp(
            f"Sets {subject.lower()} for {context}; accepted values are {widget.minimum()} to {widget.maximum()}{unit}.",
            f"Set it to {widget.value()}{unit}, then inspect the affected preview.",
        )
    if isinstance(widget, QSlider):
        return ControlHelp(
            f"Adjusts {subject.lower()} for {context} from {widget.minimum()} to {widget.maximum()}.",
            f"Move it to {widget.value()} and compare the preview without changing source pixels.",
        )
    if isinstance(widget, QComboBox):
        option = widget.currentText().strip() or (
            widget.itemText(0).strip() if widget.count() else "an available value"
        )
        return ControlHelp(
            f"Selects {subject.lower()} used by {context}; changing it updates the applicable controls or preview.",
            f"Choose {option} and review the resulting {context} settings.",
        )
    if isinstance(widget, QLineEdit):
        sample = widget.placeholderText().strip() or widget.text().strip() or subject
        return ControlHelp(
            f"Sets {subject.lower()} stored by {context}; blank values use the documented default where supported.",
            f"Enter {sample} and review the dependent fields before applying it.",
        )
    if isinstance(widget, QListWidget):
        return ControlHelp(
            f"Selects {subject.lower()} shown by {context}; available actions depend on that selection.",
            f"Select one {subject.lower()} to inspect its details before editing or deleting it.",
        )
    if isinstance(widget, QTabWidget):
        tabs = [widget.tabText(index) for index in range(widget.count())]
        names = ", ".join(item for item in tabs if item) or "the available views"
        return ControlHelp(
            f"Switches between {names} within {context} without applying an edit.",
            f"Open {tabs[0] if tabs else 'the first view'} to inspect its controls.",
        )
    if isinstance(widget, QAbstractButton):
        return _semantic_action_help(label or subject, context)
    return ControlHelp(
        f"Configures {subject.lower()} for {context}.",
        f"Change it and inspect the affected {context} result before saving.",
    )


def _semantic_action_help(label: str, context: str = "the active workspace") -> ControlHelp:
    """Derive useful action help from its verb and target."""
    cleaned = _clean_label(label) or "selected item"
    words = cleaned.split()
    verb = words[0].casefold() if words else "apply"
    target = " ".join(words[1:]).strip() or "selected item"
    target_lower = target.lower()
    if verb in {"delete", "remove"}:
        return ControlHelp(
            f"Removes the {target_lower} from {context}; dependent selections or links may also become invalid.",
            f"Verify the selected {target_lower} and its references before removing it; use Undo immediately if available and needed.",
        )
    if verb == "clear":
        return ControlHelp(
            f"Clears the {target_lower} in {context} without changing unrelated project data.",
            f"Use it after confirming the current {target_lower} is no longer needed.",
        )
    if verb in {"run", "start", "play", "continue", "step", "fire", "send"}:
        return ControlHelp(
            f"Runs {target_lower} using the current {context} settings.",
            f"Configure the relevant target and inputs, then run it and inspect the result.",
        )
    if verb in {"import", "open", "choose", "relink"}:
        return ControlHelp(
            f"Opens a chooser for {target_lower} used by {context}; nothing is replaced until the selection is accepted.",
            f"Select a compatible source, review its preview, then confirm the choice.",
        )
    if verb in {"save", "export", "apply", "convert", "replace"}:
        return ControlHelp(
            f"Reviews and applies {target_lower} from {context} to its stated destination.",
            f"Inspect the preview or diff first, then confirm the operation.",
        )
    if verb in {"add", "insert", "duplicate", "copy"}:
        if verb == "copy":
            return ControlHelp(
                f"Copies {target_lower} from {context} to the system clipboard or stated destination.",
                f"Copy it, then paste it where the exact {target_lower} is required.",
            )
        return ControlHelp(
            f"Creates or inserts {target_lower} in {context} without altering the original source item.",
            f"Select the intended source or location, then inspect the new {target_lower}.",
        )
    if verb in {"undo", "redo"}:
        return ACTION_HELP[verb.title()]
    if verb in {"connect", "reconnect"}:
        return ControlHelp(
            f"Connects {target_lower} using the currently selected compatible endpoints in {context}.",
            "Review both endpoints and their types, then connect and validate the resulting path.",
        )
    if verb in {"previous", "next", "back", "forward"}:
        return ControlHelp(
            f"Moves to the {cleaned.lower()} item in {context} without modifying stored content.",
            "Use it to inspect adjacent items, then edit only the intended selection.",
        )
    if verb in {"fit", "zoom", "center"}:
        return ControlHelp(
            f"Adjusts the view to {cleaned.lower()} without changing project geometry or generated output.",
            "Use it after resizing the editor, then continue editing at the clearer view scale.",
        )
    if verb in {"move", "bring", "send"}:
        return ControlHelp(
            f"Changes the selected item's position or drawing order using {cleaned.lower()}.",
            "Apply it once, inspect overlaps or order, and use Undo if the wrong item moved.",
        )
    return ControlHelp(
        f"Performs {cleaned.lower()} on the current selection in {context}.",
        f"Select the intended item, run the action, and inspect the resulting change.",
    )


def _is_qt_internal(widget: QWidget) -> bool:
    """Exclude implementation children users cannot meaningfully configure."""
    name = widget.objectName()
    if name.startswith("qt_") or name in {"ScrollLeftButton", "ScrollRightButton"}:
        return True
    parent = widget.parentWidget()
    return isinstance(parent, (QAbstractSpinBox, QTabWidget))


def _widget_label(widget: QWidget) -> str:
    """Return one stable visible label where the widget exposes one."""
    if isinstance(widget, QAbstractButton):
        return _clean_label(widget.text())
    if isinstance(widget, QComboBox):
        return _clean_label(widget.currentText())
    return ""


def _owner_context(root: QWidget) -> str:
    """Describe the workspace or dialog owning a control."""
    title = root.windowTitle().strip()
    if title:
        return title
    value = root.__class__.__name__
    value = re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace(" Widget", "")
    return value.lower()


def _humanize_control_name(name: str, label: str = "") -> str:
    """Turn a stable Python attribute into a readable setting name."""
    if label:
        return label
    value = re.sub(
        r"_(button|check|combo|spin|edit|list|slider|tabs)$", "", name
    )
    return value.replace("_", " ").strip() or "this setting"


def _key_from_label(label: str) -> str:
    """Return a stable synthetic key for a locally scoped visible button."""
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_") + "_button"


def _split_tooltip(value: str) -> tuple[str, str]:
    """Split existing help without retaining a weak generated example."""
    description, separator, example = value.partition("Example:")
    return description.strip(), example.strip() if separator else ""


def _is_generated_help(value: str) -> bool:
    """Recognize the old label-repeating fallback so it can be replaced."""
    if not value:
        return False
    description, example = _split_tooltip(value)
    if any(description.startswith(prefix) for prefix in _OLD_GENERIC_PREFIXES):
        return True
    return bool(example and _is_generic_example(example) and len(description.split()) <= 8)


def _is_generic_example(value: str) -> bool:
    """Return whether an example merely repeats how to operate the widget."""
    return any(value.startswith(prefix) for prefix in _OLD_GENERIC_EXAMPLES) or value.endswith(
        "from a menu or toolbar."
    )


def _clean_label(value: str) -> str:
    """Remove shortcut markers, icons, and trailing dialog punctuation."""
    cleaned = value.replace("&", "").replace("...", "").replace("…", "").strip()
    return cleaned.lstrip("▶▸").strip()
