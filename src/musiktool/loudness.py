"""EBU R128 loudness measurement using ffmpeg."""

import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import mutagen

from musiktool.constants import PEAK_CEILING_DBTP, TRANSPARENT_LIMITING_BUDGET_DB
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
    from musiktool.constants import collect_audio_files

    directory_path = Path(directory).resolve()
    if tracks is None:
        tracks = [
            measure_track(str(p.resolve()))
            for p in collect_audio_files(directory_path)
        ]
    else:
        tracks = [
            replace(t, path=str(Path(t.path).resolve()))
            for t in tracks
        ]

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
    if result.returncode != 0:
        raise AudioReadError(
            f"ffmpeg album measurement failed for {directory_path}: "
            f"{_stderr_tail(output)}"
        )
    try:
        album_lufs = _parse_summary_value(output, "I:")
        album_lra = _parse_summary_value(output, "LRA:")
    except ValueError as e:
        raise AudioReadError(
            f"failed to parse album loudness for {directory_path}: {e}\n"
            f"{_stderr_tail(output)}"
        ) from e

    # Album true peak = max across all tracks
    album_peak = max(t.true_peak_dbtp for t in tracks)
    album_duration = sum(t.duration_sec for t in tracks)

    album_info = LoudnessInfo(
        integrated_lufs=album_lufs,
        true_peak_dbtp=album_peak,
        lra=album_lra,
        duration_sec=album_duration,
        path=str(directory_path),
    )

    return tracks, album_info


def _stderr_tail(output: str, *, max_lines: int = 20) -> str:
    """Return a concise stderr tail for diagnostics."""
    lines = output.strip().splitlines()
    if not lines:
        return "(no stderr)"
    return "\n".join(lines[-max_lines:])


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


# --- Gain computation ---


def compute_gain(
    measured_lufs: float,
    true_peak_dbtp: float,
    target_lufs: float,
    peak_ceiling_dbtp: float = PEAK_CEILING_DBTP,
    limiting_budget_db: float = TRANSPARENT_LIMITING_BUDGET_DB,
) -> tuple[float, float, bool, float]:
    """Compute gain with bounded peak limiting.

    The limiting budget determines how many dB of peak limiting is acceptable
    to reach (or approach) target LUFS. Gain is applied up to
    max_safe_gain + budget, with the limiter catching the excess peaks.

    Returns (gain_db, peak_after_gain, needs_limiter, limiter_penalty_db).
    - gain_db: actual gain applied
    - peak_after_gain: true peak after gain (before limiter)
    - needs_limiter: True if gain pushes peaks above ceiling
    - limiter_penalty_db: how much quieter than target due to budget exhaustion
    """
    desired_gain = target_lufs - measured_lufs
    max_safe_gain = peak_ceiling_dbtp - true_peak_dbtp

    if desired_gain <= max_safe_gain:
        return desired_gain, true_peak_dbtp + desired_gain, False, 0.0

    # Peaks prevent reaching target. Apply gain up to budget.
    gain = min(desired_gain, max_safe_gain + limiting_budget_db)
    peak_after = true_peak_dbtp + gain
    penalty = desired_gain - gain
    needs_limiter = gain > max_safe_gain
    return gain, peak_after, needs_limiter, penalty


def compute_gain_with_limiter(
    measured_lufs: float,
    true_peak_dbtp: float,
    target_lufs: float,
    peak_ceiling_dbtp: float = PEAK_CEILING_DBTP,
    use_limiter: bool = False,
) -> tuple[float, float, bool, float]:
    """Compute gain with optional unlimited limiting budget.

    When use_limiter is False: uses TRANSPARENT_LIMITING_BUDGET_DB (3 dB).
    When use_limiter is True: unlimited budget (full desired gain applied).

    Returns (gain_db, peak_after_gain, needs_limiter, limiter_penalty_db).
    """
    budget = float("inf") if use_limiter else TRANSPARENT_LIMITING_BUDGET_DB
    return compute_gain(
        measured_lufs, true_peak_dbtp, target_lufs, peak_ceiling_dbtp,
        limiting_budget_db=budget,
    )
