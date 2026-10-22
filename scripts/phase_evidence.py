#!/usr/bin/env python3
"""Red-before-green evidence of each E1 phase.

A phase has a red commit (`phase N: red ...`) that adds the failing test, then a green commit
(`phase N: ...`) that makes it pass. The agent records the check of the phase once:

    python3 phase_evidence.py record --phase 2 --run "uv run pytest tests/test_build.py -q"
    python3 phase_evidence.py record --phase 3 --none "the phase changes the README only"

The E1 gate runs each check in a clean checkout of the red commit (it must fail) and of the
green commit (it must pass). The author time of a commit proves nothing: commits one second
apart from a finished tree passed the check that this replaces. A phase with no code records
`--none` and a reason; the gate prints each one.

Results are kept in `.context/gates/phase-evidence-<run>.json` by (commit, check), so one pair
runs once. Rows go to `.context/implementation/<task_id>_phases.jsonl`; the last row of a phase counts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

TIMEOUT_SECONDS = 600
_RED = re.compile(r"(?i)phase[\s_-]*\d+\s*[:.\-–—]?\s*red\b")


def is_red(subject: str) -> bool:
    return bool(_RED.search(subject))


def _session(context_dir: Path, worktree: Path | None = None) -> dict:
    from artifact_requirements import current_session
    from pipeline_run_binding import run_for

    run = run_for(Path(context_dir), cwd=Path(worktree)) if worktree is not None else None
    return run or current_session(Path(context_dir)) or {}


def rows_path(context_dir: Path, session: dict) -> Path | None:
    task = re.sub(r"[\\/]", "-", str(session.get("task_id") or ""))
    return Path(context_dir) / "implementation" / f"{task}_phases.jsonl" if task else None


def record(context_dir: Path, phase: int, *, run: str | None = None, none: str | None = None,
           worktree: Path | None = None) -> list[str]:
    """Append the check of one phase. A check that proves nothing, or `--none` with no reason, is refused."""
    from findings_io import check_problem

    if (run is None) == (none is None):
        return ["phase-evidence: give --run <command> or --none <reason>"]
    if run is not None and (run.strip() == "manual" or check_problem(run)):
        return [f"phase-evidence: {check_problem(run) or 'manual is not a check'}"]
    if none is not None and not none.strip():
        return ["phase-evidence: --none needs a reason"]
    path = rows_path(context_dir, _session(context_dir, worktree))
    if path is None:
        return ["phase-evidence: the session has no task_id"]
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"phase": phase, "check": run.strip()} if run is not None else {"phase": phase, "none": (none or "").strip()}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return []


def recorded(context_dir: Path, session: dict) -> dict[int, dict]:
    """{phase: its last row}. A line that is not a row does not count."""
    path = rows_path(context_dir, session)
    rows: dict[int, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path else []
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("phase"), int):
            rows[row["phase"]] = row
    return rows


def _commits(worktree: Path, base: str) -> list[tuple[str, str]]:
    """(sha, subject) since the base, oldest first."""
    out = subprocess.run(["git", "log", "--reverse", "--topo-order", f"{base}..HEAD", "--format=%H %s"],
                         cwd=worktree, capture_output=True, text=True, check=False).stdout
    return [(sha, subject) for sha, _, subject in (line.partition(" ") for line in out.splitlines())]


def _phase_shas(commits: list[tuple[str, str]], phase: int) -> tuple[str | None, str | None]:
    """(the first red commit, the last green commit) of one phase. Reverts do not count."""
    from phase_ledger import phase_of, revert_depth

    red = green = None
    for sha, subject in commits:
        if phase_of(subject) != phase or revert_depth(subject):
            continue
        if is_red(subject):
            red = red or sha
        else:
            green = sha
    return red, green


def _run_at(worktree: Path, sha: str, check: str) -> int:
    """The exit code of `check` in a clean, detached checkout of `sha`."""
    tmp = Path(tempfile.mkdtemp(prefix="cadence-phase-"))
    tree = tmp / "tree"
    try:
        added = subprocess.run(["git", "worktree", "add", "-q", "--detach", str(tree), sha], cwd=worktree,
                               capture_output=True, text=True, check=False)
        if added.returncode != 0:
            return -1
        try:
            return subprocess.run(check, shell=True, cwd=tree, capture_output=True, text=True,  # noqa: S602 - the phase check
                                  timeout=TIMEOUT_SECONDS).returncode
        except subprocess.TimeoutExpired:
            return -2
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=worktree, capture_output=True, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


class _Cache:
    def __init__(self, context_dir: Path, run_id: str):
        self.path = Path(context_dir) / "gates" / f"phase-evidence-{run_id}.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def exit_code(self, worktree: Path, sha: str, check: str) -> int:
        key = f"{sha}|{check}"
        if key not in self.data:
            self.data[key] = _run_at(worktree, sha, check)
            self.dirty = True
        return int(self.data[key])

    def save(self) -> None:
        from gate_common import GATES_READONLY_ENV

        if self.dirty and os.environ.get(GATES_READONLY_ENV) != "1":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def problems(context_dir: Path, worktree: Path, phases: list[tuple[int, str]], base: str,
             session: dict | None = None) -> tuple[list[str], list[str]]:
    """(problems, notes) of the evidence of each phase. Call it when every phase is done."""
    session = session if session is not None else _session(context_dir, worktree)
    rows = recorded(context_dir, session)
    commits = _commits(worktree, base)
    cache = _Cache(context_dir, str(session.get("pipeline_run_id") or "run"))
    errors: list[str] = []
    notes: list[str] = []
    for number, title in phases:
        row = rows.get(number)
        if row is None:
            errors.append(f"phase {number} ({title}) has no check. Run `python3 \"$CADENCE_HOME/scripts/phase_evidence.py\" "
                          f"record --phase {number} --run \"<the test command of the phase>\"` "
                          "(or --none \"<reason>\" for a phase with no code)")
            continue
        if "none" in row:
            notes.append(f"phase {number} has no check: {row['none']}")
            continue
        red, green = _phase_shas(commits, number)
        if red is None:
            errors.append(f"phase {number} ({title}) has no red commit. Commit the failing test first, with a subject "
                          f"that starts with 'phase {number}: red'. A phase that adds no code, such as a test that pins "
                          f"behavior that already works, records `python3 \"$CADENCE_HOME/scripts/phase_evidence.py\" "
                          f"record --phase {number} --none \"<reason>\"` (live run y1)")
            continue
        if green is None:
            errors.append(f"phase {number} ({title}) has no green commit after its red commit")
            continue
        if cache.exit_code(worktree, red, row["check"]) == 0:
            errors.append(f"phase {number}: `{row['check']}` passes at its red commit {red[:12]}. The test must fail "
                          "before the code of the phase exists (red before green)")
        elif (code := cache.exit_code(worktree, green, row["check"])) != 0:
            errors.append(f"phase {number}: `{row['check']}` fails at its green commit {green[:12]} (exit {code})")
    cache.save()
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    from gate_common import ctx

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--phase", type=int, required=True)
    rec.add_argument("--run")
    rec.add_argument("--none")
    args = parser.parse_args(argv)
    errors = record(ctx(), args.phase, run=args.run, none=args.none, worktree=Path.cwd())
    for error in errors:
        print(f"FAIL  {error}", file=sys.stderr)
    if not errors:
        print(f"OK  phase {args.phase} recorded")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
