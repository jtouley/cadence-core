#!/usr/bin/env python3
"""P9 entry invariant — require planning artifacts + write entry-invariant.ok."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import (  # noqa: E402
    check_required_before_execution,
    clear_entry_invariant_ok,
    plan_changed_after_review,
    write_entry_invariant_ok,
)
from acceptance import pin as pin_acceptance  # noqa: E402
from acceptance import problems as acceptance_problems  # noqa: E402
from gate_common import ctx, fail, ok  # noqa: E402
from load_stages import SKILL_ROOT, load_registry, stage_slugs, stages_by_phase  # noqa: E402
from pack_loader import gate_errors, phase_packs  # noqa: E402
from pipeline_run_binding import run_for  # noqa: E402

REQUIRED_BEFORE_EXECUTION = (
    "activate-session",
    "plan-create",
    "plan-review",
    "adversarial-review",
    "test-benchmark-plan",
    "deferral-register",
)


def _rows_on(root: Path, wt: Path, errors: list[str]) -> set[str] | None:
    """The slugs of the rows that are on, so P9 skips the artifact of a row whose pack is off.
    An unknown pack name is recorded, and None then requires every artifact (fail closed)."""
    try:
        return set(stage_slugs(root, wt))
    except ValueError as exc:
        errors.append(f"packs: {exc}")
        return None


def check_execution_entry(
    context_dir: Path,
    *,
    skill_root: Path | None = None,
    worktree: Path | None = None,
) -> list[str]:
    root = Path(skill_root) if skill_root is not None else SKILL_ROOT
    errors: list[str] = []
    wt = Path(worktree) if worktree is not None else Path.cwd()

    slugs = {r["slug"] for r in load_registry(root)["stages"]}
    for req in REQUIRED_BEFORE_EXECUTION:
        if req not in slugs:
            errors.append(f"registry missing required slug: {req}")

    by_phase = stages_by_phase(root)
    if "execution" not in by_phase:
        errors.append("registry missing execution phase")

    errors.extend(check_required_before_execution(context_dir, worktree=wt, on=_rows_on(root, wt, errors)))
    errors.extend(plan_changed_after_review(context_dir, session=run_for(context_dir, cwd=wt)))
    errors.extend(acceptance_problems(context_dir, worktree=wt))  # P9 pins these checks; R5 runs them

    # The planning gate of each pack that `packs` of the resolved config turns on.
    packs, load_errors = phase_packs(root, "planning", wt)
    errors.extend(load_errors + gate_errors(packs, "planning", wt))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Cadence entry invariant (P9)")
    parser.add_argument(
        "--phase",
        choices=("execution",),
        default="execution",
        help="Invariant phase (execution clears E1)",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    context_dir = ctx()
    if args.phase != "execution":
        print(f"ERROR: unsupported phase {args.phase}", file=sys.stderr)
        return 1
    # P9 acts on the run of its own worktree only, never the root redirect.
    run = run_for(context_dir)
    if run is None:
        return fail("entry-invariant", ["entry-invariant: no Cadence run in this worktree; run P9 from the run's worktree"])
    run_id = str(run.get("pipeline_run_id") or "")
    errors = check_execution_entry(context_dir)
    if errors:
        clear_entry_invariant_ok(context_dir, run_id)
        return fail("entry-invariant", errors)

    if args.check:
        return ok("entry-invariant")

    session = run_for(context_dir)
    if session is None:
        clear_entry_invariant_ok(context_dir, run_id)
        return fail("entry-invariant", ["entry-invariant: session vanished after check"])
    pin_errors = pin_acceptance(context_dir, Path.cwd(), session)
    if pin_errors:
        clear_entry_invariant_ok(context_dir, run_id)
        return fail("entry-invariant", pin_errors)
    write_entry_invariant_ok(
        context_dir,
        pipeline_run_id=str(session.get("pipeline_run_id") or ""),
        task_id=str(session.get("task_id") or ""),
        written_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return ok("entry-invariant")


if __name__ == "__main__":
    raise SystemExit(main())
