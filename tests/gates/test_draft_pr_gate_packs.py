"""ADR-0007: with the deferrals pack off (no E2), E4 reads the diff since the merge base itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import enable_all_packs, git


@pytest.fixture
def run(tmp_path, monkeypatch, scripts_path):
    """A draft-PR record and a session of run-1 on feature/gh-28, branched from main; no E2 artifact."""
    import draft_pr_gate

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    git(repo, "checkout", "-q", "-b", "feature/gh-28")
    (repo / ".git" / "info" / "exclude").write_text(".context/\n", encoding="utf-8")  # as in a real repo
    monkeypatch.chdir(repo)
    monkeypatch.setattr(draft_pr_gate, "current_branch", lambda *_a: "feature/gh-28")
    context = repo / ".context"
    (context / "prs").mkdir(parents=True)
    (context / "prs" / "draft-pr.md").write_text(
        "RUN_BY_COMMAND: commit-push-pr\nPR: https://github.com/o/r/pull/28\nBranch: feature/gh-28\n", encoding="utf-8")
    session = {"active_skill": "cadence", "task_id": "gh-28", "pipeline_run_id": "run-1",
               "workspace": str(repo), "base": "main"}
    (context / "cadence-session.json").write_text(json.dumps(session), encoding="utf-8")
    git(repo, "commit", "-q", "--allow-empty", "-m", "noop")
    return repo, context, draft_pr_gate


def _commit_work(repo: Path) -> None:
    (repo / "x.py").write_text("X = 1\n", encoding="utf-8")
    git(repo, "add", "x.py")
    git(repo, "commit", "-q", "-m", "work")


def test_e4_passes_on_the_live_diff_when_e2_is_off(run):
    repo, context, gate = run
    _commit_work(repo)
    assert gate.run(context) == 0
    assert (context / "gates" / "draft-pr-run-1.json").is_file()


def test_e4_fails_with_no_diff_when_e2_is_off(run, capsys):
    repo, context, gate = run  # the branch has one empty commit since main
    assert gate.run(context) == 1
    assert "no change since the merge base" in capsys.readouterr().err


def test_e4_requires_the_e2_artifact_when_the_deferrals_pack_is_on(run, capsys):
    repo, context, gate = run
    _commit_work(repo)
    enable_all_packs(repo)
    assert gate.run(context) == 1
    assert "impl artifact is missing" in capsys.readouterr().err
