"""E1, R4 and the peer filter judge the current worktree's own run (cd-47 D1, G-D4).

The root redirect points at the last activation in any worktree. Before cd-47, a
gate run in worktree W1 read that redirect and judged another worktree's run:
its HEAD, its plan, its base, its run id. Now E1 and R4 use only the session of
the worktree they run in, and the planning gates prefer it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import peer_attest  # noqa: E402
from artifact_requirements import peer_written_filter, require_active_session, ticket_scope  # noqa: E402
from conftest import (commit_file, git, ledger_row, linked_worktrees, pr_review, save_worktree_session, write_ledger)  # noqa: E402
from final_consolidate import consolidate, head_review_problems  # noqa: E402
from findings_io import FindingsValidationError  # noqa: E402
from phase_ledger import assess  # noqa: E402
from pipeline_run_binding import worktree_run_session  # noqa: E402

RUN_0 = str(uuid.UUID(int=0x1111_2222_3333_4444_5555_6666_7777_8888))
RUN_1 = str(uuid.UUID(int=0x9999_aaaa_bbbb_cccc_dddd_eeee_ffff_0000))


def _attest(context: Path, run: str, path: Path, agent: str = "peer-agent") -> None:
    rel = path.resolve().relative_to(context.resolve()).as_posix()
    peer_attest.record(context, run, rel, agent, peer_attest.sha256(path.read_bytes()))


def test_worktree_run_session_is_none_for_another_context(tmp_path):
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w0"], pipeline_run_id=RUN_0)
    assert worktree_run_session(primary / ".context", trees["w0"])["pipeline_run_id"] == RUN_0
    assert worktree_run_session(tmp_path / "elsewhere" / ".context", trees["w0"]) is None


def test_e1_session_check_fails_in_a_worktree_without_a_session(tmp_path):
    """G-D4: W0's run must not pass E1 in W1."""
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w0"], pipeline_run_id=RUN_0)
    errors = require_active_session(primary / ".context", cwd=trees["w1"])
    assert errors
    text = " ".join(errors)
    assert f"the active run is in {trees['w0'].resolve()}" in text
    assert f"--workspace {trees['w0'].resolve()}" in text


def test_no_operator_hint_when_the_root_redirect_names_a_completed_run(tmp_path):
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w0"], pipeline_run_id=RUN_0, active_skill=None, status="completed")
    errors = require_active_session(primary / ".context", cwd=trees["w1"])
    assert errors and "the active run is in" not in " ".join(errors)


def test_e1_in_w1_reads_w1s_plan_base_and_commits_only(tmp_path):
    """MF-2: two runs; E1 in W1 must not report W0's plan, base or run."""
    primary, trees = linked_worktrees(tmp_path)
    context = primary / ".context"
    git(primary, "branch", "feat-base", "main")
    plans = context / "plans"
    plans.mkdir(parents=True)
    (plans / "a_cd-32.plan.md").write_text(
        f"RUN_BY_COMMAND: plan-create\nPipeline-Run: {RUN_0}\n### Phase 1: a\n### Phase 2: b\n### Phase 3: c\n", encoding="utf-8"
    )
    (plans / "b_cd-47.plan.md").write_text(
        f"RUN_BY_COMMAND: plan-create\nPipeline-Run: {RUN_1}\n### Phase 1: x\n### Phase 2: y\n", encoding="utf-8"
    )
    save_worktree_session(trees["w1"], pipeline_run_id=RUN_1, task_id="cd-47", ticket_tokens=["47", "cd-47"], base="feat-base")
    # The last activation was W0's, so the root redirect names W0's run.
    save_worktree_session(trees["w0"], pipeline_run_id=RUN_0, task_id="cd-32", ticket_tokens=["32", "cd-32"], base="main")
    commit_file(trees["w1"], "feat: phase 1 the first part")
    state = assess(context, trees["w1"])
    assert Path(state["plan"]).name == "b_cd-47.plan.md"
    assert state["base"] == "feat-base"
    assert state["completed"] == [1]


def test_ticket_scope_reads_the_worktree_session(tmp_path):
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w1"], pipeline_run_id=RUN_1, task_id="cd-47", ticket_tokens=["cd-47"])
    save_worktree_session(trees["w0"], pipeline_run_id=RUN_0, task_id="cd-32", ticket_tokens=["cd-32"])
    patterns = ticket_scope(primary / ".context", cwd=trees["w1"])
    assert any(p.search("plan_cd-47.md") for p in patterns)
    assert not any(p.search("plan_cd-32.md") for p in patterns)


def test_r4_in_w1_reads_w1_head_and_accepts_an_any_run_attested_review(tmp_path, monkeypatch):
    """W1 has no session; the root redirect names a stale claude run in W0."""
    monkeypatch.setenv("CLAUDECODE", "1")
    primary, trees = linked_worktrees(tmp_path)
    context = primary / ".context"
    save_worktree_session(trees["w0"], pipeline_run_id=RUN_0, host="claude")
    commit_file(trees["w0"], "feat: w0 work")
    head_w1 = commit_file(trees["w1"], "feat: w1 work")
    review = pr_review(context, head_w1)
    assert head_review_problems(context, trees["w1"]) != []  # not attested anywhere
    _attest(context, RUN_0, review)
    assert head_review_problems(context, trees["w1"]) == []


def test_r4_with_a_stale_non_claude_root_still_needs_attestation_under_claude_code(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    primary, trees = linked_worktrees(tmp_path)
    context = primary / ".context"
    save_worktree_session(trees["w0"], pipeline_run_id=RUN_0, host="other")
    pr_review(context, commit_file(trees["w1"], "feat: w1 work"))
    assert head_review_problems(context, trees["w1"]) != []


def test_the_filter_uses_the_worktree_runs_log(tmp_path):
    primary, trees = linked_worktrees(tmp_path)
    context = primary / ".context"
    save_worktree_session(trees["w1"], pipeline_run_id=RUN_1, host="claude")
    review = pr_review(context, "a" * 40)
    _attest(context, RUN_0, review)
    accept = peer_written_filter(context, cwd=trees["w1"])
    assert accept is not None and not accept(review)
    _attest(context, RUN_1, review)
    assert accept(review)


def test_headless_prints_the_workspace_it_used(tmp_path):
    _primary, trees = linked_worktrees(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CADENCE", "CLAUDE"))}
    env["CADENCE_HOME"] = str(ROOT)
    for extra in ([], ["--json"]):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "headless.py"), "next", "--workspace", str(trees["w1"]), *extra],
            capture_output=True, text=True, env=env, cwd=tmp_path, timeout=300,
        )
        if extra:
            assert json.loads(proc.stdout)["workspace"] == str(trees["w1"].resolve())
        else:
            assert proc.stdout.splitlines()[0] == f"workspace: {trees['w1'].resolve()}"


# --- R2 on PR #52: binding and attest survivors (M17-M20) -----------------------------------------


def test_a_legacy_session_of_another_worktree_is_not_this_worktrees_run(tmp_path):
    """M17 (G-D4): own context, but the single-file session names W0."""
    primary, trees = linked_worktrees(tmp_path)
    context = primary / ".context"
    context.mkdir()
    (context / "cadence-session.json").write_text(json.dumps(
        {"active_skill": "cadence", "pipeline_run_id": RUN_0, "workspace": str(trees["w0"].resolve())}), encoding="utf-8")
    assert worktree_run_session(context, trees["w1"]) is None
    assert worktree_run_session(context, trees["w0"])["pipeline_run_id"] == RUN_0


@pytest.mark.parametrize("record", ["completed", "redirect"])
def test_a_completed_or_redirect_record_is_no_run(tmp_path, record):
    """M18 (plan F7)."""
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w1"], pipeline_run_id=RUN_1)
    path = next((primary / ".context" / "sessions" / "by-worktree").glob("*.json"))
    data = {"ref": "sessions/by-worktree/other.json"} if record == "redirect" else {
        **json.loads(path.read_text(encoding="utf-8")), "status": "completed"}
    path.write_text(json.dumps(data), encoding="utf-8")
    (primary / ".context" / "cadence-session.json").unlink()
    assert worktree_run_session(primary / ".context", trees["w1"]) is None


def test_attested_any_takes_the_newest_row_by_time_not_by_log_name(tmp_path):
    """M19."""
    context = tmp_path / ".context"
    review = pr_review(context, "a" * 40)
    rel = review.relative_to(context).as_posix()
    old_digest = "0" * 64
    attest = context / "attest"
    attest.mkdir()
    # "z" sorts last but holds the older row; "a" holds the newer row with the current content.
    (attest / "z-run.jsonl").write_text(json.dumps({"path": rel, "agent_id": "P", "sha256": old_digest, "at": 1}) + "\n", encoding="utf-8")
    (attest / "a-run.jsonl").write_text(json.dumps(
        {"path": rel, "agent_id": "P", "sha256": peer_attest.sha256(review.read_bytes()), "at": 2}) + "\n", encoding="utf-8")
    assert peer_attest.attested_any(context, review)
    # A subagent lifetime log with a matching path is not an attest log.
    (attest / "z-run.agents.jsonl").write_text(json.dumps({"path": rel, "agent_id": "P", "sha256": old_digest, "at": 3}) + "\n", encoding="utf-8")
    assert peer_attest.attested_any(context, review)
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    assert not peer_attest.attested_any(context, outside)


def test_e1_names_a_missing_session_and_a_finished_one(tmp_path):
    """M20."""
    primary, trees = linked_worktrees(tmp_path)
    context = primary / ".context"
    context.mkdir()
    assert require_active_session(context, cwd=trees["w1"]) == ["activate-session: missing cadence-session.json"]
    (context / "cadence-session.json").write_text(json.dumps({"active_skill": None}), encoding="utf-8")
    assert require_active_session(context, cwd=trees["w1"]) == ["activate-session: active_skill != 'cadence'"]


def test_an_undecodable_plan_is_not_accepted(tmp_path):
    """M20."""
    from artifact_requirements import run_scope_accept

    context = tmp_path / ".context"
    plan = context / "plans" / "x_cd-47.plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_bytes(b"\xff\xfe not utf-8 \xff")
    assert not run_scope_accept(context, tmp_path)(plan)


SCOPE_KEYS = {"task_id", "scope", "other_task_skipped", "unowned"}


def _r4_ledger(env) -> Path:
    return write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-902-F1"),
                        ledger_row("cd-902-F2", severity="nit", status="fixed", fixed_in="0" * 40), ledger_row("zz-1-F1"),
                        ledger_row("zz-1-F2", severity="nit"))


def test_r4_open_must_fix_of_another_run_does_not_block(two_worktree_runs):
    """cd-49: R4 of run A skips the rows of run B and lists the unowned ones."""
    env = two_worktree_runs
    ledger = _r4_ledger(env)
    result = consolidate(ledger, context_dir=env.ctx, cwd=env.a)
    assert result["close_pass"] == {"open_must_fix": [], "fixed_awaiting_verify": []}
    assert result["scope"] == "task" and result["other_task_skipped"] == 2 and result["unowned"] == ["zz-1-F1"]
    result = consolidate(ledger, context_dir=env.ctx, cwd=env.b)
    assert result["close_pass"]["open_must_fix"] == ["cd-902-F1"] and SCOPE_KEYS <= result.keys()


@pytest.mark.parametrize("where", ["primary", "no-task-id"])
def test_r4_with_no_run_judges_every_row(two_worktree_runs, where):
    env = two_worktree_runs
    cwd = env.primary
    if where == "no-task-id":
        save_worktree_session(env.a, pipeline_run_id=env.run_a, task_id="")
        cwd = env.a
    result = consolidate(_r4_ledger(env), context_dir=env.ctx, cwd=cwd)
    assert result["scope"] == "all" and result["close_pass"]["open_must_fix"] == ["cd-902-F1", "zz-1-F1"]


def test_r4_main_explicit_ledger_keeps_the_run_scope(two_worktree_runs, monkeypatch, capsys):
    import final_consolidate

    env = two_worktree_runs
    ledger = _r4_ledger(env)
    monkeypatch.chdir(env.a)
    monkeypatch.setattr(sys, "argv", ["final_consolidate.py", "--ledger", str(ledger)])
    assert final_consolidate.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == "task" and result["close_pass"]["open_must_fix"] == []


def test_r4_corrupt_row_of_another_task_fails(two_worktree_runs):
    env = two_worktree_runs
    ledger = write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-901-F1", status="deferred", ticket="#1"))
    bad = {k: v for k, v in ledger_row("cd-902-F1").items() if k != "evidence"}
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(bad) + "\n")
    with pytest.raises(FindingsValidationError):
        consolidate(ledger, context_dir=env.ctx, cwd=env.a)


def test_r4_with_no_ledger_file_still_names_the_run(two_worktree_runs, monkeypatch, capsys):
    """R1 N-1: no ledger file in a run's worktree gives `scope: task`, not `all`."""
    import final_consolidate

    env = two_worktree_runs
    monkeypatch.chdir(env.a)
    monkeypatch.setattr(sys, "argv", ["final_consolidate.py"])
    assert final_consolidate.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["scope"], result["task_id"]) == ("task", "cd-901") and result["close_pass"]["open_must_fix"] == []
