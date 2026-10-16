# Sprite3D Mesh Import Export — Blender 5

Version 1.3.3 · Picoware Sprite3D mesh import/export.

The codec and Blender integration suites run against Blender 5.3.0 Alpha.
They check byte-exact round-trips, materials, transforms, triangulation,
backup behavior and Picoware validation limits. These are host checks;
rendering and available memory still need testing on the target device.

## Install once

1. Keep `sprite3d_mesh_io-1.3.3.zip` zipped.
2. Open Blender 5: **Edit → Preferences → Get Extensions**.
3. Open the top-right drop-down menu and choose **Install from Disk…**.
4. Select the ZIP and install it. Enable **Sprite3D Mesh Import Export** if it
   is not already enabled; it is listed under **Add-ons**.
5. Close Preferences. In the 3D View, press **N** and select **Sprite3D**.

No separate Python installation, command-line converter, OBJ file, internet
connection, paid service, or external Python dependency is required to use it.

## File names and compatibility

`.bin` is the default extension for both mesh and billboard exports. Mesh import
also accepts `.sprite3d`. Explicitly chosen `.sprite3d` mesh export names and
`.billboard` billboard export names are retained for compatibility; Save Back to
Source preserves the imported filename. The packed billboard loader accepts
both `.bin` and `.billboard` (it validates the header, not the extension).
The binary formats are unchanged. Choose the mesh importer for mesh files;
packed billboards are loaded by `picoware.engine.billboard_asset` in the game.

## Import, edit, export

**File → Import → Sprite3D Mesh (.bin / .sprite3d)** opens your original file directly.
The same command is available in **N → Sprite3D → Import Mesh**.
Choose `knight.sprite3d` and leave the defaults for this sample.

Edit the resulting mesh normally in Blender. The default import converts the
file's assumed Y-up coordinates to Blender Z-up, merges exactly coincident
positions for easier editing, creates flat-color materials, and switches the
viewport to Material Preview. Disable **Merge Exact Duplicate Positions** if
surfaces that merely touch should remain independent. Disable **Show Material
Colors** to avoid changing viewport shading.

**File → Export → Sprite3D Mesh (.bin / .sprite3d)** writes the file directly. It
exports selected mesh objects by default, triangulates automatically, includes
modifiers and object transforms, and reverses the saved import-axis conversion.
The sidebar has an **Export Selected…** button for the same operation.
The first suggested output is `knight_edited.bin`, not your original.
Multiple selected meshes are flattened into one triangle stream; their names,
parenting and hierarchy are not stored in the output.

**N → Sprite3D → Save Back to Source** replaces the original imported path for
only the **active mesh**, after a confirmation dialog and a timestamped backup.
If the source has changed on disk since import/last Save Back, the button
refuses to overwrite it. Re-import or use the regular exporter with a new name.

A backup looks like:

    knight.sprite3d.20260929T123456_123456Z.bak

Backups contain the old file bytes and are never automatically deleted. To
restore one, copy it to a new file ending in `.sprite3d`, or replace the damaged
asset with its bytes. External file writes are **not** undone with Ctrl+Z.
Save your working Blender project as `.blend` as well; the mesh's custom face
attributes, material properties and source settings are stored in the project.

## Edit colors without special material names

For normal recoloring, edit a material's **Principled BSDF → Base Color**, or
use **N → Sprite3D → Face Color** for the active material. Assign materials to
faces normally. Imported materials can be renamed freely: metadata is stored
as mesh attributes/custom properties, not encoded only in names.

Use Material Preview to see Base Color changes. Blender's separate
**Viewport Display → Color** is not the exported color when nodes are enabled.
Changes to one material affect every face assigned that material. Make a
single-user material copy for an independent color.

The exporter's **Colors / Data** modes are:

- **Material Colors / Keep Extra Data** (default): export flat material colors
  as RGB565 while retaining the wireframe byte and padding byte per original face.
  A face with no material retains its original color; a completely new face
  without a material becomes white and produces a warning.
- **Preserve Original Face Data**: ignore material edits and assignments.
  Preserve the original two uint16 values per face. Where original face
  attributes are missing, an imported material's stored values are accepted;
  otherwise export stops rather than inventing original data.
- **Require Material Colors**: every face must have a supported material.
  Missing materials are errors rather than fallback colors.

Colors are RGB565, matching the current Picoware game engine.
Choose **Preserve Original Face Data** for geometry-only editing.

A supported node material uses a Principled BSDF connected to Material Output,
with an unlinked, solid Base Color. Linked textures/procedural colors or mixed
shader graphs cause a clear error in color-export modes rather than a silent
texture approximation. Texture baking, vertex paint, UVs, alpha, emission and
other shader effects are not exported.

## Geometry, units and export options

**File Coordinates → Automatic / Match Import** is the normal setting. For
imported objects it reverses the saved axis conversion and divides out the
saved import scale. New meshes default to the knight's assumed Y-up convention.
The explicit **Source Y Up** and **Already Blender Z Up** export settings use
that convention directly and do *not* divide out a saved import scale.
**Additional Export Scale** is applied after those conversions.

**Apply Object Transforms** includes world-space position, rotation, scale and
parenting. Disable it for local mesh coordinates. **Apply Modifiers** exports
the evaluated mesh at the current frame without changing the modifier stack.
An animated/rigged object can therefore export its current evaluated shape,
but not its rig or animation. Geometry Nodes instances must be realized first.
Collection instances and non-mesh objects are not exported.

**Correct Mirrored Winding** reverses triangle winding for an applied
negative-determinant object transform. It does not repair an already
inconsistently wound source mesh.

Quads and polygons are triangulated for the file only; the working mesh is not
permanently triangulated. Nonplanar polygons use Blender's tessellation. Exact
byte identity is not promised after geometry/topology edits, arbitrary scales,
modifier evaluation, or Blender operations that reorder vertices/triangles.

Wireframe and padding are kept in integer FACE attributes compatible with
existing Blender projects. Topology changes may copy or discard those attributes.
New faces use their assigned imported material's metadata when available;
otherwise **New Face Wireframe** controls the outline flag (on by default),
and padding is zero. Imported padding bytes are preserved exactly. Invalid
wireframe flags are rejected instead of silently normalized.

Import and export accept at most **4,096 triangles**, matching this Picoware
build's `ENGINE_MAX_TRIANGLES_PER_SPRITE`. Export counts evaluated, triangulated
geometry across all selected meshes. Other engine builds may have lower limits.
Coordinates must be finite and within **+/-1e12**, matching the Picoware editor.
Validation completes before an existing output is replaced or backed up.

Loose edges and isolated vertices are not representable in this triangle-only
format and are not exported. Degenerate input triangles are kept.

## What file format is supported?

The current Picoware engine uses this headerless binary layout:

| Offset in each 40-byte record | Contents |
| --- | --- |
| 0–35 | Nine little-endian float32 numbers: three XYZ vertices |
| 36–37 | RGB565 uint16 color |
| 38 | Wireframe flag: 0 or 1 |
| 39 | Preserved alignment padding byte |

There is no header, index buffer, skeleton, material table, UV data, object
hierarchy or animation block in this supported layout. This is **not** a
universal Godot `.tscn`/`.tres` importer or a reader for every unrelated format
named `.sprite3d`. A coincidental size divisible by 40 is not proof that an
unfamiliar file has this format; the importer can only validate the structural
layout, flags, coordinate bounds and triangle count, not recognize a missing magic signature.
Legacy 52-byte records are not supported; convert them with the Picoware editor first.

Source sample: 8,960 bytes, 224 triangle records, 155 exact vertex positions,
36 different per-face metadata pairs; all sample extra uint16 values are 1.

## Verify in your Blender installation

Switch to Object Mode and click **N → Sprite3D → Run Self-Test**. It temporarily
imports the bundled knight sample, checks exact byte round-trips through raw
and evaluated Blender meshes, checks unchanged colors, renaming/recoloring,
object transforms and quad triangulation, then removes the temporary objects.
It does not replace files or change your existing meshes/materials.

A successful result appears in Blender's status/report area. Full check details
are printed to Blender's Python/system console. A failure reports its message
instead of claiming success. This is useful verification, not a comprehensive
guarantee about every modifier or original-engine loader.

For development only, from the Picoware repository root (the packed-loader tests also use the runtime source):

    python3 -m unittest discover -s tools/blender/sprite3d_mesh_io/tests -v

A larger, optional actual-Blender integration script is included:

    blender --background --factory-startup --python-exit-code 1 --python /absolute/path/to/tests/run_in_blender.py

The normal add-on workflow does not require either command.

## Export directional billboards

Select the model meshes, then use **N → Sprite3D → Export Billboard** or
**File → Export → Picoware Billboard**. Static export bakes the current evaluated frame. Defaults are eight views
at 8x8 pixels. Under **Advanced**, choose 1, 4 or 8 views and 8 or 16
pixels per square view. **Model Forward** is the direction the model faces
in Blender's XY ground plane (the example dragon presets -X).

Export writes **one `.bin` file** containing every direction and pose,
RGB332 pixels, transparency masks, world height, playback speed and loop setting.
No image folder, JSON sidecar or per-asset Python loader is needed. Existing
files are refused; choose a new name to preserve the old asset. Temporary render
PNGs are removed automatically. All views share orthographic framing with 3%
ground clearance. View zero looks at the front; camera azimuth increases
counterclockwise around Blender +Z. The bake uses camera-relative studio lighting.

Copy that single file to the SD card. Use the reusable loader included in this
Picoware checkout (older firmware needs the new `billboard_asset.py` module):

```python
from picoware.engine.billboard_asset import load_billboard
asset, height = load_billboard("/sd/dragon.bin")
entity.set_billboard(asset, height)
```

The loader validates the header and exact file length before creating images,
then reads one frame at a time. New firmware with `set_direction_pose_data` copies each frame directly into
billboard-owned storage, avoiding an intermediate native Image. The loader
automatically falls back to the older Image API on compatible earlier firmware.
Native billboard storage still keeps all poses
and directions in memory; packing does not reduce that payload.
The entity direction defines its front in engine world space; Model Forward
defines which side of the Blender model is baked as the front.
You may pass a smaller world height to display the dragon at a different scale.
The renderer chooses an image from camera angle relative to the entity direction.

Eight 8x8 views contain **576 bytes** of pixel and mask payload.
This excludes native objects, metadata and temporary loading copies. Lower
resolution reduces memory; billboards also trade true 3D geometry for discrete
views. The example dragon passed 17 complete framebuffer comparisons in the
native desktop simulator (eight directions at two sizes, transparency and
behind-camera rejection). Device memory, performance and rendering have not
been tested. Mesh export remains available independently.

## Animate and export — normal workflow

1. Animate your model with ordinary Blender keyframes. Set the timeline start
   and end to the clip you want. For a loop, the last frame should match the first.
2. Select the model meshes (selecting all is fine; cameras and lights are ignored).
3. Choose **N → Sprite3D → Export Animated Billboard**, choose a new output name,
   and export. The defaults use the timeline range and speed, four poses, eight
   views and 8x8 pixels. No Python is needed to author or export the animation.

**Preview Sampled Poses** shows exactly the poses that will be exported.
Click **Stop Preview** to return to the original timeline frame.
The preview also stops automatically for non-looping animations. Stop normal
Blender timeline playback before using this preview.

The export dialog shows payload memory before baking: the default animated
asset uses **2.25 KiB**, excluding object overhead and loading copies.
The maintainer target is 8x8, with 16x16 as the maximum export size.
At 16x16, eight views and four poses use 9 KiB of pixel and mask payload.
For larger detail, use triangle mesh export. Existing larger packed files
remain loadable for compatibility; their old performance results below do
not establish a recommended billboard size.
**Advanced** holds resolution, direction count, model-forward axis and manual
frame/rate settings. Change **Exported Poses** from 1 to 8 for the current engine.

Loop export excludes the repeated closing frame: range 1–13 with four poses
samples frames 1, 4, 7 and 10. At a 24 FPS timeline, that becomes eight poses per
second and a half-second loop. Non-looping export includes both endpoints.
Fractional sample frames are supported. The exporter evaluates mesh modifiers
and armatures at every sample; all frames and directions share the same framing.
Do not use uncached simulations that depend on sequential playback as a source.
Timeline position is restored after successful or failed baking. Output files
are only published after the whole bake succeeds.

### Editable dragon example

Open `emerald-dragon-animated.blend` and press Space to play. The separate
**Dragon — Wing Flap Example** scene contains **Wing Flap L**, **Wing Flap R**
and **Tail Sway** arrow controls, parented at the joints. Select a control to
edit its rotation keyframes in the timeline or Dope Sheet. The static dragon
scene is retained. The example is an object-parent rig, not a skeletal armature.
Select the mesh parts again before exporting.

### Play it in Picoware

The same runtime module supports animation:

```python
from picoware.engine.billboard_asset import load_animation
asset, animation = load_animation("/sd/dragon.bin", entity, world_height=2.0)
# In your existing game update, using elapsed time in seconds:
animation.update(dt_seconds)
```

Keep `asset` and `animation` alive with the entity. The helper uses exported
pose count, speed and loop settings. It advances the pose; the engine chooses
the viewing direction automatically. One-shot playback holds its final pose;
`animation.reset()` restarts it. A delayed update advances to the correct pose
instead of slowing the clip. No extra timer or background thread is created.

The packed animated dragon passed 64 full-frame native simulator comparisons
(eight directions, four poses, two display sizes), plus looping, skipped-update,
one-shot and reset checks. On a PicoCalc Pico 2 W with firmware built from this
checkout, the 147,480-byte file loaded in 162 ms and rendered 96 test frames
across all directions and poses. The user confirmed correct display output.
Python free heap was 349,888 bytes before loading, 199,536 after loading and GC,
and 349,152 after detaching and GC. Mean render-call time was 35.24 ms; this is
one dragon scene, not a general performance guarantee or sustained FPS test.

## Files and license

`__init__.py` contains the Blender integration and UI. `codec.py` is the
standalone binary codec. `blender_manifest.toml` describes the extension.
`tests/` includes codec tests, an actual-Blender integration script, and the
unchanged sample knight as a local test fixture used by the built-in self-test.

The add-on code is supplied under GPL-3.0-or-later; see `LICENSE`. The bundled
`tests/fixtures/knight.sprite3d` is the user's supplied asset, included only for
local testing. Its inclusion does not grant new ownership or licensing rights
over that asset.

## Blender documentation

- Extension installation: https://docs.blender.org/manual/en/latest/editors/preferences/extensions.html
- Extension packaging: https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html
- Mesh API: https://docs.blender.org/api/current/bpy.types.Mesh.html

This is a custom generated add-on, not an official Blender or Godot extension.

## Packed billboard format v1

The 24-byte little-endian header uses `<4sBBBBHHffI`:

| Offset | Value |
| --- | --- |
| 0 | Four-byte magic `PWBB` |
| 4 | Version, uint8: 1 |
| 5 | Directions, uint8: 1, 4 or 8 |
| 6 | Poses, uint8: 1–8 |
| 7 | Flags, uint8: bit 0 means loop; other bits must be zero |
| 8 | Width, uint16: 8 or 16; legacy loading also accepts 32, 64, 96, 128 |
| 10 | Height, uint16: same as width |
| 12 | Poses per second, float32: positive, at most 60 |
| 16 | World height, float32: positive and finite |
| 20 | Payload length, uint32 |

Payload order is pose first, then direction. Each frame contains width × height
RGB332 bytes followed by ceil(width × height / 8) transparency bytes. Pixels
are top-down, row-major; mask bits are MSB-first without row padding. A set bit
is visible. No trailing data is allowed. A default 8-view, 4-pose, 8x8 asset
is 2,328 bytes including its header. The 16x16 equivalent is 9,240 bytes. Older folder exports remain usable with
their generated loader; re-export from Blender to obtain the packed format.

## Historical 64x64 runtime optimization validation

These tests predate the maintainer correction to 8x8 / 16x16 and are not
representative of the new defaults.

With the direct-buffer engine API and PicoCalc span-write integration, the same
8-direction, 4-pose dragon at 240 MHz loaded in 125 ms versus 163 ms on the
baseline firmware. Mean engine tick time over 96 frames was 34.47 ms versus
35.25 ms. Retained heap use was unchanged at 150,352 bytes. These are one-scene
measurements, not a general FPS or peak-memory guarantee. Separate measurements
put full-screen swap at 28.69 ms and clear at 2.31 ms; full redraw is retained.
Core implementation lives on the separate pico-game-engine branch
`perf/billboard-buffer-load-20261016`; the binding, vendored integration and LCD
backend live on Picoware branch `perf/billboard-runtime-20261016`.

## Maintainer-size validation

The normal exporter now defaults to 8x8 and permits at most 16x16. Both sizes
passed 64 full-frame native simulator checks and 96 PicoCalc render frames.
For eight directions and four poses, 8x8 loaded in 16 ms and retained 5,456 bytes
of heap; 16x16 loaded in 24 ms and retained 12,112 bytes. These heap figures
include object overhead, unlike the 2,304-byte and 9,216-byte image payloads.
Tests used the same projected display size and 240 MHz CPU clock. Smaller
source textures greatly reduced asset memory; whole-screen transfer still
dominated frame time. These are not comparisons against triangle meshes.
