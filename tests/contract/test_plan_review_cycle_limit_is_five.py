"""The plan-review loop allows five cycles by default (`plan_review_cycles`).

A cap of three stopped plans that were still getting better. The default, the code and
the three docs that state the cap must agree. A config layer can only lower it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from artifact_requirements import plan_review_cycle_limit  # noqa: E402

DOCS = ("SKILL.md", "commands/plan-review.md", "commands/plan-update.md")


def test_the_limit_is_five():
    assert plan_review_cycle_limit() == 5


@pytest.mark.parametrize("doc", DOCS)
def test_each_doc_states_five(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert "at most five review cycles" in text and "fifth blocking review" in text
    assert "plan_review_cycles" in text
    for old in ("three review cycles", "third blocking review", "after the third review"):
        assert old not in text, old


def test_plan_update_names_the_fifth_review_for_not_ready():
    assert "after the fifth review" in (ROOT / "commands" / "plan-update.md").read_text(encoding="utf-8")
