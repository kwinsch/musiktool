"""Tests for CLI entry-point behavior."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path


def test_console_script_points_to_error_handling_wrapper() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["musiktool"] == "musiktool.cli:main"


def test_main_reports_musiktool_errors_without_traceback(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "musiktool.cli",
            "tape",
            "show",
            "definitely-missing-project",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "Error: tape project not found: definitely-missing-project" in result.stderr
    assert "Traceback" not in result.stderr
