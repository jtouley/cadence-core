"""Repo states for the Pi extension tests. One builder for Python and Node tests."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import git, git_repo, hook_session

STATES = ("before_p9", "e1", "e1_owned", "finished", "none", "fresh")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pi"


def _src(repo: Path) -> None:
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")


def _p9(repo: Path) -> None:
    gates = repo / ".context" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "entry-invariant-run-1.ok").write_text(json.dumps({"task_id": "gh-1", "pipeline_run_id": "run-1"}) + "\n", encoding="utf-8")


def repo_for(state: str, root: Path) -> Path:
    """A git repo in one run state. `fresh` has one commit on a named branch and no run."""
    assert state in STATES, state
    repo = git_repo(root / state)
    _src(repo)
    if state == "fresh":
        git(repo, "checkout", "-q", "-b", "feature/gh-1-pi")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
    elif state == "before_p9":
        hook_session(repo, resume_slug="plan-create")
    elif state == "e1":
        hook_session(repo, resume_slug="implement")
        _p9(repo)
    elif state == "e1_owned":
        hook_session(repo, "pointer", resume_slug="implement", owner_session_id="pi-sess-1")
        _p9(repo)
    elif state == "finished":
        hook_session(repo, active_skill=None, resume_slug="session-close")
    return repo


def fixtures() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) | {"name": p.stem} for p in sorted(FIXTURES.glob("*.json"))]
