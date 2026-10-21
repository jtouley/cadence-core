"""A run ends at R7, or at a recorded hard stop. `--deactivate` is not an escape hatch.

cd-32 / 2d541e75: the agent deactivated at A1 or after R1, and every stop hold ended.
`--hard-stop <class> --reason <text>` is the one sanctioned writer for the hard stop
that SKILL.md names (destructive risk, missing credentials, security, retries spent),
plus the two no-PR ends (an A5 gap, work not needed).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import peer_summary, git_repo, p9_marker, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import session_store  # noqa: E402

ACTIVATE = ROOT / "scripts" / "activate_session.py"


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_py(ACTIVATE, *args, cwd=repo)


def _active_repo(tmp_path: Path) -> Path:
    repo = git_repo(tmp_path / "r")
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "i"], cwd=repo, check=True)
    assert _run(repo, "--task-id", "gh-3", "--fresh").returncode == 0
    return repo


def test_deactivate_before_closure_is_refused(tmp_path):
    repo = _active_repo(tmp_path)
    proc = _run(repo, "--deactivate")
    assert proc.returncode == 1
    assert "--hard-stop" in proc.stderr
    assert session_store.load_worktree_session(repo)["active_skill"] == "cadence"


def test_hard_stop_records_class_and_reason_then_deactivate_works(tmp_path):
    repo = _active_repo(tmp_path)
    proc = _run(repo, "--hard-stop", "retries", "--reason", "plan review did not converge")
    assert proc.returncode == 0, proc.stderr
    stop = session_store.load_worktree_session(repo)["hard_stop"]
    assert stop["class"] == "retries" and "converge" in stop["reason"]
    assert _run(repo, "--deactivate").returncode == 1  # the summary peer writes first
    peer_summary(repo)
    assert _run(repo, "--deactivate").returncode == 0
    assert session_store.load_worktree_session(repo)["active_skill"] is None


def test_hard_stop_rejects_an_unknown_class(tmp_path):
    repo = _active_repo(tmp_path)
    proc = _run(repo, "--hard-stop", "bored", "--reason", "x")
    assert proc.returncode == 2
    assert "hard_stop" not in session_store.load_worktree_session(repo)


def test_hard_stop_needs_a_reason(tmp_path):
    repo = _active_repo(tmp_path)
    assert _run(repo, "--hard-stop", "security").returncode == 2


def test_stop_guard_allows_a_stop_after_a_hard_stop(tmp_path, run_hook):
    repo = _active_repo(tmp_path)
    gates = repo / ".context" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    p9_marker(repo).write_text(json.dumps({"task_id": "gh-3"}), encoding="utf-8")  # cd-76: the run's own marker
    _run(repo, "--hard-stop", "credentials", "--reason", "gh auth expired")
    code, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)})
    assert code == 0 and "followup_message" not in out
