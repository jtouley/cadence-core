"""The peer text names the report shape that the P6 gate reads."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import artifact_requirements as ar  # noqa: E402

SKILL = (ROOT / "skills" / "de-adversarial-reviewer" / "SKILL.md").read_text(encoding="utf-8")
UPDATE = (ROOT / "commands" / "plan-update.md").read_text(encoding="utf-8")
PIPELINE = (ROOT / "SKILL.md").read_text(encoding="utf-8")


def test_the_skill_names_each_line_the_gate_reads():
    for needle in ("Mode: plan-gate", "Mode: spec-pressure", "Pipeline-Run:", "## Blocking issues",
                   "## Conditions", "### B-n. <title>", "### C-n. <title>"):
        assert needle in SKILL, needle


def test_each_verdict_line_in_the_skill_reads_as_that_verdict():
    for verdict in ("APPROVE", "APPROVE WITH CONDITIONS", "REQUEST CHANGES"):
        assert f"`Verdict: {verdict}`" in SKILL
        assert ar.adversarial_verdict_in_text(f"Verdict: {verdict}\n") == verdict


def test_the_skill_names_the_round_files_and_the_round_rule():
    assert "adversarial_{slug}.md" in SKILL and "adversarial_{slug}_r{n}.md" in SKILL
    assert "adversarial_{slug}_spec-pressure.md" in SKILL
    assert "Do not write to a file that exists." in SKILL
    assert "Write the verdict last" in SKILL and "only in a table row" in SKILL


def test_the_example_item_headings_give_ids():
    text = "## Blocking issues\n\n### B-1. one\n\n## Conditions\n\n### C-1. two\n"
    assert ar.adversarial_ids(text) == ({"B-1"}, {"C-1"})


def test_plan_update_names_the_p6_carry():
    for needle in ("three P6 cycles", "--heading", "Do not run P6 again", "One budget", "`C-n`"):
        assert needle in UPDATE, needle


def test_the_pipeline_section_names_the_p6_loop():
    for needle in ("**P6:**", "at most three cycles", "new Agent call for each P3 and P6 review round",
                   "send these peers in one message, in parallel",
                   "When the gate output names a step, do that step before the `NEXT` command."):
        assert needle in PIPELINE, needle
    assert ar.adversarial_cycle_limit() == 3
