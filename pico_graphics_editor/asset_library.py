"""Persist reusable RGB565 assets independently from editor projects."""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from .asset_codegen import encode_asset, fingerprint_encoded_asset
from .model import PixelArt


ASSET_LIBRARY_VERSION = 1


@dataclass(frozen=True)
class LibraryAsset:
    """Store one portable static or animated personal-library asset."""

    id: str
    name: str
    width: int
    height: int
    origin_x: int
    origin_y: int
    frames: tuple[tuple[int | None, ...], ...]
    durations: tuple[int, ...] = ()
    fingerprint: str = ""

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> LibraryAsset:
        """Create and validate one JSON-compatible library record."""
        asset = cls(
            str(values.get("id", "")),
            str(values.get("name", "")),
            int(values.get("width", 0)),
            int(values.get("height", 0)),
            int(values.get("origin_x", 0)),
            int(values.get("origin_y", 0)),
            tuple(tuple(frame) for frame in values.get("frames", [])),
            tuple(int(value) for value in values.get("durations", [])),
            str(values.get("fingerprint", "")),
        )
        asset.validate()
        return asset

    @classmethod
    def from_frames(
        cls,
        asset_id: str,
        name: str,
        frames: list[PixelArt] | tuple[PixelArt, ...],
        durations: list[int] | tuple[int, ...] | None = None,
    ) -> LibraryAsset:
        """Create one portable record from lossless desktop pixel frames."""
        encoded = encode_asset(frames, durations)
        return cls(
            asset_id,
            name.strip() or "Untitled Asset",
            encoded.width,
            encoded.height,
            encoded.origin_x,
            encoded.origin_y,
            tuple(tuple(frame.pixels) for frame in frames),
            encoded.durations,
            fingerprint_encoded_asset(encoded),
        )

    def validate(self) -> None:
        """Reject malformed or incomplete persisted library data."""
        if not self.id or not self.name:
            raise ValueError("Library assets require an ID and name")
        frames = self.pixel_frames()
        encoded = encode_asset(frames, self.durations or None)
        if self.fingerprint and fingerprint_encoded_asset(encoded) != self.fingerprint:
            raise ValueError("Library asset fingerprint does not match its pixels")

    def pixel_frames(self) -> tuple[PixelArt, ...]:
        """Return independent editable frames for this library asset."""
        if self.width < 1 or self.height < 1 or not self.frames:
            raise ValueError("Library asset dimensions and frames are required")
        expected = self.width * self.height
        if any(len(frame) != expected for frame in self.frames):
            raise ValueError("Library asset frame dimensions do not match")
        return tuple(
            PixelArt(
                self.width,
                self.height,
                self.origin_x,
                self.origin_y,
                list(frame),
            )
            for frame in self.frames
        )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-compatible values."""
        return {
            "id": self.id,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "frames": [list(frame) for frame in self.frames],
            "durations": list(self.durations),
            "fingerprint": self.fingerprint,
        }


class AssetLibrary:
    """Load and atomically update one local personal asset-library file."""

    def __init__(
        self,
        path: str | Path,
        *,
        reserved_names: Sequence[str] = (),
        reserved_ids: Sequence[str] = (),
    ):
        self.path = Path(path).expanduser()
        self._reserved_names = tuple(reserved_names)
        self._reserved_ids = frozenset(reserved_ids)
        self._cache_valid = False
        self._cached_file_token: tuple[int, int, int, int] | None = None
        self._cached_assets: tuple[LibraryAsset, ...] = ()

    def set_reserved_catalogue(
        self,
        names: Sequence[str],
        asset_ids: Sequence[str],
    ) -> None:
        """Configure names and IDs owned by the read-only companion catalogue."""
        reserved_names = tuple(names)
        reserved_ids = frozenset(asset_ids)
        if (
            reserved_names == self._reserved_names
            and reserved_ids == self._reserved_ids
        ):
            return
        self._reserved_names = reserved_names
        self._reserved_ids = reserved_ids
        self._cache_valid = False

    def assets(self) -> tuple[LibraryAsset, ...]:
        """Return all assets sorted by display name and stable ID."""
        return tuple(
            sorted(self._load(), key=lambda item: (item.name.casefold(), item.id))
        )

    def asset(self, asset_id: str) -> LibraryAsset | None:
        """Return one stored asset by stable library ID."""
        return next((asset for asset in self._load() if asset.id == asset_id), None)

    def revision(self, assets: Sequence[LibraryAsset] | None = None) -> str:
        """Return a content revision suitable for optimistic history checks."""
        records = sorted(
            tuple(assets) if assets is not None else tuple(self._load()),
            key=lambda asset: asset.id,
        )
        payload = json.dumps(
            [asset.to_dict() for asset in records],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def available_name(
        self,
        name: str,
        exclude_id: str = "",
    ) -> str:
        """Return an unambiguous display name without changing existing records."""
        return self._available_name(
            name,
            self._load(),
            exclude_id=exclude_id,
        )

    def add(
        self,
        name: str,
        frames: list[PixelArt] | tuple[PixelArt, ...],
        durations: list[int] | tuple[int, ...] | None = None,
    ) -> LibraryAsset:
        """Add one independent asset and persist it atomically."""
        return self.add_many(((name, frames, durations),))[0]

    def add_many(
        self,
        entries: Sequence[
            tuple[
                str,
                Sequence[PixelArt],
                Sequence[int] | None,
            ]
        ],
    ) -> tuple[LibraryAsset, ...]:
        """Validate and persist independent assets in one atomic transaction."""
        assets = self._load()
        existing_ids = {asset.id for asset in assets}
        additions: list[LibraryAsset] = []
        for name, frames, durations in entries:
            asset_id = f"library_{uuid.uuid4().hex[:12]}"
            while asset_id in existing_ids:
                asset_id = f"library_{uuid.uuid4().hex[:12]}"
            addition = LibraryAsset.from_frames(
                asset_id,
                self._available_name(
                    name,
                    (*assets, *additions),
                ),
                tuple(frames),
                tuple(durations) if durations is not None else None,
            )
            existing_ids.add(asset_id)
            additions.append(addition)
        if not additions:
            return ()
        assets.extend(additions)
        self._save(assets)
        return tuple(additions)

    def remove(self, asset_id: str) -> bool:
        """Remove one stored asset and report whether it existed."""
        return bool(self.remove_many((asset_id,)))

    def remove_many(self, asset_ids: Sequence[str]) -> tuple[LibraryAsset, ...]:
        """Remove several stored assets in one atomic transaction."""
        assets = self._load()
        requested = set(asset_ids)
        removed = tuple(asset for asset in assets if asset.id in requested)
        if not removed:
            return ()
        retained = [asset for asset in assets if asset.id not in requested]
        self._save(retained)
        return removed

    def rename(
        self,
        asset_id: str,
        name: str,
    ) -> LibraryAsset:
        """Rename one stored asset without changing its stable ID or pixels."""
        label = name.strip()
        if not label:
            raise ValueError("Library asset name cannot be empty")
        assets = self._load()
        for index, asset in enumerate(assets):
            if asset.id == asset_id:
                label = self._available_name(
                    label,
                    assets,
                    exclude_id=asset_id,
                )
                renamed = LibraryAsset(
                    asset.id,
                    label,
                    asset.width,
                    asset.height,
                    asset.origin_x,
                    asset.origin_y,
                    asset.frames,
                    asset.durations,
                    asset.fingerprint,
                )
                assets[index] = renamed
                self._save(assets)
                return renamed
        raise KeyError(asset_id)

    def _available_name(
        self,
        name: str,
        assets: Sequence[LibraryAsset],
        *,
        exclude_id: str = "",
    ) -> str:
        """Disambiguate one case-insensitive display name with a numeric suffix."""
        label = name.strip() or "Untitled Asset"
        occupied = {
            self.name_key(asset.name) for asset in assets if asset.id != exclude_id
        } | {self.name_key(reserved) for reserved in self._reserved_names}
        if self.name_key(label) not in occupied:
            return label
        numbered = re.fullmatch(r"(.+?)\s+(\d+)", label)
        if numbered:
            base = numbered.group(1).rstrip()
            suffix = max(2, int(numbered.group(2)) + 1)
        else:
            base = label
            suffix = 2
        while self.name_key(f"{base} {suffix}") in occupied:
            suffix += 1
        return f"{base} {suffix}"

    def duplicate(
        self,
        asset_id: str,
        name: str | None = None,
    ) -> LibraryAsset:
        """Create an independent copy with a new stable identity."""
        source = self.asset(asset_id)
        if source is None:
            raise KeyError(asset_id)
        return self.add(
            name or f"{source.name} Copy",
            source.pixel_frames(),
            source.durations or None,
        )

    def replace(
        self,
        asset_id: str,
        frames: list[PixelArt] | tuple[PixelArt, ...],
        durations: list[int] | tuple[int, ...] | None = None,
    ) -> LibraryAsset:
        """Replace pixels while preserving the record's ID and display name."""
        assets = self._load()
        for index, asset in enumerate(assets):
            if asset.id != asset_id:
                continue
            replacement = LibraryAsset.from_frames(
                asset.id,
                asset.name,
                frames,
                durations,
            )
            assets[index] = replacement
            self._save(assets)
            return replacement
        raise KeyError(asset_id)

    def restore_snapshot(
        self, assets: Sequence[LibraryAsset]
    ) -> tuple[LibraryAsset, ...]:
        """Validate and atomically restore one complete library snapshot."""
        restored = list(assets)
        self._validate_collection(restored)
        self._save(restored)
        return self.assets()

    @staticmethod
    def name_key(name: str) -> str:
        """Normalize insignificant spacing and case for visible-name identity."""
        return " ".join(name.split()).casefold()

    def _validate_collection(self, assets: Sequence[LibraryAsset]) -> None:
        """Enforce personal names and IDs plus reserved read-only identities."""
        identifiers: set[str] = set()
        names: set[str] = set()
        for asset in assets:
            asset.validate()
            if asset.id in identifiers:
                raise ValueError("Library asset IDs must be unique")
            if asset.id in self._reserved_ids:
                raise ValueError(
                    f"Personal library asset ID conflicts with built-in ID {asset.id}"
                )
            identifiers.add(asset.id)
            name_key = self.name_key(asset.name)
            if name_key in names:
                raise ValueError(f'Library asset name "{asset.name}" is already in use')
            names.add(name_key)

    def _load(self) -> list[LibraryAsset]:
        """Read and validate the complete versioned library."""
        token = self._file_token()
        if self._cache_valid and token == self._cached_file_token:
            return list(self._cached_assets)
        if token is None:
            self._cache_valid = True
            self._cached_file_token = None
            self._cached_assets = ()
            return []
        values = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("Asset library root must be an object")
        version = int(values.get("format_version", 0))
        if version != ASSET_LIBRARY_VERSION:
            raise ValueError(f"Unsupported asset library format {version}")
        records = values.get("assets", [])
        if not isinstance(records, list):
            raise ValueError("Asset library records must be a list")
        assets = [LibraryAsset.from_dict(record) for record in records]
        self._validate_collection(assets)
        self._cache_valid = True
        self._cached_file_token = token
        self._cached_assets = tuple(assets)
        return assets

    def _file_token(self) -> tuple[int, int, int, int] | None:
        """Return a strong, cheap identity for detecting external file changes."""
        try:
            status = self.path.stat()
        except FileNotFoundError:
            return None
        return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)

    def _save(self, assets: list[LibraryAsset]) -> None:
        """Write the complete library through a flushed temporary sibling."""
        self._validate_collection(assets)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": ASSET_LIBRARY_VERSION,
            "assets": [asset.to_dict() for asset in assets],
        }
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temporary:
                json.dump(payload, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            Path(temporary_name).replace(self.path)
            self._cached_assets = tuple(assets)
            self._cached_file_token = self._file_token()
            self._cache_valid = True
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
