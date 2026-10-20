"""gh-104: `headless.py next` gives the P3 re-review after P4, on the real registry.

Before the fix, the P3 gate checked only that a review existed, and the P4 gate passed on
the old plan-update marker. Thus the walk went past P4 to P5 with a blocking review.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import headless  # noqa: E402
from p3_loop import NR, RWC, at_limit, p3_repo, s1b, s2, s3  # noqa: E402


@pytest.fixture
def p3_run_repo(tmp_path, monkeypatch):
    return p3_repo(tmp_path / "r", monkeypatch, home=ROOT)


def walk(repo: Path) -> dict:
    return headless.walk(ROOT, repo, since="plan-review", through="plan-update", readonly=True)


def test_s2_walk_after_plan_update_gives_p3_with_next_cycle(p3_run_repo, capsys):
    s2(p3_run_repo)
    report = walk(p3_run_repo)
    assert report["done"] is False
    assert report["next"]["block"] == "P3"
    assert "cycle 2 of 5" in report["results"][-1]["output"]
    headless.print_report(report)
    text = capsys.readouterr().out
    assert "NEXT  P3 (plan-review)" in text and "cycle 2 of 5" in text


def test_s1b_walk_cycle_two_blocking_review_newer_than_plan_gives_p4(p3_run_repo):
    s1b(p3_run_repo)
    report = walk(p3_run_repo)
    assert report["next"] is not None and report["next"]["block"] == "P4"
    assert "/plan-update" in report["results"][-1]["output"]


def test_s3_walk_after_ready_to_execute_is_done_through_p4(p3_run_repo):
    s3(p3_run_repo)
    assert walk(p3_run_repo)["done"] is True


def test_s3n_walk_with_no_ticket_scope_is_done_through_p4(p3_run_repo):
    s3(p3_run_repo, task_id=None)
    assert walk(p3_run_repo)["done"] is True


@pytest.mark.parametrize("verdict, plan_at, needle", [
    pytest.param(RWC, 60, "Carried review findings", id="s4_walk_at_the_limit_gives_p4_carry"),
    pytest.param(RWC, 80, "Carried review findings", id="s4p_walk_at_the_limit_plan_newer_gives_p4_not_review_six"),
    pytest.param(NR, 60, "--hard-stop retries", id="s5_walk_at_the_limit_not_ready_gives_p4_hard_stop"),
])
def test_walk_at_the_limit_gives_p4(p3_run_repo, verdict, plan_at, needle):
    at_limit(p3_run_repo, verdict, plan_at)
    report = walk(p3_run_repo)
    assert report["next"]["block"] == "P4"
    assert needle in report["results"][-1]["output"]
