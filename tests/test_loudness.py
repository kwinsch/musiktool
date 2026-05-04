"""Tests for loudness parsing."""

import pytest

from musiktool.loudness import _parse_summary_value


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
