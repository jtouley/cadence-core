#!/usr/bin/env python3
"""R4 final consolidate — Close pass then Delta pass.

A fix round is one `## Update` section of the PR review. The loop goes on until the gate passes: a
must-fix is fixed in the PR. It holds when a round changed nothing, and at the optional
`review_fix_rounds` cap of the config.
"""

from __future__ import annotations

import json
import os
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
STALL_HOLD = (
    "final-consolidate: fix round {rounds} changed nothing: HEAD and the open must-fix rows ({open}) are the same "
    "as after round {before}. Do not send the same fix round again. Record the stop: "
    'activate_session.py --hard-stop retries --reason "..."'
)
CAP_HOLD = (
    "final-consolidate: the fix loop is at review_fix_rounds ({budget}) of the config and is still open. "
    'Record the stop: activate_session.py --hard-stop retries --reason "..."'
)


def fix_rounds(text: str) -> int:
    """The fix rounds of a PR review: its `## Update` sections, one for each /pr-review-update."""
    return len(_UPDATE.findall(text))


def fix_round_budget(root: Path, workspace: Path | None = None) -> int | None:
    """`review_fix_rounds` of the resolved config: an optional cap on the fix rounds, else None (no cap).
    A layer can set a cap or lower it. A bad value raises."""
    import cadence_config

    return cadence_config.load(root, workspace).get("review_fix_rounds")


def stalled_since(context_dir: Path, rounds: int, head: str, open_ids: list[str]) -> int | None:
    """Record HEAD and the open must-fix rows at the first gate run of fix round `rounds` of the cwd's run. The
    round before it when it started from the same state, else None: a fix round that changed nothing ends the loop."""
    from pipeline_run_binding import run_for

    run_id = str((run_for(context_dir, cwd=Path.cwd()) or {}).get("pipeline_run_id") or "")
    if not run_id or not head:
        return None
    path = Path(context_dir) / "gates" / f"final-consolidate-rounds-{run_id}.json"
    readonly = os.environ.get("CADENCE_GATES_READONLY") == "1"  # the stop guard records nothing
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    seen = seen if isinstance(seen, dict) else {}
    state = {"head": head, "open": sorted(open_ids)}
    if not readonly and str(rounds) not in seen:
        seen[str(rounds)] = state
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen, indent=2), encoding="utf-8")
    return rounds - 1 if rounds >= 1 and seen.get(str(rounds - 1)) == state else None


def fix_loop_moves(context_dir: Path, run_id: str, root: Path) -> str | None:
    """Why a `retries` hard stop is early, else None: the PR fix loop of run `run_id` is still moving.

    The loop has no retry count. R4 holds it when a round changed nothing or at review_fix_rounds.
    Runs stopped themselves after three rounds that each changed HEAD and the open rows."""
    path = Path(context_dir) / "gates" / f"final-consolidate-rounds-{run_id}.json"
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rounds = sorted((int(k), v) for k, v in seen.items() if str(k).isdigit() and isinstance(v, dict)) \
        if isinstance(seen, dict) else []
    if not rounds:
        return None
    try:
        budget = fix_round_budget(root)
    except ValueError:
        return None
    last, state = rounds[-1]
    if budget is not None and last >= budget:
        return None
    before = dict(rounds).get(last - 1)
    if before is not None and before == state:
        return None
    return (f"the PR fix loop is still moving: round {last} is at HEAD {str(state.get('head'))[:12]} with open "
            f"{', '.join(state.get('open') or []) or 'none'}, and no round changed nothing. The loop has no retry "
            "count: send the next fix round. R4 (final_consolidate.py --check) says when it holds")


def consolidate(ledger: Path, *, context_dir: Path | None = None, cwd: Path | None = None,
                rounds: int = 0, budget: int | None = None) -> dict:
    """Judge the rows of the run of ``cwd`` when ``context_dir`` is given; else every row."""
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
            "budget_remaining_hint": None if budget is None else max(budget - rounds, 0),
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
    """(reviews a peer wrote, stage lines when none counts): name the writer stage or the
    missing join row, not the attest text."""
    import artifact_requirements

    kept = [p for p in reviews if accept(p)]
    if kept:
        return kept, []
    return kept, artifact_requirements.stage_rejections(context_dir, reviews, cwd=worktree, slug="final-consolidate")


def run_pr_review(context_dir: Path, worktree: Path) -> Path | None:
    """The review path of the run's PR when the run's draft-PR record names one, else None."""
    import artifact_requirements
    from gate_common import current_branch
    from pipeline_run_binding import run_for

    record = artifact_requirements.draft_pr_record(
        context_dir, run_for(context_dir, cwd=worktree), current_branch(worktree)
    )
    number = artifact_requirements.draft_pr_number(record) if record is not None else None
    return None if number is None else artifact_requirements.pr_review_file(context_dir, number)


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

    One compare for R4 and the R1 head check.
    """
    sha = last_reviewed_sha(review.read_text(encoding="utf-8", errors="replace"))
    if sha and head.startswith(sha):
        return None
    return f"cites {sha}" if sha else "has no `Last reviewed commit:` line"


def last_reviewed_sha(text: str) -> str | None:
    """The last `Last reviewed commit: <sha>` value in any Markdown form (labeled_lines)."""
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
    open_ids = result["close_pass"]["open_must_fix"]
    problems = [f"final-consolidate: open must-fix {fid}" for fid in open_ids]
    problems += head_review_problems(ctx(), run_worktree(ctx()))
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    before = stalled_since(ctx(), rounds, head, open_ids) if problems else None
    if before is not None:
        problems.append(STALL_HOLD.format(rounds=rounds, open=", ".join(open_ids) or "none", before=before))
    elif problems and budget is not None and rounds >= budget:
        problems.append(CAP_HOLD.format(budget=budget))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
