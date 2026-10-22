"""The plan is reviewed once (`plan_review_cycles` and `adversarial_cycles` default to 1).

Live runs of one request compared no plan review, one plan round and up to four. Code that shipped
after no plan review and after one round passed and failed the same probes. The PR round found each
real code bug, and each run that did not finish stopped in a loop or at a gate, never on a missing
review. So each reviewer of the plan round reviews once, and P4 carries its open items. The round
in stages.json names the limit, so a reviewer that joins the round gets it. A project layer may raise
the limit (`x-cadence-stricter: higher`): a raise only adds review.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from artifact_requirements import adversarial_cycle_limit, cycle_limit, plan_review_cycle_limit  # noqa: E402
from conftest import project_config  # noqa: E402
from load_stages import load_registry  # noqa: E402

DOCS = ("SKILL.md", "commands/plan-review.md", "commands/plan-update.md")


def test_each_limit_is_one():
    assert (plan_review_cycle_limit(), adversarial_cycle_limit()) == (1, 1)


def test_the_round_names_the_limit():
    assert cycle_limit(load_registry(ROOT)["review_rounds"]["plan"]["cycle_limit"]) == 1


def test_a_project_can_raise_the_limit_and_cannot_lower_it_below_one(tmp_path):
    project_config(tmp_path, {"plan_review_cycles": 2})
    assert cycle_limit("plan_review_cycles", tmp_path) == 2
    assert cycle_limit("adversarial_cycles", tmp_path) == 1


@pytest.mark.parametrize("doc", DOCS)
def test_each_doc_states_one_review(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert "one review cycle" in text and "plan_review_cycles" in text
    for old in ("at most five review cycles", "fifth blocking review", "after the fifth review"):
        assert old not in text, old
