"""A failed R3 has a named fix step: subagents/simplify-code.md with the R3 record as its one input.

tests/gates/test_cognitive_complexity.py asserts that the R3 fail text ends with the step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECORD = "`.context/gates/cognitive-complexity.json`"


@pytest.mark.parametrize(("rel", "phrase"), [
    pytest.param("subagents/simplify-code.md", RECORD, id="simplify-names-the-record"),
    pytest.param("subagents/simplify-code.md", "`offenders`", id="simplify-reads-the-offenders"),
    pytest.param("subagents/simplify-code.md", "is your task list", id="simplify-task-list"),
    pytest.param("subagents/simplify-code.md", "Change nothing else", id="simplify-changes-nothing-else"),
    pytest.param("commands/spec-driven.md", "## A failed R3", id="spec-driven-section"),
    pytest.param("commands/spec-driven.md", "$CADENCE_HOME/subagents/simplify-code.md", id="spec-driven-names-the-peer"),
    pytest.param("commands/spec-driven.md", RECORD, id="spec-driven-names-the-record"),
    pytest.param("commands/spec-driven.md", "Run R3 again", id="spec-driven-runs-r3-again"),
    pytest.param("commands/spec-driven.md", "Do the fix step one time", id="spec-driven-one-cycle"),
    pytest.param("commands/spec-driven.md", "Then the loop rule applies", id="spec-driven-then-the-loop-rule"),
])
def test_the_r3_fix_step_is_written(rel, phrase):
    assert phrase in (ROOT / rel).read_text(encoding="utf-8"), f"{rel} does not say: {phrase}"
