"""Tape project management — business logic layer."""

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from musiktool import db
from musiktool.constants import MEDIUM_PRESETS
from musiktool.loudness import compute_gain, compute_gain_with_limiter
from musiktool.exceptions import (
    AudioReadError,
    InvalidMediumError,
    InvalidPositionError,
    MissingLoudnessError,
    PathNotFoundError,
    ProjectNotFoundError,
    ValidationError,
)

CAPACITY_WARNING_GRACE_SEC = 60.0


def create_project(
    conn: sqlite3.Connection,
    name: str,
    medium: str,
    *,
    duration_min: float | None = None,
    lead_in_sec: float = 25,
    lead_out_sec: float = 20,
    album_gap_sec: float = 8,
    track_gap_sec: float = 4,
    marker: str = "none",
    marker_freq: float = 400,
    marker_level_dbfs: float = -30,
    marker_duration_sec: float = 0.5,
    target_lufs: float = -14,
    peak_ceiling_dbtp: float | None = None,
    sample_rate: str = "auto",
    bit_depth: int = 24,
    use_limiter: bool = False,
    use_compressor: bool = False,
) -> int:
    """Create a tape project. Returns project id."""
    if marker not in ("none", "tone"):
        raise InvalidMediumError(f"marker must be 'none' or 'tone': {marker}")
    if bit_depth not in (16, 24):
        raise InvalidMediumError(f"bit_depth must be 16 or 24: {bit_depth}")

    if medium == "custom":
        if duration_min is None:
            raise InvalidMediumError("--duration required for custom medium")
        duration_sec = duration_min * 60
        side_a_duration_sec = None
        side_b_duration_sec = None
        if peak_ceiling_dbtp is None:
            peak_ceiling_dbtp = -1.0
    else:
        if medium not in MEDIUM_PRESETS:
            raise InvalidMediumError(
                f"unknown medium: {medium} "
                f"(available: {', '.join(sorted(MEDIUM_PRESETS))})"
            )
        preset = MEDIUM_PRESETS[medium]
        duration_sec = preset["duration_min"] * 60
        if preset.get("sides", 1) == 2:
            side_a_duration_sec = preset["side_a_min"] * 60
            side_b_duration_sec = preset["side_b_min"] * 60
        else:
            side_a_duration_sec = None
            side_b_duration_sec = None
        if peak_ceiling_dbtp is None:
            peak_ceiling_dbtp = -1.0 if preset["peak_behavior"] == "hard" else 0.0

    now = datetime.now(timezone.utc).isoformat()
    return db.create_tape_project(
        conn,
        name=name,
        medium=medium,
        duration_sec=duration_sec,
        lead_in_sec=lead_in_sec,
        lead_out_sec=lead_out_sec,
        album_gap_sec=album_gap_sec,
        track_gap_sec=track_gap_sec,
        marker=marker,
        marker_freq=marker_freq,
        marker_level_dbfs=marker_level_dbfs,
        marker_duration_sec=marker_duration_sec,
        target_lufs=target_lufs,
        peak_ceiling_dbtp=peak_ceiling_dbtp,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        use_limiter=use_limiter,
        use_compressor=use_compressor,
        side_a_duration_sec=side_a_duration_sec,
        side_b_duration_sec=side_b_duration_sec,
        created_at=now,
    )


def get_project(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    """Get project by name. Raises if not found."""
    row = db.get_tape_project(conn, name)
    if row is None:
        raise ProjectNotFoundError(f"tape project not found: {name}")
    return row


def list_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """List all tape projects."""
    return db.list_tape_projects(conn)


def delete_project(conn: sqlite3.Connection, name: str) -> None:
    """Delete a tape project and all its items."""
    get_project(conn, name)  # asserts existence
    db.delete_tape_project(conn, name)


def get_items(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    """Get all items for a project, ordered by position."""
    return db.get_tape_items(conn, project_id)


def resolve_path(path_str: str) -> tuple[str, str]:
    """Resolve a path to (canonical_path, item_type).

    Directories → album, files → track.
    """
    p = Path(path_str).resolve()
    if not p.exists():
        raise PathNotFoundError(f"path does not exist: {p}")
    if p.is_dir():
        return str(p), "album"
    else:
        return str(p), "track"


def validate_loudness(
    conn: sqlite3.Connection, path: str, item_type: str,
) -> sqlite3.Row:
    """Validate that loudness data exists for a path. Returns the row."""
    if item_type == "album":
        row = conn.execute(
            "SELECT * FROM album_loudness WHERE path = ?", (path,),
        ).fetchone()
        if row is None:
            raise MissingLoudnessError(
                f"no loudness data for album: {path}\n"
                f"  Run: musiktool analyze \"{path}\""
            )
    else:
        row = db.get_track(conn, path)
        if row is None:
            raise MissingLoudnessError(
                f"no loudness data for track: {path}\n"
                f"  Run: musiktool analyze \"{Path(path).parent}\""
            )
    return row


def add_items(
    conn: sqlite3.Connection,
    project_id: int,
    paths: list[str],
    *,
    side: str | None = None,
) -> list[tuple[str, str]]:
    """Append items to a project. Returns list of (path, item_type) added."""
    existing = db.get_tape_items(conn, project_id)
    next_pos = (existing[-1]["position"] + 1) if existing else 1

    added = []
    for path_str in paths:
        canonical, item_type = resolve_path(path_str)
        validate_loudness(conn, canonical, item_type)
        db.add_tape_item(
            conn,
            project_id=project_id,
            position=next_pos,
            item_type=item_type,
            path=canonical,
            side=side,
        )
        added.append((canonical, item_type))
        next_pos += 1

    conn.commit()
    return added


def insert_items(
    conn: sqlite3.Connection,
    project_id: int,
    position: int,
    paths: list[str],
    *,
    side: str | None = None,
) -> list[tuple[str, str]]:
    """Insert items at a position. Shifts existing items down."""
    existing = db.get_tape_items(conn, project_id)
    max_pos = len(existing) + 1
    if not (1 <= position <= max_pos):
        raise InvalidPositionError(
            f"position {position} out of range (1-{max_pos})"
        )

    # Resolve and validate all paths before modifying anything
    resolved = []
    for path_str in paths:
        canonical, item_type = resolve_path(path_str)
        validate_loudness(conn, canonical, item_type)
        resolved.append((canonical, item_type))

    # Rebuild positions: items before insert point keep position,
    # new items get inserted, items at/after shift down
    items_before = [row for row in existing if row["position"] < position]
    items_after = [row for row in existing if row["position"] >= position]

    # Clear all positions to avoid UNIQUE constraint violations during rewrite
    for row in existing:
        # Set to negative temp positions
        db.update_tape_item_position(conn, row["id"], -(row["id"]))

    # Write items before insert point
    pos = 1
    for row in items_before:
        db.update_tape_item_position(conn, row["id"], pos)
        pos += 1

    # Insert new items
    for canonical, item_type in resolved:
        db.add_tape_item(
            conn,
            project_id=project_id,
            position=pos,
            item_type=item_type,
            path=canonical,
            side=side,
        )
        pos += 1

    # Write items after insert point
    for row in items_after:
        db.update_tape_item_position(conn, row["id"], pos)
        pos += 1

    conn.commit()
    return resolved


def remove_items(
    conn: sqlite3.Connection,
    project_id: int,
    positions: list[int],
) -> int:
    """Remove items by position. Renumbers remaining. Returns count removed."""
    existing = db.get_tape_items(conn, project_id)
    pos_set = set(positions)

    for pos in pos_set:
        if not any(row["position"] == pos for row in existing):
            raise InvalidPositionError(f"no item at position {pos}")

    to_keep = [row for row in existing if row["position"] not in pos_set]
    to_delete = [row for row in existing if row["position"] in pos_set]

    for row in to_delete:
        db.delete_tape_item(conn, row["id"])

    # Renumber remaining
    for i, row in enumerate(to_keep, 1):
        if row["position"] != i:
            db.update_tape_item_position(conn, row["id"], i)

    conn.commit()
    return len(to_delete)


def move_item(
    conn: sqlite3.Connection,
    project_id: int,
    from_pos: int,
    to_pos: int,
    *,
    side: str | None = ...,
) -> None:
    """Move an item from one position to another.

    side: 'a'/'b' pins to side, None clears pin, ... (default) leaves unchanged.
    """
    existing = db.get_tape_items(conn, project_id)
    if not (1 <= from_pos <= len(existing)):
        raise InvalidPositionError(
            f"from position {from_pos} out of range (1-{len(existing)})"
        )
    if not (1 <= to_pos <= len(existing)):
        raise InvalidPositionError(
            f"to position {to_pos} out of range (1-{len(existing)})"
        )

    # Update side pin if requested
    if side is not ...:
        item_row = next(r for r in existing if r["position"] == from_pos)
        db.update_tape_item_side(conn, item_row["id"], side)

    if from_pos == to_pos:
        conn.commit()
        return

    # Work with a mutable list of (id, position, ...) tuples
    items = list(existing)
    item = items.pop(from_pos - 1)
    items.insert(to_pos - 1, item)

    # Clear positions to avoid UNIQUE conflicts
    for row in existing:
        db.update_tape_item_position(conn, row["id"], -(row["id"]))

    # Renumber
    for i, row in enumerate(items, 1):
        db.update_tape_item_position(conn, row["id"], i)

    conn.commit()


def compute_gap(
    prev_type: str | None,
    curr_type: str,
    project: sqlite3.Row,
) -> float:
    """Determine gap duration between two adjacent items.

    If either item is an album, use album_gap. Both tracks → track_gap.
    No gap before the first item (prev_type is None).
    """
    if prev_type is None:
        return 0.0
    if prev_type == "track" and curr_type == "track":
        return project["track_gap_sec"]
    return project["album_gap_sec"]


def format_duration(seconds: float) -> str:
    """Format seconds as 'm:ss' or 'h:mm:ss'."""
    total = int(round(seconds))
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _exceeds_capacity_warning_grace(total_sec: float, capacity_sec: float) -> bool:
    """Return true when duration is beyond the nominal capacity grace window."""
    return total_sec - capacity_sec > CAPACITY_WARNING_GRACE_SEC


def _remaining_exceeds_capacity_warning_grace(remaining_sec: float) -> bool:
    """Return true when negative remaining time is beyond the grace window."""
    return remaining_sec < -CAPACITY_WARNING_GRACE_SEC


def _capacity_status(total_sec: float, capacity_sec: float) -> str:
    """Classify duration against nominal capacity plus a small media grace window."""
    if _exceeds_capacity_warning_grace(total_sec, capacity_sec):
        return "OVER"
    if total_sec > capacity_sec:
        return "grace"
    return "fits"


# --- Layout dataclasses ---


@dataclass
class LayoutItem:
    """A single item in a project layout with computed display data."""

    position: int
    item_type: str
    path: str
    label: str
    duration_sec: float
    track_count: int
    gap_before_sec: float
    lufs: float | None
    peak: float | None
    lra: float | None
    track_range: tuple[int, int] | None = None


@dataclass
class SplitAlbumInfo:
    """Info about an album that was split across sides."""

    path: str
    label: str
    split_after: int
    total_tracks: int


@dataclass
class SideLayout:
    """Layout for one side of a two-sided medium."""

    items: list[LayoutItem]
    content_sec: float
    overhead_sec: float
    overhead_parts: list[str]
    total_sec: float
    remaining_sec: float
    capacity_sec: float


@dataclass
class ProjectLayout:
    """Full project layout with durations, gaps, and optional side info."""

    items: list[LayoutItem]
    content_sec: float
    overhead_sec: float
    overhead_parts: list[str]
    total_sec: float
    remaining_sec: float
    # Two-sided only (None for single-sided)
    side_a: SideLayout | None = None
    side_b: SideLayout | None = None
    split_albums: list[SplitAlbumInfo] | None = None

    @property
    def is_two_sided(self) -> bool:
        return self.side_a is not None


def _item_label(item: sqlite3.Row) -> str:
    """Build a display label for a tape item from its path."""
    p = Path(item["path"])
    if item["item_type"] == "album":
        # Artist / Album (Year)
        return f"{p.parent.name} / {p.name}"
    else:
        # Artist / Track filename (without extension)
        return f"{p.parent.parent.name} / {p.stem}"


def _get_loudness_for_item(
    conn: sqlite3.Connection, item: sqlite3.Row,
) -> sqlite3.Row | None:
    """Look up loudness data for a tape item."""
    if item["item_type"] == "album":
        return conn.execute(
            "SELECT * FROM album_loudness WHERE path = ?",
            (item["path"],),
        ).fetchone()
    else:
        return db.get_track(conn, item["path"])


def _build_side_items(
    conn: sqlite3.Connection,
    db_items: list[sqlite3.Row],
) -> list["SideItem"]:
    """Build SideItem list with track durations from DB."""
    result = []
    for item in db_items:
        loudness = _get_loudness_for_item(conn, item)
        duration = loudness["duration_sec"] if loudness else 0.0

        track_durations = None
        if item["item_type"] == "album":
            track_rows = db.get_tracks_for_album(conn, item["path"])
            if track_rows:
                track_durations = [r["duration_sec"] for r in track_rows]

        result.append(SideItem(
            position=item["position"],
            item_type=item["item_type"],
            path=item["path"],
            duration_sec=duration,
            pinned_side=item["side"],
            track_durations=track_durations,
        ))
    return result


def _compute_side_layout(
    items: list[LayoutItem],
    project: sqlite3.Row,
    capacity_sec: float,
) -> SideLayout:
    """Compute layout for one side (items already assigned)."""
    content_sec = 0.0
    gaps = []
    prev_type = None

    for it in items:
        gap = compute_gap(prev_type, it.item_type, project)
        gaps.append(gap)
        it.gap_before_sec = gap
        content_sec += it.duration_sec
        prev_type = it.item_type

    total_gaps = sum(gaps)
    lead_in = project["lead_in_sec"]
    lead_out = project["lead_out_sec"]
    overhead_sec = lead_in + lead_out + total_gaps

    overhead_parts = [f"{lead_in:.0f}s lead-in"]
    for gap in gaps:
        if gap > 0:
            overhead_parts.append(f"{gap:.0f}s gap")
    overhead_parts.append(f"{lead_out:.0f}s lead-out")

    total_sec = content_sec + overhead_sec
    remaining_sec = capacity_sec - total_sec

    return SideLayout(
        items=items,
        content_sec=content_sec,
        overhead_sec=overhead_sec,
        overhead_parts=overhead_parts,
        total_sec=total_sec,
        remaining_sec=remaining_sec,
        capacity_sec=capacity_sec,
    )


def _make_layout_item(
    conn: sqlite3.Connection,
    item: sqlite3.Row,
    *,
    label_suffix: str = "",
    track_range: tuple[int, int] | None = None,
) -> LayoutItem:
    """Build a LayoutItem for layout display."""
    loudness = _get_loudness_for_item(conn, item)
    duration = loudness["duration_sec"] if loudness else 0.0
    track_count = loudness["track_count"] if loudness and item["item_type"] == "album" else 1

    if track_range is not None and item["item_type"] == "album":
        # Split album — recalculate duration and track count from track range
        track_rows = db.get_tracks_for_album(conn, item["path"])
        if track_rows:
            start, end = track_range
            duration = sum(r["duration_sec"] for r in track_rows[start:end])
            track_count = end - start

    label = _item_label(item) + label_suffix

    return LayoutItem(
        position=item["position"],
        item_type=item["item_type"],
        path=item["path"],
        label=label,
        duration_sec=duration,
        track_count=track_count,
        gap_before_sec=0.0,  # set by _compute_side_layout
        lufs=loudness["integrated_lufs"] if loudness else None,
        peak=loudness["true_peak_dbtp"] if loudness else None,
        lra=loudness["lra"] if loudness else None,
        track_range=track_range,
    )


def compute_project_layout(
    conn: sqlite3.Connection, project: sqlite3.Row,
) -> ProjectLayout:
    """Compute full project layout with durations and gaps."""
    db_items = db.get_tape_items(conn, project["id"])

    if not is_two_sided(project):
        # Single-sided layout
        result_items: list[LayoutItem] = []
        content_sec = 0.0
        gaps = []
        prev_type = None

        for item in db_items:
            it = _make_layout_item(conn, item)
            gap = compute_gap(prev_type, it.item_type, project)
            gaps.append(gap)
            it.gap_before_sec = gap
            result_items.append(it)
            content_sec += it.duration_sec
            prev_type = it.item_type

        total_gaps = sum(gaps)
        lead_in = project["lead_in_sec"]
        lead_out = project["lead_out_sec"]
        overhead_sec = lead_in + lead_out + total_gaps

        overhead_parts = [f"{lead_in:.0f}s lead-in"]
        for gap in gaps:
            if gap > 0:
                overhead_parts.append(f"{gap:.0f}s gap")
        overhead_parts.append(f"{lead_out:.0f}s lead-out")

        total_sec = content_sec + overhead_sec
        remaining_sec = project["duration_sec"] - total_sec

        return ProjectLayout(
            items=result_items,
            content_sec=content_sec,
            overhead_sec=overhead_sec,
            overhead_parts=overhead_parts,
            total_sec=total_sec,
            remaining_sec=remaining_sec,
        )

    # Two-sided layout
    side_items = _build_side_items(conn, db_items)
    assignments = assign_sides(
        side_items,
        project["side_a_duration_sec"],
        project["side_b_duration_sec"],
        project["lead_in_sec"],
        project["lead_out_sec"],
        project["album_gap_sec"],
        project["track_gap_sec"],
    )

    # Build items per side
    items_by_pos = {item["position"]: item for item in db_items}
    side_a_items: list[LayoutItem] = []
    side_b_items: list[LayoutItem] = []
    split_albums: list[SplitAlbumInfo] = []

    for sa in assignments:
        item_row = items_by_pos[sa.position]
        suffix = ""
        if sa.track_range is not None:
            start, end = sa.track_range
            total_tracks = len(side_items[sa.position - 1].track_durations or [])
            suffix = f" [tracks {start + 1}-{end}]"
            if sa.track_range[0] == 0:
                split_albums.append(SplitAlbumInfo(
                    path=sa.path,
                    label=_item_label(item_row),
                    split_after=end,
                    total_tracks=total_tracks,
                ))

        it = _make_layout_item(conn, item_row, label_suffix=suffix, track_range=sa.track_range)

        if sa.side == "a":
            side_a_items.append(it)
        else:
            side_b_items.append(it)

    layout_a = _compute_side_layout(
        side_a_items, project, project["side_a_duration_sec"],
    )
    layout_b = _compute_side_layout(
        side_b_items, project, project["side_b_duration_sec"],
    )

    return ProjectLayout(
        items=layout_a.items + layout_b.items,
        content_sec=layout_a.content_sec + layout_b.content_sec,
        overhead_sec=layout_a.overhead_sec + layout_b.overhead_sec,
        overhead_parts=[],
        total_sec=layout_a.total_sec + layout_b.total_sec,
        remaining_sec=min(layout_a.remaining_sec, layout_b.remaining_sec),
        side_a=layout_a,
        side_b=layout_b,
        split_albums=split_albums,
    )


def _format_side_compact(
    side_layout: SideLayout, *, side_label: str | None = None,
) -> list[str]:
    """Format items table and summary for compact display.

    If side_label is provided, includes a side header (two-sided media).
    """
    lines = []
    if side_label is not None:
        cap = format_duration(side_layout.capacity_sec)
        lines.append(f"  === Side {side_label.upper()} ({cap}) ===")
        lines.append("")

    if not side_layout.items:
        lines.append("  (empty)")
    else:
        lines.append("  Pos  Type   Tracks  Duration  LUFS    Peak   LRA   Item")
        for it in side_layout.items:
            dur = format_duration(it.duration_sec)
            lufs = f"{it.lufs:6.1f}" if it.lufs is not None else "   N/A"
            peak = f"{it.peak:6.1f}" if it.peak is not None else "   N/A"
            lra = f"{it.lra:5.1f}" if it.lra is not None else "  N/A"
            lines.append(
                f"  {it.position:<4d} {it.item_type:<6s} {it.track_count:<7d} {dur:>8s}  {lufs}  {peak}  {lra}   {it.label}"
            )

    lines.append("")

    content = format_duration(side_layout.content_sec)
    overhead = format_duration(side_layout.overhead_sec)
    total = format_duration(side_layout.total_sec)
    remaining = format_duration(abs(side_layout.remaining_sec))
    parts = " + ".join(side_layout.overhead_parts)

    lines.append(f"  Content:  {content:>8s}")
    lines.append(f"  Overhead: {overhead:>8s}  ({parts})")
    lines.append(f"  Total:    {total:>8s}")
    if _remaining_exceeds_capacity_warning_grace(side_layout.remaining_sec):
        lines.append(f"  OVER BY:  {remaining:>8s}  !")
    elif side_layout.remaining_sec < 0:
        grace = format_duration(CAPACITY_WARNING_GRACE_SEC)
        lines.append(f"  Over by:  {remaining:>8s}  (within {grace} grace)")
    else:
        lines.append(f"  Remaining:{remaining:>8s}")

    return lines


def show_compact(conn: sqlite3.Connection, project: sqlite3.Row) -> str:
    """Build compact show output."""
    layout = compute_project_layout(conn, project)
    lines = []

    # Header
    medium_label = project["medium"].upper()
    if is_two_sided(project):
        cap_a = format_duration(project["side_a_duration_sec"])
        cap_b = format_duration(project["side_b_duration_sec"])
        capacity = f"{cap_a} + {cap_b}"
    else:
        capacity = format_duration(project["duration_sec"])
    sr = project["sample_rate"]
    sr_label = f"{sr} Hz" if sr != "auto" else "auto"
    header = (
        f"{project['name']} — {medium_label} ({capacity}) "
        f"— target {project['target_lufs']:.0f} LUFS "
        f"— {sr_label} / {project['bit_depth']}-bit"
    )
    modes = []
    if project["use_limiter"]:
        modes.append("limiter")
    if project["use_compressor"]:
        modes.append("compressor")
    if modes:
        header += f" — {', '.join(modes)}"
    lines.append(header)
    lines.append("")

    if not layout.items:
        lines.append("  (empty)")
        return "\n".join(lines)

    if layout.is_two_sided:
        lines.extend(_format_side_compact(layout.side_a, side_label="A"))
        lines.append("")
        lines.extend(_format_side_compact(layout.side_b, side_label="B"))
    else:
        side = SideLayout(
            items=layout.items,
            content_sec=layout.content_sec,
            overhead_sec=layout.overhead_sec,
            overhead_parts=layout.overhead_parts,
            total_sec=layout.total_sec,
            remaining_sec=layout.remaining_sec,
            capacity_sec=project["duration_sec"],
        )
        lines.extend(_format_side_compact(side))

    return "\n".join(lines)


def _format_side_timeline(
    side_layout: SideLayout,
    project: sqlite3.Row,
    *,
    side_label: str | None = None,
) -> list[str]:
    """Format timeline with timestamps.

    If side_label is provided, includes a side header (two-sided media).
    """
    lines = []
    if side_label is not None:
        cap = format_duration(side_layout.capacity_sec)
        lines.append(f"  --- Side {side_label.upper()} ({cap}) ---")
    lines.append("  Time      Event")

    cursor = 0.0
    lead_in = project["lead_in_sec"]
    lines.append(f"  {format_duration(cursor):<9s} Lead-in ({lead_in:.0f}s silence)")
    cursor += lead_in

    for it in side_layout.items:
        if it.gap_before_sec > 0:
            lines.append(f"  {format_duration(cursor):<9s} Gap ({it.gap_before_sec:.0f}s silence)")
            cursor += it.gap_before_sec

        dur = format_duration(it.duration_sec)
        if it.item_type == "album":
            detail = f"{it.track_count} tracks, {dur}"
        else:
            detail = dur
        lines.append(f"  {format_duration(cursor):<9s} [{it.position}] {it.label} — {detail}")
        cursor += it.duration_sec

    lead_out = project["lead_out_sec"]
    lines.append(f"  {format_duration(cursor):<9s} Lead-out ({lead_out:.0f}s silence)")
    cursor += lead_out
    lines.append(f"  {format_duration(cursor):<9s} End")
    lines.append("")

    remaining = format_duration(abs(side_layout.remaining_sec))
    if _remaining_exceeds_capacity_warning_grace(side_layout.remaining_sec):
        lines.append(f"  OVER BY: {remaining}")
    elif side_layout.remaining_sec < 0:
        grace = format_duration(CAPACITY_WARNING_GRACE_SEC)
        lines.append(f"  Over by: {remaining} (within {grace} grace)")
    else:
        lines.append(f"  Remaining: {remaining}")

    return lines


def show_timeline(conn: sqlite3.Connection, project: sqlite3.Row) -> str:
    """Build timeline show output with timestamps."""
    layout = compute_project_layout(conn, project)
    lines = []

    medium_label = project["medium"].upper()
    if layout.is_two_sided:
        cap_a = format_duration(project["side_a_duration_sec"])
        cap_b = format_duration(project["side_b_duration_sec"])
        lines.append(f"{project['name']} — {medium_label} ({cap_a} + {cap_b})")
        lines.append("")
        lines.extend(_format_side_timeline(layout.side_a, project, side_label="A"))
        lines.append("")
        lines.extend(_format_side_timeline(layout.side_b, project, side_label="B"))
    else:
        capacity = format_duration(project["duration_sec"])
        lines.append(f"{project['name']} — {medium_label} ({capacity} usable)")
        lines.append("")
        side = SideLayout(
            items=layout.items,
            content_sec=layout.content_sec,
            overhead_sec=layout.overhead_sec,
            overhead_parts=layout.overhead_parts,
            total_sec=layout.total_sec,
            remaining_sec=layout.remaining_sec,
            capacity_sec=project["duration_sec"],
        )
        lines.extend(_format_side_timeline(side, project))

    return "\n".join(lines)


# --- Side assignment (two-sided media) ---


def is_two_sided(project: sqlite3.Row) -> bool:
    """Check whether a project uses two-sided media."""
    return project["side_a_duration_sec"] is not None


@dataclass
class SideAssignment:
    """Assignment of an item (or part of a split album) to a side."""

    side: str  # 'a' or 'b'
    position: int
    item_type: str
    path: str
    duration_sec: float
    track_range: tuple[int, int] | None = None  # (start, end) for split albums


@dataclass
class SideItem:
    """Input item for side assignment algorithm."""

    position: int
    item_type: str
    path: str
    duration_sec: float
    pinned_side: str | None  # None = auto, 'a' or 'b'
    track_durations: list[float] | None  # per-track durations for albums


def find_best_split(track_durations: list[float], available_sec: float) -> int:
    """Find how many tracks fit within available_sec.

    Greedy: takes tracks in order until the next would exceed capacity.
    Returns count of tracks that fit (0 = none, len = all).
    """
    total = 0.0
    for i, dur in enumerate(track_durations):
        if total + dur > available_sec:
            return i
        total += dur
    return len(track_durations)


def _side_sort_key(a: SideAssignment) -> tuple[int, int]:
    """Sort assignments in final playback order within a side."""
    split_order = 0 if a.track_range is None or a.track_range[0] == 0 else 1
    return (a.position, split_order)


def _sorted_side(assignments: list[SideAssignment]) -> list[SideAssignment]:
    """Return side assignments sorted in final playback order."""
    return sorted(assignments, key=_side_sort_key)


def _assignment_gap(
    prev_type: str | None,
    curr_type: str,
    album_gap_sec: float,
    track_gap_sec: float,
) -> float:
    """Gap between adjacent side assignments using project gap rules."""
    if prev_type is None:
        return 0.0
    if prev_type == "track" and curr_type == "track":
        return track_gap_sec
    return album_gap_sec


def _side_assignments_total_sec(
    assignments: list[SideAssignment],
    lead_in_sec: float,
    lead_out_sec: float,
    album_gap_sec: float,
    track_gap_sec: float,
) -> float:
    """Compute final side duration for assignments in playback order."""
    total = lead_in_sec + lead_out_sec
    prev_type = None
    for a in _sorted_side(assignments):
        total += _assignment_gap(
            prev_type, a.item_type, album_gap_sec, track_gap_sec,
        )
        total += a.duration_sec
        prev_type = a.item_type
    return total


def _side_fits(
    assignments: list[SideAssignment],
    capacity_sec: float,
    lead_in_sec: float,
    lead_out_sec: float,
    album_gap_sec: float,
    track_gap_sec: float,
) -> bool:
    """Return whether assignments fit a side when sorted into final order."""
    return (
        _side_assignments_total_sec(
            assignments,
            lead_in_sec,
            lead_out_sec,
            album_gap_sec,
            track_gap_sec,
        )
        <= capacity_sec
    )


def _assignment_from_item(
    side: str,
    item: SideItem,
    *,
    duration_sec: float | None = None,
    track_range: tuple[int, int] | None = None,
) -> SideAssignment:
    """Build a side assignment from an input item."""
    return SideAssignment(
        side=side,
        position=item.position,
        item_type=item.item_type,
        path=item.path,
        duration_sec=item.duration_sec if duration_sec is None else duration_sec,
        track_range=track_range,
    )


def _best_split_for_side(
    side_assignments: list[SideAssignment],
    item: SideItem,
    side_capacity_sec: float,
    lead_in_sec: float,
    lead_out_sec: float,
    album_gap_sec: float,
    track_gap_sec: float,
) -> int:
    """Find the largest album prefix that fits the side in final order."""
    if item.item_type != "album" or not item.track_durations:
        return 0

    best = 0
    prefix_duration = 0.0
    # Only return a real split point; full-album fit is handled before this.
    for split_point, track_duration in enumerate(item.track_durations[:-1], 1):
        prefix_duration += track_duration
        candidate = _assignment_from_item(
            "a",
            item,
            duration_sec=prefix_duration,
            track_range=(0, split_point),
        )
        if _side_fits(
            side_assignments + [candidate],
            side_capacity_sec,
            lead_in_sec,
            lead_out_sec,
            album_gap_sec,
            track_gap_sec,
        ):
            best = split_point
        else:
            break

    return best


def assign_sides(
    items: list[SideItem],
    side_a_sec: float,
    side_b_sec: float,
    lead_in_sec: float,
    lead_out_sec: float,
    album_gap_sec: float,
    track_gap_sec: float,
) -> list[SideAssignment]:
    """Assign items to sides for two-sided media.

    Uses greedy fill: side A fills first, overflow goes to side B.
    Pinned items are respected. Albums straddling the boundary are
    split at the best track boundary.
    """
    if not items:
        return []

    side_a: list[SideAssignment] = [
        _assignment_from_item("a", it) for it in items if it.pinned_side == "a"
    ]
    side_b: list[SideAssignment] = [
        _assignment_from_item("b", it) for it in items if it.pinned_side == "b"
    ]

    overflow_to_b = False

    for it in items:
        if it.pinned_side is not None:
            continue

        assignment_a = _assignment_from_item("a", it)
        if not overflow_to_b and _side_fits(
            side_a + [assignment_a],
            side_a_sec,
            lead_in_sec,
            lead_out_sec,
            album_gap_sec,
            track_gap_sec,
        ):
            side_a.append(assignment_a)
            continue

        if not overflow_to_b:
            split_point = _best_split_for_side(
                side_a,
                it,
                side_a_sec,
                lead_in_sec,
                lead_out_sec,
                album_gap_sec,
                track_gap_sec,
            )
            if 0 < split_point < len(it.track_durations or []):
                dur_a = sum(it.track_durations[:split_point])
                dur_b = sum(it.track_durations[split_point:])
                side_a.append(_assignment_from_item(
                    "a",
                    it,
                    duration_sec=dur_a,
                    track_range=(0, split_point),
                ))
                side_b.append(_assignment_from_item(
                    "b",
                    it,
                    duration_sec=dur_b,
                    track_range=(split_point, len(it.track_durations)),
                ))
                overflow_to_b = True
                continue

        side_b.append(_assignment_from_item("b", it))
        overflow_to_b = True

    return _sorted_side(side_a) + _sorted_side(side_b)


# --- Tape analysis ---

LUFS_SPREAD_WARNING_THRESHOLD = 10.0  # dB


@dataclass
class ItemAnalysis:
    position: int
    label: str
    item_type: str
    path: str
    lufs: float
    true_peak: float
    lra: float
    gain_db: float
    peak_after_gain: float
    needs_limiter: bool
    limiter_penalty_db: float  # dB quieter than target due to peak capping
    lra_warning: bool
    use_limiter: bool = False
    use_compressor: bool = False
    compressor_params: dict | None = None
    track_range: tuple[int, int] | None = None  # for split albums on two-sided media


@dataclass
class AnalysisResult:
    project_name: str
    medium: str
    target_lufs: float
    source_rates: dict[int, int]  # sample rate → track count
    resolved_rate: int
    items: list[ItemAnalysis]
    lufs_spread: float
    lra_min: float
    lra_max: float
    duration_total_sec: float
    duration_capacity_sec: float
    warnings: list[str] = field(default_factory=list)
    use_limiter: bool = False
    use_compressor: bool = False
    side_durations: list[tuple[str, float, float]] = field(default_factory=list)


def _collect_sample_rates(
    conn: sqlite3.Connection, items: list[sqlite3.Row],
) -> dict[int, int]:
    """Collect sample rates from all source tracks via mutagen."""
    import mutagen

    from musiktool.constants import collect_audio_files

    rates: Counter[int] = Counter()
    for item in items:
        if item["item_type"] == "album":
            for f in collect_audio_files(Path(item["path"])):
                mf = mutagen.File(str(f))
                if mf is not None and mf.info is not None:
                    rates[mf.info.sample_rate] += 1
        else:
            mf = mutagen.File(item["path"])
            if mf is not None and mf.info is not None:
                rates[mf.info.sample_rate] += 1

    return dict(rates)


def _resolve_sample_rate(
    source_rates: dict[int, int], project_rate: str,
) -> int:
    """Resolve output sample rate.

    If project specifies a rate, use it. If 'auto', use majority vote
    with 44100 Hz as tiebreaker.
    """
    if project_rate != "auto":
        return int(project_rate)

    if not source_rates:
        return 44100

    max_count = max(source_rates.values())
    candidates = [r for r, c in source_rates.items() if c == max_count]

    if 44100 in candidates:
        return 44100
    return min(candidates)  # prefer lower rate on tie


def _compute_compressor_params(
    lra: float,
    medium_dynamic_range_db: float,
) -> dict | None:
    """Compute acompressor params if LRA exceeds medium's usable range.

    Returns dict with threshold, ratio, attack, release, knee
    or None if no compression needed.
    """
    lra_limit = medium_dynamic_range_db * 0.25
    if lra <= lra_limit:
        return None

    excess = lra - lra_limit
    # Gentle ratio: 1.5:1 for small excess, up to 3:1 for large excess
    ratio = min(1.5 + excess * 0.1, 3.0)
    # Threshold: -20 dBFS base, drop 1 dB per LU of excess, capped at -40 dBFS
    threshold_db = max(-20.0 - excess, -40.0)
    threshold_linear = 10 ** (threshold_db / 20)

    return {
        "ratio": round(ratio, 1),
        "threshold": round(threshold_linear, 6),
        "threshold_db": round(threshold_db, 1),
        "attack": 20.0,
        "release": 250.0,
        "knee": 2.83,
    }


def analyze_project(
    conn: sqlite3.Connection, project: sqlite3.Row,
) -> AnalysisResult:
    """Analyze a tape project for compatibility and compute processing params."""
    layout = compute_project_layout(conn, project)
    db_items = db.get_tape_items(conn, project["id"])

    if not db_items:
        raise ValidationError(f"project \"{project['name']}\" has no items")

    target = project["target_lufs"]
    medium = project["medium"]
    user_limiter = bool(project["use_limiter"])
    use_comp = bool(project["use_compressor"])
    medium_dr = MEDIUM_PRESETS.get(medium, {}).get("dynamic_range_db", 90)
    peak_behavior = MEDIUM_PRESETS.get(medium, {}).get("peak_behavior", "hard")
    # Render limiter is present when: user opted in, hard-clip medium,
    # or any item's gain exceeds max_safe (budget applied limiting).
    # Computed per-item below — base flag for hard-clip safety.
    hard_clip_safety = peak_behavior == "hard"

    # Sample rate resolution
    source_rates = _collect_sample_rates(conn, db_items)
    resolved_rate = _resolve_sample_rate(source_rates, project["sample_rate"])

    # Per-item analysis
    item_analyses = []
    all_lufs = []
    all_lra = []
    warnings = []

    for it in layout.items:
        if it.lufs is None:
            raise MissingLoudnessError(f"missing loudness data for: {it.path}")
        if it.peak is None:
            raise MissingLoudnessError(f"missing peak data for: {it.path}")
        if it.lra is None:
            raise MissingLoudnessError(f"missing LRA data for: {it.path}")

        gain, peak_after, needs_limiter, penalty = compute_gain_with_limiter(
            it.lufs, it.peak, target, project["peak_ceiling_dbtp"],
            use_limiter=user_limiter,
        )

        lra_warn = it.lra > medium_dr * 0.25

        comp_params = None
        if use_comp:
            comp_params = _compute_compressor_params(it.lra, medium_dr)

        item_analyses.append(ItemAnalysis(
            position=it.position,
            label=it.label,
            item_type=it.item_type,
            path=it.path,
            lufs=it.lufs,
            true_peak=it.peak,
            lra=it.lra,
            gain_db=gain,
            peak_after_gain=peak_after,
            needs_limiter=needs_limiter,
            limiter_penalty_db=penalty,
            lra_warning=lra_warn,
            use_limiter=needs_limiter or hard_clip_safety or user_limiter,
            use_compressor=use_comp,
            compressor_params=comp_params,
        ))

        all_lufs.append(it.lufs)
        all_lra.append(it.lra)

    # Session-level metrics
    lufs_spread = max(all_lufs) - min(all_lufs)
    lra_min = min(all_lra)
    lra_max = max(all_lra)

    # Build warnings
    if medium == "vinyl-lp":
        warnings.append(
            "Vinyl mastering constraints not implemented (bass mono, "
            "de-essing, HF rolloff). Use for duration/side planning only "
            "\u2014 do not send this master to a cutting engineer without "
            "additional processing."
        )

    if layout.is_two_sided:
        for side_label, side_layout in [("A", layout.side_a), ("B", layout.side_b)]:
            if _remaining_exceeds_capacity_warning_grace(side_layout.remaining_sec):
                over = format_duration(abs(side_layout.remaining_sec))
                cap = format_duration(side_layout.capacity_sec)
                total = format_duration(side_layout.total_sec)
                warnings.append(
                    f"Side {side_label}: {total} exceeds capacity {cap} by {over}"
                )
    elif _remaining_exceeds_capacity_warning_grace(layout.remaining_sec):
        over = format_duration(abs(layout.remaining_sec))
        cap = format_duration(project["duration_sec"])
        total = format_duration(layout.total_sec)
        warnings.append(
            f"Duration {total} exceeds capacity {cap} by {over}"
        )

    if lufs_spread > LUFS_SPREAD_WARNING_THRESHOLD:
        warnings.append(
            f"LUFS spread {lufs_spread:.1f} dB — consider separating material"
        )

    for ia in item_analyses:
        if ia.lra_warning:
            warnings.append(
                f"\"{ia.label}\" LRA {ia.lra:.1f} LU "
                f"may exceed medium dynamic range"
            )
        if ia.needs_limiter:
            limiter_work = ia.gain_db - (project["peak_ceiling_dbtp"] - ia.true_peak)
            if user_limiter:
                warnings.append(
                    f"Item {ia.position} limiter active: "
                    f"gain {ia.gain_db:+.1f} dB, "
                    f"peaks limited to {project['peak_ceiling_dbtp']:.1f} dBTP"
                )
            elif ia.limiter_penalty_db > 0:
                warnings.append(
                    f"Item {ia.position} limiting {limiter_work:.1f} dB (budget), "
                    f"penalty {ia.limiter_penalty_db:.1f} dB below target"
                )
        if ia.compressor_params is not None:
            warnings.append(
                f"Item {ia.position} compressor active: "
                f"LRA {ia.lra:.1f} LU, ratio {ia.compressor_params['ratio']:.1f}:1"
            )

    return AnalysisResult(
        project_name=project["name"],
        medium=medium,
        target_lufs=target,
        source_rates=source_rates,
        resolved_rate=resolved_rate,
        items=item_analyses,
        lufs_spread=lufs_spread,
        lra_min=lra_min,
        lra_max=lra_max,
        duration_total_sec=layout.total_sec,
        duration_capacity_sec=project["duration_sec"],
        warnings=warnings,
        use_limiter=any(ia.use_limiter for ia in item_analyses),
        use_compressor=use_comp,
        side_durations=(
            [
                ("A", layout.side_a.total_sec, layout.side_a.capacity_sec),
                ("B", layout.side_b.total_sec, layout.side_b.capacity_sec),
            ]
            if layout.is_two_sided
            else []
        ),
    )


def format_analysis(result: AnalysisResult) -> str:
    """Format analysis result for CLI output."""
    lines = []

    lines.append(f"{result.project_name} — {result.medium.upper()}")

    if result.use_limiter or result.use_compressor:
        modes = []
        if result.use_limiter:
            peak_behavior = MEDIUM_PRESETS.get(result.medium, {}).get("peak_behavior", "hard")
            if peak_behavior == "hard":
                modes.append("limiter (hard-clip safety)")
            else:
                modes.append("limiter")
        if result.use_compressor:
            modes.append("compressor")
        lines.append(f"  Processing: {', '.join(modes)} enabled")

    lines.append("")

    # Sample rates
    rate_parts = []
    for rate in sorted(result.source_rates.keys()):
        count = result.source_rates[rate]
        rate_parts.append(f"{rate} Hz ({count} tracks)")
    lines.append(f"  Source sample rates: {', '.join(rate_parts)}")
    if len(result.source_rates) > 1:
        lines.append(
            f"  Output sample rate:  {result.resolved_rate} Hz (auto — majority)"
        )
    else:
        lines.append(f"  Output sample rate:  {result.resolved_rate} Hz")
    lines.append("")

    # Per-item table
    lines.append(
        "  Pos  Item                                          "
        "LUFS    Gain   Peak\u2192  Limiter  Comp"
    )
    for ia in result.items:
        label = ia.label[:44]
        gain_str = f"{ia.gain_db:+.1f}"
        peak_str = f"{ia.peak_after_gain:.1f}"
        if ia.needs_limiter and ia.use_limiter:
            limiter = "YES"
        elif ia.needs_limiter:
            limiter = "off"
        else:
            limiter = "no"
        if ia.compressor_params is not None:
            comp = f"{ia.compressor_params['ratio']:.1f}:1"
        else:
            comp = "no"
        lines.append(
            f"  {ia.position:<4d} {label:<44s}  "
            f"{ia.lufs:6.1f}  {gain_str:>6s}  "
            f"{peak_str:>5s}   {limiter:<7s}  {comp}"
        )

    lines.append("")

    # Session metrics
    if result.lufs_spread <= 5:
        spread_note = "homogeneous"
    elif result.lufs_spread <= 10:
        spread_note = "moderate"
    else:
        spread_note = "heterogeneous"

    lines.append(f"  LUFS spread:  {result.lufs_spread:.1f} dB ({spread_note})")
    lines.append(f"  LRA range:    {result.lra_min:.1f} - {result.lra_max:.1f} LU")

    if result.side_durations:
        side_parts = []
        for label, total_sec, capacity_sec in result.side_durations:
            total = format_duration(total_sec)
            cap = format_duration(capacity_sec)
            fits = _capacity_status(total_sec, capacity_sec)
            side_parts.append(f"Side {label} {total} / {cap} ({fits})")
        lines.append(f"  Duration:     {'; '.join(side_parts)}")
    else:
        total = format_duration(result.duration_total_sec)
        cap = format_duration(result.duration_capacity_sec)
        fits = _capacity_status(
            result.duration_total_sec,
            result.duration_capacity_sec,
        )
        lines.append(f"  Duration:     {total} / {cap} ({fits})")

    lines.append("")

    if result.warnings:
        for w in result.warnings:
            lines.append(f"  ! {w}")
    else:
        lines.append("  No issues found.")

    return "\n".join(lines)


# --- Tape rendering ---


def _collect_album_tracks(album_path: str) -> list[str]:
    """Collect sorted audio file paths in an album directory."""
    from musiktool.constants import collect_audio_files

    return [str(f) for f in collect_audio_files(Path(album_path))]


def _build_gap_segments(
    project: sqlite3.Row,
    prev_type: str,
    curr_type: str,
) -> list:
    """Build gap segment(s) between two items.

    Returns a list of Silence/Tone segments for the gap.
    If marker=tone and this is an album boundary, embeds a marker pip.
    """
    from musiktool.segments import Silence, Tone

    gap_sec = compute_gap(prev_type, curr_type, project)
    if gap_sec <= 0:
        return []

    use_marker = (
        project["marker"] == "tone"
        and (prev_type == "album" or curr_type == "album")
    )

    if use_marker:
        marker_dur = project["marker_duration_sec"]
        pad = (gap_sec - marker_dur) / 2
        if pad < 0:
            pad = 0
        segments = []
        if pad > 0:
            segments.append(Silence(duration=pad))
        segments.append(Tone(
            frequency=project["marker_freq"],
            level_dbfs=project["marker_level_dbfs"],
            duration=marker_dur,
        ))
        if pad > 0:
            segments.append(Silence(duration=pad))
        return segments
    else:
        return [Silence(duration=gap_sec)]


def build_tape_segments(
    conn: sqlite3.Connection,
    project: sqlite3.Row,
    analysis: AnalysisResult,
) -> list:
    """Build the ordered segment list for tape rendering."""
    from musiktool.segments import FileInput, Silence

    segments = []

    # Lead-in
    segments.append(Silence(duration=project["lead_in_sec"]))

    prev_type = None
    for ia in analysis.items:
        # Gap before item
        if prev_type is not None:
            gap_segs = _build_gap_segments(project, prev_type, ia.item_type)
            segments.extend(gap_segs)

        # Compute per-item processing params
        limiter_limit = None
        if ia.use_limiter:
            limiter_limit = 10 ** (project["peak_ceiling_dbtp"] / 20)

        # Item content
        if ia.item_type == "album":
            tracks = _collect_album_tracks(ia.path)
            if ia.track_range is not None:
                start, end = ia.track_range
                tracks = tracks[start:end]
            for track_path in tracks:
                segments.append(FileInput(
                    path=track_path, gain_db=ia.gain_db,
                    limiter_limit=limiter_limit,
                    compressor_params=ia.compressor_params,
                ))
        else:
            segments.append(FileInput(
                path=ia.path, gain_db=ia.gain_db,
                limiter_limit=limiter_limit,
                compressor_params=ia.compressor_params,
            ))

        prev_type = ia.item_type

    # Lead-out
    segments.append(Silence(duration=project["lead_out_sec"]))

    return segments


def render_side(
    conn: sqlite3.Connection,
    project: sqlite3.Row,
    analysis: AnalysisResult,
    items: list[ItemAnalysis],
    output: Path,
) -> None:
    """Render a subset of analysis items to a single audio file."""
    from musiktool.segments import render

    # Build a temporary AnalysisResult with just the side's items
    side_analysis = AnalysisResult(
        project_name=analysis.project_name,
        medium=analysis.medium,
        target_lufs=analysis.target_lufs,
        source_rates=analysis.source_rates,
        resolved_rate=analysis.resolved_rate,
        items=items,
        lufs_spread=analysis.lufs_spread,
        lra_min=analysis.lra_min,
        lra_max=analysis.lra_max,
        duration_total_sec=analysis.duration_total_sec,
        duration_capacity_sec=analysis.duration_capacity_sec,
        use_limiter=analysis.use_limiter,
        use_compressor=analysis.use_compressor,
    )
    segments = build_tape_segments(conn, project, side_analysis)
    render(
        segments,
        output,
        sample_rate=analysis.resolved_rate,
        bit_depth=project["bit_depth"],
    )


def render_project(
    conn: sqlite3.Connection,
    project: sqlite3.Row,
    output: Path,
    *,
    analysis: AnalysisResult | None = None,
) -> AnalysisResult:
    """Render a tape project to a single audio file.

    For single-sided projects only. Two-sided rendering is handled
    by the CLI calling render_side() per side.

    If analysis is provided, reuses it; otherwise computes fresh.
    Returns the analysis result used for rendering.
    """
    from musiktool.segments import render

    if analysis is None:
        analysis = analyze_project(conn, project)
    segments = build_tape_segments(conn, project, analysis)

    render(
        segments,
        output,
        sample_rate=analysis.resolved_rate,
        bit_depth=project["bit_depth"],
    )

    return analysis


# --- Tape index (CUE + TXT) ---


@dataclass
class TrackEntry:
    position: int        # 1-based global track number
    title: str
    artist: str
    duration_sec: float
    start_sec: float     # absolute start time in the master
    album_name: str
    track_in_album: int  # 1-based within album


@dataclass
class AlbumEntry:
    name: str
    artist: str
    gain_db: float
    start_sec: float       # where album music starts
    gap_start_sec: float   # where gap before album starts (INDEX 00)
    tracks: list[TrackEntry]


@dataclass
class TapeIndex:
    project_name: str
    medium: str
    date: str
    target_lufs: float
    sample_rate: int
    bit_depth: int
    master_filename: str
    lead_in_sec: float
    lead_out_sec: float
    albums: list[AlbumEntry]
    total_duration_sec: float


def _get_track_duration(path: str) -> float:
    """Get track duration via mutagen."""
    import mutagen

    f = mutagen.File(path)
    if f is None or f.info is None:
        raise AudioReadError(f"cannot read: {path}")
    return f.info.length


def _format_cue_time(seconds: float) -> str:
    """Format seconds as CUE timestamp MM:SS:FF (FF = 1/75th sec frames)."""
    total_frames = int(round(seconds * 75))
    mm, remainder = divmod(total_frames, 75 * 60)
    ss, ff = divmod(remainder, 75)
    return f"{mm:02d}:{ss:02d}:{ff:02d}"


def _format_deck_time(seconds: float) -> str:
    """Format seconds as deck counter H:MM:SS."""
    total = int(round(seconds))
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}:{m:02d}:{s:02d}"


def generate_index(
    conn: sqlite3.Connection,
    project: sqlite3.Row,
    master_filename: str,
    analysis: AnalysisResult,
    *,
    items_override: list[ItemAnalysis] | None = None,
) -> TapeIndex:
    """Generate a tape index with per-track timestamps.

    If items_override is provided, use those items instead of
    analysis.items (used for per-side index generation).
    """
    from musiktool.tags import read_tags

    items = items_override if items_override is not None else analysis.items

    albums = []
    cursor = project["lead_in_sec"]
    global_track = 0
    prev_type = None

    for ia in items:
        # Gap before item
        gap = compute_gap(prev_type, ia.item_type, project) if prev_type else 0.0
        gap_start = cursor
        cursor += gap

        album_start = cursor

        if ia.item_type == "album":
            track_paths = _collect_album_tracks(ia.path)
            if ia.track_range is not None:
                start, end = ia.track_range
                track_paths = track_paths[start:end]
            album_name = ia.label
            # Get artist from first track tags
            first_tags = read_tags(track_paths[0]) if track_paths else None
            album_artist = first_tags.artist if first_tags and first_tags.artist else Path(ia.path).parent.name

            tracks = []
            for i, tp in enumerate(track_paths):
                global_track += 1
                tags = read_tags(tp)
                title = tags.title if tags.title else Path(tp).stem
                artist = tags.artist if tags.artist else album_artist
                dur = _get_track_duration(tp)

                tracks.append(TrackEntry(
                    position=global_track,
                    title=title,
                    artist=artist,
                    duration_sec=dur,
                    start_sec=cursor,
                    album_name=album_name,
                    track_in_album=i + 1,
                ))
                cursor += dur

            albums.append(AlbumEntry(
                name=album_name,
                artist=album_artist,
                gain_db=ia.gain_db,
                start_sec=album_start,
                gap_start_sec=gap_start,
                tracks=tracks,
            ))
        else:
            # Single track item
            global_track += 1
            tags = read_tags(ia.path)
            title = tags.title if tags.title else Path(ia.path).stem
            artist = tags.artist if tags.artist else Path(ia.path).parent.parent.name
            dur = _get_track_duration(ia.path)
            label = ia.label

            track_entry = TrackEntry(
                position=global_track,
                title=title,
                artist=artist,
                duration_sec=dur,
                start_sec=cursor,
                album_name=label,
                track_in_album=1,
            )
            cursor += dur

            albums.append(AlbumEntry(
                name=label,
                artist=artist,
                gain_db=ia.gain_db,
                start_sec=album_start,
                gap_start_sec=gap_start,
                tracks=[track_entry],
            ))

        prev_type = ia.item_type

    return TapeIndex(
        project_name=project["name"],
        medium=project["medium"],
        date=project["created_at"][:10],
        target_lufs=project["target_lufs"],
        sample_rate=analysis.resolved_rate,
        bit_depth=project["bit_depth"],
        master_filename=master_filename,
        lead_in_sec=project["lead_in_sec"],
        lead_out_sec=project["lead_out_sec"],
        albums=albums,
        total_duration_sec=cursor + project["lead_out_sec"],
    )


def format_cue(index: TapeIndex) -> str:
    """Format a TapeIndex as a CUE sheet."""
    lines = []
    lines.append(f"REM COMMENT \"Rendered by musiktool\"")
    lines.append(f"REM DATE {index.date}")
    lines.append(f"REM TARGET_LUFS {index.target_lufs:.0f}")
    lines.append(f"TITLE \"{index.project_name}\"")
    lines.append(f"FILE \"{index.master_filename}\" WAVE")

    is_first_track = True
    for album in index.albums:
        for track in album.tracks:
            lines.append(f"  TRACK {track.position:02d} AUDIO")
            lines.append(f"    TITLE \"{track.title}\"")
            lines.append(f"    PERFORMER \"{track.artist}\"")
            lines.append(f"    REM ALBUM \"{album.name}\"")

            # INDEX 00 for first track of an album (marks gap/lead-in start)
            if track.track_in_album == 1:
                if is_first_track:
                    lines.append(f"    INDEX 00 00:00:00")
                else:
                    lines.append(f"    INDEX 00 {_format_cue_time(album.gap_start_sec)}")

            lines.append(f"    INDEX 01 {_format_cue_time(track.start_sec)}")
            is_first_track = False

    return "\n".join(lines) + "\n"


def _format_txt_side(index: TapeIndex, side_label: str | None = None) -> list[str]:
    """Format one side's TXT content."""
    lines = []
    for album in index.albums:
        album_time = _format_deck_time(album.start_sec)
        lines.append(f"{album_time}  {album.name:<40s} {album.gain_db:+.1f} dB")
        for track in album.tracks:
            track_time = _format_deck_time(track.start_sec)
            lines.append(
                f"          {track.track_in_album:02d}  {track.title:<36s} {track_time}"
            )
        lines.append("")
    return lines


def format_txt(index: TapeIndex) -> str:
    """Format a TapeIndex as printable text for case insert."""
    lines = []

    lines.append(index.project_name.upper())
    lines.append(
        f"{index.medium.upper()} | {index.date} | "
        f"{index.target_lufs:.0f} LUFS | {index.sample_rate} Hz / {index.bit_depth}-bit"
    )
    lines.append("")
    lines.extend(_format_txt_side(index))

    total = _format_deck_time(index.total_duration_sec)
    lines.append(f"Total: {total}")

    return "\n".join(lines) + "\n"


def format_txt_two_sided(index_a: TapeIndex, index_b: TapeIndex) -> str:
    """Format a combined TXT insert for two-sided media."""
    lines = []

    lines.append(index_a.project_name.upper())
    lines.append(
        f"{index_a.medium.upper()} | {index_a.date} | "
        f"{index_a.target_lufs:.0f} LUFS | {index_a.sample_rate} Hz / {index_a.bit_depth}-bit"
    )
    lines.append("")

    lines.append("--- Side A ---")
    lines.append("")
    lines.extend(_format_txt_side(index_a))

    lines.append("--- Side B ---")
    lines.append("")
    lines.extend(_format_txt_side(index_b))

    total_a = _format_deck_time(index_a.total_duration_sec)
    total_b = _format_deck_time(index_b.total_duration_sec)
    lines.append(f"Side A: {total_a}  |  Side B: {total_b}")

    return "\n".join(lines) + "\n"
