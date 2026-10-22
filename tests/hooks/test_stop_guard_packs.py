"""The stop guard walk never opens a row whose pack is off (default and full config)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import enable_all_packs, git_repo, review_cycles, stop_decision, stop_guard_module

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from adversarial_loop import Loop  # noqa: E402
from test_stop_guard_reads_progress import GUARD, MAIN, real_run  # noqa: E402

BLOCKING = "## Blocking issues\n\n### B-1. one\n"


def _guard():
    return stop_guard_module("adr7_stop_guard")


def test_default_config_past_p9_opens_e4_not_e2(tmp_path, run_hook):
    repo = real_run(tmp_path, marker=True)
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert code == 0 and stop_decision("claude", out) == "block"
    assert "E4 (draft-pr)" in out
    assert "impl-artifact" not in out and "architecture-review-impl" not in out


def test_full_config_past_p9_opens_e2(tmp_path, run_hook):
    repo = real_run(tmp_path, marker=True)
    enable_all_packs(repo)
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert stop_decision("claude", out) == "block"
    assert "E2 (impl-artifact)" in out


def test_default_config_before_p9_holds_on_a_core_block(tmp_path, run_hook):
    repo = real_run(tmp_path, marker=False)
    code, out, _ = run_hook(GUARD, repo, MAIN["claude"], "claude")
    assert stop_decision("claude", out) == "block"
    assert "P1 (plan-create)" in out


@pytest.mark.parametrize("full, nxt", [(False, "P9 (entry-invariant)"), (True, "P5 (architecture-review-plan)")])
def test_a_ready_plan_names_the_next_row_that_is_on(tmp_path, monkeypatch, full, nxt):
    ws = tmp_path / "ws"
    ws.mkdir()
    if full:
        enable_all_packs(ws)
    monkeypatch.chdir(ws)
    assert nxt in _guard().planning_continuation("plan-review", "READY TO EXECUTE")


@pytest.mark.parametrize("full", [False, True])
def test_a_p6_request_changes_holds_only_when_spec_review_is_on(tmp_path, monkeypatch, full):
    repo = git_repo(tmp_path / "r")
    if full:
        enable_all_packs(repo)
    review_cycles(repo)  # below the P6 limit, a REQUEST CHANGES report holds for another round
    monkeypatch.chdir(repo)
    lp = Loop(repo)
    path = lp.ctx / "reviews" / f"plan_{lp.slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {lp.run}\n**Ticket:** gh-1\n\nVerdict: READY TO EXECUTE\n",
                    encoding="utf-8")
    lp.attest(path, "reviewer-0")
    os.utime(path, (50, 50))
    os.utime(lp.plan_path, (60, 60))
    report = lp.report("REQUEST CHANGES", round_n=1, agent="peer-1", body=BLOCKING)
    os.utime(report, (100, 100))
    _decision, message, _here, _s = _guard()._judge_one(lp.session, lp.ctx, repo, corrupt=False, budget=30.0)
    assert ("cycle 1 of 3" in message) is full
