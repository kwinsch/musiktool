"""Agent-facing library audit, inspection, and fix-plan execution."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

import mutagen

from musiktool import db
from musiktool.constants import AUDIO_EXTENSIONS
from musiktool.exceptions import PathNotFoundError, ValidationError
from musiktool.ignore import IgnorePolicy, load_ignore_policy
from musiktool.tags import Tags, read_tags, write_tags

SCHEMA_VERSION = 1

OUTPUT_FORMATS = {"text", "json", "ndjson"}
SEVERITIES = {"info": 0, "warning": 1, "error": 2}
_SEVERITY_ICONS = {"error": "\u2717", "warning": "\u26a0", "info": "\u2139"}
FIX_PLAN_ACTIONS = {
    "copy_album_dir",
    "copy_file",
    "ignore_finding",
    "move_file",
    "quarantine",
    "rename_album_dir",
    "rename_track_file",
    "write_tags",
}

TRACK_FILENAME_RE = re.compile(r"^(?P<number>\d{2})\s+(?P<title>.+)\.[^.]+$")
ALBUM_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)$")

# ---------------------------------------------------------------------------
# Media profiles
# ---------------------------------------------------------------------------

_TAG_FIELD_MAP: dict[str, str] = {
    "artist": "artist",
    "album": "album",
    "title": "title",
    "tracknumber": "track_number",
    "date": "year",
}

_MUSIC_REQUIRED: tuple[str, ...] = ("artist", "album", "title", "tracknumber", "date")


@dataclass(frozen=True)
class MediaProfile:
    """Per-media-kind audit rules."""

    name: str
    required_tags: tuple[str, ...]
    filename_re: re.Pattern[str]
    filename_description: str
    check_year_structure: bool
    check_provenance: bool
    check_track_number_match: bool


_SPOKEN_FILENAME_RE = re.compile(r"^(?P<number>\d{2,3})\s+(?P<title>.+)\.[^.]+$")
_PODCAST_FILENAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<title>.+)\.[^.]+$",
)

MEDIA_PROFILES: dict[str, MediaProfile] = {
    "music": MediaProfile(
        name="music",
        required_tags=_MUSIC_REQUIRED,
        filename_re=TRACK_FILENAME_RE,
        filename_description="NN Title.ext",
        check_year_structure=True,
        check_provenance=True,
        check_track_number_match=True,
    ),
    "radio": MediaProfile(
        name="radio",
        required_tags=("artist", "title"),
        filename_re=_SPOKEN_FILENAME_RE,
        filename_description="NN(N) Title.ext",
        check_year_structure=False,
        check_provenance=False,
        check_track_number_match=False,
    ),
    "audiobook": MediaProfile(
        name="audiobook",
        required_tags=("artist", "title"),
        filename_re=_SPOKEN_FILENAME_RE,
        filename_description="NN(N) Title.ext",
        check_year_structure=True,
        check_provenance=False,
        check_track_number_match=False,
    ),
    "podcast": MediaProfile(
        name="podcast",
        required_tags=("artist", "title", "date"),
        filename_re=_PODCAST_FILENAME_RE,
        filename_description="YYYY-MM-DD Title.ext",
        check_year_structure=False,
        check_provenance=False,
        check_track_number_match=False,
    ),
}

PROFILE_NAMES: frozenset[str] = frozenset((*MEDIA_PROFILES, "auto"))

MEDIA_KIND_SUBTREES: dict[str, str] = {
    "music": "music",
    "radio": "radio",
    "audiobook": "audiobooks",
    "podcast": "podcasts",
}
_MEDIA_KIND_MIN_CONFIDENCE = 0.9

AUXILIARY_SUFFIXES = {
    ".cue",
    ".jpg",
    ".jpeg",
    ".log",
    ".m3u",
    ".m3u8",
    ".nfo",
    ".png",
    ".txt",
}

FORMAT_SCORES = {
    ".flac": 100,
    ".wav": 95,
    ".ape": 90,
    ".m4a": 60,
    ".ogg": 55,
    ".mp3": 50,
    ".wma": 35,
}


@dataclass
class Finding:
    """A machine-readable audit finding."""

    finding_id: str
    severity: str
    category: str
    paths: list[str]
    message: str
    evidence: dict[str, Any]
    confidence: float
    fixable: bool
    suggested_actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "category": self.category,
            "paths": self.paths,
            "message": self.message,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "fixable": self.fixable,
            "suggested_actions": self.suggested_actions,
        }


@dataclass
class AuditResult:
    """Full audit output document."""

    root: str
    generated_at: str
    summary: dict[str, Any]
    findings: list[Finding]
    checks: list[dict[str, Any]] = field(default_factory=list)
    album_profiles: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": SCHEMA_VERSION,
            "command": "audit",
            "root": self.root,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
        }
        if self.checks:
            result["checks"] = self.checks
        return result


@dataclass
class InspectResult:
    """Detailed evidence for one library path."""

    path: str
    generated_at: str
    kind: str
    metadata: dict[str, Any]
    findings: list[Finding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "command": "inspect",
            "path": self.path,
            "generated_at": self.generated_at,
            "kind": self.kind,
            "metadata": self.metadata,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class ApplyActionResult:
    """Validation/execution result for one fix-plan action."""

    action_id: str
    type: str
    status: str
    source: str | None = None
    destination: str | None = None
    reason: str | None = None
    db_updates: dict[str, int] | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "action_id": self.action_id,
            "type": self.type,
            "status": self.status,
        }
        if self.source is not None:
            result["source"] = self.source
        if self.destination is not None:
            result["destination"] = self.destination
        if self.reason is not None:
            result["reason"] = self.reason
        if self.db_updates:
            result["db_updates"] = self.db_updates
        if self.warnings:
            result["warnings"] = self.warnings
        return result


@dataclass
class ApplyResult:
    """Fix-plan validation/execution output document."""

    plan_path: str
    dry_run: bool
    executed: bool
    valid: bool
    actions: list[ApplyActionResult]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "command": "apply",
            "plan_path": self.plan_path,
            "dry_run": self.dry_run,
            "executed": self.executed,
            "valid": self.valid,
            "actions": [a.to_dict() for a in self.actions],
            "errors": self.errors,
        }


@dataclass
class ProposeResult:
    """A generated fix plan from mechanical analysis."""

    library_root: str
    generated_at: str
    generated_by: str
    proposal_type: str
    actions: list[dict[str, Any]]
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_plan(self) -> dict[str, Any]:
        """Return valid fix plan JSON, pipeable to apply."""
        return {
            "schema_version": SCHEMA_VERSION,
            "library_root": self.library_root,
            "generated_by": self.generated_by,
            "actions": self.actions,
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.to_plan()
        result["command"] = "propose"
        result["generated_at"] = self.generated_at
        result["proposal_type"] = self.proposal_type
        result["skipped"] = self.skipped
        result["summary"] = {
            "proposed": len(self.actions),
            "skipped": len(self.skipped),
        }
        return result


@dataclass
class StatsResult:
    """Library statistics overview."""

    root: str
    generated_at: str
    albums: int
    tracks: int
    total_files: int
    audio_files: int
    non_audio_files: int
    total_bytes: int
    audio_bytes: int
    non_audio_bytes: int
    format_histogram: list[dict[str, Any]]
    asset_histogram: list[dict[str, Any]]
    provenance: dict[str, int]
    # DB-enriched fields (None when no index DB available):
    tag_coverage: dict[str, dict[str, int]] | None = None
    codec_histogram: list[dict[str, Any]] | None = None
    classifications: dict[str, int] | None = None
    index_coverage: dict[str, int] | None = None
    loudness_coverage: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "command": "stats",
            "root": self.root,
            "generated_at": self.generated_at,
            "albums": self.albums,
            "tracks": self.tracks,
            "total_files": self.total_files,
            "audio_files": self.audio_files,
            "non_audio_files": self.non_audio_files,
            "total_bytes": self.total_bytes,
            "audio_bytes": self.audio_bytes,
            "non_audio_bytes": self.non_audio_bytes,
            "format_histogram": self.format_histogram,
            "asset_histogram": self.asset_histogram,
            "provenance": self.provenance,
        }
        if self.tag_coverage is not None:
            result["tag_coverage"] = self.tag_coverage
        if self.codec_histogram is not None:
            result["codec_histogram"] = self.codec_histogram
        if self.classifications is not None:
            result["classifications"] = self.classifications
        if self.index_coverage is not None:
            result["index_coverage"] = self.index_coverage
        if self.loudness_coverage is not None:
            result["loudness_coverage"] = self.loudness_coverage
        return result


@dataclass
class _IndexedTrackFacts:
    path: Path
    current: bool
    source: str
    indexed: sqlite3.Row | None
    audio: sqlite3.Row | None
    tags: sqlite3.Row | None
    classification: sqlite3.Row | None


@dataclass
class _AlbumDuplicateProfile:
    path: Path
    tracks: list[Path]
    titles: list[str]
    durations: list[float | None]
    blake3: list[str | None]
    chromaprints: list[str | None]
    artist: str
    album: str
    path_artist: str
    path_album: str


class _AuditIndex:
    """Read-through view over indexed facts for one audit run."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._facts: dict[str, _IndexedTrackFacts] = {}

    def facts(self, path: Path) -> _IndexedTrackFacts:
        resolved = path.resolve()
        path_str = str(resolved)
        cached = self._facts.get(path_str)
        if cached is not None:
            return cached

        indexed = db.get_indexed_file(self.conn, path_str)
        audio = None
        tags = None
        classification = None
        current = False
        source = "live"
        if indexed is not None:
            stat = resolved.stat()
            current = db.indexed_file_unchanged(
                indexed,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
            if current:
                audio = db.get_audio_facts(self.conn, path_str)
                tags = db.get_tag_facts(self.conn, path_str)
                classification = db.get_effective_library_classification(
                    self.conn, path_str,
                )
                if audio is not None and tags is not None:
                    source = "indexed"
                elif audio is not None or tags is not None:
                    source = "partial"
            else:
                source = "stale"

        facts = _IndexedTrackFacts(
            path=resolved,
            current=current,
            source=source,
            indexed=indexed,
            audio=audio,
            tags=tags,
            classification=classification,
        )
        self._facts[path_str] = facts
        return facts

    def tags(self, path: Path) -> Tags:
        facts = self.facts(path)
        if facts.current and facts.tags is not None:
            return _tags_from_index_row(facts.tags)
        return _safe_read_tags_cached(str(path.resolve()))

    def duration(self, path: Path) -> float | None:
        facts = self.facts(path)
        if facts.current and facts.audio is not None:
            duration = facts.audio["duration_sec"]
            if duration is not None:
                return round(float(duration), 3)
        return _duration_cached(str(path.resolve()))

    def source(self, path: Path) -> str:
        return self.facts(path).source

    def source_counts(self) -> dict[str, int]:
        counts = {"indexed": 0, "partial": 0, "live": 0, "stale": 0}
        for facts in self._facts.values():
            counts[facts.source] += 1
        return counts


def _resolve_album_profile(
    album_dir: Path,
    profile: str,
    audit_index: _AuditIndex | None,
    ignore_policy: IgnorePolicy | None,
) -> MediaProfile:
    """Return the effective MediaProfile for *album_dir*."""
    if profile != "auto":
        return MEDIA_PROFILES[profile]
    if audit_index is None:
        return MEDIA_PROFILES["music"]
    classification = _resolve_album_classification(
        album_dir,
        audit_index.conn,
        ignore_policy,
        audit_index=audit_index,
    )
    if classification is not None:
        kind = classification["media_kind"]
        if kind in MEDIA_PROFILES:
            return MEDIA_PROFILES[kind]
    return MEDIA_PROFILES["music"]


def _resolve_album_classification(
    album_dir: Path,
    conn: sqlite3.Connection,
    ignore_policy: IgnorePolicy | None,
    *,
    audit_index: _AuditIndex | None = None,
) -> sqlite3.Row | None:
    """Return directory classification, falling back to first indexed track."""
    classification = db.get_effective_library_classification(conn, str(album_dir))
    if classification is not None:
        return classification

    if audit_index is None:
        audit_index = _AuditIndex(conn)
    for track in _direct_audio_files(album_dir, ignore_policy):
        facts = audit_index.facts(track)
        if facts.classification is not None:
            return facts.classification
        break  # One current indexed track is enough to resolve album profile.
    return None


def audit_library(
    path: Path,
    *,
    against: Path | None = None,
    min_severity: str = "info",
    include_ok: bool = False,
    index_conn: sqlite3.Connection | None = None,
    excludes: list[str] | None = None,
    similarity_threshold: float = 0.08,
    profile: str = "auto",
) -> AuditResult:
    """Audit a library or staging path without modifying files."""
    assert profile in PROFILE_NAMES, f"unknown profile: {profile}"
    _validate_severity(min_severity)
    root = path.resolve()
    if not root.exists():
        raise PathNotFoundError(f"path does not exist: {root}")

    findings: list[Finding] = []
    checks: list[dict[str, Any]] = []
    threshold = SEVERITIES[min_severity]
    include_info_findings = threshold <= SEVERITIES["info"]
    include_warning_findings = threshold <= SEVERITIES["warning"]
    audit_index = _AuditIndex(index_conn) if index_conn is not None else None
    ignore_policy = load_ignore_policy(root, excludes=excludes)

    album_dirs = _discover_album_dirs(root, ignore_policy)
    audio_files = _audio_files_under(root, ignore_policy)
    if audit_index is not None:
        for audio_file in audio_files:
            audit_index.facts(audio_file)
    direct_root_files = [
        p for p in _direct_files(root, ignore_policy) if not _is_audio(p)
    ]

    if include_info_findings:
        for file_path in direct_root_files:
            if not _is_auxiliary(file_path):
                findings.append(_finding(
                    "structure.stray_file",
                    "info",
                    [file_path],
                    "Non-audio file in an unusual location.",
                    {
                        "name": file_path.name,
                        "size_bytes": file_path.stat().st_size,
                        "location": "root",
                    },
                    confidence=1.0,
                    fixable=True,
                    suggested_actions=[{
                        "type": "quarantine",
                        "source": str(file_path),
                        "reason": "File is not audio or recognized provenance.",
                    }],
                ))

    album_set = set(album_dirs)
    profiles_used: dict[str, int] = {}
    album_profiles: dict[str, str] = {}
    for album_dir in album_dirs:
        rel_parts = _relative_parts(album_dir, root)
        album_profile = _resolve_album_profile(
            album_dir, profile, audit_index, ignore_policy,
        )
        profiles_used[album_profile.name] = profiles_used.get(album_profile.name, 0) + 1
        album_profiles[str(album_dir)] = album_profile.name
        if include_warning_findings:
            _audit_album_structure(
                root, album_dir, rel_parts, findings,
                audit_index=audit_index,
                ignore_policy=ignore_policy,
                profile=album_profile,
            )
        if include_info_findings:
            _audit_album_provenance(album_dir, findings, profile=album_profile)
        if include_warning_findings or include_info_findings:
            _audit_tracks(
                album_dir,
                findings,
                include_warnings=include_warning_findings,
                include_info=include_info_findings,
                audit_index=audit_index,
                ignore_policy=ignore_policy,
                profile=album_profile,
            )

        if include_warning_findings:
            parent_album = _nearest_parent_album(album_dir, album_set)
            if parent_album is not None:
                findings.append(_nested_album_finding(
                    parent_album,
                    album_dir,
                    audit_index=audit_index,
                    ignore_policy=ignore_policy,
                ))

    if include_warning_findings or include_info_findings:
        findings.extend(_duplicate_findings(
            album_dirs,
            "duplicates.same_album_candidate",
            audit_index=audit_index,
            ignore_policy=ignore_policy,
            include_info=include_info_findings,
            similarity_threshold=similarity_threshold,
        ))

    if against is not None and include_warning_findings:
        against_root = against.resolve()
        if not against_root.exists():
            raise PathNotFoundError(f"--against path does not exist: {against_root}")
        against_ignore_policy = load_ignore_policy(against_root, excludes=excludes)
        findings.extend(_against_findings(
            album_dirs,
            _discover_album_dirs(against_root, against_ignore_policy),
            audit_index=audit_index,
            source_ignore_policy=ignore_policy,
            curated_ignore_policy=against_ignore_policy,
        ))

    filtered = _filter_findings(findings, min_severity)
    by_severity = {name: 0 for name in SEVERITIES}
    for finding in filtered:
        by_severity[finding.severity] += 1

    if include_ok:
        checks.append({
            "category": "scan.completed",
            "status": "ok",
            "albums_scanned": len(album_dirs),
            "tracks_scanned": len(audio_files),
        })
        if audit_index is not None:
            checks.append({
                "category": "index.facts",
                "status": "ok",
                "sources": audit_index.source_counts(),
            })

    summary: dict[str, Any] = {
        "albums_scanned": len(album_dirs),
        "tracks_scanned": len(audio_files),
        "findings": len(filtered),
        "by_severity": by_severity,
        "profiles_used": profiles_used,
    }
    if audit_index is not None:
        summary["index"] = {
            "fact_sources": audit_index.source_counts(),
        }

    return AuditResult(
        root=str(root),
        generated_at=_now(),
        summary=summary,
        findings=filtered,
        checks=checks,
        album_profiles=album_profiles,
    )


def inspect_path(
    path: Path,
    *,
    against: Path | None = None,
    min_severity: str = "info",
) -> InspectResult:
    """Inspect one existing filesystem path and include local audit findings."""
    target = path.resolve()
    if not target.exists():
        if ":" in str(path):
            raise ValidationError(
                "finding-id inspection needs the original path; inspect one of "
                "the finding's paths in this version"
            )
        raise PathNotFoundError(f"path does not exist: {target}")

    if target.is_file():
        metadata = _inspect_track(target)
        audit_root = target.parent
        kind = "track"
    else:
        metadata = _inspect_directory(target, against=against)
        audit_root = target
        kind = "album" if _direct_audio_files(target) else "directory"

    audit_result = audit_library(
        audit_root,
        against=against,
        min_severity=min_severity,
        include_ok=False,
    )

    return InspectResult(
        path=str(target),
        generated_at=_now(),
        kind=kind,
        metadata=metadata,
        findings=audit_result.findings,
    )


def apply_plan(
    plan_path: Path | str,
    *,
    plan_text: str | None = None,
    dry_run: bool = True,
    quarantine_dir: Path | None = None,
    db_conn: sqlite3.Connection | None = None,
    skip_ids: set[str] | None = None,
) -> ApplyResult:
    """Validate and optionally execute a whitelisted fix plan."""
    label = str(plan_path)
    if plan_text is None:
        plan_text = Path(plan_path).read_text()

    try:
        plan = json.loads(plan_text)
    except json.JSONDecodeError as e:
        raise ValidationError(f"invalid JSON fix plan: {e}") from e

    if not isinstance(plan, dict):
        raise ValidationError("fix plan must be a JSON object")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError(f"unsupported fix plan schema_version: {plan.get('schema_version')}")

    root_value = plan.get("library_root")
    if not isinstance(root_value, str) or not root_value:
        raise ValidationError("fix plan requires library_root")
    library_root = Path(root_value).resolve(strict=False)
    if not library_root.exists():
        raise PathNotFoundError(f"library_root does not exist: {library_root}")

    source_roots = _coerce_source_roots(plan.get("source_roots"), library_root)

    qdir = (
        quarantine_dir.resolve(strict=False)
        if quarantine_dir is not None
        else (library_root / "_quarantine").resolve(strict=False)
    )

    actions_value = plan.get("actions")
    if not isinstance(actions_value, list):
        raise ValidationError("fix plan actions must be a list")

    validated_actions: list[tuple[dict[str, Any], list[str], bool]] = []
    for index, raw_action in enumerate(actions_value, 1):
        action = _validate_action(
            raw_action,
            index,
            library_root,
            qdir,
            source_roots,
        )
        warnings = _action_warnings(action, db_conn)
        skipped = skip_ids is not None and action["action_id"] in skip_ids
        validated_actions.append((action, warnings, skipped))

    _validate_path_move_conflicts(
        action
        for action, _warnings, skipped in validated_actions
        if not skipped
    )

    action_results: list[ApplyActionResult] = []
    for action, warnings, skipped in validated_actions:
        db_updates = None
        if not dry_run and not skipped:
            _execute_action(action)
            db_updates = _relocate_action_paths(action, db_conn)
        if skipped:
            status = "skipped"
        elif dry_run and action["type"] == "ignore_finding":
            status = "would_ignore"
        elif dry_run:
            status = "would_apply"
        else:
            status = "applied"
        action_results.append(ApplyActionResult(
            action_id=action["action_id"],
            type=action["type"],
            status=status,
            source=action.get("source"),
            destination=action.get("destination"),
            reason=action.get("reason"),
            db_updates=db_updates if db_updates else None,
            warnings=warnings if warnings else None,
        ))

    return ApplyResult(
        plan_path=label,
        dry_run=dry_run,
        executed=not dry_run,
        valid=True,
        actions=action_results,
        errors=[],
    )


def propose_year_folders(
    path: Path,
    *,
    index_conn: sqlite3.Connection | None = None,
    excludes: list[str] | None = None,
) -> ProposeResult:
    """Generate rename actions for album dirs missing (Year)."""
    root = path.resolve()
    if not root.exists():
        raise PathNotFoundError(f"path does not exist: {root}")

    audit_index = _AuditIndex(index_conn) if index_conn is not None else None
    ignore_policy = load_ignore_policy(root, excludes=excludes)
    album_dirs = _discover_album_dirs(root, ignore_policy)

    if audit_index is not None:
        for f in _audio_files_under(root, ignore_policy):
            audit_index.facts(f)

    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for album_dir in album_dirs:
        if audit_index is not None:
            album_profile = _resolve_album_profile(
                album_dir,
                "auto",
                audit_index,
                ignore_policy,
            )
            if not album_profile.check_year_structure:
                continue
        rel_parts = _relative_parts(album_dir, root)
        layout_parts = _album_layout_parts(rel_parts)
        if len(layout_parts) > 2:
            continue
        if ALBUM_YEAR_RE.search(album_dir.name):
            continue

        year = _consensus_year(album_dir, audit_index, ignore_policy)
        if year is None:
            skipped.append({"path": str(album_dir), "reason": "no_consensus_year"})
            continue

        suggested_name = f"{album_dir.name} ({year})"
        destination = album_dir.parent / suggested_name
        if destination.exists():
            skipped.append({
                "path": str(album_dir),
                "reason": "destination_exists",
                "destination": str(destination),
            })
            continue

        tracks = _direct_audio_files(album_dir, ignore_policy)
        action: dict[str, Any] = {
            "action_id": f"rename_album_dir:{len(actions) + 1}",
            "type": "rename_album_dir",
            "because_finding": "structure.album_year_missing",
            "source": str(album_dir),
            "destination": str(destination),
            "reason": f'Rename to "{suggested_name}" (tag year).',
            "track_count": len(tracks),
            "formats": sorted({p.suffix.lower().lstrip(".") for p in tracks}),
        }
        if index_conn is not None:
            refs = db.find_tape_references(index_conn, str(album_dir))
            if refs:
                action["tape_references"] = [
                    {"project": name, "path": item_path}
                    for name, item_path in refs
                ]
        actions.append(action)

    return ProposeResult(
        library_root=str(root),
        generated_at=_now(),
        generated_by="musiktool propose year-folders",
        proposal_type="year-folders",
        actions=actions,
        skipped=skipped,
    )


def propose_media_kind_folders(
    path: Path,
    *,
    conn: sqlite3.Connection,
    excludes: list[str] | None = None,
) -> ProposeResult:
    """Generate rename actions to move albums into media-kind subtrees."""
    root = path.resolve()
    if not root.exists():
        raise PathNotFoundError(f"path does not exist: {root}")

    ignore_policy = load_ignore_policy(root, excludes=excludes)
    album_dirs = _discover_album_dirs(root, ignore_policy)

    subtree_names = set(MEDIA_KIND_SUBTREES.values())
    audit_index = _AuditIndex(conn)

    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for album_dir in album_dirs:
        rel_parts = _relative_parts(album_dir, root)
        if not rel_parts:
            continue

        # Already under a recognized subtree?
        if rel_parts[0] in subtree_names:
            cls = _resolve_album_classification(
                album_dir,
                conn,
                ignore_policy,
                audit_index=audit_index,
            )
            if cls is not None:
                expected = MEDIA_KIND_SUBTREES.get(cls["media_kind"])
                if expected == rel_parts[0]:
                    continue  # already correct
            skipped.append({
                "path": str(album_dir),
                "reason": "wrong_subtree",
                "current_subtree": rel_parts[0],
                "classified_as": cls["media_kind"] if cls else None,
            })
            continue

        cls = _resolve_album_classification(
            album_dir,
            conn,
            ignore_policy,
            audit_index=audit_index,
        )
        if cls is None:
            skipped.append({"path": str(album_dir), "reason": "unclassified"})
            continue

        media_kind = cls["media_kind"]
        source = cls["source"]
        confidence = cls["confidence"]

        if source != "manual" and confidence < _MEDIA_KIND_MIN_CONFIDENCE:
            skipped.append({
                "path": str(album_dir),
                "reason": "low_confidence",
                "media_kind": media_kind,
                "source": source,
                "confidence": confidence,
            })
            continue

        subtree = MEDIA_KIND_SUBTREES.get(media_kind)
        if subtree is None:
            skipped.append({
                "path": str(album_dir),
                "reason": "unknown_media_kind",
                "media_kind": media_kind,
            })
            continue

        destination = root / subtree / album_dir.relative_to(root)
        if destination.exists():
            skipped.append({
                "path": str(album_dir),
                "reason": "destination_exists",
                "destination": str(destination),
            })
            continue

        tracks = _direct_audio_files(album_dir, ignore_policy)
        action: dict[str, Any] = {
            "action_id": f"rename_album_dir:{len(actions) + 1}",
            "type": "rename_album_dir",
            "because_finding": "structure.media_kind_subtree_missing",
            "source": str(album_dir),
            "destination": str(destination),
            "reason": f"Move to {subtree}/ ({media_kind}, {source}).",
            "track_count": len(tracks),
            "formats": sorted({p.suffix.lower().lstrip(".") for p in tracks}),
        }
        refs = db.find_tape_references(conn, str(album_dir))
        if refs:
            action["tape_references"] = [
                {"project": name, "path": item_path}
                for name, item_path in refs
            ]
        actions.append(action)

    return ProposeResult(
        library_root=str(root),
        generated_at=_now(),
        generated_by="musiktool propose media-kind-folders",
        proposal_type="media-kind-folders",
        actions=actions,
        skipped=skipped,
    )


def _finding_album_dirs(finding: Finding, root: str) -> list[str]:
    """Return album directory paths this finding should appear under."""
    root_resolved = root.rstrip("/")
    result_dirs: list[str] = []
    for path_str in finding.paths:
        p = Path(path_str)
        try:
            p.relative_to(root_resolved)
        except ValueError:
            continue
        # Files (audio or non-audio with a suffix) belong to their parent dir.
        # Directories (album dirs) are used directly.
        if p.suffix:
            album = str(p.parent)
        else:
            album = path_str
        if album == root_resolved:
            continue
        if album not in result_dirs:
            result_dirs.append(album)
    return result_dirs


_AGGREGATABLE_CATEGORIES = frozenset({
    "tags.missing_required",
    "tags.track_number_mismatch",
    "tracks.filename_pattern",
})


def _finding_detail(finding: Finding, root: str, album_dir: str) -> str:
    """Concise one-line detail from category and evidence."""
    cat = finding.category
    ev = finding.evidence

    if cat == "structure.album_year_missing":
        suggested = ev.get("suggested_name")
        if suggested:
            return f"\u2192 {suggested}"
        return "no consensus year"

    if cat == "structure.album_year_invalid":
        return f"year {ev.get('year')} implausible"

    if cat == "structure.nested_album":
        parent_info = ev.get("parent", {})
        nested_info = ev.get("nested", {})
        nested_path = nested_info.get("path", "")
        parent_path = parent_info.get("path", "")
        # Under the parent album, describe the nested child.
        # Under the nested album itself, describe the parent.
        if album_dir == parent_path or album_dir != nested_path:
            count = nested_info.get("track_count", "?")
            formats = ", ".join(nested_info.get("formats", []))
            nested_name = Path(nested_path).name
            return f"{nested_name} (nested, {count} tracks {formats})"
        try:
            parent_rel = str(Path(parent_path).relative_to(root))
        except ValueError:
            parent_rel = parent_path
        return f"nested under {parent_rel}"

    if cat == "structure.stray_file":
        return ev.get("name", "")

    if cat in ("provenance.cue_missing", "provenance.eac_log_missing"):
        return ""

    if cat in ("duplicates.same_album_candidate", "duplicates.source_already_curated"):
        evidence_type = ev.get("evidence_type", "")
        other_paths = [
            p for p in finding.paths
            if p != album_dir
        ]
        if other_paths:
            try:
                rel = str(Path(other_paths[0]).relative_to(root))
            except ValueError:
                rel = other_paths[0]
            return f"{evidence_type} with {rel}"
        return evidence_type

    return ""


def _aggregate_lines(
    findings: list[Finding], category: str,
) -> str:
    """Collapse per-track findings of the same category into one line."""
    count = len(findings)
    if category == "tags.missing_required":
        all_missing: set[str] = set()
        for f in findings:
            all_missing.update(f.evidence.get("missing", []))
        fields = ", ".join(sorted(all_missing))
        return f"{count} tracks missing: {fields}"
    if category == "tags.track_number_mismatch":
        return f"{count} tracks: filename/tag track number disagree"
    if category == "tracks.filename_pattern":
        return f"{count} tracks: filename does not match NN Title.ext"
    return f"{count} findings"


def _format_audit_text(result: AuditResult) -> str:
    """Render audit result as grouped operator-facing text."""
    root = result.root

    groups: dict[str, list[Finding]] = {}
    library_findings: list[Finding] = []
    for finding in result.findings:
        album_dirs = _finding_album_dirs(finding, root)
        if not album_dirs:
            library_findings.append(finding)
        else:
            for album_dir in album_dirs:
                groups.setdefault(album_dir, []).append(finding)

    root_path = Path(root)
    sorted_albums = sorted(groups.keys(), key=lambda d: str(Path(d).relative_to(root_path)))

    lines: list[str] = []

    if library_findings:
        lines.append("(library root)")
        for f in library_findings:
            icon = _SEVERITY_ICONS.get(f.severity, "?")
            short_cat = f.category.rsplit(".", 1)[-1]
            detail = _finding_detail(f, root, root)
            if detail:
                lines.append(f"  {icon} {short_cat}: {detail}")
            else:
                lines.append(f"  {icon} {short_cat}")
        lines.append("")

    for album_dir in sorted_albums:
        album_findings = groups[album_dir]
        try:
            header = str(Path(album_dir).relative_to(root_path))
        except ValueError:
            header = album_dir
        album_profile_name = result.album_profiles.get(album_dir)
        if album_profile_name and album_profile_name != "music":
            header = f"{header}  [{album_profile_name}]"
        lines.append(header)

        aggregatable: dict[str, list[Finding]] = {}
        individual: list[Finding] = []
        for f in album_findings:
            if f.category in _AGGREGATABLE_CATEGORIES:
                aggregatable.setdefault(f.category, []).append(f)
            else:
                individual.append(f)

        for f in individual:
            icon = _SEVERITY_ICONS.get(f.severity, "?")
            short_cat = f.category.rsplit(".", 1)[-1]
            detail = _finding_detail(f, root, album_dir)
            if detail:
                lines.append(f"  {icon} {short_cat}: {detail}")
            else:
                lines.append(f"  {icon} {short_cat}")

        for cat in sorted(aggregatable):
            cat_findings = aggregatable[cat]
            severity = cat_findings[0].severity
            icon = _SEVERITY_ICONS.get(severity, "?")
            text = _aggregate_lines(cat_findings, cat)
            lines.append(f"  {icon} {text}")

        lines.append("")

    if not result.findings:
        lines.append("No findings.")
    else:
        lines.append(f"{len(result.findings)} findings")

    profiles_used = result.summary.get("profiles_used")
    if isinstance(profiles_used, dict) and len(profiles_used) > 1:
        parts = [f"{count} {name}" for name, count in sorted(profiles_used.items())]
        lines.append(f"Profiles: {', '.join(parts)}")

    index_summary = result.summary.get("index")
    if isinstance(index_summary, dict):
        sources = index_summary.get("fact_sources")
        if isinstance(sources, dict):
            lines.append(
                "Index facts: "
                f"{sources.get('indexed', 0)} indexed, "
                f"{sources.get('partial', 0)} partial, "
                f"{sources.get('live', 0)} live, "
                f"{sources.get('stale', 0)} stale"
            )
        refresh = index_summary.get("refresh")
        if isinstance(refresh, dict):
            lines.append(
                "Index refresh: "
                f"{refresh.get('files_indexed', 0)} refreshed, "
                f"{refresh.get('files_from_sidecar', 0)} from sidecar, "
                f"{refresh.get('files_skipped', 0)} current"
            )

    return "\n".join(lines) + "\n"


def format_audit(result: AuditResult, output_format: str = "text") -> str:
    """Render an audit result."""
    _validate_output_format(output_format)
    if output_format == "json":
        return _json(result.to_dict())
    if output_format == "ndjson":
        lines = [
            _json_line({
                "schema_version": SCHEMA_VERSION,
                "command": "audit",
                "type": "summary",
                "root": result.root,
                "generated_at": result.generated_at,
                "summary": result.summary,
            })
        ]
        lines.extend(_json_line({"type": "finding", **f.to_dict()}) for f in result.findings)
        return "\n".join(lines) + "\n"
    return _format_audit_text(result)


def format_inspect(result: InspectResult, output_format: str = "text") -> str:
    """Render an inspect result."""
    _validate_output_format(output_format)
    if output_format == "json":
        return _json(result.to_dict())
    if output_format == "ndjson":
        lines = [
            _json_line({
                "schema_version": SCHEMA_VERSION,
                "command": "inspect",
                "type": "metadata",
                "path": result.path,
                "generated_at": result.generated_at,
                "kind": result.kind,
                "metadata": result.metadata,
            })
        ]
        lines.extend(_json_line({"type": "finding", **f.to_dict()}) for f in result.findings)
        return "\n".join(lines) + "\n"

    lines = [f"{result.path} ({result.kind})", ""]
    if result.kind == "track":
        tags = result.metadata["tags"]
        lines.append(f"  Title:  {tags.get('title')}")
        lines.append(f"  Artist: {tags.get('artist')}")
        lines.append(f"  Album:  {tags.get('album')}")
    else:
        lines.append(f"  Tracks: {result.metadata.get('track_count', 0)}")
        lines.append(f"  Formats: {', '.join(result.metadata.get('formats', [])) or 'none'}")
        lines.append(f"  CUE: {len(result.metadata.get('cue_files', []))}")
        lines.append(f"  EAC logs: {len(result.metadata.get('eac_logs', []))}")
    lines.append("")
    lines.append(f"  Findings: {len(result.findings)}")
    return "\n".join(lines) + "\n"


def format_apply(result: ApplyResult, output_format: str = "text") -> str:
    """Render an apply result."""
    _validate_output_format(output_format)
    if output_format == "json":
        return _json(result.to_dict())
    if output_format == "ndjson":
        lines = [
            _json_line({
                "schema_version": SCHEMA_VERSION,
                "command": "apply",
                "type": "summary",
                "plan_path": result.plan_path,
                "dry_run": result.dry_run,
                "executed": result.executed,
                "valid": result.valid,
                "errors": result.errors,
            })
        ]
        lines.extend(_json_line({"type": "action", **a.to_dict()}) for a in result.actions)
        return "\n".join(lines) + "\n"

    prefix = "Dry run" if result.dry_run else "Applied"
    lines = [f"{prefix}: {len(result.actions)} action(s)", ""]
    for action in result.actions:
        src = f" {action.source}" if action.source else ""
        dst = f" -> {action.destination}" if action.destination else ""
        lines.append(f"  {action.status:<11s} {action.type}{src}{dst}")
        if action.warnings:
            for warning in action.warnings:
                lines.append(f"               \u26a0 {warning}")
        if action.db_updates:
            total = sum(action.db_updates.values())
            lines.append(f"               \u2192 {total} DB reference(s) updated")
    return "\n".join(lines) + "\n"


def format_propose(result: ProposeResult, output_format: str = "text") -> str:
    """Render a propose result."""
    _validate_output_format(output_format)
    if output_format == "json":
        return _json(result.to_dict())
    if output_format == "ndjson":
        lines = [
            _json_line({
                "schema_version": SCHEMA_VERSION,
                "command": "propose",
                "type": "summary",
                "library_root": result.library_root,
                "generated_at": result.generated_at,
                "proposal_type": result.proposal_type,
                "summary": {
                    "proposed": len(result.actions),
                    "skipped": len(result.skipped),
                },
            })
        ]
        lines.extend(_json_line({"type": "action", **a}) for a in result.actions)
        return "\n".join(lines) + "\n"

    root = result.library_root
    verb = "move" if result.proposal_type == "media-kind-folders" else "rename"
    lines = [
        f"Propose: {result.proposal_type} "
        f"({len(result.actions)} {verb}(s), {len(result.skipped)} skipped)",
        "",
    ]
    for action in result.actions:
        source = action["source"]
        destination = action["destination"]
        try:
            src_rel = Path(source).relative_to(root)
            dst_rel = Path(destination).relative_to(root)
        except ValueError:
            src_rel = source
            dst_rel = destination
        tape_refs = action.get("tape_references", [])
        tape_suffix = ""
        if tape_refs:
            projects = sorted({r["project"] for r in tape_refs})
            tape_suffix = f"  [{', '.join(projects)}: {len(tape_refs)} ref(s)]"
        track_count = action.get("track_count")
        formats = action.get("formats")
        meta = ""
        if track_count is not None:
            fmt_str = "/".join(formats) if formats else "?"
            meta = f"  ({track_count} tracks, {fmt_str})"
        lines.append(f"  {src_rel}  \u2192  {dst_rel}{meta}{tape_suffix}")
    if result.skipped:
        lines.append("")
        lines.append("Skipped:")
        for entry in result.skipped:
            try:
                skip_rel = str(Path(entry["path"]).relative_to(root))
            except ValueError:
                skip_rel = entry["path"]
            lines.append(f"  {skip_rel}: {entry['reason']}")
    lines.append("")
    lines.append("Pipe JSON to apply:")
    lines.append(
        "  musiktool propose <path> --type "
        f"{result.proposal_type} --format json | musiktool apply - --execute"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def compute_stats(
    path: Path,
    *,
    index_conn: sqlite3.Connection | None = None,
    excludes: list[str] | None = None,
) -> StatsResult:
    """Compute library statistics by walking the filesystem.

    If *index_conn* is provided, enriches with tag coverage, codec histogram,
    classification distribution, index coverage, and loudness coverage from DB.
    """
    root = path.resolve()
    ignore_policy = load_ignore_policy(root, excludes=excludes)
    album_dirs = _discover_album_dirs(root, ignore_policy)
    audio_files = _audio_files_under(root, ignore_policy)
    all_files = _files_under(root, ignore_policy)
    audio_file_set = set(audio_files)

    ext_counts: dict[str, int] = {}
    ext_bytes: dict[str, int] = {}
    asset_counts: dict[str, int] = {}
    asset_bytes: dict[str, int] = {}
    total_bytes = 0
    audio_bytes = 0
    non_audio_bytes = 0

    for file_path in all_files:
        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0
        total_bytes += size

        if file_path in audio_file_set:
            audio_bytes += size
            ext = file_path.suffix.lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            ext_bytes[ext] = ext_bytes.get(ext, 0) + size
        else:
            non_audio_bytes += size
            kind = _asset_kind(file_path)
            asset_counts[kind] = asset_counts.get(kind, 0) + 1
            asset_bytes[kind] = asset_bytes.get(kind, 0) + size

    format_histogram = sorted(
        [
            {"ext": ext, "count": ext_counts[ext], "bytes": ext_bytes.get(ext, 0)}
            for ext in ext_counts
        ],
        key=lambda x: x["count"],
        reverse=True,
    )
    asset_histogram = sorted(
        [
            {
                "kind": kind,
                "count": asset_counts[kind],
                "bytes": asset_bytes.get(kind, 0),
            }
            for kind in asset_counts
        ],
        key=lambda x: x["count"],
        reverse=True,
    )

    provenance = _stats_provenance(album_dirs, ignore_policy=ignore_policy)

    # DB-enriched stats
    tag_coverage = None
    codec_histogram = None
    classifications = None
    index_coverage = None
    loudness_coverage = None

    if index_conn is not None:
        audit_index = _AuditIndex(index_conn)
        provenance = _stats_provenance(
            album_dirs,
            audit_index=audit_index,
            ignore_policy=ignore_policy,
        )
        index_coverage = _stats_index_coverage(index_conn, audio_files)
        tag_coverage = _stats_tag_coverage(audio_files, audit_index)
        codec_histogram = _stats_codec_histogram(audio_files, audit_index)
        classifications = _stats_classifications(
            album_dirs,
            audit_index,
            ignore_policy,
        )
        loudness_coverage = _stats_loudness_coverage(
            index_conn,
            audio_files,
            album_dirs,
        )

    return StatsResult(
        root=str(root),
        generated_at=_now(),
        albums=len(album_dirs),
        tracks=len(audio_files),
        total_files=len(all_files),
        audio_files=len(audio_files),
        non_audio_files=len(all_files) - len(audio_files),
        total_bytes=total_bytes,
        audio_bytes=audio_bytes,
        non_audio_bytes=non_audio_bytes,
        format_histogram=format_histogram,
        asset_histogram=asset_histogram,
        provenance=provenance,
        tag_coverage=tag_coverage or None,
        codec_histogram=codec_histogram,
        classifications=classifications,
        index_coverage=index_coverage,
        loudness_coverage=loudness_coverage,
    )


def _asset_kind(path: Path) -> str:
    """Classify a non-audio side file for inventory/reporting."""
    suffix = path.suffix.lower()
    if suffix == ".cue":
        return "cue_sheet"
    if suffix == ".log":
        return "rip_log" if _is_eac_log(path) else "log"
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
        return "image"
    if suffix == ".pdf":
        return "booklet"
    if suffix in {".m3u", ".m3u8", ".xspf", ".pls"}:
        return "playlist"
    if suffix in {".txt", ".nfo", ".md"}:
        return "text"
    return "other"


def _stats_provenance(
    album_dirs: list[Path],
    *,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> dict[str, int]:
    checked_albums = 0
    albums_with_cue = 0
    albums_with_eac_log = 0
    cue_files = 0
    eac_logs = 0
    for album in album_dirs:
        if audit_index is not None:
            profile = _resolve_album_profile(album, "auto", audit_index, ignore_policy)
            if not profile.check_provenance:
                continue
        checked_albums += 1
        cues = _cue_files(album)
        logs = _eac_logs(album)
        if cues:
            albums_with_cue += 1
            cue_files += len(cues)
        if logs:
            albums_with_eac_log += 1
            eac_logs += len(logs)
    return {
        "albums": checked_albums,
        "albums_with_cue": albums_with_cue,
        "albums_with_eac_log": albums_with_eac_log,
        "cue_files": cue_files,
        "eac_logs": eac_logs,
    }


def _stats_index_coverage(
    conn: sqlite3.Connection,
    audio_files: list[Path],
) -> dict[str, int]:
    indexed = 0
    stale = 0
    missing = 0
    for file_path in audio_files:
        row = db.get_indexed_file(conn, str(file_path))
        if row is None:
            missing += 1
            continue
        try:
            stat = file_path.stat()
        except OSError:
            stale += 1
            continue
        if db.indexed_file_unchanged(row, size=stat.st_size, mtime_ns=stat.st_mtime_ns):
            indexed += 1
        else:
            stale += 1
    return {
        "indexed": indexed,
        "stale": stale,
        "missing": missing,
        "total": len(audio_files),
    }


def _stats_tag_coverage(
    audio_files: list[Path],
    audit_index: _AuditIndex,
) -> dict[str, dict[str, int]]:
    fields = ("artist", "album", "title", "date", "track_number")
    result = {field: {"have": 0, "total": len(audio_files)} for field in fields}
    for file_path in audio_files:
        tags = audit_index.tags(file_path)
        values = {
            "artist": tags.artist,
            "album": tags.album,
            "title": tags.title,
            "date": tags.year,
            "track_number": tags.track_number,
        }
        for field in fields:
            if values[field] is not None:
                result[field]["have"] += 1
    return result


def _stats_codec_histogram(
    audio_files: list[Path],
    audit_index: _AuditIndex,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    durations: dict[str, float] = {}
    for file_path in audio_files:
        facts = audit_index.facts(file_path)
        codec = None
        duration = None
        if facts.current and facts.audio is not None:
            codec = facts.audio["codec"]
            duration = facts.audio["duration_sec"]
        codec = str(codec or file_path.suffix.lower().lstrip(".") or "unknown")
        counts[codec] = counts.get(codec, 0) + 1
        if duration is not None:
            durations[codec] = durations.get(codec, 0.0) + float(duration)
    return [
        {
            "codec": codec,
            "count": counts[codec],
            "duration_sec": round(durations[codec], 3) if codec in durations else None,
        }
        for codec in sorted(counts, key=lambda c: (-counts[c], c))
    ]


def _stats_classifications(
    album_dirs: list[Path],
    audit_index: _AuditIndex,
    ignore_policy: IgnorePolicy,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for album in album_dirs:
        profile = _resolve_album_profile(album, "auto", audit_index, ignore_policy)
        result[profile.name] = result.get(profile.name, 0) + 1
    return result


def _stats_loudness_coverage(
    conn: sqlite3.Connection,
    audio_files: list[Path],
    album_dirs: list[Path],
) -> dict[str, int]:
    tracks = 0
    for file_path in audio_files:
        if db.get_track(conn, str(file_path)) is not None:
            tracks += 1
    albums = 0
    for album in album_dirs:
        row = conn.execute(
            "SELECT 1 FROM album_loudness WHERE path = ?",
            (str(album),),
        ).fetchone()
        if row is not None:
            albums += 1
    return {"tracks": tracks, "albums": albums}


def _format_bytes(n: int) -> str:
    """Human-readable byte size."""
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def format_stats(result: StatsResult, output_format: str = "text") -> str:
    """Render stats result as text, json, or ndjson."""
    _validate_output_format(output_format)
    if output_format == "json":
        return _json(result.to_dict())
    if output_format == "ndjson":
        return _json_line(result.to_dict()) + "\n"

    # Text format
    lines = [
        result.root,
        f"  Albums:      {result.albums:>6d}",
        f"  Tracks:      {result.tracks:>6d}",
        f"  Files:       {result.total_files:>6d}  "
        f"({result.audio_files} audio, {result.non_audio_files} side files)",
        f"  Total size:  {_format_bytes(result.total_bytes):>6s}",
        f"  Audio size:  {_format_bytes(result.audio_bytes):>6s}",
    ]

    if result.format_histogram:
        lines.append("")
        lines.append("  Formats:")
        for entry in result.format_histogram:
            ext = entry["ext"].lstrip(".")
            count = entry["count"]
            pct = count / result.tracks * 100 if result.tracks else 0
            size = _format_bytes(entry["bytes"])
            lines.append(f"    {ext:<8s} {count:>5d}  ({pct:>5.1f}%)  {size:>8s}")

    if result.asset_histogram:
        lines.append("")
        lines.append("  Side files:")
        for entry in result.asset_histogram:
            kind = entry["kind"]
            count = entry["count"]
            pct = count / result.non_audio_files * 100 if result.non_audio_files else 0
            size = _format_bytes(entry["bytes"])
            lines.append(f"    {kind:<12s} {count:>5d}  ({pct:>5.1f}%)  {size:>8s}")

    if result.provenance["albums"]:
        cue = result.provenance["albums_with_cue"]
        log = result.provenance["albums_with_eac_log"]
        albums = result.provenance["albums"]
        lines.append("")
        lines.append(
            f"  Provenance: {cue} / {albums} albums with CUE, "
            f"{log} / {albums} with EAC/log"
        )

    if result.tag_coverage:
        lines.append("")
        lines.append("  Tag coverage:")
        for field, counts in result.tag_coverage.items():
            have = counts["have"]
            total = counts["total"]
            pct = have / total * 100 if total else 0
            lines.append(f"    {field:<14s} {have:>5d} / {total:<5d}  ({pct:>5.1f}%)")

    if result.classifications:
        lines.append("")
        lines.append("  Media profiles:")
        for kind, count in result.classifications.items():
            lines.append(f"    {kind:<14s} {count:>5d}")

    if result.index_coverage is not None:
        indexed = result.index_coverage["indexed"]
        stale = result.index_coverage.get("stale", 0)
        missing = result.index_coverage.get("missing", 0)
        total = result.index_coverage["total"]
        lines.append("")
        lines.append(
            f"  Index: {indexed} / {total} current"
            f"{f', {stale} stale' if stale else ''}"
            f"{f', {missing} missing' if missing else ''}"
        )

    if result.loudness_coverage is not None:
        lc = result.loudness_coverage
        lines.append(f"  Loudness: {lc['tracks']} tracks, {lc['albums']} albums analyzed")

    return "\n".join(lines) + "\n"


def _audit_album_structure(
    root: Path,
    album_dir: Path,
    rel_parts: tuple[str, ...],
    findings: list[Finding],
    *,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
    profile: MediaProfile = MEDIA_PROFILES["music"],
) -> None:
    if not profile.check_year_structure:
        return
    layout_parts = _album_layout_parts(rel_parts)
    if len(layout_parts) > 2:
        return

    year_match = ALBUM_YEAR_RE.search(album_dir.name)
    if year_match is None:
        tag_year = _consensus_year(album_dir, audit_index, ignore_policy)
        suggested_name = f"{album_dir.name} ({tag_year})" if tag_year is not None else None
        destination = None
        if suggested_name is not None:
            dest_path = album_dir.parent / suggested_name
            if not dest_path.exists():
                destination = str(dest_path)
        findings.append(_finding(
            "structure.album_year_missing",
            "warning",
            [album_dir],
            'Album directory does not end in "(Year)".',
            {
                "album_name": album_dir.name,
                "expected_pattern": "Album (Year)",
                "relative_depth": len(rel_parts),
                "tag_year": tag_year,
                "suggested_name": suggested_name,
            },
            confidence=1.0,
            fixable=True,
            suggested_actions=[{
                "type": "rename_album_dir",
                "source": str(album_dir),
                **({"destination": destination} if destination else {}),
                "reason": (
                    f'Rename to "{suggested_name}".'
                    if suggested_name
                    else "Add a release year once verified from tags or external metadata."
                ),
            }],
        ))
        return

    year = int(year_match.group("year"))
    current_year = datetime.now().year
    if year < 1877 or year > current_year + 1:
        findings.append(_finding(
            "structure.album_year_invalid",
            "warning",
            [album_dir],
            "Album directory year is implausible.",
            {"album_name": album_dir.name, "year": year},
            confidence=1.0,
            fixable=True,
            suggested_actions=[{
                "type": "rename_album_dir",
                "source": str(album_dir),
                "reason": "Correct the release year once verified.",
            }],
        ))


def _audit_album_provenance(
    album_dir: Path,
    findings: list[Finding],
    *,
    profile: MediaProfile = MEDIA_PROFILES["music"],
) -> None:
    if not profile.check_provenance:
        return
    cue_files = _cue_files(album_dir)
    log_files = _eac_logs(album_dir)
    if not cue_files:
        findings.append(_finding(
            "provenance.cue_missing",
            "info",
            [album_dir],
            "No CUE sheet found.",
            {"album_name": album_dir.name},
            confidence=1.0,
            fixable=False,
        ))
    if not log_files:
        findings.append(_finding(
            "provenance.eac_log_missing",
            "info",
            [album_dir],
            "No EAC log found.",
            {"album_name": album_dir.name},
            confidence=1.0,
            fixable=False,
        ))


def _audit_tracks(
    album_dir: Path,
    findings: list[Finding],
    *,
    include_warnings: bool,
    include_info: bool,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
    profile: MediaProfile = MEDIA_PROFILES["music"],
) -> None:
    for track in _direct_audio_files(album_dir, ignore_policy):
        tags = _safe_read_tags(track, audit_index) if include_warnings else Tags()
        if include_warnings:
            missing = _missing_required_tags(tags, profile.required_tags)
        else:
            missing = []
        if include_warnings and missing:
            findings.append(_finding(
                "tags.missing_required",
                "warning",
                [track],
                "Required tag fields are missing.",
                {"missing": missing, "filename": track.name},
                confidence=1.0,
                fixable=True,
                suggested_actions=[{
                    "type": "write_tags",
                    "source": str(track),
                    "reason": "Write verified tag values.",
                }],
            ))

        match = profile.filename_re.match(track.name)
        if include_info and match is None:
            findings.append(_finding(
                "tracks.filename_pattern",
                "info",
                [track],
                f'Filename does not follow "{profile.filename_description}".',
                {"filename": track.name, "expected_pattern": profile.filename_description},
                confidence=1.0,
                fixable=True,
                suggested_actions=[{
                    "type": "rename_track_file",
                    "source": str(track),
                    "reason": "Rename once track number and title are verified.",
                }],
            ))
            continue

        if match is None:
            continue

        file_track_number = int(match.group("number")) if profile.check_track_number_match else None
        if (
            profile.check_track_number_match
            and include_warnings
            and file_track_number is not None
            and tags.track_number is not None
            and tags.track_number != file_track_number
        ):
            findings.append(_finding(
                "tags.track_number_mismatch",
                "warning",
                [track],
                "Filename track number and tag track number disagree.",
                {
                    "filename_track_number": file_track_number,
                    "tag_track_number": tags.track_number,
                },
                confidence=1.0,
                fixable=True,
                suggested_actions=[{
                    "type": "write_tags",
                    "source": str(track),
                    "tags": {"track_number": file_track_number},
                    "reason": "Filename follows library convention.",
                }],
            ))


def _nested_album_finding(
    parent: Path,
    nested: Path,
    *,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> Finding:
    parent_summary = _album_summary(parent, audit_index, ignore_policy)
    nested_summary = _album_summary(nested, audit_index, ignore_policy)
    duration_delta = _duration_delta(parent_summary, nested_summary)
    confidence = 0.92 if (
        parent_summary["track_count"] == nested_summary["track_count"]
        and duration_delta is not None
        and duration_delta <= 2
    ) else 0.75

    return _finding(
        "structure.nested_album",
        "warning",
        [parent, nested],
        "Nested album-like directory found below another album.",
        {
            "parent": parent_summary,
            "nested": nested_summary,
            "duration_delta_sec": duration_delta,
        },
        confidence=confidence,
        fixable=True,
        suggested_actions=[{
            "type": "quarantine",
            "source": str(nested),
            "reason": "Nested album directories are outside the library convention.",
        }],
    )


def _duplicate_findings(
    album_dirs: list[Path],
    category: str,
    *,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
    include_info: bool = False,
    similarity_threshold: float = 0.08,
) -> list[Finding]:
    profiles = [
        _album_duplicate_profile(album_dir, audit_index, ignore_policy)
        for album_dir in album_dirs
    ]
    profiles = [p for p in profiles if p.tracks]

    findings: list[Finding] = []
    claimed: set[frozenset[str]] = set()

    # Exact file hash matches
    for group in _duplicate_groups_by_exact_files(profiles):
        key = frozenset(str(p.path) for p in group)
        if key in claimed:
            continue
        findings.append(_duplicate_finding(
            category,
            "warning",
            group,
            evidence_type="exact_file",
            confidence=0.98,
            fixable=True,
            audit_index=audit_index,
            ignore_policy=ignore_policy,
        ))
        claimed.add(key)

    # Chromaprint similarity matches
    for group, ber in _duplicate_groups_by_chromaprint(profiles, similarity_threshold):
        key = frozenset(str(p.path) for p in group)
        if key in claimed:
            continue
        confidence = _ber_to_confidence(ber)
        findings.append(_duplicate_finding(
            category,
            "warning",
            group,
            evidence_type="audio_fingerprint",
            confidence=confidence,
            fixable=True,
            audit_index=audit_index,
            ignore_policy=ignore_policy,
            extra_evidence={
                "similarity_ber": round(ber, 6),
                "similarity_threshold": similarity_threshold,
            },
        ))
        claimed.add(key)

    title_groups: dict[tuple[Any, ...], list[_AlbumDuplicateProfile]] = {}
    for profile in profiles:
        signature = _title_duplicate_signature(profile)
        if signature is None:
            continue
        title_groups.setdefault(signature, []).append(profile)

    for group in title_groups.values():
        if len(group) < 2:
            continue
        key = frozenset(str(p.path) for p in group)
        if key in claimed:
            continue

        if _is_strong_metadata_duplicate(group):
            findings.append(_duplicate_finding(
                category,
                "warning",
                group,
                evidence_type="strong_album_metadata",
                confidence=0.9,
                fixable=True,
                audit_index=audit_index,
                ignore_policy=ignore_policy,
            ))
            claimed.add(key)
        elif include_info:
            findings.append(_duplicate_finding(
                category,
                "info",
                group,
                evidence_type="weak_title_overlap",
                confidence=0.55,
                fixable=False,
                audit_index=audit_index,
                ignore_policy=ignore_policy,
            ))
            claimed.add(key)

    return findings


def _duplicate_groups_by_exact_files(
    profiles: list[_AlbumDuplicateProfile],
) -> list[list[_AlbumDuplicateProfile]]:
    groups: dict[tuple[str, ...], list[_AlbumDuplicateProfile]] = {}
    for profile in profiles:
        key = _exact_file_key(profile)
        if key is not None:
            groups.setdefault(key, []).append(profile)
    return [group for group in groups.values() if len(group) > 1]


def _duplicate_groups_by_chromaprint(
    profiles: list[_AlbumDuplicateProfile],
    threshold: float,
) -> list[tuple[list[_AlbumDuplicateProfile], float]]:
    """Group albums by chromaprint similarity (BER below threshold).

    Returns list of (group, best_ber) tuples. Uses union-find for transitive
    grouping: if A~B and B~C, all three form one group.
    """
    from musiktool.chromaprint_sim import album_similarity, decode_fingerprint

    # Filter to profiles with complete chromaprints and pre-decode
    decoded: list[tuple[_AlbumDuplicateProfile, list[list[int]]]] = []
    for profile in profiles:
        if not profile.chromaprints or not all(profile.chromaprints):
            continue
        try:
            fps = [decode_fingerprint(fp) for fp in profile.chromaprints]
        except (ValueError, OSError):
            continue
        decoded.append((profile, fps))

    if len(decoded) < 2:
        return []

    # Union-find for transitive grouping
    parent: list[int] = list(range(len(decoded)))
    best_ber: dict[frozenset[int], float] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Pairwise comparison (same track count, first-track quick filter)
    for i in range(len(decoded)):
        profile_i, fps_i = decoded[i]
        for j in range(i + 1, len(decoded)):
            profile_j, fps_j = decoded[j]
            if len(fps_i) != len(fps_j):
                continue
            ber = album_similarity(fps_i, fps_j)
            if ber is not None and ber <= threshold:
                union(i, j)
                key = frozenset((i, j))
                best_ber[key] = ber

    # Collect groups
    groups_map: dict[int, list[int]] = {}
    for idx in range(len(decoded)):
        root = find(idx)
        groups_map.setdefault(root, []).append(idx)

    results: list[tuple[list[_AlbumDuplicateProfile], float]] = []
    for members in groups_map.values():
        if len(members) < 2:
            continue
        group = [decoded[m][0] for m in members]
        # Find best (lowest) BER among pairs in this group
        group_ber = min(
            (best_ber[frozenset((a, b))]
             for a in members for b in members
             if a < b and frozenset((a, b)) in best_ber),
            default=0.0,
        )
        results.append((group, group_ber))

    return results


def _exact_file_key(profile: _AlbumDuplicateProfile) -> tuple[str, ...] | None:
    if profile.blake3 and all(profile.blake3):
        return tuple(str(value) for value in profile.blake3)
    return None


def _chromaprint_key(profile: _AlbumDuplicateProfile) -> tuple[str, ...] | None:
    """Exact chromaprint match key (used for --against comparison)."""
    if profile.chromaprints and all(profile.chromaprints):
        return tuple(str(value) for value in profile.chromaprints)
    return None


def _strong_duplicate_evidence_type(
    profiles: list[_AlbumDuplicateProfile],
) -> str | None:
    if len(profiles) < 2:
        return None
    first = profiles[0]
    first_hash = _exact_file_key(first)
    if first_hash is not None and all(
        _exact_file_key(profile) == first_hash for profile in profiles[1:]
    ):
        return "exact_file"

    first_fingerprint = _chromaprint_key(first)
    if first_fingerprint is not None and all(
        _chromaprint_key(profile) == first_fingerprint
        for profile in profiles[1:]
    ):
        return "audio_fingerprint"

    if _is_strong_metadata_duplicate(profiles):
        return "strong_album_metadata"
    return None


def _ber_to_confidence(ber: float) -> float:
    """Map BER to confidence score for audio fingerprint duplicates."""
    if ber < 0.01:
        return 0.97
    if ber < 0.05:
        return 0.93
    return 0.88


def _duplicate_finding(
    category: str,
    severity: str,
    profiles: list[_AlbumDuplicateProfile],
    *,
    evidence_type: str,
    confidence: float,
    fixable: bool,
    audit_index: _AuditIndex | None,
    ignore_policy: IgnorePolicy | None,
    extra_evidence: dict[str, Any] | None = None,
) -> Finding:
    albums = [p.path for p in profiles]
    preferred = _preferred_album(albums, ignore_policy)
    summaries = [_album_summary(p, audit_index, ignore_policy) for p in albums]
    suggested_actions = []
    if fixable:
        suggested_actions = [
            {
                "type": "quarantine",
                "source": str(p),
                "reason": f"Preferred copy appears to be {preferred}.",
            }
            for p in albums if p != preferred
        ]

    evidence: dict[str, Any] = {
        "evidence_type": evidence_type,
        "albums": summaries,
        "preferred_path": str(preferred),
    }
    if extra_evidence:
        evidence.update(extra_evidence)

    return _finding(
        category,
        severity,
        albums,
        "Multiple album directories appear to contain the same album.",
        evidence,
        confidence=confidence,
        fixable=fixable,
        suggested_actions=suggested_actions,
    )


def _album_duplicate_profile(
    album_dir: Path,
    audit_index: _AuditIndex | None,
    ignore_policy: IgnorePolicy | None,
) -> _AlbumDuplicateProfile:
    tracks = _direct_audio_files(album_dir, ignore_policy)
    titles: list[str] = []
    durations: list[float | None] = []
    hashes: list[str | None] = []
    chromaprints: list[str | None] = []
    artists: list[str] = []
    albums: list[str] = []

    for track in tracks:
        tags = _safe_read_tags(track, audit_index)
        title = tags.title or _title_from_filename(track)
        titles.append(_normalize_text(title))
        durations.append(_duration(track, audit_index))
        hashes.append(_indexed_blake3(track, audit_index))
        chromaprints.append(_indexed_chromaprint(track, audit_index))
        if tags.artist:
            artists.append(_normalize_text(tags.artist))
        if tags.album:
            albums.append(_normalize_text(tags.album))

    return _AlbumDuplicateProfile(
        path=album_dir,
        tracks=tracks,
        titles=titles,
        durations=durations,
        blake3=hashes,
        chromaprints=chromaprints,
        artist=_most_common(artists),
        album=_most_common(albums),
        path_artist=_normalize_text(album_dir.parent.name),
        path_album=_normalize_text(ALBUM_YEAR_RE.sub("", album_dir.name).strip()),
    )


def _title_duplicate_signature(
    profile: _AlbumDuplicateProfile,
) -> tuple[Any, ...] | None:
    if not profile.titles or not any(profile.titles):
        return None
    return (len(profile.tracks), tuple(profile.titles))


def _is_strong_metadata_duplicate(
    profiles: list[_AlbumDuplicateProfile],
) -> bool:
    if len(profiles) < 2:
        return False
    reference = profiles[0]
    for candidate in profiles[1:]:
        if len(reference.tracks) != len(candidate.tracks):
            return False
        if _title_overlap(reference, candidate) < 0.8:
            return False
        duration_delta = _median_duration_delta(reference, candidate)
        if duration_delta is None or duration_delta > 3:
            return False
        if not _album_identity_agrees(reference, candidate):
            return False
    return True


def _title_overlap(
    left: _AlbumDuplicateProfile,
    right: _AlbumDuplicateProfile,
) -> float:
    left_titles = {t for t in left.titles if t}
    right_titles = {t for t in right.titles if t}
    if not left_titles or not right_titles:
        return 0.0
    return len(left_titles & right_titles) / max(len(left_titles), len(right_titles))


def _median_duration_delta(
    left: _AlbumDuplicateProfile,
    right: _AlbumDuplicateProfile,
) -> float | None:
    deltas = [
        abs(float(a) - float(b))
        for a, b in zip(left.durations, right.durations)
        if a is not None and b is not None
    ]
    if not deltas:
        return None
    return float(median(deltas))


def _album_identity_agrees(
    left: _AlbumDuplicateProfile,
    right: _AlbumDuplicateProfile,
) -> bool:
    artist_agrees = _nonempty_equal(left.artist, right.artist) or _nonempty_equal(
        left.path_artist, right.path_artist,
    )
    album_agrees = _nonempty_equal(left.album, right.album) or _nonempty_equal(
        left.path_album, right.path_album,
    )
    return artist_agrees and album_agrees


def _indexed_blake3(
    track: Path,
    audit_index: _AuditIndex | None,
) -> str | None:
    if audit_index is None:
        return None
    facts = audit_index.facts(track)
    if facts.current and facts.indexed is not None:
        return facts.indexed["blake3"]
    return None


def _indexed_chromaprint(
    track: Path,
    audit_index: _AuditIndex | None,
) -> str | None:
    if audit_index is None:
        return None
    facts = audit_index.facts(track)
    if facts.current and facts.audio is not None:
        return facts.audio["chromaprint"]
    return None


def _most_common(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _nonempty_equal(left: str, right: str) -> bool:
    return bool(left and right and left == right)


def _against_findings(
    source_albums: list[Path],
    curated_albums: list[Path],
    *,
    audit_index: _AuditIndex | None = None,
    source_ignore_policy: IgnorePolicy | None = None,
    curated_ignore_policy: IgnorePolicy | None = None,
) -> list[Finding]:
    curated_profiles = [
        _album_duplicate_profile(album, audit_index, curated_ignore_policy)
        for album in curated_albums
    ]
    curated_profiles = [p for p in curated_profiles if p.tracks]
    curated_by_key: dict[tuple[str, tuple[Any, ...]], list[_AlbumDuplicateProfile]] = {}
    for profile in curated_profiles:
        for key_name, key in _candidate_duplicate_keys(profile):
            curated_by_key.setdefault((key_name, key), []).append(profile)

    findings = []
    for source in source_albums:
        source_profile = _album_duplicate_profile(
            source, audit_index, source_ignore_policy,
        )
        if not source_profile.tracks:
            continue
        match = None
        evidence_type = None
        seen: set[str] = set()
        for key_name, key in _candidate_duplicate_keys(source_profile):
            for curated_profile in curated_by_key.get((key_name, key), []):
                curated_path = str(curated_profile.path)
                if curated_path in seen:
                    continue
                seen.add(curated_path)
                evidence_type = _strong_duplicate_evidence_type([
                    source_profile, curated_profile,
                ])
                if evidence_type is not None:
                    match = curated_profile
                    break
            if match is not None:
                break
        if match is None or evidence_type is None:
            continue
        curated = match.path
        findings.append(_finding(
            "duplicates.source_already_curated",
            "warning",
            [source, curated],
            "Source album appears to duplicate an existing curated album.",
            {
                "evidence_type": evidence_type,
                "source": _album_summary(source, audit_index, source_ignore_policy),
                "curated": _album_summary(curated, audit_index, curated_ignore_policy),
                "preferred_path": str(curated),
            },
            confidence=0.9,
            fixable=True,
            suggested_actions=[{
                "type": "quarantine",
                "source": str(source),
                "reason": "Album appears to already exist in the curated library.",
            }],
        ))
    return findings


def _candidate_duplicate_keys(
    profile: _AlbumDuplicateProfile,
) -> list[tuple[str, tuple[Any, ...]]]:
    keys: list[tuple[str, tuple[Any, ...]]] = []
    exact_key = _exact_file_key(profile)
    if exact_key is not None:
        keys.append(("exact_file", exact_key))
    fingerprint_key = _chromaprint_key(profile)
    if fingerprint_key is not None:
        keys.append(("audio_fingerprint", fingerprint_key))
    title_key = _title_duplicate_signature(profile)
    if title_key is not None:
        keys.append(("title", title_key))
    return keys


def _validate_path_move_conflicts(actions: Any) -> None:
    """Reject path moves whose sources or destinations overlap."""
    moves: list[tuple[str, Path, Path]] = []
    destinations: dict[str, str] = {}
    for action in actions:
        if action["type"] in {"ignore_finding", "write_tags"}:
            continue
        source = Path(action["source"])
        destination = Path(action["destination"])
        action_id = action["action_id"]

        if _path_is_self_or_descendant(destination, source):
            raise ValidationError(
                f"action {action_id} destination is inside its source: {destination}"
            )

        dest_key = str(destination)
        previous = destinations.get(dest_key)
        if previous is not None:
            raise ValidationError(
                f"actions {previous} and {action_id} have the same destination: "
                f"{destination}"
            )
        destinations[dest_key] = action_id
        moves.append((action_id, source, destination))

    for i, (left_id, left_source, left_dest) in enumerate(moves):
        for right_id, right_source, right_dest in moves[i + 1:]:
            if (
                _path_is_self_or_descendant(left_source, right_source)
                or _path_is_self_or_descendant(right_source, left_source)
            ):
                raise ValidationError(
                    f"actions {left_id} and {right_id} move overlapping sources: "
                    f"{left_source} / {right_source}"
                )
            if (
                _path_is_self_or_descendant(left_dest, right_dest)
                or _path_is_self_or_descendant(right_dest, left_dest)
            ):
                raise ValidationError(
                    f"actions {left_id} and {right_id} move to overlapping "
                    f"destinations: {left_dest} / {right_dest}"
                )
            if _path_is_self_or_descendant(left_dest, right_source):
                raise ValidationError(
                    f"action {left_id} destination overlaps action {right_id} "
                    f"source: {left_dest}"
                )
            if _path_is_self_or_descendant(right_dest, left_source):
                raise ValidationError(
                    f"action {right_id} destination overlaps action {left_id} "
                    f"source: {right_dest}"
                )


def _path_is_self_or_descendant(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return True


def _validate_action(
    raw_action: Any,
    index: int,
    library_root: Path,
    quarantine_dir: Path,
    source_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    if not isinstance(raw_action, dict):
        raise ValidationError(f"action {index} must be an object")
    action_type = raw_action.get("type")
    if action_type not in FIX_PLAN_ACTIONS:
        raise ValidationError(f"action {index} has unsupported type: {action_type}")

    action_id = raw_action.get("action_id") or f"{action_type}:{index}"
    if not isinstance(action_id, str):
        raise ValidationError(f"action {index} action_id must be a string")
    because_finding = raw_action.get("because_finding")
    manual = raw_action.get("manual", False)
    if not isinstance(because_finding, str) and manual is not True:
        raise ValidationError(
            f"action {index} requires because_finding or manual=true"
        )

    action: dict[str, Any] = {
        "action_id": action_id,
        "type": action_type,
        "reason": raw_action.get("reason"),
    }

    if action_type == "ignore_finding":
        return action

    if action_type == "write_tags":
        paths_value = raw_action.get("paths", raw_action.get("path", raw_action.get("source")))
        paths = _coerce_paths(paths_value, index)
        tags = raw_action.get("tags")
        if not isinstance(tags, dict) or not tags:
            raise ValidationError(f"action {index} write_tags requires non-empty tags")
        for path in paths:
            _validate_source(path, library_root, index)
        action["paths"] = [str(p.resolve()) for p in paths]
        action["source"] = action["paths"][0] if len(action["paths"]) == 1 else None
        action["tags"] = tags
        return action

    source_value = raw_action.get("source")
    if not isinstance(source_value, str) or not source_value:
        raise ValidationError(f"action {index} requires source")
    source = Path(source_value).resolve(strict=False)
    if action_type in {"copy_album_dir", "copy_file"}:
        _validate_source_in_roots(source, source_roots or (library_root,), index)
    else:
        _validate_source(source, library_root, index)

    if action_type == "quarantine":
        destination_value = raw_action.get("destination")
        if destination_value:
            destination = Path(destination_value).resolve(strict=False)
        else:
            destination = (quarantine_dir / source.relative_to(library_root)).resolve(strict=False)
        _validate_inside(destination, quarantine_dir, f"action {index} destination")
    else:
        destination_value = raw_action.get("destination")
        if not isinstance(destination_value, str) or not destination_value:
            raise ValidationError(f"action {index} requires destination")
        destination = Path(destination_value).resolve(strict=False)
        _validate_inside(destination, library_root, f"action {index} destination")

    if destination.exists():
        raise ValidationError(f"action {index} destination already exists: {destination}")

    if action_type in {"rename_track_file", "move_file", "copy_file"} and not source.is_file():
        raise ValidationError(f"action {index} source must be a file: {source}")
    if action_type in {"rename_album_dir", "copy_album_dir"} and not source.is_dir():
        raise ValidationError(f"action {index} source must be a directory: {source}")

    action["source"] = str(source)
    action["destination"] = str(destination)
    return action


def _execute_action(action: dict[str, Any]) -> None:
    action_type = action["type"]
    if action_type == "ignore_finding":
        return
    if action_type == "write_tags":
        tags = _tags_from_dict(action["tags"])
        for path in action["paths"]:
            write_tags(path, tags)
        return

    source = Path(action["source"])
    destination = Path(action["destination"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if action_type == "copy_file":
        shutil.copy2(source, destination)
        return
    if action_type == "copy_album_dir":
        shutil.copytree(source, destination)
        return
    shutil.move(str(source), str(destination))


def _relocate_action_paths(
    action: dict[str, Any],
    conn: sqlite3.Connection | None,
) -> dict[str, int] | None:
    """Update DB path references after a filesystem move."""
    if conn is None:
        return None
    if action["type"] in {"ignore_finding", "write_tags", "copy_file", "copy_album_dir"}:
        return None
    source = action.get("source")
    destination = action.get("destination")
    if not source or not destination:
        return None
    counts = db.relocate_paths(conn, source, destination)
    return counts if counts else None


def _action_warnings(
    action: dict[str, Any],
    conn: sqlite3.Connection | None,
) -> list[str]:
    """Check for potential problems with an action."""
    warnings: list[str] = []
    if action["type"] in {"ignore_finding", "write_tags"}:
        return warnings

    destination = action.get("destination")
    if destination:
        dest_name = Path(destination).name
        if dest_name.startswith("."):
            warnings.append(
                f"Destination name starts with '.' (hidden on Unix): {dest_name}"
            )

    source = action.get("source")
    if conn is not None and source and action["type"] not in {"copy_file", "copy_album_dir"}:
        refs = db.find_tape_references(conn, source)
        if refs:
            projects = sorted({name for name, _ in refs})
            warnings.append(
                f"Tape project(s) reference this path: {', '.join(projects)} "
                f"({len(refs)} item(s) — will be updated automatically)"
            )

    return warnings


def _inspect_directory(path: Path, *, against: Path | None = None) -> dict[str, Any]:
    album_dirs = _discover_album_dirs(path)
    tracks = _direct_audio_files(path)
    if not tracks and len(album_dirs) == 1:
        tracks = _direct_audio_files(album_dirs[0])

    metadata = {
        "path": str(path),
        "album_dirs": [str(p) for p in album_dirs],
        "track_count": len(tracks),
        "tracks": [_track_metadata(p) for p in tracks],
        "formats": sorted({p.suffix.lower().lstrip(".") for p in tracks}),
        "cue_files": [str(p) for p in _cue_files(path)],
        "eac_logs": [str(p) for p in _eac_logs(path)],
    }
    if against is not None:
        matches = []
        signature = _album_signature(path)
        if signature is not None:
            for album in _discover_album_dirs(against.resolve()):
                if _album_signature(album) == signature:
                    matches.append(_album_summary(album))
        metadata["against_matches"] = matches
    return metadata


def _inspect_track(path: Path) -> dict[str, Any]:
    return _track_metadata(path)


def _track_metadata(
    path: Path,
    audit_index: _AuditIndex | None = None,
) -> dict[str, Any]:
    tags = _safe_read_tags(path, audit_index)
    metadata = {
        "path": str(path),
        "filename": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "duration_sec": _duration(path, audit_index),
        "tags": _tags_to_dict(tags),
    }
    if audit_index is not None:
        metadata["index_source"] = audit_index.source(path)
    return metadata


def _album_summary(
    path: Path,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> dict[str, Any]:
    tracks = _direct_audio_files(path, ignore_policy)
    durations = [_duration(p, audit_index) for p in tracks]
    known_durations = [d for d in durations if d is not None]
    return {
        "path": str(path),
        "track_count": len(tracks),
        "duration_sec": round(sum(known_durations), 3) if known_durations else None,
        "formats": sorted({p.suffix.lower().lstrip(".") for p in tracks}),
        "has_cue": bool(_cue_files(path)),
        "has_eac_log": bool(_eac_logs(path)),
        "format_score": _album_format_score(path, ignore_policy),
    }


def _album_signature(
    path: Path,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> tuple[Any, ...] | None:
    tracks = _direct_audio_files(path, ignore_policy)
    if not tracks:
        return None
    titles = []
    for track in tracks:
        tags = _safe_read_tags(track, audit_index)
        title = tags.title or _title_from_filename(track)
        titles.append(_normalize_text(title))
    if not any(titles):
        return None
    return (len(tracks), tuple(titles))


def _preferred_album(
    albums: list[Path],
    ignore_policy: IgnorePolicy | None = None,
) -> Path:
    return sorted(
        albums,
        key=lambda p: (
            -_album_format_score(p, ignore_policy),
            -int(bool(_eac_logs(p))),
            -int(bool(_cue_files(p))),
            len(p.parts),
            str(p),
        ),
    )[0]


def _album_format_score(
    path: Path,
    ignore_policy: IgnorePolicy | None = None,
) -> int:
    scores = [
        FORMAT_SCORES.get(p.suffix.lower(), 0)
        for p in _direct_audio_files(path, ignore_policy)
    ]
    return max(scores) if scores else 0


def _duration_delta(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    left_duration = left.get("duration_sec")
    right_duration = right.get("duration_sec")
    if left_duration is None or right_duration is None:
        return None
    return round(abs(float(left_duration) - float(right_duration)), 3)


def _discover_album_dirs(
    root: Path,
    ignore_policy: IgnorePolicy | None = None,
) -> list[Path]:
    if root.is_file():
        return [
            root.parent.resolve()
        ] if (
            _is_audio(root)
            and not _is_ignored(root, ignore_policy, is_dir=False)
        ) else []

    root = root.resolve()
    albums = []
    if _direct_audio_files(root, ignore_policy):
        albums.append(root)

    for current, dirs, _names in root.walk():
        dirs[:] = [
            name for name in dirs
            if not _is_ignored(current / name, ignore_policy, is_dir=True)
        ]
        if current == root:
            continue
        if _direct_audio_files(current, ignore_policy):
            albums.append(current.resolve())
    return sorted(set(albums))


def _audio_files_under(
    root: Path,
    ignore_policy: IgnorePolicy | None = None,
) -> list[Path]:
    if root.is_file():
        return [
            root.resolve()
        ] if (
            _is_audio(root)
            and not _is_ignored(root, ignore_policy, is_dir=False)
        ) else []

    files = []
    for current, dirs, names in root.walk():
        dirs[:] = [
            name for name in dirs
            if not _is_ignored(current / name, ignore_policy, is_dir=True)
        ]
        for name in names:
            candidate = current / name
            if (
                _is_audio(candidate)
                and not _is_ignored(candidate, ignore_policy, is_dir=False)
            ):
                files.append(candidate.resolve())
    return sorted(files)


def _files_under(
    root: Path,
    ignore_policy: IgnorePolicy | None = None,
) -> list[Path]:
    if root.is_file():
        return [
            root.resolve()
        ] if not _is_ignored(root, ignore_policy, is_dir=False) else []

    files = []
    for current, dirs, names in root.walk():
        dirs[:] = [
            name for name in dirs
            if not _is_ignored(current / name, ignore_policy, is_dir=True)
        ]
        for name in names:
            candidate = current / name
            if not _is_ignored(candidate, ignore_policy, is_dir=False):
                files.append(candidate.resolve())
    return sorted(files)


def _direct_audio_files(
    path: Path,
    ignore_policy: IgnorePolicy | None = None,
) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(
        p.resolve()
        for p in path.iterdir()
        if (
            p.is_file()
            and _is_audio(p)
            and not _is_ignored(p, ignore_policy, is_dir=False)
        )
    )


def _direct_files(
    path: Path,
    ignore_policy: IgnorePolicy | None = None,
) -> list[Path]:
    if path.is_file():
        return [
            path.resolve()
        ] if not _is_ignored(path, ignore_policy, is_dir=False) else []
    return sorted(
        p.resolve()
        for p in path.iterdir()
        if p.is_file() and not _is_ignored(p, ignore_policy, is_dir=False)
    )


def _cue_files(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(p.resolve() for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".cue")


def _eac_logs(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(
        p.resolve()
        for p in path.iterdir()
        if p.is_file() and _is_eac_log(p)
    )


def _is_eac_log(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() == ".log" and ("eac" in name or name != "accuraterip.log")


def _nearest_parent_album(path: Path, albums: set[Path]) -> Path | None:
    for parent in path.parents:
        if parent in albums:
            return parent
    return None


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return path.parts


def _album_layout_parts(rel_parts: tuple[str, ...]) -> tuple[str, ...]:
    """Return album path parts after an optional media-kind subtree prefix."""
    if rel_parts and rel_parts[0] in MEDIA_KIND_SUBTREES.values():
        return rel_parts[1:]
    return rel_parts


def _missing_required_tags(
    tags: Tags,
    required: tuple[str, ...] = _MUSIC_REQUIRED,
) -> list[str]:
    fields = {
        "artist": tags.artist,
        "album": tags.album,
        "title": tags.title,
        "tracknumber": tags.track_number,
        "date": tags.year,
    }
    return [name for name in required if not fields.get(name)]


def _title_from_filename(path: Path) -> str:
    stem = path.stem
    match = re.match(r"^(?:\d+-)?\d+\s+(.+)$", stem)
    if match:
        return match.group(1)
    match = re.match(r"^\d+-\d+\s+(.+)$", stem)
    if match:
        return match.group(1)
    return stem


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _duration(path: Path, audit_index: _AuditIndex | None = None) -> float | None:
    if audit_index is not None:
        return audit_index.duration(path)
    return _duration_cached(str(path.resolve()))


@lru_cache(maxsize=None)
def _duration_cached(path: str) -> float | None:
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None or audio.info is None:
        return None
    length = getattr(audio.info, "length", None)
    if length is None:
        return None
    return round(float(length), 3)


def _tags_to_dict(tags: Tags) -> dict[str, Any]:
    return {
        "title": tags.title,
        "artist": tags.artist,
        "album": tags.album,
        "album_artist": tags.album_artist,
        "year": tags.year,
        "track_number": tags.track_number,
        "genre": tags.genre,
    }


def _tags_from_index_row(row: sqlite3.Row) -> Tags:
    return Tags(
        title=row["title"],
        artist=row["artist"],
        album=row["album"],
        album_artist=row["album_artist"],
        year=_year_from_index_date(row["date"]),
        track_number=row["track_number"],
        genre=row["genre"],
    )


def _year_from_index_date(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


def _safe_read_tags(
    path: Path,
    audit_index: _AuditIndex | None = None,
) -> Tags:
    if audit_index is not None:
        return audit_index.tags(path)
    return _safe_read_tags_cached(str(path.resolve()))


@lru_cache(maxsize=None)
def _safe_read_tags_cached(path: str) -> Tags:
    try:
        return read_tags(path)
    except Exception:
        return Tags()


def _consensus_year(
    album_dir: Path,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> int | None:
    """Return the year if all tracks agree on exactly one non-None year."""
    tracks = _direct_audio_files(album_dir, ignore_policy)
    years: set[int] = set()
    for track in tracks:
        tags = _safe_read_tags(track, audit_index)
        if tags.year is not None:
            years.add(tags.year)
    if len(years) == 1:
        return years.pop()
    return None


def _tags_from_dict(values: dict[str, Any]) -> Tags:
    allowed = {"title", "artist", "album", "album_artist", "year", "track_number", "genre"}
    unknown = set(values) - allowed
    if unknown:
        raise ValidationError(f"unsupported tag fields: {', '.join(sorted(unknown))}")
    return Tags(
        title=_optional_str(values.get("title")),
        artist=_optional_str(values.get("artist")),
        album=_optional_str(values.get("album")),
        album_artist=_optional_str(values.get("album_artist")),
        year=_optional_int(values.get("year")),
        track_number=_optional_int(values.get("track_number")),
        genre=_optional_str(values.get("genre")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValidationError(f"tag value must be an integer: {value}") from e


def _coerce_paths(value: Any, index: int) -> list[Path]:
    if isinstance(value, str):
        return [Path(value).resolve(strict=False)]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [Path(v).resolve(strict=False) for v in value]
    raise ValidationError(f"action {index} requires path, paths, or source")


def _coerce_source_roots(value: Any, library_root: Path) -> tuple[Path, ...]:
    if value is None:
        return (library_root,)
    if isinstance(value, str):
        raw_roots = [value]
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        raw_roots = value
    else:
        raise ValidationError("source_roots must be a string or list of strings")

    roots = [library_root]
    for raw_root in raw_roots:
        root = Path(raw_root).resolve(strict=False)
        if not root.exists():
            raise PathNotFoundError(f"source_root does not exist: {root}")
        roots.append(root)
    return tuple(dict.fromkeys(roots))


def _validate_source(source: Path, library_root: Path, index: int) -> None:
    _validate_inside(source, library_root, f"action {index} source")
    if not source.exists():
        raise PathNotFoundError(f"action {index} source does not exist: {source}")


def _validate_source_in_roots(
    source: Path,
    source_roots: tuple[Path, ...],
    index: int,
) -> None:
    if not source.exists():
        raise PathNotFoundError(f"action {index} source does not exist: {source}")
    if any(_path_is_self_or_descendant(source, root) for root in source_roots):
        return
    roots = ", ".join(str(root) for root in source_roots)
    raise ValidationError(
        f"action {index} source must stay inside one of: {roots}: {source}"
    )


def _validate_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as e:
        raise ValidationError(f"{label} must stay inside {root}: {path}") from e


def _is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def _is_auxiliary(path: Path) -> bool:
    return path.suffix.lower() in AUXILIARY_SUFFIXES


def _is_ignored(
    path: Path,
    ignore_policy: IgnorePolicy | None,
    *,
    is_dir: bool,
) -> bool:
    if ignore_policy is not None:
        return ignore_policy.is_ignored(path, is_dir=is_dir)
    return is_dir and _is_ignored_dir(path)


def _is_ignored_dir(path: Path) -> bool:
    return path.name in {".git", ".hg", "__pycache__", "_quarantine"}


def _has_ignored_parent(path: Path) -> bool:
    return any(_is_ignored_dir(parent) for parent in path.parents)


def _filter_findings(findings: list[Finding], min_severity: str) -> list[Finding]:
    threshold = SEVERITIES[min_severity]
    return [f for f in findings if SEVERITIES[f.severity] >= threshold]


def _validate_output_format(output_format: str) -> None:
    if output_format not in OUTPUT_FORMATS:
        raise ValidationError(
            f"unknown output format: {output_format} "
            f"(available: {', '.join(sorted(OUTPUT_FORMATS))})"
        )


def _validate_severity(severity: str) -> None:
    if severity not in SEVERITIES:
        raise ValidationError(
            f"unknown severity: {severity} "
            f"(available: {', '.join(sorted(SEVERITIES))})"
        )


def _finding(
    category: str,
    severity: str,
    paths: list[Path],
    message: str,
    evidence: dict[str, Any],
    *,
    confidence: float,
    fixable: bool,
    suggested_actions: list[dict[str, Any]] | None = None,
) -> Finding:
    path_strings = [str(p.resolve()) for p in paths]
    payload = {
        "category": category,
        "paths": path_strings,
        "evidence": evidence,
    }
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:8]
    return Finding(
        finding_id=f"{category}:{digest}",
        severity=severity,
        category=category,
        paths=path_strings,
        message=message,
        evidence=evidence,
        confidence=round(confidence, 2),
        fixable=fixable,
        suggested_actions=suggested_actions or [],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _json_line(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True)
