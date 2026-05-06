"""iTunes XML catalog reading and import-plan helpers."""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from musiktool.exceptions import PathNotFoundError, ValidationError
from musiktool.library import SCHEMA_VERSION

OUTPUT_FORMATS = {"text", "json", "ndjson"}
ALBUM_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)$")
_UNSAFE_NAME_CHARS = re.compile(r"[/:]+")


@dataclass
class ItunesTrack:
    """One track entry from iTunes Music Library.xml."""

    track_id: int
    name: str | None
    artist: str | None
    album_artist: str | None
    album: str | None
    genre: str | None
    kind: str | None
    track_number: int | None
    disc_number: int | None
    year: int | None
    total_time_ms: int | None
    rating: int | None
    play_count: int
    skip_count: int
    date_added: str | None
    location: str | None
    path: Path | None
    exists: bool
    media_kind: str

    @property
    def protected(self) -> bool:
        kind = (self.kind or "").casefold()
        suffix = self.path.suffix.casefold() if self.path is not None else ""
        return "protected" in kind or suffix == ".m4p"

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "name": self.name,
            "artist": self.artist,
            "album_artist": self.album_artist,
            "album": self.album,
            "genre": self.genre,
            "kind": self.kind,
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "year": self.year,
            "total_time_ms": self.total_time_ms,
            "rating": self.rating,
            "play_count": self.play_count,
            "skip_count": self.skip_count,
            "date_added": self.date_added,
            "location": self.location,
            "path": str(self.path) if self.path is not None else None,
            "exists": self.exists,
            "media_kind": self.media_kind,
            "protected": self.protected,
        }


@dataclass
class ItunesAlbum:
    """Album-level grouping for iTunes music tracks."""

    artist: str
    album: str
    year: int | None
    media_kind: str
    tracks: list[ItunesTrack] = field(default_factory=list)

    @property
    def existing_tracks(self) -> int:
        return sum(1 for track in self.tracks if track.exists)

    @property
    def missing_tracks(self) -> int:
        return len(self.tracks) - self.existing_tracks

    @property
    def play_count(self) -> int:
        return sum(track.play_count for track in self.tracks)

    @property
    def kinds(self) -> list[str]:
        return sorted({track.kind or "unknown" for track in self.tracks})

    def to_dict(self) -> dict[str, Any]:
        return {
            "artist": self.artist,
            "album": self.album,
            "year": self.year,
            "media_kind": self.media_kind,
            "tracks": len(self.tracks),
            "existing_tracks": self.existing_tracks,
            "missing_tracks": self.missing_tracks,
            "play_count": self.play_count,
            "kinds": self.kinds,
            "track_ids": [track.track_id for track in self.tracks],
        }


@dataclass
class ItunesCatalog:
    """Parsed iTunes catalog."""

    root: Path
    xml_path: Path
    tracks: list[ItunesTrack]

    @property
    def albums(self) -> list[ItunesAlbum]:
        return group_albums(self.tracks)

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        with_location = 0
        existing = 0
        for track in self.tracks:
            by_kind[track.media_kind] = by_kind.get(track.media_kind, 0) + 1
            if track.location:
                with_location += 1
            if track.exists:
                existing += 1
        music_albums = [
            album for album in self.albums
            if album.media_kind == "music"
        ]
        return {
            "tracks": len(self.tracks),
            "tracks_with_location": with_location,
            "tracks_existing": existing,
            "tracks_missing": with_location - existing,
            "media_kinds": by_kind,
            "music_albums": len(music_albums),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "command": "itunes.scan",
            "root": str(self.root),
            "xml_path": str(self.xml_path),
            "summary": self.summary(),
        }


def load_catalog(root: Path, *, xml_path: Path | None = None) -> ItunesCatalog:
    """Load an iTunes Music Library XML file below *root*."""
    root = root.resolve()
    if not root.exists():
        raise PathNotFoundError(f"path does not exist: {root}")
    xml_path = (xml_path or find_xml(root)).resolve()
    if not xml_path.exists():
        raise PathNotFoundError(f"iTunes XML does not exist: {xml_path}")

    with xml_path.open("rb") as handle:
        document = plistlib.load(handle)
    raw_tracks = document.get("Tracks", {})
    if not isinstance(raw_tracks, dict):
        raise ValidationError(f"invalid iTunes XML: missing Tracks dictionary: {xml_path}")

    tracks = [
        _track_from_dict(raw, root=root)
        for raw in raw_tracks.values()
        if isinstance(raw, dict)
    ]
    tracks.sort(key=lambda track: (track.artist or "", track.album or "", track.track_number or 0, track.name or ""))
    return ItunesCatalog(root=root, xml_path=xml_path, tracks=tracks)


def find_xml(root: Path) -> Path:
    """Return the conventional XML library path below an iTunes root."""
    xml_path = root / "iTunes Music Library.xml"
    if xml_path.exists():
        return xml_path
    candidates = sorted(root.glob("*.xml"))
    if candidates:
        return candidates[0]
    return xml_path


def group_albums(
    tracks: list[ItunesTrack],
    *,
    media_kind: str | None = None,
) -> list[ItunesAlbum]:
    """Group tracks into albums by media kind, album artist, album, and year."""
    groups: dict[tuple[str, str, int | None, str], ItunesAlbum] = {}
    for track in tracks:
        if media_kind is not None and track.media_kind != media_kind:
            continue
        artist = track.album_artist or track.artist or "Unknown Artist"
        album = track.album or "Unknown Album"
        key = (artist, album, track.year, track.media_kind)
        if key not in groups:
            groups[key] = ItunesAlbum(
                artist=artist,
                album=album,
                year=track.year,
                media_kind=track.media_kind,
            )
        groups[key].tracks.append(track)

    albums = list(groups.values())
    for album in albums:
        album.tracks.sort(key=lambda track: (
            track.disc_number or 0,
            track.track_number or 0,
            track.name or "",
        ))
    return sorted(albums, key=lambda album: (
        album.artist.lower(),
        album.album.lower(),
        album.year or 0,
    ))


def filter_albums(
    catalog: ItunesCatalog,
    *,
    artist: str | None = None,
    album: str | None = None,
    media_kind: str = "music",
    limit: int | None = None,
) -> list[ItunesAlbum]:
    """Return album groups matching optional case-insensitive filters."""
    albums = group_albums(catalog.tracks, media_kind=media_kind)
    if artist:
        needle = artist.casefold()
        albums = [item for item in albums if needle in item.artist.casefold()]
    if album:
        needle = album.casefold()
        albums = [item for item in albums if needle in item.album.casefold()]
    if limit is not None:
        albums = albums[:limit]
    return albums


def build_import_plan(
    catalog: ItunesCatalog,
    *,
    target: Path,
    artist: str | None = None,
    album: str | None = None,
    media_kind: str = "music",
    limit: int | None = None,
    include_protected: bool = False,
) -> dict[str, Any]:
    """Build a dry-runable copy plan for selected iTunes tracks."""
    target = target.resolve(strict=False)
    if not target.exists():
        raise PathNotFoundError(f"target library does not exist: {target}")
    if artist is None and album is None:
        raise ValidationError("itunes propose requires --artist or --album")

    selected = filter_albums(
        catalog,
        artist=artist,
        album=album,
        media_kind=media_kind,
        limit=limit,
    )
    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    planned_destinations: set[Path] = set()
    for album_group in selected:
        for track in album_group.tracks:
            if track.path is None:
                skipped.append({
                    "track_id": track.track_id,
                    "reason": "missing_location",
                })
                continue
            if not track.exists:
                skipped.append({
                    "track_id": track.track_id,
                    "path": str(track.path),
                    "reason": "source_missing",
                })
                continue
            if track.protected and not include_protected:
                skipped.append({
                    "track_id": track.track_id,
                    "path": str(track.path),
                    "reason": "protected_file",
                    "kind": track.kind,
                })
                continue
            destination = target / _target_relative_path(album_group, track)
            if destination in planned_destinations or destination.exists():
                skipped.append({
                    "track_id": track.track_id,
                    "path": str(track.path),
                    "destination": str(destination),
                    "reason": "destination_exists",
                })
                continue
            planned_destinations.add(destination)
            actions.append({
                "action_id": f"copy_file:{len(actions) + 1}",
                "type": "copy_file",
                "because_finding": "itunes.import_candidate",
                "source": str(track.path),
                "destination": str(destination),
                "reason": f"Copy from iTunes: {album_group.artist} - {album_group.album}.",
                "track_id": track.track_id,
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "library_root": str(target),
        "source_roots": [str(catalog.root)],
        "generated_by": "musiktool itunes propose",
        "selection": {
            "artist": artist,
            "album": album,
            "media_kind": media_kind,
            "limit": limit,
            "include_protected": include_protected,
        },
        "actions": actions,
        "skipped": skipped,
    }


def format_scan(catalog: ItunesCatalog, output_format: str = "text") -> str:
    _validate_output_format(output_format)
    if output_format == "json":
        return _json(catalog.to_dict())
    if output_format == "ndjson":
        return _json_line(catalog.to_dict()) + "\n"

    summary = catalog.summary()
    lines = [
        str(catalog.root),
        f"  XML:              {catalog.xml_path.name}",
        f"  Tracks:           {summary['tracks']}",
        f"  Existing files:   {summary['tracks_existing']} / {summary['tracks_with_location']}",
        f"  Missing files:    {summary['tracks_missing']}",
        f"  Music albums:     {summary['music_albums']}",
        "",
        "  Media kinds:",
    ]
    for kind, count in sorted(summary["media_kinds"].items()):
        lines.append(f"    {kind:<10s} {count:>6d}")
    return "\n".join(lines) + "\n"


def format_albums(
    albums: list[ItunesAlbum],
    *,
    output_format: str = "text",
) -> str:
    _validate_output_format(output_format)
    if output_format == "json":
        return _json({
            "schema_version": SCHEMA_VERSION,
            "command": "itunes.albums",
            "albums": [album.to_dict() for album in albums],
            "summary": {"albums": len(albums)},
        })
    if output_format == "ndjson":
        return "".join(
            _json_line({
                "schema_version": SCHEMA_VERSION,
                "command": "itunes.albums",
                "type": "album",
                **album.to_dict(),
            }) + "\n"
            for album in albums
        )

    lines = [f"{len(albums)} album(s)", ""]
    for album in albums:
        year = f" ({album.year})" if album.year else ""
        missing = f", {album.missing_tracks} missing" if album.missing_tracks else ""
        kinds = "/".join(album.kinds)
        lines.append(
            f"{album.artist} - {album.album}{year}: "
            f"{len(album.tracks)} tracks ({album.existing_tracks} files{missing}), "
            f"plays {album.play_count}, {kinds}"
        )
    return "\n".join(lines) + "\n"


def format_plan(plan: dict[str, Any], output_format: str = "text") -> str:
    _validate_output_format(output_format)
    if output_format == "json":
        return _json(plan)
    if output_format == "ndjson":
        lines = [
            _json_line({
                "schema_version": SCHEMA_VERSION,
                "command": "itunes.propose",
                "type": "summary",
                "library_root": plan["library_root"],
                "selection": plan.get("selection", {}),
                "summary": {
                    "actions": len(plan.get("actions", [])),
                    "skipped": len(plan.get("skipped", [])),
                },
            })
        ]
        lines.extend(
            _json_line({"type": "action", **action})
            for action in plan.get("actions", [])
        )
        return "\n".join(lines) + "\n"

    lines = [
        "iTunes import proposal",
        f"  Target:  {plan['library_root']}",
        f"  Actions: {len(plan.get('actions', []))}",
        f"  Skipped: {len(plan.get('skipped', []))}",
        "",
    ]
    for action in plan.get("actions", [])[:40]:
        lines.append(f"  {action['source']} -> {action['destination']}")
    more = len(plan.get("actions", [])) - 40
    if more > 0:
        lines.append(f"  ... and {more} more")
    return "\n".join(lines) + "\n"


def _track_from_dict(raw: dict[str, Any], *, root: Path) -> ItunesTrack:
    location = _optional_str(raw.get("Location"))
    path = _resolve_location(location, root) if location else None
    media_kind = _media_kind(raw, path)
    return ItunesTrack(
        track_id=_int_or_zero(raw.get("Track ID")),
        name=_optional_str(raw.get("Name")),
        artist=_optional_str(raw.get("Artist")),
        album_artist=_optional_str(raw.get("Album Artist")),
        album=_optional_str(raw.get("Album")),
        genre=_optional_str(raw.get("Genre")),
        kind=_optional_str(raw.get("Kind")),
        track_number=_optional_int(raw.get("Track Number")),
        disc_number=_optional_int(raw.get("Disc Number")),
        year=_optional_int(raw.get("Year")),
        total_time_ms=_optional_int(raw.get("Total Time")),
        rating=_optional_int(raw.get("Rating")),
        play_count=_int_or_zero(raw.get("Play Count")),
        skip_count=_int_or_zero(raw.get("Skip Count")),
        date_added=_date_to_iso(raw.get("Date Added")),
        location=location,
        path=path,
        exists=path.exists() if path is not None else False,
        media_kind=media_kind,
    )


def _resolve_location(location: str, root: Path) -> Path:
    parsed = urlparse(location)
    raw_path = unquote(parsed.path if parsed.scheme else location)
    candidate = Path(raw_path).resolve(strict=False)
    if candidate.exists():
        return candidate

    parts = candidate.parts
    for marker in ("iTunes Music", "iTunes Media"):
        if marker in parts:
            idx = parts.index(marker)
            return (root / Path(*parts[idx:])).resolve(strict=False)
    return candidate


def _media_kind(raw: dict[str, Any], path: Path | None) -> str:
    kind = str(raw.get("Kind", "")).casefold()
    genre = str(raw.get("Genre", "")).casefold()
    path_parts = {part.casefold() for part in path.parts} if path is not None else set()
    if raw.get("Podcast") or "podcast" in kind or "podcasts" in path_parts:
        return "podcast"
    if raw.get("Movie") or "movie" in kind or "video" in kind or "movies" in path_parts:
        return "movie"
    if "pdf" in kind or "book" in kind or "books" in path_parts:
        return "book"
    if "audio" in kind or "mpeg" in kind or "aac" in kind or "mp3" in kind:
        if genre in {"podcast", "audiobook", "audiobooks", "books & spoken"}:
            return "podcast" if genre == "podcast" else "audiobook"
        return "music"
    return "other"


def _target_relative_path(album: ItunesAlbum, track: ItunesTrack) -> Path:
    artist = _safe_name(album.artist, fallback="Unknown Artist")
    album_name = _safe_name(album.album, fallback="Unknown Album")
    if album.year and not ALBUM_YEAR_RE.search(album_name):
        album_name = f"{album_name} ({album.year})"
    title = _safe_name(track.name or (track.path.stem if track.path else "Unknown Track"), fallback="Unknown Track")
    suffix = track.path.suffix.lower() if track.path is not None else ".m4a"
    if track.track_number is not None:
        filename = f"{track.track_number:02d} {title}{suffix}"
    else:
        filename = f"{title}{suffix}"
    return Path("music") / artist / album_name / filename


def _safe_name(value: str, *, fallback: str) -> str:
    cleaned = _UNSAFE_NAME_CHARS.sub("-", value).strip()
    cleaned = cleaned.rstrip(". ")
    return cleaned or fallback


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


def _date_to_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return _optional_str(value)


def _validate_output_format(output_format: str) -> None:
    if output_format not in OUTPUT_FORMATS:
        raise ValidationError(f"unknown output format: {output_format}")


def _json(data: Any) -> str:
    import json

    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _json_line(data: Any) -> str:
    import json

    return json.dumps(data, sort_keys=True)
