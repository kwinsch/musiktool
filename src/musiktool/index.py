"""Library file index and sidecar-backed scan cache."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mutagen

from musiktool import db
from musiktool.constants import AUDIO_EXTENSIONS
from musiktool.exceptions import PathNotFoundError, ValidationError
from musiktool.fingerprint import fingerprint
from musiktool.ignore import IgnorePolicy, load_ignore_policy
from musiktool.sidecar import (
    SIDECAR_SCHEMA_VERSION,
    SidecarError,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,
)
from musiktool.tags import Tags, read_tags

try:
    import blake3 as _blake3
except ModuleNotFoundError:  # pragma: no cover - exercised only without optional dep
    _blake3 = None

_BUFFER_SIZE = 256 * 1024


@dataclass
class IndexScanSummary:
    """Summary for one index scan."""

    root: str
    files_total: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_from_sidecar: int = 0
    files_hashed: int = 0
    files_fingerprinted: int = 0
    sidecars_written: int = 0
    warnings: list[str] = field(default_factory=list)


def default_sidecar_root() -> Path:
    """Return the default out-of-tree sidecar root."""
    return db.DEFAULT_DB_DIR / "sidecars"


def scan_path(
    path: Path,
    conn,
    *,
    sidecar_root: Path | None = None,
    force: bool = False,
    hash_files: bool = False,
    fingerprint_files: bool = False,
    excludes: list[str] | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> IndexScanSummary:
    """Scan audio files into SQLite, using sidecars for cold-cache recovery."""
    target = path.resolve()
    if not target.exists():
        raise PathNotFoundError(f"path does not exist: {target}")

    source_root = target.parent if target.is_file() else target
    sidecar_root = (sidecar_root or default_sidecar_root()).resolve()
    if ignore_policy is None:
        ignore_policy = load_ignore_policy(source_root, excludes=excludes)
    files = _audio_files(target, ignore_policy)
    now = _now()
    summary = IndexScanSummary(root=str(source_root), files_total=len(files))

    for file_path in files:
        stat = _stat_facts(file_path)
        path_str = str(file_path)
        row = db.get_indexed_file(conn, path_str)
        unchanged = db.indexed_file_unchanged(
            row, size=stat["size"], mtime_ns=stat["mtime_ns"],
        )

        if (
            not force
            and unchanged
            and _has_required_facts(
                conn,
                path_str,
                hash_files=hash_files,
                fingerprint_files=fingerprint_files,
            )
        ):
            summary.files_skipped += 1
            continue

        relative = file_path.relative_to(source_root)
        sidecar_path = sidecar_path_for(
            file_path,
            source_root=source_root,
            sidecar_root=sidecar_root,
        )

        if not force and sidecar_path.exists():
            try:
                document = read_sidecar(sidecar_path)
                if (
                    _sidecar_matches(document, stat)
                    and _document_has_required_facts(
                        document,
                        hash_files=hash_files,
                        fingerprint_files=fingerprint_files,
                    )
                ):
                    _hydrate_from_document(
                        conn,
                        document,
                        path=file_path,
                        source_root=source_root,
                        relative=relative,
                        sidecar_path=sidecar_path,
                        current_stat=stat,
                        scanned_at=now,
                    )
                    summary.files_from_sidecar += 1
                    continue
            except SidecarError as e:
                summary.warnings.append(f"{sidecar_path}: {e}")

        try:
            document = _scan_live_file(
                file_path,
                source_root=source_root,
                relative=relative,
                stat=stat,
                hash_file=hash_files,
                fingerprint_file=fingerprint_files,
                summary=summary,
            )
        except Exception as e:
            summary.warnings.append(f"{file_path}: scan failed: {e}")
            continue
        _preserve_existing_identity_facts(
            document,
            conn=conn,
            path=path_str,
            indexed=row,
            unchanged=unchanged,
            hash_file=hash_files,
            fingerprint_file=fingerprint_files,
        )
        _hydrate_from_document(
            conn,
            document,
            path=file_path,
            source_root=source_root,
            relative=relative,
            sidecar_path=sidecar_path,
            current_stat=stat,
            scanned_at=now,
        )
        write_sidecar(document, sidecar_path)
        summary.files_indexed += 1
        summary.sidecars_written += 1

    conn.commit()
    return summary


def hash_file_blake3(path: Path) -> str:
    """Compute BLAKE3 for an exact file identity hash."""
    if _blake3 is None:
        raise ValidationError(
            "BLAKE3 hashing requires the optional 'blake3' package",
        )

    hasher = _blake3.blake3()
    with path.open("rb") as handle:
        while chunk := handle.read(_BUFFER_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest().upper()


def _scan_live_file(
    path: Path,
    *,
    source_root: Path,
    relative: Path,
    stat: dict[str, int],
    hash_file: bool,
    fingerprint_file: bool,
    summary: IndexScanSummary,
) -> dict[str, Any]:
    tags = _safe_read_tags(path)
    tag_snapshot = _tag_snapshot(tags)
    audio_snapshot = _audio_snapshot(path)

    file_hash = None
    if hash_file:
        file_hash = hash_file_blake3(path)
        summary.files_hashed += 1

    if fingerprint_file:
        duration, fp = fingerprint(str(path))
        audio_snapshot["chromaprint_duration"] = duration
        audio_snapshot["chromaprint"] = fp
        summary.files_fingerprinted += 1

    media_kind = _classify_media_kind(relative)
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "path": {
            "root": str(source_root),
            "relative": relative.as_posix(),
        },
        "file": {
            **stat,
            "blake3": file_hash,
        },
        "audio": audio_snapshot,
        "tags": tag_snapshot,
        "library": media_kind,
    }


def _hydrate_from_document(
    conn,
    document: dict[str, Any],
    *,
    path: Path,
    source_root: Path,
    relative: Path,
    sidecar_path: Path,
    current_stat: dict[str, int],
    scanned_at: str,
) -> None:
    path_str = str(path)
    file_facts = document.get("file", {})
    audio = document.get("audio", {})
    tags = document.get("tags", {})
    library = document.get("library", {})

    db.upsert_indexed_file(
        conn,
        path=path_str,
        root=str(source_root),
        relative_path=relative.as_posix(),
        size=current_stat["size"],
        mtime_ns=current_stat["mtime_ns"],
        ctime_ns=current_stat.get("ctime_ns"),
        dev=current_stat.get("dev"),
        inode=current_stat.get("inode"),
        blake3=_optional_str(file_facts.get("blake3")),
        sidecar_path=str(sidecar_path),
        scanned_at=scanned_at,
    )
    db.upsert_audio_facts(
        conn,
        path=path_str,
        duration_sec=_optional_float(audio.get("duration_sec")),
        codec=_optional_str(audio.get("codec")),
        container=_optional_str(audio.get("container")),
        sample_rate=_optional_int(audio.get("sample_rate")),
        channels=_optional_int(audio.get("channels")),
        bits_per_sample=_optional_int(audio.get("bits_per_sample")),
        bitrate=_optional_int(audio.get("bitrate")),
        chromaprint=_optional_str(audio.get("chromaprint")),
        chromaprint_duration=_optional_int(audio.get("chromaprint_duration")),
        audio_hash=_optional_str(audio.get("audio_hash")),
    )
    db.upsert_tag_facts(
        conn,
        path=path_str,
        tag_hash=str(tags.get("tag_hash", "")),
        artist=_optional_str(tags.get("artist")),
        album=_optional_str(tags.get("album")),
        album_artist=_optional_str(tags.get("album_artist")),
        title=_optional_str(tags.get("title")),
        date=_optional_str(tags.get("date")),
        track_number=_optional_int(tags.get("track_number")),
        disc_number=_optional_int(tags.get("disc_number")),
        genre=_optional_str(tags.get("genre")),
    )
    existing_classification = db.get_library_classification(conn, path_str)
    if (
        existing_classification is None
        or existing_classification["source"] != "manual"
    ):
        db.upsert_library_classification(
            conn,
            subject_path=path_str,
            media_kind=str(library.get("media_kind", "music")),
            source=str(library.get("source", "default")),
            confidence=float(library.get("confidence", 0.5)),
            confirmed_at=_optional_str(library.get("confirmed_at")),
        )


def _audio_files(path: Path, ignore_policy: IgnorePolicy) -> list[Path]:
    if path.is_file():
        return [
            path.resolve()
        ] if (
            path.suffix.lower() in AUDIO_EXTENSIONS
            and not ignore_policy.is_ignored(path, is_dir=False)
        ) else []

    files = []
    for current, dirs, names in path.walk():
        dirs[:] = [
            name for name in dirs
            if not ignore_policy.is_ignored(current / name, is_dir=True)
        ]
        for name in names:
            candidate = current / name
            if (
                candidate.suffix.lower() in AUDIO_EXTENSIONS
                and not ignore_policy.is_ignored(candidate, is_dir=False)
            ):
                files.append(candidate.resolve())
    return sorted(files)


def _stat_facts(path: Path) -> dict[str, int]:
    st = path.stat()
    return {
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
        "dev": st.st_dev,
        "inode": st.st_ino,
    }


def _sidecar_matches(document: dict[str, Any], stat: dict[str, int]) -> bool:
    file_facts = document.get("file", {})
    return (
        file_facts.get("size") == stat["size"]
        and file_facts.get("mtime_ns") == stat["mtime_ns"]
    )


def _has_required_facts(
    conn, path: str, *, hash_files: bool, fingerprint_files: bool,
) -> bool:
    indexed = db.get_indexed_file(conn, path)
    audio = db.get_audio_facts(conn, path)
    if hash_files and (indexed is None or not indexed["blake3"]):
        return False
    if fingerprint_files and (audio is None or not audio["chromaprint"]):
        return False
    return True


def _document_has_required_facts(
    document: dict[str, Any], *, hash_files: bool, fingerprint_files: bool,
) -> bool:
    file_facts = document.get("file", {})
    audio = document.get("audio", {})
    if hash_files and not file_facts.get("blake3"):
        return False
    if fingerprint_files and not audio.get("chromaprint"):
        return False
    return True


def _preserve_existing_identity_facts(
    document: dict[str, Any],
    *,
    conn,
    path: str,
    indexed,
    unchanged: bool,
    hash_file: bool,
    fingerprint_file: bool,
) -> None:
    if not unchanged:
        return

    if not hash_file and indexed is not None and indexed["blake3"]:
        document.setdefault("file", {})["blake3"] = indexed["blake3"]

    if fingerprint_file:
        return

    audio = db.get_audio_facts(conn, path)
    if audio is None:
        return

    audio_doc = document.setdefault("audio", {})
    if audio["chromaprint"]:
        audio_doc["chromaprint"] = audio["chromaprint"]
        audio_doc["chromaprint_duration"] = audio["chromaprint_duration"]
    if audio["audio_hash"]:
        audio_doc["audio_hash"] = audio["audio_hash"]


def _audio_snapshot(path: Path) -> dict[str, Any]:
    try:
        audio = mutagen.File(str(path))
    except Exception:
        audio = None

    info = audio.info if audio is not None else None
    duration = getattr(info, "length", None)
    return {
        "duration_sec": round(float(duration), 3) if duration is not None else None,
        "codec": path.suffix.lower().lstrip("."),
        "container": type(audio).__name__ if audio is not None else None,
        "sample_rate": _optional_int(getattr(info, "sample_rate", None)),
        "channels": _optional_int(getattr(info, "channels", None)),
        "bits_per_sample": _optional_int(getattr(info, "bits_per_sample", None)),
        "bitrate": _optional_int(getattr(info, "bitrate", None)),
        "chromaprint": None,
        "chromaprint_duration": None,
        "audio_hash": None,
    }


def _safe_read_tags(path: Path) -> Tags:
    try:
        return read_tags(str(path))
    except Exception:
        return Tags()


def _tag_snapshot(tags: Tags) -> dict[str, Any]:
    data = {
        "artist": tags.artist,
        "album": tags.album,
        "album_artist": tags.album_artist,
        "title": tags.title,
        "date": str(tags.year) if tags.year is not None else None,
        "track_number": tags.track_number,
        "disc_number": None,
        "genre": tags.genre,
    }
    data["tag_hash"] = hashlib.sha256(
        repr(sorted(data.items())).encode("utf-8"),
    ).hexdigest()
    return data


def _classify_media_kind(relative: Path) -> dict[str, Any]:
    first = relative.parts[0].lower() if relative.parts else ""
    mapping = {
        "music": "music",
        "radio": "radio",
        "audiobook": "audiobook",
        "audiobooks": "audiobook",
        "podcast": "podcast",
        "podcasts": "podcast",
    }
    if first in mapping:
        return {
            "media_kind": mapping[first],
            "source": "path",
            "confidence": 0.95,
            "confirmed_at": None,
        }
    return {
        "media_kind": "music",
        "source": "default",
        "confidence": 0.5,
        "confirmed_at": None,
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
