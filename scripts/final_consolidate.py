#!/usr/bin/env python3
"""R4 final consolidate — Close pass then Delta pass (fix-loop budget: `review_fix_rounds`).

A fix round is one `## Update` section of the PR review. At the budget, a loop that is still
open holds with the ADR-0009 text (#182, ADR-0015).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from findings_io import is_open_must_fix, ledger_scope, load_findings, scope_summary  # noqa: E402
from labeled_lines import labeled_values  # noqa: E402

_UPDATE = re.compile(r"^##[ \t]+Update\b", re.M)
BUDGET_HOLD = (
    "final-consolidate: the review fix loop is at its budget: {rounds} fix rounds (`## Update` sections) of "
    "{budget} (review_fix_rounds). Do not start another fix round. ADR-0009: a MERGE-READY verdict with no open "
    "must-fix ends the loop, and a nit goes to the tracker, not into a fix commit. Defer each open must-fix: add its "
    "row under `## Review deferrals` in .context/deferrals/<task>_deferral-register.md with the ticket, pass "
    "deferral_adjudication_check.py, then findings_io.py set-status --id <id> --status deferred --ticket <n> "
    '(ADR-0012). Or stop and tell the user (activate_session.py --hard-stop retries --reason "...")'
)


def fix_rounds(text: str) -> int:
    """The fix rounds of a PR review: its `## Update` sections, one for each /pr-review-update (#182)."""
    return len(_UPDATE.findall(text))


def fix_round_budget(root: Path, workspace: Path | None = None) -> int:
    """`review_fix_rounds` of the resolved config: a layer can only lower it (ADR-0015). A bad value raises."""
    import cadence_config

    return cadence_config.load(root, workspace).get("review_fix_rounds")


def consolidate(ledger: Path, *, context_dir: Path | None = None, cwd: Path | None = None,
                rounds: int = 0, budget: int = 2) -> dict:
    """Judge the rows of the run of ``cwd`` when ``context_dir`` is given (cd-49); else every row."""
    rows = load_findings(ledger)
    scope = ledger_scope(rows, context_dir, cwd=cwd) if context_dir is not None else None
    judged = scope["rows"] if scope else rows
    return {
        "close_pass": {
            "open_must_fix": [r["id"] for r in judged if is_open_must_fix(r)],
            "fixed_awaiting_verify": [r["id"] for r in judged if r.get("status") == "fixed"],
        },
        "delta_pass": {
            "note": "Re-scan for regressions after close-pass fixes" if ledger.is_file() else "no findings ledger",
            "budget_remaining_hint": max(budget - rounds, 0),
        },
        "fix_loop_budget": budget,
        "fix_rounds": rounds,
        "ledger": str(ledger),
        **(scope_summary(scope, is_open_must_fix) if scope else {}),
    }


REVIEW_GLOBS = ("prs/reviews/*.md", "reviews/pr_*.md")
REVIEW_MARKERS = ("RUN_BY_COMMAND: pr-review-local", "RUN_BY_COMMAND: pr-review-update", "RUN_BY_SKILL: pr-review")


def head_review_problems(context_dir: Path, worktree: Path) -> list[str]:
    """R4 needs a PR review of the current head: review -> fix -> re-review.

    Without this check, fixes after R1 would close with no review of the fixed head.
    """
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True).stdout.strip()
    if not head:
        return ["final-consolidate: no git HEAD in the run's worktree"]
    reviews = marked_reviews(Path(context_dir))
    import artifact_requirements

    accept = artifact_requirements.peer_written_filter(Path(context_dir), cwd=Path(worktree), slug="final-consolidate")
    if getattr(accept, "reason", None):
        return [f"final-consolidate: {accept.reason}"]
    own = run_pr_review(Path(context_dir), Path(worktree))
    if own is not None:
        return _own_review_problems(Path(context_dir), own, reviews, accept, head, worktree=Path(worktree))
    if accept is not None:
        # Claude Code: only a review a peer wrote counts (the orchestrator could write one).
        reviews, lines = _peer_reviews(Path(context_dir), Path(worktree), reviews, accept)
        if lines:
            return lines
    if not reviews:
        return ["final-consolidate: no PR review written by a review peer (run R1 /pr-review-local)"]
    for review in reviews:
        sha = last_reviewed_sha(review.read_text(encoding="utf-8", errors="replace"))
        if sha and head.startswith(sha):
            return []
    from pipeline_run_binding import other_workspace_hint

    newest = max(reviews, key=lambda p: p.stat().st_mtime)
    read = last_reviewed_sha(newest.read_text(encoding="utf-8", errors="replace"))
    rel = newest.relative_to(Path(context_dir)).as_posix()
    found = f"its last reviewed commit is {read}" if read else "no reviewed-commit line"
    hint = other_workspace_hint(Path(context_dir), Path(worktree))
    return [
        f"final-consolidate: no PR review covers HEAD {head[:7]}; newest review {rel}: {found}. "
        f"Run /pr-review-update on the PR to review the commits up to {head[:7]}"
        + (f"; {hint}" if hint else "")
    ]


def marked_reviews(context_dir: Path) -> list[Path]:
    """The PR reviews under `context_dir` that carry a RUN_BY marker of a review command."""
    return [
        p for g in REVIEW_GLOBS for p in context_dir.glob(g)
        if p.is_file() and any(m in p.read_text(encoding="utf-8", errors="replace") for m in REVIEW_MARKERS)
    ]


def review_rounds(context_dir: Path, worktree: Path) -> int:
    """The fix rounds of the review R4 reads: the run's own PR review, else the newest marked review."""
    own = run_pr_review(context_dir, worktree)
    reviews = [own] if own is not None and own.is_file() else marked_reviews(context_dir)
    if not reviews:
        return 0
    return fix_rounds(max(reviews, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8", errors="replace"))


def _peer_reviews(context_dir: Path, worktree: Path, reviews: list[Path], accept) -> tuple[list[Path], list[str]]:
    """(reviews a peer wrote, gh-66 stage lines when none counts): name the writer stage or the
    missing join row, not the attest text."""
    import artifact_requirements

    kept = [p for p in reviews if accept(p)]
    if kept:
        return kept, []
    return kept, artifact_requirements.stage_rejections(context_dir, reviews, cwd=worktree, slug="final-consolidate")


def run_pr_review(context_dir: Path, worktree: Path) -> Path | None:
    """The review path of the run's PR when the run's draft-PR record names one (cd-76 D8), else None."""
    import artifact_requirements
    from gate_common import current_branch
    from pipeline_run_binding import run_for

    record = artifact_requirements.draft_pr_record(
        context_dir, run_for(context_dir, cwd=worktree), current_branch(worktree)
    )
    number = artifact_requirements.draft_pr_number(record) if record is not None else None
    return None if number is None else context_dir / "prs" / "reviews" / f"PR_REVIEW_#{number}.md"


def _own_review_problems(
    context_dir: Path, own: Path, reviews: list[Path], accept, head: str, *, worktree: Path
) -> list[str]:
    """R4 for a run with a PR: only the review of that PR counts, with no fallback (R3-D8b)."""
    rel = own.relative_to(context_dir).as_posix()
    if not own.is_file():
        return [f"final-consolidate: no review of this run's PR at {rel}; run R1 /pr-review-local on the PR"]
    if own not in reviews:
        return [f"final-consolidate: {rel} has no RUN_BY marker of a PR review ({', '.join(REVIEW_MARKERS)})"]
    if accept is not None and not accept(own):
        import artifact_requirements

        lines = artifact_requirements.stage_rejections(context_dir, [own], cwd=worktree, slug="final-consolidate")
        return lines or [f"final-consolidate: not peer-written (ignored): {rel}; "
                         "write the review with the Write or Edit tool from the peer"]
    found = head_coverage(own, head)
    if found is None:
        return []
    return [
        f"final-consolidate: no PR review covers HEAD {head[:7]}; review {rel} {found}. "
        f"Run /pr-review-update on the PR to review the commits up to {head[:7]}"
    ]


def head_coverage(review: Path, head: str) -> str | None:
    """None when the review's `Last reviewed commit` is `head`, else what it has instead.

    One compare for R4 and the R1 head check (PR #168 R1 N-18).
    """
    sha = last_reviewed_sha(review.read_text(encoding="utf-8", errors="replace"))
    if sha and head.startswith(sha):
        return None
    return f"cites {sha}" if sha else "has no `Last reviewed commit:` line"


def last_reviewed_sha(text: str) -> str | None:
    """The last `Last reviewed commit: <sha>` value in any Markdown form (labeled_lines, cd-47 D3)."""
    values = labeled_values(text, ("last reviewed commit",), r"[0-9a-f]{7,40}")
    return values[-1] if values else None


def main() -> int:
    from gate_common import ctx, ledger_gate_args

    from implement_gate import run_worktree

    args, ledger = ledger_gate_args("Cadence R4 final consolidate")
    try:
        budget = fix_round_budget(_SCRIPTS.parent, run_worktree(ctx()))
    except ValueError as exc:  # a bad budget fails closed
        print(f"final-consolidate: bad config: {exc}", file=sys.stderr)
        return 1
    rounds = review_rounds(ctx(), run_worktree(ctx()))
    # a missing ledger has no rows (R1 N-1)
    result = consolidate(ledger, context_dir=ctx(), cwd=Path.cwd(), rounds=rounds, budget=budget)
    print(json.dumps(result, indent=2))
    if not args.check:
        return 0
    problems = [f"final-consolidate: open must-fix {fid}" for fid in result["close_pass"]["open_must_fix"]]
    problems += head_review_problems(ctx(), run_worktree(ctx()))
    if problems and rounds >= budget:
        problems.append(BUDGET_HOLD.format(rounds=rounds, budget=budget))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
