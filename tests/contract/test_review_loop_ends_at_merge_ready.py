"""ADR-0009: a merge-ready verdict ends the R1 review loop; a nit after it goes to the tracker."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("command, phrase", [
    pytest.param("pr-review-update", "`MERGE-READY` with no open must-fix ends the review", id="update-names-the-end"),
    pytest.param("pr-review-update", "do not ask for a fix commit", id="update-asks-for-no-fix-commit"),
    pytest.param("pr-review-update", "A new must-fix opens the loop again", id="update-must-fix-reopens"),
    pytest.param("pr-review-update", "nits to the tracker", id="update-return-line-names-the-state"),
    pytest.param("pr-review-local", "`MERGE-READY` with no open must-fix ends the review loop", id="local-names-the-end"),
    pytest.param("pr-review-local", "not into a fix commit", id="local-asks-for-no-fix-commit"),
])
def test_the_review_commands_say_when_the_loop_ends(command, phrase):
    text = (ROOT / "commands" / f"{command}.md").read_text(encoding="utf-8")
    assert phrase in text, f"{command}.md does not say: {phrase}"
