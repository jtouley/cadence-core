"""The review rounds come from the registry: no script names their members.

`stages.json` declares each round under `review_rounds`, and a row joins a round with `review_round`.
A row whose command is a slash command is a peer that the orchestrator sends in parallel with the
other peers of the round; any other row is a script that the round runs. A pack row that names a
round joins it with no code change, and a pack that is off takes its rows out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import activated_repo, code_paths, read_session, run_py, write_files

code_paths()
import artifact_requirements as ar  # noqa: E402
import load_stages  # noqa: E402
import plan_wave  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_stages.load_registry(ROOT)


def _members(name: str) -> list[str]:
    return [r["block"] for r in REGISTRY["stages"] if (r.get("review_round") or {}).get("round") == name]


def test_each_round_is_declared_with_a_driver_that_exists():
    rounds = REGISTRY["review_rounds"]
    assert {"plan", "pr"} <= set(rounds)
    for name, spec in rounds.items():
        script = spec["driver"].split()[1] if spec["driver"].startswith("python3 ") else spec["driver"].split()[0]
        assert (ROOT / script).is_file(), (name, spec["driver"])


def test_each_member_names_a_declared_round_and_a_known_limit():
    for row in REGISTRY["stages"]:
        member = row.get("review_round")
        if member is None:
            continue
        assert member["round"] in REGISTRY["review_rounds"], row["block"]
        if "cycle_limit" in member:
            assert ar.cycle_limit(member["cycle_limit"]) >= 1, row["block"]


def test_the_rounds_hold_the_reviewers_of_one_plan_and_one_head():
    assert _members("plan") == ["P3", "P5", "P6", "P7"]
    assert _members("pr") == ["E3", "R1", "R2", "R3"]


def test_a_limit_is_a_config_key_or_a_number_and_an_unknown_key_fails():
    assert ar.cycle_limit("plan_review_cycles") == ar.plan_review_cycle_limit()
    assert ar.cycle_limit(4) == 4
    with pytest.raises(ValueError, match="no_such_limit"):
        ar.cycle_limit("no_such_limit")


@pytest.fixture
def repo(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    session = read_session(repo)
    write_files(repo, {".cadence.json": json.dumps({"packs": ["spec-review", "test-plan"]}),
                       ".cursor/plans/x_ab12.plan.md": f"# Plan\n\nRUN_BY_COMMAND: plan-create\nTicket: "
                       f"{session['task_id']}\nPipeline-Run: {session['pipeline_run_id']}\n\n### Phase 1: a\n\nA.\n"})
    return repo


def test_a_pack_row_that_names_the_plan_round_joins_it(repo, monkeypatch):
    rows = load_stages.load_stages(ROOT, repo)
    extra = {"slug": "security-review-plan", "block": "P6.5", "phase": "planning", "command": "/security-review",
             "gate_script": "scripts/plan_review_gate.py", "pack": "core", "inputs": ["plan"],
             "review_round": {"round": "plan", "cycle_limit": 2}}
    monkeypatch.setattr(load_stages, "load_stages", lambda *a, **k: rows + [extra])
    monkeypatch.chdir(repo)
    rnd = plan_wave.Round(repo / ".context", read_session(repo))
    peers = plan_wave.peers(rnd)
    assert list(peers) == ["plan-review", "adversarial-review", "test-benchmark-plan", "security-review-plan"]
    assert peers["security-review-plan"].startswith("/security-review .cursor/plans/x_ab12.plan.md")
    assert peers["adversarial-review"] == "/de-adversarial-reviewer plan-gate .cursor/plans/x_ab12.plan.md"


def test_a_pack_that_is_off_takes_its_rows_out_of_the_round(repo, monkeypatch):
    write_files(repo, {".cadence.json": json.dumps({"packs": []})})
    monkeypatch.chdir(repo)
    rnd = plan_wave.Round(repo / ".context", read_session(repo))
    assert list(plan_wave.peers(rnd)) == ["plan-review"]


def test_the_plan_round_sends_the_registry_peers(repo):
    proc = run_py("scripts/plan_wave.py", cwd=repo)
    assert proc.returncode == 1
    for command in ("/plan-review", "/de-adversarial-reviewer plan-gate", "/test-benchmark plan"):
        assert f"{command} .cursor/plans/x_ab12.plan.md" in proc.stdout


def test_the_stop_guard_holds_each_peer_of_the_plan_round():
    import sys

    sys.path.insert(0, str(ROOT / "hooks"))
    import pipeline_stop_guard

    assert pipeline_stop_guard.round_stages(ROOT, None) == {
        "P3": "plan-review", "P6": "adversarial-review", "P7": "test-benchmark-plan"}


def test_the_pr_round_sends_its_peers_in_one_message_and_runs_its_scripts(repo):
    write_files(repo, {".context/prs/draft-pr-" + read_session(repo)["pipeline_run_id"] + ".md":
                       f"RUN_BY_COMMAND: commit-push-pr\nPR: https://github.com/o/r/pull/42\nBranch: feature/gh-7-toy\n"})
    proc = run_py("scripts/review_round.py", "--round", "pr", cwd=repo)
    assert proc.returncode == 1
    line = next(l for l in proc.stdout.splitlines() if l.startswith("NEXT: send these peers in one message"))
    assert "/pr-review-local 42" in line and "/test-benchmark 42" in line
    assert "R3 (cognitive-complexity)" in proc.stdout  # a script member: the round ran it


def test_a_peer_that_reviewed_this_head_and_still_fails_goes_to_the_fix_round(repo):
    """R2's FAIL card at a HEAD fails its own gate, so the round sent /test-benchmark again and again. A peer that the round sent at this HEAD is not sent again: the round names the fix round."""
    import subprocess

    write_files(repo, {".context/prs/draft-pr-" + read_session(repo)["pipeline_run_id"] + ".md":
                       f"RUN_BY_COMMAND: commit-push-pr\nPR: https://github.com/o/r/pull/42\nBranch: feature/gh-7-toy\n"})
    first = run_py("scripts/review_round.py", "--round", "pr", cwd=repo)
    assert "/test-benchmark 42" in first.stdout
    again = run_py("scripts/review_round.py", "--round", "pr", cwd=repo)
    assert again.returncode == 1 and "send these peers" not in again.stdout
    assert "NEXT: one fix round answers the must-fix rows of each member" in again.stdout
    check = run_py("scripts/review_round.py", "--round", "pr", "--check", cwd=repo)
    assert "NEXT: one fix round" in check.stdout  # the read-only walk reads the same state
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "fix: the fix round"], cwd=repo, check=True)
    assert "/test-benchmark 42" in run_py("scripts/review_round.py", "--round", "pr", cwd=repo).stdout  # a new HEAD


def test_an_unknown_round_fails_closed(repo):
    proc = run_py("scripts/review_round.py", "--round", "nope", cwd=repo)
    assert proc.returncode == 1 and "nope" in proc.stderr and "plan, pr" in proc.stderr


def test_an_open_acceptance_defect_names_the_repair_before_the_fix_round(repo):
    """R1 recorded an AC-4 defect, and the round named only the fix round, which may not change a pinned
    check. With an open defect, the round names `/plan-update <plan> --acceptance AC-n` first."""
    session = read_session(repo)
    task, run = session["task_id"], session["pipeline_run_id"]
    write_files(repo, {
        f".context/prs/draft-pr-{run}.md": "RUN_BY_COMMAND: commit-push-pr\nPR: https://github.com/o/r/pull/42\n"
                                           "Branch: feature/gh-7-toy\n",
        f".context/plans/{task}.plan.md": f"# Plan\n\nRUN_BY_COMMAND: plan-create\nTicket: {task}\nPipeline-Run: {run}\n\n"
                                          "### Phase 1: export\n",
        f".context/acceptance/{task}.repair.jsonl": json.dumps({"kind": "defect", "id": "AC-4", "run": run}) + "\n"})
    run_py("scripts/review_round.py", "--round", "pr", cwd=repo)  # the peers of this HEAD are sent
    again = run_py("scripts/review_round.py", "--round", "pr", cwd=repo)
    line = next(l for l in again.stdout.splitlines() if l.startswith("NEXT:"))
    assert f"/plan-update .context/plans/{task}.plan.md --acceptance AC-4` first" in line
