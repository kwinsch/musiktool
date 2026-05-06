"""Playback via mpv with EBU R128 loudness normalization.

Manages an mpv subprocess with JSON IPC over a Unix socket.
Per-track gain is applied via mpv's af= per-file options,
using the same gain computation as the tape mastering pipeline.
"""

import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from musiktool import db
from musiktool.config import get_config
from musiktool.constants import PEAK_CEILING_DBTP, collect_audio_files
from musiktool.exceptions import (
    MpvCommandError,
    MpvConnectionError,
    MpvNotFoundError,
    RenderNotFoundError,
)
from musiktool.loudness import compute_gain


DEFAULT_TARGET_LUFS = -14.0
TapePlayMode = Literal["rendered", "playlist"]

_IPC_TIMEOUT = 2.0  # seconds
_SPAWN_TIMEOUT = 2.0  # seconds to wait for mpv socket after spawn
_SPAWN_POLL_INTERVAL = 0.05  # seconds between socket checks


def _socket_path() -> Path:
    return get_config().data_dir / "mpv.sock"


def _pid_path() -> Path:
    return get_config().data_dir / "mpv.pid"


@dataclass
class GainResult:
    gain_db: float
    needs_limiter: bool
    limiter_limit: float | None  # linear amplitude for alimiter, None if no limiter
    penalty_db: float
    source: str  # "album" or "none"


@dataclass
class PlaybackStatus:
    state: str  # "playing", "paused", "stopped", "idle"
    path: str | None
    title: str | None
    artist: str | None
    album: str | None
    position_sec: float
    duration_sec: float
    playlist_count: int
    playlist_pos: int  # 0-based, -1 if empty


# --- mpv IPC client ---


class MpvClient:
    """Manages mpv subprocess and JSON IPC communication."""

    def __init__(
        self,
        socket_path: Path | None = None,
        pid_path: Path | None = None,
    ):
        self._socket_path = socket_path if socket_path is not None else _socket_path()
        self._pid_path = pid_path if pid_path is not None else _pid_path()
        self._sock: socket.socket | None = None
        self._buf = b""

    def ensure_running(self) -> None:
        """Connect to an existing mpv or spawn a new one."""
        if self._try_connect():
            return
        self._cleanup_stale()
        self._spawn()
        self._wait_for_socket()
        if not self._try_connect():
            raise MpvConnectionError("failed to connect after spawning mpv")

    def _try_connect(self) -> bool:
        """Attempt to connect to the IPC socket. Returns True on success."""
        if not self._socket_path.exists():
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_IPC_TIMEOUT)
        try:
            sock.connect(str(self._socket_path))
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            sock.close()
            return False
        self._sock = sock
        return True

    def _cleanup_stale(self) -> None:
        """Remove stale socket and kill orphaned mpv process."""
        if self._pid_path.exists():
            try:
                pid = int(self._pid_path.read_text().strip())
                os.kill(pid, 0)  # check if alive
                if self._is_mpv_process(pid):
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.1)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
            self._pid_path.unlink(missing_ok=True)
        self._socket_path.unlink(missing_ok=True)

    @staticmethod
    def _is_mpv_process(pid: int) -> bool:
        """Check /proc/<pid>/cmdline to verify the process is mpv."""
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            return b"mpv" in cmdline
        except (FileNotFoundError, PermissionError):
            return False

    def _spawn(self) -> None:
        """Start mpv in idle mode with IPC socket."""
        mpv_bin = shutil.which("mpv")
        if mpv_bin is None:
            raise MpvNotFoundError("mpv not found on PATH — install mpv for playback")

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        proc = subprocess.Popen(
            [
                mpv_bin,
                "--idle",
                "--no-terminal",
                "--no-video",
                "--gapless-audio=weak",
                "--audio-display=no",
                "--really-quiet",
                f"--input-ipc-server={self._socket_path}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._pid_path.write_text(str(proc.pid))

    def _wait_for_socket(self) -> None:
        """Poll until the socket file appears or timeout."""
        deadline = time.monotonic() + _SPAWN_TIMEOUT
        while time.monotonic() < deadline:
            if self._socket_path.exists():
                return
            time.sleep(_SPAWN_POLL_INTERVAL)
        raise MpvConnectionError(
            f"mpv did not create socket within {_SPAWN_TIMEOUT}s"
        )

    def send_command(self, *args: object) -> dict:
        """Send a JSON IPC command and return the response."""
        assert self._sock is not None, "not connected — call ensure_running() first"
        msg = json.dumps({"command": list(args)}) + "\n"
        try:
            self._sock.sendall(msg.encode())
        except (BrokenPipeError, OSError) as e:
            raise MpvConnectionError(f"failed to send command: {e}") from e
        return self._read_response()

    def _read_response(self) -> dict:
        """Read lines from socket until we get a command response (not an event)."""
        assert self._sock is not None
        while True:
            # Check buffer for complete lines
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Skip event messages, return command responses
                if "event" not in data and "error" in data:
                    return data

            # Read more data
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout as e:
                raise MpvConnectionError("mpv IPC response timeout") from e
            except OSError as e:
                raise MpvConnectionError(f"socket read error: {e}") from e
            if not chunk:
                raise MpvConnectionError("mpv closed connection")
            self._buf += chunk

    def get_property(self, name: str) -> object:
        """Get an mpv property value."""
        resp = self.send_command("get_property", name)
        if resp.get("error") != "success":
            return None
        return resp.get("data")

    def set_property(self, name: str, value: object) -> None:
        """Set an mpv property."""
        self.send_command("set_property", name, value)

    def loadfile(
        self,
        path: str,
        mode: str = "replace",
        options: str | None = None,
    ) -> None:
        """Load a file into mpv's playlist.

        mode: "replace", "append-play", "insert-next"
        options: per-file options string (e.g. "af=volume=3.0dB")

        Raises MpvCommandError if mpv reports an error.
        """
        args: list[object] = ["loadfile", path, mode]
        if options is not None:
            args.extend([-1, options])
        resp = self.send_command(*args)
        if resp.get("error") != "success":
            raise MpvCommandError(
                f"mpv loadfile failed for '{path}': {resp.get('error', 'unknown')}"
            )

    def playlist_next(self) -> None:
        self.send_command("playlist-next", "weak")

    def playlist_prev(self) -> None:
        self.send_command("playlist-prev", "weak")

    def toggle_pause(self) -> None:
        paused = self.get_property("pause")
        self.set_property("pause", not paused)

    def stop(self) -> None:
        """Stop playback and clear playlist. mpv stays idle."""
        self.send_command("stop")

    def quit(self) -> None:
        """Quit mpv process."""
        try:
            self.send_command("quit")
        except MpvConnectionError:
            pass  # expected — mpv closes the connection on quit
        self.close()
        self._socket_path.unlink(missing_ok=True)
        self._pid_path.unlink(missing_ok=True)

    def get_status(self) -> PlaybackStatus:
        """Query current playback state."""
        idle = self.get_property("idle-active")
        paused = self.get_property("pause")
        path = self.get_property("path")
        playlist_count = self.get_property("playlist-count") or 0
        playlist_pos = self.get_property("playlist-pos")
        if playlist_pos is None:
            playlist_pos = -1

        if idle or path is None:
            state = "idle"
        elif paused:
            state = "paused"
        else:
            state = "playing"

        return PlaybackStatus(
            state=state,
            path=str(path) if path else None,
            title=self.get_property("media-title") or None,
            artist=self.get_property("metadata/by-key/artist") or None,
            album=self.get_property("metadata/by-key/album") or None,
            position_sec=self.get_property("time-pos") or 0.0,
            duration_sec=self.get_property("duration") or 0.0,
            playlist_count=int(playlist_count),
            playlist_pos=int(playlist_pos),
        )

    def close(self) -> None:
        """Close socket connection. mpv keeps running."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._buf = b""


# --- Normalization ---


def compute_playback_gain(
    conn: sqlite3.Connection,
    file_path: str,
    target_lufs: float = DEFAULT_TARGET_LUFS,
) -> GainResult | None:
    """Compute gain for a track using its album's loudness.

    Returns None if the track or album is not in the analytics DB.
    Uses album-level LUFS and peak (atomic album model — all tracks
    in an album share one gain value).
    """
    track = db.get_track(conn, file_path)
    if track is None:
        return None

    album = db.get_album(conn, track["album_path"])
    if album is None:
        return None

    gain_db, _peak_after, needs_limiter, penalty = compute_gain(
        album["integrated_lufs"],
        album["true_peak_dbtp"],
        target_lufs,
        peak_ceiling_dbtp=PEAK_CEILING_DBTP,
    )

    limiter_limit: float | None = None
    if needs_limiter:
        # Convert peak ceiling from dBTP to linear amplitude
        limiter_limit = 10 ** (PEAK_CEILING_DBTP / 20)

    return GainResult(
        gain_db=gain_db,
        needs_limiter=needs_limiter,
        limiter_limit=limiter_limit,
        penalty_db=penalty,
        source="album",
    )


def build_af_filter(
    gain_db: float,
    limiter_limit: float | None = None,
    compressor_params: dict | None = None,
) -> str | None:
    """Build mpv af= option string for playback.

    Returns None if no processing is needed (zero gain, no limiter/compressor).
    """
    if gain_db == 0.0 and limiter_limit is None and compressor_params is None:
        return None

    parts = []
    if gain_db != 0.0:
        parts.append(f"volume={gain_db:.2f}dB")

    lavfi_filters = []
    if compressor_params is not None:
        cp = compressor_params
        lavfi_filters.append(
            f"acompressor=threshold={cp['threshold']}"
            f":ratio={cp['ratio']}"
            f":attack={cp['attack']}"
            f":release={cp['release']}"
            f":knee={cp['knee']}"
        )
    if limiter_limit is not None:
        lavfi_filters.append(
            f"alimiter=limit={limiter_limit:.6f}:attack=5:release=50"
        )

    if lavfi_filters:
        parts.append(f"lavfi=[{','.join(lavfi_filters)}]")

    return "af=" + ",".join(parts)


# --- Queue functions ---


def queue_album(
    client: MpvClient,
    conn: sqlite3.Connection | None,
    album_path: Path,
    mode: str = "replace",
    normalize: bool = True,
) -> list[tuple[str, str | None]]:
    """Queue all tracks in an album with per-track gain.

    Returns list of (path, af_options) tuples for reporting.
    All tracks in the album share one album-level gain value.
    """
    tracks = collect_audio_files(album_path)
    assert tracks, f"no audio files found in {album_path}"

    # Compute gain once for the album (atomic album model)
    af: str | None = None
    gain_result: GainResult | None = None
    if normalize and conn is not None:
        gain_result = compute_playback_gain(conn, str(tracks[0].resolve()))
        if gain_result is not None:
            af = build_af_filter(gain_result.gain_db, gain_result.limiter_limit)

    result = []
    for i, track in enumerate(tracks):
        track_mode = mode if i == 0 else "append-play"
        client.loadfile(str(track.resolve()), track_mode, af)
        result.append((str(track), af))

    return result


def queue_file(
    client: MpvClient,
    conn: sqlite3.Connection | None,
    file_path: Path,
    mode: str = "replace",
    normalize: bool = True,
) -> tuple[str, str | None]:
    """Queue a single file with gain normalization.

    Returns (path, af_options) for reporting.
    """
    resolved = str(file_path.resolve())

    af: str | None = None
    if normalize and conn is not None:
        gain_result = compute_playback_gain(conn, resolved)
        if gain_result is not None:
            af = build_af_filter(gain_result.gain_db, gain_result.limiter_limit)

    client.loadfile(resolved, mode, af)
    return resolved, af


def queue_tape(
    client: MpvClient,
    conn: sqlite3.Connection,
    project_name: str,
    mode: str = "replace",
    render_dir: Path | None = None,
    *,
    tape_mode: TapePlayMode = "rendered",
) -> list[tuple[str, str | None]]:
    """Queue a tape project for playback.

    tape_mode:
        "rendered" - play rendered FLAC output only; raise if not found
        "playlist" - play source tracks with tape processing chain (preview)
    """
    from musiktool import tape

    project = tape.get_project(conn, project_name)
    slug = _slugify(project_name)

    if tape_mode == "rendered":
        rendered = _find_rendered_output(slug, render_dir)
        if not rendered:
            raise RenderNotFoundError(
                f"No rendered output found for '{project_name}'. "
                "Run 'musiktool tape render' first, or use --as-playlist for preview."
            )
        result = []
        for i, flac_path in enumerate(rendered):
            track_mode = mode if i == 0 else "append-play"
            client.loadfile(str(flac_path), track_mode)
            result.append((str(flac_path), None))
        return result

    # tape_mode == "playlist" — real-time playback with processing chain
    analysis = tape.analyze_project(conn, project)
    layout = tape.compute_project_layout(conn, project)

    result = []
    first = True
    for layout_item in layout.items:
        item_analysis = next(
            a for a in analysis.items if a.position == layout_item.position
        )

        # Determine track range for split albums
        track_range = layout_item.track_range

        # Collect source tracks
        if item_analysis.item_type == "album":
            all_tracks = collect_audio_files(Path(item_analysis.path))
            if track_range is not None:
                all_tracks = all_tracks[track_range[0]:track_range[1]]
        else:
            all_tracks = [Path(item_analysis.path)]

        # Build per-track filter from tape analysis
        af = build_af_filter(
            item_analysis.gain_db,
            limiter_limit=(
                10 ** (project["peak_ceiling_dbtp"] / 20)
                if item_analysis.needs_limiter and item_analysis.use_limiter
                else None
            ),
            compressor_params=item_analysis.compressor_params,
        )

        for track in all_tracks:
            track_mode = mode if first else "append-play"
            first = False
            client.loadfile(str(track.resolve()), track_mode, af)
            result.append((str(track), af))

    return result


def _slugify(name: str) -> str:
    """Convert project name to filesystem-safe slug."""
    import re

    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _find_rendered_output(
    slug: str, render_dir: Path | None = None,
) -> list[Path] | None:
    """Look for rendered FLAC files for a tape project.

    Checks render_dir (if given), then config render_dir.
    Returns sorted list of FLAC paths, or None if not found.
    """
    search_dirs = []
    if render_dir is not None:
        search_dirs.append(render_dir / slug)
        search_dirs.append(render_dir)

    config_render = get_config().render_dir
    if config_render is not None:
        candidate = config_render / slug
        if candidate not in search_dirs:
            search_dirs.append(candidate)

    for d in search_dirs:
        if not d.is_dir():
            continue
        # Single-sided: <slug>.flac
        single = d / f"{slug}.flac"
        if single.is_file():
            return [single]
        # Two-sided: <slug>-side-a.flac, <slug>-side-b.flac
        side_a = d / f"{slug}-side-a.flac"
        side_b = d / f"{slug}-side-b.flac"
        if side_a.is_file() or side_b.is_file():
            sides = []
            if side_a.is_file():
                sides.append(side_a)
            if side_b.is_file():
                sides.append(side_b)
            return sides

    return None
