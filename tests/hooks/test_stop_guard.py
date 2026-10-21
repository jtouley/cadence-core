"""pipeline_stop_guard: when a stop is held, passed or escalated, and for which run."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import (peer_summary, HOOKS_DIR, SESSION_LAYOUTS, activate, activated_repo, assert_decision, code_paths, enable_all_packs, fake_gh, git, git_repo, hook_session, host_shape, pushed_repo, read_session, run_py, stop_decision)

ROOT = Path(__file__).resolve().parents[2]
code_paths()
os.environ["CADENCE_HOME"] = str(ROOT)
import pipeline_stop_guard as guard  # noqa: E402
import session_store  # noqa: E402
from p3_loop import p3_loop, p3_review, plan  # noqa: E402

pytestmark = pytest.mark.integration
GUARD = "pipeline_stop_guard.py"


def _session(slug: str, **fields) -> dict:
    return {"active_skill": "cadence", "resume_slug": slug, **fields}


def _reviews(ctx: Path, *files: tuple[str, str]) -> None:
    """Write each (name, text) under ctx/reviews. Each file is newer than the one before it (mtime 1, 2, ...)."""
    reviews = ctx / "reviews"
    reviews.mkdir()
    for at, (name, text) in enumerate(files, start=1):
        (reviews / name).write_text(text, encoding="utf-8")
        os.utime(reviews / name, (at, at))


# --- pipeline_stop_guard_branches: Phase 5: stop guard allow/block/escalate/fail-closed branches.

@pytest.mark.parametrize("session, kwargs, want, needles", [
    pytest.param(None, {}, "allow", (), id="no_session_allows"),
    pytest.param(_session("implement", stop_refusals=0), {}, "block", ("early stop in execution",),
                 id="execution_blocks"),
    pytest.param(_session("implement", hard_stop=True), {}, "allow", (), id="hard_stop_allows"),
    pytest.param(_session("pr-review-local", stop_refusals=3), {}, "escalate", (), id="fourth_refusal_escalates"),
    pytest.param(None, {"corrupt": True}, "block", ("FAIL-CLOSED",), id="corrupt_session_blocks"),
    # A real run leaves resume_slug at activate-session. The review file is the evidence.
    pytest.param(_session("activate-session", stop_refusals=0), {"plan_verdict": "READY WITH CHANGES"}, "block",
                 ("/plan-update", "READY WITH CHANGES"), id="blocking_verdict_blocks_even_when_slug_is_activation"),
    pytest.param(_session("plan-review", stop_refusals=0), {"plan_verdict": "READY WITH CHANGES"}, "block",
                 ("/plan-update",), id="plan_review_slug_with_blocking_verdict_blocks"),
    pytest.param(_session("plan-update", stop_refusals=0), {"plan_verdict": "NOT READY"}, "block",
                 ("/plan-review",), id="plan_update_slug_points_at_plan_review"),
    pytest.param(_session("architecture-review-plan", stop_refusals=0), {"plan_verdict": "READY TO EXECUTE"},
                 "allow", (), id="ready_plan_at_architecture_review_allows"),
    pytest.param(_session("plan-review", stop_refusals=0), {"plan_verdict": "READY TO EXECUTE"}, "block",
                 ("architecture-review-plan",), id="ready_verdict_still_on_plan_review_continues"),
    pytest.param(_session("plan-review", hard_stop=True), {"plan_verdict": "READY WITH CHANGES"}, "allow", (),
                 id="hard_stop_allows_a_blocking_verdict"),
])
def test_pipeline_stop_guard_decides(session, kwargs, want, needles):
    decision, msg = guard.decide(session, **kwargs)
    assert decision == want
    for needle in needles:
        assert needle in msg


def test_other_ticket_ready_review_does_not_clear_this_hold(tmp_path):
    (tmp_path / "cadence-session.json").write_text(
        json.dumps(
            {
                "active_skill": "cadence",
                "task_id": "cd-1",
                "ticket_tokens": ["cd-1"],
                "pipeline_run_id": "run-1",
            }
        ),
        encoding="utf-8",
    )
    _reviews(tmp_path, ("plan_cd-1.md", "RUN_BY_COMMAND: plan-review\nVerdict: NOT READY\n"),
             ("plan_cd-12.md", "RUN_BY_COMMAND: plan-review\nVerdict: READY TO EXECUTE\n"))
    assert guard.latest_plan_verdict(tmp_path) == "NOT READY"
    from artifact_requirements import check_plan_review_verdict

    errors = check_plan_review_verdict(tmp_path)
    assert errors and "NOT READY" in errors[0]


def test_bare_not_ready_blocks_the_entry_check(tmp_path):
    _reviews(tmp_path, ("plan_r3.md", "# Review\n\n**NOT READY**\n"))
    from artifact_requirements import check_plan_review_verdict

    errors = check_plan_review_verdict(tmp_path)
    assert errors and "NOT READY" in errors[0]


def test_newer_review_without_a_verdict_does_not_clear_the_hold(tmp_path):
    _reviews(tmp_path, ("plan_a.md", "Verdict: NOT READY\n"), ("plan_b.md", "RUN_BY_COMMAND: plan-review\n"))
    assert guard.latest_plan_verdict(tmp_path) == "NOT READY"


def test_latest_plan_verdict_reads_a_bare_heading(tmp_path):
    """Reviews in the addendum run use a heading, not a Verdict: line."""
    _reviews(tmp_path, ("plan_r3.md", "# Review\n\n**READY WITH CHANGES**\n"))
    assert guard.latest_plan_verdict(tmp_path) == "READY WITH CHANGES"


# --- stop_guard_budget_and_session_layouts: Stop guard: subagents never spend the run's stop budget; corrupt sessions escalate; pointers survive.
#

MAIN = {"cursor": {"hook_event_name": "stop"}, "claude": {"hook_event_name": "Stop"}}
SUB = {"cursor": {"hook_event_name": "subagentStop"}, "claude": {"hook_event_name": "SubagentStop"}}


@pytest.mark.parametrize("layout", SESSION_LAYOUTS)
@pytest.mark.parametrize("host", ["cursor", "claude"])
def test_subagent_stops_do_not_spend_the_main_budget(tmp_path, run_hook, host, layout):
    repo = git_repo(tmp_path)
    hook_session(repo, layout, resume_slug="implement")
    for _ in range(3):
        code, out, _ = run_hook(GUARD, repo, SUB[host], host, "--subagent")
        assert code == 0 and stop_decision(host, out) == "block"
    assert read_session(repo).get("stop_refusals", 0) == 0
    _, out, _ = run_hook(GUARD, repo, MAIN[host], host)
    assert stop_decision(host, out) == "block"
    assert read_session(repo)["stop_refusals"] == 1


def test_cursor_subagent_event_without_flag_is_still_recognized(tmp_path, run_hook):
    repo = git_repo(tmp_path)
    hook_session(repo, resume_slug="implement")
    run_hook(GUARD, repo, SUB["cursor"], "cursor")
    assert read_session(repo).get("stop_refusals", 0) == 0


def test_claude_subagent_is_released_after_one_push_back(tmp_path, run_hook):
    repo = git_repo(tmp_path)
    hook_session(repo, resume_slug="implement")
    payload = {**SUB["claude"], "stop_hook_active": True}
    code, out, _ = run_hook(GUARD, repo, payload, "claude", "--subagent")
    assert code == 0 and stop_decision("claude", out) == "allow"


@pytest.mark.parametrize("layout", SESSION_LAYOUTS)
def test_main_budget_still_escalates_after_three(tmp_path, run_hook, layout):
    repo = git_repo(tmp_path)
    hook_session(repo, layout, resume_slug="implement")
    decisions = [stop_decision("claude", run_hook(GUARD, repo, MAIN["claude"], "claude")[1]) for _ in range(4)]
    assert decisions == ["block", "block", "block", "allow"]


@pytest.mark.parametrize("corruption", ["dangling_ref", "truncated"])
def test_corrupt_session_blocks_once_then_escalates_on_claude(tmp_path, run_hook, corruption):
    repo = git_repo(tmp_path)
    ctx = repo / ".context"
    ctx.mkdir()
    if corruption == "dangling_ref":
        (ctx / "cadence-session.json").write_text(json.dumps({"ref": "sessions/by-worktree/gone.json"}))
    else:
        (ctx / "cadence-session.json").write_text('{"active_skill": "cad')
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert stop_decision("claude", out) == "block"
    _, out, _ = run_hook(GUARD, repo, {**MAIN["claude"], "stop_hook_active": True}, "claude")
    assert stop_decision("claude", out) == "allow"
    assert "corrupt" in json.loads(out)["systemMessage"]


def test_refusal_keeps_the_session_pointer(tmp_path, run_hook):
    repo = git_repo(tmp_path)
    real = hook_session(repo, "pointer", resume_slug="implement")
    pointer_before = (repo / ".context" / "cadence-session.json").read_text()
    run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert (repo / ".context" / "cadence-session.json").read_text() == pointer_before
    assert json.loads(real.read_text())["stop_refusals"] == 1


def test_fragments_mark_subagent_registrations():
    cursor = json.loads((HOOKS_DIR / "hooks-cursor.json").read_text())["hooks"]
    claude = json.loads((HOOKS_DIR / "hooks.json").read_text())["hooks"]
    assert all("--subagent" in e["command"] for e in cursor["subagentStop"])
    assert all("--subagent" not in e["command"] for e in cursor["stop"])
    sub = [h["command"] for g in claude["SubagentStop"] for h in g["hooks"]]
    main = [h["command"] for g in claude["Stop"] for h in g["hooks"]]
    assert sub and all("--subagent" in c for c in sub)
    assert main and all("--subagent" not in c for c in main)


@pytest.mark.parametrize("body", ["[1, 2]", '"text"', "null"])
def test_unexpected_session_shape_blocks_once_then_escalates(tmp_path, run_hook, body):
    # cd-10-F2: a pointer to valid JSON that is not an object used to crash on every stop.
    repo = git_repo(tmp_path)
    sessions = repo / ".context" / "sessions"
    sessions.mkdir(parents=True)
    (repo / ".context" / "cadence-session.json").write_text(json.dumps({"ref": "sessions/x.json"}))
    (sessions / "x.json").write_text(body)
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert code == 0 and stop_decision("claude", out) == "block"
    assert "corrupt session" in json.loads(out)["reason"]  # classified, not a crash
    code, out, _ = run_hook(GUARD, repo, {**MAIN["claude"], "stop_hook_active": True}, "claude")
    assert code == 0 and stop_decision("claude", out) == "allow"


@pytest.mark.parametrize("host", ["cursor", "claude"])
def test_guard_error_blocks_with_json_and_releases_on_repeat(tmp_path, run_hook, host):
    # cd-10-F2: any runtime error used to exit 2 on every stop (no loop bound on Claude Code).
    repo = git_repo(tmp_path)
    hook_session(repo, resume_slug="implement", stop_refusals="not-a-number")
    code, out, _ = run_hook(GUARD, repo, MAIN[host], host)
    assert code == 0 and stop_decision(host, out) == "block"
    assert "stop guard error" in out
    if host == "claude":
        code, out, _ = run_hook(GUARD, repo, {**MAIN["claude"], "stop_hook_active": True}, "claude")
        assert code == 0 and stop_decision("claude", out) == "allow"


# --- stop_guard_holds_planning: From activation on, a stop is held at the first open block, planning included.
#
# Before, any stop before P9 passed unless a plan review was blocking (2d541e75,
# e539e523). The guard walks A1-P8 read-only (never P9: a failed P9 clears its
# marker). A stop while a delegated peer is still running is not held: the
# orchestrator waits for the peer.

def _peer(repo: Path, *, minutes_ago: int, status: str = "running") -> None:
    run_id = read_session(repo)["pipeline_run_id"]
    path = repo / ".context" / "subagent-runs" / run_id / "plan-review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    started = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    path.write_text(json.dumps({"stage": "plan-review", "status": status, "started_at": started}), encoding="utf-8")


def test_a_planning_stop_is_held_at_the_open_block(tmp_path, run_hook):
    repo = activated_repo(tmp_path / "proj")
    code, out, err = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude")
    assert code == 0, err
    assert stop_decision("claude", out) == "block"
    reason = json.loads(out)["reason"]
    assert "early stop in" in reason and "Continue paceline: next" in reason


@pytest.mark.parametrize("minutes_ago, status, want", [
    pytest.param(3, "running", "allow", id="a_stop_while_a_peer_runs_is_not_held"),
    pytest.param(90, "running", "block", id="a_peer_record_older_than_an_hour_does_not_hold_the_door_open"),
    pytest.param(3, "complete", "block", id="a_completed_peer_does_not_hold_the_door_open"),
])
def test_a_peer_record_opens_the_door_only_while_it_runs(tmp_path, run_hook, minutes_ago, status, want):
    # The orchestrator waits for a running peer.
    repo = activated_repo(tmp_path / "proj")
    _peer(repo, minutes_ago=minutes_ago, status=status)
    code, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude")
    assert code == 0 and stop_decision("claude", out) == want


def test_a_subagent_stop_in_planning_is_not_held(tmp_path, run_hook):
    repo = activated_repo(tmp_path / "proj")
    code, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude", "--subagent")
    assert stop_decision("claude", out) == "allow"


def test_the_documented_a3_ops_stop_is_not_held(tmp_path, run_hook, monkeypatch):
    """SKILL.md: ops-only work stops at A3. The planning hold must not fight that stop."""
    repo = activated_repo(tmp_path / "proj")
    enable_all_packs(repo)  # A3 is in the preflight pack
    # A2 passes first. It needs gh auth and a clean tree, so the pack config is committed.
    git(repo, "add", ".cadence.json")
    git(repo, "commit", "-qm", "packs")
    monkeypatch.setenv("PATH", f"{fake_gh(tmp_path / 'bin').parent}{os.pathsep}{os.environ['PATH']}")
    debug = repo / ".context" / "debug"
    debug.mkdir(parents=True, exist_ok=True)
    (debug / "handoff.md").write_text("Fix type: ops\nCATALOG_STALE\n", encoding="utf-8")
    code, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude")
    assert code == 0 and stop_decision("claude", out) == "allow"


# --- stop_guard_p3_rereview: the stop guard holds a planning stop in the P3 re-review states.
#
# Characterization tests (plan SF-3): `planning_continuation` names `/plan-review` for a
# blocking verdict on HEAD before the fix too. The guard text does not follow the open
# block (a known limit); a text fix is a follow-up.

RWC = "READY WITH CHANGES"


def test_stop_guard_s2_blocking_review_older_than_plan_holds_with_plan_review(tmp_path, run_hook):
    repo = git_repo(tmp_path / "r")
    lp = p3_loop(repo, cycles=1)
    hook_session(repo, **lp.session)
    p3_review(lp, 50, RWC)
    plan(lp, 60, updated=True)
    result = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude")
    assert_decision(result, "claude", "block", reason="/plan-review")


def test_stop_guard_s1b_resume_slug_plan_update_holds_with_plan_review(tmp_path, run_hook):
    # The guard text says /plan-review here, while `next` gives P4 (/plan-update).
    # A known limit: the fix of the guard text is a follow-up.
    repo = git_repo(tmp_path / "r")
    lp = p3_loop(repo, cycles=2)
    hook_session(repo, **lp.session, resume_slug="plan-update")
    p3_review(lp, 50, RWC)
    p3_review(lp, 70, RWC, n=2)
    plan(lp, 60, updated=True)
    result = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude")
    assert_decision(result, "claude", "block", reason="/plan-review")


# --- stop_guard_host_workspace: The stop guard finds the run from the host, also with only `workspace_roots`.
#
# The run is activated by the real activate_session.py (by-worktree session, root pointer).
# The guard keeps its two candidates [here, project]; only `here` uses the resolver.

HOLD = "Continue paceline: next"


@pytest.fixture
def project(tmp_path):
    return activated_repo(tmp_path / "project")


@pytest.fixture
def outside(tmp_path):
    path = tmp_path / "outside"
    path.mkdir()
    return path


def test_pin_payload_cwd_in_another_repo_still_judges_the_project_dir_run(run_hook, project, tmp_path):
    other = git_repo(tmp_path / "other")
    result = run_hook(GUARD, project, {"hook_event_name": "Stop", "cwd": str(other)}, "claude",
                      cwd=other, project_env={"CLAUDE_PROJECT_DIR": project})
    assert_decision(result, "claude", "block", reason=HOLD)


@pytest.mark.parametrize("shape", [
    pytest.param("cursor_env", id="pin_cursor_stop_with_cursor_project_dir_judges_the_project_run"),
    pytest.param("cursor_roots_only", id="cursor_stop_with_only_workspace_roots_judges_the_project_run"),
])
def test_cursor_stop_judges_the_project_run(run_hook, project, outside, shape):
    extras, cwd, env = host_shape(shape, project, outside)
    result = run_hook(GUARD, project, {"hook_event_name": "stop", **extras}, cwd=cwd, project_env=env)
    assert_decision(result, "cursor", "block", reason=HOLD)


def test_cursor_stop_outside_with_no_run_passes(run_hook, tmp_path, outside):
    plain = git_repo(tmp_path / "plain")
    extras, cwd, env = host_shape("cursor_roots_only", plain, outside)
    result = run_hook(GUARD, plain, {"hook_event_name": "stop", **extras}, cwd=cwd, project_env=env)
    assert_decision(result, "cursor", "pass")
    assert not (plain / ".context").exists()


# --- stop_guard_run_ownership: Each run belongs to one worktree and one chat. The stop guard acts only for its owner.
#
# Activation stores the session per worktree (shared/scripts/session_store.py, the
# store ported from ship-feature) and records the owner chat id from the host env.
# The stop guard judges, in order: the run this chat owns; this worktree's run when
# no other chat owns it; a legacy single-file session. Any other stop passes with no
# message (12 stop-hook injections into chats that never ran /cadence).



@pytest.fixture
def two_worktrees(tmp_path):
    primary = pushed_repo(tmp_path / "primary")
    w1, w2 = tmp_path / "w1", tmp_path / "w2"
    git(primary, "worktree", "add", "-q", "-b", "feature/gh-1-a", str(w1), "origin/main")
    git(primary, "worktree", "add", "-q", "-b", "feature/gh-2-b", str(w2), "origin/main")
    return primary, w1, w2


def _p9(primary: Path, task_id: str) -> None:
    """Write the P9 marker of the run of `task_id` (gates/entry-invariant-<run_id>.ok)."""
    ctx = primary / ".context"
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in (ctx / "sessions" / "by-worktree").glob("*.json")]
    run_id = next(r["pipeline_run_id"] for r in runs if r.get("task_id") == task_id)
    gates = ctx / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / f"entry-invariant-{run_id}.ok").write_text(json.dumps({"task_id": task_id}) + "\n", encoding="utf-8")


def _stop(project_dir: Path, payload: dict) -> str:
    proc = run_py(HOOKS_DIR / GUARD, "--host", "claude", cwd=payload.get("cwd", project_dir), stdin=payload,
                  CLAUDE_PROJECT_DIR=project_dir)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_two_worktrees_keep_two_sessions_with_their_owner(two_worktrees):
    primary, w1, w2 = two_worktrees
    activate(w1, CLAUDE_CODE_SESSION_ID="chat-A")
    activate(w2, CLAUDE_CODE_SESSION_ID="chat-B")
    s1, s2 = session_store.load_worktree_session(w1), session_store.load_worktree_session(w2)
    assert (s1["task_id"], s1["owner_session_id"]) == ("cd-1", "chat-A")
    assert (s2["task_id"], s2["owner_session_id"]) == ("cd-2", "chat-B")
    assert s1["base"] == "origin/main"


def test_owner_chat_is_held_even_from_the_primary_checkout(two_worktrees):
    primary, w1, _ = two_worktrees
    activate(w1, CLAUDE_CODE_SESSION_ID="chat-A")
    _p9(primary, "cd-1")
    out = _stop(primary, {"session_id": "chat-A", "cwd": str(primary)})
    assert json.loads(out)["decision"] == "block"
    assert "E1" in out


def test_another_chat_in_the_same_worktree_is_not_held(two_worktrees):
    primary, w1, _ = two_worktrees
    activate(w1, CLAUDE_CODE_SESSION_ID="chat-A")
    _p9(primary, "cd-1")
    assert _stop(w1, {"session_id": "chat-Z", "cwd": str(w1)}) == ""


def test_a_chat_in_another_worktree_is_not_held(two_worktrees):
    primary, w1, w2 = two_worktrees
    activate(w1)
    _p9(primary, "cd-1")
    assert _stop(w2, {"cwd": str(w2)}) == ""
    assert _stop(primary, {"cwd": str(primary)}) == ""


def test_a_run_with_no_owner_id_is_still_held_in_its_own_worktree(two_worktrees):
    primary, w1, _ = two_worktrees
    activate(w1)
    _p9(primary, "cd-1")
    assert json.loads(_stop(w1, {"cwd": str(w1)}))["decision"] == "block"


def test_a_refusal_is_counted_in_the_judged_run_only(two_worktrees):
    primary, w1, w2 = two_worktrees
    activate(w1, CLAUDE_CODE_SESSION_ID="chat-A")
    activate(w2, CLAUDE_CODE_SESSION_ID="chat-B")
    _p9(primary, "cd-1")
    before = session_store.load_worktree_session(w2)
    _stop(primary, {"session_id": "chat-A", "cwd": str(primary)})
    assert session_store.load_worktree_session(w1)["stop_refusals"] == 1
    assert session_store.load_worktree_session(w2) == before


def test_deactivate_closes_only_this_worktree(two_worktrees):
    _, w1, w2 = two_worktrees
    activate(w1, CLAUDE_CODE_SESSION_ID="chat-A")
    activate(w2, CLAUDE_CODE_SESSION_ID="chat-B")
    activate(w1, "--hard-stop", "not-needed", "--reason", "test")
    peer_summary(w1)
    activate(w1, "--deactivate")
    assert session_store.load_worktree_session(w1)["active_skill"] is None
    assert session_store.load_worktree_session(w2)["active_skill"] == "cadence"
