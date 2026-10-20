"""Run scope: the planning gates (P1, P3) and the A5 gate accept only the artifacts of this run (cd-47, cd-93)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from conftest import RUN_A, RUN_B, code_paths, linked_worktrees, make_session, save_worktree_session

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import gate_common  # noqa: E402
import vibe_reports as vr  # noqa: E402
from artifact_requirements import (ORCHESTRATOR_UNKNOWN, check_plan_create_artifacts, run_scope_accept,  # noqa: E402
                                   select_plan_review)
from pipeline_run_binding import artifact_is_current, run_ids_in_text, stamp_markdown  # noqa: E402
from prompt_shape import write_delegation_record  # noqa: E402


# --- planning_gates_accept_only_current_run: P1 and P3 accept only this run's plan and review (cd-47 item 10, D8).
#
# On run efcd8404 the P1 gate passed for cd-47 before any cd-47 plan existed: a
# plan-create plan from run cd-32 sat in the shared .context. A planning artifact
# now counts when it names this run, or names no run and is in ticket scope.

RUN_0 = str(uuid.UUID(int=0x1111_2222_3333_4444_5555_6666_7777_8888))
RUN_1 = str(uuid.UUID(int=0x9999_aaaa_bbbb_cccc_dddd_eeee_ffff_0000))


def _session(context: Path, run: str = RUN_1) -> None:
    make_session(context, task_id="cd-47", ticket_tokens=["47", "cd-47"], pipeline_run_id=run)


def _plan(context: Path, name: str, run_line: str | None) -> Path:
    path = context / "plans" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "RUN_BY_COMMAND: plan-create\n" + (run_line + "\n" if run_line else "") + "# Plan\n### Phase 1: x\n"
    path.write_text(body, encoding="utf-8")
    return path


def _review(context: Path, name: str, run_line: str) -> Path:
    path = context / "reviews" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"RUN_BY_COMMAND: plan-review\n{run_line}\nTicket: cd-47\nVerdict: READY TO EXECUTE\n", encoding="utf-8")
    return path


def _p1_errors(context: Path, tmp_path: Path) -> list[str]:
    return check_plan_create_artifacts(context, tmp_path, accept=run_scope_accept(context))


def test_p1_fails_on_another_runs_plan_and_passes_on_this_runs(tmp_path):
    context = tmp_path / ".context"
    _session(context)
    _plan(context, "x_cd-32.plan.md", f"Pipeline-Run: {RUN_0}")
    assert _p1_errors(context, tmp_path)
    _plan(context, "y_cd-47.plan.md", f"Pipeline-Run: {RUN_1}")
    assert _p1_errors(context, tmp_path) == []


@pytest.mark.parametrize("label", ["Pipeline-Run", "pipeline_run_id"])
def test_a_plan_naming_this_ticket_and_another_run_fails(tmp_path, label):
    context = tmp_path / ".context"
    _session(context)
    _plan(context, "x_cd-47.plan.md", f"{label}: {RUN_0}")
    assert _p1_errors(context, tmp_path)


def test_a_plan_in_ticket_scope_with_no_run_id_passes(tmp_path):
    context = tmp_path / ".context"
    _session(context)
    _plan(context, "x_cd-47.plan.md", None)
    assert _p1_errors(context, tmp_path) == []


@pytest.mark.parametrize("run_line", ["- Pipeline-Run: {}", "**Pipeline run:** `{}`"])
def test_a_stale_list_or_bold_form_plan_fails(tmp_path, run_line):
    """MF-2: the old anchored parser read neither form, so these plans passed on ticket scope."""
    context = tmp_path / ".context"
    _session(context)
    _plan(context, "x_cd-47.plan.md", run_line.format(RUN_0))
    assert _p1_errors(context, tmp_path)


@pytest.mark.parametrize("run_line", ["Pipeline-Run: {}", "- Pipeline-Run: {}"])
def test_select_plan_review_skips_a_review_of_another_run(tmp_path, run_line):
    context = tmp_path / ".context"
    _session(context)
    _review(context, "plan_x_cd-47.md", run_line.format(RUN_0))
    assert select_plan_review(context) is None
    _review(context, "plan_y_cd-47.md", f"Pipeline-Run: {RUN_1}")
    chosen = select_plan_review(context)
    assert chosen is not None
    assert chosen[0].name == "plan_y_cd-47.md"


READ = [
    "Pipeline-Run: {id}",
    "pipeline_run_id: {id}",
    "Pipeline run: `{id}`",
    "- Pipeline-Run: {id}",
    "- pipeline_run_id: {id}",
    "* Pipeline-Run: {id}",
    "1. Pipeline-Run: {id}",
    "**Pipeline run:** `{id}`",
    "**Pipeline-Run**: {id}",
    "**Pipeline run**: `{id}`",
    "## Pipeline-Run: {id}",
    "> Pipeline-Run: {id}",
    "Pipeline-Run {id}",
]


@pytest.mark.parametrize("form", READ)
def test_run_ids_in_text_reads_every_form(form):
    assert run_ids_in_text(form.format(id=RUN_0)) == {RUN_0}


def test_upper_case_label_and_value_are_read_lower_cased():
    assert run_ids_in_text(f"PIPELINE-RUN: {RUN_0.upper()}") == {RUN_0}


@pytest.mark.parametrize("line", [
    "Pipeline-Run: not-a-uuid",
    f"Pipeline run {RUN_0} started",
    f"The Pipeline-Run: {RUN_0} line is in the old file.",
])
def test_run_ids_in_text_does_not_read_prose_or_non_uuids(line):
    assert run_ids_in_text(line) == set()


def test_artifact_is_current_binds_a_list_form_run_line(tmp_path):
    path = tmp_path / "x.md"
    path.write_text(f"RUN_BY_COMMAND: plan-create\n- Pipeline-Run: {RUN_1}\n", encoding="utf-8")
    ok, _detail = artifact_is_current(path, session={"pipeline_run_id": RUN_1, "activated_at": "2000-01-01T00:00:00+00:00"})
    assert ok


def test_stamp_markdown_does_not_add_a_second_run_line():
    text = f"RUN_BY_COMMAND: plan-create\n- Pipeline-Run: {RUN_1}\n"
    assert stamp_markdown(text, RUN_1) == text


# --- a5_run_scope: cd-93: the A5 gate judges only the reports of the run of its own worktree (#93).
#
# Runs live on host `cursor`, where no peer attestation filters reports, so the run scope
# is the only thing that keeps another run's report out (test rule 1). Each run has a lean
# vibe-test record (rule 2), so the lean check never decides a result.

LEAN_TEXTS = ("lean delegation", "has no pipeline_run_id", "lean prompt rejected",
              "lean delegation record has no command", "delegation record is for")


@pytest.fixture(autouse=True)
def _no_claude_attest(monkeypatch):
    """Rule 5 (P6 r1 C-2): peer_written_filter reads CLAUDECODE; same result here and in CI."""
    monkeypatch.delenv("CLAUDECODE", raising=False)


def _save(worktree: Path, fields: dict) -> None:
    """Save one run from one dict of fields, so a re-save cannot drift (SF-6)."""
    save_worktree_session(worktree, host="cursor", **fields)


def _lean(ctx: Path, run_id: str) -> None:
    write_delegation_record(ctx, run_id=run_id, stage="vibe-test", command="/vibe-test docs/spec.md",
                            source="PreToolUse")


def _fields(run: str, start: float, task_id: str) -> dict:
    return {"pipeline_run_id": run, "task_id": task_id, "activated_at": vr.iso(start)}


def _gate(ctx: Path, worktree: Path) -> int:
    return gate_common.run_artifact_gate("validate-gate", ctx, check=True, worktree=worktree)


def _error_lines(err: str) -> list[str]:
    return [line[4:] for line in err.splitlines() if line.startswith("  - ")]


def assert_gate_fails(capsys, rc: int, expected: list[str]) -> str:
    """Rule 3: rc 1, the FAIL line, and the error lines are exactly the expected set."""
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "FAIL  validate-gate" in err
    lines = _error_lines(err)
    assert lines, err
    assert all(any(want in line for want in expected) for line in lines), err
    assert all(any(want in line for line in lines) for want in expected), err
    assert not any(text in err for text in LEAN_TEXTS), err
    assert ORCHESTRATOR_UNKNOWN not in err, err
    return err


def assert_gate_passes(capsys, rc: int) -> None:
    """Rule 4: rc 0 and no error line."""
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert _error_lines(captured.err) == [], captured.err


@pytest.fixture
def runs(tmp_path):
    primary, trees = linked_worktrees(tmp_path, ("a", "b"))
    ctx = primary / ".context"
    return ctx, trees["a"], trees["b"], ctx / "vibetest"


def _run_a_active(runs) -> tuple[Path, Path, Path, float]:
    """Run A (cd-901) active in worktree A since 10 minutes ago, lean. Returns (ctx, a, vibe, start)."""
    ctx, a, _b, vibe = runs
    start = vr.now() - 600
    _save(a, _fields(RUN_A, start, "cd-901"))
    _lean(ctx, RUN_A)
    return ctx, a, vibe, start


def test_t9_the_root_pointer_does_not_decide_which_run_a5_judges(runs, capsys):
    """Acceptance 1 (the cd-63 incident): each worktree's gate judges its own run."""
    ctx, a, b, vibe = runs
    start = vr.now() - 600
    run_a, run_b = _fields(RUN_A, start, "cd-901"), _fields(RUN_B, start, "cd-902")
    _save(a, run_a)
    _save(b, run_b)  # the root redirect names B
    _lean(ctx, RUN_A)
    _lean(ctx, RUN_B)
    vr.write_report(vibe, "a.md", run=RUN_A)
    vr.write_report(vibe, "b.md", run=RUN_B, header=vr.LINEAR)
    assert_gate_passes(capsys, _gate(ctx, a))
    _save(a, run_a)  # now the root redirect names A
    err = assert_gate_fails(capsys, _gate(ctx, b), ["b.md"])
    assert "a.md" not in err


def test_t10_a_run_with_no_report_fails_and_names_no_old_report(runs, capsys):
    """Acceptance 2, 4, 6: no fallback; old reports of other tickets do not count."""
    ctx, a, vibe, start = _run_a_active(runs)
    vr.write_report(vibe, "old-good.md", header="**Tracker:** github-issues\n**Ticket:** #22\n",
                    mtime=start - vr.HOUR)
    vr.write_report(vibe, "old-bad.md", header=vr.LINEAR, mtime=start - vr.HOUR)
    err = assert_gate_fails(capsys, _gate(ctx, a), ["no verdict file"])
    assert "old-good.md" not in err and "old-bad.md" not in err


def test_t11_a_bad_own_report_fails_and_is_named(runs, capsys):
    """Acceptance 3."""
    ctx, a, vibe, _start = _run_a_active(runs)
    vr.write_report(vibe, "own.md", run=RUN_A, header=vr.LINEAR)
    assert_gate_fails(capsys, _gate(ctx, a), ["own.md"])


def test_t12_a5_is_run_scoped_with_p3_and_p6():
    """Acceptance 5: validate-gate joins the run-scoped gates; nothing else changes."""
    assert gate_common._RUN_SCOPED == {"validate-gate", "plan-review-gate", "adversarial-review-gate"}


def test_t13_a_report_naming_another_run_is_not_this_runs_evidence(runs, capsys):
    """Acceptance 7: newer than A's activation, but it names run B."""
    ctx, a, vibe, start = _run_a_active(runs)
    vr.write_report(vibe, "run-b.md", run=RUN_B, mtime=start + 60)
    err = assert_gate_fails(capsys, _gate(ctx, a), ["no verdict file"])
    assert "run-b.md" not in err


def test_t14_a_worktree_with_no_run_fails_a5(runs, capsys):
    """MF-1 (D7): only B has a run; A's gate must not borrow it through the root redirect."""
    ctx, a, b, vibe = runs
    _save(b, _fields(RUN_B, vr.now() - 600, "cd-902"))
    _lean(ctx, RUN_B)
    vr.write_report(vibe, "old.md", header="**Tracker:** github-issues\n**Ticket:** #22\n")
    err = assert_gate_fails(capsys, _gate(ctx, a), ["no Cadence run for this worktree"])
    assert f"The active run is in {b}" in err  # R1 N-3: the hint names the worktree that has a run


def test_t15_every_report_a5_accepts_has_its_header_judged(runs, capsys):
    """B-1 (D8): an own-ticket report with no run id, written before activation."""
    ctx, a, vibe, start = _run_a_active(runs)
    vr.write_report(vibe, "cd-901_old.md", header=vr.LINEAR, mtime=start - vr.HOUR)
    err = assert_gate_fails(capsys, _gate(ctx, a), ["cd-901_old.md"])
    assert "header names Linear" in err


def test_on_claude_code_a5_filters_its_own_run_by_peer_writes(runs, capsys, monkeypatch, tmp_path):
    """R1 N-2: the peer filter reads the worktree's claude run, not the process cwd, even with
    CLAUDECODE unset (a gate walk from a hook). An unattested report of that run fails."""
    ctx, a, b, vibe = runs
    monkeypatch.chdir(tmp_path)
    save_worktree_session(a, host="claude", orchestrator_source="toplevel",
                          **_fields(RUN_A, vr.now() - 600, "cd-901"))
    _save(b, _fields(RUN_B, vr.now() - 600, "cd-902"))  # the root redirect names B, a cursor run
    _lean(ctx, RUN_A)
    _lean(ctx, RUN_B)
    vr.write_report(vibe, "own.md", run=RUN_A)
    err = assert_gate_fails(capsys, _gate(ctx, a), ["no verdict file"])
    assert "own.md" not in err
