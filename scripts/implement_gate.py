#!/usr/bin/env python3
"""E1 — implement gate. Passes when every plan phase has a green commit (phase_ledger),
and the check of each phase fails at its red commit and passes at its green commit
(phase_evidence). Then it runs the acceptance checks at HEAD (E1.5), so a deliverable
that does not meet the request fails here, before the draft PR.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import entry_invariant_matches, entry_invariant_ok_path, require_active_session  # noqa: E402
from gate_common import cli, fail, ok, write_gate_breadcrumb  # noqa: E402
from phase_ledger import assess, open_work  # noqa: E402
from pipeline_run_binding import run_for, worktree_run_session  # noqa: E402

SLUG = "implement-gate"


def run_worktree(context_dir: Path) -> Path:
    """The run's worktree: this worktree's session workspace when it exists, else the cwd.

    Never another worktree's run.
    """
    workspace = (worktree_run_session(context_dir) or {}).get("workspace")
    if isinstance(workspace, str) and Path(workspace).is_dir():
        return Path(workspace)
    return Path.cwd()


def run(context_dir: Path, *, check: bool = False) -> int:
    errors = list(require_active_session(context_dir, label="implement-gate"))
    # The marker of this worktree's run only.
    session = run_for(context_dir)
    run_id = (session or {}).get("pipeline_run_id")
    invariant = entry_invariant_ok_path(context_dir, run_id)
    if not run_id:
        errors.append("implement-gate: no Cadence run in this worktree; run P9 and E1 from the run's worktree")
    elif not entry_invariant_matches(context_dir, session):
        errors.append(
            f"implement-gate: {invariant} missing; "
            "run entry_invariant.py (P9) to clear all pre-execution gates first"
        )
    notes: list[str] = []
    if not errors:
        worktree = run_worktree(context_dir)
        state = assess(context_dir, worktree)
        work = open_work(state)
        if work:
            errors.append(f"implement-gate: {work}")
        elif state["phases"]:  # each phase fails at its red commit and passes at its green one
            from phase_evidence import problems

            found, notes = problems(context_dir, worktree, state["phases"], state["base"], session)
            errors.extend(f"implement-gate: {e}" for e in found)
        if not errors:  # E1.5: the deliverable meets the request before the draft PR; R5 runs them again
            from acceptance import verify

            errors.extend(f"implement-gate: {e}" for e in verify(context_dir, worktree, session))
    if errors:
        return fail(SLUG, errors)
    if not check:
        write_gate_breadcrumb(SLUG, context_dir=context_dir)
    return ok(SLUG, "; ".join(notes)) if notes else ok(SLUG)


if __name__ == "__main__":
    raise SystemExit(cli(run))
