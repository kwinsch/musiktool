"""Tests for agent-facing library audit and fix plans."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from musiktool import db
from musiktool.exceptions import ValidationError
from musiktool.library import (
    _consensus_year,
    apply_plan,
    audit_library,
    format_audit,
    format_propose,
    inspect_path,
    propose_media_kind_folders,
    propose_year_folders,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _index_track(
    conn,
    root: Path,
    track: Path,
    *,
    artist: str | None = None,
    album: str | None = None,
    title: str | None = None,
    track_number: int | None = None,
    duration_sec: float | None = None,
    blake3: str | None = None,
    chromaprint: str | None = None,
) -> None:
    track = track.resolve()
    root = root.resolve()
    stat = track.stat()
    db.upsert_indexed_file(
        conn,
        path=str(track),
        root=str(root),
        relative_path=track.relative_to(root).as_posix(),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        ctime_ns=stat.st_ctime_ns,
        dev=stat.st_dev,
        inode=stat.st_ino,
        blake3=blake3,
        sidecar_path=None,
        scanned_at="2026-05-05T00:00:00+00:00",
    )
    db.upsert_audio_facts(
        conn,
        path=str(track),
        duration_sec=duration_sec,
        codec=track.suffix.lower().lstrip("."),
        container=None,
        sample_rate=None,
        channels=None,
        bits_per_sample=None,
        bitrate=None,
        chromaprint=chromaprint,
        chromaprint_duration=None,
        audio_hash=None,
    )
    db.upsert_tag_facts(
        conn,
        path=str(track),
        tag_hash="tags",
        artist=artist,
        album=album,
        album_artist=artist,
        title=title,
        date="2000" if artist or album or title else None,
        track_number=track_number,
        disc_number=None,
        genre=None,
    )


def test_audit_reports_structure_tags_and_duplicate_evidence(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "analytics.db").write_text("")
    (root / "notes.bin").write_text("")

    parent = root / "Soulfly" / "Primitive"
    nested = parent / "Primitive"
    _touch(parent / "01 Back to the Primitive.flac")
    _touch(parent / "02 Pain.flac")
    _touch(nested / "01 Back to the Primitive.m4a")
    _touch(nested / "02 Pain.m4a")

    result = audit_library(root)
    categories = {finding.category for finding in result.findings}

    assert result.summary["albums_scanned"] == 2
    assert result.summary["tracks_scanned"] == 4
    assert "structure.stray_file" in categories
    assert "structure.album_year_missing" in categories
    assert "structure.nested_album" in categories
    assert "tags.missing_required" in categories
    assert "duplicates.same_album_candidate" in categories

    duplicate = next(
        f for f in result.findings
        if f.category == "duplicates.same_album_candidate"
    )
    assert duplicate.finding_id.startswith("duplicates.same_album_candidate:")
    assert duplicate.severity == "info"
    assert duplicate.evidence["evidence_type"] == "weak_title_overlap"
    assert duplicate.fixable is False
    assert duplicate.suggested_actions == []

    warning_result = audit_library(root, min_severity="warning")
    assert "duplicates.same_album_candidate" not in {
        finding.category for finding in warning_result.findings
    }


def test_audit_duplicate_warning_requires_strong_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    left = root / "Artist" / "Album (2000)"
    right = root / "Import" / "Different Folder"
    left_tracks = [
        _touch(left / "01 Song.flac"),
        _touch(left / "02 Other.flac"),
    ]
    right_tracks = [
        _touch(right / "01 Song.mp3"),
        _touch(right / "02 Other.mp3"),
    ]

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        for tracks in (left_tracks, right_tracks):
            _index_track(
                conn,
                root,
                tracks[0],
                artist="Artist",
                album="Album",
                title="Song",
                track_number=1,
                duration_sec=60.0,
            )
            _index_track(
                conn,
                root,
                tracks[1],
                artist="Artist",
                album="Album",
                title="Other",
                track_number=2,
                duration_sec=61.5,
            )
        conn.commit()

        result = audit_library(root, min_severity="warning", index_conn=conn)
    finally:
        conn.close()

    duplicate = next(
        f for f in result.findings
        if f.category == "duplicates.same_album_candidate"
    )
    assert duplicate.severity == "warning"
    assert duplicate.evidence["evidence_type"] == "strong_album_metadata"
    assert duplicate.fixable is True
    assert duplicate.suggested_actions[0]["type"] == "quarantine"


def test_audit_duplicate_warning_uses_exact_hashes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    left = _touch(root / "Left" / "Album" / "01 Song.flac")
    right = _touch(root / "Right" / "Album" / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        _index_track(conn, root, left, blake3="ABC")
        _index_track(conn, root, right, blake3="ABC")
        conn.commit()

        result = audit_library(root, min_severity="warning", index_conn=conn)
    finally:
        conn.close()

    duplicate = next(
        f for f in result.findings
        if f.category == "duplicates.same_album_candidate"
    )
    assert duplicate.severity == "warning"
    assert duplicate.evidence["evidence_type"] == "exact_file"


def test_against_duplicate_warning_requires_strong_evidence(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    curated_root = tmp_path / "curated"
    source = _touch(source_root / "Import" / "Album" / "01 Song.flac")
    curated = _touch(curated_root / "Artist" / "Album" / "01 Song.flac")

    weak = audit_library(
        source_root,
        against=curated_root,
        min_severity="warning",
    )
    assert "duplicates.source_already_curated" not in {
        finding.category for finding in weak.findings
    }

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        _index_track(
            conn,
            tmp_path,
            source,
            artist="Artist",
            album="Album",
            title="Song",
            track_number=1,
            duration_sec=60.0,
        )
        _index_track(
            conn,
            tmp_path,
            curated,
            artist="Artist",
            album="Album",
            title="Song",
            track_number=1,
            duration_sec=60.0,
        )
        conn.commit()

        strong = audit_library(
            source_root,
            against=curated_root,
            min_severity="warning",
            index_conn=conn,
        )
    finally:
        conn.close()

    duplicate = next(
        f for f in strong.findings
        if f.category == "duplicates.source_already_curated"
    )
    assert duplicate.evidence["evidence_type"] == "strong_album_metadata"


def test_audit_json_is_machine_readable(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    rendered = format_audit(audit_library(root, min_severity="warning"), "json")
    data = json.loads(rendered)

    assert data["schema_version"] == 1
    assert data["command"] == "audit"
    assert data["summary"]["albums_scanned"] == 1
    assert all(f["severity"] == "warning" for f in data["findings"])


def test_audit_ndjson_emits_summary_then_findings(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    rendered = format_audit(audit_library(root), "ndjson")
    lines = [json.loads(line) for line in rendered.splitlines()]

    assert lines[0]["type"] == "summary"
    assert any(line["type"] == "finding" for line in lines[1:])


def test_audit_uses_current_indexed_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "lib"
    track = _touch(root / "Artist" / "Album (2000)" / "01 Song.flac")
    stat = track.stat()

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        db.upsert_indexed_file(
            conn,
            path=str(track.resolve()),
            root=str(root.resolve()),
            relative_path="Artist/Album (2000)/01 Song.flac",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            ctime_ns=stat.st_ctime_ns,
            dev=stat.st_dev,
            inode=stat.st_ino,
            blake3=None,
            sidecar_path=None,
            scanned_at="2026-05-05T00:00:00+00:00",
        )
        db.upsert_audio_facts(
            conn,
            path=str(track.resolve()),
            duration_sec=60.0,
            codec="flac",
            container=None,
            sample_rate=None,
            channels=None,
            bits_per_sample=None,
            bitrate=None,
            chromaprint=None,
            chromaprint_duration=None,
            audio_hash=None,
        )
        db.upsert_tag_facts(
            conn,
            path=str(track.resolve()),
            tag_hash="tags",
            artist="Artist",
            album="Album",
            album_artist="Artist",
            title="Song",
            date="2000",
            track_number=2,
            disc_number=None,
            genre=None,
        )
        conn.commit()

        def fail_read_tags(path: str):  # pragma: no cover - failure path only
            raise AssertionError(f"live tags read for {path}")

        monkeypatch.setattr("musiktool.library.read_tags", fail_read_tags)

        result = audit_library(root, min_severity="warning", index_conn=conn)
    finally:
        conn.close()

    categories = {finding.category for finding in result.findings}
    assert "tags.track_number_mismatch" in categories
    assert "tags.missing_required" not in categories
    assert result.summary["index"]["fact_sources"]["indexed"] == 1


def test_audit_falls_back_when_index_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "lib"
    track = _touch(root / "Artist" / "Album (2000)" / "01 Song.flac")
    stat = track.stat()

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        db.upsert_indexed_file(
            conn,
            path=str(track.resolve()),
            root=str(root.resolve()),
            relative_path="Artist/Album (2000)/01 Song.flac",
            size=stat.st_size,
            mtime_ns=1,
            ctime_ns=stat.st_ctime_ns,
            dev=stat.st_dev,
            inode=stat.st_ino,
            blake3=None,
            sidecar_path=None,
            scanned_at="2026-05-05T00:00:00+00:00",
        )
        db.upsert_tag_facts(
            conn,
            path=str(track.resolve()),
            tag_hash="tags",
            artist="Artist",
            album="Album",
            album_artist="Artist",
            title="Song",
            date="2000",
            track_number=1,
            disc_number=None,
            genre=None,
        )
        conn.commit()

        def fail_read_tags(path: str):
            raise ValueError(f"unreadable tags: {path}")

        monkeypatch.setattr("musiktool.library.read_tags", fail_read_tags)

        result = audit_library(root, min_severity="warning", index_conn=conn)
    finally:
        conn.close()

    categories = {finding.category for finding in result.findings}
    assert "tags.missing_required" in categories
    assert result.summary["index"]["fact_sources"]["stale"] == 1


def test_audit_applies_builtin_musiktoolignore_and_cli_excludes(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    (root / ".musiktoolignore").parent.mkdir(parents=True)
    (root / ".musiktoolignore").write_text("ignored-from-file/\n")
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    _touch(root / ".Trash-1000" / "Trash Artist" / "Trash Album" / "01 Trash.flac")
    _touch(root / "musiktool" / "render" / "_calibration" / "tone.flac")
    _touch(root / "ignored-from-file" / "01 Skip.flac")
    _touch(root / "ignored-from-cli" / "01 Skip.flac")

    result = audit_library(root, excludes=["ignored-from-cli/"])

    assert result.summary["albums_scanned"] == 1
    assert result.summary["tracks_scanned"] == 1
    assert all(".Trash-1000" not in p for finding in result.findings for p in finding.paths)
    assert all("musiktool/render" not in p for finding in result.findings for p in finding.paths)
    assert all("ignored-from-file" not in p for finding in result.findings for p in finding.paths)
    assert all("ignored-from-cli" not in p for finding in result.findings for p in finding.paths)


def test_inspect_path_returns_track_metadata(tmp_path: Path) -> None:
    album = tmp_path / "lib" / "Artist" / "Album (2000)"
    _touch(album / "01 Song.flac")

    result = inspect_path(album)

    assert result.kind == "album"
    assert result.metadata["track_count"] == 1
    assert result.metadata["tracks"][0]["filename"] == "01 Song.flac"
    assert result.findings


def test_apply_plan_dry_run_and_execute_quarantine(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    source = root / "Artist" / "Album (2000)" / "Nested"
    _touch(source / "01 Song.m4a")

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [
            {
                "action_id": "quarantine:test",
                "type": "quarantine",
                "because_finding": "structure.nested_album:test",
                "source": str(source),
                "reason": "duplicate candidate",
            }
        ],
    }
    plan_text = json.dumps(plan)

    dry = apply_plan("-", plan_text=plan_text, dry_run=True)
    assert dry.actions[0].status == "would_apply"
    assert source.exists()

    applied = apply_plan("-", plan_text=plan_text, dry_run=False)
    destination = root / "_quarantine" / "Artist" / "Album (2000)" / "Nested"
    assert applied.executed is True
    assert not source.exists()
    assert (destination / "01 Song.m4a").exists()


def test_apply_plan_copy_file_from_source_root(tmp_path: Path) -> None:
    source_root = tmp_path / "itunes"
    target_root = tmp_path / "lib"
    source = _touch(source_root / "iTunes Music" / "Artist" / "Album" / "01 Song.m4a")
    source.write_text("audio")
    target_root.mkdir()
    destination = target_root / "music" / "Artist" / "Album (2000)" / "01 Song.m4a"

    plan = {
        "schema_version": 1,
        "library_root": str(target_root),
        "source_roots": [str(source_root)],
        "actions": [
            {
                "action_id": "copy_file:1",
                "type": "copy_file",
                "because_finding": "itunes.import_candidate",
                "source": str(source),
                "destination": str(destination),
            }
        ],
    }

    dry = apply_plan("-", plan_text=json.dumps(plan), dry_run=True)
    assert dry.actions[0].status == "would_apply"
    assert not destination.exists()

    applied = apply_plan("-", plan_text=json.dumps(plan), dry_run=False)
    assert applied.actions[0].status == "applied"
    assert source.exists()
    assert destination.read_text() == "audio"


def test_apply_plan_copy_file_rejects_unlisted_source_root(tmp_path: Path) -> None:
    source = _touch(tmp_path / "outside" / "01 Song.m4a")
    target_root = tmp_path / "lib"
    target_root.mkdir()
    plan = {
        "schema_version": 1,
        "library_root": str(target_root),
        "actions": [
            {
                "type": "copy_file",
                "because_finding": "itunes.import_candidate",
                "source": str(source),
                "destination": str(target_root / "music" / "Artist" / "Album" / "01 Song.m4a"),
            }
        ],
    }

    with pytest.raises(ValidationError, match="source must stay inside"):
        apply_plan("-", plan_text=json.dumps(plan), dry_run=True)


def test_apply_plan_rejects_destination_outside_library(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    source = _touch(root / "Artist" / "Album (2000)" / "01 Song.flac")
    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [
            {
                "action_id": "move:test",
                "type": "move_file",
                "because_finding": "tracks.filename_pattern:test",
                "source": str(source),
                "destination": str(tmp_path / "outside.flac"),
            }
        ],
    }

    with pytest.raises(ValidationError, match="must stay inside"):
        apply_plan("-", plan_text=json.dumps(plan))


def test_cli_audit_json(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    default_db = tmp_path / ".local" / "share" / "musiktool" / "analytics.db"
    default_db.mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "musiktool.cli",
            "audit",
            str(root),
            "--format",
            "json",
            "--severity",
            "warning",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["command"] == "audit"
    assert data["summary"]["findings"] >= 1


def test_cli_audit_explicit_bad_db_fails(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    bad_db = tmp_path / "bad.db"
    bad_db.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "musiktool.cli",
            "audit",
            str(root),
            "--db",
            str(bad_db),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "could not open index database" in result.stderr


# --- _consensus_year tests ---


def test_consensus_year_unanimous(tmp_path: Path, monkeypatch) -> None:
    album = tmp_path / "Artist" / "Album"
    _touch(album / "01 A.flac")
    _touch(album / "02 B.flac")
    _touch(album / "03 C.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=2020),
    )
    assert _consensus_year(album) == 2020


def test_consensus_year_disagreement(tmp_path: Path, monkeypatch) -> None:
    album = tmp_path / "Artist" / "Album"
    t1 = _touch(album / "01 A.flac")
    t2 = _touch(album / "02 B.flac")

    from musiktool.tags import Tags

    years = {str(t1.resolve()): 2020, str(t2.resolve()): 2021}
    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=years.get(path, None)),
    )
    assert _consensus_year(album) is None


def test_consensus_year_no_tags(tmp_path: Path, monkeypatch) -> None:
    album = tmp_path / "Artist" / "Album"
    _touch(album / "01 A.flac")
    _touch(album / "02 B.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(),
    )
    assert _consensus_year(album) is None


def test_consensus_year_partial(tmp_path: Path, monkeypatch) -> None:
    album = tmp_path / "Artist" / "Album"
    t1 = _touch(album / "01 A.flac")
    t2 = _touch(album / "02 B.flac")
    t3 = _touch(album / "03 C.flac")

    from musiktool.tags import Tags

    no_year = {str(t3.resolve())}
    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags() if path in no_year else Tags(year=2020),
    )
    assert _consensus_year(album) == 2020


# --- Enriched album_year_missing evidence tests ---


def test_audit_year_missing_includes_tag_year(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    _touch(album / "01 Song.flac")
    _touch(album / "02 Other.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(
            artist="Artist", album="Album", title="Song",
            year=1981, track_number=1,
        ),
    )
    result = audit_library(root, min_severity="warning")
    finding = next(
        f for f in result.findings
        if f.category == "structure.album_year_missing"
    )
    assert finding.evidence["tag_year"] == 1981
    assert finding.evidence["suggested_name"] == "Album (1981)"
    assert finding.suggested_actions[0]["destination"].endswith("Album (1981)")


def test_audit_year_missing_handles_media_kind_subtree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "lib"
    _touch(root / "music" / "Artist" / "Album" / "01 Song.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(
            artist="Artist", album="Album", title="Song",
            year=1981, track_number=1,
        ),
    )
    result = audit_library(root, min_severity="warning")
    finding = next(
        f for f in result.findings
        if f.category == "structure.album_year_missing"
    )
    assert finding.paths[0].endswith("music/Artist/Album")
    assert finding.evidence["suggested_name"] == "Album (1981)"


def test_audit_year_missing_no_consensus(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    t1 = _touch(album / "01 Song.flac")
    t2 = _touch(album / "02 Other.flac")

    from musiktool.tags import Tags

    years = {str(t1.resolve()): 2020, str(t2.resolve()): 2021}
    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(
            artist="Artist", album="Album", title="Song",
            year=years.get(path, None), track_number=1,
        ),
    )
    result = audit_library(root, min_severity="warning")
    finding = next(
        f for f in result.findings
        if f.category == "structure.album_year_missing"
    )
    assert finding.evidence["tag_year"] is None
    assert finding.evidence["suggested_name"] is None
    assert "destination" not in finding.suggested_actions[0]


# --- propose_year_folders tests ---


def test_propose_generates_renames(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    _touch(root / "Artist" / "Good Album (2020)" / "01 Song.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=1981),
    )
    result = propose_year_folders(root)
    assert len(result.actions) == 1
    assert result.actions[0]["type"] == "rename_album_dir"
    assert result.actions[0]["source"].endswith("Album")
    assert result.actions[0]["destination"].endswith("Album (1981)")
    assert len(result.skipped) == 0


def test_propose_year_folders_handles_media_kind_subtree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "lib"
    _touch(root / "music" / "Artist" / "Album" / "01 Song.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=1981),
    )
    result = propose_year_folders(root)
    assert len(result.actions) == 1
    assert result.actions[0]["source"].endswith("music/Artist/Album")
    assert result.actions[0]["destination"].endswith("music/Artist/Album (1981)")


def test_propose_year_folders_skips_radio_profile_with_index(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    track = _touch(root / "radio" / "Show" / "Season" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        _index_track(conn, root, track, artist="Show", title="Episode")
        db.upsert_library_classification(
            conn,
            subject_path=str((root / "radio" / "Show").resolve()),
            media_kind="radio",
            source="manual",
            confidence=1.0,
            confirmed_at="2026-05-05T00:00:00+00:00",
        )
        conn.commit()
        result = propose_year_folders(root, index_conn=conn)
    finally:
        conn.close()

    assert result.actions == []
    assert result.skipped == []


def test_propose_skips_no_consensus(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    t1 = _touch(root / "Artist" / "Album" / "01 Song.flac")
    t2 = _touch(root / "Artist" / "Album" / "02 Other.flac")

    from musiktool.tags import Tags

    years = {str(t1.resolve()): 2020, str(t2.resolve()): 2021}
    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=years.get(path, None)),
    )
    result = propose_year_folders(root)
    assert len(result.actions) == 0
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "no_consensus_year"


def test_propose_skips_destination_exists(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    _touch(root / "Artist" / "Album (1981)" / "01 Song.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=1981),
    )
    result = propose_year_folders(root)
    assert len(result.actions) == 0
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "destination_exists"


def test_propose_json_roundtrip(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=1981),
    )
    result = propose_year_folders(root)
    plan_json = format_propose(result, "json")
    plan = json.loads(plan_json)

    assert plan["schema_version"] == 1
    assert plan["library_root"] == str(root)
    assert len(plan["actions"]) == 1

    apply_result = apply_plan(
        "test",
        plan_text=json.dumps(plan),
        dry_run=True,
    )
    assert apply_result.valid
    assert len(apply_result.actions) == 1
    assert apply_result.actions[0].status == "would_apply"


# --- DB path relocation tests ---


def test_relocate_paths_updates_all_tables(tmp_path: Path) -> None:
    conn = db.get_connection(tmp_path / "analytics.db")
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    track = _touch(album / "01 Song.flac")

    _index_track(conn, root, track, artist="Artist", album="Album",
                 title="Song", track_number=1, duration_sec=200.0)
    conn.execute(
        "INSERT INTO track_loudness VALUES (?, -14, -1, 8, 200, ?, '2026', 1000)",
        (str(track), str(album)),
    )
    conn.execute(
        "INSERT INTO album_loudness VALUES (?, -14, -1, 8, 1, 200, '2026')",
        (str(album),),
    )
    conn.commit()

    counts = db.relocate_paths(conn, str(album), str(album) + " (2020)")
    assert counts.get("track_loudness.path", 0) == 1
    assert counts.get("track_loudness.album_path", 0) == 1
    assert counts.get("album_loudness.path", 0) == 1
    assert counts.get("indexed_files.path", 0) == 1
    assert counts.get("audio_facts.path", 0) == 1
    assert counts.get("tag_facts.path", 0) == 1

    row = conn.execute("SELECT path FROM track_loudness").fetchone()
    assert "(2020)" in row[0]

    conn.close()


def test_relocate_paths_updates_tape_items(tmp_path: Path) -> None:
    conn = db.get_connection(tmp_path / "analytics.db")
    conn.execute(
        "INSERT INTO tape_project (name, medium, duration_sec, created_at) "
        "VALUES ('Mix', 'vhs-120', 7080, '2026')",
    )
    project_id = conn.execute(
        "SELECT id FROM tape_project WHERE name = 'Mix'"
    ).fetchone()[0]
    db.add_tape_item(conn, project_id=project_id, position=1,
                     item_type="track", path="/lib/Artist/Album/01.flac")
    conn.commit()

    counts = db.relocate_paths(conn, "/lib/Artist/Album", "/lib/Artist/Album (2020)")
    assert counts["tape_item.path"] == 1

    row = conn.execute("SELECT path FROM tape_item WHERE position = 1").fetchone()
    assert row[0] == "/lib/Artist/Album (2020)/01.flac"
    conn.close()


def test_find_tape_references(tmp_path: Path) -> None:
    conn = db.get_connection(tmp_path / "analytics.db")
    conn.execute(
        "INSERT INTO tape_project (name, medium, duration_sec, created_at) "
        "VALUES ('Rock', 'vhs-120', 7080, '2026')",
    )
    project_id = conn.execute(
        "SELECT id FROM tape_project WHERE name = 'Rock'"
    ).fetchone()[0]
    db.add_tape_item(conn, project_id=project_id, position=1,
                     item_type="track", path="/lib/Artist/Album/01.flac")
    db.add_tape_item(conn, project_id=project_id, position=2,
                     item_type="track", path="/lib/Other/Song/01.flac")
    conn.commit()

    refs = db.find_tape_references(conn, "/lib/Artist/Album")
    assert len(refs) == 1
    assert refs[0] == ("Rock", "/lib/Artist/Album/01.flac")

    refs_none = db.find_tape_references(conn, "/lib/Missing")
    assert len(refs_none) == 0
    conn.close()


# --- Apply with DB integration tests ---


def test_apply_relocates_paths_on_execute(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    _touch(album / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    conn.execute(
        "INSERT INTO tape_project (name, medium, duration_sec, created_at) "
        "VALUES ('Mix', 'vhs-120', 7080, '2026')",
    )
    project_id = conn.execute(
        "SELECT id FROM tape_project WHERE name = 'Mix'"
    ).fetchone()[0]
    db.add_tape_item(conn, project_id=project_id, position=1,
                     item_type="album", path=str(album))
    conn.commit()

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [{
            "action_id": "rename:1",
            "type": "rename_album_dir",
            "manual": True,
            "source": str(album),
            "destination": str(album) + " (2020)",
            "reason": "test",
        }],
    }
    result = apply_plan("test", plan_text=json.dumps(plan),
                        dry_run=False, db_conn=conn)
    assert result.executed
    assert result.actions[0].db_updates is not None
    assert result.actions[0].db_updates.get("tape_item.path") == 1

    row = conn.execute("SELECT path FROM tape_item").fetchone()
    assert "(2020)" in row[0]
    conn.close()


def test_apply_warns_about_tape_references_on_dry_run(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    _touch(album / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    conn.execute(
        "INSERT INTO tape_project (name, medium, duration_sec, created_at) "
        "VALUES ('Mix', 'vhs-120', 7080, '2026')",
    )
    project_id = conn.execute(
        "SELECT id FROM tape_project WHERE name = 'Mix'"
    ).fetchone()[0]
    db.add_tape_item(conn, project_id=project_id, position=1,
                     item_type="album", path=str(album))
    conn.commit()

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [{
            "action_id": "rename:1",
            "type": "rename_album_dir",
            "manual": True,
            "source": str(album),
            "destination": str(album) + " (2020)",
            "reason": "test",
        }],
    }
    result = apply_plan("test", plan_text=json.dumps(plan),
                        dry_run=True, db_conn=conn)
    assert result.actions[0].warnings
    assert any("Mix" in w for w in result.actions[0].warnings)
    assert result.actions[0].db_updates is None
    conn.close()


def test_apply_warns_hidden_destination(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "_Album"
    _touch(album / "01 Song.flac")

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [{
            "action_id": "rename:1",
            "type": "rename_album_dir",
            "manual": True,
            "source": str(album),
            "destination": str(root / "Artist" / ".Album (2020)"),
            "reason": "test",
        }],
    }
    result = apply_plan("test", plan_text=json.dumps(plan), dry_run=True)
    assert result.actions[0].warnings
    assert any("hidden" in w.lower() for w in result.actions[0].warnings)


def test_apply_execute_validates_all_actions_before_moving(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    _touch(album / "01 Song.flac")

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [
            {
                "action_id": "rename:1",
                "type": "rename_album_dir",
                "manual": True,
                "source": str(album),
                "destination": str(album) + " (2020)",
                "reason": "test",
            },
            {
                "action_id": "rename:2",
                "type": "rename_album_dir",
                "manual": True,
                "source": str(root / "Artist" / "Missing"),
                "destination": str(root / "Artist" / "Missing (2021)"),
                "reason": "test",
            },
        ],
    }

    with pytest.raises(ValidationError, match="source does not exist"):
        apply_plan("test", plan_text=json.dumps(plan), dry_run=False)

    assert album.exists()
    assert not (album.parent / "Album (2020)").exists()


def test_apply_rejects_overlapping_move_sources(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    parent = root / "Artist" / "Album"
    nested = parent / "Nested"
    _touch(parent / "01 Song.flac")
    _touch(nested / "01 Song.flac")

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [
            {
                "action_id": "rename:parent",
                "type": "rename_album_dir",
                "manual": True,
                "source": str(parent),
                "destination": str(root / "music" / "Artist" / "Album"),
                "reason": "test",
            },
            {
                "action_id": "rename:nested",
                "type": "rename_album_dir",
                "manual": True,
                "source": str(nested),
                "destination": str(root / "music" / "Artist" / "Album" / "Nested"),
                "reason": "test",
            },
        ],
    }

    with pytest.raises(ValidationError, match="overlapping sources"):
        apply_plan("test", plan_text=json.dumps(plan), dry_run=False)

    assert parent.exists()
    assert nested.exists()


# --- apply --skip ---


def test_apply_skip_excludes_action(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album1 = root / "Artist" / "Album1"
    album2 = root / "Artist" / "Album2"
    _touch(album1 / "01 Song.flac")
    _touch(album2 / "01 Song.flac")

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [
            {
                "action_id": "rename:1",
                "type": "rename_album_dir",
                "manual": True,
                "source": str(album1),
                "destination": str(album1) + " (2020)",
                "reason": "test",
            },
            {
                "action_id": "rename:2",
                "type": "rename_album_dir",
                "manual": True,
                "source": str(album2),
                "destination": str(album2) + " (2021)",
                "reason": "test",
            },
        ],
    }
    result = apply_plan(
        "test",
        plan_text=json.dumps(plan),
        dry_run=False,
        skip_ids={"rename:2"},
    )
    assert result.actions[0].status == "applied"
    assert result.actions[1].status == "skipped"
    assert not album1.exists()
    assert (album1.parent / "Album1 (2020)").exists()
    assert album2.exists()  # Not moved


def test_apply_skip_in_dry_run(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    _touch(album / "01 Song.flac")

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [{
            "action_id": "rename:1",
            "type": "rename_album_dir",
            "manual": True,
            "source": str(album),
            "destination": str(album) + " (2020)",
            "reason": "test",
        }],
    }
    result = apply_plan(
        "test",
        plan_text=json.dumps(plan),
        dry_run=True,
        skip_ids={"rename:1"},
    )
    assert result.actions[0].status == "skipped"


def test_apply_skip_unknown_id_ignored(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    _touch(album / "01 Song.flac")

    plan = {
        "schema_version": 1,
        "library_root": str(root),
        "actions": [{
            "action_id": "rename:1",
            "type": "rename_album_dir",
            "manual": True,
            "source": str(album),
            "destination": str(album) + " (2020)",
            "reason": "test",
        }],
    }
    result = apply_plan(
        "test",
        plan_text=json.dumps(plan),
        dry_run=False,
        skip_ids={"nonexistent:99"},
    )
    assert result.actions[0].status == "applied"
    assert not album.exists()


# --- Propose with tape references tests ---


def test_propose_shows_tape_references(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album"
    _touch(album / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    conn.execute(
        "INSERT INTO tape_project (name, medium, duration_sec, created_at) "
        "VALUES ('Rock', 'vhs-120', 7080, '2026')",
    )
    project_id = conn.execute(
        "SELECT id FROM tape_project WHERE name = 'Rock'"
    ).fetchone()[0]
    db.add_tape_item(conn, project_id=project_id, position=1,
                     item_type="track", path=str(album / "01 Song.flac"))
    conn.commit()

    from musiktool.tags import Tags
    monkeypatch.setattr("musiktool.library.read_tags", lambda path: Tags(year=2020))

    result = propose_year_folders(root, index_conn=conn)
    assert len(result.actions) == 1
    assert "tape_references" in result.actions[0]
    assert result.actions[0]["tape_references"][0]["project"] == "Rock"
    conn.close()


# --- Chromaprint similarity-based duplicate detection ---

# Pre-encoded fingerprints with known BER:
# FP_A and FP_B: BER = 0.03125 (similar, same audio different codec)
# FP_A and FP_C: BER = 0.49 (completely different audio)
_FP_A = "AQAABEmiREmiKJKSKEiiUUmWLQsUZlEiLYwCJYmkREqSRFoA"
_FP_B = "AQAABEqUKEkURVISBUk0KsmyZYHCLEqkhVGgJJGUSEmSSAsA"
_FP_C = "AQAABCFJkkiWZVmWZUFIkiSSKFGiRIkSJUoA"


def test_audit_chromaprint_similarity_detects_similar(tmp_path: Path) -> None:
    """Albums with similar (not identical) chromaprints are flagged as duplicates."""
    root = tmp_path / "lib"
    left = _touch(root / "Artist" / "Album FLAC" / "01 Song.flac")
    right = _touch(root / "Artist" / "Album M4A" / "01 Song.m4a")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        _index_track(
            conn, root, left,
            artist="Artist", album="Album", title="Song",
            track_number=1, chromaprint=_FP_A,
        )
        _index_track(
            conn, root, right,
            artist="Artist", album="Album", title="Song",
            track_number=1, chromaprint=_FP_B,
        )
        conn.commit()

        result = audit_library(
            root, min_severity="warning", index_conn=conn,
            similarity_threshold=0.08,
        )
    finally:
        conn.close()

    duplicate = next(
        (f for f in result.findings
         if f.category == "duplicates.same_album_candidate"
         and f.evidence.get("evidence_type") == "audio_fingerprint"),
        None,
    )
    assert duplicate is not None
    assert duplicate.evidence["similarity_ber"] == pytest.approx(0.03125, abs=0.001)
    assert duplicate.evidence["similarity_threshold"] == 0.08
    assert duplicate.confidence == 0.93  # BER 0.01-0.05 range


def test_audit_chromaprint_similarity_respects_threshold(tmp_path: Path) -> None:
    """Albums with BER above threshold are not flagged."""
    root = tmp_path / "lib"
    left = _touch(root / "Artist" / "Album FLAC" / "01 Song.flac")
    right = _touch(root / "Artist" / "Album M4A" / "01 Song.m4a")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        _index_track(
            conn, root, left,
            artist="Artist", album="Album", title="Song",
            track_number=1, chromaprint=_FP_A,
        )
        _index_track(
            conn, root, right,
            artist="Artist", album="Album", title="Song",
            track_number=1, chromaprint=_FP_B,
        )
        conn.commit()

        # Use very strict threshold (below the actual BER)
        result = audit_library(
            root, min_severity="warning", index_conn=conn,
            similarity_threshold=0.01,
        )
    finally:
        conn.close()

    fingerprint_findings = [
        f for f in result.findings
        if f.category == "duplicates.same_album_candidate"
        and f.evidence.get("evidence_type") == "audio_fingerprint"
    ]
    assert fingerprint_findings == []


def test_audit_chromaprint_different_audio_not_flagged(tmp_path: Path) -> None:
    """Albums with very different chromaprints are not duplicate-flagged."""
    root = tmp_path / "lib"
    left = _touch(root / "Artist" / "Album One" / "01 Song.flac")
    right = _touch(root / "Artist" / "Album Two" / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        _index_track(
            conn, root, left,
            artist="Artist", album="One", title="Song",
            track_number=1, chromaprint=_FP_A,
        )
        _index_track(
            conn, root, right,
            artist="Artist", album="Two", title="Different",
            track_number=1, chromaprint=_FP_C,
        )
        conn.commit()

        result = audit_library(
            root, min_severity="warning", index_conn=conn,
            similarity_threshold=0.08,
        )
    finally:
        conn.close()

    fingerprint_findings = [
        f for f in result.findings
        if f.category == "duplicates.same_album_candidate"
        and f.evidence.get("evidence_type") == "audio_fingerprint"
    ]
    assert fingerprint_findings == []


def test_audit_chromaprint_different_track_count_not_compared(tmp_path: Path) -> None:
    """Albums with different track counts skip chromaprint comparison."""
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album A" / "01 Song.flac")
    track_b1 = _touch(root / "Artist" / "Album B" / "01 Song.flac")
    track_b2 = _touch(root / "Artist" / "Album B" / "02 Another.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        _index_track(
            conn, root, root / "Artist" / "Album A" / "01 Song.flac",
            artist="Artist", album="Album", title="Song",
            track_number=1, chromaprint=_FP_A,
        )
        _index_track(
            conn, root, track_b1,
            artist="Artist", album="Album", title="Song",
            track_number=1, chromaprint=_FP_A,
        )
        _index_track(
            conn, root, track_b2,
            artist="Artist", album="Album", title="Another",
            track_number=2, chromaprint=_FP_B,
        )
        conn.commit()

        result = audit_library(
            root, min_severity="warning", index_conn=conn,
            similarity_threshold=0.08,
        )
    finally:
        conn.close()

    fingerprint_findings = [
        f for f in result.findings
        if f.category == "duplicates.same_album_candidate"
        and f.evidence.get("evidence_type") == "audio_fingerprint"
    ]
    assert fingerprint_findings == []


# --- Grouped text report ---


def test_format_audit_text_groups_by_album(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2000)" / "01 Song.flac")
    _touch(root / "Zebra" / "Disc" / "01 Track.flac")

    rendered = format_audit(audit_library(root), "text")

    assert "Artist/Album (2000)" in rendered
    assert "Zebra/Disc" in rendered
    # Severity icons present
    assert "\u26a0" in rendered or "\u2139" in rendered
    # Album headers appear before their findings
    album_pos = rendered.index("Zebra/Disc")
    assert "album_year_missing" in rendered[album_pos:]


def test_format_audit_text_aggregates_tag_findings(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2000)" / "01 One.flac")
    _touch(root / "Artist" / "Album (2000)" / "02 Two.flac")
    _touch(root / "Artist" / "Album (2000)" / "03 Three.flac")

    rendered = format_audit(audit_library(root, min_severity="warning"), "text")

    assert "3 tracks missing:" in rendered
    # Individual per-track lines should NOT appear
    assert "01 One.flac" not in rendered
    assert "02 Two.flac" not in rendered


def test_format_audit_text_cross_album_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "A" / "Album (2000)" / "01 Song.flac")
    _touch(root / "B" / "Album (2000)" / "01 Song.flac")

    result = audit_library(root, min_severity="info")
    dup_findings = [f for f in result.findings if f.category.startswith("duplicates.")]

    if dup_findings:
        rendered = format_audit(result, "text")
        # Cross-album finding should mention the other album
        assert "A/Album (2000)" in rendered
        assert "B/Album (2000)" in rendered


def test_format_audit_text_library_level(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2000)" / "01 Song.flac")
    _touch(root / "stray.bin")

    rendered = format_audit(audit_library(root, min_severity="info"), "text")

    assert "(library root)" in rendered
    assert "stray_file" in rendered
    assert "stray.bin" in rendered


def test_format_audit_text_no_findings(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir(parents=True)

    rendered = format_audit(audit_library(root), "text")

    assert "No findings." in rendered


def test_format_audit_text_suggested_name(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    result = audit_library(root, min_severity="warning")
    year_findings = [f for f in result.findings if f.category == "structure.album_year_missing"]
    assert year_findings

    rendered = format_audit(result, "text")

    assert "album_year_missing" in rendered
    # When there's no tag_year, should show "no consensus year"
    assert "no consensus year" in rendered


def test_propose_text_shows_track_count_and_format(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    _touch(root / "Artist" / "Album" / "02 Other.flac")

    from musiktool.tags import Tags

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(year=1999),
    )
    result = propose_year_folders(root)

    assert len(result.actions) == 1
    assert result.actions[0]["track_count"] == 2
    assert result.actions[0]["formats"] == ["flac"]

    rendered = format_propose(result, "text")
    assert "(2 tracks, flac)" in rendered


# ---------------------------------------------------------------------------
# propose media-kind-folders
# ---------------------------------------------------------------------------


def test_propose_media_kind_moves_classified_albums(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2020)" / "01 Song.flac")
    _touch(root / "Show" / "Season 1" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Artist").resolve()),
        media_kind="music",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Show").resolve()),
        media_kind="radio",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 2
    by_dest = {a["destination"]: a for a in result.actions}
    music_dest = str((root / "music" / "Artist" / "Album (2020)").resolve())
    radio_dest = str((root / "radio" / "Show" / "Season 1").resolve())
    assert music_dest in by_dest
    assert radio_dest in by_dest
    assert by_dest[music_dest]["type"] == "rename_album_dir"
    assert by_dest[radio_dest]["type"] == "rename_album_dir"
    assert len(result.skipped) == 0


def test_propose_media_kind_skips_low_confidence(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Artist").resolve()),
        media_kind="music",
        source="default",
        confidence=0.5,
        confirmed_at=None,
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 0
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "low_confidence"


def test_propose_media_kind_skips_already_correct(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "music" / "Artist" / "Album (2020)" / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "music" / "Artist").resolve()),
        media_kind="music",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 0
    assert len(result.skipped) == 0


def test_propose_media_kind_uses_indexed_track_classification_to_move(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    track = _touch(root / "Show" / "Season 1" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    _index_track(conn, root, track, artist="Show", title="Episode")
    db.upsert_library_classification(
        conn,
        subject_path=str(track.resolve()),
        media_kind="radio",
        source="path",
        confidence=0.95,
        confirmed_at=None,
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 1
    assert result.actions[0]["destination"].endswith("radio/Show/Season 1")
    assert len(result.skipped) == 0


def test_propose_media_kind_uses_track_classification_when_already_correct(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    track = _touch(root / "radio" / "Show" / "Season 1" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    _index_track(conn, root, track, artist="Show", title="Episode")
    db.upsert_library_classification(
        conn,
        subject_path=str(track.resolve()),
        media_kind="radio",
        source="path",
        confidence=0.95,
        confirmed_at=None,
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 0
    assert len(result.skipped) == 0


def test_propose_media_kind_skips_wrong_subtree(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "music" / "Show" / "Season 1" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "music" / "Show").resolve()),
        media_kind="radio",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 0
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "wrong_subtree"


def test_propose_media_kind_skips_destination_exists(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    _touch(root / "music" / "Artist" / "Album" / "01 Other.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Artist").resolve()),
        media_kind="music",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 0
    reasons = {s["reason"] for s in result.skipped}
    assert "destination_exists" in reasons


def test_propose_media_kind_json_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "Season 1" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Show").resolve()),
        media_kind="radio",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    plan_json = format_propose(result, "json")
    plan = json.loads(plan_json)

    assert plan["schema_version"] == 1
    assert plan["library_root"] == str(root.resolve())
    assert len(plan["actions"]) == 1

    apply_result = apply_plan(
        "test",
        plan_text=json.dumps(plan),
        dry_run=True,
    )
    assert apply_result.valid
    assert len(apply_result.actions) == 1
    assert apply_result.actions[0].status == "would_apply"


def test_propose_media_kind_text_says_move(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "Season 1" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Show").resolve()),
        media_kind="radio",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    rendered = format_propose(result, "text")
    assert "move(s)" in rendered
    assert "rename(s)" not in rendered


def test_propose_media_kind_includes_tape_refs(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album (2020)"
    _touch(album / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Artist").resolve()),
        media_kind="music",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.execute(
        "INSERT INTO tape_project (name, medium, duration_sec, created_at) "
        "VALUES ('Mix', 'vhs-120', 7080, '2026')",
    )
    project_id = conn.execute(
        "SELECT id FROM tape_project WHERE name = 'Mix'"
    ).fetchone()[0]
    db.add_tape_item(conn, project_id=project_id, position=1,
                     item_type="track",
                     path=str((album / "01 Song.flac").resolve()))
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 1
    assert "tape_references" in result.actions[0]
    assert result.actions[0]["tape_references"][0]["project"] == "Mix"


def test_propose_media_kind_skips_unclassified(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    # No classifications at all
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 0
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "unclassified"


def test_propose_media_kind_manual_always_accepted(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Artist").resolve()),
        media_kind="music",
        source="manual",
        confidence=0.5,  # Low confidence but manual
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 1
    assert result.actions[0]["destination"].endswith("music/Artist/Album")


def test_propose_media_kind_inherits_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "Season 1" / "01 Episode.mp3")
    _touch(root / "Show" / "Season 2" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    db.upsert_library_classification(
        conn,
        subject_path=str((root / "Show").resolve()),
        media_kind="radio",
        source="manual",
        confidence=1.0,
        confirmed_at="2026-05-05T00:00:00+00:00",
    )
    conn.commit()

    result = propose_media_kind_folders(root, conn=conn)
    conn.close()

    assert len(result.actions) == 2
    dests = sorted(a["destination"] for a in result.actions)
    assert dests[0].endswith("radio/Show/Season 1")
    assert dests[1].endswith("radio/Show/Season 2")


# ---------------------------------------------------------------------------
# Profile-aware audit
# ---------------------------------------------------------------------------


def test_audit_music_profile_matches_default(tmp_path: Path) -> None:
    """Explicit profile='music' gives same findings as default."""
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")
    default_result = audit_library(root)
    music_result = audit_library(root, profile="music")
    default_cats = {f.category for f in default_result.findings}
    music_cats = {f.category for f in music_result.findings}
    assert default_cats == music_cats
    assert music_result.summary["profiles_used"] == {"music": 1}


def test_audit_radio_profile_skips_year_structure(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Philip Maloney" / "Season 1" / "01 Episode.mp3")
    result = audit_library(root, profile="radio")
    categories = {f.category for f in result.findings}
    assert "structure.album_year_missing" not in categories


def test_audit_radio_profile_skips_provenance(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "Season (2020)" / "01 Episode.mp3")
    result = audit_library(root, profile="radio")
    categories = {f.category for f in result.findings}
    assert "provenance.cue_missing" not in categories
    assert "provenance.eac_log_missing" not in categories


def test_audit_radio_profile_required_tags(tmp_path: Path, monkeypatch) -> None:
    """Radio only requires artist+title; missing album/tracknumber/date not flagged."""
    from musiktool.tags import Tags

    root = tmp_path / "lib"
    _touch(root / "Show" / "Season (2020)" / "01 Episode.mp3")

    # has artist+title but no album, no tracknumber, no date
    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(artist="Show", title="Episode"),
    )
    result = audit_library(root, profile="radio", min_severity="warning")
    missing_findings = [f for f in result.findings if f.category == "tags.missing_required"]
    assert missing_findings == []


def test_audit_radio_profile_flags_missing_title(tmp_path: Path, monkeypatch) -> None:
    """Radio profile still flags missing title."""
    from musiktool.tags import Tags

    root = tmp_path / "lib"
    _touch(root / "Show" / "Season (2020)" / "01 Episode.mp3")

    monkeypatch.setattr(
        "musiktool.library.read_tags",
        lambda path: Tags(artist="Show"),
    )
    result = audit_library(root, profile="radio", min_severity="warning")
    missing_findings = [f for f in result.findings if f.category == "tags.missing_required"]
    assert len(missing_findings) == 1
    assert missing_findings[0].evidence["missing"] == ["title"]


def test_audit_radio_profile_filename_pattern(tmp_path: Path) -> None:
    """Radio accepts 2- and 3-digit track numbers."""
    root = tmp_path / "lib"
    _touch(root / "Show" / "Season (2020)" / "01 Episode.mp3")
    _touch(root / "Show" / "Season (2020)" / "123 Long Episode.mp3")
    result = audit_library(root, profile="radio")
    pattern_findings = [f for f in result.findings if f.category == "tracks.filename_pattern"]
    assert pattern_findings == []


def test_audit_audiobook_profile_keeps_year(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Author" / "Book" / "01 Chapter.mp3")
    result = audit_library(root, profile="audiobook")
    categories = {f.category for f in result.findings}
    assert "structure.album_year_missing" in categories


def test_audit_audiobook_profile_skips_provenance(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Author" / "Book (2020)" / "01 Chapter.mp3")
    result = audit_library(root, profile="audiobook")
    categories = {f.category for f in result.findings}
    assert "provenance.cue_missing" not in categories
    assert "provenance.eac_log_missing" not in categories


def test_audit_podcast_profile_filename_pattern(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "2025" / "2025-01-15 Episode Title.mp3")
    result = audit_library(root, profile="podcast")
    pattern_findings = [f for f in result.findings if f.category == "tracks.filename_pattern"]
    assert pattern_findings == []


def test_audit_podcast_profile_rejects_numeric_prefix(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "2025" / "01 Episode.mp3")
    result = audit_library(root, profile="podcast")
    pattern_findings = [f for f in result.findings if f.category == "tracks.filename_pattern"]
    assert len(pattern_findings) == 1


def test_audit_auto_profile_resolves_from_classification(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "Season (2020)" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        db.upsert_library_classification(
            conn,
            subject_path=str((root / "Show").resolve()),
            media_kind="radio",
            source="manual",
            confidence=1.0,
            confirmed_at="2026-05-05T00:00:00+00:00",
        )
        conn.commit()
        track = root / "Show" / "Season (2020)" / "01 Episode.mp3"
        _index_track(conn, root, track, artist="Show", title="Episode")
        conn.commit()
        result = audit_library(root, profile="auto", index_conn=conn)
    finally:
        conn.close()

    categories = {f.category for f in result.findings}
    # Radio profile: no year structure, no provenance
    assert "structure.album_year_missing" not in categories
    assert "provenance.cue_missing" not in categories
    assert result.summary["profiles_used"] == {"radio": 1}


def test_audit_auto_profile_falls_back_to_music(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album" / "01 Song.flac")

    # No index at all → fall back to music
    result = audit_library(root, profile="auto")
    categories = {f.category for f in result.findings}
    assert "structure.album_year_missing" in categories
    assert result.summary["profiles_used"] == {"music": 1}


def test_audit_auto_profile_mixed_library(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2020)" / "01 Song.flac")
    _touch(root / "Show" / "Season (2020)" / "01 Episode.mp3")

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        # Classify Show as radio
        db.upsert_library_classification(
            conn,
            subject_path=str((root / "Show").resolve()),
            media_kind="radio",
            source="manual",
            confidence=1.0,
            confirmed_at="2026-05-05T00:00:00+00:00",
        )
        conn.commit()
        # Index both tracks
        music_track = root / "Artist" / "Album (2020)" / "01 Song.flac"
        radio_track = root / "Show" / "Season (2020)" / "01 Episode.mp3"
        _index_track(conn, root, music_track, artist="Artist", album="Album", title="Song", track_number=1)
        _index_track(conn, root, radio_track, artist="Show", title="Episode")
        conn.commit()
        result = audit_library(root, profile="auto", index_conn=conn)
    finally:
        conn.close()

    assert result.summary["profiles_used"] == {"music": 1, "radio": 1}
    # Music album gets provenance checks (info level)
    provenance = [f for f in result.findings if f.category.startswith("provenance.")]
    provenance_paths = {f.paths[0] for f in provenance}
    # Only music album should have provenance findings
    assert all("Artist" in p for p in provenance_paths)


def test_audit_profile_in_summary(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2020)" / "01 Song.flac")
    result = audit_library(root, profile="music")
    assert "profiles_used" in result.summary
    assert result.summary["profiles_used"] == {"music": 1}


def test_audit_text_shows_profile_tag(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Show" / "Season (2020)" / "01 Episode.mp3")
    result = audit_library(root, profile="radio")
    rendered = format_audit(result, "text")
    assert "[radio]" in rendered


# ---------------------------------------------------------------------------
# Stats tests
# ---------------------------------------------------------------------------

from musiktool.library import compute_stats, format_stats


def test_stats_filesystem_only(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")
    _touch(root / "Artist" / "Album (2020)" / "02 Track.flac")
    _touch(root / "Artist" / "Other (2019)" / "01 Song.mp3")

    result = compute_stats(root)
    assert result.albums == 2
    assert result.tracks == 3
    assert result.total_files == 3
    assert result.audio_files == 3
    assert result.non_audio_files == 0
    assert result.total_bytes == 0  # touched files have 0 bytes
    assert result.audio_bytes == 0
    assert result.non_audio_bytes == 0
    assert len(result.format_histogram) == 2
    exts = {e["ext"] for e in result.format_histogram}
    assert ".flac" in exts
    assert ".mp3" in exts
    assert result.asset_histogram == []
    assert result.provenance == {
        "albums": 2,
        "albums_with_cue": 0,
        "albums_with_eac_log": 0,
        "cue_files": 0,
        "eac_logs": 0,
    }
    # No DB enrichment
    assert result.tag_coverage is None
    assert result.codec_histogram is None
    assert result.classifications is None
    assert result.index_coverage is None
    assert result.loudness_coverage is None


def test_stats_with_index(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    t1 = _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")
    t2 = _touch(root / "Artist" / "Album (2020)" / "02 Track.flac")

    conn = db.get_connection(tmp_path / "test.db")
    try:
        _index_track(conn, root, t1, artist="Artist", album="Album", title="Track")
        _index_track(conn, root, t2, artist="Artist", album="Album", title=None)
        conn.commit()

        result = compute_stats(root, index_conn=conn)
        assert result.albums == 1
        assert result.tracks == 2
        assert result.tag_coverage is not None
        assert result.tag_coverage["title"]["have"] == 1
        assert result.tag_coverage["title"]["total"] == 2
        assert result.tag_coverage["artist"]["have"] == 2
        assert result.index_coverage == {
            "indexed": 2,
            "stale": 0,
            "missing": 0,
            "total": 2,
        }
    finally:
        conn.close()


def test_stats_json_output(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")

    result = compute_stats(root)
    output = format_stats(result, "json")
    data = json.loads(output)
    assert data["command"] == "stats"
    assert data["albums"] == 1
    assert data["tracks"] == 1
    assert data["schema_version"] == 1
    assert "format_histogram" in data


def test_stats_ndjson_output(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")

    result = compute_stats(root)
    output = format_stats(result, "ndjson")
    lines = output.strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["command"] == "stats"


def test_stats_empty_directory(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()

    result = compute_stats(root)
    assert result.albums == 0
    assert result.tracks == 0
    assert result.total_bytes == 0
    assert result.format_histogram == []


def test_stats_respects_ignore_policy(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")
    _touch(root / "_quarantine" / "Bad" / "01 Track.flac")
    _touch(root / "_quarantine" / "Bad" / "cover.jpg")

    result = compute_stats(root)
    assert result.albums == 1
    assert result.tracks == 1
    assert result.total_files == 1


def test_stats_classifications(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    t1 = _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")

    conn = db.get_connection(tmp_path / "test.db")
    try:
        _index_track(conn, root, t1, artist="Artist", album="Album", title="Track")
        db.upsert_library_classification(
            conn,
            subject_path=str((root / "Artist" / "Album (2020)").resolve()),
            media_kind="music",
            source="manual",
            confidence=1.0,
            confirmed_at="2026-05-05T00:00:00+00:00",
        )
        conn.commit()

        result = compute_stats(root, index_conn=conn)
        assert result.classifications == {"music": 1}
    finally:
        conn.close()


def test_stats_counts_album_side_files_and_provenance(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Album (2020)"
    _touch(album / "01 Track.flac")
    _touch(album / "album.cue")
    _touch(album / "eac.log")
    _touch(album / "cover.jpg")
    _touch(album / "booklet.pdf")
    _touch(album / "album.m3u8")
    _touch(album / "notes.txt")

    result = compute_stats(root)
    assert result.tracks == 1
    assert result.total_files == 7
    assert result.audio_files == 1
    assert result.non_audio_files == 6
    assert result.provenance == {
        "albums": 1,
        "albums_with_cue": 1,
        "albums_with_eac_log": 1,
        "cue_files": 1,
        "eac_logs": 1,
    }
    assets = {entry["kind"]: entry["count"] for entry in result.asset_histogram}
    assert assets == {
        "booklet": 1,
        "cue_sheet": 1,
        "image": 1,
        "playlist": 1,
        "rip_log": 1,
        "text": 1,
    }


def test_stats_index_coverage_uses_current_active_files_only(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    current = _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")
    stale = _touch(root / "Artist" / "Album (2020)" / "02 Track.flac")
    quarantined = _touch(root / "_quarantine" / "Bad" / "01 Track.flac")

    conn = db.get_connection(tmp_path / "test.db")
    try:
        _index_track(conn, root, current, artist="A", album="B", title="T1")
        _index_track(conn, root, stale, artist="A", album="B", title="T2")
        _index_track(conn, root, quarantined, artist="A", album="B", title="Q")
        conn.execute(
            "UPDATE indexed_files SET mtime_ns = ? WHERE path = ?",
            (1, str(stale.resolve())),
        )
        conn.commit()

        result = compute_stats(root, index_conn=conn)
        assert result.tracks == 2
        assert result.index_coverage == {
            "indexed": 1,
            "stale": 1,
            "missing": 0,
            "total": 2,
        }
    finally:
        conn.close()


def test_stats_classifications_use_effective_album_profile(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    track = _touch(root / "radio" / "Show" / "Season" / "001 Episode.mp3")

    conn = db.get_connection(tmp_path / "test.db")
    try:
        _index_track(conn, root, track, artist="Show", title="Episode")
        db.upsert_library_classification(
            conn,
            subject_path=str(track.resolve()),
            media_kind="radio",
            source="path",
            confidence=0.95,
            confirmed_at=None,
        )
        conn.commit()

        result = compute_stats(root, index_conn=conn)
        assert result.classifications == {"radio": 1}
        assert result.provenance == {
            "albums": 0,
            "albums_with_cue": 0,
            "albums_with_eac_log": 0,
            "cue_files": 0,
            "eac_logs": 0,
        }
    finally:
        conn.close()


def test_prune_removes_missing_files(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    t1 = _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")
    t2 = _touch(root / "Artist" / "Album (2020)" / "02 Track.flac")

    conn = db.get_connection(tmp_path / "test.db")
    try:
        _index_track(conn, root, t1, artist="A", album="B", title="T1")
        _index_track(conn, root, t2, artist="A", album="B", title="T2")
        conn.commit()

        # Verify both indexed
        assert conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0] == 2

        # Remove one file from disk
        t2.unlink()

        # Prune
        root_str = str(root.resolve()) + "/"
        counts = db.prune_missing(conn, root_str)
        assert counts["indexed_files"] == 1
        assert counts["audio_facts"] == 1
        assert counts["tag_facts"] == 1

        # Only t1 remains
        assert conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0] == 1
    finally:
        conn.close()


def test_prune_nothing_to_do(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    t1 = _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")

    conn = db.get_connection(tmp_path / "test.db")
    try:
        _index_track(conn, root, t1, artist="A", album="B", title="T1")
        conn.commit()

        root_str = str(root.resolve()) + "/"
        counts = db.prune_missing(conn, root_str)
        assert counts == {}
    finally:
        conn.close()


def test_stats_loudness_coverage(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    t1 = _touch(root / "Artist" / "Album (2020)" / "01 Track.flac")

    conn = db.get_connection(tmp_path / "test.db")
    try:
        db.upsert_track(
            conn,
            path=str(t1.resolve()),
            integrated_lufs=-14.0,
            true_peak_dbtp=-1.0,
            lra=8.0,
            duration_sec=240.0,
            album_path=str((root / "Artist" / "Album (2020)").resolve()),
            analyzed_at="2026-05-05T00:00:00+00:00",
            file_mtime=0.0,
        )
        db.upsert_album(
            conn,
            path=str((root / "Artist" / "Album (2020)").resolve()),
            integrated_lufs=-14.0,
            true_peak_dbtp=-1.0,
            lra=8.0,
            track_count=1,
            duration_sec=240.0,
            analyzed_at="2026-05-05T00:00:00+00:00",
        )
        conn.commit()

        result = compute_stats(root, index_conn=conn)
        assert result.loudness_coverage == {"tracks": 1, "albums": 1}
    finally:
        conn.close()
