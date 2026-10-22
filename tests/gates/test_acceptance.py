"""Acceptance checks: P1 writes executable checks from the request, P9 pins them, R5 runs them.

The feedback probe (examples/feedback-probe) showed a wrong deliverable that passed every gate:
no gate ran the deliverable against the request. These checks close that gap.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import code_paths, git, git_repo, make_session

code_paths()
import acceptance  # noqa: E402

TASK = "gh-1"


def _repo(tmp_path: Path, script: str = "import sys; sys.exit(0)\n") -> tuple[Path, Path]:
    repo = git_repo(tmp_path / "repo")
    (repo / "acceptance").mkdir()
    (repo / "acceptance" / "check_out.py").write_text(script, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    ctx = repo / ".context"
    make_session(ctx, task_id=TASK, pipeline_run_id="run-1", workspace=str(repo))
    return repo, ctx


def _write(ctx: Path, checks: list[dict], **extra) -> Path:
    path = ctx / "acceptance" / f"{TASK}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"RUN_BY_COMMAND": "plan-create", "task_id": TASK, "checks": checks, **extra}),
                    encoding="utf-8")
    return path


GOOD = {"id": "AC-1", "claim": "downstream reads only out/marts/", "run": "python3 acceptance/check_out.py"}


def test_p1_needs_the_acceptance_file(tmp_path):
    _repo_path, ctx = _repo(tmp_path)
    problems = acceptance.problems(ctx)
    assert problems and f"acceptance/{TASK}.json" in problems[0]


def test_a_valid_file_has_no_problems(tmp_path):
    _repo_path, ctx = _repo(tmp_path)
    _write(ctx, [GOOD])
    assert acceptance.problems(ctx) == []


@pytest.mark.parametrize("check, needle", [
    pytest.param({**GOOD, "run": "true"}, "no-op", id="noop"),
    pytest.param({**GOOD, "run": "manual"}, "manual", id="manual"),
    pytest.param({**GOOD, "claim": ""}, "claim", id="no_claim"),
    pytest.param({**GOOD, "id": "X"}, "AC-<n>", id="bad_id"),
])
def test_a_check_that_proves_nothing_fails(tmp_path, check, needle):
    _repo_path, ctx = _repo(tmp_path)
    _write(ctx, [check])
    assert any(needle in p for p in acceptance.problems(ctx))


def test_no_checks_fails(tmp_path):
    _repo_path, ctx = _repo(tmp_path)
    _write(ctx, [])
    assert any("no check" in p for p in acceptance.problems(ctx))


def test_two_checks_with_one_id_fail(tmp_path):
    _repo_path, ctx = _repo(tmp_path)
    _write(ctx, [GOOD, GOOD])
    assert any("AC-1" in p and "twice" in p for p in acceptance.problems(ctx))


def test_r5_runs_each_check_at_head(tmp_path):
    repo, ctx = _repo(tmp_path)
    _write(ctx, [GOOD])
    assert acceptance.pin(ctx, repo) == []
    assert acceptance.verify(ctx, repo) == []


def test_r5_fails_when_a_check_fails(tmp_path):
    repo, ctx = _repo(tmp_path, "import sys; print('out/marts/x.csv is missing'); sys.exit(1)\n")
    _write(ctx, [GOOD])
    acceptance.pin(ctx, repo)
    errors = acceptance.verify(ctx, repo)
    assert errors and "AC-1" in errors[0]
    assert "downstream reads only out/marts/" in errors[0] and "is missing" in errors[0]


def test_r5_fails_without_a_pin(tmp_path):
    repo, ctx = _repo(tmp_path)
    _write(ctx, [GOOD])
    errors = acceptance.verify(ctx, repo)
    assert errors and "P9" in errors[0]


def test_r5_fails_when_the_checks_changed_after_p9(tmp_path):
    repo, ctx = _repo(tmp_path)
    _write(ctx, [GOOD])
    acceptance.pin(ctx, repo)
    _write(ctx, [{**GOOD, "claim": "a weaker claim"}])
    assert any("changed after P9" in e for e in acceptance.verify(ctx, repo))


def test_r5_fails_when_a_check_script_changed_after_p9(tmp_path):
    repo, ctx = _repo(tmp_path, "import sys; sys.exit(1)\n")
    _write(ctx, [GOOD])
    acceptance.pin(ctx, repo)
    (repo / "acceptance" / "check_out.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    errors = acceptance.verify(ctx, repo)
    assert any("acceptance/check_out.py" in e and "changed after P9" in e for e in errors)


def test_the_cli_runs_the_checks(tmp_path):
    repo, ctx = _repo(tmp_path, "import sys; sys.exit(3)\n")
    _write(ctx, [GOOD])
    root = Path(__file__).resolve().parents[2]
    pinned = subprocess.run(["python3", str(root / "scripts" / "acceptance.py"), "pin"], cwd=repo,
                            capture_output=True, text=True)
    assert pinned.returncode == 0, pinned.stderr
    done = subprocess.run(["python3", str(root / "scripts" / "acceptance.py"), "run"], cwd=repo,
                          capture_output=True, text=True)
    assert done.returncode == 1 and "AC-1" in done.stderr


# --- the gates that read the checks: P1, P9, R5.

ROOT = Path(__file__).resolve().parents[2]


def _gate(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(ROOT / "scripts" / script), *args], cwd=repo, capture_output=True, text=True)


def test_the_p1_gate_names_a_missing_acceptance_file(tmp_path, monkeypatch):
    import gate_common

    repo, ctx = _repo(tmp_path)
    plan = ctx / "plans" / f"{TASK}.plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(f"RUN_BY_COMMAND: plan-create\nTicket: {TASK}\nPipeline-Run: run-1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert gate_common.run_plan_create_gate(ctx, check=True, worktree=repo) == 1
    _write(ctx, [GOOD])
    assert gate_common.run_plan_create_gate(ctx, check=True, worktree=repo) == 0


def test_p9_needs_the_checks(tmp_path):
    import entry_invariant

    repo, ctx = _repo(tmp_path)
    errors = entry_invariant.check_execution_entry(ctx, worktree=repo)
    assert any(f"acceptance/{TASK}.json" in e for e in errors)


def test_closure_runs_the_checks(tmp_path):
    repo, ctx = _repo(tmp_path, "import sys; print('no mart'); sys.exit(1)\n")
    _write(ctx, [GOOD])
    assert acceptance.pin(ctx, repo) == []
    done = _gate(repo, "closure.py", "--check")
    assert done.returncode == 1 and "AC-1" in done.stderr and "no mart" in done.stderr
    (repo / "acceptance" / "check_out.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    acceptance.pin(ctx, repo)
    assert _gate(repo, "closure.py", "--check").returncode == 0


def test_r5_with_no_run_judges_the_ledger_only(tmp_path):
    repo = git_repo(tmp_path / "bare")
    assert acceptance.verify(repo / ".context", repo, session={}) == []


def test_e1_runs_the_checks_before_the_draft_pr(tmp_path, monkeypatch):
    """E1.5: the acceptance checks run when the last phase is done, so a wrong deliverable never reaches E4."""
    import implement_gate
    from artifact_requirements import write_entry_invariant_ok

    repo, ctx = _repo(tmp_path, "import sys; print('no mart'); sys.exit(1)\n")
    _write(ctx, [GOOD])
    acceptance.pin(ctx, repo)
    write_entry_invariant_ok(ctx, pipeline_run_id="run-1", task_id=TASK, written_at="t")
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "feat: the change")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(implement_gate, "open_work", lambda state: None)
    assert implement_gate.run(ctx, check=True) == 1
    (repo / "acceptance" / "check_out.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    acceptance.pin(ctx, repo)
    assert implement_gate.run(ctx, check=True) == 0
