"""Tests for tape project logic — pure functions only."""

import pytest

from musiktool.loudness import (
    compute_gain as _compute_gain,
    compute_gain_with_limiter as _compute_gain_with_limiter,
)
from musiktool.tape import (
    AnalysisResult,
    SideItem,
    _compute_compressor_params,
    _capacity_status,
    _format_cue_time,
    _format_deck_time,
    _resolve_sample_rate,
    assign_sides,
    compute_gap,
    find_best_split,
    format_analysis,
    format_duration,
)


# --- _compute_gain ---


class TestComputeGain:
    """Core gain algorithm: gain_db, peak_after, needs_limiter, penalty."""

    def test_quiet_album_gets_positive_gain(self):
        # -20 LUFS album, target -14, peak at -6 dBTP → room for +6 dB
        gain, peak, limiter, penalty = _compute_gain(-20.0, -6.0, -14.0)
        assert gain == pytest.approx(6.0)
        assert peak == pytest.approx(0.0)
        assert limiter is False
        assert penalty == pytest.approx(0.0)

    def test_loud_album_gets_negative_gain(self):
        # -8 LUFS album, target -14 → needs -6 dB
        gain, peak, limiter, penalty = _compute_gain(-8.0, -1.0, -14.0)
        assert gain == pytest.approx(-6.0)
        assert peak == pytest.approx(-7.0)
        assert limiter is False
        assert penalty == pytest.approx(0.0)

    def test_already_at_target(self):
        gain, peak, limiter, penalty = _compute_gain(-14.0, -3.0, -14.0)
        assert gain == pytest.approx(0.0)
        assert peak == pytest.approx(-3.0)
        assert limiter is False
        assert penalty == pytest.approx(0.0)

    def test_peak_limited_gain(self):
        # -20 LUFS, target -14 → wants +6 dB
        # Peak at -2 dBTP, ceiling 0 → max safe gain is +2 dB
        # Budget 3: gain = min(6, 2+3) = 5, penalty = 1
        gain, peak, limiter, penalty = _compute_gain(-20.0, -2.0, -14.0, 0.0)
        assert gain == pytest.approx(5.0)
        assert peak == pytest.approx(3.0)
        assert limiter is True
        assert penalty == pytest.approx(1.0)

    def test_peak_at_zero_budget_limited(self):
        # Peak at 0 dBTP, ceiling 0 → max safe = 0. Budget 3: gain = min(6, 3) = 3
        gain, peak, limiter, penalty = _compute_gain(-20.0, 0.0, -14.0, 0.0)
        assert gain == pytest.approx(3.0)
        assert peak == pytest.approx(3.0)
        assert limiter is True
        assert penalty == pytest.approx(3.0)

    def test_negative_peak_ceiling(self):
        # Ceiling at -1 dBTP (headroom for DAC intersample peaks)
        gain, peak, limiter, penalty = _compute_gain(-14.0, -3.0, -14.0, -1.0)
        # Desired gain = 0, max safe = -1 - (-3) = +2 → no limiting needed
        assert gain == pytest.approx(0.0)
        assert limiter is False

    def test_negative_ceiling_triggers_limiter(self):
        # -20 LUFS, target -14, peak -2, ceiling -1
        # Desired = +6, max safe = -1 - (-2) = +1. Budget 3: gain = min(6, 1+3) = 4
        gain, peak, limiter, penalty = _compute_gain(-20.0, -2.0, -14.0, -1.0)
        assert gain == pytest.approx(4.0)
        assert peak == pytest.approx(2.0)
        assert limiter is True
        assert penalty == pytest.approx(2.0)

    def test_very_quiet_classical(self):
        # Classical album at -25 LUFS, peak -8, target -14
        # Desired = +11, max safe = 0 - (-8) = +8. Budget 3: gain = min(11, 8+3) = 11
        # Budget is sufficient — album reaches target!
        gain, peak, limiter, penalty = _compute_gain(-25.0, -8.0, -14.0, 0.0)
        assert gain == pytest.approx(11.0)
        assert peak == pytest.approx(3.0)
        assert limiter is True
        assert penalty == pytest.approx(0.0)

    def test_cd_rock_to_hard_clip_medium_transparent(self):
        # Loud CD rock (-8 LUFS, peak +0.5 dBTP) → VHS/MD ceiling -1 dBTP
        # desired = -6, max_safe = -1 - 0.5 = -1.5
        # -6 ≤ -1.5 → gain = -6 (attenuation), no limiter
        gain, peak, limiter, penalty = _compute_gain(-8.0, 0.5, -14.0, -1.0)
        assert gain == pytest.approx(-6.0)
        assert peak == pytest.approx(-5.5)
        assert limiter is False
        assert penalty == pytest.approx(0.0)

    def test_cd_classical_to_hard_clip_needs_limiter(self):
        # Quiet classical (-22 LUFS, peak -1 dBTP) → ceiling -1 dBTP
        # desired = +8, max_safe = -1 - (-1) = 0. Budget 3: gain = min(8, 0+3) = 3
        gain, peak, limiter, penalty = _compute_gain(-22.0, -1.0, -14.0, -1.0)
        assert gain == pytest.approx(3.0)
        assert peak == pytest.approx(2.0)
        assert limiter is True
        assert penalty == pytest.approx(5.0)


# --- format_duration ---


class TestFormatDuration:

    def test_zero(self):
        assert format_duration(0) == "0:00"

    def test_seconds_only(self):
        assert format_duration(45) == "0:45"

    def test_minutes_seconds(self):
        assert format_duration(185) == "3:05"

    def test_exact_hour(self):
        assert format_duration(3600) == "1:00:00"

    def test_hour_format(self):
        assert format_duration(3661) == "1:01:01"

    def test_rounding(self):
        assert format_duration(59.5) == "1:00"
        assert format_duration(59.4) == "0:59"


# --- _format_cue_time ---


class TestFormatCueTime:

    def test_zero(self):
        assert _format_cue_time(0.0) == "00:00:00"

    def test_one_second(self):
        # 1 second = 75 frames
        assert _format_cue_time(1.0) == "00:01:00"

    def test_fractional_frames(self):
        # 0.5 seconds = 37.5 frames → rounds to 38
        assert _format_cue_time(0.5) == "00:00:38"

    def test_one_frame(self):
        # 1/75th of a second
        assert _format_cue_time(1.0 / 75) == "00:00:01"

    def test_over_one_minute(self):
        assert _format_cue_time(61.0) == "01:01:00"

    def test_large_timestamp(self):
        # 90 minutes
        result = _format_cue_time(90 * 60)
        assert result == "90:00:00"

    def test_mixed(self):
        # 2 min 30 sec + 25 frames worth
        secs = 2 * 60 + 30 + 25 / 75
        assert _format_cue_time(secs) == "02:30:25"


# --- _format_deck_time ---


class TestFormatDeckTime:

    def test_zero(self):
        assert _format_deck_time(0) == "0:00:00"

    def test_seconds(self):
        assert _format_deck_time(45) == "0:00:45"

    def test_minutes(self):
        assert _format_deck_time(185) == "0:03:05"

    def test_hours(self):
        assert _format_deck_time(7261) == "2:01:01"


# --- compute_gap ---


class TestComputeGap:

    def _make_project(self, album_gap=8.0, track_gap=4.0):
        """Build a dict that quacks like a sqlite3.Row for gap fields."""
        return {"album_gap_sec": album_gap, "track_gap_sec": track_gap}

    def test_first_item_no_gap(self):
        assert compute_gap(None, "album", self._make_project()) == 0.0
        assert compute_gap(None, "track", self._make_project()) == 0.0

    def test_album_after_album(self):
        assert compute_gap("album", "album", self._make_project()) == 8.0

    def test_track_after_album(self):
        assert compute_gap("album", "track", self._make_project()) == 8.0

    def test_album_after_track(self):
        assert compute_gap("track", "album", self._make_project()) == 8.0

    def test_track_after_track(self):
        assert compute_gap("track", "track", self._make_project()) == 4.0

    def test_custom_gaps(self):
        p = self._make_project(album_gap=12.0, track_gap=2.0)
        assert compute_gap("album", "album", p) == 12.0
        assert compute_gap("track", "track", p) == 2.0


# --- _resolve_sample_rate ---


class TestResolveSampleRate:

    def test_explicit_rate(self):
        assert _resolve_sample_rate({44100: 10}, "48000") == 48000

    def test_auto_single_rate(self):
        assert _resolve_sample_rate({44100: 15}, "auto") == 44100

    def test_auto_majority_vote(self):
        assert _resolve_sample_rate({44100: 5, 48000: 10}, "auto") == 48000

    def test_auto_tie_prefers_44100(self):
        assert _resolve_sample_rate({44100: 10, 48000: 10}, "auto") == 44100

    def test_auto_tie_without_44100_prefers_lower(self):
        assert _resolve_sample_rate({48000: 5, 96000: 5}, "auto") == 48000

    def test_auto_empty_defaults_44100(self):
        assert _resolve_sample_rate({}, "auto") == 44100


# --- _compute_gain_with_limiter ---


class TestComputeGainWithLimiter:

    def test_limiter_off_same_as_compute_gain(self):
        result = _compute_gain_with_limiter(-20.0, -2.0, -14.0, 0.0, use_limiter=False)
        expected = _compute_gain(-20.0, -2.0, -14.0, 0.0)
        assert result == expected

    def test_limiter_on_no_limiting_needed(self):
        # Peak has headroom — limiter flag doesn't change anything
        result = _compute_gain_with_limiter(-20.0, -6.0, -14.0, 0.0, use_limiter=True)
        expected = _compute_gain(-20.0, -6.0, -14.0, 0.0)
        assert result == expected

    def test_limiter_on_uses_full_gain(self):
        # -20 LUFS, peak -2, target -14, ceiling 0
        # Without limiter: gain +2, penalty 4
        # With limiter: gain +6 (full desired), penalty 0
        gain, peak, limiter, penalty = _compute_gain_with_limiter(
            -20.0, -2.0, -14.0, 0.0, use_limiter=True,
        )
        assert gain == pytest.approx(6.0)
        assert peak == pytest.approx(4.0)  # -2 + 6 = 4 (limiter catches this)
        assert limiter is True
        assert penalty == pytest.approx(0.0)

    def test_limiter_on_peak_exceeds_ceiling(self):
        # With limiter enabled, peak_after_gain may exceed ceiling
        # The alimiter filter handles it in the render pipeline
        gain, peak, limiter, penalty = _compute_gain_with_limiter(
            -25.0, -8.0, -14.0, 0.0, use_limiter=True,
        )
        assert gain == pytest.approx(11.0)  # full desired gain
        assert peak == pytest.approx(3.0)  # -8 + 11 = 3 (above 0 ceiling)
        assert limiter is True
        assert penalty == pytest.approx(0.0)

    def test_limiter_off_uses_budget(self):
        # Without --limiter: budget=3. desired=6, max_safe=2, gain=min(6,5)=5
        gain, peak, limiter, penalty = _compute_gain_with_limiter(
            -20.0, -2.0, -14.0, 0.0, use_limiter=False,
        )
        assert gain == pytest.approx(5.0)
        assert penalty == pytest.approx(1.0)

    def test_limiter_default_is_off(self):
        result = _compute_gain_with_limiter(-20.0, -2.0, -14.0, 0.0)
        expected = _compute_gain(-20.0, -2.0, -14.0, 0.0)
        assert result == expected


# --- Limiting budget ---


class TestLimitingBudget:
    """Explicit tests for the limiting_budget_db parameter."""

    def test_budget_zero_pure_cap(self):
        # Budget 0 = gain capped at max_safe (no limiting at all)
        gain, peak, lim, pen = _compute_gain(-20.0, -2.0, -14.0, 0.0, limiting_budget_db=0.0)
        assert gain == pytest.approx(2.0)
        assert peak == pytest.approx(0.0)
        assert lim is False
        assert pen == pytest.approx(4.0)

    def test_budget_infinite_full_gain(self):
        # Budget inf = full desired gain regardless of peaks
        gain, peak, lim, pen = _compute_gain(-20.0, -2.0, -14.0, 0.0, limiting_budget_db=float("inf"))
        assert gain == pytest.approx(6.0)
        assert peak == pytest.approx(4.0)
        assert lim is True
        assert pen == pytest.approx(0.0)

    def test_budget_3_intermediate(self):
        # desired=6, max_safe=2, budget=3 → gain=min(6, 2+3)=5, penalty=1
        gain, peak, lim, pen = _compute_gain(-20.0, -2.0, -14.0, 0.0, limiting_budget_db=3.0)
        assert gain == pytest.approx(5.0)
        assert peak == pytest.approx(3.0)
        assert lim is True
        assert pen == pytest.approx(1.0)

    def test_budget_sufficient_reaches_target(self):
        # desired=3, max_safe=1, budget=3 → gain=min(3, 1+3)=3, penalty=0
        gain, peak, lim, pen = _compute_gain(-17.0, -2.0, -14.0, -1.0, limiting_budget_db=3.0)
        assert gain == pytest.approx(3.0)
        assert peak == pytest.approx(1.0)
        assert lim is True
        assert pen == pytest.approx(0.0)

    def test_no_limiting_needed_budget_irrelevant(self):
        # Attenuation case: desired=-6, max_safe=-1.5. desired ≤ max_safe → no limiting.
        gain, peak, lim, pen = _compute_gain(-8.0, 0.5, -14.0, -1.0, limiting_budget_db=3.0)
        assert gain == pytest.approx(-6.0)
        assert lim is False
        assert pen == pytest.approx(0.0)

    def test_budget_exactly_covers_excess(self):
        # desired=5, max_safe=2, budget=3 → gain=min(5, 2+3)=5, penalty=0
        gain, peak, lim, pen = _compute_gain(-19.0, -2.0, -14.0, 0.0, limiting_budget_db=3.0)
        assert gain == pytest.approx(5.0)
        assert pen == pytest.approx(0.0)
        assert lim is True

    def test_budget_1_small(self):
        # Restrictive budget: desired=6, max_safe=2, budget=1 → gain=3, penalty=3
        gain, peak, lim, pen = _compute_gain(-20.0, -2.0, -14.0, 0.0, limiting_budget_db=1.0)
        assert gain == pytest.approx(3.0)
        assert pen == pytest.approx(3.0)
        assert lim is True


# --- _compute_compressor_params ---


class TestComputeCompressorParams:

    def test_lra_within_range_no_compression(self):
        # VHS 90 dB → threshold = 90 * 0.25 = 22.5 LU
        assert _compute_compressor_params(15.0, 90) is None

    def test_lra_at_threshold_no_compression(self):
        # VHS 90 dB → threshold = 22.5 LU
        assert _compute_compressor_params(22.5, 90) is None

    def test_lra_above_threshold_returns_params(self):
        # VHS 90 dB → threshold = 22.5, LRA 25 exceeds
        params = _compute_compressor_params(25.0, 90)
        assert params is not None
        assert params["ratio"] > 1.0
        assert params["threshold"] < 1.0
        assert params["attack"] == 20.0
        assert params["release"] == 250.0
        assert params["knee"] == 2.83

    def test_higher_lra_higher_ratio(self):
        p1 = _compute_compressor_params(24.0, 90)
        p2 = _compute_compressor_params(30.0, 90)
        assert p2["ratio"] > p1["ratio"]

    def test_ratio_capped_at_3(self):
        params = _compute_compressor_params(50.0, 90)
        assert params["ratio"] == 3.0

    def test_threshold_db_capped_at_minus_40(self):
        params = _compute_compressor_params(50.0, 90)
        assert params["threshold_db"] == -40.0

    def test_small_medium_dynamic_range(self):
        # Cassette ~55 dB → threshold = 55 * 0.25 = 13.75 LU
        assert _compute_compressor_params(13.0, 55) is None
        params = _compute_compressor_params(15.0, 55)
        assert params is not None

    def test_type_i_bare_triggers_at_lra_15(self):
        # Type I ferric (57 dB) → threshold = 57 * 0.25 = 14.25 LU
        assert _compute_compressor_params(14.0, 57) is None
        params = _compute_compressor_params(15.0, 57)
        assert params is not None

    def test_metal_dolbyc_no_compression_at_lra_22(self):
        # Type IV + Dolby C (94 dB) → threshold = 94 * 0.25 = 23.5 LU
        assert _compute_compressor_params(23.0, 94) is None

    def test_open_reel_dolbysr_no_compression_at_lra_21(self):
        # Reel + Dolby SR (88 dB) → threshold = 88 * 0.25 = 22.0 LU
        assert _compute_compressor_params(21.0, 88) is None

    def test_minidisc_no_compression_at_lra_23(self):
        # MiniDisc (93 dB) → threshold = 93 * 0.25 = 23.25 LU
        assert _compute_compressor_params(23.0, 93) is None

    def test_threshold_scales_with_dr(self):
        # No fixed cap — higher DR = higher threshold
        # 80 dB: threshold 20, 96 dB: threshold 24
        assert _compute_compressor_params(21.0, 80) is not None   # 21 > 20
        assert _compute_compressor_params(21.0, 96) is None       # 21 < 24

    def test_vhs_does_not_compress_cd_source(self):
        # Wagner: LRA 22.1 from CD. VHS 90 dB → threshold 22.5.
        # CD source should not need compression on VHS Hi-Fi.
        assert _compute_compressor_params(22.1, 90) is None

    def test_cd_equivalent_never_compresses_cd_source(self):
        # CD dynamic range ~96 dB → threshold 24. Wagner LRA 22.1 passes.
        assert _compute_compressor_params(22.1, 96) is None


# --- CD transparency invariant ---


class TestCdTransparencyInvariant:
    """CD source → CD-class medium must be near-transparent.

    The library is EAC-ripped Red Book. When targeting a medium with
    comparable dynamic range (CD 96 dB, VHS 90 dB, MD 93 dB), the
    pipeline should only apply gain — no compression, no limiting
    for typical loud masters.
    """

    def test_loud_rock_no_limiter(self):
        # Metallica Black Album style: -8 LUFS, peak +0.5 dBTP
        # Target -14 → gain -6 dB, peak drops to -5.5. No limiter.
        gain, peak, limiter, penalty = _compute_gain(-8.0, 0.5, -14.0, -1.0)
        assert gain == pytest.approx(-6.0)
        assert peak == pytest.approx(-5.5)
        assert limiter is False
        assert penalty == pytest.approx(0.0)

    def test_moderate_rock_no_limiter(self):
        # Dylan Blood on the Tracks style: -14 LUFS, peak -1.5 dBTP
        # Target -14 → gain 0 dB. Peak stays at -1.5 (below ceiling). No limiter.
        gain, peak, limiter, penalty = _compute_gain(-14.0, -1.5, -14.0, -1.0)
        assert gain == pytest.approx(0.0)
        assert peak == pytest.approx(-1.5)
        assert limiter is False
        assert penalty == pytest.approx(0.0)

    def test_loudness_war_master_no_limiter(self):
        # Extreme loudness war: -6 LUFS, peak +1.2 dBTP (intersample)
        # Target -14 → gain -8 dB, peak drops to -6.8. No limiter.
        gain, peak, limiter, penalty = _compute_gain(-6.0, 1.2, -14.0, -1.0)
        assert gain == pytest.approx(-8.0)
        assert peak == pytest.approx(-6.8)
        assert limiter is False
        assert penalty == pytest.approx(0.0)

    def test_cd_medium_never_compresses_pop_rock(self):
        # Pop/rock LRA range: 4-12 LU. CD medium 96 dB → threshold 24 LU.
        for lra in (4.0, 6.0, 8.0, 10.0, 12.0):
            assert _compute_compressor_params(lra, 96) is None

    def test_cd_medium_never_compresses_jazz(self):
        # Jazz LRA: 10-16 LU. Still well below 24 LU threshold.
        for lra in (10.0, 12.0, 14.0, 16.0):
            assert _compute_compressor_params(lra, 96) is None

    def test_cd_medium_never_compresses_classical(self):
        # Classical LRA: 15-23 LU. All below 24 LU threshold.
        for lra in (15.0, 18.0, 20.0, 22.0, 23.0):
            assert _compute_compressor_params(lra, 96) is None

    def test_vhs_never_compresses_pop_rock(self):
        # VHS 90 dB → threshold 22.5 LU. Pop/rock never exceeds.
        for lra in (4.0, 6.0, 8.0, 10.0, 12.0):
            assert _compute_compressor_params(lra, 90) is None

    def test_md_never_compresses_pop_rock(self):
        # MD 93 dB → threshold 23.25 LU. Pop/rock never exceeds.
        for lra in (4.0, 6.0, 8.0, 10.0, 12.0):
            assert _compute_compressor_params(lra, 93) is None

    def test_quiet_classical_budget_limits_gain(self):
        # Quiet classical (-22 LUFS, peak -1) → ceiling -1 dBTP
        # Without --limiter: budget=3. desired=+8, max_safe=0, gain=min(8,3)=+3
        # Album plays at -19 LUFS (5 dB below target), 3 dB transparent limiting.
        gain, peak, limiter, penalty = _compute_gain_with_limiter(
            -22.0, -1.0, -14.0, -1.0, use_limiter=False,
        )
        assert gain == pytest.approx(3.0)
        assert peak == pytest.approx(2.0)
        assert limiter is True
        assert penalty == pytest.approx(5.0)
        # No compression — dynamics preserved (just 3 dB of peak shaving)
        assert _compute_compressor_params(20.0, 96) is None

    def test_quiet_classical_full_alignment_with_user_limiter(self):
        # Same album WITH --limiter: user chose volume over dynamics.
        # Full +8 dB gain, limiter catches 8 dB of peaks above -1 dBTP.
        gain, peak, limiter, penalty = _compute_gain_with_limiter(
            -22.0, -1.0, -14.0, -1.0, use_limiter=True,
        )
        assert gain == pytest.approx(8.0)
        assert peak == pytest.approx(7.0)
        assert limiter is True
        assert penalty == pytest.approx(0.0)


# --- find_best_split ---


class TestFindBestSplit:

    def test_all_fit(self):
        assert find_best_split([60, 60, 60], 300) == 3

    def test_none_fit(self):
        assert find_best_split([120, 60], 60) == 0

    def test_partial_fit(self):
        assert find_best_split([60, 60, 60, 60], 150) == 2

    def test_exact_fit(self):
        assert find_best_split([60, 60, 60], 180) == 3

    def test_single_track_too_long(self):
        assert find_best_split([300], 200) == 0

    def test_empty_tracks(self):
        assert find_best_split([], 300) == 0

    def test_first_track_fits_exactly(self):
        assert find_best_split([100, 100], 100) == 1


# --- assign_sides ---


class TestAssignSides:

    # Defaults: 25s lead-in, 20s lead-out, 8s album gap, 4s track gap
    _DEFAULTS = dict(lead_in_sec=25, lead_out_sec=20, album_gap_sec=8, track_gap_sec=4)

    def _item(self, pos, duration, item_type="album", pinned=None, tracks=None):
        return SideItem(
            position=pos, item_type=item_type, path=f"/music/{pos}",
            duration_sec=duration, pinned_side=pinned, track_durations=tracks,
        )

    def test_empty(self):
        result = assign_sides([], 2700, 2700, **self._DEFAULTS)
        assert result == []

    def test_single_item_fits_side_a(self):
        items = [self._item(1, 1200)]  # 20 min album, 45 min sides
        result = assign_sides(items, 2700, 2700, **self._DEFAULTS)
        assert len(result) == 1
        assert result[0].side == "a"

    def test_two_items_overflow_to_b(self):
        # Two 20-min albums, 30-min sides (1800s)
        # Overhead per side: 25 + 20 = 45s → usable 1755s per side
        # First album (1200s) fits A, second (1200s) goes to B
        items = [self._item(1, 1200), self._item(2, 1200)]
        result = assign_sides(items, 1800, 1800, **self._DEFAULTS)
        assert len(result) == 2
        assert result[0].side == "a"
        assert result[1].side == "b"

    def test_exact_fit_side_a(self):
        # Item exactly fills side A capacity (side_a - overhead)
        # 30 min side, 45s overhead → 1755s usable
        items = [self._item(1, 1755)]
        result = assign_sides(items, 1800, 1800, **self._DEFAULTS)
        assert len(result) == 1
        assert result[0].side == "a"

    def test_album_split_at_boundary(self):
        # 10-track album, 25 min total. Side A has 15 min (900s) usable.
        # Tracks: 10 x 150s = 1500s total
        # Tracks 1-6 (900s) fit on A, tracks 7-10 (600s) go to B
        tracks = [150.0] * 10
        items = [self._item(1, 1500, tracks=tracks)]
        # Side capacity: 900 + 45 (overhead) = 945s
        result = assign_sides(items, 945, 2700, **self._DEFAULTS)
        assert len(result) == 2
        assert result[0].side == "a"
        assert result[0].track_range == (0, 6)
        assert result[0].duration_sec == pytest.approx(900.0)
        assert result[1].side == "b"
        assert result[1].track_range == (6, 10)
        assert result[1].duration_sec == pytest.approx(600.0)

    def test_album_single_track_no_split(self):
        # Single-track album too big for A → goes to B whole (no split)
        tracks = [1800.0]  # 30 min
        items = [self._item(1, 1800, tracks=tracks)]
        result = assign_sides(items, 945, 2700, **self._DEFAULTS)
        assert len(result) == 1
        assert result[0].side == "b"
        assert result[0].track_range is None

    def test_album_no_tracks_fit_goes_to_b(self):
        # First track alone exceeds remaining capacity → whole album to B
        tracks = [600.0, 600.0]  # 10 + 10 min
        items = [self._item(1, 300), self._item(2, 1200, tracks=tracks)]
        # Side A: 300 consumed, remaining = 1755 - 300 - 8 (gap) = 1447
        # But album is 1200 which fits... let me make it not fit
        # Side A capacity: 400 + 45 = 445s. Item 1 (300s) fills, remaining = 100 - 8 (gap) = 92
        # Album track 1 (600s) doesn't fit → 0 tracks → whole album to B
        result = assign_sides(items, 445, 2700, **self._DEFAULTS)
        assert len(result) == 2
        assert result[0].side == "a"
        assert result[1].side == "b"
        assert result[1].track_range is None

    def test_pinned_item_side_b(self):
        items = [
            self._item(1, 1200),
            self._item(2, 1200, pinned="b"),
        ]
        result = assign_sides(items, 2700, 2700, **self._DEFAULTS)
        sides = {r.position: r.side for r in result}
        assert sides[1] == "a"
        assert sides[2] == "b"

    def test_pinned_item_side_a(self):
        # Side A: 1300s, overhead 45s → usable 1255s
        # Pinned item (1200s) consumes most of A, auto item (1200s) doesn't fit → B
        items = [
            self._item(1, 1200, pinned="a"),
            self._item(2, 1200),
        ]
        result = assign_sides(items, 1300, 2700, **self._DEFAULTS)
        sides = {r.position: r.side for r in result}
        assert sides[1] == "a"
        assert sides[2] == "b"

    def test_pinned_preserves_position_order(self):
        # Items: [1 auto, 2 pinned_a, 3 auto]. All fit on A.
        # Must play in position order: 1, 2, 3 — not 2, 1, 3.
        items = [
            self._item(1, 600),
            self._item(2, 600, pinned="a"),
            self._item(3, 600),
        ]
        result = assign_sides(items, 2700, 2700, **self._DEFAULTS)
        a_positions = [r.position for r in result if r.side == "a"]
        assert a_positions == [1, 2, 3]

    def test_pinned_b_preserves_order(self):
        # Items: [1 pinned_b, 2 auto→B, 3 pinned_b]. Must be 1, 2, 3 on B.
        items = [
            self._item(1, 600, pinned="b"),
            self._item(2, 5000),  # too big for A → goes to B
            self._item(3, 600, pinned="b"),
        ]
        result = assign_sides(items, 100, 2700, **self._DEFAULTS)
        b_positions = [r.position for r in result if r.side == "b"]
        assert b_positions == [1, 2, 3]

    def test_pinned_order_capacity_uses_final_gap_sequence(self):
        # Old bug: pinned A was accounted before auto items, so the algorithm
        # counted album→track then track→track gaps instead of final playback
        # order track→album→track. That let item 3 overfill side A.
        items = [
            self._item(1, 50, item_type="track"),
            self._item(2, 50, item_type="album", pinned="a"),
            self._item(3, 50, item_type="track"),
        ]
        result = assign_sides(items, 207, 2700, **self._DEFAULTS)
        sides = {r.position: r.side for r in result}
        assert sides[1] == "a"
        assert sides[2] == "a"
        assert sides[3] == "b"

    def test_future_pinned_reservation_prevents_auto_overfill(self):
        # Item 1 fits on an empty side A, but not once the future pinned item
        # is reserved in its final playback position.
        items = [
            self._item(1, 600),
            self._item(2, 600, pinned="a"),
        ]
        result = assign_sides(items, 1000, 2700, **self._DEFAULTS)
        sides = {r.position: r.side for r in result}
        assert sides[1] == "b"
        assert sides[2] == "a"

    def test_split_album_accounts_for_future_pinned_item(self):
        # Full album + pinned item is over side A. The largest prefix that
        # fits with the pinned item in final order is two tracks.
        items = [
            self._item(1, 150, tracks=[50.0, 50.0, 50.0]),
            self._item(2, 50, pinned="a"),
        ]
        result = assign_sides(items, 203, 2700, **self._DEFAULTS)
        prefix = next(r for r in result if r.position == 1 and r.side == "a")
        suffix = next(r for r in result if r.position == 1 and r.side == "b")
        assert prefix.track_range == (0, 2)
        assert prefix.duration_sec == pytest.approx(100.0)
        assert suffix.track_range == (2, 3)
        assert suffix.duration_sec == pytest.approx(50.0)

    def test_pinned_exceeds_capacity(self):
        # Pinned item larger than side → still assigned (warning elsewhere)
        items = [self._item(1, 5000, pinned="a")]
        result = assign_sides(items, 2700, 2700, **self._DEFAULTS)
        assert len(result) == 1
        assert result[0].side == "a"

    def test_gaps_counted_in_capacity(self):
        # Two 800s albums on 30-min side (1800s)
        # Overhead: 45s. Usable: 1755s. Two albums + gap: 800 + 8 + 800 = 1608 → fits
        items = [self._item(1, 800), self._item(2, 800)]
        result = assign_sides(items, 1800, 1800, **self._DEFAULTS)
        assert all(r.side == "a" for r in result)

    def test_gap_pushes_to_side_b(self):
        # Two albums: barely fits without gap, doesn't with gap
        # Usable: 1755s. Albums: 876 + 876 = 1752 (fits). But gap 8s → 1760 > 1755 → second to B
        items = [self._item(1, 876), self._item(2, 876)]
        result = assign_sides(items, 1800, 1800, **self._DEFAULTS)
        assert result[0].side == "a"
        assert result[1].side == "b"

    def test_track_gap_smaller_than_album_gap(self):
        # Two tracks use track_gap (4s), not album_gap (8s)
        # Usable: 1755s. Tracks: 876 + 876 = 1752. Gap 4s → 1756 > 1755 → second to B
        items = [
            self._item(1, 876, item_type="track"),
            self._item(2, 876, item_type="track"),
        ]
        result = assign_sides(items, 1800, 1800, **self._DEFAULTS)
        assert result[0].side == "a"
        assert result[1].side == "b"

    def test_three_items_first_two_on_a(self):
        items = [self._item(1, 600), self._item(2, 600), self._item(3, 600)]
        # Usable: 1755s. Items 1+2 = 600 + 8 + 600 = 1208 → fits. Item 3 = +8+600 = 1816 > 1755 → B
        result = assign_sides(items, 1800, 1800, **self._DEFAULTS)
        assert result[0].side == "a"
        assert result[1].side == "a"
        assert result[2].side == "b"


# --- format_analysis ---


class TestFormatAnalysis:

    def test_two_sided_duration_summary_is_side_aware(self):
        result = AnalysisResult(
            project_name="Test",
            medium="c-90",
            target_lufs=-14.0,
            source_rates={44100: 1},
            resolved_rate=44100,
            items=[],
            lufs_spread=0.0,
            lra_min=0.0,
            lra_max=0.0,
            duration_total_sec=111.0,
            duration_capacity_sec=120.0,
            warnings=["Side A: over"],
            side_durations=[("A", 121.0, 60.0), ("B", 50.0, 60.0)],
        )

        out = format_analysis(result)

        assert (
            "Duration:     Side A 2:01 / 1:00 (OVER); "
            "Side B 0:50 / 1:00 (fits)"
        ) in out

    def test_capacity_summary_uses_nominal_grace_before_over(self):
        result = AnalysisResult(
            project_name="Test",
            medium="vhs-120",
            target_lufs=-14.0,
            source_rates={44100: 1},
            resolved_rate=44100,
            items=[],
            lufs_spread=0.0,
            lra_min=0.0,
            lra_max=0.0,
            duration_total_sec=92.0,
            duration_capacity_sec=60.0,
        )

        out = format_analysis(result)

        assert "Duration:     1:32 / 1:00 (grace)" in out
        assert "No issues found." in out


class TestCapacityStatus:

    def test_under_capacity_fits(self):
        assert _capacity_status(59.0, 60.0) == "fits"

    def test_over_capacity_within_grace(self):
        assert _capacity_status(90.0, 60.0) == "grace"

    def test_over_capacity_past_grace(self):
        assert _capacity_status(121.0, 60.0) == "OVER"
