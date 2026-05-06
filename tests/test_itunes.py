"""Tests for iTunes XML import planning."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path

from musiktool.itunes import (
    build_import_plan,
    filter_albums,
    format_albums,
    format_plan,
    format_scan,
    load_catalog,
)
from musiktool.library import apply_plan


def _touch(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _write_itunes_xml(root: Path) -> Path:
    _touch(root / "iTunes Music" / "AC_DC" / "High Voltage" / "01 Song.m4a", "audio")
    _touch(root / "iTunes Music" / "AC_DC" / "High Voltage" / "03 Protected.m4p", "protected")
    _touch(root / "iTunes Music" / "Podcasts" / "Episode.mp4", "video")
    document = {
        "Tracks": {
            "1": {
                "Track ID": 1,
                "Name": "Song",
                "Artist": "AC/DC",
                "Album": "High Voltage",
                "Genre": "Rock",
                "Kind": "AAC audio file",
                "Track Number": 1,
                "Year": 1976,
                "Play Count": 4,
                "Location": (
                    "file://localhost/Volumes/My%20Passport/iTunes/"
                    "iTunes%20Music/AC_DC/High%20Voltage/01%20Song.m4a"
                ),
            },
            "2": {
                "Track ID": 2,
                "Name": "Episode",
                "Kind": "MPEG-4 video file",
                "Podcast": True,
                "Location": (
                    "file://localhost/Volumes/My%20Passport/iTunes/"
                    "iTunes%20Music/Podcasts/Episode.mp4"
                ),
            },
            "3": {
                "Track ID": 3,
                "Name": "Missing",
                "Artist": "AC/DC",
                "Album": "High Voltage",
                "Kind": "AAC audio file",
                "Track Number": 2,
                "Year": 1976,
                "Location": (
                    "file://localhost/Volumes/My%20Passport/iTunes/"
                    "iTunes%20Music/AC_DC/High%20Voltage/02%20Missing.m4a"
                ),
            },
            "4": {
                "Track ID": 4,
                "Name": "Protected",
                "Artist": "AC/DC",
                "Album": "High Voltage",
                "Kind": "Protected AAC audio file",
                "Track Number": 3,
                "Year": 1976,
                "Location": (
                    "file://localhost/Volumes/My%20Passport/iTunes/"
                    "iTunes%20Music/AC_DC/High%20Voltage/03%20Protected.m4p"
                ),
            },
        }
    }
    xml_path = root / "iTunes Music Library.xml"
    root.mkdir(parents=True, exist_ok=True)
    with xml_path.open("wb") as handle:
        plistlib.dump(document, handle)
    return xml_path


def test_load_catalog_remaps_legacy_locations(tmp_path: Path) -> None:
    root = tmp_path / "iTunes"
    _write_itunes_xml(root)

    catalog = load_catalog(root)
    summary = catalog.summary()

    assert summary["tracks"] == 4
    assert summary["tracks_existing"] == 3
    assert summary["tracks_missing"] == 1
    assert summary["media_kinds"] == {"music": 3, "podcast": 1}
    assert summary["music_albums"] == 1
    assert "Existing files" in format_scan(catalog)


def test_filter_albums_and_json_output(tmp_path: Path) -> None:
    root = tmp_path / "iTunes"
    _write_itunes_xml(root)
    catalog = load_catalog(root)

    albums = filter_albums(catalog, artist="ac/dc")

    assert len(albums) == 1
    assert albums[0].artist == "AC/DC"
    assert albums[0].album == "High Voltage"
    payload = json.loads(format_albums(albums, output_format="json"))
    assert payload["summary"]["albums"] == 1


def test_build_import_plan_copies_existing_selected_tracks(tmp_path: Path) -> None:
    root = tmp_path / "iTunes"
    _write_itunes_xml(root)
    target = tmp_path / "lib"
    target.mkdir()
    catalog = load_catalog(root)

    plan = build_import_plan(catalog, target=target, artist="AC/DC")

    assert len(plan["actions"]) == 1
    assert len(plan["skipped"]) == 2
    assert {entry["reason"] for entry in plan["skipped"]} == {
        "protected_file",
        "source_missing",
    }
    assert plan["actions"][0]["type"] == "copy_file"
    assert plan["actions"][0]["destination"].endswith(
        "music/AC-DC/High Voltage (1976)/01 Song.m4a"
    )
    assert "iTunes import proposal" in format_plan(plan)

    with_protected = build_import_plan(
        catalog,
        target=target,
        artist="AC/DC",
        include_protected=True,
    )
    assert len(with_protected["actions"]) == 2

    result = apply_plan("-", plan_text=json.dumps(plan), dry_run=False)
    assert result.actions[0].status == "applied"
    destination = target / "music" / "AC-DC" / "High Voltage (1976)" / "01 Song.m4a"
    assert destination.read_text() == "audio"
