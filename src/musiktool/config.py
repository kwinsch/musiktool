"""Read-only TOML configuration (~/.config/musiktool/config.toml)."""

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    render_dir: Path | None  # None = not configured


def _config_path() -> Path:
    """Return path to config.toml respecting XDG_CONFIG_HOME."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "musiktool" / "config.toml"


def _default_data_dir() -> Path:
    """Default data directory respecting XDG_DATA_HOME."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "musiktool"


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Load config. Missing file uses defaults. Cached after first call."""
    config_file = _config_path()
    raw: dict = {}
    if config_file.is_file():
        with open(config_file, "rb") as f:
            raw = tomllib.load(f)

    data_dir_str = raw.get("data_dir")
    data_dir = Path(data_dir_str).expanduser() if data_dir_str else _default_data_dir()

    render_dir_str = raw.get("render_dir")
    render_dir = Path(render_dir_str).expanduser() if render_dir_str else None

    return Config(data_dir=data_dir, render_dir=render_dir)
