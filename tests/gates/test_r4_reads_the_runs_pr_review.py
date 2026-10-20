"""cd-76 Phase 6: R4 checks the review of the run's own PR, named by the run's draft-PR record.

With a record that names PR n, R4 checks prs/reviews/PR_REVIEW_#<n>.md only: a newer review
of another PR does not count, a missing one is named, and a dropped (not peer-written) one is
named as such. With no record, or a record with no PR URL, R4 keeps its e5c3a92 rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import head_sha, save_worktree_session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import artifact_requirements  # noqa: E402
import final_consolidate  # noqa: E402

MARK = "RUN_BY_COMMAND: pr-review-local\n"


@pytest.fixture(autouse=True)
def _no_peer_filter(two_worktree_runs, monkeypatch):
    """No peer filter unless a test sets one: runs on another host, and no Claude Code env."""
    w = two_worktree_runs
    save_worktree_session(w.a, pipeline_run_id=w.run_a, task_id="cd-901", host="other")
    save_worktree_session(w.b, pipeline_run_id=w.run_b, task_id="cd-902", host="other")
    monkeypatch.delenv("CLAUDECODE", raising=False)


def review(ctx: Path, n: int, sha: str) -> Path:
    path = ctx / "prs" / "reviews" / f"PR_REVIEW_#{n}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MARK + f"Last reviewed commit: {sha}\n", encoding="utf-8")
    return path


def record(ctx: Path, name: str, body: str) -> None:
    path = ctx / "prs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("RUN_BY_COMMAND: commit-push-pr\n" + body, encoding="utf-8")


def problems(ctx: Path, tree: Path) -> list[str]:
    return final_consolidate.head_review_problems(ctx, tree)


def test_r4_takes_the_review_of_the_runs_pr_not_the_newest(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.a)
    record(w.ctx, f"draft-pr-{w.run_a}.md", "PR: https://github.com/o/r/pull/41\nBranch: feature/wa\n")
    review(w.ctx, 41, head_sha(w.a))
    review(w.ctx, 42, "0000000")
    assert problems(w.ctx, w.a) == []
    review(w.ctx, 41, "0000000")
    review(w.ctx, 42, head_sha(w.a))  # another PR's review that covers HEAD does not count
    out = problems(w.ctx, w.a)
    assert out and "PR_REVIEW_#41.md" in out[0]


def test_r4_names_a_missing_review_of_the_runs_pr(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.a)
    record(w.ctx, f"draft-pr-{w.run_a}.md", "PR: https://github.com/o/r/pull/41\nBranch: feature/wa\n")
    review(w.ctx, 42, head_sha(w.a))
    out = problems(w.ctx, w.a)
    assert out and "prs/reviews/PR_REVIEW_#41.md" in out[0]


def test_r4_names_the_runs_review_when_the_peer_filter_drops_it(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.a)
    record(w.ctx, f"draft-pr-{w.run_a}.md", "PR: https://github.com/o/r/pull/41\nBranch: feature/wa\n")
    review(w.ctx, 41, head_sha(w.a))
    review(w.ctx, 42, head_sha(w.a))

    def only_42(*_a, **_k):
        return lambda path: path.name == "PR_REVIEW_#42.md"

    monkeypatch.setattr(artifact_requirements, "peer_written_filter", only_42)
    out = problems(w.ctx, w.a)
    assert out and "not peer-written (ignored): prs/reviews/PR_REVIEW_#41.md" in out[0]
    assert "#42" not in out[0]


def test_r4_with_no_record_keeps_todays_rule(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.a)
    review(w.ctx, 42, head_sha(w.a))
    assert problems(w.ctx, w.a) == []


def test_r4_with_a_record_that_names_no_pr_keeps_todays_rule(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.a)
    record(w.ctx, f"draft-pr-{w.run_a}.md", "Branch: feature/wa\n")
    review(w.ctx, 42, head_sha(w.a))
    assert problems(w.ctx, w.a) == []


def test_r4_reads_the_branch_of_the_runs_worktree(two_worktree_runs, monkeypatch):
    """N-1: the process cwd is the primary checkout (on main); the legacy record names the run's branch."""
    w = two_worktree_runs
    monkeypatch.chdir(w.primary)
    record(w.ctx, "draft-pr.md", "PR: https://github.com/o/r/pull/41\nBranch: feature/wa\n")
    review(w.ctx, 41, "0000000")
    review(w.ctx, 42, head_sha(w.a))
    out = problems(w.ctx, w.a)
    assert out and "PR_REVIEW_#41.md" in out[0]


def test_r4_names_a_runs_review_with_no_run_by_marker(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.a)
    record(w.ctx, f"draft-pr-{w.run_a}.md", "PR: https://github.com/o/r/pull/41\nBranch: feature/wa\n")
    path = review(w.ctx, 41, head_sha(w.a))
    path.write_text(f"Last reviewed commit: {head_sha(w.a)}\n", encoding="utf-8")
    assert problems(w.ctx, w.a) == [
        "final-consolidate: prs/reviews/PR_REVIEW_#41.md has no RUN_BY marker of a PR review "
        f"({', '.join(final_consolidate.REVIEW_MARKERS)})"
    ]
