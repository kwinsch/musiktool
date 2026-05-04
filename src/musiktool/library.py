"""Agent-facing library audit, inspection, and fix-plan execution."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import mutagen

from musiktool.constants import AUDIO_EXTENSIONS
from musiktool.exceptions import PathNotFoundError, ValidationError
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


def audit_library(
    path: Path,
    *,
    against: Path | None = None,
    min_severity: str = "info",
    include_ok: bool = False,
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

    album_dirs = _discover_album_dirs(root)
    audio_files = _audio_files_under(root)
    direct_root_files = [p for p in _direct_files(root) if not _is_audio(p)]

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
            _audit_album_structure(root, album_dir, rel_parts, findings)
        if include_info_findings:
            _audit_album_provenance(album_dir, findings)
        if include_warning_findings or include_info_findings:
            _audit_tracks(
                album_dir,
                findings,
                include_warnings=include_warning_findings,
                include_info=include_info_findings,
            )

        if include_warning_findings:
            parent_album = _nearest_parent_album(album_dir, album_set)
            if parent_album is not None:
                findings.append(_nested_album_finding(parent_album, album_dir))

    if include_warning_findings:
        findings.extend(_duplicate_findings(album_dirs, "duplicates.same_album_candidate"))

    if against is not None and include_warning_findings:
        against_root = against.resolve()
        if not against_root.exists():
            raise PathNotFoundError(f"--against path does not exist: {against_root}")
        findings.extend(_against_findings(album_dirs, _discover_album_dirs(against_root)))

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

    return AuditResult(
        root=str(root),
        generated_at=_now(),
        summary={
            "albums_scanned": len(album_dirs),
            "tracks_scanned": len(audio_files),
            "findings": len(filtered),
            "by_severity": by_severity,
        },
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
        if not dry_run:
            _execute_action(action)
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
        ))

    return ApplyResult(
        plan_path=label,
        dry_run=dry_run,
        executed=not dry_run,
        valid=True,
        actions=action_results,
        errors=[],
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
    return "\n".join(lines) + "\n"


def _audit_album_structure(
    root: Path,
    album_dir: Path,
    rel_parts: tuple[str, ...],
    findings: list[Finding],
) -> None:
    if len(rel_parts) > 2:
        return

    year_match = ALBUM_YEAR_RE.search(album_dir.name)
    if year_match is None:
        findings.append(_finding(
            "structure.album_year_missing",
            "warning",
            [album_dir],
            'Album directory does not end in "(Year)".',
            {
                "album_name": album_dir.name,
                "expected_pattern": "Album (Year)",
                "relative_depth": len(rel_parts),
            },
            confidence=1.0,
            fixable=True,
            suggested_actions=[{
                "type": "rename_album_dir",
                "source": str(album_dir),
                "reason": "Add a release year once verified from tags or external metadata.",
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
) -> None:
    for track in _direct_audio_files(album_dir):
        tags = _safe_read_tags(track) if include_warnings else Tags()
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


def _nested_album_finding(parent: Path, nested: Path) -> Finding:
    parent_summary = _album_summary(parent)
    nested_summary = _album_summary(nested)
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


def _duplicate_findings(album_dirs: list[Path], category: str) -> list[Finding]:
    groups: dict[tuple[Any, ...], list[Path]] = {}
    for album_dir in album_dirs:
        signature = _album_signature(album_dir)
        if signature is None:
            continue
        groups.setdefault(signature, []).append(album_dir)

    findings = []
    for albums in groups.values():
        if len(albums) < 2:
            continue
        preferred = _preferred_album(albums)
        summaries = [_album_summary(p) for p in albums]
        findings.append(_finding(
            category,
            "warning",
            albums,
            "Multiple album directories appear to contain the same album.",
            {
                "albums": summaries,
                "preferred_path": str(preferred),
            },
            confidence=0.9,
            fixable=True,
            suggested_actions=[
                {
                    "type": "quarantine",
                    "source": str(p),
                    "reason": f"Preferred copy appears to be {preferred}.",
                }
                for p in albums if p != preferred
            ],
        ))
    return findings


def _against_findings(source_albums: list[Path], curated_albums: list[Path]) -> list[Finding]:
    curated_by_signature: dict[tuple[Any, ...], list[Path]] = {}
    for album in curated_albums:
        signature = _album_signature(album)
        if signature is not None:
            curated_by_signature.setdefault(signature, []).append(album)

    findings = []
    for source in source_albums:
        signature = _album_signature(source)
        if signature is None or signature not in curated_by_signature:
            continue
        curated = _preferred_album(curated_by_signature[signature])
        findings.append(_finding(
            "duplicates.source_already_curated",
            "warning",
            [source, curated],
            "Source album appears to duplicate an existing curated album.",
            {
                "source": _album_summary(source),
                "curated": _album_summary(curated),
                "preferred_path": str(curated),
            },
            confidence=0.88,
            fixable=True,
            suggested_actions=[{
                "type": "quarantine",
                "source": str(source),
                "reason": "Album appears to already exist in the curated library.",
            }],
        ))
    return findings


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


def _track_metadata(path: Path) -> dict[str, Any]:
    tags = _safe_read_tags(path)
    return {
        "path": str(path),
        "filename": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "duration_sec": _duration(path),
        "tags": _tags_to_dict(tags),
    }


def _album_summary(path: Path) -> dict[str, Any]:
    tracks = _direct_audio_files(path)
    durations = [_duration(p) for p in tracks]
    known_durations = [d for d in durations if d is not None]
    return {
        "path": str(path),
        "track_count": len(tracks),
        "duration_sec": round(sum(known_durations), 3) if known_durations else None,
        "formats": sorted({p.suffix.lower().lstrip(".") for p in tracks}),
        "has_cue": bool(_cue_files(path)),
        "has_eac_log": bool(_eac_logs(path)),
        "format_score": _album_format_score(path),
    }


def _album_signature(path: Path) -> tuple[Any, ...] | None:
    tracks = _direct_audio_files(path)
    if not tracks:
        return None
    titles = []
    for track in tracks:
        tags = _safe_read_tags(track)
        title = tags.title or _title_from_filename(track)
        titles.append(_normalize_text(title))
    if not any(titles):
        return None
    return (len(tracks), tuple(titles))


def _preferred_album(albums: list[Path]) -> Path:
    return sorted(
        albums,
        key=lambda p: (
            -_album_format_score(p),
            -int(bool(_eac_logs(p))),
            -int(bool(_cue_files(p))),
            len(p.parts),
            str(p),
        ),
    )[0]


def _album_format_score(path: Path) -> int:
    scores = [FORMAT_SCORES.get(p.suffix.lower(), 0) for p in _direct_audio_files(path)]
    return max(scores) if scores else 0


def _duration_delta(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    left_duration = left.get("duration_sec")
    right_duration = right.get("duration_sec")
    if left_duration is None or right_duration is None:
        return None
    return round(abs(float(left_duration) - float(right_duration)), 3)


def _discover_album_dirs(root: Path) -> list[Path]:
    if root.is_file():
        return [root.parent.resolve()] if _is_audio(root) else []

    root = root.resolve()
    albums = []
    if _direct_audio_files(root):
        albums.append(root)

    for p in sorted(root.rglob("*")):
        if not p.is_dir() or _is_ignored_dir(p) or _has_ignored_parent(p):
            continue
        if _direct_audio_files(p):
            albums.append(p.resolve())
    return sorted(set(albums))


def _audio_files_under(root: Path) -> list[Path]:
    if root.is_file():
        return [root.resolve()] if _is_audio(root) else []
    return sorted(
        p.resolve()
        for p in root.rglob("*")
        if p.is_file() and _is_audio(p) and not _has_ignored_parent(p)
    )


def _direct_audio_files(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(p.resolve() for p in path.iterdir() if p.is_file() and _is_audio(p))


def _direct_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path.resolve()]
    return sorted(p.resolve() for p in path.iterdir() if p.is_file())


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


def _duration(path: Path) -> float | None:
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


def _safe_read_tags(path: Path) -> Tags:
    return _safe_read_tags_cached(str(path.resolve()))


@lru_cache(maxsize=None)
def _safe_read_tags_cached(path: str) -> Tags:
    try:
        return read_tags(path)
    except Exception:
        return Tags()


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
