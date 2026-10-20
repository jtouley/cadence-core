"""Each test runs with a temporary HOME, so host files of the developer cannot change a result (cd-53)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Read at import, before any fixture runs.
_REAL_HOME = os.environ.get("HOME", "")


def test_home_is_a_temporary_directory():
    home = Path.home().resolve()
    assert _REAL_HOME
    assert home != Path(_REAL_HOME).resolve()
    assert home.is_dir()


def test_subprocess_sees_the_same_home():
    out = subprocess.run(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path.home())"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(out).resolve() == Path.home().resolve()
    assert Path(out).resolve() != Path(_REAL_HOME).resolve()


def test_commands_dir_override_is_cleared():
    assert "CADENCE_COMMANDS_DIR" not in os.environ
