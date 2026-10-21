"""Phase 4: closure fails on open must-fix; passes on terminal statuses."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from closure import closure_errors  # noqa: E402
from findings_io import append_finding, load_findings, rewrite_findings  # noqa: E402

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "finding_open_must_fix.json").read_text()
)


def test_closure_rejects_open_must_fix(tmp_path):
    path = tmp_path / "f.jsonl"
    append_finding(path, dict(FIXTURE))
    errs = closure_errors(load_findings(path))
    assert errs and "open" in errs[0]


@pytest.mark.parametrize("status", ["verified", "deferred", "spec_gap"])
def test_closure_accepts_terminal_must_fix(tmp_path, status):
    path = tmp_path / "f.jsonl"
    row = dict(FIXTURE)
    if status == "verified":
        row.update(status="fixed", fixed_in="0" * 40)
        append_finding(path, row)
        fixed = load_findings(path)[0]
        fixed["status"] = "verified"
        rewrite_findings(path, [fixed])
    else:
        row["status"] = status
        append_finding(path, row)
    assert closure_errors(load_findings(path)) == []


# --- one task-prefix rule and one ledger-row builder ------------------------------------------

from conftest import ledger_row, write_ledger  # noqa: E402
from findings_io import FindingsValidationError, rows_of_task, split_by_task  # noqa: E402


def test_ledger_row_and_write_ledger_round_trip(tmp_path):
    """P7 cd-49-F01: one builder for every ledger row a test writes."""
    assert ledger_row("cd-901-F1") == {**FIXTURE, "id": "cd-901-F1"}
    nit = ledger_row("cd-901-F2", severity="nit", status="fixed")
    assert (nit["severity"], nit["status"]) == ("nit", "fixed")
    done = ledger_row("cd-901-F3", status="verified")
    path = write_ledger(tmp_path / "f.jsonl", ledger_row("cd-901-F1"), done)
    assert [r["id"] for r in load_findings(path)] == ["cd-901-F1", "cd-901-F3"]
    bad = {k: v for k, v in ledger_row("cd-901-F4").items() if k != "evidence"}
    with pytest.raises(FindingsValidationError):
        write_ledger(tmp_path / "g.jsonl", bad)
    assert not (tmp_path / "g.jsonl").exists()


@pytest.mark.parametrize("task_id, finding_id, kept", [
    ("cd-63", "CD-63-F1", True),
    ("cd-6", "cd-63-F1", False),
    ("gh-1", "gh-1-initial-cadence-publish-F1", False),
    ("cd-63", "cd-63-F1-F2", False),  # a row of task `cd-63-F1`; kills the no-`$` mutant
    ("owner/repo#12", "owner-repo-12-F3", True),
    ("", "CADENCE-F1", True),
])
def test_rows_of_task_is_anchored_and_ignores_task_case(task_id, finding_id, kept):
    assert bool(rows_of_task([ledger_row(finding_id)], task_id)) is kept


def test_split_by_task_other_and_unowned():
    rows = [ledger_row(i) for i in ("cd-901-F1", "cd-902-F1", "zz-1-F1", "CD-901-F2")]
    split = split_by_task(rows, "cd-901", {"cd-901", "cd-902"})
    assert [r["id"] for r in split.own] == ["cd-901-F1", "CD-901-F2"]
    assert [r["id"] for r in split.other] == ["cd-902-F1"]
    assert [r["id"] for r in split.unowned] == ["zz-1-F1"]


def test_known_task_ids_keeps_a_completed_run(two_worktree_runs):
    """P7 cd-49-F04: a finished run stays known, so its rows are other, not unowned."""
    from pipeline_run_binding import known_task_ids

    shared = str(ROOT / "shared" / "scripts")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import session_store

    env = two_worktree_runs
    session_store.deactivate_session(env.b)
    assert {"cd-901", "cd-902"} <= known_task_ids(env.ctx)


# --- R5 judges only the must-fix rows of its run's task ----------------------------------------


def _r5(monkeypatch, capsys, cwd, *extra):
    import closure

    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["closure.py", "--check", *extra])
    code = closure.main()
    out = capsys.readouterr()
    lines = [l for l in (out.out if code == 0 else out.err).splitlines() if l.startswith("{")]
    return code, json.loads(lines[-1])


SCOPE_KEYS = {"task_id", "scope", "other_task_skipped", "unowned"}


def test_r5_judges_only_the_runs_task(two_worktree_runs, monkeypatch, capsys):
    env = two_worktree_runs
    write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-902-F1"))
    code, summary = _r5(monkeypatch, capsys, env.a)
    assert code == 0 and summary["scope"] == "task" and summary["other_task_skipped"] == 1
    code, summary = _r5(monkeypatch, capsys, env.b)
    assert code == 1 and SCOPE_KEYS <= summary.keys() and summary["task_id"] == "cd-902"


def test_r5_lists_unowned_rows_and_passes(two_worktree_runs, monkeypatch, capsys):
    env = two_worktree_runs
    write_ledger(env.ctx / "findings.jsonl", ledger_row("zz-1-F1"))
    code, summary = _r5(monkeypatch, capsys, env.a)
    assert code == 0 and summary["unowned"] == ["zz-1-F1"]


@pytest.mark.parametrize("where", ["primary", "no-task-id"])
def test_r5_with_no_run_judges_every_row(two_worktree_runs, monkeypatch, capsys, where):
    from conftest import save_worktree_session

    env = two_worktree_runs
    cwd = env.primary
    if where == "no-task-id":
        save_worktree_session(env.a, pipeline_run_id=env.run_a, task_id="")
        cwd = env.a
    write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-902-F1"))
    code, summary = _r5(monkeypatch, capsys, cwd)
    assert code == 1 and summary["scope"] == "all"


def test_r5_explicit_ledger_keeps_the_run_scope(two_worktree_runs, monkeypatch, capsys):
    """P7 cd-49-F02: --ledger changes the file, not the filter."""
    env = two_worktree_runs
    ledger = write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-902-F1"))
    code, summary = _r5(monkeypatch, capsys, env.a, "--ledger", str(ledger))
    assert code == 0 and summary["scope"] == "task" and summary["other_task_skipped"] == 1


def test_r5_corrupt_row_of_another_task_fails(two_worktree_runs, monkeypatch):
    """P7 cd-49-F03 (C5): every row is validated before the filter."""
    import closure

    env = two_worktree_runs
    ledger = write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-901-F1", status="deferred", ticket="#1"))
    bad = {k: v for k, v in ledger_row("cd-902-F1").items() if k != "evidence"}
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(bad) + "\n")
    monkeypatch.chdir(env.a)
    monkeypatch.setattr(sys, "argv", ["closure.py", "--check"])
    with pytest.raises(FindingsValidationError):
        closure.main()
