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
FIX_PLAN_ACTIONS = {
    "ignore_finding",
    "move_file",
    "quarantine",
    "rename_album_dir",
    "rename_track_file",
    "write_tags",
}

TRACK_FILENAME_RE = re.compile(r"^(?P<number>\d{2})\s+(?P<title>.+)\.[^.]+$")
ALBUM_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)$")

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


def audit_library(
    path: Path,
    *,
    against: Path | None = None,
    min_severity: str = "info",
    include_ok: bool = False,
    index_conn: sqlite3.Connection | None = None,
    excludes: list[str] | None = None,
    similarity_threshold: float = 0.08,
) -> AuditResult:
    """Audit a library or staging path without modifying files."""
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
    for album_dir in album_dirs:
        rel_parts = _relative_parts(album_dir, root)
        if include_warning_findings:
            _audit_album_structure(
                root, album_dir, rel_parts, findings,
                audit_index=audit_index,
                ignore_policy=ignore_policy,
            )
        if include_info_findings:
            _audit_album_provenance(album_dir, findings)
        if include_warning_findings or include_info_findings:
            _audit_tracks(
                album_dir,
                findings,
                include_warnings=include_warning_findings,
                include_info=include_info_findings,
                audit_index=audit_index,
                ignore_policy=ignore_policy,
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

    summary = {
        "albums_scanned": len(album_dirs),
        "tracks_scanned": len(audio_files),
        "findings": len(filtered),
        "by_severity": by_severity,
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

    qdir = (
        quarantine_dir.resolve(strict=False)
        if quarantine_dir is not None
        else (library_root / "_quarantine").resolve(strict=False)
    )

    actions_value = plan.get("actions")
    if not isinstance(actions_value, list):
        raise ValidationError("fix plan actions must be a list")

    action_results: list[ApplyActionResult] = []
    for index, raw_action in enumerate(actions_value, 1):
        action = _validate_action(raw_action, index, library_root, qdir)
        warnings = _action_warnings(action, db_conn)
        db_updates = None
        if not dry_run:
            _execute_action(action)
            db_updates = _relocate_action_paths(action, db_conn)
        status = "would_apply" if dry_run and action["type"] != "ignore_finding" else "applied"
        if dry_run and action["type"] == "ignore_finding":
            status = "would_ignore"
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
        rel_parts = _relative_parts(album_dir, root)
        if len(rel_parts) > 2:
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

        action: dict[str, Any] = {
            "action_id": f"rename_album_dir:{len(actions) + 1}",
            "type": "rename_album_dir",
            "because_finding": "structure.album_year_missing",
            "source": str(album_dir),
            "destination": str(destination),
            "reason": f'Rename to "{suggested_name}" (tag year).',
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

    lines = [result.root, ""]
    if not result.findings:
        lines.append("No findings.")
    else:
        for finding in result.findings:
            lines.append(f"  {finding.severity:<7s}  {finding.category}")
            for p in finding.paths:
                lines.append(f"           {p}")
            lines.append(f"           {finding.message}")
            lines.append("")
        lines.append(f"{len(result.findings)} findings")
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
    lines = [
        f"Propose: {result.proposal_type} "
        f"({len(result.actions)} rename(s), {len(result.skipped)} skipped)",
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
        lines.append(f"  {src_rel}  \u2192  {dst_rel}{tape_suffix}")
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


def _audit_album_structure(
    root: Path,
    album_dir: Path,
    rel_parts: tuple[str, ...],
    findings: list[Finding],
    *,
    audit_index: _AuditIndex | None = None,
    ignore_policy: IgnorePolicy | None = None,
) -> None:
    if len(rel_parts) > 2:
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


def _audit_album_provenance(album_dir: Path, findings: list[Finding]) -> None:
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
) -> None:
    for track in _direct_audio_files(album_dir, ignore_policy):
        tags = _safe_read_tags(track, audit_index) if include_warnings else Tags()
        if include_warnings:
            missing = _missing_required_tags(tags)
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

        match = TRACK_FILENAME_RE.match(track.name)
        if include_info and match is None:
            findings.append(_finding(
                "tracks.filename_pattern",
                "info",
                [track],
                'Filename does not follow "NN Title.ext".',
                {"filename": track.name, "expected_pattern": "NN Title.ext"},
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

        file_track_number = int(match.group("number"))
        if include_warnings and tags.track_number is not None and tags.track_number != file_track_number:
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


def _validate_action(
    raw_action: Any,
    index: int,
    library_root: Path,
    quarantine_dir: Path,
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

    if action_type in {"rename_track_file", "move_file"} and not source.is_file():
        raise ValidationError(f"action {index} source must be a file: {source}")
    if action_type == "rename_album_dir" and not source.is_dir():
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
    shutil.move(str(source), str(destination))


def _relocate_action_paths(
    action: dict[str, Any],
    conn: sqlite3.Connection | None,
) -> dict[str, int] | None:
    """Update DB path references after a filesystem move."""
    if conn is None:
        return None
    if action["type"] in {"ignore_finding", "write_tags"}:
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
    if conn is not None and source:
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
        if p.is_file()
        and p.suffix.lower() == ".log"
        and ("eac" in p.name.lower() or p.name.lower() != "accuraterip.log")
    )


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


def _missing_required_tags(tags: Tags) -> list[str]:
    missing = []
    if not tags.artist:
        missing.append("artist")
    if not tags.album:
        missing.append("album")
    if not tags.title:
        missing.append("title")
    if tags.track_number is None:
        missing.append("tracknumber")
    if tags.year is None:
        missing.append("date")
    return missing


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


def _validate_source(source: Path, library_root: Path, index: int) -> None:
    _validate_inside(source, library_root, f"action {index} source")
    if not source.exists():
        raise PathNotFoundError(f"action {index} source does not exist: {source}")


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
