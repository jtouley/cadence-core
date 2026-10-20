"""Which run sees a peer as running: SubagentStart and SubagentStop on Claude Code, the begin record elsewhere (cd-32, cd-76)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from conftest import activated_repo, code_paths, git_repo, read_session, run_py, save_worktree_session, stop_decision

code_paths()
import peer_attest  # noqa: E402
import session_store  # noqa: E402


# --- peer_running_from_host_events: On Claude Code, "a peer is running" comes from SubagentStart/SubagentStop, not from a record.
#
# A `begin` record stays `running` when the orchestrator never calls `complete`
# (cd-32 A5). Trusting it would let planning stops pass for an hour. On a
# host=claude run the hooks record each subagent's start and stop instead.

def _session_host(repo: Path, host: str) -> None:
    ctx = repo / ".context"
    root = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    path = ctx / root["ref"] if "ref" in root else ctx / "cadence-session.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["host"] = host
    path.write_text(json.dumps(data), encoding="utf-8")


def _begin_record(repo: Path) -> None:
    run_id = read_session(repo)["pipeline_run_id"]
    path = repo / ".context" / "subagent-runs" / run_id / "plan-review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps({"stage": "plan-review", "status": "running", "started_at": now}), encoding="utf-8")


def _held(out: str) -> bool:
    return bool(out) and json.loads(out).get("decision") == "block"


def test_a_stale_running_record_does_not_open_the_door_on_claude(tmp_path, run_hook):
    repo = activated_repo(tmp_path / "proj")
    _session_host(repo, "claude")
    _begin_record(repo)
    _, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo), "session_id": "c"}, "claude")
    assert _held(out)


def test_a_started_subagent_opens_the_door_until_it_stops(tmp_path, run_hook):
    repo = activated_repo(tmp_path / "proj")
    _session_host(repo, "claude")
    start = {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "general-purpose", "cwd": str(repo)}
    run_hook("guard_subagent_handoff.py", repo, start, "claude")
    _, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo), "session_id": "c"}, "claude")
    assert not _held(out)
    stop = {"hook_event_name": "SubagentStop", "agent_id": "a1", "cwd": str(repo), "session_id": "c"}
    run_hook("pipeline_stop_guard.py", repo, stop, "claude", "--subagent")
    _, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo), "session_id": "c"}, "claude")
    assert _held(out)


def test_other_hosts_still_read_the_begin_record(tmp_path, run_hook):
    repo = activated_repo(tmp_path / "proj")
    _session_host(repo, "other")
    _begin_record(repo)
    _, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude")
    assert not _held(out)


def test_subagent_stops_are_not_logged_without_an_active_run(tmp_path, run_hook):
    repo = git_repo(tmp_path / "plain")
    stop = {"hook_event_name": "SubagentStop", "agent_id": "a1", "cwd": str(repo), "session_id": "c"}
    run_hook("pipeline_stop_guard.py", repo, stop, "claude", "--subagent")
    assert not (repo / ".context" / "attest").exists()


# --- peer_start_in_each_owned_run: cd-76 Phase 5: a peer's start goes to each run the chat owns; re-activation records the orchestrator.
#
# SubagentStart records `start` in each active run whose owner is this chat, else (no chat id, or
# no owned run) in the run the handoff hook reads today. A second activation of an active run
# adds the noted subagent id to `orchestrator_agent_ids`; a top-level note adds nothing.

CHAT = "chat-c"


def started(ctx: Path, run: str) -> list[str]:
    return peer_attest.running_agents(ctx, run)


def subagent_start(run_hook, cwd: Path, agent: str, chat: str | None = CHAT) -> None:
    payload = {"hook_event_name": "SubagentStart", "agent_id": agent, "agent_type": "general-purpose", "cwd": str(cwd)}
    if chat:
        payload["session_id"] = chat
    code, _, err = run_hook("guard_subagent_handoff.py", cwd, payload, "claude")
    assert code == 0, err


def test_a_peer_start_goes_to_each_run_the_chat_owns(two_worktree_runs, run_hook):
    w = two_worktree_runs
    save_worktree_session(w.a, pipeline_run_id=w.run_a, task_id="cd-901", owner_session_id=CHAT)
    save_worktree_session(w.b, pipeline_run_id=w.run_b, task_id="cd-902", owner_session_id=CHAT)
    subagent_start(run_hook, w.primary, "peer-1")
    assert started(w.ctx, w.run_a) == ["peer-1"]
    assert started(w.ctx, w.run_b) == ["peer-1"]


def test_with_no_chat_id_the_start_goes_to_the_run_the_hook_reads(two_worktree_runs, run_hook):
    w = two_worktree_runs  # no owner; the root redirect names run B
    subagent_start(run_hook, w.primary, "peer-1", chat=None)
    assert started(w.ctx, w.run_b) == ["peer-1"]
    assert started(w.ctx, w.run_a) == []


def test_a_chat_with_no_owned_run_falls_back_to_the_run_the_hook_reads(two_worktree_runs, run_hook):
    w = two_worktree_runs
    subagent_start(run_hook, w.primary, "peer-1")
    assert started(w.ctx, w.run_b) == ["peer-1"]
    assert started(w.ctx, w.run_a) == []


def test_a_peer_of_run_a_starts_and_stops_with_the_hook_cwd_at_the_primary(two_worktree_runs, run_hook):
    w = two_worktree_runs
    save_worktree_session(w.b, pipeline_run_id=w.run_b, task_id="cd-902", owner_session_id="other-chat")
    save_worktree_session(w.a, pipeline_run_id=w.run_a, task_id="cd-901", owner_session_id=CHAT)
    subagent_start(run_hook, w.primary, "peer-a")
    assert started(w.ctx, w.run_a) == ["peer-a"]
    stop = {"hook_event_name": "SubagentStop", "agent_id": "peer-a", "cwd": str(w.primary), "session_id": CHAT}
    code, out, err = run_hook("pipeline_stop_guard.py", w.primary, stop, "claude", "--subagent")
    assert code == 0, err
    assert stop_decision("claude", out) == "allow"
    assert started(w.ctx, w.run_a) == []


def _activate_again(tree: Path, task: str, note_agent: str | None, ctx: Path) -> dict:
    session_store.write_pending_orchestrator(note_agent, ctx)
    proc = run_py("scripts/activate_session.py", "--task-id", task, cwd=tree)
    assert proc.returncode == 0, proc.stderr
    assert "already active" in proc.stderr
    return session_store.load_worktree_session(tree)


def test_a_subagent_re_activating_a_top_level_run_does_not_take_it_over(two_worktree_runs):
    """R1 D-1: the run stays top level, so the main agent's stop is still judged against it."""
    w = two_worktree_runs
    assert not _activate_again(w.a, "cd-901", "sub-7", w.ctx).get("orchestrator_agent_ids")


def test_a_new_subagent_re_activating_a_subagent_run_is_recorded(two_worktree_runs):
    w = two_worktree_runs
    save_worktree_session(w.a, pipeline_run_id=w.run_a, task_id="cd-901", orchestrator_agent_ids=["sub-1"])
    assert _activate_again(w.a, "cd-901", "sub-7", w.ctx)["orchestrator_agent_ids"] == ["sub-1", "sub-7"]


def test_a_second_activation_from_the_top_level_adds_nothing(two_worktree_runs):
    w = two_worktree_runs
    assert not _activate_again(w.a, "cd-901", None, w.ctx).get("orchestrator_agent_ids")
