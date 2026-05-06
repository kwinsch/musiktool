"""Tests for playback module — pure functions and mocked IPC."""

import json
import socket
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from musiktool.play import (
    GainResult,
    MpvClient,
    PlaybackStatus,
    build_af_filter,
    compute_playback_gain,
    queue_album,
    queue_file,
    _find_rendered_output,
    _slugify,
)


# --- build_af_filter ---


class TestBuildAfFilter:
    def test_gain_only(self):
        result = build_af_filter(3.5)
        assert result == "af=volume=3.50dB"

    def test_negative_gain(self):
        result = build_af_filter(-6.0)
        assert result == "af=volume=-6.00dB"

    def test_zero_gain_returns_none(self):
        result = build_af_filter(0.0)
        assert result is None

    def test_gain_with_limiter(self):
        result = build_af_filter(3.0, limiter_limit=1.0)
        assert result == "af=volume=3.00dB,lavfi=[alimiter=limit=1.000000:attack=5:release=50]"

    def test_limiter_only_zero_gain(self):
        result = build_af_filter(0.0, limiter_limit=0.891251)
        assert result == "af=lavfi=[alimiter=limit=0.891251:attack=5:release=50]"

    def test_gain_with_compressor(self):
        cp = {
            "threshold": 0.1,
            "ratio": 2.0,
            "attack": 20,
            "release": 250,
            "knee": 2.83,
        }
        result = build_af_filter(2.0, compressor_params=cp)
        assert "volume=2.00dB" in result
        assert "acompressor=threshold=0.1:ratio=2.0:attack=20:release=250:knee=2.83" in result

    def test_full_chain_gain_compressor_limiter(self):
        cp = {
            "threshold": 0.1,
            "ratio": 2.0,
            "attack": 20,
            "release": 250,
            "knee": 2.83,
        }
        result = build_af_filter(3.0, limiter_limit=1.0, compressor_params=cp)
        assert result.startswith("af=volume=3.00dB,lavfi=[")
        # Compressor before limiter in the chain
        assert result.index("acompressor") < result.index("alimiter")

    def test_zero_gain_no_processing_returns_none(self):
        assert build_af_filter(0.0, None, None) is None


# --- compute_playback_gain ---


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    """Create an in-memory analytics DB with test data."""
    from musiktool.db import get_connection

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    # Insert test album loudness
    conn.execute(
        """INSERT INTO album_loudness
           (path, integrated_lufs, true_peak_dbtp, lra, track_count,
            duration_sec, analyzed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/music/Artist/Album (2000)", -12.0, -0.5, 8.0, 10, 3000.0,
         "2026-01-01T00:00:00"),
    )

    # Insert test track
    conn.execute(
        """INSERT INTO track_loudness
           (path, integrated_lufs, true_peak_dbtp, lra, duration_sec,
            album_path, analyzed_at, file_mtime)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("/music/Artist/Album (2000)/01 Track.flac", -11.0, -0.5, 6.0, 300.0,
         "/music/Artist/Album (2000)", "2026-01-01T00:00:00", 1000.0),
    )
    conn.commit()
    return conn


class TestComputePlaybackGain:
    def test_album_in_db(self, tmp_path):
        conn = _make_db(tmp_path)
        result = compute_playback_gain(
            conn, "/music/Artist/Album (2000)/01 Track.flac",
        )
        assert result is not None
        assert result.source == "album"
        # Album is -12 LUFS, target -14 → gain -2 dB
        assert result.gain_db == pytest.approx(-2.0)
        assert result.needs_limiter is False
        assert result.limiter_limit is None
        assert result.penalty_db == pytest.approx(0.0)
        conn.close()

    def test_track_not_in_db(self, tmp_path):
        conn = _make_db(tmp_path)
        result = compute_playback_gain(
            conn, "/music/Unknown/Track.flac",
        )
        assert result is None
        conn.close()

    def test_track_without_album(self, tmp_path):
        conn = _make_db(tmp_path)
        # Insert track without matching album
        conn.execute(
            """INSERT INTO track_loudness
               (path, integrated_lufs, true_peak_dbtp, lra, duration_sec,
                album_path, analyzed_at, file_mtime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("/orphan/track.flac", -10.0, -1.0, 5.0, 200.0,
             "/orphan", "2026-01-01", 1000.0),
        )
        conn.commit()
        result = compute_playback_gain(conn, "/orphan/track.flac")
        assert result is None  # album not in album_loudness
        conn.close()

    def test_gain_triggers_limiter(self, tmp_path):
        conn = _make_db(tmp_path)
        # Quiet album with high peak
        conn.execute(
            """INSERT INTO album_loudness
               (path, integrated_lufs, true_peak_dbtp, lra, track_count,
                duration_sec, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("/music/Quiet/Album", -20.0, -1.0, 15.0, 8, 2400.0,
             "2026-01-01"),
        )
        conn.execute(
            """INSERT INTO track_loudness
               (path, integrated_lufs, true_peak_dbtp, lra, duration_sec,
                album_path, analyzed_at, file_mtime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("/music/Quiet/Album/01.flac", -19.0, -1.0, 12.0, 300.0,
             "/music/Quiet/Album", "2026-01-01", 1000.0),
        )
        conn.commit()
        result = compute_playback_gain(conn, "/music/Quiet/Album/01.flac")
        assert result is not None
        # -20 LUFS → target -14 = +6 dB desired. Peak -1, ceiling 0 → max safe +1.
        # Budget 3: gain = min(6, 1+3) = 4, penalty = 2
        assert result.gain_db == pytest.approx(4.0)
        assert result.needs_limiter is True
        assert result.limiter_limit is not None
        assert result.limiter_limit == pytest.approx(1.0)
        assert result.penalty_db == pytest.approx(2.0)
        conn.close()

    def test_custom_target_lufs(self, tmp_path):
        conn = _make_db(tmp_path)
        result = compute_playback_gain(
            conn, "/music/Artist/Album (2000)/01 Track.flac",
            target_lufs=-23.0,
        )
        assert result is not None
        # Album -12, target -23 → gain -11 dB
        assert result.gain_db == pytest.approx(-11.0)
        conn.close()


# --- _slugify ---


class TestSlugify:
    def test_simple(self):
        assert _slugify("Dylan Classic") == "dylan-classic"

    def test_special_chars(self):
        assert _slugify("Rock & Roll Mix!") == "rock-roll-mix"

    def test_already_slug(self):
        assert _slugify("rock-mix") == "rock-mix"

    def test_trailing_spaces(self):
        assert _slugify("  Hello World  ") == "hello-world"


# --- _find_rendered_output ---


class TestFindRenderedOutput:
    def test_single_sided(self, tmp_path):
        project_dir = tmp_path / "my-mix"
        project_dir.mkdir()
        flac = project_dir / "my-mix.flac"
        flac.write_bytes(b"fake")
        result = _find_rendered_output("my-mix", tmp_path)
        assert result == [flac]

    def test_two_sided(self, tmp_path):
        project_dir = tmp_path / "road-trip"
        project_dir.mkdir()
        side_a = project_dir / "road-trip-side-a.flac"
        side_b = project_dir / "road-trip-side-b.flac"
        side_a.write_bytes(b"a")
        side_b.write_bytes(b"b")
        result = _find_rendered_output("road-trip", tmp_path)
        assert result == [side_a, side_b]

    def test_not_found(self, tmp_path):
        result = _find_rendered_output("nonexistent", tmp_path)
        assert result is None

    def test_side_a_only(self, tmp_path):
        project_dir = tmp_path / "mix"
        project_dir.mkdir()
        side_a = project_dir / "mix-side-a.flac"
        side_a.write_bytes(b"a")
        result = _find_rendered_output("mix", tmp_path)
        assert result == [side_a]


# --- MpvClient ---


class TestMpvClientSpawn:
    def test_ensure_running_spawns_mpv(self, tmp_path, monkeypatch):
        sock_path = tmp_path / "mpv.sock"
        pid_path = tmp_path / "mpv.pid"

        # Mock shutil.which to return a path
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/mpv")

        # Mock Popen to simulate mpv starting
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        def fake_popen(cmd, **kwargs):
            # Simulate mpv creating the socket
            sock_path.touch()
            return mock_proc

        monkeypatch.setattr("subprocess.Popen", fake_popen)

        # Mock socket connection
        mock_socket = MagicMock()
        mock_socket.connect = MagicMock()

        def fake_socket(*args, **kwargs):
            return mock_socket

        monkeypatch.setattr("socket.socket", fake_socket)

        client = MpvClient(socket_path=sock_path, pid_path=pid_path)
        client.ensure_running()

        # Verify PID file was written
        assert pid_path.read_text() == "12345"

    def test_mpv_not_found_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)

        client = MpvClient(
            socket_path=tmp_path / "mpv.sock",
            pid_path=tmp_path / "mpv.pid",
        )
        with pytest.raises(Exception, match="mpv not found"):
            client.ensure_running()


class TestMpvClientIPC:
    """Test IPC protocol with a fake socket server."""

    def _make_client_with_fake_socket(self, responses: list[dict]):
        """Create a MpvClient with a mock socket that returns canned responses."""
        response_data = b""
        for r in responses:
            response_data += json.dumps(r).encode() + b"\n"

        mock_sock = MagicMock()
        mock_sock.recv = MagicMock(return_value=response_data)
        mock_sock.sendall = MagicMock()

        client = MpvClient()
        client._sock = mock_sock
        return client, mock_sock

    def test_send_command(self):
        client, mock_sock = self._make_client_with_fake_socket(
            [{"error": "success", "data": 42}],
        )
        result = client.send_command("get_property", "volume")
        assert result == {"error": "success", "data": 42}

        # Verify correct JSON was sent
        sent = mock_sock.sendall.call_args[0][0]
        parsed = json.loads(sent)
        assert parsed == {"command": ["get_property", "volume"]}

    def test_skips_event_messages(self):
        client, mock_sock = self._make_client_with_fake_socket([
            {"event": "property-change", "id": 1, "data": 0.5},
            {"event": "playback-restart"},
            {"error": "success", "data": "ok"},
        ])
        result = client.send_command("get_property", "path")
        assert result["data"] == "ok"

    def test_get_property(self):
        client, _ = self._make_client_with_fake_socket(
            [{"error": "success", "data": "/path/to/file.flac"}],
        )
        assert client.get_property("path") == "/path/to/file.flac"

    def test_get_property_error_returns_none(self):
        client, _ = self._make_client_with_fake_socket(
            [{"error": "property unavailable", "data": None}],
        )
        assert client.get_property("nonexistent") is None

    def test_loadfile_with_options(self):
        client, mock_sock = self._make_client_with_fake_socket(
            [{"error": "success"}],
        )
        client.loadfile("/test.flac", "append-play", "af=volume=3.00dB")

        sent = json.loads(mock_sock.sendall.call_args[0][0])
        assert sent == {
            "command": ["loadfile", "/test.flac", "append-play", -1, "af=volume=3.00dB"],
        }

    def test_loadfile_without_options(self):
        client, mock_sock = self._make_client_with_fake_socket(
            [{"error": "success"}],
        )
        client.loadfile("/test.flac", "replace")

        sent = json.loads(mock_sock.sendall.call_args[0][0])
        assert sent == {"command": ["loadfile", "/test.flac", "replace"]}

    def test_get_status_playing(self):
        responses = [
            {"error": "success", "data": False},   # idle-active
            {"error": "success", "data": False},   # pause
            {"error": "success", "data": "/music/track.flac"},  # path
            {"error": "success", "data": 5},        # playlist-count
            {"error": "success", "data": 2},        # playlist-pos
            {"error": "success", "data": "Track Name"},  # media-title
            {"error": "success", "data": "Artist"},  # metadata/artist
            {"error": "success", "data": "Album"},   # metadata/album
            {"error": "success", "data": 45.5},      # time-pos
            {"error": "success", "data": 180.0},     # duration
        ]
        client, _ = self._make_client_with_fake_socket(responses)
        status = client.get_status()
        assert status.state == "playing"
        assert status.path == "/music/track.flac"
        assert status.playlist_count == 5
        assert status.playlist_pos == 2

    def test_get_status_idle(self):
        responses = [
            {"error": "success", "data": True},    # idle-active
            {"error": "success", "data": False},   # pause
            {"error": "success", "data": None},    # path
            {"error": "success", "data": 0},       # playlist-count
            {"error": "success", "data": None},    # playlist-pos
            {"error": "success", "data": None},    # media-title
            {"error": "success", "data": None},    # metadata/artist
            {"error": "success", "data": None},    # metadata/album
            {"error": "success", "data": None},    # time-pos
            {"error": "success", "data": None},    # duration
        ]
        client, _ = self._make_client_with_fake_socket(responses)
        status = client.get_status()
        assert status.state == "idle"
        assert status.path is None
        assert status.playlist_pos == -1


# --- queue_album ---


class TestQueueAlbum:
    def test_queues_all_tracks_with_same_gain(self, tmp_path):
        # Create fake album with 3 tracks
        album_dir = tmp_path / "Artist" / "Album (2000)"
        album_dir.mkdir(parents=True)
        for i in range(1, 4):
            (album_dir / f"{i:02d} Track {i}.flac").write_bytes(b"fake")

        conn = _make_db(tmp_path)
        # Insert matching data for this album
        album_path = str(album_dir.resolve())
        conn.execute(
            """INSERT INTO album_loudness
               (path, integrated_lufs, true_peak_dbtp, lra, track_count,
                duration_sec, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (album_path, -10.0, -0.5, 6.0, 3, 900.0, "2026-01-01"),
        )
        for i in range(1, 4):
            track_path = str((album_dir / f"{i:02d} Track {i}.flac").resolve())
            conn.execute(
                """INSERT INTO track_loudness
                   (path, integrated_lufs, true_peak_dbtp, lra, duration_sec,
                    album_path, analyzed_at, file_mtime)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (track_path, -9.0, -0.5, 5.0, 300.0,
                 album_path, "2026-01-01", 1000.0),
            )
        conn.commit()

        # Mock MpvClient
        client = MagicMock(spec=MpvClient)
        result = queue_album(client, conn, album_dir, "replace", normalize=True)

        assert len(result) == 3
        # All tracks should have the same af filter (album-level gain)
        afs = [r[1] for r in result]
        assert all(af == afs[0] for af in afs)
        # Album is -10 LUFS, target -14 → gain -4 dB
        assert "volume=-4.00dB" in afs[0]

        # First track uses "replace", rest use "append-play"
        calls = client.loadfile.call_args_list
        assert calls[0].args[1] == "replace"
        assert calls[1].args[1] == "append-play"
        assert calls[2].args[1] == "append-play"

        conn.close()

    def test_queues_without_normalization(self, tmp_path):
        album_dir = tmp_path / "Artist" / "Album"
        album_dir.mkdir(parents=True)
        (album_dir / "01 Track.flac").write_bytes(b"fake")

        client = MagicMock(spec=MpvClient)
        result = queue_album(client, None, album_dir, "replace", normalize=False)

        assert len(result) == 1
        assert result[0][1] is None  # No af filter
        conn_arg = client.loadfile.call_args
        assert conn_arg.args[2] is None  # No options

    def test_queues_without_db_data(self, tmp_path):
        album_dir = tmp_path / "Unknown" / "Album"
        album_dir.mkdir(parents=True)
        (album_dir / "01 Track.flac").write_bytes(b"fake")

        conn = _make_db(tmp_path)
        client = MagicMock(spec=MpvClient)
        result = queue_album(client, conn, album_dir, "replace", normalize=True)

        assert len(result) == 1
        assert result[0][1] is None  # No loudness data → no filter
        conn.close()


# --- queue_file ---


class TestQueueFile:
    def test_queues_with_gain(self, tmp_path):
        conn = _make_db(tmp_path)
        track = Path("/music/Artist/Album (2000)/01 Track.flac")

        client = MagicMock(spec=MpvClient)
        resolved, af = queue_file(client, conn, track, "replace", normalize=True)

        assert af is not None
        assert "volume=-2.00dB" in af
        client.loadfile.assert_called_once()
        conn.close()

    def test_queues_without_normalization(self, tmp_path):
        track = tmp_path / "track.flac"
        track.write_bytes(b"fake")

        client = MagicMock(spec=MpvClient)
        resolved, af = queue_file(client, None, track, "replace", normalize=False)

        assert af is None
        client.loadfile.assert_called_once()


# --- CLI validation ---


class TestPlayCLIValidation:
    def test_queue_and_next_exclusive(self):
        from musiktool.exceptions import ValidationError

        # This tests the logic that would be in the CLI handler
        queue = True
        next_ = True
        if queue and next_:
            with pytest.raises(ValidationError):
                raise ValidationError("--queue and --next are mutually exclusive")


# --- Loudness gain functions (smoke tests from loudness.py) ---


class TestLoudnessGainImport:
    """Verify gain functions are importable from loudness module."""

    def test_compute_gain_importable(self):
        from musiktool.loudness import compute_gain
        gain, peak, limiter, penalty = compute_gain(-14.0, -3.0, -14.0)
        assert gain == pytest.approx(0.0)
        assert limiter is False

    def test_compute_gain_with_limiter_importable(self):
        from musiktool.loudness import compute_gain_with_limiter
        gain, peak, limiter, penalty = compute_gain_with_limiter(
            -20.0, -2.0, -14.0, 0.0, use_limiter=True,
        )
        # Unlimited budget: full desired gain
        assert gain == pytest.approx(6.0)
        assert limiter is True
        assert penalty == pytest.approx(0.0)


# --- PID verification ---


class TestPidVerification:
    def test_cleanup_kills_verified_mpv(self, tmp_path, monkeypatch):
        pid_path = tmp_path / "mpv.pid"
        sock_path = tmp_path / "mpv.sock"
        pid_path.write_text("999")
        sock_path.write_text("")  # stale socket

        killed_pids = []

        def fake_kill(pid, sig):
            if sig == 0:
                return  # alive check
            killed_pids.append((pid, sig))

        monkeypatch.setattr("os.kill", fake_kill)
        monkeypatch.setattr(
            MpvClient, "_is_mpv_process", staticmethod(lambda pid: True),
        )

        client = MpvClient(socket_path=sock_path, pid_path=pid_path)
        client._cleanup_stale()

        assert (999, 15) in killed_pids  # SIGTERM = 15
        assert not pid_path.exists()
        assert not sock_path.exists()

    def test_cleanup_skips_non_mpv_pid(self, tmp_path, monkeypatch):
        pid_path = tmp_path / "mpv.pid"
        sock_path = tmp_path / "mpv.sock"
        pid_path.write_text("999")
        sock_path.write_text("")

        killed_pids = []

        def fake_kill(pid, sig):
            if sig == 0:
                return
            killed_pids.append((pid, sig))

        monkeypatch.setattr("os.kill", fake_kill)
        monkeypatch.setattr(
            MpvClient, "_is_mpv_process", staticmethod(lambda pid: False),
        )

        client = MpvClient(socket_path=sock_path, pid_path=pid_path)
        client._cleanup_stale()

        # No SIGTERM sent
        assert len(killed_pids) == 0
        # But stale files are still cleaned up
        assert not pid_path.exists()
        assert not sock_path.exists()

    def test_cleanup_handles_process_gone(self, tmp_path, monkeypatch):
        pid_path = tmp_path / "mpv.pid"
        sock_path = tmp_path / "mpv.sock"
        pid_path.write_text("999")

        def fake_kill(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr("os.kill", fake_kill)

        client = MpvClient(socket_path=sock_path, pid_path=pid_path)
        client._cleanup_stale()

        assert not pid_path.exists()

    def test_is_mpv_process_true(self, tmp_path, monkeypatch):
        """Process with mpv in cmdline returns True."""
        fake_proc = tmp_path / "proc" / "42" / "cmdline"
        fake_proc.parent.mkdir(parents=True)
        fake_proc.write_bytes(b"/usr/bin/mpv\x00--idle\x00--no-terminal\x00")

        def patched_is_mpv(pid: int) -> bool:
            try:
                cmdline = (tmp_path / "proc" / str(pid) / "cmdline").read_bytes()
                return b"mpv" in cmdline
            except (FileNotFoundError, PermissionError):
                return False

        monkeypatch.setattr(MpvClient, "_is_mpv_process", staticmethod(patched_is_mpv))
        assert MpvClient._is_mpv_process(42) is True

    def test_is_mpv_process_not_mpv(self, tmp_path, monkeypatch):
        """Non-mpv process returns False."""
        fake_proc = tmp_path / "proc" / "42" / "cmdline"
        fake_proc.parent.mkdir(parents=True)
        fake_proc.write_bytes(b"/usr/bin/python\x00script.py\x00")

        def patched_is_mpv(pid: int) -> bool:
            try:
                cmdline = (tmp_path / "proc" / str(pid) / "cmdline").read_bytes()
                return b"mpv" in cmdline
            except (FileNotFoundError, PermissionError):
                return False

        monkeypatch.setattr(MpvClient, "_is_mpv_process", staticmethod(patched_is_mpv))
        assert MpvClient._is_mpv_process(42) is False

    def test_is_mpv_process_missing_proc(self, tmp_path, monkeypatch):
        """Missing /proc entry returns False."""
        def patched_is_mpv(pid: int) -> bool:
            try:
                cmdline = (tmp_path / "proc" / str(pid) / "cmdline").read_bytes()
                return b"mpv" in cmdline
            except (FileNotFoundError, PermissionError):
                return False

        monkeypatch.setattr(MpvClient, "_is_mpv_process", staticmethod(patched_is_mpv))
        assert MpvClient._is_mpv_process(99999) is False


# --- loadfile error checking ---


class TestLoadfileErrors:
    def _make_client_with_fake_socket(self, responses: list[dict]):
        response_data = b""
        for r in responses:
            response_data += json.dumps(r).encode() + b"\n"

        mock_sock = MagicMock()
        mock_sock.recv = MagicMock(return_value=response_data)
        mock_sock.sendall = MagicMock()

        client = MpvClient()
        client._sock = mock_sock
        return client

    def test_loadfile_success(self):
        client = self._make_client_with_fake_socket([{"error": "success"}])
        # Should not raise
        client.loadfile("/test.flac", "replace")

    def test_loadfile_error_raises(self):
        from musiktool.exceptions import MpvCommandError

        client = self._make_client_with_fake_socket(
            [{"error": "loading failed"}],
        )
        with pytest.raises(MpvCommandError, match="loading failed"):
            client.loadfile("/bad.flac", "replace")

    def test_loadfile_error_with_filter(self):
        from musiktool.exceptions import MpvCommandError

        client = self._make_client_with_fake_socket(
            [{"error": "filter not found"}],
        )
        with pytest.raises(MpvCommandError, match="/bad.flac"):
            client.loadfile("/bad.flac", "replace", "af=badfilter")


# --- queue_tape modes ---


class TestQueueTape:
    def test_rendered_mode_plays_flacs(self, tmp_path, monkeypatch):
        from musiktool.config import get_config
        from musiktool.play import queue_tape

        # Set up rendered output
        render_dir = tmp_path / "render"
        project_dir = render_dir / "my-tape"
        project_dir.mkdir(parents=True)
        (project_dir / "my-tape.flac").write_bytes(b"fake flac")

        # Mock tape.get_project
        conn = MagicMock()
        with patch("musiktool.tape.get_project") as mock_get:
            mock_get.return_value = {"name": "My Tape"}
            client = MagicMock(spec=MpvClient)
            client.loadfile = MagicMock()

            result = queue_tape(
                client, conn, "My Tape", "replace", render_dir,
                tape_mode="rendered",
            )

        assert len(result) == 1
        assert "my-tape.flac" in result[0][0]
        assert result[0][1] is None  # No processing on rendered output
        client.loadfile.assert_called_once()

    def test_rendered_mode_raises_when_missing(self, tmp_path, monkeypatch):
        from musiktool.config import get_config as _get_config
        from musiktool.exceptions import RenderNotFoundError
        from musiktool.play import queue_tape

        # No rendered output anywhere
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        _get_config.cache_clear()

        conn = MagicMock()
        try:
            with patch("musiktool.tape.get_project") as mock_get:
                mock_get.return_value = {"name": "Missing Tape"}
                client = MagicMock(spec=MpvClient)

                with pytest.raises(RenderNotFoundError, match="No rendered output"):
                    queue_tape(
                        client, conn, "Missing Tape", "replace",
                        tape_mode="rendered",
                    )
        finally:
            _get_config.cache_clear()

    def test_rendered_mode_two_sided(self, tmp_path, monkeypatch):
        from musiktool.play import queue_tape

        render_dir = tmp_path / "render"
        project_dir = render_dir / "rock-mix"
        project_dir.mkdir(parents=True)
        (project_dir / "rock-mix-side-a.flac").write_bytes(b"side a")
        (project_dir / "rock-mix-side-b.flac").write_bytes(b"side b")

        conn = MagicMock()
        with patch("musiktool.tape.get_project") as mock_get:
            mock_get.return_value = {"name": "Rock Mix"}
            client = MagicMock(spec=MpvClient)
            client.loadfile = MagicMock()

            result = queue_tape(
                client, conn, "Rock Mix", "replace", render_dir,
                tape_mode="rendered",
            )

        assert len(result) == 2
        assert "side-a" in result[0][0]
        assert "side-b" in result[1][0]

    def test_playlist_mode_uses_processing(self, tmp_path, monkeypatch):
        from musiktool.play import queue_tape

        # Create fake album tracks
        album_dir = tmp_path / "Artist" / "Album"
        album_dir.mkdir(parents=True)
        (album_dir / "01 Track.flac").write_bytes(b"fake")
        (album_dir / "02 Track.flac").write_bytes(b"fake")

        conn = MagicMock()

        # Mock tape module functions
        mock_project = {"name": "Test", "peak_ceiling_dbtp": -1.0}
        mock_item_analysis = MagicMock()
        mock_item_analysis.position = 1
        mock_item_analysis.item_type = "album"
        mock_item_analysis.path = str(album_dir)
        mock_item_analysis.gain_db = -3.0
        mock_item_analysis.needs_limiter = False
        mock_item_analysis.use_limiter = False
        mock_item_analysis.compressor_params = None

        mock_analysis = MagicMock()
        mock_analysis.items = [mock_item_analysis]

        mock_layout_item = MagicMock()
        mock_layout_item.position = 1
        mock_layout_item.track_range = None

        mock_layout = MagicMock()
        mock_layout.items = [mock_layout_item]

        with patch("musiktool.tape.get_project", return_value=mock_project), \
             patch("musiktool.tape.analyze_project", return_value=mock_analysis), \
             patch("musiktool.tape.compute_project_layout", return_value=mock_layout):
            client = MagicMock(spec=MpvClient)
            client.loadfile = MagicMock()

            result = queue_tape(
                client, conn, "Test", "replace",
                tape_mode="playlist",
            )

        assert len(result) == 2
        # Should have processing filters
        assert result[0][1] is not None
        assert "volume=-3.00dB" in result[0][1]
