"""AcoustID fingerprinting and lookup."""

import json
import os
import subprocess
import time
from dataclasses import dataclass

import httpx

_last_request_time: float = 0.0

ACOUSTID_API = "https://api.acoustid.org/v2/lookup"

# Public API key (for open-source/personal use).
# Can be overridden via ACOUSTID_API_KEY environment variable.
# The default key is a well-known public key used by many open-source tools.
ACOUSTID_API_KEY = os.environ.get("ACOUSTID_API_KEY", "1vOwZtEn")


@dataclass
class TrackInfo:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    year: int | None = None
    track_number: int | None = None
    musicbrainz_id: str | None = None
    score: float = 0.0


def fingerprint(path: str) -> tuple[int, str]:
    """Run fpcalc and return (duration, fingerprint)."""
    result = subprocess.run(
        ["fpcalc", "-json", path],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data["duration"], data["fingerprint"]


def lookup(duration: int, fp: str) -> list[TrackInfo]:
    """Query AcoustID API and return possible matches."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.34:
        time.sleep(0.34 - elapsed)
    _last_request_time = time.time()

    resp = httpx.get(
        ACOUSTID_API,
        params={
            "client": ACOUSTID_API_KEY,
            "duration": str(int(duration)),
            "fingerprint": fp,
            "meta": "recordings releasegroups",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "ok":
        return []

    results = []
    for result in data.get("results", []):
        score = result.get("score", 0)
        for recording in result.get("recordings", []):
            artists = recording.get("artists", [])
            artist_name = artists[0].get("name") if artists else None

            # Create one TrackInfo per release group (album)
            release_groups = recording.get("releasegroups", [])
            for rg in release_groups:
                info = TrackInfo(score=score)
                info.title = recording.get("title")
                info.musicbrainz_id = recording.get("id")
                info.artist = artist_name
                info.album = rg.get("title")
                releases = rg.get("releases", [])
                if releases:
                    date = releases[0].get("date", {})
                    if isinstance(date, dict):
                        info.year = date.get("year")
                    elif isinstance(date, str) and len(date) >= 4:
                        try:
                            info.year = int(date[:4])
                        except ValueError:
                            pass
                results.append(info)

            # Also add without album if no release groups
            if not release_groups:
                info = TrackInfo(score=score)
                info.title = recording.get("title")
                info.musicbrainz_id = recording.get("id")
                info.artist = artist_name
                results.append(info)

    return results


def identify(path: str) -> list[TrackInfo]:
    """Fingerprint a file and return lookup results (top 5 unique by title+artist)."""
    duration, fp = fingerprint(path)
    results = lookup(duration, fp)
    # Deduplicate by title+artist for single-track display
    seen = set()
    unique = []
    for r in results:
        key = (r.title, r.artist)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:5]


@dataclass
class AlbumMatch:
    album: str
    artist: str | None
    year: int | None
    track_hits: int
    total_tracks: int
    confidence: float


def identify_album(paths: list[str]) -> list[AlbumMatch]:
    """Identify the most likely album from a list of tracks.

    Fingerprints all tracks, collects all album candidates,
    and ranks by how many tracks matched to each album.
    """
    from collections import Counter

    album_votes: Counter[tuple[str, str | None]] = Counter()
    album_years: dict[tuple[str, str | None], int | None] = {}
    total = len(paths)

    for path in paths:
        try:
            duration, fp = fingerprint(path)
            results = lookup(duration, fp)
        except Exception:
            continue

        # Each track votes for all albums it matched to
        seen_albums = set()
        for r in results:
            if r.album and r.score >= 0.5:
                key = (r.album, r.artist)
                if key not in seen_albums:
                    seen_albums.add(key)
                    album_votes[key] += 1
                    if r.year and key not in album_years:
                        album_years[key] = r.year

    # Rank by number of track hits
    matches = []
    for (album, artist), hits in album_votes.most_common(10):
        matches.append(AlbumMatch(
            album=album,
            artist=artist,
            year=album_years.get((album, artist)),
            track_hits=hits,
            total_tracks=total,
            confidence=hits / total,
        ))

    return matches
