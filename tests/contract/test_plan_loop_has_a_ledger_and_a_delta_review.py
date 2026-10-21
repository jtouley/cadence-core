"""The plan update answers each MF-n in a Revision ledger, and the next review reads it as a delta."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

EXAMPLE_SHA = "5a76c1d2e3f4" + "0" * 52


@pytest.mark.parametrize("command, phrase", [
    pytest.param("plan-update", "`--from-review <review-path>`", id="update-reads-the-named-review"),
    pytest.param("plan-update", "read the newest `.context/reviews/plan_<basename>.md`", id="update-falls-back-to-the-newest"),
    pytest.param("plan-update", "End the update section with a `## Revision ledger (review <sha>)` table", id="update-ends-with-the-ledger"),
    pytest.param("plan-update", "| Id | Change | Where (phase or section) | Verification (the test or check that proves it) |",
                 id="update-names-the-ledger-columns"),
    pytest.param("plan-update", "Write `not changed: <reason>` in its Change cell", id="update-drops-nothing-in-silence"),
    pytest.param("plan-update", "A ledger of an earlier update does not answer a newer review", id="update-writes-a-new-ledger"),
    pytest.param("plan-update", "The fail text names each missing id and the review file", id="update-names-the-gate"),
    pytest.param("plan-update", "## Carried review findings", id="update-keeps-the-limit-carry"),
    pytest.param("plan-review", "the orchestrator gives you the previous review as a second input", id="review-reads-the-previous-review"),
    pytest.param("plan-review", "Start the review with a `## Status of earlier items` table", id="review-opens-with-the-status"),
    pytest.param("plan-review", "`closed`: cite the Revision ledger row and the plan text", id="review-closed-needs-evidence"),
    pytest.param("plan-review", "`open`: write what is still missing", id="review-open-names-the-gap"),
    pytest.param("plan-review", "A new must-fix item may cite only text that the newest update section added or changed",
                 id="review-new-items-are-delta-only"),
    pytest.param("plan-review", "is not a new must-fix item", id="review-old-text-is-not-new"),
    pytest.param("plan-review", "Put every open must-fix item under one `## Must-fix items` heading", id="review-keeps-the-heading"),
    pytest.param("plan-review", "A test name, a file name, a phase title or a word choice is not a must-fix item",
                 id="review-keeps-the-structure-rule"),
])
def test_the_plan_loop_commands_name_the_ledger_and_the_delta_review(command, phrase):
    text = (ROOT / "commands" / f"{command}.md").read_text(encoding="utf-8")
    assert phrase in text, f"{command}.md does not say: {phrase}"


def _example_ledger() -> str:
    """The fenced `## Revision ledger (review <sha>)` example of commands/plan-update.md."""
    text = (ROOT / "commands" / "plan-update.md").read_text(encoding="utf-8")
    found = re.search(r"```markdown\n(## Revision ledger \(review 5a76c1d2e3f4\)\n.*?)```", text, re.S)
    assert found, "commands/plan-update.md has no example ledger"
    return found.group(1)


def test_the_example_ledger_of_plan_update_is_the_ledger_the_gate_reads():
    """#187 (PR #186 N-11): the command's example heading, through the gate's own parser."""
    from artifact_requirements import revision_ledger_ids

    assert revision_ledger_ids(_example_ledger(), EXAMPLE_SHA) == {"MF-1", "MF-2"}


def test_the_example_ledger_does_not_answer_another_review():
    from artifact_requirements import revision_ledger_ids

    assert revision_ledger_ids(_example_ledger(), "0" * 64) is None


def test_the_plan_loop_text_names_the_ledger_by_the_review_sha():
    """#187 (PR #186 N-10): the gate reads the ledger that names the review, not the newest ledger."""
    for rel in ("commands/plan-update.md",):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "newest ledger" not in text, rel
    assert "heading with the word `update`" not in (ROOT / "commands" / "plan-update.md").read_text(encoding="utf-8")
