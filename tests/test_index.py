"""Tests for the sidecar-backed library index."""

from pathlib import Path

from musiktool import db
from musiktool.index import scan_path
from musiktool.sidecar import sidecar_path_for


def _touch_audio(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path.resolve()


def test_scan_indexes_file_and_writes_sidecar(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    track = _touch_audio(root / "music" / "Artist" / "Album" / "01 Song.flac")
    sidecars = tmp_path / "sidecars"

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        summary = scan_path(root, conn, sidecar_root=sidecars)
        row = db.get_indexed_file(conn, str(track))
        tags = db.get_tag_facts(conn, str(track))
        classification = db.get_library_classification(conn, str(track))
    finally:
        conn.close()

    assert summary.files_total == 1
    assert summary.files_indexed == 1
    assert row is not None
    assert row["relative_path"] == "music/Artist/Album/01 Song.flac"
    assert tags is not None
    assert classification is not None
    assert classification["media_kind"] == "music"
    assert sidecar_path_for(
        track,
        source_root=root,
        sidecar_root=sidecars,
    ).exists()


def test_second_scan_skips_unchanged_file(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    _touch_audio(root / "Artist" / "Album" / "01 Song.flac")
    sidecars = tmp_path / "sidecars"

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        first = scan_path(root, conn, sidecar_root=sidecars)
        assert first.files_indexed == 1

        def fail_read_tags(_path: str):
            raise AssertionError("unchanged scan should not read tags")

        monkeypatch.setattr("musiktool.index.read_tags", fail_read_tags)
        second = scan_path(root, conn, sidecar_root=sidecars)
    finally:
        conn.close()

    assert second.files_skipped == 1
    assert second.files_indexed == 0
    assert second.files_from_sidecar == 0


def test_cold_db_recovers_from_sidecar(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    track = _touch_audio(root / "radio" / "Show" / "Season" / "001 Episode.mp3")
    sidecars = tmp_path / "sidecars"

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        scan_path(root, conn, sidecar_root=sidecars)
    finally:
        conn.close()

    cold_db = tmp_path / "cold.db"

    def fail_read_tags(_path: str):
        raise AssertionError("cold recovery should hydrate from sidecar")

    monkeypatch.setattr("musiktool.index.read_tags", fail_read_tags)
    conn = db.get_connection(cold_db)
    try:
        summary = scan_path(root, conn, sidecar_root=sidecars)
        row = db.get_indexed_file(conn, str(track))
        classification = db.get_library_classification(conn, str(track))
    finally:
        conn.close()

    assert summary.files_from_sidecar == 1
    assert summary.files_indexed == 0
    assert row is not None
    assert classification is not None
    assert classification["media_kind"] == "radio"


def test_stale_sidecar_falls_back_to_live_scan(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    track = _touch_audio(root / "Artist" / "Album" / "01 Song.flac")
    sidecars = tmp_path / "sidecars"

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        scan_path(root, conn, sidecar_root=sidecars)
        track.write_bytes(b"changed")
        summary = scan_path(root, conn, sidecar_root=sidecars)
        row = db.get_indexed_file(conn, str(track))
    finally:
        conn.close()

    assert summary.files_from_sidecar == 0
    assert summary.files_indexed == 1
    assert row is not None
    assert row["size"] == len(b"changed")


def test_inode_change_alone_does_not_force_rescan(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "lib"
    track = _touch_audio(root / "Artist" / "Album" / "01 Song.flac")
    sidecars = tmp_path / "sidecars"

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        scan_path(root, conn, sidecar_root=sidecars)
        row = db.get_indexed_file(conn, str(track))
        assert row is not None
        conn.execute(
            "UPDATE indexed_files SET inode = ? WHERE path = ?",
            ((row["inode"] or 0) + 1000, str(track)),
        )
        conn.commit()

        def fail_read_tags(_path: str):
            raise AssertionError("inode-only mismatch should not read tags")

        monkeypatch.setattr("musiktool.index.read_tags", fail_read_tags)
        summary = scan_path(root, conn, sidecar_root=sidecars)
    finally:
        conn.close()

    assert summary.files_skipped == 1


def test_force_rescan_preserves_expensive_identity_facts_for_unchanged_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    track = _touch_audio(root / "Artist" / "Album" / "01 Song.flac", b"audio")
    sidecars = tmp_path / "sidecars"

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        scan_path(root, conn, sidecar_root=sidecars, hash_files=True)
        before = db.get_indexed_file(conn, str(track))
        assert before is not None
        assert before["blake3"]
        conn.execute(
            """\
            UPDATE audio_facts
            SET chromaprint = ?, chromaprint_duration = ?, audio_hash = ?
            WHERE path = ?""",
            ("fp", 123, "audio-hash", str(track)),
        )
        conn.commit()

        summary = scan_path(root, conn, sidecar_root=sidecars, force=True)
        after = db.get_indexed_file(conn, str(track))
        audio = db.get_audio_facts(conn, str(track))
    finally:
        conn.close()

    assert summary.files_indexed == 1
    assert after is not None
    assert after["blake3"] == before["blake3"]
    assert audio is not None
    assert audio["chromaprint"] == "fp"
    assert audio["chromaprint_duration"] == 123
    assert audio["audio_hash"] == "audio-hash"


def test_scan_applies_builtin_ignore_policy(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    kept = _touch_audio(root / "Artist" / "Album" / "01 Song.flac")
    ignored = _touch_audio(root / ".Trash-1000" / "Artist" / "Album" / "01 Song.flac")
    _touch_audio(root / "musiktool" / "render" / "_calibration" / "tone.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        summary = scan_path(root, conn, sidecar_root=tmp_path / "sidecars")
        kept_row = db.get_indexed_file(conn, str(kept))
        ignored_row = db.get_indexed_file(conn, str(ignored))
    finally:
        conn.close()

    assert summary.files_total == 1
    assert kept_row is not None
    assert ignored_row is None


def test_scan_applies_musiktoolignore_and_cli_excludes(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    (root / ".musiktoolignore").parent.mkdir(parents=True)
    (root / ".musiktoolignore").write_text("ignored-from-file/\n")
    kept = _touch_audio(root / "Artist" / "Album" / "01 Song.flac")
    from_file = _touch_audio(root / "ignored-from-file" / "01 Skip.flac")
    from_cli = _touch_audio(root / "ignored-from-cli" / "01 Skip.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        summary = scan_path(
            root,
            conn,
            sidecar_root=tmp_path / "sidecars",
            excludes=["ignored-from-cli/"],
        )
        kept_row = db.get_indexed_file(conn, str(kept))
        file_row = db.get_indexed_file(conn, str(from_file))
        cli_row = db.get_indexed_file(conn, str(from_cli))
    finally:
        conn.close()

    assert summary.files_total == 1
    assert kept_row is not None
    assert file_row is None
    assert cli_row is None


def test_scan_does_not_overwrite_manual_file_classification(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    track = _touch_audio(root / "Artist" / "Album" / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        db.upsert_library_classification(
            conn,
            subject_path=str(track),
            media_kind="radio",
            source="manual",
            confidence=1.0,
            confirmed_at="2026-05-05T00:00:00+00:00",
        )
        conn.commit()

        scan_path(root, conn, sidecar_root=tmp_path / "sidecars")
        row = db.get_library_classification(conn, str(track))
    finally:
        conn.close()

    assert row is not None
    assert row["media_kind"] == "radio"
    assert row["source"] == "manual"
