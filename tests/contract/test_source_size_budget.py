"""The runtime has a size budget. A change that passes it may not grow it back."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIRS = ("scripts", "hooks", "shared")

# Cadence core 0.1.0 has about 17,350 tracked Python lines under the runtime directories: the
# private decision index left the tree, and the config loader came in. Lower this
# number when a change removes code. Raise it only with the reason in the pull request and in
# this comment.
BUDGET_LINES = 17_400


def runtime_lines() -> int:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *(f"{d}/*.py" for d in RUNTIME_DIRS)],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    files = [ROOT / p.decode() for p in out.split(b"\0") if p]
    return sum(len(f.read_bytes().splitlines()) for f in files)


def test_runtime_stays_under_the_size_budget():
    lines = runtime_lines()
    assert lines <= BUDGET_LINES, (
        f"{lines} tracked runtime lines, budget {BUDGET_LINES}. "
        "Remove code, or record the decision that raises the budget."
    )
