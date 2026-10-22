"""A verdict counts only from the peer of its stage.

Four runs had a /plan-update peer write the P3 review of its own update, and P3 passed.
On a `verdict_join` run, each verdict gate joins the file's newest attest row to the call
that started its writer, and accepts the file only from a writer of the gate's stage
(`writer_stages` in stages.json). These cases go through the real gate entries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import branch_repo, code_paths, git_repo, head_sha

ROOT = Path(__file__).resolve().parents[2]
code_paths()

import artifact_requirements as ar  # noqa: E402
import gate_common  # noqa: E402
import test_benchmark_gate  # noqa: E402
from artifact_requirements import REVIEW_RIGOR_SECTIONS  # noqa: E402
from prompt_shape import write_delegation_record  # noqa: E402
from verdict_join import peer_wrote, tamper, verdict_join_session  # noqa: E402

PLAN_REVIEW = "RUN_BY_COMMAND: plan-review\nTicket: gh-1\n\nVerdict: {verdict}\n"
NO_JOIN = "no join row: call the peer again from the orchestrator"


@pytest.fixture(name="ctx")
def joined_ctx(tmp_path, monkeypatch):
    repo = branch_repo(tmp_path / "repo")  # one commit: R2 reads the change against it
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    context = repo / ".context"
    verdict_join_session(context, workspace=str(repo))
    for stage, command in (("plan-review", "/plan-review x"), ("vibe-test", "/vibe-test x"),
                           ("de-adversarial-reviewer", "/de-adversarial-reviewer plan-gate x"),
                           ("pr-review-local", "/pr-review-local 1"), ("test-benchmark-plan", "/test-benchmark plan x"),
                           ("test-benchmark-pr", "/test-benchmark x")):
        write_delegation_record(context, run_id="run-1", stage=stage, command=command, source="PreToolUse")
    return context


def p3(ctx, capsys) -> tuple[int, str]:
    code = gate_common.run_artifact_gate("plan-review-gate", ctx, check=True)
    return code, capsys.readouterr().err


def plan_review_file(ctx, rel="reviews/plan_x.md", *, stage, verdict="READY TO EXECUTE", agent="a", **switch) -> Path:
    return peer_wrote(ctx, "run-1", rel, agent_id=agent, stage=stage, body=PLAN_REVIEW.format(verdict=verdict),
                      **switch)


# --- P3: the incident ---------------------------------------------------------------------------

def test_a_review_by_a_plan_update_peer_fails_p3(ctx, capsys):
    plan_review_file(ctx, stage="plan-update")
    code, err = p3(ctx, capsys)
    assert code == 1
    assert "reviews/plan_x.md written by a plan-update peer; this gate accepts plan-review" in err
    assert ar.loop_cycles(ctx, ar.P3_LOOP) == 0


def test_a_review_by_the_plan_review_peer_passes_p3(ctx, capsys):
    plan_review_file(ctx, stage="plan-review")
    code, err = p3(ctx, capsys)
    assert code == 0, err
    assert ar.loop_cycles(ctx, ar.P3_LOOP) == 1


@pytest.mark.parametrize("switch", [{"join": False}, {"call": False}, {"decision": "deny"}],
                         ids=["no-join", "no-call", "denied"])
def test_each_no_stage_cause_fails_p3_with_its_reason_and_no_rewrite(ctx, capsys, switch):
    plan_review_file(ctx, stage="plan-review", **switch)
    code, err = p3(ctx, capsys)
    assert code == 1
    cause = {"join": NO_JOIN, "call": "no call row for the join", "decision": "the peer call was denied"}
    assert cause[next(iter(switch))] in err
    assert "rewrite" not in err and "Write or Edit" not in err


def test_two_review_peers_each_pass_and_count(ctx, capsys):
    plan_review_file(ctx, stage="plan-review", agent="a", verdict="READY WITH CHANGES")
    plan_review_file(ctx, "reviews/plan_x_c2.md", stage="plan-review", agent="b")
    assert p3(ctx, capsys)[0] == 0
    assert ar.loop_cycles(ctx, ar.P3_LOOP) == 2


def test_an_edit_by_the_review_peer_owns_the_file(ctx, capsys):
    plan_review_file(ctx, stage="plan-update", agent="w", verdict="READY WITH CHANGES")
    plan_review_file(ctx, stage="plan-review", agent="r")
    assert p3(ctx, capsys)[0] == 0


def test_an_old_run_keeps_the_old_check(ctx, capsys):
    verdict_join_session(ctx, workspace=str(ctx.parent), verdict_join=False)
    plan_review_file(ctx, stage="plan-update")
    assert p3(ctx, capsys)[0] == 0


@pytest.mark.parametrize("host", ["cursor", "pi"])
def test_other_hosts_keep_their_check(ctx, host):
    verdict_join_session(ctx, workspace=str(ctx.parent), host=host)
    assert ar.peer_written_filter(ctx, slug="plan-review") is None
    assert ar.stage_rejections(ctx, [ctx / "reviews" / "plan_x.md"], slug="plan-review") == []


# --- P3 verdict choice (MF-5) and P9 (PD17) -----------------------------------------------------

def _p9_plan_review_errors(ctx) -> list[str]:
    return [e for e in ar.check_required_before_execution(ctx, worktree=ctx.parent) if e.startswith("plan-review")]


def test_a_newer_self_review_does_not_decide_the_p3_verdict(ctx, monkeypatch):
    # The guard puts CADENCE_HOME/scripts first on sys.path at import: keep it this tree.
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    monkeypatch.setattr(sys, "path", list(sys.path))
    import pipeline_stop_guard
    from conftest import review_cycles

    review_cycles(ctx.parent, plan=2)  # below the limit, the error names the verdict that decides
    plan_review_file(ctx, stage="plan-review", agent="a", verdict="READY WITH CHANGES")
    newer = plan_review_file(ctx, "reviews/plan_x_c2.md", stage="plan-update", agent="b")
    import os
    os.utime(newer, (newer.stat().st_mtime + 10, newer.stat().st_mtime + 10))
    session = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    assert ar.select_plan_review(ctx, session=session)[0].name == "plan_x.md"
    [error] = ar.check_plan_review_verdict(ctx, session=session)
    assert "READY WITH CHANGES" in error
    assert any("READY WITH CHANGES" in e for e in _p9_plan_review_errors(ctx))
    assert pipeline_stop_guard.latest_plan_verdict(ctx, session) == "READY WITH CHANGES"


def test_a_newer_review_by_the_review_peer_decides(ctx):
    plan_review_file(ctx, stage="plan-review", agent="a", verdict="READY WITH CHANGES")
    newer = plan_review_file(ctx, "reviews/plan_x_c2.md", stage="plan-review", agent="b")
    import os
    os.utime(newer, (newer.stat().st_mtime + 10, newer.stat().st_mtime + 10))
    session = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    assert ar.select_plan_review(ctx, session=session)[0].name == "plan_x_c2.md"
    assert ar.check_plan_review_verdict(ctx, session=session) == []


def test_p9_fails_when_only_a_wrong_stage_review_exists(ctx):
    plan_review_file(ctx, stage="plan-update")
    session = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    [error] = ar.check_plan_review_verdict(ctx, session=session)
    assert error.startswith("plan-review: no review from a plan-review peer; ")
    assert "plan-update" in error
    assert error in _p9_plan_review_errors(ctx)


def test_p9_ignores_a_review_of_another_run(ctx):
    peer_wrote(ctx, "run-1", "reviews/plan_y.md", agent_id="a", stage="plan-update",
               body=PLAN_REVIEW.format(verdict="READY TO EXECUTE") + "Pipeline-Run: 99999999-9999-4999-8999-999999999999\n")
    session = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    assert ar.check_plan_review_verdict(ctx, session=session) == []


def test_the_old_run_keeps_the_p3_verdict_choice(ctx):
    verdict_join_session(ctx, workspace=str(ctx.parent), verdict_join=False)
    plan_review_file(ctx, stage="plan-review", agent="a", verdict="READY WITH CHANGES")
    newer = plan_review_file(ctx, "reviews/plan_x_c2.md", stage="plan-update", agent="b")
    import os
    os.utime(newer, (newer.stat().st_mtime + 10, newer.stat().st_mtime + 10))
    session = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    assert ar.select_plan_review(ctx, session=session)[0].name == "plan_x_c2.md"


# --- every verdict gate, through its real entry ---------------------------------------------------

BENCH = json.dumps({"RUN_BY_SKILL": "test-benchmark", "ticket": "gh-1", "verdict": "PASS",
                    "scores": {d: 8 for d in ("coverage_of_behavior", "assertion_strength", "dry", "layer_correctness",
                                              "failure_paths", "mutation_resilience")},
                    "adversarial_inputs": [{"input": "x", "expected": "y", "observed": "y"}],
                    "dry_measurement": {"helpers_reimplemented": [], "helpers_reused": [], "thin_files": []}})
# R1 reads the reviewed commit: `_bodied` puts the HEAD of the ctx repo in it.
PR_BODY = "RUN_BY_COMMAND: pr-review-local\nLast reviewed commit: {head}\n" + "".join(f"\n## {h}\nnone\n" for h in REVIEW_RIGOR_SECTIONS)
GATES = {
    "A5": (lambda c: gate_common.run_artifact_gate("validate-gate", c, check=True, worktree=c.parent),
           "vibetest/x.md", "RUN_BY_SKILL: vibe-test\nPipeline-Run: run-1\n**Tracker:** github-issues\n**Ticket:** #1\nScope: gh-1\n", "vibe-test"),
    "P6": (lambda c: gate_common.run_artifact_gate("adversarial-review-gate", c, check=True),
           "adversarial/adversarial_x.md",
           "RUN_BY_SKILL: de-adversarial-reviewer\nTicket: gh-1\nMode: plan-gate\nVerdict: APPROVE\n",
           "de-adversarial-reviewer"),
    "R1": (lambda c: gate_common.run_artifact_gate("pr-review-local-gate", c, check=True),
           "prs/reviews/PR_REVIEW_#1.md", PR_BODY, "pr-review-local"),
    "P7": (lambda c: test_benchmark_gate.run(c, mode="plan", check=True),
           "benchmarks/x_plan.json", BENCH, "test-benchmark-plan"),
    "R2": (lambda c: test_benchmark_gate.run(c, mode="code", check=True),
           "benchmarks/x_code.json", BENCH, "test-benchmark-pr"),
}


def _bodied(ctx: Path, body: str) -> str:
    return body.replace("{head}", head_sha(ctx.parent))


@pytest.mark.parametrize("block", sorted(GATES))
def test_each_verdict_gate_rejects_a_wrong_stage_writer(ctx, capsys, block):
    gate, rel, body, _stage = GATES[block]
    body = _bodied(ctx, body)
    peer_wrote(ctx, "run-1", rel, agent_id="a", stage="plan-update", body=body)
    assert gate(ctx) == 1
    err = capsys.readouterr().err
    assert f"{rel} written by a plan-update peer" in err


@pytest.mark.parametrize("block", sorted(GATES))
def test_each_verdict_gate_takes_its_own_stage_writer(ctx, capsys, block):
    gate, rel, body, stage = GATES[block]
    body = _bodied(ctx, body)
    peer_wrote(ctx, "run-1", rel, agent_id="a", stage=stage, body=body)
    assert gate(ctx) == 0, capsys.readouterr().err


def test_r1_accepts_an_r4_append_and_rejects_a_plan_update_append(ctx, capsys):
    """R2 MF-1: the stop guard runs R1 again after R4 appends to the same review file."""
    rel, body = "prs/reviews/PR_REVIEW_#1.md", _bodied(ctx, PR_BODY)
    peer_wrote(ctx, "run-1", rel, agent_id="r1", stage="pr-review-local", body=body)
    peer_wrote(ctx, "run-1", rel, agent_id="r4", stage="pr-review-update", body=body + "\nUpdate: ok\n")
    assert gate_common.run_artifact_gate("pr-review-local-gate", ctx, check=True) == 0, capsys.readouterr().err
    peer_wrote(ctx, "run-1", rel, agent_id="pu", stage="plan-update", body=body + "\nforged\n")
    assert gate_common.run_artifact_gate("pr-review-local-gate", ctx, check=True) == 1
    assert "written by a plan-update peer" in capsys.readouterr().err


def test_p6_names_the_writer_stage_not_a_missing_report(ctx, capsys):
    peer_wrote(ctx, "run-1", "adversarial/adversarial_x.md", agent_id="a", stage="plan-update",
               body="RUN_BY_SKILL: de-adversarial-reviewer\nTicket: gh-1\nMode: plan-gate\nVerdict: APPROVE\n")
    [error] = ar.check_adversarial_verdict(ctx)
    assert "written by a plan-update peer; this gate accepts de-adversarial-reviewer" in error
    assert "no plan-gate report of run" not in error


def test_the_p6_cycle_count_ignores_a_wrong_stage_writer(tmp_path):
    from adversarial_loop import Loop

    loop = Loop(git_repo(tmp_path / "loop"), verdict_join=True)
    path = loop.report(attest=False)
    loop.attest(path, "w", stage="plan-update")
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    loop.attest(path, "r", stage="de-adversarial-reviewer")
    assert ar.loop_cycles(loop.ctx, ar.P6_LOOP, loop.session) == 1


def test_a_changed_file_keeps_the_attest_text(ctx, capsys):
    tamper(plan_review_file(ctx, stage="plan-review"))
    code, err = p3(ctx, capsys)
    assert code == 1
    assert "no verdict file whose content matches" in err and "written by a" not in err


def test_an_unknown_stage_slug_fails_the_gate_with_a_line(ctx, capsys, monkeypatch):
    """R9 C-2: a typo slug on a verdict_join run fails with a message, not a traceback."""
    plan_review_file(ctx, stage="plan-review")
    spec = {**gate_common.ARTIFACT_GATES["plan-review-gate"], "stage_slug": "plan-review-gate"}
    monkeypatch.setitem(gate_common.ARTIFACT_GATES, "plan-review-gate", spec)
    code, err = p3(ctx, capsys)
    assert code == 1
    assert "no writer_stages for plan-review-gate" in err


def test_an_old_run_never_reads_writer_stages(ctx, capsys, monkeypatch):
    import load_stages

    verdict_join_session(ctx, workspace=str(ctx.parent), verdict_join=False)
    plan_review_file(ctx, stage="plan-review")

    def boom(*_a, **_k):
        raise AssertionError("writer_stages read on a run with no verdict_join")

    monkeypatch.setattr(load_stages, "writer_stages", boom)
    assert p3(ctx, capsys)[0] == 0


# --- the any-run path (no worktree run, CLAUDECODE=1): verdict_join of the row's run ----------

def test_the_any_run_path_checks_the_stage_per_file(tmp_path, monkeypatch, capsys):
    import load_stages

    ctx = tmp_path / "ctx"
    for key, run, join in (("a", "run-a", False), ("b", "run-b", True)):
        record = {"active_skill": "cadence", "pipeline_run_id": run, "host": "claude", "task_id": "gh-1"}
        if join:
            record["verdict_join"] = True
        (ctx / "sessions" / "by-worktree").mkdir(parents=True, exist_ok=True)
        (ctx / "sessions" / "by-worktree" / f"{key}.json").write_text(json.dumps(record), encoding="utf-8")
    old = peer_wrote(ctx, "run-a", "reviews/plan_a.md", agent_id="a", stage="plan-update")
    new = peer_wrote(ctx, "run-b", "reviews/plan_b.md", agent_id="b", stage="plan-update")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")
    calls = []
    real = load_stages.writer_stages

    def spy(slug, **kwargs):
        calls.append(slug)
        raise KeyError(slug)

    monkeypatch.setattr(load_stages, "writer_stages", spy)
    check = ar.peer_written_filter(ctx, slug="plan-review")
    assert calls == []
    assert check(old) is True and calls == []
    assert check(new) is False
    [line] = ar.stage_rejections(ctx, [old, new], slug="plan-review")
    assert line.startswith("plan-review: no writer_stages for plan-review in ")
    monkeypatch.setattr(load_stages, "writer_stages", real)
    [line] = ar.stage_rejections(ctx, [old, new], slug="plan-review")
    assert "reviews/plan_b.md written by a plan-update peer" in line


def test_writer_stages_reads_the_stages_json_of_its_own_tree(tmp_path):
    """R9 C-1: CADENCE_HOME names an old tree; the field ships with the code that reads it."""
    import os
    import subprocess

    old = tmp_path / "old"
    old.mkdir()
    rows = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))
    for row in rows["stages"]:
        row.pop("writer_stages", None)
    (old / "stages.json").write_text(json.dumps(rows), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CADENCE_SKILL_ROOT"}
    env["CADENCE_HOME"] = str(old)
    proc = subprocess.run([sys.executable, "-c", "import load_stages; print(sorted(load_stages.writer_stages('plan-review')))"],
                          cwd=ROOT / "scripts", env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "['plan-review']"


def _running(ctx: Path, agent: str, *, stopped: bool) -> None:
    import peer_attest

    peer_attest.agent_event(ctx, "run-1", agent, "start")
    if stopped:
        peer_attest.agent_event(ctx, "run-1", agent, "stop")


def test_a_peer_that_runs_its_gate_before_it_returns_is_told_to_wait_not_to_call_again(ctx, capsys):
    """A foreground peer has no join row until PostToolUse, after it returns."""
    plan_review_file(ctx, stage="plan-review", join=False)
    _running(ctx, "a", stopped=False)
    code, err = p3(ctx, capsys)
    assert code == 1
    assert "no join row yet: the peer is still running" in err
    assert "call the peer again" not in err and "rewrite" not in err


def test_a_peer_that_returned_with_no_join_row_still_names_the_cause(ctx, capsys):
    plan_review_file(ctx, stage="plan-review", join=False)
    _running(ctx, "a", stopped=True)
    code, err = p3(ctx, capsys)
    assert code == 1
    assert "no join row: call the peer again from the orchestrator" in err
