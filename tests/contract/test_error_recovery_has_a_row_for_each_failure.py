"""references/error-recovery.md has one row for each failure a gate or a hook reports (#182).

The docs point at it. They do not keep a second, shorter recovery table.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "references" / "error-recovery.md").read_text(encoding="utf-8")
# P2 (domain-analysis) records its result and exits 0: it reports no failure.
NO_FAILURE = {"P2"}


def _rows() -> list[list[str]]:
    """The cells of each failure table row (a table under `## Failure table` with a Next command column)."""
    section = DOC.split("## Failure table", 1)[1].split("## Retry budget", 1)[0]
    cells = [[c.strip() for c in line.strip().strip("|").split(" | ")] for line in section.splitlines()
             if line.startswith("| ") and not line.startswith("| Block |") and not line.startswith("| Hook |")
             and not line.startswith("| Failure |")]
    return [c for c in cells if not set(c[0]) <= {"-", "|"}]


def _blocked_texts() -> list[str]:
    """The first words of each literal `BLOCKED:` text under hooks/ and shared/."""
    found = []
    for path in sorted([*(ROOT / "hooks").glob("*.py"), *(ROOT / "shared").glob("*.py")]):
        for match in re.finditer(r'BLOCKED: ([^{`"\\]+)', path.read_text(encoding="utf-8")):
            words = " ".join(match.group(1).split()[:3]).rstrip(",.(:")
            if len(words) >= 6:
                found.append(words)
    return found


def test_each_row_names_a_next_command():
    rows = _rows()
    assert [r for r in rows if len(r) < 2 or not r[-1]] == []


def test_each_block_that_can_fail_has_a_row():
    stages = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))["stages"]
    named = {block.strip() for r in _rows() for block in r[0].split(",")}
    missing = [s["block"] for s in stages if s["block"] not in named | NO_FAILURE]
    assert missing == []


@pytest.mark.parametrize("text", _blocked_texts())
def test_each_hook_denial_has_a_row(text):
    assert text in DOC


@pytest.mark.parametrize("rel, heading", [
    ("docs/pipeline.md", "## Backtrack posture"),
    ("docs/system-design.md", "## Backtrack / recovery matrix"),
])
def test_the_docs_point_at_the_recovery_table(rel, heading):
    text = (ROOT / rel).read_text(encoding="utf-8")
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    assert "references/error-recovery.md" in section
    assert "\n| " not in section, f"{rel} keeps its own recovery table"
