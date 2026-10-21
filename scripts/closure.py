#!/usr/bin/env python3
"""R5 closure — refuse unless every must-fix is terminal.

A pass writes `gates/closure-<run id>.json` with HEAD. `gh pr comment` and
`gh pr review` wait for it (hooks/pr_guard.py, shared/skill_gate.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from findings_io import blocks_closure, ledger_scope, load_findings, scope_summary  # noqa: E402


def closure_errors(records: list[dict]) -> list[str]:
    errors: list[str] = []
    for row in records:
        if blocks_closure(row):
            errors.append(f"{row.get('id')}: must-fix status={row.get('status')!r} not terminal")
    return errors


def manually_confirmed(records: list[dict]) -> list[dict]:
    """Each row that a person confirmed with `set-status --human`. R5 names them, so a person sees them."""
    return [{k: row.get(k) for k in ("id", "confirmed_by", "confirmed_at")} for row in records if row.get("human_confirmed")]


def closure_problem(context_dir: Path, session: dict | None, head: str) -> str | None:
    """Why R5 has not passed for the run of ``session`` at ``head``, else None."""
    run_id = str((session or {}).get("pipeline_run_id") or "")
    if not run_id:
        return "no Cadence run in this worktree"
    try:
        data = json.loads((Path(context_dir) / "gates" / f"closure-{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("pipeline_run_id") != run_id:
        return f"no R5 closure breadcrumb for run {run_id}"
    if not head or data.get("head") != head:
        return f"R5 passed at {data.get('head')}, HEAD is {head}: run closure.py again"
    return None


def _write_breadcrumb(context_dir: Path) -> None:
    """The breadcrumb of the cwd's run at HEAD. With no run or no HEAD, there is nothing to open."""
    import subprocess

    from gate_common import write_gate_breadcrumb
    from pipeline_run_binding import run_for

    run_id = str((run_for(context_dir, cwd=Path.cwd()) or {}).get("pipeline_run_id") or "")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if run_id and head:
        write_gate_breadcrumb(f"closure-{run_id}", {"pipeline_run_id": run_id, "head": head}, context_dir=context_dir)


def main() -> int:
    from gate_common import ctx, fail, ledger_gate_args

    args, ledger = ledger_gate_args("Cadence R5 closure gate")
    scope = ledger_scope(load_findings(ledger), ctx(), cwd=Path.cwd())
    summary = {**scope_summary(scope, blocks_closure), "manually_confirmed": manually_confirmed(scope["rows"])}
    errors = closure_errors(scope["rows"])
    stream = sys.stderr if errors else sys.stdout
    for row in summary["manually_confirmed"]:
        print(f"MANUAL  {row['id']} confirmed by {row['confirmed_by']} at {row['confirmed_at']}", file=stream)
    if errors:
        code = fail("closure", errors)
        print(json.dumps(summary), file=sys.stderr)
        return code
    must_fix = sum(1 for r in scope["rows"] if r.get("severity") == "must-fix")
    if not args.check:
        _write_breadcrumb(ctx())
    print(json.dumps({"ok": True, "must_fix": must_fix, **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
