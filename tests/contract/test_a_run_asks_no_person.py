"""A run has no person in the loop, after P9 and at a hard stop too.

Runs stopped, or ended with a choice for the user, where a contract or a hook told the orchestrator to
stop and tell the user. A change after P9 is logged and the run goes on with the frozen plan. This pins that wording, so a later edit cannot bring the stop back.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from skill_gate import PLAN_FROZEN_ACTION  # noqa: E402

DOCS = ("SKILL.md", "commands/plan-create.md", "commands/spec-driven.md", "commands/plan-update.md",
        "references/error-recovery.md", "references/execution-protocol.md", "references/deferral-framework.md",
        "references/final-consolidate.md")
STOPS = re.compile(r"(?i)\b(stop and (tell|ask) the user|or stop and tell|stop the run and ask the user)\b")


def _text(rel: str) -> str:
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())


@pytest.mark.parametrize("rel", DOCS)
def test_no_contract_tells_the_run_to_stop_for_a_person(rel):
    assert not STOPS.search(_text(rel)), STOPS.search(_text(rel))


def test_skill_md_goes_on_with_the_frozen_plan_and_asks_no_question_at_the_end():
    text = _text("SKILL.md")
    assert "go on with the frozen plan" in text
    assert "Do not stop to ask the user." in text
    assert "It asks the user no question and offers no choice" in text


def test_p1_writes_the_plan_on_a_blocking_gap_and_a_request_that_contradicts_itself():
    text = _text("commands/plan-create.md")
    assert "A blocking gap, or a vibe-test verdict of NOT READY, is never a P1 stop" in text
    assert "the item stays in this change: plan it in a phase, and put the deferral under `## Deferrals rejected`" in text


def test_the_plan_freeze_names_no_person():
    assert "without a person" in PLAN_FROZEN_ACTION
    assert "the user" not in PLAN_FROZEN_ACTION
