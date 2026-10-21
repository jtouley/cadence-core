"""What P9 counts: planning artifacts of the session's ticket, the documented deferral register, hook-written lean records, and the stub peers' artifacts."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import code_paths, enable_all_packs, run_py

ROOT = Path(__file__).resolve().parents[2]
code_paths()
sys.path.insert(0, str(ROOT / "scripts" / "stubs"))
from artifact_requirements import _EXECUTION_ARTIFACTS, check_required_before_execution  # noqa: E402
from entry_invariant import check_execution_entry  # noqa: E402
from load_stages import load_registry, load_stages  # noqa: E402
from prompt_shape import delegation_record_path, delegation_stage_for_prompt, write_delegation_record  # noqa: E402
from stub_pipeline import run_stub_alignment_through_entry  # noqa: E402


# --- entry_invariant_requires_ticket_scoped_artifacts: P9 only counts planning artifacts that belong to the session's ticket.

def _setup(tmp_path, *, task_id, tokens, review_name, review_body="RUN_BY_COMMAND: plan-review\n"):
    ctx = tmp_path / ".context"
    (ctx / "reviews").mkdir(parents=True)
    (ctx / "reviews" / review_name).write_text(review_body, encoding="utf-8")
    (ctx / "cadence-session.json").write_text(
        json.dumps(
            {
                "active_skill": "cadence",
                "task_id": task_id,
                "ticket_tokens": tokens,
                "pipeline_run_id": "run-1",
            }
        ),
        encoding="utf-8",
    )
    write_delegation_record(
        ctx,
        run_id="run-1",
        stage="plan-review",
        command="/plan-review .cursor/plans/foo.plan.md",
        source="test",
    )
    return ctx


def _plan_review_errors(ctx, tmp_path):
    return [
        e
        for e in check_required_before_execution(ctx, worktree=tmp_path)
        if e.startswith("plan-review:")
    ]


def test_other_tickets_review_does_not_count(tmp_path):
    ctx = _setup(tmp_path, task_id="cd-2", tokens=["2", "cd-2"], review_name="plan_cd-1.md")
    assert _plan_review_errors(ctx, tmp_path)


def test_ticket_prefix_does_not_match_longer_id(tmp_path):
    ctx = _setup(tmp_path, task_id="cd-1", tokens=["1", "cd-1"], review_name="plan_cd-12.md")
    assert _plan_review_errors(ctx, tmp_path)


def test_own_ticket_review_counts_by_name_or_content(tmp_path):
    ctx = _setup(tmp_path, task_id="cd-2", tokens=["2", "cd-2"], review_name="plan_cd-2.md")
    assert not _plan_review_errors(ctx, tmp_path)
    ctx2 = _setup(
        tmp_path / "b",
        task_id="cd-2",
        tokens=["2", "cd-2"],
        review_name="plan_refactor.md",
        review_body="RUN_BY_COMMAND: plan-review\nTicket: #2\n",
    )
    assert not _plan_review_errors(ctx2, tmp_path / "b")


# --- entry_invariant_accepts_documented_deferral_register: P9 accepts the register references/deferral-framework.md tells the agent to write.

ZERO = "# Deferral register: cd-7\n\n## Approved deferrals\n\nZero approved deferrals.\n"
NO_TICKET = (
    "# Deferral register: cd-7\n\n## Approved deferrals\n\n"
    "| # | Item | Why not | Ripeness | Follow-up ticket |\n"
    "|---|---|---|---|---|\n"
    "| 1 | widget | needs design | | |\n"
)


def _deferral_errors(tmp_path: Path, name: str, body: str) -> list[str]:
    ctx = tmp_path / ".context"
    (ctx / "deferrals").mkdir(parents=True)
    (ctx / "deferrals" / name).write_text(body, encoding="utf-8")
    (ctx / "cadence-session.json").write_text(
        json.dumps({"active_skill": "cadence", "task_id": "cd-7", "ticket_tokens": ["7", "cd-7"]}),
        encoding="utf-8",
    )
    errors = check_required_before_execution(ctx, worktree=tmp_path)
    return [e for e in errors if e.startswith("deferral-register")]


def test_documented_markdown_register_clears_p9(tmp_path):
    assert _deferral_errors(tmp_path, "cd-7_deferral-register.md", ZERO) == []


def test_register_failing_adjudication_blocks_p9(tmp_path):
    errors = _deferral_errors(tmp_path, "cd-7_deferral-register.md", NO_TICKET)
    assert errors and "deferral-missing-ticket" in errors[0]


def test_other_tickets_register_does_not_count(tmp_path):
    assert _deferral_errors(tmp_path, "cd-8_deferral-register.md", ZERO.replace("cd-7", "cd-8"))


# --- entry_invariant_claude_hook_records: cd-64 AC7, AC11, AC15: P9 passes on a marked Claude Code run whose peers the hook recorded, with no begin.

# The P9 delegated stages, from the gate itself (not a copy).
def _stage(spec: dict) -> str:
    stage = spec.get("delegation_stage", spec["label"])
    assert isinstance(stage, str), f"P9 spec {spec['label']!r} names no stage"
    return stage


STAGES = ["plan-create"] + [_stage(spec) for spec in _EXECUTION_ARTIFACTS if spec["label"] != "deferral-register"]
LEAN = {
    "plan-create": "/plan-create plan.md",
    "plan-review": "/plan-review plan.md",
    "de-adversarial-reviewer": "/de-adversarial-reviewer plan-gate plan.md",
    "test-benchmark-plan": "/test-benchmark plan plan.md",
}
MARKERS = "\n".join([
    "RUN_BY_COMMAND: plan-create", "RUN_BY_COMMAND: plan-review",
    "RUN_BY_SKILL: de-adversarial-reviewer", "RUN_BY_SKILL: test-benchmark",
])


def _hook(worktree: Path, prompt: str) -> subprocess.CompletedProcess[str]:
    body = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(worktree),
            "tool_input": {"prompt": prompt, "subagent_type": "general-purpose"}}
    return run_py("hooks/guard_subagent_handoff.py", "--host", "claude", cwd=worktree, stdin=body)


def _complete(worktree: Path, stage: str) -> subprocess.CompletedProcess[str]:
    artifact = worktree / f"{stage}.out.md"
    artifact.write_text(MARKERS + "\n", encoding="utf-8")
    return run_py("scripts/subagent_provenance.py", "complete", "--stage", stage, "--artifact", str(artifact), cwd=worktree)


def test_p9_passes_on_hook_records_and_rejects_a_begin_record(tmp_path, monkeypatch):
    worktree = tmp_path / "toy"
    shutil.copytree(ROOT / "tests" / "fixtures" / "toy_repo", worktree)
    enable_all_packs(worktree)  # P6 and P7 count only with their packs on
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    context = worktree / ".context"
    run_stub_alignment_through_entry(worktree=worktree, context_dir=context)

    session_file = context / "cadence-session.json"
    session = json.loads(session_file.read_text(encoding="utf-8"))
    session.update(lean_record_by_hook=True, host="claude")
    session_file.write_text(json.dumps(session), encoding="utf-8")
    run = session["pipeline_run_id"]
    shutil.rmtree(context / "subagent-runs" / run)
    # cd-63: on Claude Code, P6 counts only attested peer writes. The stub writes its report
    # directly, so attest it as the subagent write hook does for a real peer.
    import hashlib

    import peer_attest

    for report in sorted((context / "adversarial").glob("adversarial_*.md")):
        peer_attest.record(context, run, report.relative_to(context).as_posix(), "stub-peer",
                           hashlib.sha256(report.read_bytes()).hexdigest())

    assert set(LEAN) == set(STAGES)
    assert all(delegation_stage_for_prompt(LEAN[s]) == s for s in STAGES)
    assert {delegation_stage_for_prompt(r["command"]) for r in load_stages(ROOT)} >= set(STAGES)

    errors = check_execution_entry(context, skill_root=ROOT, worktree=worktree)
    for stage in STAGES:
        assert f"{stage}: missing lean delegation record" in errors

    for stage in STAGES:
        proc = _hook(worktree, LEAN[stage])
        assert proc.returncode == 0, proc.stderr
        proc = _complete(worktree, stage)
        assert proc.returncode == 0, proc.stderr
    assert check_execution_entry(context, skill_root=ROOT, worktree=worktree) == []

    path = delegation_record_path(context, run, "plan-review")
    record = json.loads(path.read_text(encoding="utf-8"))
    record.pop("source")
    path.write_text(json.dumps(record), encoding="utf-8")
    errors = check_execution_entry(context, skill_root=ROOT, worktree=worktree)
    assert any("only the hook writes the lean delegation record (source none)" in e for e in errors)


# --- fixture_pipeline_stub_peers_reach_entry_invariant: Phase 8 #1: stub peers drive Alignment→Execution gates on toy_repo to P9.

def test_fixture_pipeline_stub_peers_reach_entry_invariant(tmp_path, monkeypatch):
    # Arrange: copy toy_repo under gated prefix src/
    toy_src = ROOT / "tests" / "fixtures" / "toy_repo"
    worktree = tmp_path / "toy"
    shutil.copytree(toy_src, worktree)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))

    context = worktree / ".context"
    # Act: stub peers write required artifacts with RUN_BY markers + slug ids
    report = run_stub_alignment_through_entry(worktree=worktree, context_dir=context)

    # Assert
    assert report["reached"] == "entry-invariant"
    assert set(report["slugs_executed"]).issubset({r["slug"] for r in load_registry(ROOT)["stages"]})
    assert "activate-session" in report["slugs_executed"]
    assert "plan-create" in report["slugs_executed"]
    assert "plan-review" in report["slugs_executed"]
    errors = check_execution_entry(context, skill_root=ROOT, worktree=worktree)
    assert errors == [], f"entry invariant failed: {errors}"
    session = json.loads((context / "cadence-session.json").read_text(encoding="utf-8"))
    assert session["active_skill"] == "cadence"
    marker = context / "gates" / f"entry-invariant-{session['pipeline_run_id']}.ok"  # cd-76: per run
    # P9 main() writes marker; library check_execution_entry does not — invoke main path
    from entry_invariant import main as entry_main  # noqa: E402

    monkeypatch.setattr("entry_invariant.ctx", lambda: context)
    monkeypatch.setattr(sys, "argv", ["entry_invariant.py", "--phase", "execution"])
    assert entry_main() == 0
    assert marker.is_file(), "P9 success must write gates/entry-invariant.ok"
