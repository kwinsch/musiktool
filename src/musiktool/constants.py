"""Shared constants for musiktool."""

AUDIO_EXTENSIONS = frozenset({".flac", ".mp3", ".m4a", ".ogg", ".wav", ".ape", ".wma"})

OUTPUT_FORMATS = frozenset({".flac", ".wav"})

SAMPLE_RATES = (44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000)

MEDIUM_PRESETS = {
    # Single-sided (VHS Hi-Fi)
    "vhs-120": {"duration_min": 118, "dynamic_range_db": 80, "sides": 1},
    "vhs-160": {"duration_min": 158, "dynamic_range_db": 80, "sides": 1},
    "vhs-180": {"duration_min": 176, "dynamic_range_db": 80, "sides": 1},
    # Two-sided (cassette)
    "c-60":  {"duration_min": 60,  "side_a_min": 30, "side_b_min": 30, "dynamic_range_db": 55, "sides": 2},
    "c-90":  {"duration_min": 90,  "side_a_min": 45, "side_b_min": 45, "dynamic_range_db": 55, "sides": 2},
    "c-120": {"duration_min": 120, "side_a_min": 60, "side_b_min": 60, "dynamic_range_db": 55, "sides": 2},
    # Two-sided (vinyl LP)
    "vinyl-lp": {"duration_min": 44, "side_a_min": 22, "side_b_min": 22, "dynamic_range_db": 70, "sides": 2},
}
