"""The shared test git helper ignores the developer's git config (#46).

A developer with commit.gpgsign=true and no signing key saw test_phase_ledger and
test_implement_gate fail. The helper sets GIT_CONFIG_GLOBAL and GIT_CONFIG_NOSYSTEM,
so a hostile global config cannot change what a test commit does.
"""

from __future__ import annotations

import subprocess

from conftest import git, git_env


def test_helper_commits_under_a_hostile_global_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    assert git(repo, "log", "--format=%s").strip() == "base"


def test_git_env_ignores_global_and_system_config():
    env = git_env()
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_AUTHOR_NAME"] and env["GIT_COMMITTER_EMAIL"]


def test_a_direct_subprocess_git_call_is_hermetic_too(tmp_path, monkeypatch):
    """The autouse fixture covers git calls that do not go through the helper."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    proc = subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "x"], cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
