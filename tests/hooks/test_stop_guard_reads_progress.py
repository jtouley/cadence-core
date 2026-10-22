"""The stop guard finds the open block from evidence, not from resume_slug.

Only activate_session.py writes resume_slug, and it writes it once, so in a real run it
stays at `activate-session`. The guard used to read it, and allowed every stop.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from conftest import (activated_repo, code_paths, enable_all_packs, git, git_repo, head_sha, hook_session,
                      install_config, p9_marker, passing_acceptance, read_session, snapshot, stop_decision)

ROOT = Path(__file__).resolve().parents[2]
GUARD = "pipeline_stop_guard.py"
MAIN = {"cursor": {"hook_event_name": "stop"}, "claude": {"hook_event_name": "Stop"}}
SUB = {"cursor": {"hook_event_name": "subagentStop"}, "claude": {"hook_event_name": "SubagentStop"}}
HOSTS = ["cursor", "claude"]
PASS = "import sys; sys.exit(0)\n"
FAIL = "import sys; print('FAIL  stub'); sys.exit(1)\n"
CRASH = "raise RuntimeError('boom')\n"
HANG = "import time; time.sleep(60)\n"


def real_run(tmp_path: Path, *, marker: bool, work: bool = True) -> Path:
    """A repo that a real `activate_session.py` activated. resume_slug stays `activate-session`.

    With `work`, the branch has one commit since its base, so E1 has done its job.
    """
    repo = activated_repo(tmp_path / "proj")
    assert read_session(repo)["resume_slug"] == "activate-session"  # what a real run writes
    if marker:
        p9_marker(repo).parent.mkdir(parents=True, exist_ok=True)
        p9_marker(repo).write_text(json.dumps({"task_id": read_session(repo)["task_id"]}) + "\n")
        passing_acceptance(repo / ".context", repo, read_session(repo)["task_id"])  # what P9 pins
    if work:
        commit_work(repo)
    return repo


def commit_work(repo: Path) -> None:
    import subprocess

    (repo / "work.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "work.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "feat: the change"],
        cwd=repo, check=True,
    )


def stub_root(tmp_path: Path, execution_gates: list[tuple[str, str, str, str]]) -> Path:
    """A copy of Cadence with a small registry, so one gate can hang, crash, or pass on demand.

    execution_gates: (block, slug, phase, gate body). The rows before them fail on purpose:
    the walk must not run them.
    """
    root = tmp_path / "stub"
    for part in ("hooks", "scripts", "shared"):
        shutil.copytree(ROOT / part, root / part, ignore=shutil.ignore_patterns("__pycache__"))
    install_config(root)
    shutil.copytree(ROOT / "adapters", root / "adapters")
    rows = [
        ("A1", "activate-session", "alignment", FAIL),
        ("P9", "entry-invariant", "planning", FAIL),
        *execution_gates,
    ]
    stages = []
    for block, slug, phase, body in rows:
        script = f"scripts/stub_{slug.replace('-', '_')}.py"
        (root / script).write_text(body, encoding="utf-8")
        stages.append(
            {
                "slug": slug,
                "block": block,
                "phase": phase,
                "command": f"python3 {script}",
                "gate_script": script,
                "trigger": "always",
                "description": slug,
            }
        )
    (root / "stages.json").write_text(json.dumps({"version": 1, "stages": stages}))
    return root


def stub_run(tmp_path: Path, gates, *, marker: bool = True, **session) -> tuple[Path, Path]:
    root = stub_root(tmp_path, gates)
    repo = git_repo(tmp_path / "proj")
    hook_session(repo, "single", task_id="cd-7", resume_slug="activate-session", **session)
    if marker:
        p9_marker(repo).parent.mkdir(parents=True, exist_ok=True)
        p9_marker(repo).write_text(json.dumps({"task_id": "cd-7"}) + "\n")
    return root, repo


# --- the stop that the old guard allowed, against the real registry -------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_stop_in_a_real_run_past_p9_is_blocked_though_resume_slug_is_stale(tmp_path, run_hook, host):
    repo = real_run(tmp_path, marker=True)
    enable_all_packs(repo)  # E2 is in the deferrals pack (test_stop_guard_packs.py: the default)
    start = time.monotonic()
    code, out, _ = run_hook(GUARD, repo, MAIN[host], host)
    assert code == 0
    assert stop_decision(host, out) == "block"
    assert "E2" in out and "impl-artifact" in out  # E1 has a commit since its base. E2 has no artifact
    assert time.monotonic() - start < 14  # the contract is the 15 s hook timeout. A real stop takes 0.2 s


@pytest.mark.parametrize("host", HOSTS)
def test_stop_past_p9_with_no_work_is_held_at_e1(tmp_path, run_hook, host):
    """The P9 marker alone does not finish E1. That gap opened draft PRs with no diff."""
    repo = real_run(tmp_path, marker=True, work=False)
    code, out, _ = run_hook(GUARD, repo, MAIN[host], host)
    assert code == 0 and stop_decision(host, out) == "block"
    assert "E1" in out and "no commits since" in out


@pytest.mark.parametrize("host", HOSTS)
def test_stop_before_p9_is_held_in_planning(tmp_path, run_hook, host):
    """An active run holds from activation. Before, a planning stop passed."""
    repo = real_run(tmp_path, marker=False)
    code, out, _ = run_hook(GUARD, repo, MAIN[host], host)
    assert code == 0 and stop_decision(host, out) == "block"
    assert "early stop in planning" in out


@pytest.mark.parametrize("host", HOSTS)
def test_a_blocked_stop_changes_nothing_but_the_refusal_record(tmp_path, run_hook, host):
    """The walk runs the gates read-only. A stop must not record breadcrumbs or move the marker."""
    repo = real_run(tmp_path, marker=True)
    before = snapshot(repo / ".context")
    run_hook(GUARD, repo, MAIN[host], host)
    after = snapshot(repo / ".context")
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    # The refusal record: the run's own session file, and the phase-boundary breadcrumb (with its directory).
    session_files = {k for k in changed if k.startswith("sessions/by-worktree/")}
    assert len(session_files) == 1, changed
    assert changed == session_files | {"breadcrumbs", "breadcrumbs/phase-boundary.md"}, changed
    assert read_session(repo)["stop_refusals"] == 1
    assert p9_marker(repo).is_file()


@pytest.mark.parametrize("other", ["cd-99", "cd-70", "cd-", "CD-7"])
def test_a_marker_for_another_task_is_ignored(tmp_path, run_hook, other):
    """The session task is cd-7. `cd-70` is the adjacent id: a prefix match would take it."""
    repo = real_run(tmp_path, marker=False)
    assert read_session(repo)["task_id"] == "cd-7"
    p9_marker(repo).parent.mkdir(parents=True, exist_ok=True)
    p9_marker(repo).write_text(json.dumps({"task_id": other}) + "\n")
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    # Another task's marker is not evidence of execution: the stop is held in planning, not at E1.
    assert stop_decision("claude", out) == "block"
    assert "early stop in planning" in out


@pytest.mark.parametrize("task_id", [["cd-7"], 7, {"x": 1}, True])
def test_a_malformed_marker_task_id_blocks(tmp_path, run_hook, task_id):
    """A corrupt marker is not another task's marker. It must not let the stop through."""
    repo = real_run(tmp_path, marker=False)
    p9_marker(repo).parent.mkdir(parents=True, exist_ok=True)
    p9_marker(repo).write_text(json.dumps({"task_id": task_id}) + "\n")
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert stop_decision("claude", out) == "block"


@pytest.mark.parametrize("text", [
    pytest.param(json.dumps({"pipeline_run_id": "run-1"}) + "\n", id="a_json_marker_that_names_no_task_counts"),
    pytest.param("ok\n", id="a_legacy_marker_without_json_counts"),
])
def test_a_marker_that_names_no_task_counts(tmp_path, run_hook, text):
    repo = real_run(tmp_path, marker=False)
    p9_marker(repo).parent.mkdir(parents=True, exist_ok=True)
    p9_marker(repo).write_text(text)
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert stop_decision("claude", out) == "block"


# --- never weaker than the stored slug ---------------------------------------------------


def registry():
    return json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))["stages"]


@pytest.mark.parametrize("row", registry(), ids=[r["block"] for r in registry()])
def test_every_stop_the_old_guard_blocked_is_still_blocked(tmp_path, run_hook, row):
    """Never weaker than the old rule (block in execution or review), with no marker.

    The new guard also holds planning stops, so every active run blocks.
    """
    repo = git_repo(tmp_path / "proj")
    hook_session(repo, "single", resume_slug=row["slug"])
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    old_guard_blocks = row["phase"] in ("execution", "review")
    if old_guard_blocks:
        assert stop_decision("claude", out) == "block"
    assert stop_decision("claude", out) == "block"


# --- the stub registry: what a real run cannot produce on demand --------------------------


def test_every_gate_passes_but_the_session_is_active_so_r7_is_open(tmp_path, run_hook):
    gates = [
        ("E1", "implement", "execution", PASS),
        ("R1", "pr-review-local", "review", PASS),
        ("R7", "session-close", "review", PASS),
    ]
    root, repo = stub_run(tmp_path, gates)
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    assert code == 0 and stop_decision("claude", out) == "block"
    assert "R7" in out and "session-close" in out


@pytest.mark.parametrize("session", [{"active_skill": None}, {"hard_stop": True}], ids=["closed", "hard-stop"])
def test_a_closed_or_hard_stopped_session_is_allowed_without_a_walk(tmp_path, run_hook, session):
    """The marker is still there, but the answer is known. A hung gate must not slow the stop."""
    gates = [("E1", "implement", "execution", HANG), ("R7", "session-close", "review", PASS)]
    root, repo = stub_run(tmp_path, gates, **session)
    start = time.monotonic()
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    assert stop_decision("claude", out) == "allow"
    assert time.monotonic() - start < 3  # a walk would wait for the 4 s gate limit


@pytest.mark.parametrize("host", HOSTS)
def test_the_first_failing_gate_is_named_and_early_failing_gates_are_not_run(tmp_path, run_hook, host):
    """A1 and P9 fail in the stub. If the walk ran them the open block would be A1."""
    gates = [
        ("E1", "implement", "execution", PASS),
        ("E2", "impl-artifact", "execution", FAIL),
        ("R7", "session-close", "review", PASS),
    ]
    root, repo = stub_run(tmp_path, gates)
    _, out, _ = run_hook(GUARD, repo, MAIN[host], host, hooks_dir=root / "hooks")
    assert stop_decision(host, out) == "block"
    assert "E2" in out and "impl-artifact" in out and "A1" not in out


def test_a_registry_the_walk_cannot_read_blocks_as_unknown(tmp_path, run_hook):
    """The walk raises (a row has no gate_script). A fail-open `except` would allow the stop."""
    gates = [
        ("E1", "implement", "execution", PASS),
        ("E2", "impl-artifact", "execution", PASS),
        ("R7", "session-close", "review", PASS),
    ]
    root, repo = stub_run(tmp_path, gates)
    registry_file = root / "stages.json"
    stages = json.loads(registry_file.read_text())
    for row in stages["stages"]:
        if row["slug"] == "impl-artifact":
            del row["gate_script"]
    registry_file.write_text(json.dumps(stages))
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    assert code == 0 and stop_decision("claude", out) == "block"
    assert "unknown" in out.lower() and "E1" in out


SLOW_PASS = "import time; time.sleep(3)\n"


def test_gates_that_pass_slowly_end_at_the_walk_budget_as_unknown(tmp_path, run_hook):
    """Four gates take 3 s each, under the 4 s gate limit. Together they pass the 9 s budget.
    Without a budget the walk would finish and name R7."""
    gates = [
        ("E1", "implement", "execution", SLOW_PASS),
        ("E2", "impl-artifact", "execution", SLOW_PASS),
        ("E3", "architecture-review-impl", "execution", SLOW_PASS),
        ("E4", "draft-pr", "execution", SLOW_PASS),
        ("R7", "session-close", "review", PASS),
    ]
    root, repo = stub_run(tmp_path, gates)
    start = time.monotonic()
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    assert code == 0 and stop_decision("claude", out) == "block"
    assert "unknown" in out.lower()
    assert time.monotonic() - start < 13  # the budget is 9 s. The hook timeout is 15 s


def test_a_gate_that_crashes_blocks_the_stop(tmp_path, run_hook):
    gates = [("E1", "implement", "execution", CRASH), ("R7", "session-close", "review", PASS)]
    root, repo = stub_run(tmp_path, gates)
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    assert code == 0 and stop_decision("claude", out) == "block"


def test_a_hung_gate_means_unknown_and_blocks_inside_the_hook_timeout(tmp_path, run_hook):
    gates = [("E1", "implement", "execution", HANG), ("R7", "session-close", "review", PASS)]
    root, repo = stub_run(tmp_path, gates)
    start = time.monotonic()
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    elapsed = time.monotonic() - start
    assert code == 0 and stop_decision("claude", out) == "block"
    assert "unknown" in out.lower()
    assert 3 < elapsed < 8, elapsed  # the gate limit is 4 s. Under the 9 s walk limit it would take 9 s


def test_a_hung_gate_is_named_not_the_first_execution_row(tmp_path, run_hook):
    """The row that hung is where the run stands at the earliest. E1 passed."""
    gates = [
        ("E1", "implement", "execution", PASS),
        ("E2", "impl-artifact", "execution", HANG),
        ("R7", "session-close", "review", PASS),
    ]
    root, repo = stub_run(tmp_path, gates)
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    assert stop_decision("claude", out) == "block"
    assert "E2" in out and "impl-artifact" in out and "next E1" not in out


# --- the refusal budget and subagents behave as before ------------------------------------


def test_the_refusal_budget_still_ends_the_loop_on_the_new_path(tmp_path, run_hook):
    repo = real_run(tmp_path, marker=True)
    decisions = []
    for _ in range(4):
        _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
        decisions.append(stop_decision("claude", out))
    assert decisions == ["block", "block", "block", "allow"]
    assert read_session(repo)["stop_refusals"] == 3


GATE_ON_FILE = "import pathlib, sys\nsys.exit(0 if pathlib.Path('e2.done').exists() else 1)\n"


def test_the_refusal_budget_resets_when_the_open_block_moves(tmp_path, run_hook):
    """The budget ends a loop at one block. It must not run out because the agent stopped
    early three times and then made progress."""
    gates = [
        ("E1", "implement", "execution", PASS),
        ("E2", "impl-artifact", "execution", GATE_ON_FILE),
        ("E3", "architecture-review-impl", "execution", FAIL),
        ("R7", "session-close", "review", PASS),
    ]
    root, repo = stub_run(tmp_path, gates)

    def stop() -> tuple[str, str]:
        _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
        return stop_decision("claude", out), out

    assert [stop()[0] for _ in range(3)] == ["block", "block", "block"]
    assert read_session(repo)["stop_refusals"] == 3
    (repo / "e2.done").write_text("the agent did E2")
    decision, out = stop()
    assert decision == "block" and "E3" in out
    assert read_session(repo)["stop_refusals"] == 1
    assert [stop()[0] for _ in range(3)] == ["block", "block", "allow"]  # counts 2 and 3, then escalate


@pytest.mark.parametrize("host", HOSTS)
def test_subagent_stops_on_the_new_path_do_not_spend_the_budget(tmp_path, run_hook, host):
    repo = real_run(tmp_path, marker=True)
    for _ in range(3):
        _, out, _ = run_hook(GUARD, repo, SUB[host], host, "--subagent")
        assert stop_decision(host, out) == "block"
    assert read_session(repo).get("stop_refusals", 0) == 0


def test_a_blocking_plan_verdict_holds_the_stop_before_execution(tmp_path, run_hook):
    """resume_slug stays at activate-session until execution. The review verdict is the hold."""
    repo = real_run(tmp_path, marker=False)
    reviews = repo / ".context" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "plan_addendum.md").write_text(
        "RUN_BY_COMMAND: plan-review\nTicket: cd-7\n**Verdict:** READY WITH CHANGES\n",
        encoding="utf-8",
    )
    _, out, _ = run_hook(GUARD, repo, MAIN["cursor"], "cursor")
    assert stop_decision("cursor", out) == "block"
    assert "/plan-update" in out
    _, sub_out, _ = run_hook(GUARD, repo, SUB["cursor"], "cursor", "--subagent")
    assert stop_decision("cursor", sub_out) == "allow"


def test_a_ready_plan_verdict_does_not_hold_a_later_planning_stop(tmp_path, run_hook):
    repo = real_run(tmp_path, marker=False)
    reviews = repo / ".context" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "plan_addendum.md").write_text(
        "RUN_BY_COMMAND: plan-review\nTicket: cd-7\nVerdict: READY TO EXECUTE\n",
        encoding="utf-8",
    )
    _, out, _ = run_hook(GUARD, repo, MAIN["cursor"], "cursor")
    # A READY verdict ends the review loop; the stop is held at the next open planning block.
    assert stop_decision("cursor", out) == "block"
    assert "plan-update" not in out


def test_a_repo_that_never_ran_cadence_is_untouched(tmp_path, run_hook):
    repo = git_repo(tmp_path / "plain")
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert code == 0 and stop_decision("claude", out) == "allow"
    assert not (repo / ".context").exists()


# --- decide(), without a process --------------------------------------------------------


def row(slug: str) -> dict:
    return next(r for r in registry() if r["slug"] == slug)


@pytest.fixture
def guard(scripts_path):
    import pipeline_stop_guard

    return pipeline_stop_guard


ACTIVE = {"active_skill": "cadence", "task_id": "cd-7", "resume_slug": "activate-session"}


def test_decide_a_stale_slug_alone_allows(guard):
    decision, _ = guard.decide(dict(ACTIVE))
    assert decision == "allow"


def test_decide_an_open_block_in_execution_blocks_and_names_it(guard):
    decision, message = guard.decide(dict(ACTIVE), open_block=row("impl-artifact"))
    assert decision == "block"
    assert "BLOCKED early stop in execution" in message
    assert "next E2 (impl-artifact)" in message  # the open block is the block to run


def test_decide_the_stored_slug_is_still_a_lower_bound(guard):
    decision, message = guard.decide({**ACTIVE, "resume_slug": "implement"}, open_block=None)
    assert decision == "block" and "E2" in message


def test_decide_unknown_adds_the_note_and_still_blocks(guard):
    _, plain = guard.decide(dict(ACTIVE), open_block=row("implement"))
    decision, message = guard.decide(dict(ACTIVE), open_block=row("implement"), unknown=True)
    assert decision == "block"
    assert "Progress unknown" in message and "Progress unknown" not in plain


def test_decide_the_budget_escalates_at_three(guard):
    block = row("implement")
    assert guard.decide({**ACTIVE, "stop_refusals": 2}, open_block=block)[0] == "block"
    decision, message = guard.decide({**ACTIVE, "stop_refusals": 3}, open_block=block)
    assert decision == "escalate" and "refused 3 times" in message


@pytest.mark.parametrize("session", [{"hard_stop": True}, {"active_skill": None}], ids=["hard-stop", "closed"])
def test_decide_allows_a_hard_stopped_or_closed_run_whatever_the_walk_found(guard, session):
    assert guard.decide({**ACTIVE, **session}, open_block=row("session-close"))[0] == "allow"


def test_decide_a_corrupt_session_blocks(guard):
    assert guard.decide(None, corrupt=True)[0] == "block"


# --- the walk runs R1 again after R4 appends to the same review ------------

R1_REAL = (
    "import sys\n"
    f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
    "import gate_common\n"
    "raise SystemExit(gate_common.run_artifact_gate('pr-review-local-gate', gate_common.ctx(), check=True))\n"
)


@pytest.mark.parametrize(("appender", "open_block"), [("pr-review-update", "R2"), ("plan-update", "R1")])
def test_the_walk_after_r4_does_not_stop_at_r1(tmp_path, run_hook, appender, open_block):
    code_paths()
    from artifact_requirements import REVIEW_RIGOR_SECTIONS
    from prompt_shape import write_delegation_record
    from verdict_join import peer_wrote

    gates = [("E1", "implement", "execution", PASS), ("R1", "pr-review-local", "review", R1_REAL),
             ("R2", "test-benchmark-pr", "review", FAIL), ("R7", "session-close", "review", PASS)]
    root, repo = stub_run(tmp_path, gates, host="claude", verdict_join=True, orchestrator_source="toplevel",
                          workspace=str(repo_path := tmp_path / "proj"))
    ctx = repo / ".context"
    assert repo == repo_path
    write_delegation_record(ctx, run_id="run-1", stage="pr-review-local", command="/pr-review-local 1",
                            source="PreToolUse")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    body = (f"RUN_BY_COMMAND: pr-review-local\nLast reviewed commit: {head_sha(repo)}\n"
            + "".join(f"\n## {h}\nnone\n" for h in REVIEW_RIGOR_SECTIONS))
    rel = "prs/reviews/PR_REVIEW_#1.md"
    peer_wrote(ctx, "run-1", rel, agent_id="r1", stage="pr-review-local", body=body)
    peer_wrote(ctx, "run-1", rel, agent_id="r4", stage=appender, body=body + "\nUpdate: fixes reviewed\n")
    _, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude", hooks_dir=root / "hooks")
    assert stop_decision("claude", out) == "block"
    assert f"{open_block} " in out or f"next {open_block}" in out, out
