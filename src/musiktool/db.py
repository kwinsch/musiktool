"""Analytics database for EBU R128 measurements."""

import sqlite3
from pathlib import Path

from musiktool.config import get_config
from musiktool.constants import MEDIUM_PRESETS

HARD_CLIP_DEFAULT_PEAK_CEILING_DBTP = -1.0
LEGACY_HARD_CLIP_PEAK_CEILING_DBTP = 0.0

SCHEMA = """\
CREATE TABLE IF NOT EXISTS track_loudness (
    path TEXT PRIMARY KEY,
    integrated_lufs REAL NOT NULL,
    true_peak_dbtp REAL NOT NULL,
    lra REAL NOT NULL,
    duration_sec REAL NOT NULL,
    album_path TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    file_mtime REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS album_loudness (
    path TEXT PRIMARY KEY,
    integrated_lufs REAL NOT NULL,
    true_peak_dbtp REAL NOT NULL,
    lra REAL NOT NULL,
    track_count INTEGER NOT NULL,
    duration_sec REAL NOT NULL,
    analyzed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tape_project (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    medium TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    lead_in_sec REAL NOT NULL DEFAULT 25,
    lead_out_sec REAL NOT NULL DEFAULT 20,
    album_gap_sec REAL NOT NULL DEFAULT 8,
    track_gap_sec REAL NOT NULL DEFAULT 4,
    marker TEXT NOT NULL DEFAULT 'none',
    marker_freq REAL NOT NULL DEFAULT 400,
    marker_level_dbfs REAL NOT NULL DEFAULT -30,
    marker_duration_sec REAL NOT NULL DEFAULT 0.5,
    target_lufs REAL NOT NULL DEFAULT -14,
    peak_ceiling_dbtp REAL NOT NULL DEFAULT 0,
    sample_rate TEXT NOT NULL DEFAULT 'auto',
    bit_depth INTEGER NOT NULL DEFAULT 24,
    use_limiter INTEGER NOT NULL DEFAULT 0,
    use_compressor INTEGER NOT NULL DEFAULT 0,
    side_a_duration_sec REAL,
    side_b_duration_sec REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tape_item (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES tape_project(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    path TEXT NOT NULL,
    side TEXT DEFAULT NULL,
    UNIQUE(project_id, position)
);

CREATE TABLE IF NOT EXISTS indexed_files (
    path TEXT PRIMARY KEY,
    root TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    ctime_ns INTEGER,
    dev INTEGER,
    inode INTEGER,
    blake3 TEXT,
    sidecar_path TEXT,
    scanned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_facts (
    path TEXT PRIMARY KEY,
    duration_sec REAL,
    codec TEXT,
    container TEXT,
    sample_rate INTEGER,
    channels INTEGER,
    bits_per_sample INTEGER,
    bitrate INTEGER,
    chromaprint TEXT,
    chromaprint_duration INTEGER,
    audio_hash TEXT
);

CREATE TABLE IF NOT EXISTS tag_facts (
    path TEXT PRIMARY KEY,
    tag_hash TEXT,
    artist TEXT,
    album TEXT,
    album_artist TEXT,
    title TEXT,
    date TEXT,
    track_number INTEGER,
    disc_number INTEGER,
    genre TEXT
);

CREATE TABLE IF NOT EXISTS library_classification (
    subject_path TEXT PRIMARY KEY,
    media_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_indexed_files_blake3 ON indexed_files(blake3);
CREATE INDEX IF NOT EXISTS idx_audio_facts_chromaprint ON audio_facts(chromaprint);
CREATE INDEX IF NOT EXISTS idx_audio_facts_audio_hash ON audio_facts(audio_hash);
CREATE INDEX IF NOT EXISTS idx_tag_facts_artist_album ON tag_facts(artist, album);
"""


def default_data_dir() -> Path:
    """Return configured data directory."""
    return get_config().data_dir


def default_db_path() -> Path:
    """Return default analytics DB path."""
    return default_data_dir() / "analytics.db"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open analytics DB, create tables if needed, enable WAL mode."""
    if db_path is None:
        db_path = default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that may be missing in older databases."""
    proj_cols = {row[1] for row in conn.execute("PRAGMA table_info(tape_project)")}
    if "use_limiter" not in proj_cols:
        conn.execute(
            "ALTER TABLE tape_project ADD COLUMN use_limiter INTEGER NOT NULL DEFAULT 0",
        )
    if "use_compressor" not in proj_cols:
        conn.execute(
            "ALTER TABLE tape_project ADD COLUMN use_compressor INTEGER NOT NULL DEFAULT 0",
        )
    if "side_a_duration_sec" not in proj_cols:
        conn.execute(
            "ALTER TABLE tape_project ADD COLUMN side_a_duration_sec REAL",
        )
    if "side_b_duration_sec" not in proj_cols:
        conn.execute(
            "ALTER TABLE tape_project ADD COLUMN side_b_duration_sec REAL",
        )

    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(tape_item)")}
    if "side" not in item_cols:
        conn.execute(
            "ALTER TABLE tape_item ADD COLUMN side TEXT DEFAULT NULL",
        )

    _migrate_hard_clip_peak_ceiling(conn)
    conn.commit()


def _migrate_hard_clip_peak_ceiling(conn: sqlite3.Connection) -> None:
    """Move legacy hard-clip projects from 0 dBTP to the -1 dBTP default."""
    hard_clip_media = sorted(
        name
        for name, preset in MEDIUM_PRESETS.items()
        if preset.get("peak_behavior") == "hard"
    )
    if not hard_clip_media:
        return

    placeholders = ",".join("?" for _ in hard_clip_media)
    conn.execute(
        f"""\
        UPDATE tape_project
        SET peak_ceiling_dbtp = ?
        WHERE peak_ceiling_dbtp = ?
          AND medium IN ({placeholders})
        """,
        (
            HARD_CLIP_DEFAULT_PEAK_CEILING_DBTP,
            LEGACY_HARD_CLIP_PEAK_CEILING_DBTP,
            *hard_clip_media,
        ),
    )


def get_track(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """Get track loudness row by path."""
    return conn.execute(
        "SELECT * FROM track_loudness WHERE path = ?", (path,),
    ).fetchone()


def get_tracks_for_album(
    conn: sqlite3.Connection, album_path: str,
) -> list[sqlite3.Row]:
    """Get all track rows for an album."""
    return conn.execute(
        "SELECT * FROM track_loudness WHERE album_path = ? ORDER BY path",
        (album_path,),
    ).fetchall()


def get_album(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """Get album loudness row by path."""
    return conn.execute(
        "SELECT * FROM album_loudness WHERE path = ?", (path,),
    ).fetchone()


def upsert_track(
    conn: sqlite3.Connection,
    *,
    path: str,
    integrated_lufs: float,
    true_peak_dbtp: float,
    lra: float,
    duration_sec: float,
    album_path: str,
    analyzed_at: str,
    file_mtime: float,
) -> None:
    """Insert or replace a track loudness row."""
    conn.execute(
        """\
        INSERT OR REPLACE INTO track_loudness
            (path, integrated_lufs, true_peak_dbtp, lra, duration_sec,
             album_path, analyzed_at, file_mtime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (path, integrated_lufs, true_peak_dbtp, lra, duration_sec,
         album_path, analyzed_at, file_mtime),
    )


def upsert_album(
    conn: sqlite3.Connection,
    *,
    path: str,
    integrated_lufs: float,
    true_peak_dbtp: float,
    lra: float,
    track_count: int,
    duration_sec: float,
    analyzed_at: str,
) -> None:
    """Insert or replace an album loudness row."""
    conn.execute(
        """\
        INSERT OR REPLACE INTO album_loudness
            (path, integrated_lufs, true_peak_dbtp, lra, track_count,
             duration_sec, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (path, integrated_lufs, true_peak_dbtp, lra, track_count,
         duration_sec, analyzed_at),
    )


def get_stale_tracks(
    conn: sqlite3.Connection,
    album_path: str,
    file_mtimes: dict[str, float],
) -> list[str]:
    """Return paths of tracks that need re-analysis.

    A track is stale if it's not in the DB or its mtime has changed.
    """
    existing = get_tracks_for_album(conn, album_path)
    db_mtimes = {row["path"]: row["file_mtime"] for row in existing}

    stale = []
    for path, mtime in file_mtimes.items():
        stored_mtime = db_mtimes.get(path)
        if stored_mtime is None or stored_mtime != mtime:
            stale.append(path)
    return stale


def get_removed_tracks(
    conn: sqlite3.Connection,
    album_path: str,
    current_paths: set[str],
) -> list[str]:
    """Return DB paths for tracks that no longer exist on disk."""
    existing = get_tracks_for_album(conn, album_path)
    return [row["path"] for row in existing if row["path"] not in current_paths]


def delete_tracks(conn: sqlite3.Connection, paths: list[str]) -> None:
    """Delete track rows by path."""
    for path in paths:
        conn.execute("DELETE FROM track_loudness WHERE path = ?", (path,))


def delete_album(conn: sqlite3.Connection, path: str) -> None:
    """Delete an album row."""
    conn.execute("DELETE FROM album_loudness WHERE path = ?", (path,))


# --- Library index helpers ---


def get_indexed_file(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """Return indexed file facts by absolute path."""
    return conn.execute(
        "SELECT * FROM indexed_files WHERE path = ?", (path,),
    ).fetchone()


def indexed_file_unchanged(
    row: sqlite3.Row | None, *, size: int, mtime_ns: int,
) -> bool:
    """Return whether an indexed row matches the current cheap stat facts."""
    if row is None:
        return False
    return row["size"] == size and row["mtime_ns"] == mtime_ns


def upsert_indexed_file(
    conn: sqlite3.Connection,
    *,
    path: str,
    root: str,
    relative_path: str,
    size: int,
    mtime_ns: int,
    ctime_ns: int | None,
    dev: int | None,
    inode: int | None,
    blake3: str | None,
    sidecar_path: str | None,
    scanned_at: str,
) -> None:
    """Insert or replace file-level index facts."""
    conn.execute(
        """\
        INSERT OR REPLACE INTO indexed_files
            (path, root, relative_path, size, mtime_ns, ctime_ns, dev, inode,
             blake3, sidecar_path, scanned_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            path, root, relative_path, size, mtime_ns, ctime_ns, dev, inode,
            blake3, sidecar_path, scanned_at,
        ),
    )


def get_audio_facts(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """Return cached audio facts by absolute path."""
    return conn.execute(
        "SELECT * FROM audio_facts WHERE path = ?", (path,),
    ).fetchone()


def upsert_audio_facts(
    conn: sqlite3.Connection,
    *,
    path: str,
    duration_sec: float | None,
    codec: str | None,
    container: str | None,
    sample_rate: int | None,
    channels: int | None,
    bits_per_sample: int | None,
    bitrate: int | None,
    chromaprint: str | None,
    chromaprint_duration: int | None,
    audio_hash: str | None,
) -> None:
    """Insert or replace audio stream/fingerprint facts."""
    conn.execute(
        """\
        INSERT OR REPLACE INTO audio_facts
            (path, duration_sec, codec, container, sample_rate, channels,
             bits_per_sample, bitrate, chromaprint, chromaprint_duration,
             audio_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            path, duration_sec, codec, container, sample_rate, channels,
            bits_per_sample, bitrate, chromaprint, chromaprint_duration,
            audio_hash,
        ),
    )


def get_tag_facts(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """Return cached normalized tag facts by absolute path."""
    return conn.execute(
        "SELECT * FROM tag_facts WHERE path = ?", (path,),
    ).fetchone()


def get_tag_facts_under(
    conn: sqlite3.Connection, directory: str,
) -> list[sqlite3.Row]:
    """Return tag_facts rows for all files under a directory prefix."""
    return conn.execute(
        "SELECT * FROM tag_facts WHERE path LIKE ? || '/%'",
        (directory,),
    ).fetchall()


def upsert_tag_facts(
    conn: sqlite3.Connection,
    *,
    path: str,
    tag_hash: str,
    artist: str | None,
    album: str | None,
    album_artist: str | None,
    title: str | None,
    date: str | None,
    track_number: int | None,
    disc_number: int | None,
    genre: str | None,
) -> None:
    """Insert or replace normalized tag facts."""
    conn.execute(
        """\
        INSERT OR REPLACE INTO tag_facts
            (path, tag_hash, artist, album, album_artist, title, date,
             track_number, disc_number, genre)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            path, tag_hash, artist, album, album_artist, title, date,
            track_number, disc_number, genre,
        ),
    )


def upsert_library_classification(
    conn: sqlite3.Connection,
    *,
    subject_path: str,
    media_kind: str,
    source: str,
    confidence: float,
    confirmed_at: str | None,
) -> None:
    """Insert or replace media-kind classification for a path."""
    conn.execute(
        """\
        INSERT OR REPLACE INTO library_classification
            (subject_path, media_kind, source, confidence, confirmed_at)
        VALUES (?, ?, ?, ?, ?)""",
        (subject_path, media_kind, source, confidence, confirmed_at),
    )


def get_library_classification(
    conn: sqlite3.Connection, subject_path: str,
) -> sqlite3.Row | None:
    """Return media-kind classification for a path."""
    return conn.execute(
        "SELECT * FROM library_classification WHERE subject_path = ?",
        (subject_path,),
    ).fetchone()


def get_effective_library_classification(
    conn: sqlite3.Connection, subject_path: str,
) -> sqlite3.Row | None:
    """Return the nearest inherited media-kind classification for a path.

    Manual classifications on ancestors override lower-confidence automatic
    file rows. Among manual rows, the nearest ancestor wins.
    """
    candidates = _path_candidates(subject_path)
    if not candidates:
        return None

    placeholders = ", ".join("?" for _ in candidates)
    rows = conn.execute(
        f"""\
        SELECT * FROM library_classification
        WHERE subject_path IN ({placeholders})
        """,
        candidates,
    ).fetchall()
    by_path = {row["subject_path"]: row for row in rows}
    matches = [by_path[p] for p in candidates if p in by_path]
    if not matches:
        return None

    for row in matches:
        if row["source"] == "manual":
            return row
    return matches[0]


def _path_candidates(subject_path: str) -> list[str]:
    path = Path(subject_path).resolve(strict=False)
    return [str(path), *(str(parent) for parent in path.parents)]


def index_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Return table counts for the library index."""
    return {
        "indexed_files": conn.execute(
            "SELECT COUNT(*) FROM indexed_files",
        ).fetchone()[0],
        "audio_facts": conn.execute(
            "SELECT COUNT(*) FROM audio_facts",
        ).fetchone()[0],
        "tag_facts": conn.execute(
            "SELECT COUNT(*) FROM tag_facts",
        ).fetchone()[0],
        "library_classification": conn.execute(
            "SELECT COUNT(*) FROM library_classification",
        ).fetchone()[0],
    }


def prune_missing(conn: sqlite3.Connection, root: str) -> dict[str, int]:
    """Remove DB entries for paths that no longer exist on disk.

    Scans indexed_files, track_loudness, album_loudness, and
    library_classification under *root*. Deletes rows from all related
    tables where the referenced path no longer exists on the filesystem.
    Returns count of pruned rows per table.

    This catches orphan rows left by out-of-band moves (manual quarantine,
    ``mv``) that bypass ``apply --execute`` path cascading.
    """
    counts: dict[str, int] = {}

    # --- Prune file-level index tables ---
    rows = conn.execute(
        "SELECT path FROM indexed_files WHERE path LIKE ? || '%'",
        (root,),
    ).fetchall()
    missing_files = [row[0] for row in rows if not Path(row[0]).exists()]

    if missing_files:
        tables = [
            ("indexed_files", "path"),
            ("audio_facts", "path"),
            ("tag_facts", "path"),
            ("track_loudness", "path"),
        ]
        for table, column in tables:
            for path in missing_files:
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608
                    (path,),
                )
                if cursor.rowcount > 0:
                    counts[table] = counts.get(table, 0) + cursor.rowcount

    # --- Prune orphan track_loudness rows not covered by indexed_files ---
    loudness_rows = conn.execute(
        "SELECT path FROM track_loudness WHERE path LIKE ? || '%'",
        (root,),
    ).fetchall()
    orphan_tracks = [
        row[0] for row in loudness_rows if not Path(row[0]).exists()
    ]
    for path in orphan_tracks:
        cursor = conn.execute(
            "DELETE FROM track_loudness WHERE path = ?", (path,),
        )
        if cursor.rowcount > 0:
            counts["track_loudness"] = counts.get("track_loudness", 0) + cursor.rowcount

    # --- Prune album_loudness for albums with no remaining tracks or
    #     whose directory no longer exists ---
    # Collect candidate album paths from both sources
    album_candidates: set[str] = set()
    for path in missing_files:
        album_candidates.add(str(Path(path).parent))
    for path in orphan_tracks:
        album_candidates.add(str(Path(path).parent))
    # Also scan album_loudness directly for missing directories
    album_rows = conn.execute(
        "SELECT path FROM album_loudness WHERE path LIKE ? || '%'",
        (root,),
    ).fetchall()
    for row in album_rows:
        if not Path(row[0]).exists():
            album_candidates.add(row[0])
    for album_path in album_candidates:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM track_loudness WHERE album_path = ?",
            (album_path,),
        ).fetchone()[0]
        if remaining == 0:
            cursor = conn.execute(
                "DELETE FROM album_loudness WHERE path = ?",
                (album_path,),
            )
            if cursor.rowcount > 0:
                counts["album_loudness"] = counts.get("album_loudness", 0) + cursor.rowcount

    # --- Prune library_classification for paths that no longer exist ---
    class_rows = conn.execute(
        "SELECT subject_path FROM library_classification WHERE subject_path LIKE ? || '%'",
        (root,),
    ).fetchall()
    missing_class = [row[0] for row in class_rows if not Path(row[0]).exists()]
    for path in missing_class:
        cursor = conn.execute(
            "DELETE FROM library_classification WHERE subject_path = ?",
            (path,),
        )
        if cursor.rowcount > 0:
            counts["library_classification"] = counts.get("library_classification", 0) + cursor.rowcount

    if counts:
        conn.commit()
    return counts


# --- Tape project helpers ---


def create_tape_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    medium: str,
    duration_sec: float,
    lead_in_sec: float,
    lead_out_sec: float,
    album_gap_sec: float,
    track_gap_sec: float,
    marker: str,
    marker_freq: float,
    marker_level_dbfs: float,
    marker_duration_sec: float,
    target_lufs: float,
    peak_ceiling_dbtp: float,
    sample_rate: str,
    bit_depth: int,
    use_limiter: bool,
    use_compressor: bool,
    side_a_duration_sec: float | None = None,
    side_b_duration_sec: float | None = None,
    created_at: str,
) -> int:
    """Create a tape project. Returns the new project id."""
    cursor = conn.execute(
        """\
        INSERT INTO tape_project
            (name, medium, duration_sec, lead_in_sec, lead_out_sec,
             album_gap_sec, track_gap_sec, marker, marker_freq,
             marker_level_dbfs, marker_duration_sec, target_lufs,
             peak_ceiling_dbtp, sample_rate, bit_depth,
             use_limiter, use_compressor,
             side_a_duration_sec, side_b_duration_sec, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, medium, duration_sec, lead_in_sec, lead_out_sec,
         album_gap_sec, track_gap_sec, marker, marker_freq,
         marker_level_dbfs, marker_duration_sec, target_lufs,
         peak_ceiling_dbtp, sample_rate, bit_depth,
         int(use_limiter), int(use_compressor),
         side_a_duration_sec, side_b_duration_sec, created_at),
    )
    conn.commit()
    return cursor.lastrowid


def get_tape_project(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Get tape project by name."""
    return conn.execute(
        "SELECT * FROM tape_project WHERE name = ?", (name,),
    ).fetchone()


def list_tape_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """List all tape projects."""
    return conn.execute(
        "SELECT * FROM tape_project ORDER BY created_at",
    ).fetchall()


def delete_tape_project(conn: sqlite3.Connection, name: str) -> None:
    """Delete a tape project and all its items (CASCADE)."""
    conn.execute("DELETE FROM tape_project WHERE name = ?", (name,))
    conn.commit()


def get_tape_items(
    conn: sqlite3.Connection, project_id: int,
) -> list[sqlite3.Row]:
    """Get all items for a tape project, ordered by position."""
    return conn.execute(
        "SELECT * FROM tape_item WHERE project_id = ? ORDER BY position",
        (project_id,),
    ).fetchall()


def add_tape_item(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    position: int,
    item_type: str,
    path: str,
    side: str | None = None,
) -> None:
    """Insert a single tape item."""
    conn.execute(
        """\
        INSERT INTO tape_item (project_id, position, item_type, path, side)
        VALUES (?, ?, ?, ?, ?)""",
        (project_id, position, item_type, path, side),
    )


def update_tape_item_position(
    conn: sqlite3.Connection, item_id: int, position: int,
) -> None:
    """Update position of a tape item."""
    conn.execute(
        "UPDATE tape_item SET position = ? WHERE id = ?",
        (position, item_id),
    )


def update_tape_item_side(
    conn: sqlite3.Connection, item_id: int, side: str | None,
) -> None:
    """Update side pin of a tape item. None clears the pin."""
    conn.execute(
        "UPDATE tape_item SET side = ? WHERE id = ?",
        (side, item_id),
    )


def delete_tape_item(conn: sqlite3.Connection, item_id: int) -> None:
    """Delete a tape item by id."""
    conn.execute("DELETE FROM tape_item WHERE id = ?", (item_id,))


_PATH_COLUMNS = [
    ("track_loudness", "path"),
    ("track_loudness", "album_path"),
    ("album_loudness", "path"),
    ("tape_item", "path"),
    ("indexed_files", "path"),
    ("indexed_files", "root"),
    ("audio_facts", "path"),
    ("tag_facts", "path"),
    ("library_classification", "subject_path"),
]


def find_tape_references(
    conn: sqlite3.Connection, path_prefix: str,
) -> list[tuple[str, str]]:
    """Return [(project_name, item_path), ...] for tape items under path_prefix."""
    rows = conn.execute(
        """\
        SELECT tp.name, ti.path
        FROM tape_item ti
        JOIN tape_project tp ON ti.project_id = tp.id
        WHERE ti.path = ? OR ti.path LIKE ? || '/%'""",
        (path_prefix, path_prefix),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def relocate_paths(
    conn: sqlite3.Connection, old_prefix: str, new_prefix: str,
) -> dict[str, int]:
    """Update all path references from old_prefix to new_prefix.

    Handles both exact matches (file rename) and prefix matches
    (directory rename affecting all children). Returns a dict of
    ``table.column: count`` for columns that had updates.
    """
    assert old_prefix != new_prefix
    old_len = len(old_prefix)
    counts: dict[str, int] = {}
    for table, column in _PATH_COLUMNS:
        cursor = conn.execute(
            f"UPDATE {table} "  # noqa: S608 — table/column from constant list
            f"SET {column} = ? || SUBSTR({column}, ?) "
            f"WHERE {column} = ? OR {column} LIKE ? || '/%'",
            (new_prefix, old_len + 1, old_prefix, old_prefix),
        )
        if cursor.rowcount > 0:
            counts[f"{table}.{column}"] = cursor.rowcount
    conn.commit()
    return counts
