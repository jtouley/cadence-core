"""The public repository holds the product, its tests and its user docs, not design records (AGENTS.md).

Design history stays with the maintainers. A comment or a doc gives the reason in plain words, so it
cites no decision record, private issue or review round that a reader of this repository cannot open.
"""

from __future__ import annotations

import io
import re
import subprocess
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIRS = ("docs/adr/", "docs/adrs/", "docs/decisions/", "docs/design/", "docs/plans/")
RECORD_ID = re.compile(r"\bADR-0\d{3}\b")
# A citation, not data: a PR number, a bare issue or review id in parentheses, or an id with a review tag.
CITATION = re.compile(r"PR #\d+|jtouley/cadence#\d+|\((?:gh-|cd-|#)\d+(?:-(?:F|MF-)\d+)?(?:[ ,;]+[A-Z][\w-]*)*\)"
                      r"|\b(?:gh|cd)-\d+ (?:[A-Z]{1,3}-?\d+|R\d+-[A-Z])")
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


def _prose(rel: str, text: str) -> list[tuple[int, str]]:
    """The comment and docstring lines of a Python file, or the prose lines of a markdown file."""
    if rel.endswith(".py"):
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [(t.start[0] + i, line) for t in tokens
                if t.type == tokenize.COMMENT or (t.type == tokenize.STRING and t.string.startswith('"""'))
                for i, line in enumerate(t.string.split("\n"))]
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        fenced ^= line.lstrip().startswith("```")
        if not fenced:
            out.append((n, "".join(part for i, part in enumerate(line.split("`")) if i % 2 == 0)))
    return out


def test_no_comment_or_doc_cites_a_private_issue_or_review():
    hits = [f"{rel}:{n}: {line.strip()}" for rel in _tracked() if rel.endswith((".py", ".md"))
            for n, line in _prose(rel, (ROOT / rel).read_text(encoding="utf-8")) if CITATION.search(line)]
    assert hits == []
