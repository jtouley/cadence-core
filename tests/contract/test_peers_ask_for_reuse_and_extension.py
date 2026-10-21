"""Each stage that plans, writes or reviews a change asks for reuse and an extension point (gh-99).

#99: `core:dry-extension` covered gate modules and CLI shells only. No plan, implementation
or review asked for the helpers it reuses or for an extension point, and the DRY score of
`/test-benchmark` was the opinion of the peer. Each doc below carries its instruction.
R2 computes the DRY lists, and a peer reads the verdict of a measure; it does not judge again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEADING = "`## Reuse and extension`"
INSTRUCTIONS = [
    ("references/architecture-core-rules.md", "Use a helper that exists in a shared module"),
    ("references/architecture-core-rules.md", "`tests/conftest.py`, `scripts/gate_common.py` and `shared/`"),
    ("references/architecture-core-rules.md", "Rule of Three"),
    ("references/architecture-core-rules.md", "under the R3 limit"),
    ("references/architecture-core-rules.md", "add an extension point"),
    ("commands/plan-create.md", HEADING),
    ("commands/plan-create.md", "Name the extension point"),
    ("commands/plan-review.md", HEADING),
    ("commands/plan-review.md", "writes a helper that already exists gets `READY WITH CHANGES`"),
    ("commands/spec-driven.md", "tests/conftest.py scripts/ shared/"),
    ("commands/spec-driven.md", "remove the duplication in the same plan phase"),
    ("commands/pr-review-local.md", "Reuse and extension"),
    ("commands/pr-review-update.md", "Reuse and extension"),
    ("references/review-rigor.md", "### " + HEADING),
    ("skills/test-benchmark/SKILL.md", "`dry_measurement`"),
    ("skills/test-benchmark/SKILL.md", "scripts/test_shape.py"),
    ("skills/test-benchmark/SKILL.md", "Read `verdict`. Do not judge the code again"),
    ("references/review-rigor.md", "Read `verdict`. Do not judge the code again"),
]


@pytest.mark.parametrize(("rel", "phrase"), INSTRUCTIONS, ids=[f"{r}:{p[:32]}" for r, p in INSTRUCTIONS])
def test_each_stage_doc_carries_its_reuse_instruction(rel, phrase):
    assert phrase in (ROOT / rel).read_text(encoding="utf-8")
