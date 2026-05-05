"""Bulk EBU R128 analysis with incremental scanning."""

import collections.abc
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from musiktool import db
from musiktool.constants import AUDIO_EXTENSIONS, collect_audio_files
from musiktool.exceptions import AudioReadError
from musiktool.ignore import IgnorePolicy, load_ignore_policy
from musiktool.index import IndexScanSummary, scan_path
from musiktool.loudness import LoudnessInfo, measure_album, measure_track


@dataclass
class AlbumResult:
    path: str
    measured: int
    skipped: int
    removed: int
    error: str | None = None
    index_files_total: int = 0
    index_files_indexed: int = 0
    index_files_skipped: int = 0
    index_files_from_sidecar: int = 0
    index_warnings: list[str] = field(default_factory=list)


@dataclass
class AnalyzeSummary:
    albums: int
    tracks_measured: int
    tracks_skipped: int
    tracks_removed: int
    errors: list[str]
    index_files_total: int = 0
    index_files_indexed: int = 0
    index_files_skipped: int = 0
    index_files_from_sidecar: int = 0
    index_warnings: list[str] = field(default_factory=list)


def discover_albums(
    lib_path: Path,
    *,
    excludes: list[str] | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> list[Path]:
    """Find all album directories under a library path.

    An album directory is any directory that directly contains audio files.
    Walks the tree recursively — supports Artist/Album and deeper structures.
    """
    lib_path = lib_path.resolve()
    if ignore_policy is None:
        ignore_policy = load_ignore_policy(lib_path, excludes=excludes)

    albums = []
    for current, dirs, _names in lib_path.walk():
        dirs[:] = [
            name for name in dirs
            if not ignore_policy.is_ignored(current / name, is_dir=True)
        ]
        p = current
        has_audio = any(
            f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
            and not ignore_policy.is_ignored(f, is_dir=False)
            for f in p.iterdir()
        )
        if has_audio:
            albums.append(p.resolve())
    return sorted(set(albums))


def analyze_album(
    album_path: Path,
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    use_index: bool = False,
    sidecar_root: Path | None = None,
    hash_index: bool = False,
    fingerprint_index: bool = False,
    excludes: list[str] | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> AlbumResult:
    """Analyze a single album directory. Measures only stale/new tracks."""
    album_path = album_path.resolve()
    if ignore_policy is None:
        ignore_policy = load_ignore_policy(album_path, excludes=excludes)
    index_summary: IndexScanSummary | None = None
    if use_index:
        index_summary = scan_path(
            album_path,
            conn,
            sidecar_root=sidecar_root,
            force=force,
            hash_files=hash_index,
            fingerprint_files=fingerprint_index,
            excludes=excludes,
            ignore_policy=ignore_policy,
        )

    audio_files = [
        p.resolve() for p in collect_audio_files(album_path)
        if not ignore_policy.is_ignored(p, is_dir=False)
    ]
    album_str = str(album_path)

    if not audio_files:
        return AlbumResult(
            path=album_str,
            measured=0,
            skipped=0,
            removed=0,
            **_album_index_result(index_summary),
        )

    # Current file mtimes
    file_mtimes = {str(f): f.stat().st_mtime for f in audio_files}
    current_paths = set(file_mtimes.keys())

    # Find what needs work
    if force:
        stale_paths = list(file_mtimes.keys())
    else:
        stale_paths = db.get_stale_tracks(conn, album_str, file_mtimes)

    removed_paths = db.get_removed_tracks(conn, album_str, current_paths)

    # Clean up removed tracks
    if removed_paths:
        db.delete_tracks(conn, removed_paths)

    # Measure stale/new tracks
    now = datetime.now(timezone.utc).isoformat()
    measured_tracks: list[LoudnessInfo] = []

    for path in sorted(stale_paths):
        info = measure_track(path)
        measured_tracks.append(info)
        db.upsert_track(
            conn,
            path=path,
            integrated_lufs=info.integrated_lufs,
            true_peak_dbtp=info.true_peak_dbtp,
            lra=info.lra,
            duration_sec=info.duration_sec,
            album_path=album_str,
            analyzed_at=now,
            file_mtime=file_mtimes[path],
        )

    skipped = len(audio_files) - len(stale_paths)

    # Re-measure album-level metrics if anything changed
    if stale_paths or removed_paths or force:
        # Build full track list: freshly measured + cached from DB
        all_tracks: list[LoudnessInfo] = []
        for f in audio_files:
            path_str = str(f)
            # Use freshly measured data if available
            fresh = next(
                (t for t in measured_tracks if t.path == path_str), None,
            )
            if fresh:
                all_tracks.append(fresh)
            else:
                # Load from DB
                row = db.get_track(conn, path_str)
                if row is None:
                    raise AudioReadError(f"track missing from DB: {path_str}")
                all_tracks.append(LoudnessInfo(
                    integrated_lufs=row["integrated_lufs"],
                    true_peak_dbtp=row["true_peak_dbtp"],
                    lra=row["lra"],
                    duration_sec=row["duration_sec"],
                    path=row["path"],
                ))

        _, album_info = measure_album(album_str, tracks=all_tracks)
        if album_info is None:
            raise AudioReadError(f"album measurement failed: {album_str}")

        db.upsert_album(
            conn,
            path=album_str,
            integrated_lufs=album_info.integrated_lufs,
            true_peak_dbtp=album_info.true_peak_dbtp,
            lra=album_info.lra,
            track_count=len(all_tracks),
            duration_sec=album_info.duration_sec,
            analyzed_at=now,
        )

    conn.commit()

    return AlbumResult(
        path=album_str,
        measured=len(stale_paths),
        skipped=skipped,
        removed=len(removed_paths),
        **_album_index_result(index_summary),
    )


def analyze_library(
    lib_path: Path,
    db_path: Path,
    *,
    force: bool = False,
    use_index: bool = False,
    sidecar_root: Path | None = None,
    hash_index: bool = False,
    fingerprint_index: bool = False,
    excludes: list[str] | None = None,
    progress_fn: collections.abc.Callable | None = None,
) -> AnalyzeSummary:
    """Analyze all albums under a library path."""
    ignore_policy = load_ignore_policy(lib_path, excludes=excludes)
    albums = discover_albums(lib_path, ignore_policy=ignore_policy)
    conn = db.get_connection(db_path)

    summary = AnalyzeSummary(
        albums=len(albums),
        tracks_measured=0,
        tracks_skipped=0,
        tracks_removed=0,
        errors=[],
    )

    try:
        for i, album_path in enumerate(albums):
            if progress_fn:
                progress_fn(i + 1, len(albums), album_path)

            try:
                result = analyze_album(
                    album_path,
                    conn,
                    force=force,
                    use_index=use_index,
                    sidecar_root=sidecar_root,
                    hash_index=hash_index,
                    fingerprint_index=fingerprint_index,
                    excludes=excludes,
                    ignore_policy=ignore_policy,
                )
                summary.tracks_measured += result.measured
                summary.tracks_skipped += result.skipped
                summary.tracks_removed += result.removed
                summary.index_files_total += result.index_files_total
                summary.index_files_indexed += result.index_files_indexed
                summary.index_files_skipped += result.index_files_skipped
                summary.index_files_from_sidecar += result.index_files_from_sidecar
                summary.index_warnings.extend(result.index_warnings)
            except Exception as e:
                msg = f"{album_path}: {e}"
                summary.errors.append(msg)
    finally:
        conn.close()

    return summary


def _album_index_result(
    summary: IndexScanSummary | None,
) -> dict[str, object]:
    if summary is None:
        return {}
    return {
        "index_files_total": summary.files_total,
        "index_files_indexed": summary.files_indexed,
        "index_files_skipped": summary.files_skipped,
        "index_files_from_sidecar": summary.files_from_sidecar,
        "index_warnings": list(summary.warnings),
    }
