"""cd-76 Phase 4: the stop guard selects the runs a stop belongs to, and reads each judged run's own evidence.

Layout (two_worktree_runs): the hook cwd is the primary checkout, which has no run; runs A and B
live in linked worktrees and share one .context. A main-agent Stop judges each top-level run of
its chat, with one time budget for all of them. A SubagentStop judges only the run whose
orchestrator it is; a peer's SubagentStop records its stop and is allowed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from conftest import activate, save_worktree_session, stop_decision
from test_stop_guard_reads_progress import FAIL, PASS, stub_root

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import peer_attest  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from artifact_requirements import write_entry_invariant_ok  # noqa: E402


def past_p9(w, run: str, task: str) -> None:
    """A subagent's planning stop is never held; in execution it is."""
    write_entry_invariant_ok(w.ctx, pipeline_run_id=run, task_id=task, written_at="t")

GUARD = "pipeline_stop_guard.py"
CHAT = "chat-c"
SLEEP_PASS = "import time; time.sleep(3.3); import sys; sys.exit(0)\n"
SLEEP_FAIL = "import time; time.sleep(3.3); print('FAIL  stub'); import sys; sys.exit(1)\n"
SLEEP_MARK = (
    "import time, pathlib; time.sleep(3.3); pathlib.Path('slept.txt').write_text('x'); import sys; sys.exit(0)\n"
)
WALK_MARK = "import pathlib, sys; pathlib.Path('walked.txt').write_text('x'); print('FAIL  stub'); sys.exit(1)\n"


def planning_root(tmp_path: Path, a1: str, a3: str) -> Path:
    """A registry of A1 and A3 (ops-gate) only: a run before P9 walks these two gates."""
    root = stub_root(tmp_path, [])
    stages = [
        {"slug": "activate-session", "block": "A1", "phase": "alignment", "body": a1},
        {"slug": "ops-gate", "block": "A3", "phase": "alignment", "body": a3},
        {"slug": "deferral-register", "block": "P8", "phase": "planning", "body": PASS},
        {"slug": "entry-invariant", "block": "P9", "phase": "planning", "body": FAIL},
        {"slug": "implement", "block": "E1", "phase": "execution", "body": FAIL},
        {"slug": "session-close", "block": "R7", "phase": "review", "body": PASS},
    ]
    rows = []
    for s in stages:
        script = f"scripts/stub_{s['slug'].replace('-', '_')}.py"
        (root / script).write_text(s["body"], encoding="utf-8")
        rows.append({"slug": s["slug"], "block": s["block"], "phase": s["phase"], "command": f"python3 {script}",
                     "gate_script": script, "trigger": "always", "description": s["slug"]})
    (root / "stages.json").write_text(json.dumps({"version": 1, "stages": rows}))
    return root


def runs(w, **fields_by_run) -> None:
    """Save runs A and B again with extra fields (owner, orchestrators, hard stop). B is saved last."""
    save_worktree_session(w.a, pipeline_run_id=w.run_a, task_id="cd-901", resume_slug="activate-session",
                          **fields_by_run.get("a", {}))
    save_worktree_session(w.b, pipeline_run_id=w.run_b, task_id="cd-902", resume_slug="activate-session",
                          **fields_by_run.get("b", {}))


def session(w, tree) -> dict:
    import session_store

    return session_store.load_worktree_session(tree)


def stop(run_hook, w, root, payload=None, *flags) -> tuple[str, float]:
    body = {"cwd": str(w.primary), "session_id": CHAT, **(payload or {})}
    start = time.monotonic()
    code, out, err = run_hook(GUARD, w.primary, body, "claude", *flags, hooks_dir=root / "hooks")
    assert code == 0, err
    return out, time.monotonic() - start


OWNED = {"owner_session_id": CHAT, "host": "claude"}


# --- main-agent Stop: every top-level run of the chat ----------------------------------------------


def test_main_stop_with_two_top_level_runs_blocks_on_the_first_that_holds(two_worktree_runs, run_hook, tmp_path):
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a={**OWNED, "hard_stop": {"class": "retries"}}, b=OWNED)
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "block"
    assert session(w, w.b).get("stop_refusals") == 1
    assert not session(w, w.a).get("stop_refusals")


def test_main_stop_allows_when_no_top_level_run_holds(two_worktree_runs, run_hook, tmp_path):
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a={**OWNED, "hard_stop": {"class": "retries"}}, b={**OWNED, "hard_stop": {"class": "retries"}})
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "allow"


def test_a_running_peer_in_any_judged_run_allows_the_main_stop(two_worktree_runs, run_hook, tmp_path):
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a=OWNED, b=OWNED)
    peer_attest.agent_event(w.ctx, w.run_b, "peer-1", "start")
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "allow"


def test_main_stop_is_not_judged_against_a_run_a_subagent_activated(two_worktree_runs, run_hook, tmp_path):
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a={**OWNED, "hard_stop": {"class": "retries"}}, b={**OWNED, "orchestrator_agent_ids": ["sub-b"]})
    past_p9(w, w.run_b, "cd-902")
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "allow"
    out, _ = stop(run_hook, w, root, {"hook_event_name": "SubagentStop", "agent_id": "sub-b"}, "--subagent")
    assert stop_decision("claude", out) == "block"


def test_main_stop_skips_a_subagent_run_in_the_cwd_worktree(two_worktree_runs, run_hook, tmp_path):
    """MF-1 (a): the chat owns no top-level run; steps 2 and 3 skip a run with orchestrator ids."""
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a={"hard_stop": {"class": "retries"}}, b={**OWNED, "orchestrator_agent_ids": ["sub-b"]})
    code, out, err = run_hook(GUARD, w.b, {"cwd": str(w.b), "session_id": CHAT}, "claude", hooks_dir=root / "hooks")
    assert code == 0, err
    assert stop_decision("claude", out) == "allow"


def test_a_run_with_no_owner_is_still_judged_from_its_worktree(two_worktree_runs, run_hook, tmp_path):
    """MF-1 (b): the e5c3a92 behavior for a run that names no owner."""
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w)
    code, out, err = run_hook(GUARD, w.b, {"cwd": str(w.b), "session_id": CHAT}, "claude", hooks_dir=root / "hooks")
    assert code == 0, err
    assert stop_decision("claude", out) == "block"


def test_a_mixed_chat_judges_only_its_top_level_run(two_worktree_runs, run_hook, tmp_path):
    """MF-1 (c): run A is top level and holds nothing; run B is a subagent's and would hold."""
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a={**OWNED, "hard_stop": {"class": "retries"}}, b={**OWNED, "orchestrator_agent_ids": ["sub-b"]})
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "allow"
    assert not session(w, w.b).get("stop_refusals")


# --- SubagentStop ------------------------------------------------------------------------------------


def test_a_peer_subagent_stop_is_recorded_and_allowed(two_worktree_runs, run_hook, tmp_path):
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a=OWNED, b=OWNED)
    peer_attest.agent_event(w.ctx, w.run_b, "peer-1", "start")
    out, _ = stop(run_hook, w, root, {"hook_event_name": "SubagentStop", "agent_id": "peer-1"}, "--subagent")
    assert stop_decision("claude", out) == "allow"
    assert peer_attest.running_agents(w.ctx, w.run_b) == []


def test_subagent_stop_tie_break_takes_the_run_of_its_cwd(two_worktree_runs, run_hook, tmp_path):
    """S-5: two runs list the same orchestrator id; the stop's cwd (a subdirectory) decides."""
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a={**OWNED, "orchestrator_agent_ids": ["sub"], "hard_stop": {"class": "retries"}},
         b={**OWNED, "orchestrator_agent_ids": ["sub"]})
    past_p9(w, w.run_b, "cd-902")
    sub = w.b / "pkg"
    sub.mkdir()
    payload = {"hook_event_name": "SubagentStop", "agent_id": "sub", "cwd": str(sub), "session_id": CHAT}
    code, out, err = run_hook(GUARD, w.primary, payload, "claude", "--subagent", hooks_dir=root / "hooks")
    assert code == 0, err
    assert stop_decision("claude", out) == "block"  # run B (not hard-stopped) was judged


# --- each judged run's own evidence (acceptance 17) ------------------------------------------------


def test_the_judged_run_reads_its_own_plan_verdict(two_worktree_runs, run_hook, tmp_path):
    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, b=OWNED)
    save_worktree_session(w.a, pipeline_run_id=w.run_a, task_id="cd-901", resume_slug="activate-session",
                          owner_session_id="other-chat")  # the root redirect now names run A
    reviews = w.ctx / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "plan_a_cd-901.md").write_text(
        f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {w.run_a}\nTicket: cd-901\n**Verdict:** READY WITH CHANGES\n")
    (reviews / "plan_b_cd-902.md").write_text(
        f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {w.run_b}\nTicket: cd-902\n**Verdict:** READY TO EXECUTE\n")
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "block"
    assert "/plan-update" not in out


# --- one deadline for all judged runs (C1, F39, N-4 to N-6) -----------------------------------------


def test_one_deadline_covers_every_judged_run(two_worktree_runs, run_hook, tmp_path):
    """Run A's two gates take about 6.6 s and A allows (A3 fails: an ops-only stop). With one shared
    9 s deadline, run B has under 3 s left: its first gate is cut off, so B is unknown and holds.
    With a fresh budget per run, B would also allow. The hook ends inside its 15 s timeout."""
    w = two_worktree_runs
    root = planning_root(tmp_path, SLEEP_MARK, SLEEP_FAIL)
    runs(w, a=OWNED, b=OWNED)
    out, elapsed = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "block"
    assert "unknown" in out.lower() and w.run_b in out
    assert elapsed < 14
    assert (w.a / "slept.txt").is_file()
    assert not (w.b / "slept.txt").exists()  # run B's gate was stopped before its sleep ended


def test_the_guard_stops_at_the_first_run_that_holds(two_worktree_runs, run_hook, tmp_path):
    w = two_worktree_runs
    root = planning_root(tmp_path, WALK_MARK, PASS)
    runs(w, a=OWNED, b=OWNED)
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "block"
    assert (w.a / "walked.txt").is_file()
    assert not (w.b / "walked.txt").exists()


def test_a_subagent_re_activation_keeps_the_main_stop_held(two_worktree_runs, run_hook, tmp_path):
    """R1 D-1 regression: after a subagent activates top-level run B again, the main stop still holds."""

    import session_store

    w = two_worktree_runs
    root = planning_root(tmp_path, FAIL, PASS)
    runs(w, a={**OWNED, "hard_stop": {"class": "retries"}}, b=OWNED)
    session_store.write_pending_orchestrator("sub-x", w.ctx)
    activate(w.b, "--task-id", "cd-902", home=root)
    assert not session(w, w.b).get("orchestrator_agent_ids")
    out, _ = stop(run_hook, w, root)
    assert stop_decision("claude", out) == "block"
