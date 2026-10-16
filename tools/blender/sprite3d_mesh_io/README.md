# Sprite3D Mesh Import Export — Blender 5

Version 1.0.0 · Custom extension for the supplied `knight.sprite3d` layout.

**Verification status:** The pure-Python codec passed 25 automated tests,
including exact binary round-trip of the knight and all 65,536 RGB565 values.
Blender 5 itself was not available in the build environment. Installation, UI,
and actual Blender mesh integration have **not** been runtime-verified here.
A **Run Self-Test** button is included so those checks can run in your Blender.
See `TEST_REPORT.md` for the precise test boundary.

## Install once

1. Keep `sprite3d_mesh_io-1.0.0.zip` zipped.
2. Open Blender 5: **Edit → Preferences → Get Extensions**.
3. Open the top-right drop-down menu and choose **Install from Disk…**.
4. Select the ZIP and install it. Enable **Sprite3D Mesh Import Export** if it
   is not already enabled; it is listed under **Add-ons**.
5. Close Preferences. In the 3D View, press **N** and select **Sprite3D**.

No separate Python installation, command-line converter, OBJ file, internet
connection, paid service, or external Python dependency is required to use it.

## Import, edit, export

**File → Import → Sprite3D Mesh (.sprite3d)** opens your original file directly.
The same command is available in **N → Sprite3D → Import .sprite3d**.
Choose `knight.sprite3d` and leave the defaults for this sample.

Edit the resulting mesh normally in Blender. The default import converts the
file's assumed Y-up coordinates to Blender Z-up, merges exactly coincident
positions for easier editing, creates flat-color materials, and switches the
viewport to Material Preview. Disable **Merge Exact Duplicate Positions** if
surfaces that merely touch should remain independent. Disable **Show Material
Colors** to avoid changing viewport shading.

**File → Export → Sprite3D Mesh (.sprite3d)** writes the file directly. It
exports selected mesh objects by default, triangulates automatically, includes
modifiers and object transforms, and reverses the saved import-axis conversion.
The sidebar has an **Export Selected…** button for the same operation.
The first suggested output is `knight_edited.sprite3d`, not your original.
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
  as RGB565 while retaining the unknown upper 16 bits per original face.
  A face with no material retains its original color; a completely new face
  without a material becomes white and produces a warning.
- **Preserve Original Face Data**: ignore material edits and assignments.
  Preserve the original two uint16 values per face. Where original face
  attributes are missing, an imported material's stored values are accepted;
  otherwise export stops rather than inventing original data.
- **Require Material Colors**: every face must have a supported material.
  Missing materials are errors rather than fallback colors.

**Color interpretation is inferred, not exporter-confirmed.** The first uint16
looks like RGB565, but the original application's exporter/loader was not
identified. Unedited colors round-trip numerically; correct appearance and
meaning in the original engine still need checking. Choose **Preserve Original
Face Data** for conservative geometry-only editing.

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

Unknown per-face data is kept in integer FACE attributes. Topology-changing
operations may copy, interpolate or discard attributes. New faces that lack
valid attributes use their assigned imported material's extra data; otherwise
**New Face Extra Data** defaults to **1**, the value found in the knight.
A warning identifies triangles without original face data. That fallback is
not evidence that `1` is appropriate for a different engine or asset format.
For especially sensitive edits, re-import the edited result and check it in
the original application before replacing production assets.

The implementation has a safety limit of 250,000 triangles per file. This is
an add-on limit, not a claim about the target engine's memory/triangle budget.
Loose edges and isolated vertices are not representable in this triangle-only
format and are not exported. Degenerate input triangles are kept.

## What file format is supported?

Only the specific headerless binary layout found in your upload:

| Offset in each 40-byte record | Contents |
| --- | --- |
| 0–35 | Nine little-endian float32 numbers: three XYZ vertices |
| 36–37 | A uint16, interpreted as RGB565 for material preview/editing |
| 38–39 | An opaque uint16; preserved, not treated as padding |

There is no header, index buffer, skeleton, material table, UV data, object
hierarchy or animation block in this supported layout. This is **not** a
universal Godot `.tscn`/`.tres` importer or a reader for every unrelated format
named `.sprite3d`. A coincidental size divisible by 40 is not proof that an
unfamiliar file has this format; the importer can only validate the structural
layout and finite coordinates, not recognize a missing magic signature.

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

For development only, after extracting the package:

    python3 -m unittest discover -s tests -v

A larger, optional actual-Blender integration script is included:

    blender --background --factory-startup --python-exit-code 1 --python /absolute/path/to/tests/run_in_blender.py

The normal add-on workflow does not require either command.

## Files and license

`__init__.py` contains the Blender integration and UI. `codec.py` is the
standalone binary codec. `blender_manifest.toml` describes the extension.
`tests/` includes codec tests, an actual-Blender integration script, and the
unchanged sample knight as a local test fixture. `TEST_REPORT.md` records what
was actually verified during creation.

The add-on code is supplied under GPL-3.0-or-later; see `LICENSE`. The bundled
`tests/fixtures/knight.sprite3d` is the user's supplied asset, included only for
local testing. Its inclusion does not grant new ownership or licensing rights
over that asset.

## Blender documentation

- Extension installation: https://docs.blender.org/manual/en/latest/editors/preferences/extensions.html
- Extension packaging: https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html
- Mesh API: https://docs.blender.org/api/current/bpy.types.Mesh.html

This is a custom generated add-on, not an official Blender or Godot extension.
