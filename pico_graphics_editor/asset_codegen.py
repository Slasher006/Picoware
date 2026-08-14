"""Encode editable pixel art as deterministic compact asset records."""

from __future__ import annotations

import hashlib
import io
import json
import wave
from collections.abc import Sequence
from dataclasses import dataclass

from .model import PixelArt


ASSET_FORMAT_VERSION = 1
MAXIMUM_ASSET_DIMENSION = 320
LEGACY_ASSET_RESOURCE_MAGIC = b"PGA1"
PGA2_ASSET_RESOURCE_MAGIC = b"PGA2"
ASSET_RESOURCE_MAGIC = b"PGA3"
SUPPORTED_ASSET_RESOURCE_MAGICS = (
    LEGACY_ASSET_RESOURCE_MAGIC,
    PGA2_ASSET_RESOURCE_MAGIC,
    ASSET_RESOURCE_MAGIC,
)
MAXIMUM_RESOURCE_TEXT_BYTES = 4096
RESOURCE_INDEX_ENTRY_SIZE = 8
RESOURCE_FRAME_RECORD_SIZE = 12
RESOURCE_TYPE_IMAGE = 1
RESOURCE_TYPE_WAV = 2
RESOURCE_WAV_RECORD_SIZE = 26
INDIVIDUAL_RESOURCE_DIRECTORY = "generated_assets"
INDIVIDUAL_RESOURCE_MARKER = "_picoware_assets.pgl"
INDIVIDUAL_RESOURCE_MARKER_MAGIC = b"PGL1"
MAXIMUM_INDIVIDUAL_RESOURCE_ID_BYTES = 120

AssetPalette = tuple[int | None, ...]
AssetRectangle = tuple[int, int, int, int, int]
AssetFrame = tuple[AssetRectangle, ...]


@dataclass(frozen=True)
class EncodedAsset:
    """Store one validated v1 static or animated compact asset."""

    width: int
    height: int
    origin_x: int
    origin_y: int
    palette: AssetPalette
    frames: tuple[AssetFrame, ...]
    durations: tuple[int, ...] = ()
    format_version: int = ASSET_FORMAT_VERSION


@dataclass(frozen=True)
class GeneratedAssetEntry:
    """Bind one stable project asset identifier to encoded runtime data."""

    asset_id: str
    name: str
    asset: EncodedAsset


@dataclass(frozen=True)
class GeneratedRasterEntry:
    """Bind one stable asset ID directly to lossless editor pixel frames."""

    asset_id: str
    name: str
    frames: tuple[PixelArt, ...]
    durations: tuple[int, ...] = ()


@dataclass(frozen=True)
class GeneratedAudioEntry:
    """Bind one stable ID to one complete streamable WAV file."""

    asset_id: str
    name: str
    data: bytes
    sample_rate: int = 0
    channels: int = 0
    bits_per_sample: int = 0
    duration_ms: int = 0
    loop_start_ms: int | None = None
    loop_end_ms: int | None = None


@dataclass(frozen=True)
class GeneratedAssetResource:
    """Pair one small runtime module with its streamed PGA resource data."""

    module_source: str
    data: bytes
    resource_name: str
    asset_count: int
    frame_count: int
    maximum_row_bytes: int
    audio_count: int = 0
    files: tuple[tuple[str, bytes], ...] = ()
    storage_mode: str = "combined"

    @property
    def resource_count(self) -> int:
        """Return the total number of typed resources in the container."""
        return self.asset_count + self.audio_count

    @property
    def payload_size(self) -> int:
        """Return the deployed byte count across all generated resource files."""
        return sum(len(content) for unused_name, content in self.files) or len(
            self.data
        )


@dataclass(frozen=True)
class DecodedAssetResource:
    """Hold losslessly decoded image and audio entries from a PGA resource."""

    project_id: str
    assets: tuple[GeneratedRasterEntry, ...]
    audio_assets: tuple[GeneratedAudioEntry, ...] = ()
    format_version: int = 3


@dataclass(frozen=True)
class _ResourceAsset:
    """Hold validated desktop-side image metadata while assembling PGA bytes."""

    asset_id: str
    asset_id_bytes: bytes
    name_bytes: bytes
    width: int
    height: int
    origin_x: int
    origin_y: int
    durations: tuple[int, ...]
    frames: tuple[bytes, ...]


@dataclass(frozen=True)
class _ResourceWav:
    """Hold validated desktop-side WAV metadata while assembling PGA3 bytes."""

    asset_id: str
    asset_id_bytes: bytes
    name_bytes: bytes
    channels: int
    bits_per_sample: int
    sample_rate: int
    duration_ms: int
    loop_start_ms: int | None
    loop_end_ms: int | None
    data: bytes


@dataclass(frozen=True)
class _DecodedResourceRecord:
    """Hold one validated PGA2 record until all cross-links are checked."""

    asset_id: str
    asset_id_bytes: bytes
    name: str
    width: int
    height: int
    origin_x: int
    origin_y: int
    durations: tuple[int, ...]
    record_end: int
    frame_spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _DecodedPga3ImageRecord:
    """Hold one validated PGA3 image record before pixel reconstruction."""

    asset_id: str
    asset_id_bytes: bytes
    name: str
    width: int
    height: int
    origin_x: int
    origin_y: int
    durations: tuple[int, ...]
    record_end: int
    frame_spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _DecodedPga3WavRecord:
    """Hold one validated PGA3 WAV record before payload reconstruction."""

    asset_id: str
    asset_id_bytes: bytes
    name: str
    channels: int
    bits_per_sample: int
    sample_rate: int
    duration_ms: int
    loop_start_ms: int | None
    loop_end_ms: int | None
    record_end: int
    data_span: tuple[int, int]


def encode_asset(
    frames: Sequence[PixelArt],
    durations: Sequence[int] | None = None,
) -> EncodedAsset:
    """Encode compatible pixel frames into one deterministic v1 asset."""
    source_frames = tuple(frames)
    if not source_frames:
        raise ValueError("At least one asset frame is required")
    first = source_frames[0]
    if not isinstance(first, PixelArt):
        raise TypeError("Asset frames must be PixelArt instances")
    _validate_dimensions(first.width, first.height)
    _validate_integer(first.origin_x, "Asset origin X")
    _validate_integer(first.origin_y, "Asset origin Y")
    for frame in source_frames:
        _validate_source_frame(frame, first)

    palette = _build_palette(source_frames)
    palette_indexes = {
        color: index for index, color in enumerate(palette) if color is not None
    }
    encoded_frames = tuple(
        _encode_frame(frame, palette_indexes) for frame in source_frames
    )
    if isinstance(durations, (str, bytes, bytearray)):
        raise TypeError("Asset durations must be an integer sequence")
    encoded = EncodedAsset(
        first.width,
        first.height,
        first.origin_x,
        first.origin_y,
        palette,
        encoded_frames,
        tuple(durations) if durations is not None else (),
    )
    # Geometry, pixels, palette order, and rectangles were validated or built
    # canonically above. Revalidating every generated rectangle here doubles
    # the cost for dense imported images; only caller-provided durations remain.
    _validate_durations(encoded.durations, len(encoded.frames))
    return encoded


def validate_asset(asset: EncodedAsset) -> None:
    """Reject malformed or noncanonical v1 asset records."""
    if not isinstance(asset, EncodedAsset):
        raise TypeError("Expected an EncodedAsset record")
    if asset.format_version != ASSET_FORMAT_VERSION:
        raise ValueError(f"Unsupported asset format {asset.format_version}")
    _validate_dimensions(asset.width, asset.height)
    _validate_integer(asset.origin_x, "Asset origin X")
    _validate_integer(asset.origin_y, "Asset origin Y")
    _validate_palette(asset.palette)
    if not isinstance(asset.frames, tuple) or not asset.frames:
        raise ValueError("An asset must contain at least one frame")

    used_palette_indexes: set[int] = set()
    for frame in asset.frames:
        used_palette_indexes.update(
            _validate_frame(frame, asset.width, asset.height, len(asset.palette))
        )
    expected_palette_indexes = set(range(1, len(asset.palette)))
    if used_palette_indexes != expected_palette_indexes:
        raise ValueError("Every visible palette color must be used")
    _validate_durations(asset.durations, len(asset.frames))


def reconstruct_asset(asset: EncodedAsset, frame: int = 0) -> PixelArt:
    """Reconstruct one encoded frame for lossless desktop-side proof."""
    validate_asset(asset)
    if type(frame) is not int or not 0 <= frame < len(asset.frames):
        raise IndexError("Asset frame is out of range")
    art = PixelArt(asset.width, asset.height, asset.origin_x, asset.origin_y)
    for x, y, width, height, palette_index in asset.frames[frame]:
        art.draw_rectangle(
            x,
            y,
            width,
            height,
            asset.palette[palette_index],
            True,
        )
    return art


def canonical_asset_bytes(asset: EncodedAsset) -> bytes:
    """Return deterministic bytes used to fingerprint one encoded asset."""
    validate_asset(asset)
    payload = (
        asset.format_version,
        asset.width,
        asset.height,
        asset.origin_x,
        asset.origin_y,
        asset.palette,
        asset.frames,
        asset.durations,
    )
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def asset_fingerprint(asset: EncodedAsset) -> str:
    """Return the canonical SHA-256 fingerprint for an encoded asset."""
    return hashlib.sha256(canonical_asset_bytes(asset)).hexdigest()


def fingerprint_encoded_asset(asset: EncodedAsset) -> str:
    """Fingerprint a canonical record returned directly by :func:`encode_asset`."""
    payload = (
        asset.format_version,
        asset.width,
        asset.height,
        asset.origin_x,
        asset.origin_y,
        asset.palette,
        asset.frames,
        asset.durations,
    )
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def generate_assets_module(
    project_id: str,
    generator_version: str,
    entries: Sequence[GeneratedAssetEntry],
) -> str:
    """Generate the deterministic, MicroPython-friendly v1 asset module."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("A stable project ID is required")
    if not isinstance(generator_version, str) or not generator_version.strip():
        raise ValueError("A generator version is required")
    ordered_entries = sorted(tuple(entries), key=lambda entry: entry.asset_id)
    seen_ids: set[str] = set()
    records: list[str] = []
    for entry in ordered_entries:
        if not isinstance(entry, GeneratedAssetEntry):
            raise TypeError("Asset entries must be GeneratedAssetEntry records")
        if not entry.asset_id or entry.asset_id in seen_ids:
            raise ValueError("Generated asset IDs must be nonempty and unique")
        if not isinstance(entry.name, str):
            raise TypeError("Generated asset names must be strings")
        seen_ids.add(entry.asset_id)
        validate_asset(entry.asset)
        records.append(_runtime_asset_record(entry))

    assets_literal = "{}" if not records else "{\n" + "\n".join(records) + "}"
    return (
        "# @picoware-generated structure=1\n"
        "# @picoware-generated role=assets\n"
        f"# @picoware-generated project={project_id.strip()}\n"
        f"# @picoware-generator version={generator_version.strip()}\n"
        "# This file is editor-owned. Regenerate it instead of editing it manually.\n"
        "\n"
        "_NAME = 0\n"
        "_WIDTH = 1\n"
        "_HEIGHT = 2\n"
        "_ORIGIN_X = 3\n"
        "_ORIGIN_Y = 4\n"
        "_PALETTE = 5\n"
        "_FRAMES = 6\n"
        "_DURATIONS = 7\n"
        "\n"
        "\n"
        f"_ASSETS = {assets_literal}\n"
        "\n"
        "\n"
        "def has_asset(asset_id):\n"
        '    """Return whether a generated asset exists."""\n'
        "    return asset_id in _ASSETS\n"
        "\n"
        "\n"
        "def asset_size(asset_id):\n"
        '    """Return an asset natural dimensions."""\n'
        "    asset = _ASSETS.get(asset_id)\n"
        "    if asset is None:\n"
        "        return None\n"
        "    return asset[_WIDTH], asset[_HEIGHT]\n"
        "\n"
        "\n"
        "def frame_count(asset_id):\n"
        '    """Return the number of frames in a generated asset."""\n'
        "    asset = _ASSETS.get(asset_id)\n"
        "    if asset is None:\n"
        "        return 0\n"
        "    return len(asset[_FRAMES])\n"
        "\n"
        "\n"
        "def draw_asset(draw, asset_id, x, y, frame=0, scale=1):\n"
        '    """Draw one compact generated asset."""\n'
        "    asset = _ASSETS.get(asset_id)\n"
        "    if asset is None:\n"
        "        return False\n"
        "    frames = asset[_FRAMES]\n"
        "    try:\n"
        "        frame = int(frame)\n"
        "    except (TypeError, ValueError):\n"
        "        frame = 0\n"
        "    if frame < 0 or frame >= len(frames):\n"
        "        frame = 0\n"
        "    try:\n"
        "        scale = max(1, int(scale))\n"
        "    except (TypeError, ValueError):\n"
        "        scale = 1\n"
        "    palette = asset[_PALETTE]\n"
        "    origin_x = asset[_ORIGIN_X]\n"
        "    origin_y = asset[_ORIGIN_Y]\n"
        "    for rect_x, rect_y, width, height, palette_index in frames[frame]:\n"
        "        draw._fill_rectangle(\n"
        "            x + (origin_x + rect_x) * scale,\n"
        "            y + (origin_y + rect_y) * scale,\n"
        "            width * scale,\n"
        "            height * scale,\n"
        "            palette[palette_index],\n"
        "        )\n"
        "    return True\n"
    )


def generate_asset_resource(
    project_id: str,
    generator_version: str,
    entries: Sequence[GeneratedAssetEntry | GeneratedRasterEntry | GeneratedAudioEntry],
    resource_name: str = "generated_assets.pga",
    *,
    format_version: int = 3,
) -> GeneratedAssetResource:
    """Generate an indexed, typed, bounded-memory PGA3 resource and runtime."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("A stable project ID is required")
    if not isinstance(generator_version, str) or not generator_version.strip():
        raise ValueError("A generator version is required")
    if format_version not in (2, 3):
        raise ValueError("PGA resource format must be version 2 or 3")
    if (
        not resource_name
        or "/" in resource_name
        or "\\" in resource_name
        or resource_name in {".", ".."}
    ):
        raise ValueError("Asset resource name must be one local filename")

    ordered_entries = sorted(tuple(entries), key=lambda entry: entry.asset_id)
    if len(ordered_entries) > 0xFFFF:
        raise ValueError("An asset resource supports at most 65535 assets")
    project_bytes = project_id.strip().encode("utf-8")
    if len(project_bytes) > MAXIMUM_RESOURCE_TEXT_BYTES:
        raise ValueError("Project ID is too long for the asset resource")
    resources: list[_ResourceAsset | _ResourceWav] = []
    seen_ids: set[str] = set()
    for entry in ordered_entries:
        if not isinstance(
            entry,
            (GeneratedAssetEntry, GeneratedRasterEntry, GeneratedAudioEntry),
        ):
            raise TypeError("PGA3 entries must be generated image or audio records")
        if not entry.asset_id or entry.asset_id in seen_ids:
            raise ValueError("Generated asset IDs must be nonempty and unique")
        if not isinstance(entry.name, str):
            raise TypeError("Generated asset names must be strings")
        seen_ids.add(entry.asset_id)
        asset_id_bytes = entry.asset_id.encode("utf-8")
        name_bytes = entry.name.encode("utf-8")
        if not asset_id_bytes or len(asset_id_bytes) > MAXIMUM_RESOURCE_TEXT_BYTES:
            raise ValueError("Generated asset ID is too long for the asset resource")
        if len(name_bytes) > MAXIMUM_RESOURCE_TEXT_BYTES:
            raise ValueError("Generated asset name is too long for the asset resource")
        if isinstance(entry, GeneratedAudioEntry):
            resources.append(_validated_resource_wav(entry, asset_id_bytes, name_bytes))
            continue
        if isinstance(entry, GeneratedRasterEntry):
            source_frames = tuple(entry.frames)
            if not source_frames:
                raise ValueError("At least one asset frame is required")
            first = source_frames[0]
            _validate_source_frame(first, first)
            for source_frame in source_frames:
                _validate_source_frame(source_frame, first)
            _validate_durations(entry.durations, len(source_frames))
            width = first.width
            height = first.height
            origin_x = first.origin_x
            origin_y = first.origin_y
            durations = entry.durations
        else:
            validate_asset(entry.asset)
            source_frames = tuple(
                reconstruct_asset(entry.asset, frame_index)
                for frame_index in range(len(entry.asset.frames))
            )
            width = entry.asset.width
            height = entry.asset.height
            origin_x = entry.asset.origin_x
            origin_y = entry.asset.origin_y
            durations = entry.asset.durations
        if len(source_frames) > 0xFFFF:
            raise ValueError("An asset supports at most 65535 PGA3 frames")
        _validate_integer(origin_x, "Asset origin X")
        _validate_integer(origin_y, "Asset origin Y")
        if not -0x80000000 <= origin_x <= 0x7FFFFFFF:
            raise ValueError("Asset origin X exceeds the PGA3 signed 32-bit limit")
        if not -0x80000000 <= origin_y <= 0x7FFFFFFF:
            raise ValueError("Asset origin Y exceeds the PGA3 signed 32-bit limit")
        if any(duration > 0xFFFFFFFF for duration in durations):
            raise ValueError("Asset duration exceeds the PGA3 32-bit limit")
        resources.append(
            _ResourceAsset(
                entry.asset_id,
                asset_id_bytes,
                name_bytes,
                width,
                height,
                origin_x,
                origin_y,
                tuple(durations),
                tuple(_rgb565_resource_frame(frame) for frame in source_frames),
            )
        )

    if format_version == 2 and any(
        isinstance(resource, _ResourceWav) for resource in resources
    ):
        raise ValueError("PGA2 does not support WAV resources")
    data = (
        _assemble_pga2(
            project_bytes,
            [
                resource
                for resource in resources
                if isinstance(resource, _ResourceAsset)
            ],
        )
        if format_version == 2
        else _assemble_pga3(project_bytes, resources)
    )
    module_builder = (
        _pga2_resource_module_source if format_version == 2 else _resource_module_source
    )
    module_source = module_builder(
        project_id.strip(),
        generator_version.strip(),
        resource_name,
        hashlib.sha256(data).hexdigest(),
    )
    return GeneratedAssetResource(
        module_source,
        data,
        resource_name,
        sum(isinstance(resource, _ResourceAsset) for resource in resources),
        sum(
            len(resource.frames)
            for resource in resources
            if isinstance(resource, _ResourceAsset)
        ),
        max(
            (
                ((resource.width + 7) // 8) + resource.width * 2
                for resource in resources
                if isinstance(resource, _ResourceAsset)
            ),
            default=0,
        ),
        sum(isinstance(resource, _ResourceWav) for resource in resources),
        ((resource_name, data),),
        "combined",
    )


def generate_individual_asset_resources(
    project_id: str,
    generator_version: str,
    entries: Sequence[GeneratedAssetEntry | GeneratedRasterEntry | GeneratedAudioEntry],
    resource_directory: str = INDIVIDUAL_RESOURCE_DIRECTORY,
) -> GeneratedAssetResource:
    """Generate one independently replaceable PGA image or WAV file per resource."""
    if not isinstance(generator_version, str) or not generator_version.strip():
        raise ValueError("A generator version is required")
    if (
        not resource_directory
        or "/" in resource_directory
        or "\\" in resource_directory
        or resource_directory in {".", ".."}
    ):
        raise ValueError("Individual resource directory must be one local name")
    ordered_entries = sorted(tuple(entries), key=lambda entry: entry.asset_id)
    if len(ordered_entries) > 0xFFFF:
        raise ValueError("An asset resource supports at most 65535 resources")
    if len({entry.asset_id for entry in ordered_entries}) != len(ordered_entries):
        raise ValueError("Generated resource IDs must be unique")
    if len({entry.asset_id.casefold() for entry in ordered_entries}) != len(
        ordered_entries
    ):
        raise ValueError(
            "Individual resource IDs must remain unique on case-insensitive storage"
        )
    files: list[tuple[str, bytes]] = [
        (
            f"{resource_directory}/{INDIVIDUAL_RESOURCE_MARKER}",
            _individual_resource_marker(project_id),
        )
    ]
    image_count = 0
    frame_count = 0
    audio_count = 0
    maximum_row_bytes = 0
    for entry in ordered_entries:
        _validate_individual_resource_id(entry.asset_id)
        if isinstance(entry, GeneratedAudioEntry) and (
            entry.loop_start_ms is not None or entry.loop_end_ms is not None
        ):
            raise ValueError(
                "Individual WAV files do not support PGA loop metadata; use "
                "combined PGA3 storage for explicit loop points"
            )
        single = generate_asset_resource(
            project_id,
            generator_version,
            (entry,),
        )
        if isinstance(entry, GeneratedAudioEntry):
            files.append((f"{resource_directory}/{entry.asset_id}.wav", entry.data))
            audio_count += 1
            continue
        files.append((f"{resource_directory}/{entry.asset_id}.pga", single.data))
        image_count += single.asset_count
        frame_count += single.frame_count
        maximum_row_bytes = max(maximum_row_bytes, single.maximum_row_bytes)
    module_source = _individual_resource_module_source(
        project_id.strip(),
        generator_version.strip(),
        resource_directory,
    )
    return GeneratedAssetResource(
        module_source,
        b"",
        resource_directory,
        image_count,
        frame_count,
        maximum_row_bytes,
        audio_count,
        tuple(files),
        "individual",
    )


def _validate_individual_resource_id(asset_id: object) -> str:
    """Require a readable cross-platform filename-safe stable resource ID."""
    if not isinstance(asset_id, str) or not asset_id:
        raise ValueError("Individual resource IDs must be nonempty strings")
    encoded = asset_id.encode("utf-8")
    if len(encoded) > MAXIMUM_INDIVIDUAL_RESOURCE_ID_BYTES:
        raise ValueError("Individual resource ID is too long for a portable filename")
    if not asset_id[0].isalnum() or any(
        not (character.isascii() and (character.isalnum() or character in "_.-"))
        for character in asset_id
    ):
        raise ValueError(
            "Individual resource IDs may contain only ASCII letters, digits, '_', "
            "'-', and '.', and must start with a letter or digit"
        )
    return asset_id


def _individual_resource_marker(project_id: str) -> bytes:
    """Return the ownership marker for one generated individual-resource folder."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("A stable project ID is required")
    project_bytes = project_id.strip().encode("utf-8")
    if len(project_bytes) > MAXIMUM_RESOURCE_TEXT_BYTES:
        raise ValueError("Project ID is too long for the asset resource")
    return (
        INDIVIDUAL_RESOURCE_MARKER_MAGIC
        + len(project_bytes).to_bytes(2, "little")
        + project_bytes
    )


def parse_individual_resource_marker_project(data: bytes) -> str | None:
    """Return the project owning an individual-resource directory marker."""
    if not isinstance(data, bytes) or data[:4] != INDIVIDUAL_RESOURCE_MARKER_MAGIC:
        return None
    if len(data) < 6:
        return None
    length = int.from_bytes(data[4:6], "little")
    if length < 1 or length > MAXIMUM_RESOURCE_TEXT_BYTES or len(data) != 6 + length:
        return None
    try:
        return data[6:].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _validated_resource_wav(
    entry: GeneratedAudioEntry,
    asset_id_bytes: bytes,
    name_bytes: bytes,
) -> _ResourceWav:
    """Validate one WAV record before deterministic PGA3 assembly."""
    if not isinstance(entry.data, bytes) or not entry.data:
        raise ValueError("PGA3 WAV data must be nonempty bytes")
    if entry.data[:4] != b"RIFF" or entry.data[8:12] != b"WAVE":
        raise ValueError("PGA3 audio entries must contain a complete RIFF/WAVE file")
    declared_size = int.from_bytes(entry.data[4:8], "little") + 8
    if declared_size != len(entry.data):
        raise ValueError("PGA3 WAV RIFF size does not match the complete file")
    try:
        with wave.open(io.BytesIO(entry.data), "rb") as source:
            if source.getcomptype() != "NONE":
                raise ValueError("PGA3 WAV audio must use uncompressed PCM")
            channels = source.getnchannels()
            sample_rate = source.getframerate()
            sample_width = source.getsampwidth()
            bits_per_sample = sample_width * 8
            frame_count = source.getnframes()
            if sample_rate < 1:
                raise ValueError("PGA3 WAV sample rate must be positive")
            samples = source.readframes(frame_count)
            if len(samples) != frame_count * channels * sample_width:
                raise ValueError("PGA3 WAV sample payload is truncated")
    except (EOFError, ValueError, wave.Error) as error:
        raise ValueError("PGA3 audio entry is not a valid PCM WAV file") from error
    if bits_per_sample not in (8, 16, 24):
        raise ValueError("PGA3 WAV samples must be 8, 16, or 24 bits")
    duration_ms = (frame_count * 1000 + sample_rate // 2) // sample_rate
    supplied = (
        (entry.sample_rate, sample_rate, "sample rate"),
        (entry.channels, channels, "channel count"),
        (entry.bits_per_sample, bits_per_sample, "sample width"),
        (entry.duration_ms, duration_ms, "duration"),
    )
    for value, actual, label in supplied:
        if value and value != actual:
            raise ValueError(f"PGA3 WAV {label} does not match its RIFF metadata")
    for value, label, maximum in (
        (sample_rate, "sample rate", 0xFFFFFFFF),
        (channels, "channel count", 0xFF),
        (duration_ms, "duration", 0xFFFFFFFF),
    ):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"PGA3 WAV {label} is outside its encoded limit")
    if channels not in (1, 2):
        raise ValueError("PGA3 WAV channels must be mono or stereo")
    loop_values = (entry.loop_start_ms, entry.loop_end_ms)
    if (loop_values[0] is None) != (loop_values[1] is None):
        raise ValueError("PGA3 WAV loop points must be both present or both absent")
    if loop_values[0] is not None:
        start, end = loop_values
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= 0xFFFFFFFE
            or end > duration_ms
        ):
            raise ValueError("PGA3 WAV loop points are invalid")
    return _ResourceWav(
        entry.asset_id,
        asset_id_bytes,
        name_bytes,
        channels,
        bits_per_sample,
        sample_rate,
        duration_ms,
        entry.loop_start_ms,
        entry.loop_end_ms,
        entry.data,
    )


def parse_asset_resource_project(data: bytes) -> str | None:
    """Return the owning project ID from a recognized binary resource."""
    if not isinstance(data, bytes) or data[:4] not in SUPPORTED_ASSET_RESOURCE_MAGICS:
        return None
    header_size = 6
    if len(data) < header_size:
        return None
    length = int.from_bytes(data[4:header_size], "little")
    if length > MAXIMUM_RESOURCE_TEXT_BYTES:
        return None
    end = header_size + length
    if end > len(data):
        return None
    try:
        return data[header_size:end].decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode_asset_resource(data: bytes) -> DecodedAssetResource:
    """Strictly decode a standalone PGA2 or PGA3 resource for desktop use."""
    if not isinstance(data, bytes):
        raise TypeError("PGA resource data must be bytes")
    if data[:4] == LEGACY_ASSET_RESOURCE_MAGIC:
        raise ValueError(
            "PGA1 cannot be imported by itself; use its original project or "
            "regenerate it as PGA3 first"
        )
    if data[:4] == PGA2_ASSET_RESOURCE_MAGIC:
        return _decode_pga2_asset_resource(data)
    if data[:4] == ASSET_RESOURCE_MAGIC:
        return _decode_pga3_asset_resource(data)
    raise ValueError("Selected file is not a supported PGA2 or PGA3 resource")


def _decode_pga2_asset_resource(data: bytes) -> DecodedAssetResource:
    """Strictly decode one complete PGA2 resource into editable pixel assets.

    PGA1 deliberately is not accepted: its standalone payload does not contain the
    complete asset metadata needed to reconstruct independent editor assets.
    """
    if data[:4] != PGA2_ASSET_RESOURCE_MAGIC:
        raise ValueError("Selected file is not a PGA2 asset resource")

    def read_integer(
        offset: int,
        size: int,
        limit: int,
        *,
        signed: bool = False,
    ) -> int:
        end = offset + size
        if offset < 0 or end > limit:
            raise ValueError("PGA2 resource is truncated")
        return int.from_bytes(data[offset:end], "little", signed=signed)

    def read_text(
        offset: int,
        limit: int,
        label: str,
        *,
        required: bool,
    ) -> tuple[str, bytes, int]:
        length = read_integer(offset, 2, limit)
        offset += 2
        if length > MAXIMUM_RESOURCE_TEXT_BYTES or (required and length == 0):
            raise ValueError(f"PGA2 {label} length is invalid")
        end = offset + length
        if end > limit:
            raise ValueError("PGA2 resource is truncated")
        raw = data[offset:end]
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"PGA2 {label} is not valid UTF-8") from error
        return value, raw, end

    project_id, unused_project_bytes, position = read_text(
        4, len(data), "project ID", required=True
    )
    del unused_project_bytes
    asset_count = read_integer(position, 2, len(data))
    payload_start = read_integer(position + 2, 4, len(data))
    total_size = read_integer(position + 6, 4, len(data))
    index_start = position + 10
    index_end = index_start + asset_count * RESOURCE_INDEX_ENTRY_SIZE
    if total_size != len(data):
        raise ValueError("PGA2 total-size field does not match the selected file")
    if index_end > payload_start or payload_start > total_size:
        raise ValueError("PGA2 index or payload bounds are invalid")

    index: list[tuple[int, int]] = []
    for index_number in range(asset_count):
        offset = index_start + index_number * RESOURCE_INDEX_ENTRY_SIZE
        asset_hash = read_integer(offset, 4, index_end)
        record_offset = read_integer(offset + 4, 4, index_end)
        if not index_end <= record_offset < payload_start:
            raise ValueError("PGA2 index points outside its metadata records")
        index.append((asset_hash, record_offset))
    if len({record_offset for unused_hash, record_offset in index}) != asset_count:
        raise ValueError("PGA2 index contains duplicate record offsets")

    records: dict[int, _DecodedResourceRecord] = {}
    identifiers: set[str] = set()
    for record_offset in sorted(record_offset for unused_hash, record_offset in index):
        asset_id, asset_id_bytes, cursor = read_text(
            record_offset, payload_start, "asset ID", required=True
        )
        name, unused_name_bytes, cursor = read_text(
            cursor, payload_start, "asset name", required=False
        )
        del unused_name_bytes
        width = read_integer(cursor, 2, payload_start)
        height = read_integer(cursor + 2, 2, payload_start)
        origin_x = read_integer(cursor + 4, 4, payload_start, signed=True)
        origin_y = read_integer(cursor + 8, 4, payload_start, signed=True)
        frame_count = read_integer(cursor + 12, 2, payload_start)
        cursor += 14
        _validate_dimensions(width, height)
        if frame_count < 1:
            raise ValueError("PGA2 asset must contain at least one frame")
        descriptor_end = cursor + frame_count * RESOURCE_FRAME_RECORD_SIZE
        if descriptor_end > payload_start:
            raise ValueError("PGA2 frame descriptors cross into pixel payload data")

        expected_length = height * (((width + 7) // 8) + width * 2)
        spans: list[tuple[int, int]] = []
        durations: list[int] = []
        for frame_number in range(frame_count):
            descriptor = cursor + frame_number * RESOURCE_FRAME_RECORD_SIZE
            frame_offset = read_integer(descriptor, 4, payload_start)
            frame_length = read_integer(descriptor + 4, 4, payload_start)
            duration = read_integer(descriptor + 8, 4, payload_start)
            if frame_length != expected_length:
                raise ValueError(
                    "PGA2 frame payload length does not match its dimensions"
                )
            if frame_offset < payload_start or frame_offset + frame_length > total_size:
                raise ValueError("PGA2 frame payload points outside the selected file")
            spans.append((frame_offset, frame_length))
            durations.append(duration)
        if any(durations) and not all(durations):
            raise ValueError("PGA2 animation timing metadata is incomplete")
        if asset_id in identifiers:
            raise ValueError("PGA2 asset IDs must be unique")
        identifiers.add(asset_id)
        records[record_offset] = _DecodedResourceRecord(
            asset_id,
            asset_id_bytes,
            name,
            width,
            height,
            origin_x,
            origin_y,
            tuple(durations) if any(durations) else (),
            descriptor_end,
            tuple(spans),
        )

    expected_record_start = index_end
    for record_offset in sorted(records):
        if record_offset != expected_record_start:
            raise ValueError("PGA2 metadata records contain a gap or overlap")
        expected_record_start = records[record_offset].record_end
    if expected_record_start != payload_start:
        raise ValueError("PGA2 metadata does not end at the declared payload start")

    expected_index = [
        (asset_hash, record_offset)
        for asset_hash, unused_id, record_offset in sorted(
            (
                (
                    _asset_id_hash(record.asset_id_bytes),
                    record.asset_id_bytes,
                    record_offset,
                )
                for record_offset, record in records.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
    ]
    if index != expected_index:
        raise ValueError("PGA2 hash index does not match its asset records")

    ordered_records = [records[offset] for offset in sorted(records)]
    asset_id_order = [record.asset_id_bytes for record in ordered_records]
    if asset_id_order != sorted(asset_id_order):
        raise ValueError("PGA2 asset records are not in stable ID order")
    expected_payload_offset = payload_start
    for record in ordered_records:
        for frame_offset, frame_length in record.frame_spans:
            if frame_offset != expected_payload_offset:
                raise ValueError("PGA2 frame payloads contain a gap or overlap")
            expected_payload_offset += frame_length
    if expected_payload_offset != total_size:
        raise ValueError("PGA2 frame payloads do not fill the declared resource")

    decoded_entries: list[GeneratedRasterEntry] = []
    for record in ordered_records:
        frames: list[PixelArt] = []
        mask_size = (record.width + 7) // 8
        row_size = mask_size + record.width * 2
        for frame_offset, unused_length in record.frame_spans:
            pixels: list[int | None] = []
            for row in range(record.height):
                row_offset = frame_offset + row * row_size
                mask = data[row_offset : row_offset + mask_size]
                pixel_offset = row_offset + mask_size
                for x in range(record.width):
                    if not mask[x // 8] & (0x80 >> (x % 8)):
                        pixels.append(None)
                        continue
                    color_offset = pixel_offset + x * 2
                    pixels.append(
                        int.from_bytes(data[color_offset : color_offset + 2], "little")
                    )
            frames.append(
                PixelArt(
                    record.width,
                    record.height,
                    record.origin_x,
                    record.origin_y,
                    pixels,
                )
            )
        decoded_entries.append(
            GeneratedRasterEntry(
                record.asset_id,
                record.name,
                tuple(frames),
                record.durations,
            )
        )
    return DecodedAssetResource(project_id, tuple(decoded_entries), (), 2)


def _decode_pga3_asset_resource(data: bytes) -> DecodedAssetResource:
    """Strictly decode one complete typed PGA3 resource."""

    def read_integer(
        offset: int,
        size: int,
        limit: int,
        *,
        signed: bool = False,
    ) -> int:
        end = offset + size
        if offset < 0 or end > limit:
            raise ValueError("PGA3 resource is truncated")
        return int.from_bytes(data[offset:end], "little", signed=signed)

    def read_text(
        offset: int,
        limit: int,
        label: str,
        *,
        required: bool,
    ) -> tuple[str, bytes, int]:
        length = read_integer(offset, 2, limit)
        offset += 2
        if length > MAXIMUM_RESOURCE_TEXT_BYTES or (required and length == 0):
            raise ValueError(f"PGA3 {label} length is invalid")
        end = offset + length
        if end > limit:
            raise ValueError("PGA3 resource is truncated")
        raw = data[offset:end]
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"PGA3 {label} is not valid UTF-8") from error
        return value, raw, end

    project_id, unused_project_bytes, position = read_text(
        4, len(data), "project ID", required=True
    )
    del unused_project_bytes
    resource_count = read_integer(position, 2, len(data))
    payload_start = read_integer(position + 2, 4, len(data))
    total_size = read_integer(position + 6, 4, len(data))
    index_start = position + 10
    index_end = index_start + resource_count * RESOURCE_INDEX_ENTRY_SIZE
    if total_size != len(data):
        raise ValueError("PGA3 total-size field does not match the selected file")
    if index_end > payload_start or payload_start > total_size:
        raise ValueError("PGA3 index or payload bounds are invalid")

    index: list[tuple[int, int]] = []
    for index_number in range(resource_count):
        offset = index_start + index_number * RESOURCE_INDEX_ENTRY_SIZE
        asset_hash = read_integer(offset, 4, index_end)
        record_offset = read_integer(offset + 4, 4, index_end)
        if not index_end <= record_offset < payload_start:
            raise ValueError("PGA3 index points outside its metadata records")
        index.append((asset_hash, record_offset))
    if len({record_offset for unused_hash, record_offset in index}) != resource_count:
        raise ValueError("PGA3 index contains duplicate record offsets")

    record_type = _DecodedPga3ImageRecord | _DecodedPga3WavRecord
    records: dict[int, record_type] = {}
    identifiers: set[str] = set()
    for record_offset in sorted(record_offset for unused_hash, record_offset in index):
        kind = read_integer(record_offset, 1, payload_start)
        flags = read_integer(record_offset + 1, 1, payload_start)
        if kind not in (RESOURCE_TYPE_IMAGE, RESOURCE_TYPE_WAV) or flags != 0:
            raise ValueError("PGA3 resource type or flags are unsupported")
        asset_id, asset_id_bytes, cursor = read_text(
            record_offset + 2, payload_start, "resource ID", required=True
        )
        name, unused_name_bytes, cursor = read_text(
            cursor, payload_start, "resource name", required=False
        )
        del unused_name_bytes
        if asset_id in identifiers:
            raise ValueError("PGA3 resource IDs must be unique")
        identifiers.add(asset_id)

        if kind == RESOURCE_TYPE_IMAGE:
            width = read_integer(cursor, 2, payload_start)
            height = read_integer(cursor + 2, 2, payload_start)
            origin_x = read_integer(cursor + 4, 4, payload_start, signed=True)
            origin_y = read_integer(cursor + 8, 4, payload_start, signed=True)
            frame_count = read_integer(cursor + 12, 2, payload_start)
            cursor += 14
            _validate_dimensions(width, height)
            if frame_count < 1:
                raise ValueError("PGA3 image must contain at least one frame")
            descriptor_end = cursor + frame_count * RESOURCE_FRAME_RECORD_SIZE
            if descriptor_end > payload_start:
                raise ValueError("PGA3 frame descriptors cross into payload data")
            expected_length = height * (((width + 7) // 8) + width * 2)
            spans: list[tuple[int, int]] = []
            durations: list[int] = []
            for frame_number in range(frame_count):
                descriptor = cursor + frame_number * RESOURCE_FRAME_RECORD_SIZE
                frame_offset = read_integer(descriptor, 4, payload_start)
                frame_length = read_integer(descriptor + 4, 4, payload_start)
                duration = read_integer(descriptor + 8, 4, payload_start)
                if frame_length != expected_length:
                    raise ValueError(
                        "PGA3 frame payload length does not match its dimensions"
                    )
                if (
                    frame_offset < payload_start
                    or frame_offset + frame_length > total_size
                ):
                    raise ValueError("PGA3 frame payload points outside the resource")
                spans.append((frame_offset, frame_length))
                durations.append(duration)
            if any(durations) and not all(durations):
                raise ValueError("PGA3 animation timing metadata is incomplete")
            records[record_offset] = _DecodedPga3ImageRecord(
                asset_id,
                asset_id_bytes,
                name,
                width,
                height,
                origin_x,
                origin_y,
                tuple(durations) if any(durations) else (),
                descriptor_end,
                tuple(spans),
            )
            continue

        record_end = cursor + RESOURCE_WAV_RECORD_SIZE
        if record_end > payload_start:
            raise ValueError("PGA3 WAV metadata crosses into payload data")
        channels = read_integer(cursor, 1, payload_start)
        bits_per_sample = read_integer(cursor + 1, 1, payload_start)
        sample_rate = read_integer(cursor + 2, 4, payload_start)
        duration_ms = read_integer(cursor + 6, 4, payload_start)
        loop_start_raw = read_integer(cursor + 10, 4, payload_start)
        loop_end_raw = read_integer(cursor + 14, 4, payload_start)
        audio_offset = read_integer(cursor + 18, 4, payload_start)
        audio_length = read_integer(cursor + 22, 4, payload_start)
        if channels not in (1, 2) or bits_per_sample not in (8, 16, 24):
            raise ValueError("PGA3 WAV channel or sample-width metadata is invalid")
        if sample_rate < 1 or audio_length < 12:
            raise ValueError("PGA3 WAV sample rate or payload length is invalid")
        if audio_offset < payload_start or audio_offset + audio_length > total_size:
            raise ValueError("PGA3 WAV payload points outside the resource")
        if (loop_start_raw == 0xFFFFFFFF) != (loop_end_raw == 0xFFFFFFFF):
            raise ValueError("PGA3 WAV loop metadata is incomplete")
        loop_start = None if loop_start_raw == 0xFFFFFFFF else loop_start_raw
        loop_end = None if loop_end_raw == 0xFFFFFFFF else loop_end_raw
        if loop_start is not None and not 0 <= loop_start < loop_end <= duration_ms:
            raise ValueError("PGA3 WAV loop metadata is invalid")
        records[record_offset] = _DecodedPga3WavRecord(
            asset_id,
            asset_id_bytes,
            name,
            channels,
            bits_per_sample,
            sample_rate,
            duration_ms,
            loop_start,
            loop_end,
            record_end,
            (audio_offset, audio_length),
        )

    expected_record_start = index_end
    for record_offset in sorted(records):
        if record_offset != expected_record_start:
            raise ValueError("PGA3 metadata records contain a gap or overlap")
        expected_record_start = records[record_offset].record_end
    if expected_record_start != payload_start:
        raise ValueError("PGA3 metadata does not end at the declared payload start")

    expected_index = [
        (asset_hash, record_offset)
        for asset_hash, unused_id, record_offset in sorted(
            (
                (
                    _asset_id_hash(record.asset_id_bytes),
                    record.asset_id_bytes,
                    record_offset,
                )
                for record_offset, record in records.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
    ]
    if index != expected_index:
        raise ValueError("PGA3 hash index does not match its resource records")
    ordered_records = [records[offset] for offset in sorted(records)]
    if [record.asset_id_bytes for record in ordered_records] != sorted(
        record.asset_id_bytes for record in ordered_records
    ):
        raise ValueError("PGA3 resource records are not in stable ID order")

    expected_payload_offset = payload_start
    for record in ordered_records:
        spans = (
            record.frame_spans
            if isinstance(record, _DecodedPga3ImageRecord)
            else (record.data_span,)
        )
        for payload_offset, payload_length in spans:
            if payload_offset != expected_payload_offset:
                raise ValueError("PGA3 payloads contain a gap or overlap")
            expected_payload_offset += payload_length
    if expected_payload_offset != total_size:
        raise ValueError("PGA3 payloads do not fill the declared resource")

    decoded_images: list[GeneratedRasterEntry] = []
    decoded_audio: list[GeneratedAudioEntry] = []
    for record in ordered_records:
        if isinstance(record, _DecodedPga3ImageRecord):
            frames: list[PixelArt] = []
            mask_size = (record.width + 7) // 8
            row_size = mask_size + record.width * 2
            for frame_offset, unused_length in record.frame_spans:
                pixels: list[int | None] = []
                for row in range(record.height):
                    row_offset = frame_offset + row * row_size
                    mask = data[row_offset : row_offset + mask_size]
                    pixel_offset = row_offset + mask_size
                    for x in range(record.width):
                        if not mask[x // 8] & (0x80 >> (x % 8)):
                            pixels.append(None)
                            continue
                        color_offset = pixel_offset + x * 2
                        pixels.append(
                            int.from_bytes(
                                data[color_offset : color_offset + 2], "little"
                            )
                        )
                frames.append(
                    PixelArt(
                        record.width,
                        record.height,
                        record.origin_x,
                        record.origin_y,
                        pixels,
                    )
                )
            decoded_images.append(
                GeneratedRasterEntry(
                    record.asset_id,
                    record.name,
                    tuple(frames),
                    record.durations,
                )
            )
            continue
        audio_offset, audio_length = record.data_span
        entry = GeneratedAudioEntry(
            record.asset_id,
            record.name,
            data[audio_offset : audio_offset + audio_length],
            record.sample_rate,
            record.channels,
            record.bits_per_sample,
            record.duration_ms,
            record.loop_start_ms,
            record.loop_end_ms,
        )
        _validated_resource_wav(entry, record.asset_id_bytes, record.name.encode())
        decoded_audio.append(entry)
    return DecodedAssetResource(
        project_id,
        tuple(decoded_images),
        tuple(decoded_audio),
        3,
    )


def _assemble_pga3(
    project_bytes: bytes,
    resources: Sequence[_ResourceAsset | _ResourceWav],
) -> bytes:
    """Assemble deterministic PGA3 typed metadata and contiguous payloads."""
    header_size = 4 + 2 + len(project_bytes) + 2 + 4 + 4
    records_start = header_size + len(resources) * RESOURCE_INDEX_ENTRY_SIZE
    record_offsets: list[int] = []
    next_offset = records_start
    for resource in resources:
        record_offsets.append(next_offset)
        common_size = (
            2 + 2 + len(resource.asset_id_bytes) + 2 + len(resource.name_bytes)
        )
        if isinstance(resource, _ResourceAsset):
            next_offset += (
                common_size + 14 + len(resource.frames) * RESOURCE_FRAME_RECORD_SIZE
            )
        else:
            next_offset += common_size + RESOURCE_WAV_RECORD_SIZE
    payload_start = next_offset

    payload_spans: list[tuple[tuple[int, int], ...]] = []
    for resource in resources:
        payloads = (
            resource.frames
            if isinstance(resource, _ResourceAsset)
            else (resource.data,)
        )
        spans: list[tuple[int, int]] = []
        for payload in payloads:
            spans.append((next_offset, len(payload)))
            next_offset += len(payload)
        payload_spans.append(tuple(spans))
    if next_offset > 0xFFFFFFFF:
        raise ValueError("Asset resource exceeds the PGA3 four-gigabyte limit")

    data = bytearray(ASSET_RESOURCE_MAGIC)
    data.extend(len(project_bytes).to_bytes(2, "little"))
    data.extend(project_bytes)
    data.extend(len(resources).to_bytes(2, "little"))
    data.extend(payload_start.to_bytes(4, "little"))
    data.extend(next_offset.to_bytes(4, "little"))
    indexed_records = sorted(
        (
            (
                _asset_id_hash(resource.asset_id_bytes),
                resource.asset_id_bytes,
                record_offset,
            )
            for resource, record_offset in zip(resources, record_offsets, strict=True)
        ),
        key=lambda record: (record[0], record[1]),
    )
    for asset_hash, unused_asset_id, record_offset in indexed_records:
        data.extend(asset_hash.to_bytes(4, "little"))
        data.extend(record_offset.to_bytes(4, "little"))

    for resource, spans in zip(resources, payload_spans, strict=True):
        data.append(
            RESOURCE_TYPE_IMAGE
            if isinstance(resource, _ResourceAsset)
            else RESOURCE_TYPE_WAV
        )
        data.append(0)
        data.extend(len(resource.asset_id_bytes).to_bytes(2, "little"))
        data.extend(resource.asset_id_bytes)
        data.extend(len(resource.name_bytes).to_bytes(2, "little"))
        data.extend(resource.name_bytes)
        if isinstance(resource, _ResourceAsset):
            data.extend(resource.width.to_bytes(2, "little"))
            data.extend(resource.height.to_bytes(2, "little"))
            data.extend(resource.origin_x.to_bytes(4, "little", signed=True))
            data.extend(resource.origin_y.to_bytes(4, "little", signed=True))
            data.extend(len(resource.frames).to_bytes(2, "little"))
            for frame_index, (offset, length) in enumerate(spans):
                duration = resource.durations[frame_index] if resource.durations else 0
                data.extend(offset.to_bytes(4, "little"))
                data.extend(length.to_bytes(4, "little"))
                data.extend(duration.to_bytes(4, "little"))
            continue
        data.append(resource.channels)
        data.append(resource.bits_per_sample)
        data.extend(resource.sample_rate.to_bytes(4, "little"))
        data.extend(resource.duration_ms.to_bytes(4, "little"))
        data.extend(
            (
                resource.loop_start_ms
                if resource.loop_start_ms is not None
                else 0xFFFFFFFF
            ).to_bytes(4, "little")
        )
        data.extend(
            (
                resource.loop_end_ms if resource.loop_end_ms is not None else 0xFFFFFFFF
            ).to_bytes(4, "little")
        )
        data.extend(spans[0][0].to_bytes(4, "little"))
        data.extend(spans[0][1].to_bytes(4, "little"))
    if len(data) != payload_start:
        raise AssertionError("PGA3 metadata size calculation is inconsistent")
    for resource in resources:
        if isinstance(resource, _ResourceAsset):
            for frame in resource.frames:
                data.extend(frame)
        else:
            data.extend(resource.data)
    if len(data) != next_offset:
        raise AssertionError("PGA3 resource size calculation is inconsistent")
    return bytes(data)


def _assemble_pga2(project_bytes: bytes, assets: Sequence[_ResourceAsset]) -> bytes:
    """Assemble deterministic PGA2 header, index, records, and frame payloads."""
    header_size = 4 + 2 + len(project_bytes) + 2 + 4 + 4
    index_start = header_size
    records_start = index_start + len(assets) * RESOURCE_INDEX_ENTRY_SIZE
    record_offsets: list[int] = []
    next_offset = records_start
    for asset in assets:
        record_offsets.append(next_offset)
        next_offset += (
            2
            + len(asset.asset_id_bytes)
            + 2
            + len(asset.name_bytes)
            + 2
            + 2
            + 4
            + 4
            + 2
            + len(asset.frames) * RESOURCE_FRAME_RECORD_SIZE
        )
    payload_start = next_offset
    frame_spans: list[tuple[tuple[int, int], ...]] = []
    for asset in assets:
        spans: list[tuple[int, int]] = []
        for frame in asset.frames:
            spans.append((next_offset, len(frame)))
            next_offset += len(frame)
        frame_spans.append(tuple(spans))
    if next_offset > 0xFFFFFFFF:
        raise ValueError("Asset resource exceeds the PGA2 four-gigabyte limit")

    data = bytearray(PGA2_ASSET_RESOURCE_MAGIC)
    data.extend(len(project_bytes).to_bytes(2, "little"))
    data.extend(project_bytes)
    data.extend(len(assets).to_bytes(2, "little"))
    data.extend(payload_start.to_bytes(4, "little"))
    data.extend(next_offset.to_bytes(4, "little"))
    indexed_records = sorted(
        (
            (_asset_id_hash(asset.asset_id_bytes), asset.asset_id_bytes, record_offset)
            for asset, record_offset in zip(assets, record_offsets, strict=True)
        ),
        key=lambda record: (record[0], record[1]),
    )
    for asset_hash, unused_asset_id, record_offset in indexed_records:
        data.extend(asset_hash.to_bytes(4, "little"))
        data.extend(record_offset.to_bytes(4, "little"))
    for asset, spans in zip(assets, frame_spans, strict=True):
        data.extend(len(asset.asset_id_bytes).to_bytes(2, "little"))
        data.extend(asset.asset_id_bytes)
        data.extend(len(asset.name_bytes).to_bytes(2, "little"))
        data.extend(asset.name_bytes)
        data.extend(asset.width.to_bytes(2, "little"))
        data.extend(asset.height.to_bytes(2, "little"))
        data.extend(asset.origin_x.to_bytes(4, "little", signed=True))
        data.extend(asset.origin_y.to_bytes(4, "little", signed=True))
        data.extend(len(asset.frames).to_bytes(2, "little"))
        for frame_index, (offset, length) in enumerate(spans):
            duration = asset.durations[frame_index] if asset.durations else 0
            data.extend(offset.to_bytes(4, "little"))
            data.extend(length.to_bytes(4, "little"))
            data.extend(duration.to_bytes(4, "little"))
    if len(data) != payload_start:
        raise AssertionError("PGA2 metadata size calculation is inconsistent")
    for asset in assets:
        for frame in asset.frames:
            data.extend(frame)
    if len(data) != next_offset:
        raise AssertionError("PGA2 resource size calculation is inconsistent")
    return bytes(data)


def _asset_id_hash(asset_id: bytes) -> int:
    """Return the stable FNV-1a 32-bit hash used by the PGA2 index."""
    value = 0x811C9DC5
    for byte in asset_id:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def _rgb565_resource_frame(frame: PixelArt) -> bytes:
    """Encode one frame as interleaved opacity-mask and RGB565 rows."""
    mask_size = (frame.width + 7) // 8
    payload = bytearray()
    for y in range(frame.height):
        mask = bytearray(mask_size)
        pixels = bytearray(frame.width * 2)
        for x in range(frame.width):
            color = frame.pixel(x, y)
            if color is None:
                continue
            mask[x // 8] |= 0x80 >> (x % 8)
            offset = x * 2
            pixels[offset] = color & 0xFF
            pixels[offset + 1] = (color >> 8) & 0xFF
        payload.extend(mask)
        payload.extend(pixels)
    return bytes(payload)


def _pga2_resource_module_source(
    project_id: str,
    generator_version: str,
    resource_name: str,
    resource_fingerprint: str,
) -> str:
    """Return the retained constant-size runtime used by legacy PGA2 fixtures."""
    return (
        "# @picoware-generated structure=1\n"
        "# @picoware-generated role=assets\n"
        f"# @picoware-generated project={project_id}\n"
        f"# @picoware-generator version={generator_version}\n"
        "# This file is editor-owned. Regenerate it instead of editing it manually.\n"
        "\n"
        f"_RESOURCE_NAME = {resource_name!r}\n"
        f"_RESOURCE_SHA256 = {resource_fingerprint!r}\n"
        f"_PROJECT_ID = {project_id!r}\n"
        "_MAGIC = b'PGA2'\n"
        "_MAX_TEXT = 4096\n"
        "_INDEX_ENTRY_SIZE = 8\n"
        "_FRAME_RECORD_SIZE = 12\n"
        "\n"
        "\n"
        "def _resource_path():\n"
        "    module_file = globals().get('__file__', '')\n"
        "    base = module_file.rsplit('/', 1)[0] if '/' in module_file else ''\n"
        "    return (base + '/' if base else '') + _RESOURCE_NAME\n"
        "\n"
        "\n"
        "def _read_exact(handle, size):\n"
        "    data = handle.read(size)\n"
        "    return data if data is not None and len(data) == size else None\n"
        "\n"
        "\n"
        "def _u16(data, offset=0):\n"
        "    return data[offset] | (data[offset + 1] << 8)\n"
        "\n"
        "\n"
        "def _u32(data, offset=0):\n"
        "    return (data[offset] | (data[offset + 1] << 8) |\n"
        "            (data[offset + 2] << 16) | (data[offset + 3] << 24))\n"
        "\n"
        "\n"
        "def _i32(data, offset=0):\n"
        "    value = _u32(data, offset)\n"
        "    return value - 0x100000000 if value & 0x80000000 else value\n"
        "\n"
        "\n"
        "def _asset_hash(asset_id):\n"
        "    value = 0x811C9DC5\n"
        "    for byte in asset_id:\n"
        "        value ^= byte\n"
        "        value = (value * 0x01000193) & 0xFFFFFFFF\n"
        "    return value\n"
        "\n"
        "\n"
        "def _open_resource():\n"
        "    try:\n"
        "        handle = open(_resource_path(), 'rb')\n"
        "    except OSError:\n"
        "        return None\n"
        "    try:\n"
        "        if _read_exact(handle, 4) != _MAGIC:\n"
        "            raise ValueError\n"
        "        raw = _read_exact(handle, 2)\n"
        "        if raw is None:\n"
        "            raise ValueError\n"
        "        project_size = _u16(raw)\n"
        "        if project_size > _MAX_TEXT:\n"
        "            raise ValueError\n"
        "        project = _read_exact(handle, project_size)\n"
        "        if project is None or project.decode('utf-8') != _PROJECT_ID:\n"
        "            raise ValueError\n"
        "        raw = _read_exact(handle, 10)\n"
        "        if raw is None:\n"
        "            raise ValueError\n"
        "        asset_count = _u16(raw)\n"
        "        payload_start = _u32(raw, 2)\n"
        "        total_size = _u32(raw, 6)\n"
        "        index_start = handle.tell()\n"
        "        index_end = index_start + asset_count * _INDEX_ENTRY_SIZE\n"
        "        if index_end > payload_start or payload_start > total_size:\n"
        "            raise ValueError\n"
        "        return (handle, asset_count, index_start, index_end,\n"
        "                payload_start, total_size)\n"
        "    except Exception:\n"
        "        handle.close()\n"
        "        return None\n"
        "\n"
        "\n"
        "def _index_entry(handle, index_start, index, index_end):\n"
        "    offset = index_start + index * _INDEX_ENTRY_SIZE\n"
        "    if offset < index_start or offset + _INDEX_ENTRY_SIZE > index_end:\n"
        "        return None\n"
        "    handle.seek(offset)\n"
        "    raw = _read_exact(handle, _INDEX_ENTRY_SIZE)\n"
        "    return None if raw is None else (_u32(raw), _u32(raw, 4))\n"
        "\n"
        "\n"
        "def _record(handle, record_offset, wanted_id, index_end, payload_start):\n"
        "    if record_offset < index_end or record_offset >= payload_start:\n"
        "        return None\n"
        "    handle.seek(record_offset)\n"
        "    raw = _read_exact(handle, 2)\n"
        "    if raw is None:\n"
        "        return None\n"
        "    asset_id_size = _u16(raw)\n"
        "    if asset_id_size < 1 or asset_id_size > _MAX_TEXT:\n"
        "        return None\n"
        "    asset_id = _read_exact(handle, asset_id_size)\n"
        "    if asset_id is None or asset_id != wanted_id:\n"
        "        return None\n"
        "    raw = _read_exact(handle, 2)\n"
        "    if raw is None:\n"
        "        return None\n"
        "    name_size = _u16(raw)\n"
        "    if name_size > _MAX_TEXT or handle.tell() + name_size + 14 > payload_start:\n"
        "        return None\n"
        "    handle.seek(handle.tell() + name_size)\n"
        "    raw = _read_exact(handle, 14)\n"
        "    if raw is None:\n"
        "        return None\n"
        "    width = _u16(raw)\n"
        "    height = _u16(raw, 2)\n"
        "    origin_x = _i32(raw, 4)\n"
        "    origin_y = _i32(raw, 8)\n"
        "    frames = _u16(raw, 12)\n"
        "    frame_records = handle.tell()\n"
        "    if (width < 1 or width > 320 or height < 1 or height > 320 or\n"
        "            frames < 1 or frame_records + frames * _FRAME_RECORD_SIZE > payload_start):\n"
        "        return None\n"
        "    return (width, height, origin_x, origin_y, frames, frame_records)\n"
        "\n"
        "\n"
        "def _find_asset(opened, asset_id):\n"
        "    try:\n"
        "        wanted_id = asset_id.encode('utf-8')\n"
        "    except Exception:\n"
        "        return None\n"
        "    if not wanted_id or len(wanted_id) > _MAX_TEXT:\n"
        "        return None\n"
        "    handle, count, index_start, index_end, payload_start, unused_total = opened\n"
        "    wanted_hash = _asset_hash(wanted_id)\n"
        "    low = 0\n"
        "    high = count\n"
        "    while low < high:\n"
        "        middle = (low + high) // 2\n"
        "        entry = _index_entry(handle, index_start, middle, index_end)\n"
        "        if entry is None:\n"
        "            return None\n"
        "        if entry[0] < wanted_hash:\n"
        "            low = middle + 1\n"
        "        else:\n"
        "            high = middle\n"
        "    while low < count:\n"
        "        entry = _index_entry(handle, index_start, low, index_end)\n"
        "        if entry is None or entry[0] != wanted_hash:\n"
        "            return None\n"
        "        asset = _record(handle, entry[1], wanted_id, index_end, payload_start)\n"
        "        if asset is not None:\n"
        "            return asset\n"
        "        low += 1\n"
        "    return None\n"
        "\n"
        "\n"
        "def _asset_metadata(asset_id):\n"
        "    opened = _open_resource()\n"
        "    if opened is None:\n"
        "        return None\n"
        "    try:\n"
        "        return _find_asset(opened, asset_id)\n"
        "    finally:\n"
        "        opened[0].close()\n"
        "\n"
        "\n"
        "def has_asset(asset_id):\n"
        "    return _asset_metadata(asset_id) is not None\n"
        "\n"
        "\n"
        "def asset_size(asset_id):\n"
        "    asset = _asset_metadata(asset_id)\n"
        "    return None if asset is None else (asset[0], asset[1])\n"
        "\n"
        "\n"
        "def frame_count(asset_id):\n"
        "    asset = _asset_metadata(asset_id)\n"
        "    return 0 if asset is None else asset[4]\n"
        "\n"
        "\n"
        "def draw_asset(draw, asset_id, x, y, frame=0, scale=1):\n"
        "    opened = _open_resource()\n"
        "    if opened is None:\n"
        "        return False\n"
        "    handle = opened[0]\n"
        "    try:\n"
        "        asset = _find_asset(opened, asset_id)\n"
        "        if asset is None:\n"
        "            return False\n"
        "        try:\n"
        "            frame = int(frame)\n"
        "        except (TypeError, ValueError):\n"
        "            frame = 0\n"
        "        if frame < 0 or frame >= asset[4]:\n"
        "            frame = 0\n"
        "        try:\n"
        "            scale = max(1, int(scale))\n"
        "        except (TypeError, ValueError):\n"
        "            scale = 1\n"
        "        width = asset[0]\n"
        "        height = asset[1]\n"
        "        mask_size = (width + 7) // 8\n"
        "        row_size = width * 2\n"
        "        handle.seek(asset[5] + frame * _FRAME_RECORD_SIZE)\n"
        "        raw = _read_exact(handle, _FRAME_RECORD_SIZE)\n"
        "        if raw is None:\n"
        "            return False\n"
        "        offset = _u32(raw)\n"
        "        expected_size = _u32(raw, 4)\n"
        "        if (expected_size != height * (mask_size + row_size) or\n"
        "                offset < opened[4] or offset + expected_size > opened[5]):\n"
        "            return False\n"
        "        handle.seek(offset)\n"
        "        base_x = x + asset[2] * scale\n"
        "        base_y = y + asset[3] * scale\n"
        "        for row in range(height):\n"
        "            mask = _read_exact(handle, mask_size)\n"
        "            pixels = _read_exact(handle, row_size)\n"
        "            if mask is None or pixels is None:\n"
        "                return False\n"
        "            column = 0\n"
        "            while column < width:\n"
        "                while column < width and not (mask[column // 8] & (0x80 >> (column % 8))):\n"
        "                    column += 1\n"
        "                start = column\n"
        "                while column < width and (mask[column // 8] & (0x80 >> (column % 8))):\n"
        "                    column += 1\n"
        "                if start == column:\n"
        "                    continue\n"
        "                if scale == 1:\n"
        "                    draw._bytearray(\n"
        "                        base_x + start, base_y + row, column - start, 1,\n"
        "                        memoryview(pixels)[start * 2:column * 2], False,\n"
        "                    )\n"
        "                    continue\n"
        "                run = start\n"
        "                while run < column:\n"
        "                    pixel_offset = run * 2\n"
        "                    color = pixels[pixel_offset] | (pixels[pixel_offset + 1] << 8)\n"
        "                    end = run + 1\n"
        "                    while end < column:\n"
        "                        next_offset = end * 2\n"
        "                        next_color = pixels[next_offset] | (pixels[next_offset + 1] << 8)\n"
        "                        if next_color != color:\n"
        "                            break\n"
        "                        end += 1\n"
        "                    draw._fill_rectangle(\n"
        "                        base_x + run * scale, base_y + row * scale,\n"
        "                        (end - run) * scale, scale, color,\n"
        "                    )\n"
        "                    run = end\n"
        "        return True\n"
        "    finally:\n"
        "        handle.close()\n"
    )


def _resource_module_source(
    project_id: str,
    generator_version: str,
    resource_name: str,
    resource_fingerprint: str,
) -> str:
    """Return the constant-size MicroPython runtime for typed PGA3 resources."""
    header = (
        "# @picoware-generated structure=1\n"
        "# @picoware-generated role=assets\n"
        f"# @picoware-generated project={project_id}\n"
        f"# @picoware-generator version={generator_version}\n"
        "# This file is editor-owned. Regenerate it instead of editing it manually.\n"
        "\n"
        f"_RESOURCE_NAME = {resource_name!r}\n"
        f"_RESOURCE_SHA256 = {resource_fingerprint!r}\n"
        f"_PROJECT_ID = {project_id!r}\n"
    )
    runtime = """_MAGIC = b"PGA3"
_MAX_TEXT = 4096
_INDEX_SIZE = 8
_FRAME_SIZE = 12
_WAV_SIZE = 26
_IMAGE = 1
_WAV = 2
_MAX_AUDIO_CHUNK = 4096


def _resource_path():
    module_file = globals().get("__file__", "")
    base = module_file.rsplit("/", 1)[0] if "/" in module_file else ""
    return (base + "/" if base else "") + _RESOURCE_NAME


def _read_exact(handle, size):
    data = handle.read(size)
    return data if data is not None and len(data) == size else None


def _u16(data, offset=0):
    return data[offset] | (data[offset + 1] << 8)


def _u32(data, offset=0):
    return (data[offset] | (data[offset + 1] << 8) |
            (data[offset + 2] << 16) | (data[offset + 3] << 24))


def _i32(data, offset=0):
    value = _u32(data, offset)
    return value - 0x100000000 if value & 0x80000000 else value


def _asset_hash(asset_id):
    value = 0x811C9DC5
    for byte in asset_id:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def _open_resource():
    try:
        handle = open(_resource_path(), "rb")
    except OSError:
        return None
    try:
        if _read_exact(handle, 4) != _MAGIC:
            raise ValueError
        raw = _read_exact(handle, 2)
        if raw is None:
            raise ValueError
        project_size = _u16(raw)
        if project_size < 1 or project_size > _MAX_TEXT:
            raise ValueError
        project = _read_exact(handle, project_size)
        if project is None or project.decode("utf-8") != _PROJECT_ID:
            raise ValueError
        raw = _read_exact(handle, 10)
        if raw is None:
            raise ValueError
        count = _u16(raw)
        payload_start = _u32(raw, 2)
        total_size = _u32(raw, 6)
        index_start = handle.tell()
        index_end = index_start + count * _INDEX_SIZE
        if index_end > payload_start or payload_start > total_size:
            raise ValueError
        return (handle, count, index_start, index_end, payload_start, total_size)
    except Exception:
        handle.close()
        return None


def _index_entry(handle, index_start, index, index_end):
    offset = index_start + index * _INDEX_SIZE
    if offset < index_start or offset + _INDEX_SIZE > index_end:
        return None
    handle.seek(offset)
    raw = _read_exact(handle, _INDEX_SIZE)
    return None if raw is None else (_u32(raw), _u32(raw, 4))


def _record(opened, record_offset, wanted_id):
    handle, unused_count, unused_start, index_end, payload_start, total = opened
    if record_offset < index_end or record_offset >= payload_start:
        return None
    handle.seek(record_offset)
    common = _read_exact(handle, 4)
    if common is None or common[1] != 0:
        return None
    kind = common[0]
    asset_id_size = _u16(common, 2)
    if (kind != _IMAGE and kind != _WAV) or not 1 <= asset_id_size <= _MAX_TEXT:
        return None
    asset_id = _read_exact(handle, asset_id_size)
    if asset_id is None or asset_id != wanted_id:
        return None
    raw = _read_exact(handle, 2)
    if raw is None:
        return None
    name_size = _u16(raw)
    if name_size > _MAX_TEXT or handle.tell() + name_size > payload_start:
        return None
    handle.seek(handle.tell() + name_size)
    if kind == _IMAGE:
        raw = _read_exact(handle, 14)
        if raw is None:
            return None
        width = _u16(raw)
        height = _u16(raw, 2)
        frames = _u16(raw, 12)
        frame_records = handle.tell()
        if (width < 1 or width > 320 or height < 1 or height > 320 or
                frames < 1 or frame_records + frames * _FRAME_SIZE > payload_start):
            return None
        return (_IMAGE, width, height, _i32(raw, 4), _i32(raw, 8),
                frames, frame_records)
    raw = _read_exact(handle, _WAV_SIZE)
    if raw is None:
        return None
    channels = raw[0]
    bits = raw[1]
    rate = _u32(raw, 2)
    duration = _u32(raw, 6)
    loop_start = _u32(raw, 10)
    loop_end = _u32(raw, 14)
    offset = _u32(raw, 18)
    length = _u32(raw, 22)
    if (channels < 1 or channels > 2 or bits not in (8, 16, 24) or rate < 1 or
            length < 12 or offset < payload_start or offset + length > total):
        return None
    if (loop_start == 0xFFFFFFFF) != (loop_end == 0xFFFFFFFF):
        return None
    if loop_start != 0xFFFFFFFF and not 0 <= loop_start < loop_end <= duration:
        return None
    return (_WAV, channels, bits, rate, duration, loop_start, loop_end,
            offset, length)


def _find_resource(opened, asset_id):
    try:
        wanted_id = asset_id.encode("utf-8")
    except Exception:
        return None
    if not wanted_id or len(wanted_id) > _MAX_TEXT:
        return None
    handle, count, index_start, index_end, unused_payload, unused_total = opened
    wanted_hash = _asset_hash(wanted_id)
    low = 0
    high = count
    while low < high:
        middle = (low + high) // 2
        entry = _index_entry(handle, index_start, middle, index_end)
        if entry is None:
            return None
        if entry[0] < wanted_hash:
            low = middle + 1
        else:
            high = middle
    while low < count:
        entry = _index_entry(handle, index_start, low, index_end)
        if entry is None or entry[0] != wanted_hash:
            return None
        resource = _record(opened, entry[1], wanted_id)
        if resource is not None:
            return resource
        low += 1
    return None


def _metadata(asset_id, kind):
    opened = _open_resource()
    if opened is None:
        return None
    try:
        resource = _find_resource(opened, asset_id)
        return resource if resource is not None and resource[0] == kind else None
    finally:
        opened[0].close()


def has_asset(asset_id):
    return _metadata(asset_id, _IMAGE) is not None


def asset_size(asset_id):
    asset = _metadata(asset_id, _IMAGE)
    return None if asset is None else (asset[1], asset[2])


def frame_count(asset_id):
    asset = _metadata(asset_id, _IMAGE)
    return 0 if asset is None else asset[5]


def has_wav(asset_id):
    return _metadata(asset_id, _WAV) is not None


def wav_info(asset_id):
    wav = _metadata(asset_id, _WAV)
    if wav is None:
        return None
    loop_start = None if wav[5] == 0xFFFFFFFF else wav[5]
    loop_end = None if wav[6] == 0xFFFFFFFF else wav[6]
    return (wav[3], wav[1], wav[2], wav[4], loop_start, loop_end, wav[8])


def wav_path(asset_id):
    return None


def read_wav_chunk(asset_id, offset=0, size=1024):
    opened = _open_resource()
    if opened is None:
        return None
    handle = opened[0]
    try:
        wav = _find_resource(opened, asset_id)
        if wav is None or wav[0] != _WAV:
            return None
        try:
            offset = max(0, int(offset))
            size = max(0, min(_MAX_AUDIO_CHUNK, int(size)))
        except (TypeError, ValueError):
            return None
        if offset >= wav[8] or size == 0:
            return b""
        size = min(size, wav[8] - offset)
        handle.seek(wav[7] + offset)
        return _read_exact(handle, size)
    finally:
        handle.close()


def extract_wav(asset_id, destination):
    opened = _open_resource()
    if opened is None or not isinstance(destination, str) or not destination:
        return False
    handle = opened[0]
    target = None
    temporary = destination + ".pga-tmp"
    import os
    try:
        wav = _find_resource(opened, asset_id)
        if wav is None or wav[0] != _WAV:
            return False
        target = open(temporary, "wb")
        handle.seek(wav[7])
        remaining = wav[8]
        while remaining:
            chunk = _read_exact(handle, min(1024, remaining))
            if chunk is None or target.write(chunk) != len(chunk):
                return False
            remaining -= len(chunk)
        target.close()
        target = None
        try:
            os.remove(destination)
        except OSError:
            pass
        os.rename(temporary, destination)
        return True
    except (OSError, ValueError):
        return False
    finally:
        if target is not None:
            target.close()
        handle.close()
        try:
            os.remove(temporary)
        except OSError:
            pass


def draw_asset(draw, asset_id, x, y, frame=0, scale=1):
    opened = _open_resource()
    if opened is None:
        return False
    handle = opened[0]
    try:
        asset = _find_resource(opened, asset_id)
        if asset is None or asset[0] != _IMAGE:
            return False
        try:
            frame = int(frame)
        except (TypeError, ValueError):
            frame = 0
        if frame < 0 or frame >= asset[5]:
            frame = 0
        try:
            scale = max(1, int(scale))
        except (TypeError, ValueError):
            scale = 1
        width = asset[1]
        height = asset[2]
        mask_size = (width + 7) // 8
        row_size = width * 2
        handle.seek(asset[6] + frame * _FRAME_SIZE)
        raw = _read_exact(handle, _FRAME_SIZE)
        if raw is None:
            return False
        offset = _u32(raw)
        expected_size = _u32(raw, 4)
        if (expected_size != height * (mask_size + row_size) or
                offset < opened[4] or offset + expected_size > opened[5]):
            return False
        handle.seek(offset)
        base_x = x + asset[3] * scale
        base_y = y + asset[4] * scale
        for row in range(height):
            mask = _read_exact(handle, mask_size)
            pixels = _read_exact(handle, row_size)
            if mask is None or pixels is None:
                return False
            column = 0
            while column < width:
                while column < width and not (mask[column // 8] & (0x80 >> (column % 8))):
                    column += 1
                start = column
                while column < width and (mask[column // 8] & (0x80 >> (column % 8))):
                    column += 1
                if start == column:
                    continue
                if scale == 1:
                    draw._bytearray(
                        base_x + start, base_y + row, column - start, 1,
                        memoryview(pixels)[start * 2:column * 2], False,
                    )
                    continue
                run = start
                while run < column:
                    pixel_offset = run * 2
                    color = pixels[pixel_offset] | (pixels[pixel_offset + 1] << 8)
                    end = run + 1
                    while end < column:
                        next_offset = end * 2
                        next_color = pixels[next_offset] | (pixels[next_offset + 1] << 8)
                        if next_color != color:
                            break
                        end += 1
                    draw._fill_rectangle(
                        base_x + run * scale, base_y + row * scale,
                        (end - run) * scale, scale, color,
                    )
                    run = end
        return True
    finally:
        handle.close()
"""
    return header + runtime


def _individual_resource_module_source(
    project_id: str,
    generator_version: str,
    resource_directory: str,
) -> str:
    """Return a constant-size runtime for individually deployed PGA images and WAVs."""
    header = (
        "# @picoware-generated structure=1\n"
        "# @picoware-generated role=assets\n"
        f"# @picoware-generated project={project_id}\n"
        f"# @picoware-generator version={generator_version}\n"
        "# This file is editor-owned. Regenerate it instead of editing it manually.\n"
        "\n"
        f"_RESOURCE_DIRECTORY = {resource_directory!r}\n"
        f"_PROJECT_ID = {project_id!r}\n"
    )
    runtime = """_MAGIC = b"PGA3"
_MAX_TEXT = 4096
_FRAME_SIZE = 12
_IMAGE = 1
_MAX_AUDIO_CHUNK = 4096


def _read_exact(handle, size):
    data = handle.read(size)
    return data if data is not None and len(data) == size else None


def _u16(data, offset=0):
    return data[offset] | (data[offset + 1] << 8)


def _u32(data, offset=0):
    return (data[offset] | (data[offset + 1] << 8) |
            (data[offset + 2] << 16) | (data[offset + 3] << 24))


def _i32(data, offset=0):
    value = _u32(data, offset)
    return value - 0x100000000 if value & 0x80000000 else value


def _safe_id(asset_id):
    if not isinstance(asset_id, str) or not asset_id or len(asset_id) > 120:
        return None
    for index, character in enumerate(asset_id):
        valid = ("a" <= character <= "z" or "A" <= character <= "Z" or
                 "0" <= character <= "9" or character in "_.-")
        if not valid or (index == 0 and not character.isalnum()):
            return None
    return asset_id


def _resource_path(asset_id, extension):
    asset_id = _safe_id(asset_id)
    if asset_id is None:
        return None
    module_file = globals().get("__file__", "")
    base = module_file.rsplit("/", 1)[0] if "/" in module_file else ""
    prefix = (base + "/" if base else "") + _RESOURCE_DIRECTORY + "/"
    return prefix + asset_id + extension


def _open_image(asset_id):
    path = _resource_path(asset_id, ".pga")
    if path is None:
        return None
    try:
        handle = open(path, "rb")
    except OSError:
        return None
    try:
        if _read_exact(handle, 4) != _MAGIC:
            raise ValueError
        raw = _read_exact(handle, 2)
        if raw is None:
            raise ValueError
        project_size = _u16(raw)
        if not 1 <= project_size <= _MAX_TEXT:
            raise ValueError
        project = _read_exact(handle, project_size)
        if project is None or project.decode("utf-8") != _PROJECT_ID:
            raise ValueError
        raw = _read_exact(handle, 10)
        if raw is None or _u16(raw) != 1:
            raise ValueError
        payload_start = _u32(raw, 2)
        total_size = _u32(raw, 6)
        index_end = handle.tell() + 8
        index = _read_exact(handle, 8)
        if index is None:
            raise ValueError
        record_offset = _u32(index, 4)
        if record_offset < index_end or record_offset >= payload_start:
            raise ValueError
        handle.seek(record_offset)
        common = _read_exact(handle, 4)
        if common is None or common[0] != _IMAGE or common[1] != 0:
            raise ValueError
        id_size = _u16(common, 2)
        wanted = asset_id.encode("utf-8")
        if id_size != len(wanted) or _read_exact(handle, id_size) != wanted:
            raise ValueError
        raw = _read_exact(handle, 2)
        if raw is None:
            raise ValueError
        name_size = _u16(raw)
        if name_size > _MAX_TEXT or handle.tell() + name_size > payload_start:
            raise ValueError
        handle.seek(handle.tell() + name_size)
        raw = _read_exact(handle, 14)
        if raw is None:
            raise ValueError
        width = _u16(raw)
        height = _u16(raw, 2)
        frames = _u16(raw, 12)
        frame_records = handle.tell()
        if (width < 1 or width > 320 or height < 1 or height > 320 or
                frames < 1 or frame_records + frames * _FRAME_SIZE > payload_start):
            raise ValueError
        return (handle, width, height, _i32(raw, 4), _i32(raw, 8), frames,
                frame_records, payload_start, total_size)
    except Exception:
        handle.close()
        return None


def _image_metadata(asset_id):
    opened = _open_image(asset_id)
    if opened is None:
        return None
    opened[0].close()
    return opened[1:]


def has_asset(asset_id):
    return _image_metadata(asset_id) is not None


def asset_size(asset_id):
    asset = _image_metadata(asset_id)
    return None if asset is None else (asset[0], asset[1])


def frame_count(asset_id):
    asset = _image_metadata(asset_id)
    return 0 if asset is None else asset[4]


def _open_wav(asset_id):
    path = _resource_path(asset_id, ".wav")
    if path is None:
        return None
    try:
        handle = open(path, "rb")
    except OSError:
        return None
    try:
        header = _read_exact(handle, 12)
        if header is None or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError
        total_size = _u32(header, 4) + 8
        if total_size < 44:
            raise ValueError
        channels = bits = rate = byte_rate = data_size = 0
        cursor = 12
        while cursor + 8 <= total_size:
            handle.seek(cursor)
            chunk = _read_exact(handle, 8)
            if chunk is None:
                raise ValueError
            size = _u32(chunk, 4)
            next_cursor = cursor + 8 + size + (size & 1)
            if next_cursor > total_size:
                raise ValueError
            if chunk[:4] == b"fmt ":
                if size < 16:
                    raise ValueError
                fmt = _read_exact(handle, 16)
                if fmt is None or _u16(fmt) != 1:
                    raise ValueError
                channels = _u16(fmt, 2)
                rate = _u32(fmt, 4)
                byte_rate = _u32(fmt, 8)
                bits = _u16(fmt, 14)
            elif chunk[:4] == b"data":
                data_size = size
            cursor = next_cursor
        if (channels not in (1, 2) or bits not in (8, 16, 24) or
                rate < 1 or byte_rate < 1 or data_size < 1):
            raise ValueError
        duration = (data_size * 1000 + byte_rate // 2) // byte_rate
        return (handle, rate, channels, bits, duration, total_size)
    except Exception:
        handle.close()
        return None


def has_wav(asset_id):
    opened = _open_wav(asset_id)
    if opened is None:
        return False
    opened[0].close()
    return True


def wav_info(asset_id):
    opened = _open_wav(asset_id)
    if opened is None:
        return None
    opened[0].close()
    return (opened[1], opened[2], opened[3], opened[4], None, None, opened[5])


def wav_path(asset_id):
    path = _resource_path(asset_id, ".wav")
    return path if path is not None and has_wav(asset_id) else None


def read_wav_chunk(asset_id, offset=0, size=1024):
    opened = _open_wav(asset_id)
    if opened is None:
        return None
    handle = opened[0]
    try:
        try:
            offset = max(0, int(offset))
            size = max(0, min(_MAX_AUDIO_CHUNK, int(size)))
        except (TypeError, ValueError):
            return None
        if offset >= opened[5] or size == 0:
            return b""
        size = min(size, opened[5] - offset)
        handle.seek(offset)
        return _read_exact(handle, size)
    finally:
        handle.close()


def extract_wav(asset_id, destination):
    source = wav_path(asset_id)
    if source is None or not isinstance(destination, str) or not destination:
        return False
    if source == destination:
        return True
    temporary = destination + ".pga-tmp"
    source_handle = target = None
    import os
    try:
        source_handle = open(source, "rb")
        target = open(temporary, "wb")
        while True:
            chunk = source_handle.read(1024)
            if not chunk:
                break
            if target.write(chunk) != len(chunk):
                return False
        target.close()
        target = None
        try:
            os.remove(destination)
        except OSError:
            pass
        os.rename(temporary, destination)
        return True
    except OSError:
        return False
    finally:
        if source_handle is not None:
            source_handle.close()
        if target is not None:
            target.close()
        try:
            os.remove(temporary)
        except OSError:
            pass


def draw_asset(draw, asset_id, x, y, frame=0, scale=1):
    opened = _open_image(asset_id)
    if opened is None:
        return False
    handle = opened[0]
    try:
        try:
            frame = int(frame)
        except (TypeError, ValueError):
            frame = 0
        if frame < 0 or frame >= opened[5]:
            frame = 0
        try:
            scale = max(1, int(scale))
        except (TypeError, ValueError):
            scale = 1
        handle.seek(opened[6] + frame * _FRAME_SIZE)
        descriptor = _read_exact(handle, _FRAME_SIZE)
        if descriptor is None:
            return False
        offset = _u32(descriptor)
        length = _u32(descriptor, 4)
        width = opened[1]
        height = opened[2]
        mask_size = (width + 7) // 8
        row_size = width * 2
        expected_size = height * (mask_size + row_size)
        if (length != expected_size or offset < opened[7] or
                offset + length > opened[8]):
            return False
        handle.seek(offset)
        base_x = x + opened[3] * scale
        base_y = y + opened[4] * scale
        for row in range(height):
            mask = _read_exact(handle, mask_size)
            pixels = _read_exact(handle, row_size)
            if mask is None or pixels is None:
                return False
            column = 0
            while column < width:
                while column < width and not (mask[column // 8] & (0x80 >> (column % 8))):
                    column += 1
                start = column
                while column < width and (mask[column // 8] & (0x80 >> (column % 8))):
                    column += 1
                if start == column:
                    continue
                if scale == 1:
                    draw._bytearray(
                        base_x + start, base_y + row, column - start, 1,
                        memoryview(pixels)[start * 2:column * 2], False,
                    )
                    continue
                run = start
                while run < column:
                    pixel_offset = run * 2
                    color = pixels[pixel_offset] | (pixels[pixel_offset + 1] << 8)
                    end = run + 1
                    while end < column:
                        next_offset = end * 2
                        next_color = pixels[next_offset] | (pixels[next_offset + 1] << 8)
                        if next_color != color:
                            break
                        end += 1
                    draw._fill_rectangle(
                        base_x + run * scale, base_y + row * scale,
                        (end - run) * scale, scale, color,
                    )
                    run = end
        return True
    finally:
        handle.close()
"""
    return header + runtime


def _runtime_asset_record(entry: GeneratedAssetEntry) -> str:
    """Format one canonical runtime dictionary record."""
    asset = entry.asset
    palette = "(" + ", ".join(_format_color(color) for color in asset.palette) + ",)"
    frames = repr(asset.frames)
    durations = repr(asset.durations)
    value = (
        f"({entry.name!r}, {asset.width}, {asset.height}, "
        f"{asset.origin_x}, {asset.origin_y}, {palette}, {frames}, {durations})"
    )
    return f"    {entry.asset_id!r}: {value},\n"


def _format_color(color: int | None) -> str:
    """Format one palette entry as transparent or fixed-width RGB565."""
    return "None" if color is None else f"0x{color:04X}"


def _validate_source_frame(frame: PixelArt, first: PixelArt) -> None:
    """Validate one source frame against the shared asset geometry."""
    if not isinstance(frame, PixelArt):
        raise TypeError("Asset frames must be PixelArt instances")
    _validate_dimensions(frame.width, frame.height)
    if (
        frame.width != first.width
        or frame.height != first.height
        or frame.origin_x != first.origin_x
        or frame.origin_y != first.origin_y
    ):
        raise ValueError("Asset frames must share dimensions and origin")
    for color in frame.pixels:
        if color is None:
            continue
        if type(color) is not int or not 0 <= color <= 0xFFFF:
            raise ValueError("Asset pixels must be transparent or RGB565 integers")


def _validate_dimensions(width: int, height: int) -> None:
    """Validate supported v1 asset dimensions."""
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= MAXIMUM_ASSET_DIMENSION
        or not 1 <= height <= MAXIMUM_ASSET_DIMENSION
    ):
        raise ValueError(
            f"Asset dimensions must be between 1 and {MAXIMUM_ASSET_DIMENSION}"
        )


def _validate_integer(value: int, label: str) -> None:
    """Require a non-boolean integer metadata value."""
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")


def _build_palette(frames: tuple[PixelArt, ...]) -> AssetPalette:
    """Build a transparent-first palette in frame and pixel scan order."""
    palette: list[int | None] = [None]
    seen: set[int] = set()
    for frame in frames:
        for color in frame.pixels:
            if color is not None and color not in seen:
                seen.add(color)
                palette.append(color)
    return tuple(palette)


def _encode_frame(
    frame: PixelArt,
    palette_indexes: dict[int, int],
) -> AssetFrame:
    """Encode and vertically merge one frame's visible horizontal runs."""
    completed: list[AssetRectangle] = []
    active: dict[tuple[int, int, int], AssetRectangle] = {}
    for y in range(frame.height):
        next_active: dict[tuple[int, int, int], AssetRectangle] = {}
        for x, width, palette_index in _row_runs(frame, y, palette_indexes):
            key = (x, width, palette_index)
            previous = active.pop(key, None)
            if previous is None:
                rectangle = (x, y, width, 1, palette_index)
            else:
                rectangle = (
                    previous[0],
                    previous[1],
                    previous[2],
                    previous[3] + 1,
                    previous[4],
                )
            next_active[key] = rectangle
        completed.extend(active.values())
        active = next_active
    completed.extend(active.values())
    return tuple(sorted(completed, key=_rectangle_sort_key))


def _row_runs(
    frame: PixelArt,
    y: int,
    palette_indexes: dict[int, int],
) -> list[tuple[int, int, int]]:
    """Return visible horizontal palette runs for one row."""
    runs: list[tuple[int, int, int]] = []
    row_start = y * frame.width
    x = 0
    while x < frame.width:
        color = frame.pixels[row_start + x]
        if color is None:
            x += 1
            continue
        end = x + 1
        while end < frame.width and frame.pixels[row_start + end] == color:
            end += 1
        runs.append((x, end - x, palette_indexes[color]))
        x = end
    return runs


def _validate_palette(palette: AssetPalette) -> None:
    """Validate transparent-first unique RGB565 palette entries."""
    if not isinstance(palette, tuple) or not palette or palette[0] is not None:
        raise ValueError("Palette index zero must be transparent")
    visible: set[int] = set()
    for color in palette[1:]:
        if type(color) is not int or not 0 <= color <= 0xFFFF:
            raise ValueError("Visible palette entries must be RGB565 integers")
        if color in visible:
            raise ValueError("Visible palette entries must be unique")
        visible.add(color)


def _validate_frame(
    frame: AssetFrame,
    width: int,
    height: int,
    palette_size: int,
) -> set[int]:
    """Validate one ordered, nonoverlapping compact rectangle frame."""
    if not isinstance(frame, tuple):
        raise ValueError("Asset frames must be tuples")
    for rectangle in frame:
        if not isinstance(rectangle, tuple) or len(rectangle) != 5:
            raise ValueError("Asset rectangles must contain five integers")
        if any(type(value) is not int for value in rectangle):
            raise ValueError("Asset rectangle values must be integers")
    if frame != tuple(sorted(frame, key=_rectangle_sort_key)):
        raise ValueError("Asset rectangles must use canonical ordering")
    occupied = bytearray(width * height)
    used_palette_indexes: set[int] = set()
    for rectangle in frame:
        x, y, rect_width, rect_height, palette_index = rectangle
        if (
            x < 0
            or y < 0
            or rect_width < 1
            or rect_height < 1
            or x + rect_width > width
            or y + rect_height > height
        ):
            raise ValueError("Asset rectangle is outside the canvas")
        if not 1 <= palette_index < palette_size:
            raise ValueError("Asset rectangle palette index is invalid")
        used_palette_indexes.add(palette_index)
        for pixel_y in range(y, y + rect_height):
            start = pixel_y * width + x
            for index in range(start, start + rect_width):
                if occupied[index]:
                    raise ValueError("Asset rectangles must not overlap")
                occupied[index] = 1
    return used_palette_indexes


def _validate_durations(durations: tuple[int, ...], frame_count: int) -> None:
    """Validate optional per-frame duration metadata."""
    if not isinstance(durations, tuple):
        raise ValueError("Asset durations must be a tuple")
    if durations and len(durations) != frame_count:
        raise ValueError("Asset durations must match the frame count")
    if any(type(duration) is not int or duration <= 0 for duration in durations):
        raise ValueError("Asset durations must be positive integer milliseconds")


def _rectangle_sort_key(rectangle: AssetRectangle) -> tuple[int, int, int, int, int]:
    """Return the documented canonical rectangle ordering key."""
    return rectangle[1], rectangle[0], rectangle[4], rectangle[2], rectangle[3]
