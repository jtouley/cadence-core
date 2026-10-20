"""E1 carries four TDD rules in `## Rules` of commands/spec-driven.md, each with its reason.

references/architecture-core-rules.md points `core:implement` at that block.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC_DRIVEN = "commands/spec-driven.md"
RULES = [
    pytest.param(SPEC_DRIVEN, "\n## Rules\n", id="rules-block"),
    pytest.param(SPEC_DRIVEN, "seam-contract test through the entry point of the caller", id="seam-contract"),
    pytest.param(SPEC_DRIVEN, "Each callback or Protocol wired across modules", id="seam-scope"),
    pytest.param(SPEC_DRIVEN, "write a failing reproduction test first, then the fix", id="bug-fix-mode"),
    pytest.param(SPEC_DRIVEN, "Mock at the process or network edge, never the module under test", id="mock-boundary"),
    pytest.param(SPEC_DRIVEN, "never a connector, a customer or a ticket", id="test-names"),
    pytest.param("references/architecture-core-rules.md", "### `core:implement`", id="core-implement-section"),
    pytest.param("references/architecture-core-rules.md", "`## Rules` in `commands/spec-driven.md`",
                 id="core-implement-points-at-the-rules"),
]


@pytest.mark.parametrize(("rel", "phrase"), RULES)
def test_the_e1_rules_are_written(rel, phrase):
    assert phrase in (ROOT / rel).read_text(encoding="utf-8"), f"{rel} does not say: {phrase}"


def test_each_rule_gives_its_reason():
    text = (ROOT / SPEC_DRIVEN).read_text(encoding="utf-8")
    block = text.split("\n## Rules\n", 1)[1].split("\n## ", 1)[0]
    rules = [line for line in block.splitlines() if line[:2] in {f"{n}." for n in range(1, 10)}]
    assert len(rules) == 4 and all("because" in rule for rule in rules), rules
