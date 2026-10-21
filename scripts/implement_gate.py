#!/usr/bin/env python3
"""E1 — implement gate. Passes when every plan phase has a commit (phase_ledger).

It fails when the plan has three or more phases and two phase commits have the same
author time to the second: those commits were written in one shell, so they are
not phased work.
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
    if not errors:
        work = open_work(assess(context_dir, run_worktree(context_dir)))
        if work:
            errors.append(f"implement-gate: {work}")
    if errors:
        return fail(SLUG, errors)
    if not check:
        write_gate_breadcrumb(SLUG, context_dir=context_dir)
    return ok(SLUG)


if __name__ == "__main__":
    raise SystemExit(cli(run))
