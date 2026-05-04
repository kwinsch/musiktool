"""EBU R128 loudness measurement using ffmpeg."""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mutagen

from musiktool.exceptions import AudioReadError


@dataclass
class LoudnessInfo:
    integrated_lufs: float
    true_peak_dbtp: float
    lra: float  # loudness range
    duration_sec: float
    path: str


def _get_duration(path: str) -> float:
    """Get track duration in seconds via mutagen."""
    f = mutagen.File(path)
    if f is None:
        raise AudioReadError(f"mutagen could not open: {path}")
    if f.info is None:
        raise AudioReadError(f"no stream info in: {path}")
    return f.info.length


def measure_track(path: str) -> LoudnessInfo:
    """Measure EBU R128 loudness for a single track."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-i", path,
            "-af", "ebur128=framelog=verbose:peak=true",
            "-f", "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    # ebur128 outputs to stderr
    output = result.stderr

    integrated = _parse_summary_value(output, "I:")
    true_peak = _parse_summary_value(output, "Peak:")
    lra = _parse_summary_value(output, "LRA:")
    duration = _get_duration(path)

    return LoudnessInfo(
        integrated_lufs=integrated,
        true_peak_dbtp=true_peak,
        lra=lra,
        duration_sec=duration,
        path=path,
    )


def measure_album(
    directory: str, tracks: list[LoudnessInfo] | None = None,
) -> tuple[list[LoudnessInfo], LoudnessInfo | None]:
    """Measure all tracks in a directory, return per-track and album loudness.

    If tracks are provided, skip per-track measurement (use pre-measured data).
    Album-level integrated LUFS and LRA are measured from the concatenated stream.
    Album true peak is the max across all per-track true peaks.
    """
    from musiktool.constants import AUDIO_EXTENSIONS

    if tracks is None:
        tracks = []
        for p in sorted(Path(directory).iterdir()):
            if p.suffix.lower() in AUDIO_EXTENSIONS:
                tracks.append(measure_track(str(p)))

    if not tracks:
        return [], None

    # Album-level measurement via concat demuxer (reliable across formats)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=True,
    ) as f:
        for t in tracks:
            # Escape single quotes in paths for ffmpeg concat format
            escaped = t.path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        f.flush()

        result = subprocess.run(
            [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", f.name,
                "-af", "ebur128=framelog=verbose:peak=true",
                "-f", "null",
                "-",
            ],
            capture_output=True,
            text=True,
        )

    output = result.stderr
    album_lufs = _parse_summary_value(output, "I:")
    album_lra = _parse_summary_value(output, "LRA:")

    # Album true peak = max across all tracks
    album_peak = max(t.true_peak_dbtp for t in tracks)
    album_duration = sum(t.duration_sec for t in tracks)

    album_info = LoudnessInfo(
        integrated_lufs=album_lufs,
        true_peak_dbtp=album_peak,
        lra=album_lra,
        duration_sec=album_duration,
        path=directory,
    )

    return tracks, album_info


def _parse_summary_value(output: str, key: str) -> float:
    """Parse a value from ffmpeg ebur128 summary output.

    Only looks in the Summary section (after 'Summary:' line).
    For 'LRA:' only matches the first occurrence (not LRA low/high).
    """
    lines = output.split("\n")
    in_summary = False
    for line in lines:
        if "Summary:" in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        stripped = line.strip()
        if stripped.startswith(key):
            num_str = stripped[len(key):].strip().split()[0]
            try:
                return float(num_str)
            except ValueError:
                continue
    raise ValueError(f"failed to parse '{key}' from ffmpeg ebur128 output")
