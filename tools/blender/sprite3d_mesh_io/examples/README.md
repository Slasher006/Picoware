# Blender billboard examples

These examples belong to the Sprite3D Blender plugin and ship in its ZIP.
They were rebuilt with plugin **1.4.4**: correct clockwise view order, grounded
framing and connected material colors. Every animation is one `.bin` asset.

| Example | Source | Animation | Asset size |
| --- | --- | --- | --- |
| [Emerald dragon](dragon/emerald-dragon.bin) | [Blender project](dragon/emerald-dragon.blend) | 8 views × 4 poses, 8 poses/sec | 9,240 bytes |
| [Swaying oak](oak/wind-oak.bin) | [Blender project](oak/wind-oak.blend) | 8 views × 8 poses, 4 poses/sec | 18,456 bytes |
| [Outlined oak](oak/wind-oak1.bin) | Same oak project | 8 views × 4 poses, 2 poses/sec | 9,240 bytes |

All three are 16×16 RGB332 with a binary transparency mask. The outlined oak
recreates the `wind-oak1` comparison variant with the current exporter.

## Export in Blender

Open the project. For the dragon choose scene **Dragon — Wing Flap Example**;
for the oak choose **Oak — Gentle Wind**. Select the mesh objects and use
**Export Animated Billboard**. Leave **Pixel Art** enabled and select **16×16**,
eight views, loop, and timeline timing. Settings:

- Dragon: forward **-X**, camera height angle **10°**, four poses, frames 1–13
  at 24 FPS. No outline.
- Oak: forward **-Y**, camera height angle **0°**, eight poses, frames 1–49
  at 24 FPS. No outline.
- Outlined oak: same project and range, four poses, **Dark Outline** enabled.

The exporter refuses existing files. Source `.blend` files remain unchanged.
The `.bin` files are billboard assets, not Sprite3D triangle meshes.

## PicoCalc demo

Use Picoware firmware that includes `picoware.engine.billboard_asset` and
animated billboard support. Copy these files to the SD root:

- `dragon/emerald-dragon.bin` → `/sd/dragon.bin`
- `oak/wind-oak.bin` → `/sd/windoak.bin`
- `oak/wind-oak1.bin` → `/sd/windoak1.bin`

Run [grove_demo.py](grove_demo.py) with `mpremote run` or from your application.
It keeps the dragon between the two swaying trees, draws a dark floor, and
orbits the camera every 20 seconds. Press Esc/Back to exit. The script calls
`draw.swap()` explicitly because disabling automatic level clearing also
turns off the engine's automatic display update.

The newly rebuilt assets have Blender and simulator validation. Earlier
versions of this scene ran on PicoCalc; these final rebuilt files still need
a fresh visual device check. The demo does not install itself at startup.

The three assets total 36,936 file bytes. Runtime memory is higher because of
native image/entity objects and other scene state; file size is not heap use.
