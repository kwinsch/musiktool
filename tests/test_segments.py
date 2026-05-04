"""Tests for segment generation — ffmpeg command construction."""

import pytest

from musiktool.segments import (
    FileInput,
    Silence,
    Tone,
    _build_ffmpeg_cmd,
    _lavfi_expr,
)
from pathlib import Path


# --- _lavfi_expr ---


class TestLavfiExpr:

    def test_silence(self):
        expr = _lavfi_expr(Silence(duration=5.0), sample_rate=44100, channels=2)
        assert expr == "aevalsrc=0:s=44100:d=5.0:c=stereo"

    def test_silence_mono(self):
        expr = _lavfi_expr(Silence(duration=2.0), sample_rate=44100, channels=1)
        assert expr == "aevalsrc=0:s=44100:d=2.0:c=mono"

    def test_tone_full_scale(self):
        expr = _lavfi_expr(
            Tone(frequency=1000, level_dbfs=0, duration=10),
            sample_rate=48000, channels=2,
        )
        assert "sin(2*PI*1000*t)*1" in expr
        assert "s=48000" in expr
        assert "d=10" in expr
        assert "c=stereo" in expr

    def test_tone_attenuated(self):
        expr = _lavfi_expr(
            Tone(frequency=400, level_dbfs=-20, duration=1),
            sample_rate=44100, channels=2,
        )
        # -20 dBFS → amplitude = 10^(-20/20) = 0.1
        assert "sin(2*PI*400*t)*0.1" in expr

    def test_file_input_raises(self):
        with pytest.raises(TypeError, match="unknown segment type"):
            _lavfi_expr(FileInput(path="/tmp/test.flac"), sample_rate=44100, channels=2)


# --- Segment validation ---


class TestSegmentValidation:

    def test_silence_positive_duration(self):
        with pytest.raises(AssertionError, match="positive"):
            Silence(duration=0)

    def test_silence_negative_duration(self):
        with pytest.raises(AssertionError, match="positive"):
            Silence(duration=-1)

    def test_tone_positive_frequency(self):
        with pytest.raises(AssertionError, match="positive"):
            Tone(frequency=0, level_dbfs=-10, duration=1)

    def test_tone_level_above_zero(self):
        with pytest.raises(AssertionError, match="<= 0"):
            Tone(frequency=440, level_dbfs=1, duration=1)

    def test_tone_level_zero_ok(self):
        t = Tone(frequency=440, level_dbfs=0, duration=1)
        assert t.level_dbfs == 0

    def test_tone_positive_duration(self):
        with pytest.raises(AssertionError, match="positive"):
            Tone(frequency=440, level_dbfs=-10, duration=0)


# --- _build_ffmpeg_cmd ---


class TestBuildFfmpegCmd:

    def test_silence_only(self):
        cmd = _build_ffmpeg_cmd(
            [Silence(duration=5)],
            Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "-f" in cmd
        assert "lavfi" in cmd
        assert "-filter_complex" in cmd
        assert "-c:a" in cmd
        assert "flac" in cmd[cmd.index("-c:a") + 1]
        assert "-ar" in cmd
        assert "44100" in cmd
        assert str(Path("/tmp/out.flac")) == cmd[-1]

    def test_file_input_no_gain(self):
        cmd = _build_ffmpeg_cmd(
            [FileInput(path="/music/track.flac", gain_db=0.0)],
            Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        # File input uses -i, not lavfi
        assert "/music/track.flac" in cmd
        # No volume filter when gain is 0
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "volume" not in fc

    def test_file_input_with_gain(self):
        cmd = _build_ffmpeg_cmd(
            [FileInput(path="/music/track.flac", gain_db=3.5)],
            Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "volume=3.50dB" in fc

    def test_mixed_segments(self):
        segs = [
            Silence(duration=5),
            FileInput(path="/music/a.flac", gain_db=2.0),
            Silence(duration=3),
            FileInput(path="/music/b.flac", gain_db=-1.5),
        ]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        # Should have volume filters for both file inputs
        assert "volume=2.00dB" in fc
        assert "volume=-1.50dB" in fc
        # Concat with n=4
        assert "concat=n=4:v=0:a=1[out]" in fc

    def test_wav_output_16bit(self):
        cmd = _build_ffmpeg_cmd(
            [Silence(duration=1)],
            Path("/tmp/out.wav"),
            sample_rate=48000, bit_depth=16, channels=2,
        )
        assert "pcm_s16le" in cmd
        assert "48000" in cmd

    def test_wav_output_24bit(self):
        cmd = _build_ffmpeg_cmd(
            [Silence(duration=1)],
            Path("/tmp/out.wav"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        assert "pcm_s24le" in cmd

    def test_flac_16bit(self):
        cmd = _build_ffmpeg_cmd(
            [Silence(duration=1)],
            Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=16, channels=2,
        )
        idx = cmd.index("-sample_fmt")
        assert cmd[idx + 1] == "s16"

    def test_flac_24bit(self):
        cmd = _build_ffmpeg_cmd(
            [Silence(duration=1)],
            Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        idx = cmd.index("-sample_fmt")
        assert cmd[idx + 1] == "s32"

    def test_unsupported_format_raises(self):
        with pytest.raises(AssertionError, match="unsupported"):
            _build_ffmpeg_cmd(
                [Silence(duration=1)],
                Path("/tmp/out.mp3"),
                sample_rate=44100, bit_depth=24, channels=2,
            )

    def test_empty_segments_raises(self):
        with pytest.raises(AssertionError, match="empty"):
            _build_ffmpeg_cmd(
                [], Path("/tmp/out.flac"),
                sample_rate=44100, bit_depth=24, channels=2,
            )

    def test_concat_label_ordering(self):
        """Verify concat inputs are in correct order."""
        segs = [
            Silence(duration=2),
            FileInput(path="/a.flac", gain_db=1.0),
            Silence(duration=1),
        ]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        # Input 1 gets volume filter → label [g1]
        # Concat should be: [0:a][g1][2:a]concat=n=3
        assert "[0:a]" in fc
        assert "[g1]" in fc
        assert "[2:a]" in fc
        assert "concat=n=3:v=0:a=1[out]" in fc


# --- Filter chain with limiter/compressor ---


class TestBuildFfmpegCmdProcessing:

    _COMP_PARAMS = {
        "ratio": 2.0, "threshold": 0.1,
        "attack": 20.0, "release": 250.0, "knee": 2.83,
    }

    def test_file_input_with_limiter(self):
        segs = [FileInput(path="/a.flac", gain_db=6.0, limiter_limit=1.0)]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "volume=6.00dB" in fc
        assert "alimiter=limit=1.000000" in fc

    def test_file_input_with_compressor(self):
        segs = [FileInput(
            path="/a.flac", gain_db=0.0, compressor_params=self._COMP_PARAMS,
        )]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "acompressor=" in fc
        assert "ratio=2.0" in fc
        assert "volume" not in fc  # no volume filter when gain is 0

    def test_all_processing_correct_order(self):
        segs = [FileInput(
            path="/a.flac", gain_db=3.0,
            limiter_limit=0.891, compressor_params=self._COMP_PARAMS,
        )]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        # Order must be: volume → acompressor → alimiter
        vol_pos = fc.index("volume=")
        comp_pos = fc.index("acompressor=")
        lim_pos = fc.index("alimiter=")
        assert vol_pos < comp_pos < lim_pos

    def test_no_processing_no_filter(self):
        segs = [FileInput(path="/a.flac")]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "volume" not in fc
        assert "alimiter" not in fc
        assert "acompressor" not in fc

    def test_limiter_without_gain(self):
        segs = [FileInput(path="/a.flac", gain_db=0.0, limiter_limit=1.0)]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "alimiter=" in fc
        assert "volume" not in fc

    def test_mixed_some_with_processing(self):
        segs = [
            Silence(duration=2),
            FileInput(path="/a.flac", gain_db=3.0, limiter_limit=1.0),
            FileInput(path="/b.flac", gain_db=-1.0),
            Silence(duration=1),
        ]
        cmd = _build_ffmpeg_cmd(
            segs, Path("/tmp/out.flac"),
            sample_rate=44100, bit_depth=24, channels=2,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        # Input 1 has gain + limiter
        assert "alimiter=" in fc
        # Input 2 has gain only, no limiter
        assert "volume=-1.00dB" in fc
        assert "concat=n=4:v=0:a=1[out]" in fc
