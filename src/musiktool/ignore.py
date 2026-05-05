"""Gitignore-style ignore policy for library operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pathspec import PathSpec

IGNORE_FILENAME = ".musiktoolignore"

BUILTIN_IGNORE_PATTERNS = (
    ".Trash-*",
    ".git/",
    ".hg/",
    "__pycache__/",
    "_quarantine/",
    "musiktool/render/",
    "*.db",
    "*.sqlite",
)


@dataclass(frozen=True)
class IgnorePolicy:
    """Root-relative gitignore-style matcher."""

    root: Path
    spec: PathSpec

    def is_ignored(self, path: Path, *, is_dir: bool | None = None) -> bool:
        try:
            relative = path.resolve(strict=False).relative_to(self.root)
        except ValueError:
            return False
        if not relative.parts:
            return False

        text = relative.as_posix()
        if is_dir is None:
            is_dir = path.is_dir()
        if is_dir and not text.endswith("/"):
            text = f"{text}/"
        return self.spec.match_file(text)


def load_ignore_policy(
    root: Path,
    *,
    excludes: Iterable[str] | None = None,
) -> IgnorePolicy:
    """Load built-ins, root .musiktoolignore, and CLI excludes."""
    root = root.resolve(strict=False)
    if root.is_file():
        root = root.parent

    lines = list(BUILTIN_IGNORE_PATTERNS)
    ignore_file = root / IGNORE_FILENAME
    if ignore_file.is_file():
        lines.extend(ignore_file.read_text(encoding="utf-8").splitlines())
    lines.extend(excludes or [])

    return IgnorePolicy(
        root=root,
        spec=PathSpec.from_lines("gitignore", lines),
    )
