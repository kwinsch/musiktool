"""Audio segment generation via ffmpeg lavfi.

Segments are declarative descriptions of audio content (silence, tones).
They are rendered to files by building an ffmpeg filter_complex that
concatenates all segments and encodes to the output format.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from musiktool.constants import OUTPUT_FORMATS

@dataclass(frozen=True)
class Silence:
    """A segment of digital silence."""

    duration: float  # seconds

    def __post_init__(self) -> None:
        assert self.duration > 0, f"silence duration must be positive: {self.duration}"


@dataclass(frozen=True)
class Tone:
    """A sine tone segment at a specific level."""

    frequency: float  # Hz
    level_dbfs: float  # 0 = full scale, negative = below
    duration: float  # seconds

    def __post_init__(self) -> None:
        assert self.frequency > 0, f"frequency must be positive: {self.frequency}"
        assert self.level_dbfs <= 0, f"level_dbfs must be <= 0: {self.level_dbfs}"
        assert self.duration > 0, f"tone duration must be positive: {self.duration}"


@dataclass(frozen=True)
class FileInput:
    """An audio file input with optional gain and processing."""

    path: str
    gain_db: float = 0.0
    limiter_limit: float | None = None       # linear amplitude for alimiter limit
    compressor_params: dict | None = None     # {ratio, threshold, attack, release, knee}


Segment = Silence | Tone | FileInput


def _lavfi_expr(segment: Segment, *, sample_rate: int, channels: int) -> str:
    """Build an ffmpeg lavfi source expression for a segment.

    Returns a string suitable for use as `-f lavfi -i "..."`.
    """
    ch_layout = "stereo" if channels == 2 else "mono"

    match segment:
        case Silence(duration=dur):
            return f"aevalsrc=0:s={sample_rate}:d={dur}:c={ch_layout}"
        case Tone(frequency=freq, level_dbfs=level, duration=dur):
            amplitude = 10 ** (level / 20)
            return (
                f"aevalsrc=sin(2*PI*{freq}*t)*{amplitude:.10g}"
                f":s={sample_rate}:d={dur}:c={ch_layout}"
            )
        case _:
            raise TypeError(f"unknown segment type: {type(segment)}")


def _build_ffmpeg_cmd(
    segments: list[Segment],
    output: Path,
    *,
    sample_rate: int,
    bit_depth: int,
    channels: int,
) -> list[str]:
    """Build the ffmpeg command list for rendering segments to a file.

    Handles mixed lavfi (Silence, Tone) and file (FileInput) inputs.
    File inputs with non-zero gain get a volume filter applied.
    """
    assert segments, "segments list must not be empty"
    assert bit_depth in (16, 24), f"bit_depth must be 16 or 24: {bit_depth}"

    suffix = output.suffix.lower()
    assert suffix in OUTPUT_FORMATS, f"unsupported output format: {suffix}"

    cmd: list[str] = ["ffmpeg", "-y"]

    # Add inputs — lavfi for generated segments, file for FileInput
    for seg in segments:
        match seg:
            case FileInput(path=path):
                cmd.extend(["-i", path])
            case _:
                expr = _lavfi_expr(seg, sample_rate=sample_rate, channels=channels)
                cmd.extend(["-f", "lavfi", "-i", expr])

    # Build filter graph: apply processing chain to FileInputs, then concat all
    filter_parts = []
    concat_labels = []

    for i, seg in enumerate(segments):
        match seg:
            case FileInput() as fi if (
                fi.gain_db != 0.0
                or fi.limiter_limit is not None
                or fi.compressor_params is not None
            ):
                label = f"g{i}"
                chain = []
                if fi.gain_db != 0.0:
                    chain.append(f"volume={fi.gain_db:.2f}dB")
                if fi.compressor_params is not None:
                    cp = fi.compressor_params
                    chain.append(
                        f"acompressor=threshold={cp['threshold']}"
                        f":ratio={cp['ratio']}"
                        f":attack={cp['attack']}"
                        f":release={cp['release']}"
                        f":knee={cp['knee']}"
                    )
                if fi.limiter_limit is not None:
                    chain.append(
                        f"alimiter=limit={fi.limiter_limit:.6f}"
                        f":attack=5:release=50"
                    )
                filter_parts.append(f"[{i}:a]{','.join(chain)}[{label}]")
                concat_labels.append(f"[{label}]")
            case _:
                concat_labels.append(f"[{i}:a]")

    n = len(segments)
    concat_expr = "".join(concat_labels) + f"concat=n={n}:v=0:a=1[out]"
    if filter_parts:
        filter_expr = ";".join(filter_parts) + ";" + concat_expr
    else:
        filter_expr = concat_expr

    cmd.extend(["-filter_complex", filter_expr, "-map", "[out]"])

    # Output encoding
    if suffix == ".flac":
        sample_fmt = "s16" if bit_depth == 16 else "s32"
        cmd.extend(["-c:a", "flac", "-sample_fmt", sample_fmt])
    elif suffix == ".wav":
        codec = "pcm_s16le" if bit_depth == 16 else "pcm_s24le"
        cmd.extend(["-c:a", codec])

    cmd.extend(["-ar", str(sample_rate), str(output)])
    return cmd


def render(
    segments: list[Segment],
    output: Path,
    *,
    sample_rate: int = 44100,
    bit_depth: int = 24,
    channels: int = 2,
) -> None:
    """Render a sequence of segments to an audio file.

    Builds a single ffmpeg command that generates all segments via lavfi
    sources, concatenates them, and encodes to the output format (inferred
    from file extension: .flac or .wav).
    """
    assert segments, "segments list must not be empty"
    assert bit_depth in (16, 24), f"bit_depth must be 16 or 24: {bit_depth}"

    suffix = output.suffix.lower()
    if suffix not in OUTPUT_FORMATS:
        raise ValueError(f"unsupported output format: {suffix} (use .flac or .wav)")

    cmd = _build_ffmpeg_cmd(
        segments, output,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        channels=channels,
    )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}):\n{result.stderr}"
        )
