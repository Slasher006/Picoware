# PGA3 resource format

PGA3 is Picoware's generated, indexed resource container for images and WAV audio.
It is deliberately a data file, not an application or project file. A generated app
uses the small companion `generated_assets.py` module to find and stream resources
from `generated_assets.pga`.

The editor generates PGA3 by default. It can still identify PGA1 ownership metadata
and decode PGA2 images, but only PGA3 can contain audio. PGA3 audio is WAV-only;
MP3, compressed WAV, header-only WAV, and raw PCM data are rejected.

## Deployment modes

Every GUI project exposes an **Assets** choice in the App GUI header:

- **Combined PGA3** writes one indexed `generated_assets.pga`. This is the default and
  gives the smallest directory and the fastest indexed lookup for large catalogues.
- **Individual files** writes a `generated_assets/` directory. Every image becomes a
  valid one-image PGA3 file named `<stable-id>.pga`; every sound remains an ordinary
  `<stable-id>.wav` file. `_picoware_assets.pgl` is a tiny project-ownership marker used
  only to make reviewed regeneration safe. The device runtime never loads that marker.

The selected mode is saved in the GUI project and is used by normal export and Live
Simulator preview. `generated_assets.py` presents the same image and WAV functions in
both modes, so application drawing and extraction code does not need a storage-mode
branch.

Individual mode deliberately uses readable stable IDs as filenames. IDs must be no
more than 120 UTF-8 bytes, start with an ASCII letter or digit, and otherwise contain
only ASCII letters, digits, `.`, `_`, or `-`. This prevents path traversal and avoids
case/filename ambiguity on Pico storage. Editor-generated asset IDs already follow the
portable rule.

Explicit PGA loop points require Combined PGA3. Individual WAV mode retains the WAV
unchanged and does not create an audio sidecar, so generation rejects separate PGA loop
metadata rather than silently discarding it.

## Why one container does not consume all Pico RAM

The binary file stays on storage. `generated_assets.py` contains no Python dictionary
of resources and does not load the complete PGA file. It binary-searches a fixed
eight-byte-per-resource index, reads one metadata record, then streams only the
selected image row or WAV chunk.

- Image rendering buffers at most one RGB565 row.
- `read_wav_chunk()` returns at most 4096 bytes per call.
- `extract_wav()` copies in 1024-byte chunks.
- Hundreds of unused resources therefore increase storage and index-search time, not
  live Python-object memory.

Applications should still deploy only resources they reference. A large audio file
uses storage space and takes time to extract even though it does not occupy equivalent
RAM.

## Integer and text rules

All integers are little-endian. `u8`, `u16`, and `u32` are unsigned integers; `i32`
is signed two's-complement. Text is UTF-8 prefixed by a `u16` byte length. Project IDs
and resource IDs are required; display names may be empty. Each text field is limited
to 4096 encoded bytes.

Stable resource IDs are unique across all types. An image and a WAV cannot share an
ID. Records and payloads are emitted in resource-ID order, so identical input always
produces identical PGA bytes.

## File layout

### Header

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 4 bytes | ASCII `PGA3` |
| project ID length | `u16` | UTF-8 byte count |
| project ID | bytes | Owning GUI project ID |
| resource count | `u16` | Images plus WAV files |
| payload start | `u32` | First payload byte |
| total size | `u32` | Exact file length |

The header is followed by `resource count` index entries, then the metadata records,
then the payload area. Metadata must end exactly at `payload start`, and payloads must
fill the file contiguously without gaps or overlaps.

### Hash index

Each index entry is eight bytes:

| Field | Type | Meaning |
| --- | --- | --- |
| ID hash | `u32` | FNV-1a hash of the UTF-8 resource ID |
| record offset | `u32` | Absolute metadata-record offset |

Entries are ordered by `(hash, complete ID)`. The runtime uses the hash only to find
a small candidate range and always compares the complete ID, so a hash collision
cannot select the wrong resource.

### Common metadata prefix

Every record begins with:

| Field | Type | Meaning |
| --- | --- | --- |
| type | `u8` | `1` image, `2` WAV |
| flags | `u8` | Must currently be zero |
| resource ID length | `u16` | UTF-8 byte count |
| resource ID | bytes | Stable cross-reference ID |
| name length | `u16` | UTF-8 byte count |
| name | bytes | Human-readable name |

Unknown types or nonzero flags fail closed. A future extension must assign a new type
or format version instead of silently changing an existing record.

## Image record

The image-specific metadata after the common prefix is:

| Field | Type | Meaning |
| --- | --- | --- |
| width | `u16` | 1 through 320 pixels |
| height | `u16` | 1 through 320 pixels |
| origin X | `i32` | Drawing-origin offset |
| origin Y | `i32` | Drawing-origin offset |
| frame count | `u16` | At least one |

Each frame then has a 12-byte descriptor: absolute payload offset (`u32`), payload
length (`u32`), and duration in milliseconds (`u32`). Duration zero means no animation
timing. If one frame has timing, every frame must have a positive duration.

Each frame payload is row-major. A row contains `ceil(width / 8)` opacity-mask bytes,
followed by `width * 2` little-endian RGB565 pixel bytes. Mask bits run from the high
bit to the low bit. The separate mask preserves visible RGB565 black (`0x0000`) as
distinct from transparency.

## WAV record

The WAV-specific metadata after the common prefix is exactly 26 bytes:

| Field | Type | Meaning |
| --- | --- | --- |
| channels | `u8` | `1` mono or `2` stereo |
| bits per sample | `u8` | `8`, `16`, or `24` |
| sample rate | `u32` | Samples per second |
| duration | `u32` | Rounded milliseconds |
| loop start | `u32` | Milliseconds, or `0xffffffff` |
| loop end | `u32` | Milliseconds, or `0xffffffff` |
| payload offset | `u32` | Absolute WAV-file offset |
| payload length | `u32` | Complete WAV-file length |

The payload is the complete original RIFF/WAVE byte stream, including its header and
chunks. The RIFF length must match the file exactly. The accepted audio contract is
uncompressed PCM, mono or stereo, with 8-, 16-, or 24-bit samples. Metadata is derived
from the WAV header; explicitly supplied metadata must match it. Loop points are
optional, must be supplied as a pair, and must fall within the duration.

Loop metadata is descriptive. The current Picoware `play_wav()` API does not apply it
automatically; application logic decides whether and how to repeat playback.

## Generated MicroPython API

The companion module keeps the existing image API:

```python
has_asset(asset_id)
asset_size(asset_id)
frame_count(asset_id)
draw_asset(draw, asset_id, x, y, frame=0, scale=1)
```

PGA3 adds these WAV-only functions:

```python
has_wav(asset_id)
wav_info(asset_id)
wav_path(asset_id)
read_wav_chunk(asset_id, offset=0, size=1024)
extract_wav(asset_id, destination)
```

`wav_info()` returns `(sample_rate, channels, bits_per_sample, duration_ms,
loop_start_ms, loop_end_ms, byte_length)`, or `None`. Chunk reads include the complete
WAV stream starting at its RIFF header and are capped at 4096 bytes.

`wav_path()` returns the directly playable filename in Individual files mode and
`None` in Combined PGA3 mode. Code that wants to exploit loose files while remaining
portable across both modes can use the direct path when available and otherwise call
`extract_wav()`.

Picoware's audio backend currently accepts a filename, so an application extracts the
selected sound to an application-owned cache path before playback:

```python
from .generated_assets import extract_wav, wav_path

sound_path = wav_path("wav-click")
if sound_path is None:
    sound_path = "/sd/picoware/apps/my_app/click.wav"
    if not extract_wav("wav-click", sound_path):
        sound_path = None
if sound_path is not None:
    view_manager.audio.play_wav(sound_path)
```

Extract only the sound being used, reuse the cached file where appropriate, and remove
application-owned cache files during cleanup if they should not persist. The helper
writes a temporary `.pga-tmp` file and never loads the complete WAV into Python RAM.

## Desktop decoding and recovery

The Asset Library's **Import PGA images** command accepts PGA2 and PGA3. It recovers
image pixels, transparency, origins, names, frames, and durations as independent
library records. It does not import WAV files into the pixel-image library; the dialog
reports how many WAV entries remain in the source PGA3 file.

The PGA file cannot reconstruct the complete editor project. Screens, element
placements, behavior nodes, source audio paths, and application logic remain in the
GUI project and source tree.

## Validation and compatibility

Desktop decoding validates the complete file before returning any resource: magic,
declared size, UTF-8, index order, record order, types, metadata bounds, payload
contiguity, stable-ID uniqueness, image lengths, WAV structure, and WAV-derived
metadata. A damaged file is rejected rather than partially imported.

- PGA1: ownership can be recognized, but standalone images cannot be reconstructed.
- PGA2: image-only files remain strictly decodable and importable.
- PGA3: current generated format for images and PCM WAV files.

If another audio format is ever required, it must be a deliberate later format/type
extension. PGA3 remains WAV-only.
