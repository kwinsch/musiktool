"""Tests for loudness parsing."""

import subprocess
from pathlib import Path

import pytest

from musiktool.exceptions import AudioReadError
from musiktool.loudness import LoudnessInfo, _parse_summary_value, measure_album


SAMPLE_EBUR128_OUTPUT = """\
[Parsed_ebur128_0 @ 0x5555555] t: 0.0    M: -120.7 S: -120.7     I: -120.7 LUFS
[Parsed_ebur128_0 @ 0x5555555] t: 1.0    M:  -14.2 S:  -14.5     I:  -14.3 LUFS
[Parsed_ebur128_0 @ 0x5555555] t: 2.0    M:  -13.8 S:  -14.1     I:  -14.1 LUFS
[Parsed_ebur128_0 @ 0x5555555]
[Parsed_ebur128_0 @ 0x5555555] Summary:

  Integrated loudness:
    I:         -14.1 LUFS
    Threshold: -24.1 LUFS

  Loudness range:
    LRA:        7.2 LU
    Threshold: -34.1 LUFS
    LRA low:   -18.5 LUFS
    LRA high:  -11.3 LUFS

  True peak:
    Peak:       -0.3 dBFS
"""


class TestParseSummaryValue:

    def test_parse_integrated(self):
        val = _parse_summary_value(SAMPLE_EBUR128_OUTPUT, "I:")
        assert val == pytest.approx(-14.1)

    def test_parse_peak(self):
        val = _parse_summary_value(SAMPLE_EBUR128_OUTPUT, "Peak:")
        assert val == pytest.approx(-0.3)

    def test_parse_lra(self):
        val = _parse_summary_value(SAMPLE_EBUR128_OUTPUT, "LRA:")
        assert val == pytest.approx(7.2)

    def test_ignores_pre_summary_values(self):
        # The per-frame lines also contain "I:" but should be ignored
        val = _parse_summary_value(SAMPLE_EBUR128_OUTPUT, "I:")
        # Should be -14.1 (summary), not -120.7 or -14.3 (per-frame)
        assert val == pytest.approx(-14.1)

    def test_lra_returns_first_match_not_low_high(self):
        # Summary has LRA, LRA low, LRA high — must return LRA only
        val = _parse_summary_value(SAMPLE_EBUR128_OUTPUT, "LRA:")
        assert val == pytest.approx(7.2)

    def test_missing_key_raises(self):
        with pytest.raises(ValueError, match="failed to parse"):
            _parse_summary_value(SAMPLE_EBUR128_OUTPUT, "Nonexistent:")

    def test_empty_output_raises(self):
        with pytest.raises(ValueError, match="failed to parse"):
            _parse_summary_value("", "I:")

    def test_summary_without_key_raises(self):
        output = "Some output\nSummary:\n  Threshold: -24.1 LUFS\n"
        with pytest.raises(ValueError, match="failed to parse"):
            _parse_summary_value(output, "I:")


class TestMeasureAlbum:

    def test_concat_uses_absolute_paths(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        album = tmp_path / "album"
        album.mkdir()
        track = album / "01 It's.flac"
        track.touch()
        captured = {}

        def fake_measure_track(path: str) -> LoudnessInfo:
            assert Path(path).is_absolute()
            return LoudnessInfo(
                integrated_lufs=-14.0,
                true_peak_dbtp=-1.0,
                lra=6.0,
                duration_sec=120.0,
                path=path,
            )

        def fake_run(cmd, capture_output, text):
            concat_path = Path(cmd[cmd.index("-i") + 1])
            captured["concat"] = concat_path.read_text()
            return subprocess.CompletedProcess(
                cmd, 0, stderr=SAMPLE_EBUR128_OUTPUT,
            )

        monkeypatch.setattr(
            "musiktool.loudness.measure_track", fake_measure_track,
        )
        monkeypatch.setattr("musiktool.loudness.subprocess.run", fake_run)
        monkeypatch.chdir(tmp_path)

        tracks, album_info = measure_album("album")

        escaped = str(track.resolve()).replace("'", "'\\''")
        assert captured["concat"] == f"file '{escaped}'\n"
        assert tracks[0].path == str(track.resolve())
        assert album_info is not None
        assert album_info.path == str(album.resolve())

    def test_parse_failure_raises_audio_read_error(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        track = tmp_path / "01 Track.flac"
        tracks = [
            LoudnessInfo(
                integrated_lufs=-14.0,
                true_peak_dbtp=-1.0,
                lra=6.0,
                duration_sec=120.0,
                path=str(track),
            )
        ]

        def fake_run(cmd, capture_output, text):
            return subprocess.CompletedProcess(cmd, 0, stderr="no summary")

        monkeypatch.setattr("musiktool.loudness.subprocess.run", fake_run)

        with pytest.raises(AudioReadError, match="failed to parse album loudness"):
            measure_album(str(tmp_path), tracks=tracks)
