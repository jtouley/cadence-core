"""E3 core:decision-coverage: what counts as a recorded decision, and for which run."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "scripts"))

from architecture_review import run_architecture_review  # noqa: E402
from findings_io import load_findings  # noqa: E402
from load_stages import stage_slugs  # noqa: E402


# --- architecture_review_emits_core_decision_coverage: Phase 7: architecture-review always emits core decision-coverage findings.

def test_architecture_review_plan_emits_core_decision_coverage(tmp_path, monkeypatch):
    skill = ROOT
    monkeypatch.setenv("CADENCE_HOME", str(skill))
    context = tmp_path / ".context"
    context.mkdir()
    ledger = context / "findings.jsonl"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "src").mkdir()
    (worktree / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    # No ADR/EDR docs → decision-coverage must fire
    import cadence_config

    assert not cadence_config.load(skill, worktree).get("layer_map"), "the resolved config should have an empty layer_map"

    findings = run_architecture_review(
        phase="plan",
        worktree=worktree,
        context_dir=context,
        findings_path=ledger,
        skill_root=skill,
        task_id="TOY",
    )

    assert findings, "expected at least one finding"
    rule_refs = {f["rule_ref"] for f in findings}
    assert "core:decision-coverage" in rule_refs
    stages = stage_slugs(skill)
    for f in findings:
        assert f["stage"] in stages
        assert f["stage"] == "architecture-review-plan"
        assert f["rule_ref"].startswith("core:")
    persisted = load_findings(ledger)
    assert any(r["rule_ref"] == "core:decision-coverage" for r in persisted)


def test_architecture_review_impl_emits_plan_conformance(tmp_path, monkeypatch):
    skill = ROOT
    monkeypatch.setenv("CADENCE_HOME", str(skill))
    context = tmp_path / ".context"
    context.mkdir()
    ledger = context / "findings.jsonl"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "src").mkdir()
    (worktree / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    findings = run_architecture_review(
        phase="impl",
        worktree=worktree,
        context_dir=context,
        findings_path=ledger,
        skill_root=skill,
        task_id="TOY",
    )

    rule_refs = {f["rule_ref"] for f in findings}
    assert "core:plan-conformance" in rule_refs
    assert "core:decision-coverage" in rule_refs
    for f in findings:
        assert f["stage"] == "architecture-review-impl"
        assert f["rule_ref"].startswith("core:")


# --- A proposal counts only while its file exists.

PROPOSAL = "---\ndecision_id: D1\nstatus: proposed\nquestion: q\nchoice: c\nrationale: r\n---\n"


def _coverage(tmp_path, context, monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    return run_architecture_review(
        phase="plan",
        worktree=tmp_path,
        context_dir=context,
        findings_path=context / "findings.jsonl",
        skill_root=ROOT,
        task_id="TOY",
    )


def _must_fix_coverage(findings) -> bool:
    return any(
        f["rule_ref"] == "core:decision-coverage" and f["severity"] == "must-fix"
        for f in findings
    )


def test_architecture_review_decision_coverage_counts_a_proposal_only_while_it_exists(tmp_path, monkeypatch):
    context = tmp_path / ".context"
    (context / "proposals").mkdir(parents=True)
    draft = context / "proposals" / "D1.md"
    draft.write_text(PROPOSAL, encoding="utf-8")
    assert not _must_fix_coverage(_coverage(tmp_path, context, monkeypatch))

    draft.unlink()
    assert _must_fix_coverage(_coverage(tmp_path, context, monkeypatch))


# --- architecture_review_decision_coverage_passes_on_frontmatter_stub: Phase 4: missing DB + valid frontmatter stub passes; review does not sync.

ACCEPTED_STUB = """\
---
decision_id: ADR-0001
status: accepted
question: Why SQLite?
choice: sqlite3
rationale: stdlib
---
# Stub
"""


def test_architecture_review_decision_coverage_passes_on_frontmatter_stub(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))

    context = tmp_path / ".context"
    context.mkdir()
    worktree = tmp_path / "wt"
    adr = worktree / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001.md").write_text(ACCEPTED_STUB, encoding="utf-8")

    findings = run_architecture_review(
        phase="plan",
        worktree=worktree,
        context_dir=context,
        findings_path=context / "findings.jsonl",
        skill_root=ROOT,
        task_id="TOY",
    )
    must = [
        f
        for f in findings
        if f["rule_ref"] == "core:decision-coverage" and f["severity"] == "must-fix"
    ]
    assert must == []


# --- architecture_review_decision_coverage_requires_a_frontmatter_stub: Phase 4: plan prose without a stub or indexed row fails coverage.

PROPOSED_STUB = """\
---
decision_id: ADR-0001
status: proposed
question: Why SQLite?
choice: sqlite3
rationale: stdlib
---
# Stub
"""


def test_architecture_review_decision_coverage_requires_a_frontmatter_stub(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    context = tmp_path / ".context"
    plans = context / "plans"
    plans.mkdir(parents=True)
    (plans / "toy.plan.md").write_text(
        "# Plan\n\nWe chose SQLite for the context index.\n", encoding="utf-8"
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    adr = worktree / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "notes.md").write_text("# We chose SQLite\nNo frontmatter.\n", encoding="utf-8")

    def _review(ledger: Path):
        return run_architecture_review(
            phase="plan",
            worktree=worktree,
            context_dir=context,
            findings_path=ledger,
            skill_root=ROOT,
            task_id="TOY",
        )

    findings = _review(context / "findings.jsonl")
    assert any(f["rule_ref"] == "core:decision-coverage" for f in findings)

    (adr / "0001.md").write_text(PROPOSED_STUB, encoding="utf-8")
    findings_ok = _review(context / "findings-ok.jsonl")
    must = [
        f
        for f in findings_ok
        if f["rule_ref"] == "core:decision-coverage" and f["severity"] == "must-fix"
    ]
    assert must == []


# --- architecture_review_decision_coverage_scope_nit: Coverage from decisions that never mention this ticket stays green but leaves a nit.

ADR = "---\ndecision_id: {did}\nstatus: accepted\nquestion: q\nchoice: c\nrationale: r\n{extra}---\n"


LINK = "links:\n  - rel: covers\n    target_type: task\n    target_id: cd-2\n"


def _scoped_coverage(tmp_path, monkeypatch, extra):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    adr = tmp_path / "docs" / "adr" / "0001.md"
    adr.parent.mkdir(parents=True)
    adr.write_text(ADR.format(did="ADR-1", extra=extra), encoding="utf-8")
    context = tmp_path / ".context"
    context.mkdir()
    findings = run_architecture_review(
        phase="plan",
        worktree=tmp_path,
        context_dir=context,
        findings_path=context / "findings.jsonl",
        skill_root=ROOT,
        task_id="cd-2",
    )
    return [f for f in findings if f["rule_ref"].startswith("core:decision-coverage")]


def test_unrelated_decision_leaves_scope_nit(tmp_path, monkeypatch):
    found = _scoped_coverage(tmp_path, monkeypatch, "")
    assert [(f["rule_ref"], f["severity"]) for f in found] == [
        ("core:decision-coverage-scope", "nit")
    ]


def test_ticket_linked_decision_is_clean(tmp_path, monkeypatch):
    assert _scoped_coverage(tmp_path, monkeypatch, LINK) == []


# --- architecture_review_memory_bank_alone_does_not_satisfy_coverage: Phase 4: Memory Bank presence does not satisfy core:decision-coverage.

def test_architecture_review_memory_bank_alone_does_not_satisfy_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    context = tmp_path / ".context"
    context.mkdir()
    worktree = tmp_path / "wt"
    bank = worktree / "memory-bank"
    bank.mkdir(parents=True)
    (bank / "note.md").write_text("# recalled chat\nwe chose sqlite\n", encoding="utf-8")

    findings = run_architecture_review(
        phase="plan",
        worktree=worktree,
        context_dir=context,
        findings_path=context / "findings.jsonl",
        skill_root=ROOT,
        task_id="TOY",
    )
    assert any(f["rule_ref"] == "core:decision-coverage" for f in findings)
