"""Tests for agent-facing library audit and fix plans."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from musiktool.exceptions import ValidationError
from musiktool.library import (
    apply_plan,
    audit_library,
    format_audit,
    inspect_path,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_audit_reports_structure_tags_and_duplicate_evidence(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "analytics.db").write_text("")

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
    assert duplicate.fixable is True
    assert duplicate.suggested_actions[0]["type"] == "quarantine"


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

    env = os.environ.copy()
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
