"""Tests for bulk analysis DB behavior."""

from pathlib import Path

from musiktool import db
from musiktool.analyze import analyze_album
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
