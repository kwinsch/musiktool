"""Shared constants for musiktool."""

from pathlib import Path

AUDIO_EXTENSIONS = frozenset({".flac", ".mp3", ".m4a", ".ogg", ".wav", ".ape", ".wma"})

# Maximum dB of true-peak limiting applied automatically during gain alignment.
# 3 dB catches transients the ear doesn't resolve. Beyond this, sustained peaks
# (fortissimo, brass) become audibly affected. See docs/playback-normalization.md.
TRANSPARENT_LIMITING_BUDGET_DB = 3.0

# Default peak ceiling for digital output. 0 dBTP = full scale.
# Hard-clip media (VHS, MD, CD) use -1 dBTP for intersample safety;
# soft-clip media (cassette, reel) use 0 dBTP. Digital playback uses 0 dBTP.
PEAK_CEILING_DBTP = 0.0

OUTPUT_FORMATS = frozenset({".flac", ".wav"})

SAMPLE_RATES = (44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000)

# --- Medium presets ---
#
# dynamic_range_db drives the compressor decision: if an album's LRA exceeds
# what the medium can capture, gentle compression is applied. Higher DR = less
# compression. The value represents the usable dynamic range from noise floor
# to the onset of unacceptable distortion for the given tape type + NR combo.
#
# The rendering pipeline is purely digital (target LUFS, peak ceiling, gain).
# Calibration maps digital levels to the physical medium — it is NOT part of
# the preset. See docs/medium-selection-guide.md for details.

# -- Cassette generation tables --

_CASSETTE_BASES = {
    60:  {"duration_min": 60,  "side_a_min": 30, "side_b_min": 30},
    90:  {"duration_min": 90,  "side_a_min": 45, "side_b_min": 45},
    120: {"duration_min": 120, "side_a_min": 60, "side_b_min": 60},
}

# Base dynamic range by tape type (midpoint of documented range)
_CASSETTE_TYPES = {
    "i": 57,    # Type I ferric: 55-60 dB
    "ii": 65,   # Type II chrome/cobalt: 62-68 dB
    "iv": 74,   # Type IV metal particle: 70-78 dB
}

# Noise reduction bonus (additive dB)
_CASSETTE_NR = {
    "": 0,           # No noise reduction
    "-dolbyb": 10,   # Dolby B NR
    "-dolbyc": 20,   # Dolby C NR
    "-dolbys": 24,   # Dolby S NR
}

# -- Open reel generation tables --
# Half-track stereo, standard play (1.5 mil) tape.

_REEL_CONFIGS = {
    "reel-7-half-7.5ips":  {"duration_min": 30, "dynamic_range_db": 66},
    "reel-7-half-15ips":   {"duration_min": 15, "dynamic_range_db": 65},
    "reel-10-half-7.5ips": {"duration_min": 60, "dynamic_range_db": 66},
    "reel-10-half-15ips":  {"duration_min": 30, "dynamic_range_db": 65},
}

_REEL_NR = {
    "": 0,            # No noise reduction
    "-dolbya": 12,    # Dolby A NR
    "-dolbysr": 22,   # Dolby SR NR
    "-dbx": 30,       # dbx Type I NR
}


def _build_medium_presets() -> dict:
    """Build the full MEDIUM_PRESETS dict from generation tables."""
    presets = {}

    # VHS Hi-Fi (single-sided, FM-encoded — tape type irrelevant)
    # 90 dB per Panasonic AG-7350 spec sheet (Hi-Fi Dynamic Range)
    # Hard clip: FM deviation limit is absolute, no usable range above 0
    presets["vhs-120"] = {"duration_min": 118, "dynamic_range_db": 90, "sides": 1, "peak_behavior": "hard"}
    presets["vhs-160"] = {"duration_min": 158, "dynamic_range_db": 90, "sides": 1, "peak_behavior": "hard"}
    presets["vhs-180"] = {"duration_min": 176, "dynamic_range_db": 90, "sides": 1, "peak_behavior": "hard"}

    # Legacy cassette presets (Type I, no NR — backward compatible)
    # Soft clip: amplitude-based, progressive saturation above 0 VU
    presets["c-60"] = {
        "duration_min": 60, "side_a_min": 30, "side_b_min": 30,
        "dynamic_range_db": 55, "sides": 2, "peak_behavior": "soft",
    }
    presets["c-90"] = {
        "duration_min": 90, "side_a_min": 45, "side_b_min": 45,
        "dynamic_range_db": 55, "sides": 2, "peak_behavior": "soft",
    }
    presets["c-120"] = {
        "duration_min": 120, "side_a_min": 60, "side_b_min": 60,
        "dynamic_range_db": 55, "sides": 2, "peak_behavior": "soft",
    }

    # Cassette subtypes: c-<length>-<type>[-<nr>]
    for length, base in _CASSETTE_BASES.items():
        for type_name, type_dr in _CASSETTE_TYPES.items():
            for nr_suffix, nr_bonus in _CASSETTE_NR.items():
                # Dolby S was never practical on Type I decks
                if nr_suffix == "-dolbys" and type_name == "i":
                    continue
                name = f"c-{length}-{type_name}{nr_suffix}"
                presets[name] = {
                    "duration_min": base["duration_min"],
                    "side_a_min": base["side_a_min"],
                    "side_b_min": base["side_b_min"],
                    "dynamic_range_db": type_dr + nr_bonus,
                    "sides": 2,
                    "peak_behavior": "soft",
                }

    # Open reel: reel-<size>-half-<speed>[-<nr>]
    # Soft clip: amplitude-based, progressive saturation
    for reel_name, reel_cfg in _REEL_CONFIGS.items():
        for nr_suffix, nr_bonus in _REEL_NR.items():
            name = f"{reel_name}{nr_suffix}"
            presets[name] = {
                "duration_min": reel_cfg["duration_min"],
                "dynamic_range_db": reel_cfg["dynamic_range_db"] + nr_bonus,
                "sides": 1,
                "peak_behavior": "soft",
            }

    # MiniDisc SP (magneto-optical, ATRAC codec — hard ceiling like VHS)
    presets["md-60"] = {"duration_min": 60, "dynamic_range_db": 93, "sides": 1, "peak_behavior": "hard"}
    presets["md-74"] = {"duration_min": 74, "dynamic_range_db": 93, "sides": 1, "peak_behavior": "hard"}
    presets["md-80"] = {"duration_min": 80, "dynamic_range_db": 93, "sides": 1, "peak_behavior": "hard"}

    # CD-DA (Red Book — 16-bit linear PCM, hard digital ceiling)
    presets["cd-74"] = {"duration_min": 74, "dynamic_range_db": 96, "sides": 1, "peak_behavior": "hard"}
    presets["cd-80"] = {"duration_min": 80, "dynamic_range_db": 96, "sides": 1, "peak_behavior": "hard"}

    # Vinyl LP (two-sided)
    # Hard clip: groove excursion limit / cutting head overload
    presets["vinyl-lp"] = {
        "duration_min": 44, "side_a_min": 22, "side_b_min": 22,
        "dynamic_range_db": 70, "sides": 2, "peak_behavior": "hard",
    }

    return presets


MEDIUM_PRESETS = _build_medium_presets()


def collect_audio_files(path: Path) -> list[Path]:
    """Collect audio files from a path. Single file or sorted directory contents."""
    if path.is_file():
        return [path] if path.suffix.lower() in AUDIO_EXTENSIONS else []
    return sorted(
        f for f in path.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )
