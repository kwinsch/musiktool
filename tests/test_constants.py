"""Tests for musiktool.constants."""

from pathlib import Path

import pytest

from musiktool.constants import (
    MEDIUM_PRESETS,
    _CASSETTE_BASES,
    _CASSETTE_NR,
    _CASSETTE_TYPES,
    _REEL_CONFIGS,
    _REEL_NR,
    collect_audio_files,
)


# --- Medium preset validation ---


class TestMediumPresets:
    """Validate preset structure, backward compatibility, and correctness."""

    def test_vhs_presets(self):
        assert MEDIUM_PRESETS["vhs-120"] == {"duration_min": 118, "dynamic_range_db": 90, "sides": 1, "peak_behavior": "hard"}
        assert MEDIUM_PRESETS["vhs-160"] == {"duration_min": 158, "dynamic_range_db": 90, "sides": 1, "peak_behavior": "hard"}
        assert MEDIUM_PRESETS["vhs-180"] == {"duration_min": 176, "dynamic_range_db": 90, "sides": 1, "peak_behavior": "hard"}

    def test_backward_compat_cassette(self):
        for name, dur, side in [("c-60", 60, 30), ("c-90", 90, 45), ("c-120", 120, 60)]:
            p = MEDIUM_PRESETS[name]
            assert p["duration_min"] == dur
            assert p["side_a_min"] == side
            assert p["side_b_min"] == side
            assert p["dynamic_range_db"] == 55
            assert p["sides"] == 2
            assert p["peak_behavior"] == "soft"

    def test_backward_compat_vinyl(self):
        assert MEDIUM_PRESETS["vinyl-lp"] == {
            "duration_min": 44, "side_a_min": 22, "side_b_min": 22,
            "dynamic_range_db": 70, "sides": 2, "peak_behavior": "hard",
        }

    def test_all_presets_have_required_keys(self):
        for name, p in MEDIUM_PRESETS.items():
            assert "duration_min" in p, f"{name} missing duration_min"
            assert "dynamic_range_db" in p, f"{name} missing dynamic_range_db"
            assert "sides" in p, f"{name} missing sides"
            assert "peak_behavior" in p, f"{name} missing peak_behavior"
            assert p["peak_behavior"] in ("hard", "soft"), f"{name} invalid peak_behavior"
            if p["sides"] == 2:
                assert "side_a_min" in p, f"{name} missing side_a_min"
                assert "side_b_min" in p, f"{name} missing side_b_min"

    def test_vhs_md_cd_vinyl_are_hard_clip(self):
        for name in ("vhs-120", "vhs-160", "vhs-180", "md-60", "md-74", "md-80", "cd-74", "cd-80", "vinyl-lp"):
            assert MEDIUM_PRESETS[name]["peak_behavior"] == "hard", f"{name} should be hard"

    def test_cassette_reel_are_soft_clip(self):
        for name, p in MEDIUM_PRESETS.items():
            if name.startswith("c-") or name.startswith("reel-"):
                assert p["peak_behavior"] == "soft", f"{name} should be soft"

    def test_cassette_dr_ordering_by_type(self):
        """Type I < Type II < Type IV for same NR level."""
        for nr_suffix in _CASSETTE_NR:
            for length in _CASSETTE_BASES:
                names = [f"c-{length}-{t}{nr_suffix}" for t in ("i", "ii", "iv")]
                # Skip if Dolby S on Type I (doesn't exist)
                existing = [n for n in names if n in MEDIUM_PRESETS]
                drs = [MEDIUM_PRESETS[n]["dynamic_range_db"] for n in existing]
                assert drs == sorted(drs), f"DR not ascending for {existing}: {drs}"

    def test_nr_always_increases_dr(self):
        """Adding NR always increases DR for same tape type."""
        for length in _CASSETTE_BASES:
            for type_name in _CASSETTE_TYPES:
                base_name = f"c-{length}-{type_name}"
                if base_name not in MEDIUM_PRESETS:
                    continue
                base_dr = MEDIUM_PRESETS[base_name]["dynamic_range_db"]
                for nr_suffix in _CASSETTE_NR:
                    if nr_suffix == "":
                        continue
                    name = f"{base_name}{nr_suffix}"
                    if name in MEDIUM_PRESETS:
                        assert MEDIUM_PRESETS[name]["dynamic_range_db"] > base_dr, (
                            f"{name} DR should exceed {base_name} DR"
                        )

    def test_no_dolbys_on_type_i(self):
        """Dolby S presets should not exist for Type I."""
        for length in _CASSETTE_BASES:
            assert f"c-{length}-i-dolbys" not in MEDIUM_PRESETS

    def test_open_reel_single_sided(self):
        for name in MEDIUM_PRESETS:
            if name.startswith("reel-"):
                assert MEDIUM_PRESETS[name]["sides"] == 1, f"{name} should be single-sided"

    def test_minidisc_single_sided(self):
        for name in ("md-60", "md-74", "md-80"):
            assert name in MEDIUM_PRESETS
            assert MEDIUM_PRESETS[name]["sides"] == 1
            assert MEDIUM_PRESETS[name]["dynamic_range_db"] == 93

    def test_cd_da_presets(self):
        for name in ("cd-74", "cd-80"):
            assert name in MEDIUM_PRESETS
            p = MEDIUM_PRESETS[name]
            assert p["sides"] == 1
            assert p["dynamic_range_db"] == 96
            assert p["peak_behavior"] == "hard"
        assert MEDIUM_PRESETS["cd-74"]["duration_min"] == 74
        assert MEDIUM_PRESETS["cd-80"]["duration_min"] == 80

    def test_total_preset_count(self):
        # 3 VHS + 3 legacy cassette + 33 cassette subtypes + 16 reel + 3 MD + 2 CD + 1 vinyl = 61
        assert len(MEDIUM_PRESETS) == 61

    def test_cassette_subtypes_share_duration_with_base(self):
        """All c-90-* variants should have same duration as c-90."""
        for name, p in MEDIUM_PRESETS.items():
            if name.startswith("c-90-"):
                assert p["duration_min"] == 90
                assert p["side_a_min"] == 45
                assert p["side_b_min"] == 45

    def test_reel_nr_increases_dr(self):
        """NR always increases DR for open reel."""
        for reel_name, reel_cfg in _REEL_CONFIGS.items():
            base_dr = MEDIUM_PRESETS[reel_name]["dynamic_range_db"]
            for nr_suffix in _REEL_NR:
                if nr_suffix == "":
                    continue
                name = f"{reel_name}{nr_suffix}"
                assert MEDIUM_PRESETS[name]["dynamic_range_db"] > base_dr


class TestCollectAudioFiles:
    def test_single_audio_file(self, tmp_path: Path):
        f = tmp_path / "track.flac"
        f.touch()
        assert collect_audio_files(f) == [f]

    def test_single_non_audio_file(self, tmp_path: Path):
        f = tmp_path / "readme.txt"
        f.touch()
        assert collect_audio_files(f) == []

    def test_directory_filters_and_sorts(self, tmp_path: Path):
        flac = tmp_path / "02 Second.flac"
        mp3 = tmp_path / "01 First.mp3"
        txt = tmp_path / "notes.txt"
        m4a = tmp_path / "03 Third.m4a"
        for f in (flac, mp3, txt, m4a):
            f.touch()
        result = collect_audio_files(tmp_path)
        assert result == [mp3, flac, m4a]
        assert txt not in result

    def test_empty_directory(self, tmp_path: Path):
        assert collect_audio_files(tmp_path) == []

    def test_ignores_subdirectories(self, tmp_path: Path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "track.flac").touch()
        (tmp_path / "01 Track.flac").touch()
        result = collect_audio_files(tmp_path)
        assert len(result) == 1
        assert result[0].name == "01 Track.flac"

    def test_case_insensitive_extension(self, tmp_path: Path):
        f = tmp_path / "track.FLAC"
        f.touch()
        assert collect_audio_files(f) == [f]

    def test_all_supported_extensions(self, tmp_path: Path):
        for ext in (".flac", ".mp3", ".m4a", ".ogg", ".wav", ".ape", ".wma"):
            (tmp_path / f"track{ext}").touch()
        assert len(collect_audio_files(tmp_path)) == 7
