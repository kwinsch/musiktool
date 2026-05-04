"""Analytics database for EBU R128 measurements."""

import sqlite3
from pathlib import Path

from musiktool.constants import MEDIUM_PRESETS

DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "musiktool"

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
"""


def default_db_path() -> Path:
    """Return default analytics DB path (~/.local/share/musiktool/analytics.db)."""
    return DEFAULT_DB_DIR / "analytics.db"


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
