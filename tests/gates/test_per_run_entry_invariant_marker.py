"""cd-76 Phase 2: the P9 marker is per run (gates/entry-invariant-<run_id>.ok).

Two runs in two linked worktrees share one .context. P9, E1, the skill gate's
gated-edit read, the stop guard's run_started and --deactivate each use the marker
of their own run. The legacy shared marker gates/entry-invariant.ok is not read.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import peer_summary, RUN_A, activate, save_worktree_session, stop_guard_module

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "scripts", ROOT / "shared", ROOT / "shared" / "scripts"):
    sys.path.insert(0, str(_p))

import artifact_requirements  # noqa: E402
import entry_invariant  # noqa: E402
import implement_gate  # noqa: E402
from artifact_requirements import entry_invariant_ok_path, write_entry_invariant_ok  # noqa: E402
from pipeline_run_binding import run_for  # noqa: E402
from skill_gate import CadenceGate  # noqa: E402


def _guard():
    return stop_guard_module("stop_guard_cd76")


def marker(ctx: Path, run: str) -> Path:
    return ctx / "gates" / f"entry-invariant-{run}.ok"


def p9(ctx: Path, run: str, task: str) -> Path:
    return write_entry_invariant_ok(ctx, pipeline_run_id=run, task_id=task, written_at="2026-10-14T00:00:00Z")


def run_p9(monkeypatch, cwd: Path, errors: list[str], *flags: str) -> int:
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(entry_invariant, "check_execution_entry", lambda *a, **k: list(errors))
    monkeypatch.setattr(sys, "argv", ["entry_invariant.py", "--phase", "execution", *flags])
    return entry_invariant.main()


def e1_marker_errors(monkeypatch, cwd: Path, ctx: Path) -> list[str]:
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(implement_gate, "open_work", lambda *_a, **_k: None)
    captured: list[str] = []
    monkeypatch.setattr(implement_gate, "fail", lambda _slug, errors: captured.extend(errors) or 1)
    monkeypatch.setattr(implement_gate, "ok", lambda _slug: 0)
    implement_gate.run(ctx, check=True)
    return [e for e in captured if "entry_invariant" in e or "P9" in e]


def gated_edit_allowed(workspace: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    return CadenceGate(workspace).check_action("write", str(target)).allowed


# --- P9 writes, keeps and clears only its own run's marker ----------------------------------------


def test_p9_of_run_a_writes_its_marker_and_keeps_run_b(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    p9(w.ctx, w.run_b, "cd-902")
    assert run_p9(monkeypatch, w.a, []) == 0
    assert json.loads(marker(w.ctx, w.run_a).read_text())["pipeline_run_id"] == w.run_a
    assert marker(w.ctx, w.run_b).is_file()
    assert not (w.ctx / "gates" / "entry-invariant.ok").exists()


@pytest.mark.parametrize("flags", [(), ("--check",)])
def test_a_failed_p9_clears_only_its_own_marker(two_worktree_runs, monkeypatch, flags):
    w = two_worktree_runs
    p9(w.ctx, w.run_a, "cd-901")
    p9(w.ctx, w.run_b, "cd-902")
    assert run_p9(monkeypatch, w.a, ["plan-create: missing"], *flags) != 0
    assert not marker(w.ctx, w.run_a).exists()
    assert marker(w.ctx, w.run_b).is_file()


def test_p9_from_the_primary_checkout_with_no_run_fails_and_touches_nothing(two_worktree_runs, monkeypatch, capsys):
    w = two_worktree_runs
    p9(w.ctx, w.run_a, "cd-901")
    p9(w.ctx, w.run_b, "cd-902")
    before = sorted(p.name for p in (w.ctx / "gates").iterdir())
    assert run_p9(monkeypatch, w.primary, []) != 0
    out = capsys.readouterr()
    assert "no Cadence run in this worktree; run P9 from the run's worktree" in out.out + out.err
    assert sorted(p.name for p in (w.ctx / "gates").iterdir()) == before


def test_a_fresh_activation_clears_no_other_runs_marker(two_worktree_runs, tmp_path):
    w = two_worktree_runs
    p9(w.ctx, w.run_a, "cd-901")
    p9(w.ctx, w.run_b, "cd-902")
    wc = tmp_path / "wc"
    subprocess.run(["git", "-C", str(w.primary), "worktree", "add", "-q", "-b", "feature/gh-903-c", str(wc)],
                   check=True, capture_output=True)
    activate(wc)
    assert marker(w.ctx, w.run_a).is_file() and marker(w.ctx, w.run_b).is_file()


# --- readers: E1, the skill gate, run_started ------------------------------------------------------


def test_e1_in_worktree_b_reads_run_b_marker(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    p9(w.ctx, w.run_a, "cd-901")
    assert e1_marker_errors(monkeypatch, w.b, w.ctx) == [
        f"implement-gate: {marker(w.ctx, w.run_b)} missing; "
        "run entry_invariant.py (P9) to clear all pre-execution gates first"
    ]
    p9(w.ctx, w.run_b, "cd-902")
    assert e1_marker_errors(monkeypatch, w.b, w.ctx) == []


def test_e1_from_a_worktree_with_no_run_names_no_marker(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    p9(w.ctx, w.run_b, "cd-902")
    assert e1_marker_errors(monkeypatch, w.primary, w.ctx) == [
        "implement-gate: no Cadence run in this worktree; run P9 and E1 from the run's worktree"
    ]


def test_gated_edit_in_worktree_b_reads_run_b_marker(two_worktree_runs):
    w = two_worktree_runs
    target = w.b / "src" / "x.py"
    p9(w.ctx, w.run_a, "cd-901")
    assert not gated_edit_allowed(w.b, target)
    p9(w.ctx, w.run_b, "cd-902")
    assert gated_edit_allowed(w.b, target)
    p9(w.ctx, w.run_a, "cd-901")  # run A writes its marker later
    assert gated_edit_allowed(w.b, target)


def _three_readers(w, monkeypatch) -> tuple[bool, bool, bool]:
    e1_ok = e1_marker_errors(monkeypatch, w.b, w.ctx) == []
    edit_ok = gated_edit_allowed(w.b, w.b / "src" / "x.py")
    started = _guard().run_started(w.ctx, run_for(w.ctx, cwd=w.b))
    return e1_ok, edit_ok, started


def test_the_legacy_shared_marker_is_not_read(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    legacy = w.ctx / "gates" / "entry-invariant.ok"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"pipeline_run_id": w.run_b, "task_id": "cd-902"}), encoding="utf-8")
    assert _three_readers(w, monkeypatch) == (False, False, False)


def test_an_unparsable_run_marker_counts_as_past_p9(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    path = marker(w.ctx, w.run_b)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n", encoding="utf-8")
    assert _three_readers(w, monkeypatch) == (True, True, True)


def test_a_run_marker_that_names_another_run_is_not_evidence(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    path = marker(w.ctx, w.run_b)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pipeline_run_id": w.run_a, "task_id": "cd-902"}), encoding="utf-8")
    assert _three_readers(w, monkeypatch) == (False, False, False)


def test_a_run_marker_with_no_run_id_keeps_the_task_rule(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    path = marker(w.ctx, w.run_b)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"task_id": "cd-902"}), encoding="utf-8")
    assert _three_readers(w, monkeypatch) == (True, True, True)
    path.write_text(json.dumps({"task_id": "cd-999"}), encoding="utf-8")
    assert _three_readers(w, monkeypatch) == (False, False, False)


def test_entry_invariant_ok_path_names_the_run(tmp_path):
    assert entry_invariant_ok_path(tmp_path, RUN_A) == tmp_path / "gates" / f"entry-invariant-{RUN_A}.ok"


# --- deactivate ------------------------------------------------------------------------------------


def test_deactivate_removes_its_own_marker_and_breadcrumb_and_keeps_the_record(two_worktree_runs):
    w = two_worktree_runs
    save_worktree_session(w.b, pipeline_run_id=w.run_b, task_id="cd-902", hard_stop={"class": "retries"})
    p9(w.ctx, w.run_a, "cd-901")
    p9(w.ctx, w.run_b, "cd-902")
    crumb = w.ctx / "gates" / f"draft-pr-{w.run_b}.json"
    crumb.write_text("{}", encoding="utf-8")
    record = w.ctx / "prs" / f"draft-pr-{w.run_b}.md"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("PR: https://github.com/o/r/pull/1\n", encoding="utf-8")
    peer_summary(w.b)
    activate(w.b, "--deactivate")
    assert not marker(w.ctx, w.run_b).exists() and not crumb.exists()
    assert record.is_file()
    assert marker(w.ctx, w.run_a).is_file()


# --- carried findings at the review limit (acceptance 24) ------------------------------------------


def _plan(ctx: Path, name: str, run: str, ticket: str, carried: str) -> None:
    plans = ctx / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / name).write_text(
        f"# Plan\n\nRUN_BY_COMMAND: plan-create\nPipeline-Run: {run}\n**Ticket:** {ticket}\n\n"
        f"### Phase 1: x\n\n## Carried review findings\n\n{carried}\n",
        encoding="utf-8",
    )


def test_carried_section_reads_the_given_runs_plan(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    _plan(w.ctx, "a_cd-901.plan.md", w.run_a, "cd-901", "MF-1 phase 1")
    _plan(w.ctx, "b_cd-902.plan.md", w.run_b, "cd-902", "MF-7 phase 1")
    monkeypatch.chdir(w.primary)  # the root redirect names run B
    assert "MF-1" in artifact_requirements._carried_section(w.ctx, session=run_for(w.ctx, cwd=w.a))
    assert "MF-7" in artifact_requirements._carried_section(w.ctx, session=run_for(w.ctx, cwd=w.b))
    assert "MF-7" in artifact_requirements._carried_section(w.ctx)  # None keeps today's read
