"""Tests for tape project logic — pure functions only."""

import pytest

from musiktool.tape import (
    SideItem,
    _compute_compressor_params,
    _compute_gain,
    _compute_gain_with_limiter,
    _format_cue_time,
    _format_deck_time,
    _resolve_sample_rate,
    assign_sides,
    compute_gap,
    find_best_split,
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
        # But peak at -2 dBTP, ceiling 0 → max safe gain is +2 dB
        gain, peak, limiter, penalty = _compute_gain(-20.0, -2.0, -14.0, 0.0)
        assert gain == pytest.approx(2.0)
        assert peak == pytest.approx(0.0)
        assert limiter is True
        assert penalty == pytest.approx(4.0)  # 6 - 2 = 4 dB penalty

    def test_peak_at_zero_no_gain_possible(self):
        # Peak already at 0 dBTP, ceiling 0 → no gain allowed
        gain, peak, limiter, penalty = _compute_gain(-20.0, 0.0, -14.0, 0.0)
        assert gain == pytest.approx(0.0)
        assert peak == pytest.approx(0.0)
        assert limiter is True
        assert penalty == pytest.approx(6.0)

    def test_negative_peak_ceiling(self):
        # Ceiling at -1 dBTP (headroom for DAC intersample peaks)
        gain, peak, limiter, penalty = _compute_gain(-14.0, -3.0, -14.0, -1.0)
        # Desired gain = 0, max safe = -1 - (-3) = +2 → no limiting needed
        assert gain == pytest.approx(0.0)
        assert limiter is False

    def test_negative_ceiling_triggers_limiter(self):
        # -20 LUFS, target -14, peak -2, ceiling -1
        # Desired = +6, max safe = -1 - (-2) = +1
        gain, peak, limiter, penalty = _compute_gain(-20.0, -2.0, -14.0, -1.0)
        assert gain == pytest.approx(1.0)
        assert peak == pytest.approx(-1.0)
        assert limiter is True
        assert penalty == pytest.approx(5.0)

    def test_very_quiet_classical(self):
        # Classical album at -25 LUFS, peak -8, target -14
        # Desired = +11, max safe = 0 - (-8) = +8
        gain, peak, limiter, penalty = _compute_gain(-25.0, -8.0, -14.0, 0.0)
        assert gain == pytest.approx(8.0)
        assert peak == pytest.approx(0.0)
        assert limiter is True
        assert penalty == pytest.approx(3.0)


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

    def test_limiter_off_preserves_penalty(self):
        gain, peak, limiter, penalty = _compute_gain_with_limiter(
            -20.0, -2.0, -14.0, 0.0, use_limiter=False,
        )
        assert gain == pytest.approx(2.0)
        assert penalty == pytest.approx(4.0)

    def test_limiter_default_is_off(self):
        result = _compute_gain_with_limiter(-20.0, -2.0, -14.0, 0.0)
        expected = _compute_gain(-20.0, -2.0, -14.0, 0.0)
        assert result == expected


# --- _compute_compressor_params ---


class TestComputeCompressorParams:

    def test_lra_within_range_no_compression(self):
        assert _compute_compressor_params(15.0, 80) is None

    def test_lra_at_threshold_no_compression(self):
        # Threshold for 80 dB medium: min(80 * 0.25, 20) = 20 LU
        assert _compute_compressor_params(20.0, 80) is None

    def test_lra_above_threshold_returns_params(self):
        params = _compute_compressor_params(25.0, 80)
        assert params is not None
        assert params["ratio"] > 1.0
        assert params["threshold"] < 1.0
        assert params["attack"] == 20.0
        assert params["release"] == 250.0
        assert params["knee"] == 2.83

    def test_higher_lra_higher_ratio(self):
        p1 = _compute_compressor_params(22.0, 80)
        p2 = _compute_compressor_params(30.0, 80)
        assert p2["ratio"] > p1["ratio"]

    def test_ratio_capped_at_3(self):
        params = _compute_compressor_params(50.0, 80)
        assert params["ratio"] == 3.0

    def test_threshold_db_capped_at_minus_40(self):
        params = _compute_compressor_params(50.0, 80)
        assert params["threshold_db"] == -40.0

    def test_small_medium_dynamic_range(self):
        # Cassette ~55 dB → threshold = min(55 * 0.25, 20) = 13.75 LU
        assert _compute_compressor_params(13.0, 55) is None
        params = _compute_compressor_params(15.0, 55)
        assert params is not None


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
