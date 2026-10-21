"""A5 gaps are carried into the plan (ADR-0019): vibe-test labels each blocking gap `G-n`, the plan
peer logs an assumption for each one in the register, and P9 fails while a `G-n` has no row."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import activated_repo, code_paths, read_session, write_files

code_paths()
from artifact_requirements import check_spec_gaps, spec_gap_ids  # noqa: E402
from gate_common import _a5_gaps  # noqa: E402


def _report(run_id: str, verdict: str, gaps: str) -> str:
    return (f"# Vibe test\n\nRUN_BY_SKILL: vibe-test\nPipeline-Run: {run_id}\n\n## Blocking gaps\n\n{gaps}\n"
            f"## Risks\n\n- G-9 is a risk, not a gap\n\nVerdict: {verdict}\n")


def _register(run_id: str, task: str, rows: str) -> str:
    return (f"# Register {task}\n\nTicket: {task}\nPipeline-Run: {run_id}\n\n## Approved deferrals\n\n"
            "No approved deferrals.\n\n## Assumptions\n\n| Gap | Assumption | Settled by |\n|---|---|---|\n"
            f"{rows}\n## Deferrals rejected\n\nNone proposed.\n")


@pytest.fixture
def run(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    session = read_session(repo)
    return repo, session["pipeline_run_id"], session["task_id"]


def test_only_the_blocking_gaps_section_names_gap_ids():
    assert spec_gap_ids(_report("r", "NOT READY FOR PLANNING", "- G-1: no day rule\n- G-2: no id rule\n")) == {"G-1", "G-2"}


def test_p9_fails_until_the_register_logs_an_assumption_for_each_gap(run):
    repo, run_id, task = run
    register = f".context/deferrals/{task}_deferral-register.md"
    write_files(repo, {f".context/vibetest/{task}_2026-10-21.md": _report(run_id, "NOT READY FOR PLANNING",
                                                                          "- G-1: no day rule\n- G-2: no id rule\n"),
                       register: _register(run_id, task, "| G-1 | days 1 to 28 | phase 2 |\n")})
    [error] = check_spec_gaps(repo / ".context", repo, read_session(repo))
    assert "for G-2 of" in error and "/plan-update" in error
    write_files(repo, {register: _register(run_id, task, "| G-1 | days 1 to 28 | phase 2 |\n| `G-2` | ids count up | phase 2 |\n")})
    assert check_spec_gaps(repo / ".context", repo, read_session(repo)) == []


def test_no_register_logs_no_assumption(run):
    repo, run_id, task = run
    write_files(repo, {f".context/vibetest/{task}_2026-10-21.md": _report(run_id, "NOT READY FOR PLANNING", "- G-1: x\n")})
    [error] = check_spec_gaps(repo / ".context", repo, read_session(repo))
    assert "G-1" in error


def test_a_ready_report_carries_nothing(run):
    repo, run_id, task = run
    write_files(repo, {f".context/vibetest/{task}_2026-10-21.md": _report(run_id, "READY FOR PLANNING", "None.\n")})
    assert check_spec_gaps(repo / ".context", repo, read_session(repo)) == []


@pytest.mark.parametrize("verdict, gaps, want", [
    ("NOT READY FOR PLANNING", "- no day rule\n", 1),
    ("NOT READY FOR PLANNING", "- G-1: no day rule\n", 0),
    ("READY FOR PLANNING", "None.\n", 0),
])
def test_a5_needs_an_id_for_each_blocking_gap(run, monkeypatch, verdict, gaps, want):
    repo, run_id, task = run
    write_files(repo, {f".context/vibetest/{task}_2026-10-21.md": _report(run_id, verdict, gaps)})
    monkeypatch.chdir(repo)
    assert len(_a5_gaps(repo / ".context")) == want
