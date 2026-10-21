"""subagent_provenance.py begin and complete: the stage must match the command, and a marked Claude Code run keeps the hook record."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import code_paths, git_repo, linked_worktrees, make_session, run_py, save_worktree_session

code_paths()
import artifact_requirements  # noqa: E402
from prompt_shape import delegation_record_path, write_delegation_record  # noqa: E402


# --- subagent_provenance_lean_marker: `begin` refuses on a marked Claude Code run; `complete` keeps the hook record.

MARKED = {"lean_record_by_hook": True, "host": "claude"}


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/subagent_provenance.py", *args, cwd=cwd)


def begin(cwd: Path) -> subprocess.CompletedProcess[str]:
    return run(cwd, "begin", "--stage", "plan-review", "--command", "/plan-review plan.md")


def complete(cwd: Path) -> subprocess.CompletedProcess[str]:
    review = cwd / "review.md"
    review.write_text("RUN_BY_COMMAND: plan-review\nVerdict: READY TO EXECUTE\n", encoding="utf-8")
    return run(cwd, "complete", "--stage", "plan-review", "--artifact", str(review),
               "--manifest-id", "m-1", "--subagent-id", "agent-9")


def rec(ctx: Path, run_id: str = "run-1") -> dict | None:
    path = delegation_record_path(ctx, run_id, "plan-review")
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def fails_with(proc: subprocess.CompletedProcess[str], message: str) -> None:
    """The CLI exited 1 and its stderr is `message`."""
    assert proc.returncode == 1
    assert proc.stderr.strip() == message


@pytest.fixture
def plain_repo(tmp_path):
    """A git repo with no commit and no session; each test writes its own session."""
    return git_repo(tmp_path / "repo")


def test_begin_refuses_on_a_marked_claude_run(plain_repo):
    make_session(plain_repo / ".context", **MARKED)
    fails_with(begin(plain_repo), (
        "FAIL  on this Claude Code run the hook records the peer call; "
        "launch the peer with the lean slash command (do not run begin)"
    ))
    assert rec(plain_repo / ".context") is None
    assert not (plain_repo / ".context" / "subagent-runs" / "run-1" / "history.jsonl").exists()


@pytest.mark.parametrize("fields", [{"host": "claude"}, {"lean_record_by_hook": True, "host": "other"}])
def test_begin_writes_on_an_unmarked_or_other_host_run(plain_repo, fields):
    make_session(plain_repo / ".context", **fields)
    proc = begin(plain_repo)
    assert proc.returncode == 0, proc.stderr
    record = rec(plain_repo / ".context")
    assert record is not None
    assert record["command"] == "/plan-review plan.md"


def test_complete_keeps_the_hook_record(plain_repo, monkeypatch):
    ctx = plain_repo / ".context"
    make_session(ctx, **MARKED)
    write_delegation_record(ctx, run_id="run-1", stage="plan-review", command="/plan-review plan.md",
                            source="PreToolUse")
    before = rec(ctx)
    proc = complete(plain_repo)
    assert proc.returncode == 0, proc.stderr
    after = rec(ctx)
    for key in ("source", "command", "started_at", "pipeline_run_id", "stage"):
        assert after[key] == before[key]
    assert after["status"] == "complete" and after["completed_at"]
    assert after["artifact_path"].endswith("review.md")
    assert after["manifest_entry_id"] == "m-1"
    assert after["task_id"] == "gh-1" and after["subagent_id"] == "agent-9"
    monkeypatch.chdir(plain_repo)
    assert artifact_requirements._lean_delegation_problems(ctx, "plan-review") == []


def test_complete_with_no_hook_record_fails_on_a_marked_run(plain_repo):
    make_session(plain_repo / ".context", **MARKED)
    fails_with(complete(plain_repo), (  # The recovery text names the hook
        "FAIL  no hook record for plan-review: launch the peer with the lean slash command "
        "(the PreToolUse hook writes it; if no record appears, the plugin hooks did not run)"
    ))
    assert rec(plain_repo / ".context") is None


def test_complete_with_a_corrupt_record_fails_on_a_marked_run(plain_repo):
    ctx = plain_repo / ".context"
    make_session(ctx, **MARKED)
    path = delegation_record_path(ctx, "run-1", "plan-review")
    path.parent.mkdir(parents=True)
    path.write_text("{corrupt", encoding="utf-8")
    fails_with(complete(plain_repo), f"FAIL  unreadable hook record for plan-review: {path}")
    assert path.read_text(encoding="utf-8") == "{corrupt"


def test_complete_with_no_record_on_an_unmarked_run_keeps_todays_record(plain_repo):
    make_session(plain_repo / ".context", host="claude")
    proc = complete(plain_repo)
    assert proc.returncode == 0, proc.stderr
    record = rec(plain_repo / ".context")
    assert record["command"] == "" and record["started_at"] == ""
    assert record["stage"] == "plan-review" and record["pipeline_run_id"] == "run-1"


def test_begin_and_check_read_the_worktree_run(tmp_path):
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w0"], pipeline_run_id="run-a", host="other")
    save_worktree_session(trees["w1"], pipeline_run_id="run-b", host="other")
    proc = begin(trees["w0"])
    assert proc.returncode == 0, proc.stderr
    ctx = primary / ".context"
    assert rec(ctx, "run-a") is not None and rec(ctx, "run-b") is None
    proc = run(trees["w0"], "check", "--json")
    assert json.loads(proc.stdout)["run_id"] == "run-a"


# --- subagent_provenance_begin_stage_matches_command: `subagent_provenance.py begin` refuses a --stage that its command does not map to.
#
# A record filed under the wrong stage name never satisfies the gate. The error
# must come at `begin`, not later at the gate (`--stage validate` with
# `/vibe-test`).

def _begin(repo: Path, stage: str, command: str) -> subprocess.CompletedProcess[str]:
    return run(repo, "begin", "--stage", stage, "--command", command)


def test_begin_rejects_stage_that_command_does_not_map_to(tmp_path):
    repo = git_repo(tmp_path / "r")
    make_session(repo / ".context")
    proc = _begin(repo, "validate", "/vibe-test gh-1")
    assert proc.returncode == 1
    assert "vibe-test" in proc.stderr


def test_begin_accepts_stage_that_matches_command(tmp_path):
    repo = git_repo(tmp_path / "r")
    make_session(repo / ".context")
    proc = _begin(repo, "vibe-test", "/vibe-test gh-1")
    assert proc.returncode == 0, proc.stderr
