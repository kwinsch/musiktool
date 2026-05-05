"""Tests for musiktool sidecar read/write behavior."""

import json
from pathlib import Path

import pytest

from musiktool.sidecar import (
    SIDECAR_SCHEMA_VERSION,
    SidecarIntegrityError,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,
)


def _document() -> dict:
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "path": {"root": "/music", "relative": "Artist/Album/01 Song.flac"},
        "file": {"size": 10, "mtime_ns": 20, "blake3": None},
        "audio": {"duration_sec": 123.4, "chromaprint": None},
        "tags": {"tag_hash": "abc", "title": "Song"},
        "library": {"media_kind": "music", "source": "default", "confidence": 0.5},
    }


def test_sidecar_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "track.flac.mtk"

    write_sidecar(_document(), out)
    loaded = read_sidecar(out)

    assert loaded["schema_version"] == SIDECAR_SCHEMA_VERSION
    assert loaded["path"]["relative"] == "Artist/Album/01 Song.flac"
    assert loaded["tags"]["title"] == "Song"


def test_sidecar_detects_tamper(tmp_path: Path) -> None:
    out = tmp_path / "track.flac.mtk"
    write_sidecar(_document(), out)

    wrapper = json.loads(out.read_text())
    wrapper["payload"]["tags"]["title"] = "Tampered"
    out.write_text(json.dumps(wrapper))

    with pytest.raises(SidecarIntegrityError):
        read_sidecar(out)


def test_sidecar_path_mirrors_source_tree(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    source = root / "Artist" / "Album" / "01 Song.flac"
    sidecars = tmp_path / "sidecars"
    source.parent.mkdir(parents=True)
    source.touch()

    result = sidecar_path_for(source, source_root=root, sidecar_root=sidecars)

    assert result.name == "01 Song.flac.mtk"
    assert result.parent.name == "Album"
    assert sidecars in result.parents
