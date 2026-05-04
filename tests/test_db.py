"""Tests for analytics DB migrations."""

import sqlite3
from pathlib import Path

from musiktool import db


def _create_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    medium: str,
    peak_ceiling_dbtp: float,
) -> None:
    conn.execute(
        """\
        INSERT INTO tape_project
            (name, medium, duration_sec, peak_ceiling_dbtp, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            name,
            medium,
            60.0,
            peak_ceiling_dbtp,
            "2026-05-04T00:00:00+00:00",
        ),
    )


def test_migrates_legacy_hard_clip_peak_ceiling(tmp_path: Path) -> None:
    db_path = tmp_path / "analytics.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(db.SCHEMA)
        _create_project(
            conn,
            name="legacy-vhs",
            medium="vhs-120",
            peak_ceiling_dbtp=0.0,
        )
        _create_project(
            conn,
            name="legacy-md",
            medium="md-80",
            peak_ceiling_dbtp=0.0,
        )
        _create_project(
            conn,
            name="legacy-cd",
            medium="cd-80",
            peak_ceiling_dbtp=0.0,
        )
        _create_project(
            conn,
            name="legacy-vinyl",
            medium="vinyl-lp",
            peak_ceiling_dbtp=0.0,
        )
        _create_project(
            conn,
            name="soft-clip",
            medium="c-90",
            peak_ceiling_dbtp=0.0,
        )
        _create_project(
            conn,
            name="custom",
            medium="custom",
            peak_ceiling_dbtp=0.0,
        )
        _create_project(
            conn,
            name="explicit-hard",
            medium="vhs-120",
            peak_ceiling_dbtp=-0.5,
        )
        _create_project(
            conn,
            name="already-migrated",
            medium="md-80",
            peak_ceiling_dbtp=-1.0,
        )
        conn.commit()
    finally:
        conn.close()

    conn = db.get_connection(db_path)
    try:
        rows = {
            row["name"]: row["peak_ceiling_dbtp"]
            for row in conn.execute(
                "SELECT name, peak_ceiling_dbtp FROM tape_project",
            )
        }
    finally:
        conn.close()

    assert rows == {
        "legacy-vhs": -1.0,
        "legacy-md": -1.0,
        "legacy-cd": -1.0,
        "legacy-vinyl": -1.0,
        "soft-clip": 0.0,
        "custom": 0.0,
        "explicit-hard": -0.5,
        "already-migrated": -1.0,
    }


def test_hard_clip_peak_ceiling_migration_is_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analytics.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(db.SCHEMA)
        _create_project(
            conn,
            name="legacy-vhs",
            medium="vhs-120",
            peak_ceiling_dbtp=0.0,
        )
        conn.commit()
    finally:
        conn.close()

    conn = db.get_connection(db_path)
    conn.close()
    conn = db.get_connection(db_path)
    try:
        row = db.get_tape_project(conn, "legacy-vhs")
        assert row is not None
        assert row["peak_ceiling_dbtp"] == -1.0
    finally:
        conn.close()
