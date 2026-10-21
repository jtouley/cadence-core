"""/pr-review-update reads the PR's GitHub review threads.

scripts/pr_threads.py prints the threads as JSON, so the peer runs one command, not raw GraphQL.
An unresolved thread from a human is a must-fix. With `adapter: none`, no `gh` on PATH, or a
failed call, the status is `unsupported` (exit 2), and the review says so in one line.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import install_config, project_config, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

THREADS = {"data": {"repository": {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [
    {"id": "T1", "isResolved": False, "isOutdated": False, "path": "a.py", "line": 3, "comments": {"nodes": [
        {"author": {"login": "alice", "__typename": "User"}, "body": "This drops the error.\nSee line 3.", "url": "u1"}]}},
    {"id": "T2", "isResolved": True, "isOutdated": False, "path": "b.py", "line": 9, "comments": {"nodes": [
        {"author": {"login": "bob", "__typename": "User"}, "body": "Rename it.", "url": "u2"}]}},
    {"id": "T3", "isResolved": False, "isOutdated": True, "path": "c.py", "line": None, "comments": {"nodes": [
        {"author": {"login": "lint-bot", "__typename": "Bot"}, "body": "Style.", "url": "u3"}]}},
]}}}}}


def _gh(tmp_path: Path, stdout: str, code: int = 0) -> str:
    """A PATH whose first `gh` is a stub: it logs its arguments and prints `stdout`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "out.json").write_text(stdout, encoding="utf-8")
    stub = bin_dir / "gh"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path / "gh.log"}\ncat {tmp_path / "out.json"}\n'
                    f'echo "error connecting to api.github.com" >&2\nexit {code}\n', encoding="utf-8")
    stub.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


def _threads(tmp_path: Path, path: str) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/pr_threads.py", "42", cwd=tmp_path, PATH=path)


def test_each_thread_has_a_status_and_an_open_human_thread_is_a_must_fix(tmp_path):
    proc = _threads(tmp_path, _gh(tmp_path, json.dumps(THREADS)))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["status"] == "supported" and out["pr"] == 42
    assert [(t["id"], t["state"], t["must_fix"]) for t in out["threads"]] == [
        ("T1", "open", True), ("T2", "resolved", False), ("T3", "outdated", False)]
    assert out["threads"][0]["first_line"] == "This drops the error."
    assert out["must_fix"] == ["T1"]
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert log.startswith("api graphql") and "number=42" in log and "reviewThreads" in log


@pytest.mark.parametrize("stdout, code, reason", [
    pytest.param("", 1, "error connecting to api.github.com", id="offline"),
    pytest.param("{}", 0, "no review threads", id="no-threads-in-the-answer"),
])
def test_a_failed_call_is_unsupported(tmp_path, stdout, code, reason):
    proc = _threads(tmp_path, _gh(tmp_path, stdout, code))
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["status"] == "unsupported" and reason in out["reason"]


def test_no_gh_on_path_is_unsupported(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = _threads(tmp_path, str(empty))
    assert proc.returncode == 2
    assert json.loads(proc.stdout) == {"status": "unsupported", "reason": "gh is not on PATH"}


def test_adapter_none_is_unsupported(tmp_path, monkeypatch):
    import pr_threads

    install_config(tmp_path)
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "none.json").write_text('{"name": "none"}', encoding="utf-8")
    project_config(tmp_path, {"adapter": "none"})
    monkeypatch.chdir(tmp_path)  # the config is resolved for the cwd
    assert pr_threads.report(42, None, tmp_path) == {
        "status": "unsupported", "reason": "the adapter is none, not github-issues"}


@pytest.mark.parametrize("phrase", [
    'python3 "$CADENCE_HOME/scripts/pr_threads.py" <PR>',
    "### Review threads",
    "An unresolved thread from a human is a must-fix",
    "`Review threads: unsupported (<reason>).`",
])
def test_pr_review_update_reads_the_review_threads(phrase):
    assert phrase in (ROOT / "commands" / "pr-review-update.md").read_text(encoding="utf-8")
