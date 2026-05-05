"""musiktool sidecar files for durable scan metadata.

The sidecar is intentionally music-specific. It stores file stats, optional
hashes, audio facts, tag snapshots, and media-kind classification so the
SQLite index can be rebuilt without re-reading every audio file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from musiktool.exceptions import ValidationError

SIDECAR_SCHEMA_VERSION = 1
SIDECAR_EXTENSION = ".mtk"

_MAGIC = "MTK1"


class SidecarError(ValidationError):
    """Base class for sidecar read/write errors."""


class SidecarFormatError(SidecarError):
    """Sidecar content does not match the expected wrapper format."""


class SidecarIntegrityError(SidecarError):
    """Sidecar payload digest does not match the stored digest."""


def root_id(root: Path) -> str:
    """Return a stable short id for a source root path."""
    root_text = str(root.resolve())
    return hashlib.sha256(root_text.encode("utf-8")).hexdigest()[:16]


def sidecar_path_for(
    source: Path,
    *,
    source_root: Path,
    sidecar_root: Path,
) -> Path:
    """Return the out-of-tree sidecar path for a source file."""
    source = source.resolve()
    source_root = source_root.resolve()
    try:
        relative = source.relative_to(source_root)
    except ValueError as e:
        raise ValidationError(
            f"source is not inside source root: {source} not under {source_root}",
        ) from e

    return (
        sidecar_root
        / root_id(source_root)
        / relative.parent
        / f"{relative.name}{SIDECAR_EXTENSION}"
    )


def write_sidecar(document: dict[str, Any], path: Path | str) -> Path:
    """Write a sidecar with an integrity-checked canonical JSON payload."""
    path = Path(path)
    payload = _canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    wrapper = {
        "magic": _MAGIC,
        "payload_sha256": digest,
        "payload": document,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json(wrapper))
    return path


def read_sidecar(path: Path | str) -> dict[str, Any]:
    """Read and verify a sidecar document."""
    path = Path(path)
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SidecarFormatError(f"invalid JSON sidecar: {path}") from e

    if not isinstance(wrapper, dict) or wrapper.get("magic") != _MAGIC:
        raise SidecarFormatError(f"invalid sidecar magic: {path}")

    payload = wrapper.get("payload")
    stored_digest = wrapper.get("payload_sha256")
    if not isinstance(payload, dict) or not isinstance(stored_digest, str):
        raise SidecarFormatError(f"invalid sidecar wrapper: {path}")

    actual_digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if actual_digest != stored_digest:
        raise SidecarIntegrityError(
            f"sidecar integrity check failed for {path}: "
            f"expected {stored_digest}, got {actual_digest}",
        )

    version = payload.get("schema_version")
    if version != SIDECAR_SCHEMA_VERSION:
        raise SidecarFormatError(f"unsupported sidecar schema_version: {version}")

    return payload


def _canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
