"""gh-176: P9 fails when the plan changed after the READY TO EXECUTE review that passed it.

The review's `Last reviewed plan sha256:` line names the plan that the peer read. With no
such line, P9 compares the plan mtime with the review mtime. A tie passes.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import artifact_requirements as ar  # noqa: E402
from entry_invariant import check_execution_entry  # noqa: E402
from p3_loop import RTE, RWC, at, p3_loop, p3_repo, p3_review, plan  # noqa: E402

CHANGED = "the plan changed after its review; run /plan-review again"


@pytest.fixture
def lp(tmp_path, monkeypatch):
    return p3_loop(p3_repo(tmp_path / "r", monkeypatch), cycles=1)


def _pin(review: Path, sha: str, t: float) -> Path:
    """Add the `Last reviewed plan sha256:` line that commands/plan-review.md tells the peer to write."""
    review.write_text(review.read_text(encoding="utf-8") + f"Last reviewed plan sha256: {sha}\n", encoding="utf-8")
    return at(review, t)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(("plan_t", "review_t", "fails"), [(50, 60, False), (70, 60, True), (60, 60, False)],
                         ids=["review-after-plan", "plan-after-review", "equal"])
def test_no_sha_line_falls_back_to_the_mtime(lp, plan_t, review_t, fails):
    plan(lp, plan_t)
    p3_review(lp, review_t, RTE)
    errors = ar.plan_changed_after_review(lp.ctx, lp.session)
    assert bool(errors) is fails, errors
    assert all(CHANGED in e for e in errors)


def test_a_sha_line_that_names_the_plan_passes_when_the_plan_mtime_is_newer(lp):
    p = plan(lp, 50)
    _pin(p3_review(lp, 60, RTE), _sha(p), 60)
    at(p, 90)  # touched, same bytes
    assert ar.plan_changed_after_review(lp.ctx, lp.session) == []


def test_a_sha_line_that_names_an_older_plan_fails_when_the_review_is_newer(lp):
    p = plan(lp, 50)
    review = _pin(p3_review(lp, 60, RTE), _sha(p), 60)
    plan(lp, 55, updated=True)  # the bytes changed; the mtime is still older than the review
    [error] = ar.plan_changed_after_review(lp.ctx, lp.session)
    assert CHANGED in error and review.name in error


def test_a_blocking_newest_review_is_not_this_check(lp):
    plan(lp, 70)
    p3_review(lp, 60, RWC)
    assert ar.plan_changed_after_review(lp.ctx, lp.session) == []


def test_p9_names_the_plan_change(lp):
    plan(lp, 70)
    p3_review(lp, 60, RTE)
    assert any(CHANGED in e for e in check_execution_entry(lp.ctx, skill_root=ROOT, worktree=lp.repo))
    plan(lp, 50)
    assert not any(CHANGED in e for e in check_execution_entry(lp.ctx, skill_root=ROOT, worktree=lp.repo))
