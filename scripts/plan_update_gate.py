#!/usr/bin/env python3
"""P4 — plan-update peer gate."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import BLOCKING_PLAN_VERDICTS, plan_review_step, require_artifact  # noqa: E402
from gate_common import cli, fail, ok, write_gate_breadcrumb  # noqa: E402

SLUG = "plan-update-gate"
_UPDATE_MARKERS = (
    "RUN_BY_COMMAND: plan-update",
    "RUN_BY_SKILL: plan-update",
)
_REVIEW_GLOB = "reviews/plan_*.md"
_ON_REVIEW_CHANGES_PHRASES = (
    "on_review_changes",
    "REQUEST CHANGES",
    *sorted(BLOCKING_PLAN_VERDICTS),
)


def _reviews_need_update(context_dir: Path) -> bool:
    for path in context_dir.glob(_REVIEW_GLOB):
        try:
            # PR #143 N-1: a byte that is not UTF-8 must not hide the verdict phrases or stop the gate.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(phrase in text for phrase in _ON_REVIEW_CHANGES_PHRASES):
            return True
    return False


def run(context_dir: Path, *, check: bool = False) -> int:
    # gh-104 D3: a blocking review that is not older than the plan opens P4, whatever marker the plan has.
    # ADR-0011: so does a newer plan whose `## Revision ledger (review <sha>)` for the review lacks a row for an open MF-n.
    step = plan_review_step(context_dir)
    if step is not None and step[0] == "P4":
        return fail(SLUG, [step[1]])
    if not _reviews_need_update(context_dir):
        if not check:
            write_gate_breadcrumb(
                SLUG, {"reason": "no_on_review_changes_evidence"}, context_dir=context_dir
            )
        return ok(SLUG)

    worktree = Path.cwd()
    if not require_artifact(
        context_dir,
        relative_globs=(_REVIEW_GLOB, "plans/*.plan.md"),
        worktree_globs=(worktree / ".cursor" / "plans" / "*.plan.md",),
        markers=_UPDATE_MARKERS,
        label="plan-update",
    ):
        if not check:
            write_gate_breadcrumb(
                SLUG, {"reason": "plan_update_marker_found"}, context_dir=context_dir
            )
        return ok(SLUG)

    return fail(
        SLUG,
        [
            "plan-update: review requests changes but no RUN_BY_COMMAND: plan-update "
            "marker found; run /plan-update then re-run this gate"
        ],
    )


if __name__ == "__main__":
    raise SystemExit(cli(run))
