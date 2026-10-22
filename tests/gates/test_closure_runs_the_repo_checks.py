"""R5 runs the repository's own checks: the code works and passes them, or the run is not done.

A failing check becomes a must-fix row of the run with the command as its check, so the fix round
fixes it. The stop guard's read-only walk runs no suite and writes no row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import activated_repo, code_paths, read_session, run_py, write_files

code_paths()
from repo_checks import check_commands  # noqa: E402


@pytest.mark.parametrize("files, commands", [
    pytest.param({}, [], id="nothing-to-run"),
    pytest.param({"Makefile": "test:\n\tpytest\n", "pyproject.toml": "", "tests/x.py": ""}, ["make test"],
                 id="make-test-wins"),
    pytest.param({"pyproject.toml": "", "tests/test_x.py": "", ".venv/bin/python": ""}, [".venv/bin/python -m pytest -q"],
                 id="pytest-in-the-repo-venv"),
    pytest.param({"package.json": '{"scripts": {"test": "jest"}}'}, ["npm test"], id="npm"),
    pytest.param({"package.json": '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'}, [],
                 id="npm-placeholder"),
    pytest.param({"go.mod": "module x\n", "Cargo.toml": ""}, ["go test ./...", "cargo test"], id="go-and-cargo"),
    pytest.param({".cadence.json": '{"ci_commands": ["make lint", "make test"]}', "go.mod": ""},
                 ["make lint", "make test"], id="the-repo-names-its-checks"),
])
def test_the_checks_come_from_the_repo(tmp_path, files, commands):
    write_files(tmp_path, files)
    assert check_commands(tmp_path) == commands


def _repo(tmp_path: Path, check: str) -> Path:
    repo = activated_repo(tmp_path / "proj")
    write_files(repo, {"check.py": "import sys\nraise SystemExit(int(sys.argv[1]))\n"})  # a check of the repo
    (repo / ".cadence.json").write_text(json.dumps({"ci_commands": [check]}), encoding="utf-8")
    return repo


def _rows(repo: Path) -> list[dict]:
    ledger = repo / ".context" / "findings.jsonl"
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()] if ledger.is_file() else []


def test_a_failing_check_fails_closure_and_becomes_one_must_fix_row(tmp_path):
    repo = _repo(tmp_path, "python3 check.py 3")
    task = read_session(repo)["task_id"]
    for _ in range(2):
        proc = run_py("scripts/closure.py", cwd=repo)
        assert proc.returncode == 1 and "exits 3 at HEAD" in proc.stderr and "send the fix round" in proc.stderr
    [row] = _rows(repo)
    assert row["id"] == f"{task}-F1" and row["severity"] == "must-fix" and row["status"] == "open"
    assert row["check"] == "python3 check.py 3" and row["stage"] == "closure"


def test_a_passing_check_lets_closure_pass_and_check_mode_reuses_it(tmp_path):
    repo = _repo(tmp_path, "python3 check.py 0")
    assert run_py("scripts/closure.py", cwd=repo).returncode == 0
    (repo / ".cadence.json").write_text(json.dumps({"ci_commands": ["python3 check.py 1"]}),
                                        encoding="utf-8")
    assert run_py("scripts/closure.py", "--check", cwd=repo).returncode == 0  # passed at this HEAD
    assert _rows(repo) == []


def test_the_read_only_walk_runs_no_suite_and_writes_no_row(tmp_path):
    repo = _repo(tmp_path, "python3 check.py 3")
    proc = run_py("scripts/closure.py", "--check", cwd=repo, CADENCE_GATES_READONLY="1")
    assert proc.returncode == 1 and "have not passed at HEAD" in proc.stderr
    assert _rows(repo) == []


def test_pytest_runs_with_an_interpreter_that_has_it(tmp_path):
    """The child shell's `python3` had no pytest, so R5 opened a must-fix that no fix round could close."""
    import shlex
    import sys

    write_files(tmp_path, {"pyproject.toml": "", "tests/test_x.py": ""})
    assert check_commands(tmp_path) == [f"{shlex.quote(sys.executable)} -m pytest -q"]  # this interpreter has pytest


def test_a_runner_that_is_not_there_is_an_environment_problem_not_a_must_fix(tmp_path):
    repo = _repo(tmp_path, "python3 -m no_such_runner_for_cadence")
    proc = run_py("scripts/closure.py", cwd=repo)
    assert proc.returncode == 1 and "cannot run here" in proc.stderr and "ci_commands" in proc.stderr
    assert _rows(repo) == []


def test_a_check_that_the_check_rules_do_not_take_is_run_through_repo_checks(tmp_path, monkeypatch):
    """`gradle test` names no runner that findings_io knows: the row runs it with `repo_checks.py --run`."""
    repo = _repo(tmp_path, "sh check.sh")
    write_files(repo, {"check.sh": "exit ${CHECK_EXIT:-3}\n"})
    assert run_py("scripts/closure.py", cwd=repo).returncode == 1
    [row] = _rows(repo)
    assert row["check"] == "python3 $CADENCE_HOME/scripts/repo_checks.py --run 'sh check.sh'"
    assert run_py("scripts/repo_checks.py", "--run", "sh check.sh", cwd=repo).returncode == 3
    assert run_py("scripts/repo_checks.py", "--run", "sh check.sh", cwd=repo, CHECK_EXIT="0").returncode == 0
    proc = run_py("scripts/repo_checks.py", "--run", "python3 -c pass", cwd=repo)  # not a check of the repo
    assert proc.returncode == 2 and "not a repo check" in proc.stderr
