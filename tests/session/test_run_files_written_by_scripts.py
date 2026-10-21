"""The run's mechanical markdown comes from its block's script, never from the orchestrator.

A1 saves the user's request, P8 writes the zero deferral register, and R7 writes the manifest
that the /session-summary peer reads. R6 is in tests/gates/test_guard_gh_pr_comment.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import activate, activated_repo, branch_repo, git, read_session, run_py

REQUEST = "Add `budgetcli export`, which prints every expense as CSV.\nA note with a comma stays one field.\n"


def _activate_with(repo: Path, request: str, *flags: str):
    return run_py("scripts/activate_session.py", "--from-branch", *flags, "--request", "-", cwd=repo, stdin=request)


def test_a1_saves_the_request_as_written(tmp_path):
    repo = branch_repo(tmp_path / "proj", "feature/gh-7-toy")
    proc = _activate_with(repo, REQUEST, "--fresh")
    assert proc.returncode == 0, proc.stderr
    task = read_session(repo)["task_id"]
    text = (repo / ".context" / "proposals" / f"{task}.md").read_text(encoding="utf-8")
    assert f"Ticket: {task}" in text and REQUEST.strip() in text


def test_an_empty_request_fails_activation_and_writes_nothing(tmp_path):
    repo = branch_repo(tmp_path / "proj", "feature/gh-7-toy")
    proc = _activate_with(repo, "  \n", "--fresh")
    assert proc.returncode == 2 and "the request is empty" in proc.stderr
    assert not (repo / ".context" / "proposals").exists()


def test_a_resume_keeps_the_saved_request(tmp_path):
    repo = branch_repo(tmp_path / "proj", "feature/gh-7-toy")
    assert _activate_with(repo, REQUEST, "--fresh").returncode == 0
    task = read_session(repo)["task_id"]
    assert _activate_with(repo, "Something else.", "--resume").returncode == 0
    assert REQUEST.strip() in (repo / ".context" / "proposals" / f"{task}.md").read_text(encoding="utf-8")


def _p8(repo: Path, *flags: str):
    return run_py("scripts/deferral_adjudication_check.py", *flags, cwd=repo)


def test_p8_writes_the_zero_register_when_nobody_proposed_a_deferral(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    task = read_session(repo)["task_id"]
    assert _p8(repo, "--check").returncode == 1
    assert not (repo / ".context" / "deferrals").exists()
    proc = _p8(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    register = repo / ".context" / "deferrals" / f"{task}_deferral-register.md"
    assert "No approved deferrals" in register.read_text(encoding="utf-8")
    assert _p8(repo, "--check").returncode == 0


def test_p8_never_changes_a_register_that_a_peer_wrote(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    task = read_session(repo)["task_id"]
    register = repo / ".context" / "deferrals" / f"{task}_deferral-register.md"
    register.parent.mkdir(parents=True)
    register.write_text(f"# Register {task}\n\n## Approved deferrals\n\n| broken\n", encoding="utf-8")
    before = register.read_text(encoding="utf-8")
    _p8(repo)
    assert register.read_text(encoding="utf-8") == before


def test_r7_writes_the_manifest_and_waits_for_the_summary_peer(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    session = read_session(repo)
    task, run_id = session["task_id"], session["pipeline_run_id"]
    ctx = repo / ".context"
    (ctx / "prs").mkdir(parents=True, exist_ok=True)
    (ctx / "prs" / f"draft-pr-{run_id}.md").write_text(f"Pipeline-Run: {run_id}\nhttps://github.com/o/r/pull/12\n",
                                                     encoding="utf-8")
    (ctx / "findings.jsonl").write_text(json.dumps({"id": f"{task}-F1", "severity": "nit", "status": "open"}) + "\n",
                                        encoding="utf-8")
    git(repo, "commit", "-q", "--allow-empty", "-m", "feat: phase 1 the change")
    assert activate(repo, "--hard-stop", "not-needed", "--reason", "the change is not needed").returncode == 0
    proc = run_py("scripts/activate_session.py", "--deactivate", cwd=repo)
    assert proc.returncode == 1 and f"/session-summary .context/manifests/{task}.md" in proc.stderr
    manifest = (ctx / "manifests" / f"{task}.md").read_text(encoding="utf-8")
    assert f"Pipeline-Run: {run_id}" in manifest and "not-needed: the change is not needed" in manifest
    assert f"| .context/prs/draft-pr-{run_id}.md |" in manifest and "| .context/findings.jsonl |" in manifest
    assert "feat: phase 1 the change" in manifest and f".context/summaries/{task}.md" in manifest
    assert read_session(repo)["active_skill"] == "cadence"
    (ctx / "summaries").mkdir()
    (ctx / "summaries" / f"{task}.md").write_text("# Session summary\n\nRUN_BY_COMMAND: session-summary\n",
                                                encoding="utf-8")
    assert activate(repo, "--deactivate").returncode == 0
