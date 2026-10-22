#!/usr/bin/env python3
"""Promote fixed → verified only after the finding's check command passes at HEAD.

The check is a real test or script, `fixed_in` is HEAD or an ancestor of HEAD, and a
`manual` check needs the stamp of `findings_io.py set-status --human --by <name>`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from findings_io import (  # noqa: E402
    HUMAN_FIELDS, check_problem, commit_on_branch, ledger_lock, load_findings, missing_script, rewrite_findings,
)


def verify_finding(record: dict, *, dry_run: bool = False) -> dict:
    """The row promoted to verified, with the HEAD of the cwd that it was checked at. A dry run skips the check."""
    if record.get("status") != "fixed":
        raise ValueError(f"can only verify status=fixed (got {record.get('status')})")
    check = record.get("check") or "manual"
    problem = check_problem(check)
    if problem:
        raise ValueError(problem)
    if not record.get("fixed_in"):
        raise ValueError(f"{record.get('id')}: a fixed row needs fixed_in (findings_io.py set-status --fixed-in <sha>)")
    commit_on_branch(str(record["fixed_in"]))
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if check == "manual":
        if not all(record.get(k) for k in HUMAN_FIELDS):
            raise ValueError("a manual check needs findings_io.py set-status --human --by <name>")
        exit_code = 0
    elif dry_run:
        return dict(record)
    else:
        env = {**os.environ, "CADENCE_HOME": os.environ.get("CADENCE_HOME") or str(Path(__file__).resolve().parents[1])}
        proc = subprocess.run(check, shell=True, capture_output=True, text=True, env=env)
        exit_code = proc.returncode
        if exit_code != 0:
            raise RuntimeError(
                f"check failed ({exit_code}): {check}\n{proc.stdout}\n{proc.stderr}"
            )
    out = dict(record)
    out["status"] = "verified"
    out["verified_at"] = datetime.now(timezone.utc).isoformat()
    out["verification_exit_code"] = exit_code
    out["verified_at_sha"] = head
    return out


def supersede_finding(record: dict, passed: str) -> dict:
    """The row promoted to verified by a later pass of its own gate.

    The gate is the row's check, and it has just passed, so this runs nothing. Only an
    open or fixed row with a command check qualifies. A manual check stays with a human.
    """
    if record.get("status") not in ("open", "fixed"):
        raise ValueError(f"can only supersede status=open|fixed (got {record.get('status')})")
    if record.get("check") in (None, "", "manual"):
        raise ValueError("a manual check cannot be superseded")
    out = dict(record)
    out["status"] = "verified"
    out["note"] = f"superseded by {passed}"
    out["verified_at"] = datetime.now(timezone.utc).isoformat()
    out["verification_exit_code"] = 0
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify fixed findings → verified")
    parser.add_argument("--ledger", required=True, help="Path to findings jsonl")
    parser.add_argument("--id", help="Verify a single finding id")
    parser.add_argument("--dry-run", action="store_true", help="Name the rows a run would verify. Writes nothing.")
    args = parser.parse_args(argv)
    path = Path(args.ledger)
    rows = load_findings(path)
    selected = 0
    new_rows = []
    errors = []  # One row that cannot be verified does not stop the others
    for row in rows:
        if row.get("status") != "fixed" or (args.id and row["id"] != args.id):
            new_rows.append(row)
            continue
        try:
            new_rows.append(verify_finding(row, dry_run=args.dry_run))
            selected += 1
        except (ValueError, RuntimeError) as exc:
            missing = missing_script(row.get("check"), Path.cwd())
            hint = (f" The check names {missing}, which is not a file. The {row.get('stage') or 'review'} peer that wrote "
                    f"the row replaces it: findings_io.py set-check --id {row.get('id')} --check \"<a command that runs>\""
                    if missing else "")
            errors.append(f"error: {row.get('id')}: {exc}{hint}")
            new_rows.append(row)
    for line in errors:
        print(line, file=sys.stderr)
    if args.dry_run:
        print(json.dumps({"would_verify": selected, "verified": 0, "ledger": str(path)}))
    else:
        # The checks run without the lock. Under it, only the rows that this run verified change.
        done = {r["id"]: r for r in new_rows if r.get("status") == "verified"}
        with ledger_lock(path):
            rewrite_findings(path, [done.get(r["id"], r) if r.get("status") == "fixed" else r
                                    for r in load_findings(path)])
        print(json.dumps({"verified": selected, "ledger": str(path), "errors": len(errors)}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
