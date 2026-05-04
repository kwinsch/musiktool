"""Flatten music library from format-based subdirs to Artist/Album structure.

Moves everything (audio, CUE, logs, art) together — keeps albums intact.
"""

import shutil
from pathlib import Path


def flatten_library(lib_path: Path, dry_run: bool = True) -> list[tuple[Path, Path]]:
    """Flatten cd_flac/, cd_alac/, cd-loosy/ into lib/ root.

    Moves Artist/Album dirs up one level, removing the format prefix.
    Non-format dirs already at root level are left alone.

    Returns list of (source, destination) moves.
    """
    format_dirs = ["cd_flac", "cd_alac", "cd-loosy"]
    moves: list[tuple[Path, Path]] = []

    for fmt_dir_name in format_dirs:
        fmt_dir = lib_path / fmt_dir_name
        if not fmt_dir.is_dir():
            continue

        # Each subdir is an Artist
        for artist_dir in sorted(fmt_dir.iterdir()):
            if not artist_dir.is_dir():
                continue

            target_artist = lib_path / artist_dir.name

            # Each subdir of artist is an Album
            for album_dir in sorted(artist_dir.iterdir()):
                if not album_dir.is_dir():
                    # Loose file at artist level — move to artist dir
                    target_file = target_artist / album_dir.name
                    moves.append((album_dir, target_file))
                    continue

                target_album = target_artist / album_dir.name

                if target_album.exists():
                    # Album already exists at target — merge files
                    for f in sorted(album_dir.iterdir()):
                        if f.is_file():
                            target_file = target_album / f.name
                            if not target_file.exists():
                                moves.append((f, target_file))
                else:
                    # Move entire album dir
                    moves.append((album_dir, target_album))

    if not dry_run:
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

        # Clean up empty format dirs
        for fmt_dir_name in format_dirs:
            fmt_dir = lib_path / fmt_dir_name
            if fmt_dir.is_dir():
                _remove_empty_dirs(fmt_dir)

    return moves


def _remove_empty_dirs(path: Path) -> None:
    """Recursively remove empty directories."""
    for child in sorted(path.iterdir()):
        if child.is_dir():
            _remove_empty_dirs(child)
    # Try to remove this dir (only works if empty)
    try:
        path.rmdir()
    except OSError:
        pass
