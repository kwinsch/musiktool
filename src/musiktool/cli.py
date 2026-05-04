"""musiktool CLI - music library management."""

from pathlib import Path

import typer

from musiktool.exceptions import (
    InvalidMediumError,
    InvalidPositionError,
    MissingLoudnessError,
    MusiktoolError,
    PathNotFoundError,
    ProjectNotFoundError,
    ValidationError,
)

app = typer.Typer(
    help="Music library management tool",
    pretty_exceptions_enable=False,  # we handle MusiktoolError ourselves
)
tape_app = typer.Typer(help="Tape mastering project management")
app.add_typer(tape_app, name="tape")


@app.command()
def identify(
    path: Path = typer.Argument(..., help="Audio file or directory to identify"),
    write: bool = typer.Option(False, "--write", "-w", help="Write tags to file"),
):
    """Identify tracks via acoustic fingerprinting (AcoustID/MusicBrainz).

    On a single file: shows top matches for that track.
    On a directory: identifies the album by finding the most common album across all tracks.
    """
    from musiktool.fingerprint import identify as fp_identify, identify_album
    from musiktool.tags import Tags, write_tags

    files = _collect_audio_files(path)

    if path.is_dir() and len(files) > 1:
        # Album identification mode
        typer.echo(f"Identifying album: {path.name} ({len(files)} tracks)\n")
        matches = identify_album([str(f) for f in files])
        if not matches:
            typer.echo("No album match found")
            return

        typer.echo("Album candidates (by track hits):")
        for m in matches[:8]:
            pct = f"{m.confidence:.0%}"
            year_str = f" ({m.year})" if m.year else ""
            typer.echo(f"  {m.track_hits:2d}/{m.total_tracks} [{pct:>4s}]  {m.artist} - {m.album}{year_str}")

        best = matches[0]
        if write and best.confidence >= 0.6:
            typer.echo(f"\nWriting album={best.album}, artist={best.artist}, year={best.year}")
            for f in files:
                tags = Tags(
                    album=best.album,
                    album_artist=best.artist,
                    year=best.year,
                )
                write_tags(str(f), tags)
            typer.echo(f"  -> Album tags written to {len(files)} files")
        elif write:
            typer.echo(f"\n  -> Confidence too low ({best.confidence:.0%}), skipping write")
    else:
        # Single track mode
        for f in files:
            typer.echo(f"\n{f.name}")
            results = fp_identify(str(f))
            if not results:
                typer.echo("  No match found")
                continue

            best = results[0]
            typer.echo(f"  Artist: {best.artist}")
            typer.echo(f"  Title:  {best.title}")
            typer.echo(f"  Album:  {best.album}")
            typer.echo(f"  Year:   {best.year}")
            typer.echo(f"  Score:  {best.score:.2f}")

            if write and best.score >= 0.8:
                tags = Tags(
                    title=best.title,
                    artist=best.artist,
                    album=best.album,
                    year=best.year,
                )
                write_tags(str(f), tags)
                typer.echo("  -> Tags written")
            elif write and best.score < 0.8:
                typer.echo(f"  -> Score too low ({best.score:.2f}), skipping write")


@app.command()
def flatten(
    lib_path: Path = typer.Argument(..., help="Library root path (e.g. ~/Music)"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Preview moves without executing"),
):
    """Flatten format-based subdirs (cd_flac, cd_alac, cd-loosy) into Artist/Album at root."""
    from musiktool.flatten import flatten_library

    moves = flatten_library(lib_path, dry_run=dry_run)

    if not moves:
        typer.echo("Nothing to move.")
        return

    for src, dst in moves:
        rel_src = src.relative_to(lib_path)
        rel_dst = dst.relative_to(lib_path)
        typer.echo(f"  {rel_src} -> {rel_dst}")

    typer.echo(f"\n{'[DRY RUN] ' if dry_run else ''}{len(moves)} moves")
    if dry_run:
        typer.echo("Run with --execute to apply.")


@app.command()
def loudness(
    path: Path = typer.Argument(..., help="Audio file or directory to measure"),
    album: bool = typer.Option(False, "--album", "-a", help="Measure as album"),
):
    """Measure EBU R128 loudness."""
    from musiktool.loudness import measure_track, measure_album

    if path.is_file():
        info = measure_track(str(path))
        typer.echo(f"{path.name}")
        typer.echo(f"  Integrated: {info.integrated_lufs:.1f} LUFS")
        typer.echo(f"  True Peak:  {info.true_peak_dbtp:.1f} dBTP")
        typer.echo(f"  LRA:        {info.lra:.1f} LU")
    elif path.is_dir() and album:
        tracks, album_info = measure_album(str(path))
        for t in tracks:
            typer.echo(f"{Path(t.path).name:50s} {t.integrated_lufs:6.1f} LUFS  {t.true_peak_dbtp:6.1f} dBTP  {t.lra:5.1f} LU")
        if album_info is not None:
            typer.echo(f"\n{'Album integrated:':<50s} {album_info.integrated_lufs:6.1f} LUFS  {album_info.true_peak_dbtp:6.1f} dBTP  {album_info.lra:5.1f} LU")
    elif path.is_dir():
        files = _collect_audio_files(path)
        for f in files:
            info = measure_track(str(f))
            typer.echo(f"{f.name:50s} {info.integrated_lufs:6.1f} LUFS  {info.true_peak_dbtp:6.1f} dBTP")


@app.command()
def analyze(
    path: Path = typer.Argument(..., help="Library root or album directory to analyze"),
    db_path: Path = typer.Option(None, "--db", help="Database path (default: analytics.db next to lib/)"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-analyze all tracks regardless of mtime"),
):
    """Bulk EBU R128 analysis into analytics DB (incremental)."""
    from musiktool.analyze import (
        analyze_album as do_analyze_album,
        analyze_library,
    )
    from musiktool import db

    if db_path is None:
        db_path = db.default_db_path()

    typer.echo(f"DB: {db_path}")

    from musiktool.constants import AUDIO_EXTENSIONS as _AE

    if path.is_dir() and not any(
        f.suffix.lower() in _AE
        for f in path.iterdir() if f.is_file()
    ):
        # Library root — analyze all albums
        def progress(i: int, total: int, album: Path) -> None:
            typer.echo(f"[{i}/{total}] {album.parent.name}/{album.name}")

        summary = analyze_library(path, db_path, force=force, progress_fn=progress)

        typer.echo(f"\n{summary.albums} albums, "
                   f"{summary.tracks_measured} measured, "
                   f"{summary.tracks_skipped} skipped, "
                   f"{summary.tracks_removed} removed")
        if summary.errors:
            typer.echo(f"\n{len(summary.errors)} errors:")
            for err in summary.errors:
                typer.echo(f"  {err}")
    else:
        # Single album directory
        conn = db.get_connection(db_path)
        try:
            result = do_analyze_album(path, conn, force=force)
            typer.echo(f"{path.parent.name}/{path.name}: "
                       f"{result.measured} measured, "
                       f"{result.skipped} skipped, "
                       f"{result.removed} removed")
        finally:
            conn.close()


CALIBRATION_LEVELS = [-20, -10, -3, 0]


@app.command()
def calibrate(
    output: Path = typer.Option(..., "--output", "-o", help="Output directory for calibration files"),
    sample_rate: int = typer.Option(44100, "--sample-rate", help="Sample rate in Hz"),
    bit_depth: int = typer.Option(24, "--bit-depth", help="Bit depth: 16 or 24"),
    rates: bool = typer.Option(False, "--rates", help="Generate sample rate test files"),
) -> None:
    """Generate calibration tone files for tape deck level setting.

    Creates one FLAC file per reference level (2s silence + 30s tone + 2s silence).
    Play each at 100% volume and match the deck's VU meters.

    With --rates: also generates sample rate test files (one per standard rate)
    to verify DAC rate switching without resampling.
    """
    from musiktool.segments import Silence, Tone, render

    if bit_depth not in (16, 24):
        typer.echo("Error: --bit-depth must be 16 or 24", err=True)
        raise typer.Exit(1)

    output.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Rendering calibration tones to: {output}")
    typer.echo(f"  Sample rate: {sample_rate} Hz, bit depth: {bit_depth}")

    for level in CALIBRATION_LEVELS:
        segments = [
            Silence(duration=2),
            Tone(frequency=1000, level_dbfs=level, duration=30),
            Silence(duration=2),
        ]
        fname = f"cal_{level:+03d}dBFS.flac"
        fpath = output / fname
        render(segments, fpath, sample_rate=sample_rate, bit_depth=bit_depth)
        typer.echo(f"  {fname} (1 kHz @ {level} dBFS, 30s)")

    typer.echo(f"  {len(CALIBRATION_LEVELS)} level tones")

    if rates:
        from musiktool.constants import SAMPLE_RATES

        rates_dir = output / "sample-rates"
        rates_dir.mkdir(parents=True, exist_ok=True)

        typer.echo(f"\n  Sample rate test files:")
        for rate in SAMPLE_RATES:
            segments = [
                Silence(duration=2),
                Tone(frequency=1000, level_dbfs=-20, duration=10),
                Silence(duration=2),
            ]
            fname = f"{rate}hz.flac"
            fpath = rates_dir / fname
            render(segments, fpath, sample_rate=rate, bit_depth=24)
            typer.echo(f"  {fname} (1 kHz @ -20 dBFS, {rate} Hz)")

        typer.echo(f"  {len(SAMPLE_RATES)} rate files")

    typer.echo("  Done.")


@app.command()
def tags(
    path: Path = typer.Argument(..., help="Audio file or directory to inspect"),
):
    """Show tags for audio files."""
    from musiktool.tags import read_tags

    files = _collect_audio_files(path)
    for f in files:
        t = read_tags(str(f))
        typer.echo(f"{f.name}")
        typer.echo(f"  Artist: {t.artist}  |  Album: {t.album}  |  Year: {t.year}")
        typer.echo(f"  Title:  {t.title}  |  Track: {t.track_number}  |  Genre: {t.genre}")
        typer.echo("")


# --- Tape subcommands ---


def _tape_conn():
    """Get a DB connection for tape commands."""
    from musiktool import db
    return db.get_connection()


@tape_app.command("create")
def tape_create(
    name: str = typer.Argument(..., help="Project name"),
    medium: str = typer.Option(..., "--medium", "-m", help="Medium preset: vhs-120, vhs-160, vhs-180, or custom"),
    duration: float = typer.Option(None, "--duration", help="Usable duration in minutes (required for custom)"),
    lead_in: float = typer.Option(25, "--lead-in", help="Lead-in silence in seconds"),
    lead_out: float = typer.Option(20, "--lead-out", help="Lead-out silence in seconds"),
    album_gap: float = typer.Option(8, "--album-gap", help="Silence between albums in seconds"),
    track_gap: float = typer.Option(4, "--track-gap", help="Silence between individual tracks in seconds"),
    marker: str = typer.Option("none", "--marker", help="Album boundary marker: none or tone"),
    marker_freq: float = typer.Option(400, "--marker-freq", help="Marker tone frequency in Hz"),
    marker_level: float = typer.Option(-30, "--marker-level", help="Marker tone level in dBFS"),
    marker_duration: float = typer.Option(0.5, "--marker-duration", help="Marker tone duration in seconds"),
    target_lufs: float = typer.Option(-14, "--target-lufs", help="Target integrated loudness in LUFS"),
    peak_ceiling: float = typer.Option(0, "--peak-ceiling", help="Peak ceiling in dBTP (0=full scale, -1=1dB headroom)"),
    sample_rate: str = typer.Option("auto", "--sample-rate", help="Output sample rate (auto or Hz value)"),
    bit_depth: int = typer.Option(24, "--bit-depth", help="Output bit depth: 16 or 24"),
    limiter: bool = typer.Option(False, "--limiter", help="Enable peak limiter for items that would clip"),
    compressor: bool = typer.Option(False, "--compressor", help="Enable gentle compression for wide-dynamic-range items"),
) -> None:
    """Create a new tape project."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        project_id = tape.create_project(
            conn, name, medium,
            duration_min=duration,
            lead_in_sec=lead_in,
            lead_out_sec=lead_out,
            album_gap_sec=album_gap,
            track_gap_sec=track_gap,
            marker=marker,
            marker_freq=marker_freq,
            marker_level_dbfs=marker_level,
            marker_duration_sec=marker_duration,
            target_lufs=target_lufs,
            peak_ceiling_dbtp=peak_ceiling,
            sample_rate=sample_rate,
            bit_depth=bit_depth,
            use_limiter=limiter,
            use_compressor=compressor,
        )
        project = tape.get_project(conn, name)
        capacity = tape.format_duration(project["duration_sec"])
        typer.echo(f"Created \"{name}\" ({medium}, {capacity} usable)")
    finally:
        conn.close()


@tape_app.command("list")
def tape_list() -> None:
    """List all tape projects."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        projects = tape.list_projects(conn)
        if not projects:
            typer.echo("No tape projects.")
            return

        typer.echo(f"{'Name':<24s} {'Medium':<10s} {'Items':>5s}  {'Duration':>8s}  {'Remaining':>9s}  Created")
        for p in projects:
            items = tape.get_items(conn, p["id"])
            layout = tape.compute_project_layout(conn, p)
            total = tape.format_duration(layout.total_sec)
            remaining = tape.format_duration(abs(layout.remaining_sec))
            if layout.remaining_sec < 0:
                remaining = f"-{remaining}"
            created = p["created_at"][:10]
            typer.echo(
                f"{p['name']:<24s} {p['medium']:<10s} {len(items):>5d}  {total:>8s}  {remaining:>9s}  {created}"
            )
    finally:
        conn.close()


@tape_app.command("show")
def tape_show(
    name: str = typer.Argument(..., help="Project name"),
    timeline: bool = typer.Option(False, "--timeline", help="Show timeline view with timestamps"),
) -> None:
    """Display project contents."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)
        if timeline:
            typer.echo(tape.show_timeline(conn, project))
        else:
            typer.echo(tape.show_compact(conn, project))
    finally:
        conn.close()


@tape_app.command("analyze")
def tape_analyze_cmd(
    name: str = typer.Argument(..., help="Project name"),
) -> None:
    """Analyze project for compatibility and compute processing parameters."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)
        result = tape.analyze_project(conn, project)
        typer.echo(tape.format_analysis(result))
    finally:
        conn.close()


def _slugify(name: str) -> str:
    """Convert project name to filesystem-safe slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _print_render_progress(
    items: list, analysis_items: list, project, *, side_label: str | None = None,
) -> None:
    """Print render progress timeline with gain/processing details.

    If side_label is provided, prints a side header (two-sided media).
    """
    from musiktool import tape

    if side_label is not None:
        typer.echo(f"  --- Side {side_label.upper()} ---")

    cursor = 0.0
    lead_in = project["lead_in_sec"]
    typer.echo(f"  {tape.format_duration(cursor):<7s} Lead-in ({lead_in:.0f}s)")
    cursor += lead_in

    for it in items:
        if it.gap_before_sec > 0:
            typer.echo(f"  {tape.format_duration(cursor):<7s} Gap ({it.gap_before_sec:.0f}s)")
            cursor += it.gap_before_sec

        ia = next((a for a in analysis_items if a.position == it.position), None)
        detail = f"gain {ia.gain_db:+.1f} dB" if ia else ""
        if ia and ia.use_limiter and ia.needs_limiter:
            detail += ", limiter"
        if ia and ia.compressor_params is not None:
            detail += f", comp {ia.compressor_params['ratio']:.1f}:1"
        typer.echo(
            f"  {tape.format_duration(cursor):<7s} [{it.position}] "
            f"{it.label} \u2014 {detail}"
        )
        cursor += it.duration_sec

    lead_out = project["lead_out_sec"]
    typer.echo(f"  {tape.format_duration(cursor):<7s} Lead-out ({lead_out:.0f}s)")


def _report_file(path: Path) -> str:
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb >= 1024:
        return f"{size_mb / 1024:.2f} GB"
    return f"{size_mb:.0f} MB"


@tape_app.command("render")
def tape_render_cmd(
    name: str = typer.Argument(..., help="Project name"),
    output: Path = typer.Option(..., "--output", "-o", help="Base output directory"),
) -> None:
    """Render the project to a directory with FLAC, CUE, and TXT files."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)
        slug = _slugify(name)
        out_dir = output / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        if tape.is_two_sided(project):
            _render_two_sided(conn, project, slug, out_dir)
        else:
            _render_single_sided(conn, project, slug, out_dir)
    finally:
        conn.close()


def _render_single_sided(conn, project, slug, out_dir):
    from musiktool import tape

    analysis = tape.analyze_project(conn, project)
    layout = tape.compute_project_layout(conn, project)

    typer.echo(
        f"Rendering \"{project['name']}\" \u2192 {out_dir}/ "
        f"({analysis.resolved_rate} Hz, {project['bit_depth']}-bit)"
    )
    typer.echo("")
    _print_render_progress(layout.items, analysis.items, project)
    typer.echo("")

    flac_path = out_dir / f"{slug}.flac"
    tape.render_project(conn, project, flac_path, analysis=analysis)

    index = tape.generate_index(conn, project, f"{slug}.flac", analysis)
    (out_dir / f"{slug}.cue").write_text(tape.format_cue(index))
    (out_dir / f"{slug}.txt").write_text(tape.format_txt(index))

    typer.echo(f"  Done: {out_dir}/ ({_report_file(flac_path)}, "
               f"{tape.format_duration(analysis.duration_total_sec)})")
    typer.echo(f"    {slug}.flac")
    typer.echo(f"    {slug}.cue")
    typer.echo(f"    {slug}.txt")


def _render_two_sided(conn, project, slug, out_dir):
    from musiktool import tape

    analysis = tape.analyze_project(conn, project)
    layout = tape.compute_project_layout(conn, project)

    typer.echo(
        f"Rendering \"{project['name']}\" \u2192 {out_dir}/ "
        f"({analysis.resolved_rate} Hz, {project['bit_depth']}-bit)"
    )
    typer.echo("")

    def _build_side_analysis(side_layout):
        """Build ItemAnalysis list for a side from layout items."""
        items = []
        for it in side_layout.items:
            ia = next(a for a in analysis.items if a.position == it.position)
            items.append(tape.ItemAnalysis(
                position=ia.position, label=it.label,
                item_type=ia.item_type, path=ia.path,
                lufs=ia.lufs, true_peak=ia.true_peak, lra=ia.lra,
                gain_db=ia.gain_db, peak_after_gain=ia.peak_after_gain,
                needs_limiter=ia.needs_limiter,
                limiter_penalty_db=ia.limiter_penalty_db,
                lra_warning=ia.lra_warning,
                use_limiter=ia.use_limiter,
                use_compressor=ia.use_compressor,
                compressor_params=ia.compressor_params,
                track_range=it.track_range,
            ))
        return items

    side_analyses = {
        "side_a": _build_side_analysis(layout.side_a),
        "side_b": _build_side_analysis(layout.side_b),
    }

    files = []
    indices = {}
    for side_key, side_label in [("side_a", "A"), ("side_b", "B")]:
        side_items = side_analyses[side_key]
        if not side_items:
            continue

        fname = f"{slug}-side-{side_label.lower()}"
        flac_path = out_dir / f"{fname}.flac"

        side_layout = layout.side_a if side_key == "side_a" else layout.side_b
        _print_render_progress(
            side_layout.items, analysis.items, project, side_label=side_label,
        )
        typer.echo("")

        tape.render_side(conn, project, analysis, side_items, flac_path)
        files.append((side_label, fname, flac_path))

        index = tape.generate_index(
            conn, project, f"{fname}.flac", analysis, items_override=side_items,
        )
        indices[side_key] = index
        (out_dir / f"{fname}.cue").write_text(tape.format_cue(index))

    # Combined TXT
    if "side_a" in indices and "side_b" in indices:
        (out_dir / f"{slug}.txt").write_text(
            tape.format_txt_two_sided(indices["side_a"], indices["side_b"]),
        )
    elif "side_a" in indices:
        (out_dir / f"{slug}.txt").write_text(tape.format_txt(indices["side_a"]))

    typer.echo("")
    typer.echo(f"  Done: {out_dir}/")
    for side_label, fname, flac_path in files:
        typer.echo(f"    {fname}.flac ({_report_file(flac_path)})")
        typer.echo(f"    {fname}.cue")
    typer.echo(f"    {slug}.txt")


@tape_app.command("add")
def tape_add(
    name: str = typer.Argument(..., help="Project name"),
    paths: list[Path] = typer.Argument(..., help="Paths to add (directories=albums, files=tracks)"),
    side: str = typer.Option(None, "--side", "-s", help="Pin to side: a or b"),
) -> None:
    """Append albums or tracks to a project."""
    from musiktool import tape

    if side is not None:
        side = side.lower()
        if side not in ("a", "b"):
            typer.secho(f"Error: --side must be 'a' or 'b': {side}", err=True)
            raise typer.Exit(1)

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)
        added = tape.add_items(conn, project["id"], [str(p) for p in paths], side=side)
        for path, item_type in added:
            label = Path(path).name if item_type == "album" else Path(path).stem
            pin = f" [side {side.upper()}]" if side else ""
            typer.echo(f"  + [{item_type}] {label}{pin}")
        typer.echo(f"Added {len(added)} item(s) to \"{name}\"")
    finally:
        conn.close()


@tape_app.command("insert")
def tape_insert(
    name: str = typer.Argument(..., help="Project name"),
    position: int = typer.Argument(..., help="Insert before this position (1-based)"),
    paths: list[Path] = typer.Argument(..., help="Paths to insert"),
    side: str = typer.Option(None, "--side", "-s", help="Pin to side: a or b"),
) -> None:
    """Insert albums or tracks at a specific position."""
    from musiktool import tape

    if side is not None:
        side = side.lower()
        if side not in ("a", "b"):
            typer.secho(f"Error: --side must be 'a' or 'b': {side}", err=True)
            raise typer.Exit(1)

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)
        inserted = tape.insert_items(
            conn, project["id"], position, [str(p) for p in paths], side=side,
        )
        for path, item_type in inserted:
            label = Path(path).name if item_type == "album" else Path(path).stem
            typer.echo(f"  + [{item_type}] {label} at position {position}")
            position += 1
        typer.echo(f"Inserted {len(inserted)} item(s)")
    finally:
        conn.close()


@tape_app.command("remove")
def tape_remove(
    name: str = typer.Argument(..., help="Project name"),
    positions: list[int] = typer.Argument(..., help="Positions to remove (1-based)"),
) -> None:
    """Remove items by position."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)
        removed = tape.remove_items(conn, project["id"], positions)
        typer.echo(f"Removed {removed} item(s) from \"{name}\"")
    finally:
        conn.close()


@tape_app.command("move")
def tape_move(
    name: str = typer.Argument(..., help="Project name"),
    from_pos: int = typer.Argument(..., help="Current position"),
    to_pos: int = typer.Argument(..., help="Target position"),
    side: str = typer.Option(None, "--side", "-s", help="Pin to side: a, b, or auto (clears pin)"),
) -> None:
    """Move an item from one position to another."""
    from musiktool import tape

    side_val = ...  # sentinel: don't change
    if side is not None:
        side = side.lower()
        if side == "auto":
            side_val = None  # clear pin
        else:
            if side not in ("a", "b"):
                typer.secho(
                    f"Error: --side must be 'a', 'b', or 'auto': {side}", err=True
                )
                raise typer.Exit(1)
            side_val = side

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)
        tape.move_item(conn, project["id"], from_pos, to_pos, side=side_val)
        msg = f"Moved item {from_pos} \u2192 {to_pos}"
        if side is not None:
            msg += f" (side: {side})"
        typer.echo(msg)
    finally:
        conn.close()


@tape_app.command("index")
def tape_index_cmd(
    name: str = typer.Argument(..., help="Project name"),
    output: Path = typer.Option(..., "--output", "-o", help="Output basename (no extension)"),
) -> None:
    """Generate CUE sheet and printable TXT index."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        project = tape.get_project(conn, name)

        analysis = tape.analyze_project(conn, project)
        master_filename = output.name + ".flac"
        index = tape.generate_index(conn, project, master_filename, analysis)

        cue_path = output.with_suffix(".cue")
        txt_path = output.with_suffix(".txt")

        cue_path.write_text(tape.format_cue(index))
        txt_path.write_text(tape.format_txt(index))

        typer.echo(f"Generated: {cue_path}")
        typer.echo(f"Generated: {txt_path}")
    finally:
        conn.close()


@tape_app.command("delete")
def tape_delete(
    name: str = typer.Argument(..., help="Project name"),
) -> None:
    """Delete a tape project."""
    from musiktool import tape

    conn = _tape_conn()
    try:
        tape.delete_project(conn, name)
        typer.echo(f"Deleted \"{name}\"")
    finally:
        conn.close()


def _collect_audio_files(path: Path) -> list[Path]:
    """Collect audio files from path (file or directory)."""
    from musiktool.constants import collect_audio_files
    return collect_audio_files(path)


@app.callback()
def main() -> None:
    """Global error handler for friendly MusiktoolError output."""
    # Typer will call this before subcommands; we wrap execution below
    pass


if __name__ == "__main__":
    import sys

    try:
        app()
    except MusiktoolError as e:
        typer.secho(f"Error: {e}", err=True)
        sys.exit(1)
    except typer.Exit:
        raise
    except Exception:
        # Let other exceptions (including Click/Typer internal) behave normally
        raise
