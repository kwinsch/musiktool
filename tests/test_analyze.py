"""Tests for bulk analysis DB behavior."""

from pathlib import Path

from musiktool import db
from musiktool.index import IndexScanSummary
from musiktool.analyze import analyze_album, discover_albums
from musiktool.loudness import LoudnessInfo
from musiktool.tape import validate_loudness


def test_analyze_album_stores_absolute_paths(
    tmp_path: Path, monkeypatch,
) -> None:
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    track = album / "01 Song.flac"
    track.touch()

    def fake_measure_track(path: str) -> LoudnessInfo:
        assert Path(path).is_absolute()
        return LoudnessInfo(
            integrated_lufs=-14.0,
            true_peak_dbtp=-1.0,
            lra=6.0,
            duration_sec=120.0,
            path=path,
        )

    def fake_measure_album(
        directory: str, tracks: list[LoudnessInfo] | None = None,
    ) -> tuple[list[LoudnessInfo], LoudnessInfo]:
        assert Path(directory).is_absolute()
        assert tracks is not None
        return tracks, LoudnessInfo(
            integrated_lufs=-14.0,
            true_peak_dbtp=-1.0,
            lra=6.0,
            duration_sec=sum(t.duration_sec for t in tracks),
            path=directory,
        )

    monkeypatch.setattr("musiktool.analyze.measure_track", fake_measure_track)
    monkeypatch.setattr("musiktool.analyze.measure_album", fake_measure_album)
    monkeypatch.chdir(tmp_path)

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        result = analyze_album(Path("Artist/Album"), conn, force=True)

        album_abs = str(album.resolve())
        track_abs = str(track.resolve())
        assert result.path == album_abs

        album_row = conn.execute(
            "SELECT * FROM album_loudness WHERE path = ?", (album_abs,),
        ).fetchone()
        track_row = conn.execute(
            "SELECT * FROM track_loudness WHERE path = ?", (track_abs,),
        ).fetchone()

        assert album_row is not None
        assert track_row is not None
        assert track_row["album_path"] == album_abs

        assert validate_loudness(conn, album_abs, "album") == album_row
        assert validate_loudness(conn, track_abs, "track") == track_row
    finally:
        conn.close()


def test_analyze_album_refreshes_index_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    track = album / "01 Song.flac"
    track.touch()
    sidecars = tmp_path / "sidecars"
    scan_calls = []

    def fake_scan_path(path: Path, conn, **kwargs) -> IndexScanSummary:
        scan_calls.append((path, kwargs))
        return IndexScanSummary(
            root=str(path.resolve()),
            files_total=1,
            files_indexed=1,
            files_from_sidecar=0,
            files_skipped=0,
        )

    def fake_measure_track(path: str) -> LoudnessInfo:
        return LoudnessInfo(
            integrated_lufs=-14.0,
            true_peak_dbtp=-1.0,
            lra=6.0,
            duration_sec=120.0,
            path=path,
        )

    def fake_measure_album(
        directory: str, tracks: list[LoudnessInfo] | None = None,
    ) -> tuple[list[LoudnessInfo], LoudnessInfo]:
        assert tracks is not None
        return tracks, LoudnessInfo(
            integrated_lufs=-14.0,
            true_peak_dbtp=-1.0,
            lra=6.0,
            duration_sec=sum(t.duration_sec for t in tracks),
            path=directory,
        )

    monkeypatch.setattr("musiktool.analyze.scan_path", fake_scan_path)
    monkeypatch.setattr("musiktool.analyze.measure_track", fake_measure_track)
    monkeypatch.setattr("musiktool.analyze.measure_album", fake_measure_album)

    conn = db.get_connection(tmp_path / "analytics.db")
    try:
        result = analyze_album(
            album,
            conn,
            use_index=True,
            sidecar_root=sidecars,
        )
    finally:
        conn.close()

    assert scan_calls[0][0] == album.resolve()
    kwargs = scan_calls[0][1]
    assert kwargs["sidecar_root"] == sidecars
    assert kwargs["force"] is False
    assert kwargs["hash_files"] is False
    assert kwargs["fingerprint_files"] is False
    assert kwargs["excludes"] is None
    assert kwargs["ignore_policy"] is not None
    assert result.index_files_total == 1
    assert result.index_files_indexed == 1


def test_discover_albums_applies_ignore_policy(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    (root / ".musiktoolignore").parent.mkdir(parents=True)
    (root / ".musiktoolignore").write_text("ignored-from-file/\n")
    (root / "Artist" / "Album").mkdir(parents=True)
    (root / "Artist" / "Album" / "01 Song.flac").touch()
    (root / ".Trash-1000" / "Artist" / "Album").mkdir(parents=True)
    (root / ".Trash-1000" / "Artist" / "Album" / "01 Trash.flac").touch()
    (root / "ignored-from-file").mkdir(parents=True)
    (root / "ignored-from-file" / "01 Skip.flac").touch()
    (root / "ignored-from-cli").mkdir(parents=True)
    (root / "ignored-from-cli" / "01 Skip.flac").touch()

    albums = discover_albums(root, excludes=["ignored-from-cli/"])

    assert albums == [(root / "Artist" / "Album").resolve()]
