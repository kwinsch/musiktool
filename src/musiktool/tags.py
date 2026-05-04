"""Read and write audio file tags via mutagen."""

from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
from mutagen.id3 import TIT2, TPE1, TALB, TDRC, TRCK


@dataclass
class Tags:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    year: int | None = None
    track_number: int | None = None
    genre: str | None = None


def read_tags(path: str) -> Tags:
    """Read tags from any supported audio file."""
    f = mutagen.File(path, easy=True)
    if f is None:
        return Tags()

    tags = Tags()
    tags.title = _first(f.get("title"))
    tags.artist = _first(f.get("artist"))
    tags.album = _first(f.get("album"))
    tags.album_artist = _first(f.get("albumartist"))
    tags.genre = _first(f.get("genre"))

    date = _first(f.get("date"))
    if date:
        try:
            tags.year = int(date[:4])
        except (ValueError, TypeError):
            pass

    track = _first(f.get("tracknumber"))
    if track:
        try:
            tags.track_number = int(track.split("/")[0])
        except (ValueError, TypeError):
            pass

    return tags


def write_tags(path: str, tags: Tags) -> None:
    """Write tags to an audio file. Only writes non-None fields."""
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError(f"Cannot open file for tagging: {path}")

    if tags.title is not None:
        f["title"] = tags.title
    if tags.artist is not None:
        f["artist"] = tags.artist
    if tags.album is not None:
        f["album"] = tags.album
    if tags.album_artist is not None:
        f["albumartist"] = tags.album_artist
    if tags.year is not None:
        f["date"] = str(tags.year)
    if tags.track_number is not None:
        f["tracknumber"] = str(tags.track_number)
    if tags.genre is not None:
        f["genre"] = tags.genre

    f.save()


def _first(val) -> str | None:
    """Get first element from tag list or return None."""
    if val is None:
        return None
    if isinstance(val, list):
        return val[0] if val else None
    return str(val)
