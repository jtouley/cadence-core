"""The public repository holds the product, its tests and its user docs, not design records (AGENTS.md).

Design history stays with the maintainers. A comment or a doc gives the reason in plain words, so it
cites no decision record id that a reader of this repository cannot open.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIRS = ("docs/adr/", "docs/adrs/", "docs/decisions/", "docs/design/", "docs/plans/")
RECORD_ID = re.compile(r"\bADR-0\d{3}\b")
# The decision-coverage gate reads the decision records of the repository that Cadence runs on: its tests
# carry records as data.
DATA = ("tests/conftest.py", "tests/gates/test_architecture_review_decision_coverage.py",
        "tests/contract/test_repo_holds_no_design_records.py")


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def test_no_design_record_is_tracked():
    assert [p for p in _tracked() if p.startswith(DESIGN_DIRS)] == []


def test_no_file_cites_a_decision_record_id():
    hits = []
    for rel in _tracked():
        path = ROOT / rel
        if rel in DATA or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits += [f"{rel}:{n}" for n, line in enumerate(text.splitlines(), 1) if RECORD_ID.search(line)]
    assert hits == []
