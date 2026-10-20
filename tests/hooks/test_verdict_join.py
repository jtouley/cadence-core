"""Verdict join: the call row of each peer call, its PostToolUse join row, and the real hook chain into the P3 gate (gh-66)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import (HANDOFF_HOOK, HOOKS_DIR, code_paths, hook_session, linked_worktrees, permission,
                      permission_reason, read_session, run_py, save_worktree_session, write_files)

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tests" / "fixtures" / "host"
code_paths()
import peer_models  # noqa: E402
import prompt_shape  # noqa: E402
from prompt_shape import write_delegation_record  # noqa: E402


def logs(repo: Path, kind: str, run: str = "run-1") -> list[dict]:
    """The rows of .context/attest/<run>.<kind>.jsonl (kind: calls | joins)."""
    path = repo / ".context" / "attest" / f"{run}.{kind}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- verdict_call_rows: Each orchestrator peer call gets one call row with its stage and decision (gh-66 D1, D2).
#
# The PreToolUse hook appends `{tool_use_id, stage, model_slug, model, decision, run_id, at}`
# to .context/attest/<run>.calls.jsonl for each orchestrator peer call of an active run,
# allowed or denied. The stage is the delegation stage of the peer command. A verdict
# gate later joins its writer to this row (PostToolUse join row) to know the stage.

PILOT_ALL_HAIKU = {"input_tokens": 2, "cache_read_input_tokens": 108867, "cache_creation_input_tokens": 2867}


def call_rows(repo: Path, run: str = "run-1") -> list[dict]:
    return logs(repo, "calls", run)


def test_an_allowed_pr_review_update_call_writes_one_call_row(call, repo):
    code, out, err = call("/pr-review-update 42", tool_use_id="toolu_r4", model=None)
    assert code == 0, err
    assert permission(out) == "allow"
    [row] = call_rows(repo)
    assert row["stage"] == "pr-review-update"
    assert row["decision"] == "allow"
    assert row["model_slug"] is None
    assert row["tool_use_id"] == "toolu_r4"
    assert row["run_id"] == "run-1"


def test_a_fat_pr_review_update_prompt_is_denied_with_the_lean_hint(call, repo):
    code, out, _ = call("/pr-review-update 42 Cross-check claims", tool_use_id="toolu_fat")
    assert code == 0
    assert permission(out) == "deny"
    assert "/pr-review-update" in permission_reason(out)
    [row] = call_rows(repo)
    assert (row["stage"], row["decision"]) == ("pr-review-update", "deny")


def _deny_by_model(call, repo):
    hook_session(repo, "single", resume_slug="plan-review", peer_models={"plan-review": "opus"})
    assert read_session(repo)["peer_models"] == {"plan-review": "opus"}
    return call("/plan-review plan.md", model="sonnet", tool_use_id="toolu_d")


def _deny_by_fork(call, repo):
    return call("/plan-review plan.md", subagent_type="fork", tool_use_id="toolu_d")


def _deny_by_lean(call, repo):
    return call("/plan-review plan.md and also the spec", tool_use_id="toolu_d")


def _deny_by_budget(call, repo):
    transcript = repo.parent / "orchestrator.jsonl"
    message = {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "usage": PILOT_ALL_HAIKU}
    transcript.write_text(json.dumps({"type": "assistant", "message": message}) + "\n", encoding="utf-8")
    body = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(repo),
            "tool_use_id": "toolu_d", "transcript_path": str(transcript),
            "tool_input": {"prompt": "/plan-review plan.md", "subagent_type": "general-purpose", "model": "opus"}}
    return call("", payload=body, context_budget={"mode": "enforce"})


@pytest.mark.parametrize("deny", [_deny_by_model, _deny_by_fork, _deny_by_lean, _deny_by_budget],
                         ids=["model", "fork", "lean", "budget"])
def test_each_deny_path_writes_a_deny_call_row(call, repo, deny):
    code, out, err = deny(call, repo)
    assert code == 0, err
    assert permission(out) == "deny"
    [row] = call_rows(repo)
    assert (row["stage"], row["decision"], row["tool_use_id"]) == ("plan-review", "deny", "toolu_d")


def test_a_nested_call_inside_a_peer_writes_no_call_row(call, repo):
    code, _, err = call("/plan-review plan.md", agent_id="peer-1", tool_use_id="toolu_n")
    assert code == 0, err
    assert call_rows(repo) == []


def test_a_prompt_with_no_peer_command_writes_no_call_row(call, repo):
    code, _, err = call("Explore the repo.", tool_use_id="toolu_x")
    assert code == 0, err
    assert call_rows(repo) == []


@pytest.mark.parametrize(("prompt", "stage", "slug"), [
    ("/test-benchmark plan .context/plans/x.plan.md", "test-benchmark-plan", "test-benchmark-plan"),
    ("/vibe-test docs/spec.md", "vibe-test", "validate"),
])
def test_the_call_row_names_the_delegation_stage_and_model_slug(call, repo, prompt, stage, slug):
    code, _, err = call(prompt, tool_use_id="toolu_s", packet=False)
    assert code == 0, err
    [row] = call_rows(repo)
    assert (row["stage"], row["model_slug"]) == (stage, slug)


def test_the_lean_record_keeps_the_tool_use_id(call, repo):
    code, _, err = call("/pr-review-update 42", tool_use_id="toolu_rec", model=None)
    assert code == 0, err
    record = json.loads((repo / ".context" / "subagent-runs" / "run-1" / "pr-review-update.json")
                        .read_text(encoding="utf-8"))
    assert record["tool_use_id"] == "toolu_rec"


def test_the_stage_vocabulary_names_pr_review_update():
    assert prompt_shape.SLASH_TO_STAGE["/pr-review-update"] == "pr-review-update"
    assert "pr-review-update" in prompt_shape.LEAN_STAGES
    assert peer_models.SLUG_FOR_STAGE["pr-review-update"] is None
    assert [r["command"] for r in prompt_shape.EXTRA_PEER_ROWS] == ["/pr-review-update"]


# --- verdict_join_rows: The PostToolUse Agent hook joins each orchestrator peer call to its subagent (gh-66 D3, PD9, PD10).
#
# Input is the PostToolUse payload recorded on a real host (tests/fixtures/host). The hook
# appends `{tool_use_id, agent_id, resolved_model, at}` to .context/attest/<run>.joins.jsonl.
# It runs none of the PreToolUse checks and prints nothing. A failed write never blocks:
# the gate later finds no join row and fails closed.

def chain(name: str) -> dict:
    return json.loads((HOST / f"claude_agent_chain_{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def post(run_hook, repo):
    """post(payload) -> (exit, stdout, stderr) of the handoff hook on a PostToolUse event in `repo`."""
    def _post(payload: dict):
        body = {**payload, "cwd": str(repo)}
        return run_hook(HANDOFF_HOOK, repo, body, "claude")
    return _post


@pytest.mark.parametrize("name", ["completed", "async"])
def test_a_recorded_post_tool_use_writes_one_join_row(post, repo, name):
    payload = chain(name)["post"]
    code, out, err = post(payload)
    assert code == 0, err
    assert out == ""
    [row] = logs(repo, "joins")
    assert row["tool_use_id"] == payload["tool_use_id"]
    assert row["agent_id"] == payload["tool_response"]["agentId"]
    assert row["resolved_model"] == payload["tool_response"]["resolvedModel"]


def test_a_nested_call_inside_a_peer_writes_no_join_row(post, repo):
    code, _, err = post({**chain("completed")["post"], "agent_id": "peer-1"})
    assert code == 0, err
    assert logs(repo, "joins") == []


def test_post_tool_use_runs_no_pre_tool_use_check(post, repo):
    hook_session(repo, "single", resume_slug="plan-review", peer_models={"plan-review": "opus"})
    code, out, err = post(chain("completed")["post"])
    assert code == 0, err
    assert out == ""
    assert logs(repo, "context") == [] and logs(repo, "models") == [] and logs(repo, "calls") == []
    assert len(logs(repo, "joins")) == 1


def test_a_failed_join_write_exits_0_and_reports_on_stderr(post, repo):
    (repo / ".context" / "attest" / "run-1.joins.jsonl").mkdir(parents=True)  # the append cannot open it
    code, out, err = post(chain("completed")["post"])
    assert code == 0
    assert out == ""
    assert "join row" in err


def test_no_active_run_writes_nothing(post, repo):
    (repo / ".context" / "cadence-session.json").unlink()
    code, _, err = post(chain("completed")["post"])
    assert code == 0, err
    assert not (repo / ".context" / "attest").exists()


def test_the_claude_fragment_registers_post_tool_use_for_the_join():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    groups = [g for g in hooks.get("PostToolUse", [])
              if any("guard_subagent_handoff.py" in h["command"] for h in g["hooks"])]
    assert [g["matcher"] for g in groups] == ["Agent|Task"]
    [command] = [h["command"] for h in groups[0]["hooks"]]
    assert "--host claude" in command


# --- verdict_join_hook_chain: The real hooks join a peer's verdict to the call that started it (gh-66 R7 C-1, PD21).
#
# One recorded host chain (tests/fixtures/host) runs through the installed hooks in order:
# PreToolUse Agent (call row), the subagent's Write (attest row), PostToolUse Agent (join
# row), then the P3 gate. The hooks pick runs by different rules, so the join must not
# depend on which run each hook chose: a linked worktree whose orchestrator works from the
# primary checkout, and two active runs on one .context.

CHAIN = chain("completed")
REVIEW = "reviews/plan_chain.md"
BODY = "RUN_BY_COMMAND: plan-review\nTicket: gh-1\n\nVerdict: READY TO EXECUTE\n"


def _chain_hook(script: str, payload: dict, cwd: Path) -> None:
    proc = run_py(HOOKS_DIR / script, "--host", "claude", cwd=cwd, stdin=payload, CLAUDE_PROJECT_DIR=cwd)
    assert proc.returncode == 0, proc.stderr
    assert '"deny"' not in proc.stdout, proc.stdout


def _peer_writes_review(orchestrator: Path, context: Path, prompt: str, host: dict = CHAIN, body: str = BODY) -> None:
    pre = {**host["pre"], "cwd": str(orchestrator), "tool_input": {**host["pre"]["tool_input"], "prompt": prompt}}
    _chain_hook("guard_subagent_handoff.py", pre, orchestrator)
    target = context / REVIEW
    inner = {**host["inner_write"], "cwd": str(orchestrator),
             "tool_input": {"file_path": str(target), "content": body}}
    _chain_hook("guard_skill_gate.py", inner, orchestrator)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")  # the host's Write, after the hook allowed it
    _chain_hook("guard_subagent_handoff.py", {**host["post"], "cwd": str(orchestrator)}, orchestrator)


def _p3(worktree: Path) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/plan_review_gate.py", "--check", cwd=worktree, CLAUDE_PROJECT_DIR=worktree)


def _runs(tmp_path: Path, case: str) -> tuple[Path, Path, Path]:
    """(orchestrator cwd, gate worktree, primary .context)."""
    primary, trees = linked_worktrees(tmp_path)
    common = {"verdict_join": True, "orchestrator_source": "toplevel"}
    if case == "two-runs":
        save_worktree_session(trees["w1"], pipeline_run_id="run-2", workspace=str(trees["w1"]), **common)
    save_worktree_session(trees["w0"], pipeline_run_id="run-1", workspace=str(trees["w0"]), **common)
    context = primary / ".context"
    # ADR-0010: the enforced handoff pins the run's plan, so the run has one.
    write_files(context, {"plans/x.plan.md": "RUN_BY_COMMAND: plan-create\nTicket: gh-1\n"})
    # Setup, not the case under test: the gate's run has its lean record (the record run is #113).
    write_delegation_record(context, run_id="run-1", stage="plan-review", command="/plan-review x", source="PreToolUse")
    return primary, trees["w0"], context


@pytest.mark.parametrize("case", ["linked-worktree", "two-runs"])
def test_a_plan_review_peer_passes_p3_through_the_real_hooks(tmp_path, case):
    orchestrator, worktree, context = _runs(tmp_path, case)
    _peer_writes_review(orchestrator, context, "/plan-review .context/plans/x.plan.md")
    proc = _p3(worktree)
    assert proc.returncode == 0, proc.stderr
    assert list((context / "attest").glob("*.calls.jsonl")) and list((context / "attest").glob("*.joins.jsonl"))


@pytest.mark.parametrize("case", ["linked-worktree", "two-runs"])
def test_a_plan_update_peer_fails_p3_through_the_real_hooks(tmp_path, case):
    orchestrator, worktree, context = _runs(tmp_path, case)
    # ADR-0010: P4 pins the newest plan review, so a plan-review peer writes one first.
    _peer_writes_review(orchestrator, context, "/plan-review .context/plans/x.plan.md")
    _peer_writes_review(orchestrator, context, "/plan-update .context/plans/x.plan.md", chain("async"),
                        BODY + "\nUpdated by the wrong peer.\n")
    proc = _p3(worktree)
    assert proc.returncode == 1
    assert f"{REVIEW} written by a plan-update peer; this gate accepts plan-review" in proc.stderr
