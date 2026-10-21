#!/usr/bin/env python3
"""Findings ledger I/O — append/validate jsonl records (Gap Spec schema).

Models may set status to open|fixed|deferred|spec_gap only.
status=verified is forbidden here — only ledger_verify.py may set it.
A written row also needs a real `check` and, when fixed, `fixed_in` (gh-175).

The ledger is protected by the edit hooks, so agents change it through this CLI:

    python3 scripts/findings_io.py append --json '{"id": "gh-1-F01", ...}'
    python3 scripts/findings_io.py set-status --id gh-1-F01 --status fixed --fixed-in <sha>
    python3 scripts/findings_io.py set-status --id gh-1-F03 --status fixed --fixed-in <sha> --human --by "<name>"
    python3 scripts/findings_io.py set-status --id gh-1-F02 --status deferred --ticket "#42"

`--ledger` defaults to findings.jsonl in the canonical .context. In the examples, `gh-1` is the
task id. On the canonical ledger, `append` takes only an id of the cwd's run task, or of a
known task when the cwd has no run (cd-49).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

REQUIRED = ("id", "stage", "rule_ref", "class", "severity", "location", "evidence", "status")
CLASSES = frozenset({"bug", "spec-gap", "fast-follow"})
SEVERITIES = frozenset({"must-fix", "nit"})
STATUSES_WRITABLE = frozenset({"open", "fixed", "deferred", "spec_gap"})
STATUSES_ALL = STATUSES_WRITABLE | {"verified"}
# `<task prefix>-F<n>`, or `<task prefix>-MF-<n>` for the R1 row of a review's MF-n item (gh-174).
ID_RE = re.compile(r"^[A-Za-z0-9_-]+-(?:F|MF-)\d+$")

TERMINAL_MUST_FIX = frozenset({"verified", "deferred", "spec_gap"})

# gh-175: a check closes a finding only when it runs a test or a script. `manual` is the human path.
NO_OP_COMMANDS = frozenset({"true", ":", "echo", "printf", "exit"})
_SEGMENTS = re.compile(r"&&|\|\||[;|\n]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
RUNNERS = ("pytest, python, make, tox, nox, uv run, npm/pnpm/yarn/bun test or run, npx, cargo, go test or vet, "
           "ruff, mypy, pyright, tsc, eslint, jest, vitest, a ./ or scripts/ path")
_RUNNER = re.compile(
    r"(?:^|[\s/])(?:pytest|python[0-9.]*|make|tox|nox|npx|ruff|mypy|pyright|tsc|eslint|jest|vitest)(?:\s|$)"
    r"|\b(?:uv\s+run|(?:npm|pnpm|yarn|bun)\s+(?:test|run)|cargo\s+\w+|go\s+(?:test|vet))\b"
    r"|(?:^|\s)\./\S|(?:^|[\s/])scripts/\S")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
# Only `set-status --human --by <name>` writes these fields.
HUMAN_FIELDS = ("human_confirmed", "confirmed_by", "confirmed_at")


def _is_skill_script(word: str) -> bool:
    """A relative `.py` path that the skill root holds and the repository does not: it runs only from the skill."""
    if not word.endswith(".py") or word.startswith(("/", "$", "~")):
        return False
    return (Path(__file__).resolve().parents[1] / word).is_file() and not (Path.cwd() / word).is_file()


def check_problem(check: object) -> str | None:
    """Why ``check`` cannot close a finding, else None: empty, a shell no-op in any segment, or no runner."""
    text = str(check or "").strip()
    if text == "manual":
        return None
    if not text or text.split()[-1] == "--":
        return f"check {text!r} is empty: name the test or the script that proves the fix"
    for segment in _SEGMENTS.split(text):
        words = [w for w in segment.split() if not _ASSIGNMENT.match(w)]
        if words and words[0].rsplit("/", 1)[-1] in NO_OP_COMMANDS:
            return f"check {text!r} is a shell no-op ({words[0]}): name the test or the script that proves the fix"
        skill_path = next((w for w in words if _is_skill_script(w)), None)
        if skill_path:
            return (f"check {text!r} names {skill_path}, a Cadence script that is not in this repository: "
                    f"write $CADENCE_HOME/{skill_path}")
    return None if _RUNNER.search(text) else f"check {text!r} names no test runner or script path ({RUNNERS})"


def validate_write(record: dict[str, Any]) -> dict[str, Any]:
    """The rules of a row that an agent writes: a real check, and `fixed_in` on a fixed row (gh-175).

    load_findings does not apply them, so an older ledger still loads. ledger_verify applies them again.
    """
    problem = check_problem(record["check"]) if "check" in record else None
    if problem:
        raise FindingsValidationError(problem)
    if record["status"] == "fixed" and not _SHA.match(str(record.get("fixed_in") or "")):
        raise FindingsValidationError("status=fixed requires fixed_in, the sha of the commit "
                                      "that holds the fix (set-status --fixed-in <sha>)")
    return record


def commit_on_branch(sha: str, cwd: Path | None = None) -> str:
    """The full sha of ``sha`` when it is HEAD or an ancestor of HEAD in ``cwd``, else raise (gh-175)."""
    import subprocess

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    full = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}").stdout.strip() if _SHA.match(sha) else ""
    if not full:
        raise FindingsValidationError(f"fixed_in {sha!r} is not a commit in {cwd or Path.cwd()}: give the hex sha of "
                                      "the commit (7 to 40 characters), not a ref such as HEAD")
    if git("merge-base", "--is-ancestor", full, "HEAD").returncode != 0:
        raise FindingsValidationError(f"fixed_in {sha} is not on the branch: it is not HEAD or an ancestor of HEAD")
    return full


def is_open_must_fix(row: dict[str, Any]) -> bool:
    """R4 and R6: a must-fix row that is still open."""
    return row.get("severity") == "must-fix" and row.get("status") == "open"


def blocks_closure(row: dict[str, Any]) -> bool:
    """R5: a must-fix row that is not verified, deferred or spec_gap."""
    return row.get("severity") == "must-fix" and row.get("status") not in TERMINAL_MUST_FIX


# cd-49: the one rule for "a row of this task". Ledger ids are `<task prefix>-F<n>` or `-MF-<n>`; the
# prefix is the task id with characters the id pattern rejects folded to `-` (so a GitHub
# ticket `owner/repo#12` gives `owner-repo-12`), or `CADENCE` for an empty task id.
def task_id_prefix(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", task_id).strip("-") or "CADENCE"


def task_row_pattern(task_id: str) -> re.Pattern[str]:
    """Anchored at both ends, so `cd-6` never matches `cd-63-F1`; the task part ignores case.

    An R1 row may carry a run tag before `MF-`: `<task prefix>-<run tag>-MF-<n>` (r1_row_id)."""
    return re.compile(rf"^{re.escape(task_id_prefix(task_id))}-(?:F|(?:[A-Za-z0-9]{{1,8}}-)?MF-)(\d+)$",
                      re.IGNORECASE)


def run_tag(run_id: object) -> str:
    """The first 8 letters and digits of a run id, lower case; "" with no run."""
    return re.sub(r"[^A-Za-z0-9]", "", str(run_id or ""))[:8].lower()


def r1_row_id(task_id: str, run_id: object, n: object) -> str:
    """The ledger id of R1 item `MF-n` for run `run_id`: `<task prefix>-<run tag>-MF-<n>`.

    The run tag keeps a second run of the task from finding the row of the first (PR #188 review B-2).
    With no run, the id is `<task prefix>-MF-<n>`."""
    tag = run_tag(run_id)
    return f"{task_id_prefix(task_id)}-{tag + '-' if tag else ''}MF-{n}"


def rows_of_task(rows: Iterable[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    own = task_row_pattern(task_id)
    return [row for row in rows if own.match(str(row.get("id", "")))]


@dataclass(frozen=True)
class TaskSplit:
    own: list[dict[str, Any]]
    other: list[dict[str, Any]]
    unowned: list[dict[str, Any]]


def split_by_task(rows: list[dict[str, Any]], task_id: str, known_task_ids: Iterable[str]) -> TaskSplit:
    """Rows of this task, rows of another known task, and rows of no known task."""
    own = rows_of_task(rows, task_id)
    other = [row for row in rows if row not in own and any(rows_of_task([row], t) for t in known_task_ids)]
    unowned = [row for row in rows if row not in own and row not in other]
    return TaskSplit(own=own, other=other, unowned=unowned)


def ledger_scope(rows: list[dict[str, Any]], context_dir: Path, *, cwd: Path | None = None) -> dict[str, Any]:
    """The rows a gate judges: its run's task (from the worktree, never the root pointer).

    With no run, or a run with no task id, every row is judged (fail closed).
    """
    from pipeline_run_binding import known_task_ids, run_for

    task_id = str((run_for(Path(context_dir), cwd=cwd) or {}).get("task_id") or "")
    if not task_id:
        return {"task_id": None, "scope": "all", "rows": list(rows), "other_task_skipped": 0, "unowned": []}
    split = split_by_task(rows, task_id, known_task_ids(Path(context_dir)))
    return {"task_id": task_id, "scope": "task", "rows": split.own,
            "other_task_skipped": len(split.other), "unowned": split.unowned}


def scope_summary(scope: dict[str, Any], keep: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """The JSON keys every scoped gate prints; ``unowned`` lists the ids the gate's rule keeps."""
    return {"task_id": scope["task_id"], "scope": scope["scope"],
            "other_task_skipped": scope["other_task_skipped"],
            "unowned": [row["id"] for row in scope["unowned"] if keep(row)]}


class FindingsValidationError(ValueError):
    """Invalid finding record."""


def validate_finding(record: dict[str, Any], *, allow_verified: bool = False) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise FindingsValidationError("finding must be an object")
    missing = [k for k in REQUIRED if k not in record]
    if missing:
        raise FindingsValidationError(f"missing required fields: {missing}")
    if not ID_RE.match(str(record["id"])):
        raise FindingsValidationError(f"invalid id format: {record['id']!r}")
    if record["class"] not in CLASSES:
        raise FindingsValidationError(f"invalid class: {record['class']!r}")
    if record["severity"] not in SEVERITIES:
        raise FindingsValidationError(f"invalid severity: {record['severity']!r}")
    status = record["status"]
    allowed = STATUSES_ALL if allow_verified else STATUSES_WRITABLE
    if status not in allowed:
        if status == "verified" and not allow_verified:
            raise FindingsValidationError(
                "status=verified is forbidden via findings_io; use ledger_verify.py"
            )
        raise FindingsValidationError(f"invalid status: {status!r}")
    if not str(record["evidence"]).strip():
        raise FindingsValidationError("evidence must be non-empty")
    if record["severity"] == "must-fix" and not record.get("check"):
        raise FindingsValidationError("must-fix findings require check")
    return record


def append_finding(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    validated = validate_write(validate_finding(record, allow_verified=False))
    if any(k in validated for k in HUMAN_FIELDS):
        raise FindingsValidationError(f"{validated['id']}: only set-status --human --by <name> writes {list(HUMAN_FIELDS)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(validated, ensure_ascii=False) + "\n")
    return validated


def load_findings(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise FindingsValidationError(f"{path}:{line_no}: {e}") from e
        out.append(validate_finding(row, allow_verified=True))
    return out


def rewrite_findings(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for row in records:
        validated = validate_finding(row, allow_verified=True)
        lines.append(json.dumps(validated, ensure_ascii=False))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def ledger_must_fix_ids(context_dir: Path) -> set[str]:
    """Ids of must-fix rows in any status in findings.jsonl and findings/*.jsonl: the R1 and R2 row checks (gh-174)."""
    paths = [context_dir / "findings.jsonl", *sorted((context_dir / "findings").glob("*.jsonl"))]
    return {str(row.get("id")) for path in paths for row in load_findings(path) if row.get("severity") == "must-fix"}


def default_ledger_path(context_dir: Path, task_id: str) -> Path:
    safe = task_id.replace("/", "__")
    return context_dir / "findings" / f"{safe}.jsonl"


def set_status(path: Path, finding_id: str, status: str, *, ticket: str | None = None, note: str | None = None,
               fixed_in: str | None = None, confirmed_by: str | None = None,
               cwd: Path | None = None) -> dict[str, Any]:
    """Move one finding to a model-writable status; verified rows are never touched.

    ``confirmed_by`` is the `--human --by <name>` path of a `manual` check (gh-175).
    """
    if status not in STATUSES_WRITABLE:
        raise FindingsValidationError(
            f"status {status!r} not settable here (allowed: {sorted(STATUSES_WRITABLE)}); "
            "verified comes only from ledger_verify.py"
        )
    rows = load_findings(path)
    hits = [r for r in rows if r["id"] == finding_id]
    # Byte-identical duplicates are one finding appended twice; they change together.
    if not hits or any(h != hits[0] for h in hits):
        raise FindingsValidationError(f"{finding_id}: {len(hits)} matching findings in {path}")
    row = hits[0]
    if row["status"] == "verified":
        raise FindingsValidationError(f"{finding_id} is verified; it cannot be reopened or changed here")
    if status in ("deferred", "spec_gap") and not (ticket or row.get("ticket")):
        raise FindingsValidationError(f"status={status} requires --ticket")
    changes = {"status": status, **({"ticket": ticket} if ticket else {}), **({"note": note} if note else {})}
    if fixed_in:
        changes["fixed_in"] = commit_on_branch(fixed_in, cwd)
    if confirmed_by is not None:
        changes.update(_human_stamp(row, confirmed_by))
    for hit in hits:
        hit.update(changes)
        validate_finding(hit, allow_verified=False)
        if status == "fixed":
            validate_write(hit)
    rewrite_findings(path, rows)
    return row


def _human_stamp(row: dict[str, Any], confirmed_by: str) -> dict[str, Any]:
    """The fields of `set-status --human --by <name>`: a person confirms a `manual` check (gh-175)."""
    from datetime import datetime, timezone

    if not confirmed_by.strip():
        raise FindingsValidationError("--human requires --by <name> of the person who confirms the fix")
    if row.get("check") != "manual":
        raise FindingsValidationError(f"{row['id']}: --human confirms only a check of `manual`; "
                                      f"ledger_verify.py runs the check {row.get('check')!r}")
    return {"human_confirmed": True, "confirmed_by": confirmed_by.strip(),
            "confirmed_at": datetime.now(timezone.utc).isoformat()}


def _default_ledger() -> Path:
    shared = Path(__file__).resolve().parent.parent / "shared" / "scripts"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    from context_root import context_dir  # noqa: E402

    return context_dir() / "findings.jsonl"


def _append_owner_problem(ledger: Path, record: Any) -> str | None:
    """cd-49 D9: the canonical ledger takes only ids of the cwd's run task, or of a known task with no run."""
    canonical = _default_ledger()
    if not isinstance(record, dict) or ledger.resolve() != canonical.resolve():
        return None
    from pipeline_run_binding import known_task_ids, run_for

    task_id = str((run_for(canonical.parent, cwd=Path.cwd()) or {}).get("task_id") or "")
    fid = record.get("id")
    if task_id:
        if rows_of_task([record], task_id):
            return None
        return f"{fid} is not an id of task {task_id}; use {task_id_prefix(task_id)}-F<n>"
    if any(rows_of_task([record], known) for known in known_task_ids(canonical.parent)):
        return None
    return f"{fid} matches no known task; use <task-prefix>-F<n>, or --ledger for a file outside the run ledger"


def _deferral_problem(ledger: Path, status: str, ticket: str | None) -> str | None:
    """gh-178: in review (the cwd's run has a draft-PR record), a deferral on the canonical ledger meets P8.
    Its ticket matches the adapter pattern, and an adjudicated deferral register has a row that names it."""
    canonical = _default_ledger()
    if status != "deferred" or ledger.resolve() != canonical.resolve():
        return None
    from artifact_requirements import draft_pr_record
    from deferral_adjudication_check import register_row_problem
    from gate_common import current_branch
    from pipeline_run_binding import run_for

    run = run_for(canonical.parent, cwd=Path.cwd())
    if run is None or draft_pr_record(canonical.parent, run, current_branch(Path.cwd())) is None:
        return None
    return register_row_problem(canonical.parent, run, str(ticket or ""))


_BARE_R1 = re.compile(r"^MF-(\d+)$")


def _expand_r1_id(ledger: Path, record: Any) -> Any:
    """A bare `MF-n` id on the canonical ledger becomes the row id of the cwd's run (PR #191 review B-2).

    The peer writes `MF-n`; the task prefix and the run tag come from `run_for`, never from a file the peer reads."""
    bare = _BARE_R1.match(str(record.get("id", ""))) if isinstance(record, dict) else None
    if bare is None or ledger.resolve() != _default_ledger().resolve():
        return record
    from load_stages import writer_stages

    r1_stages = writer_stages("pr-review-local")  # the registry's list, never a Python set
    if record.get("stage") not in r1_stages:
        raise FindingsValidationError(f"{record['id']}: a bare MF-n id is for the R1 stages {sorted(r1_stages)}; "
                                      f"stage {record.get('stage')!r} writes <task prefix>-F<n>")
    from pipeline_run_binding import run_for

    run = run_for(ledger.parent, cwd=Path.cwd()) or {}
    if not run.get("task_id"):
        raise FindingsValidationError(f"{record['id']}: no Cadence run in this worktree names the task, so the row id "
                                      "cannot be built; write <task prefix>-MF-<n>")
    return {**record, "id": r1_row_id(str(run["task_id"]), run.get("pipeline_run_id"), bare.group(1))}


def _append(ledger: Path, raw: str) -> dict[str, Any]:
    """`append`: the record is valid first (R1 N-5), then the owner, deferral, duplicate and fix-commit rules."""
    record = validate_write(validate_finding(_expand_r1_id(ledger, json.loads(raw))))
    for problem in (_append_owner_problem(ledger, record), _deferral_problem(ledger, record["status"], record.get("ticket"))):
        if problem:
            raise FindingsValidationError(problem)
    if record["id"] in {r["id"] for r in load_findings(ledger)}:
        raise FindingsValidationError(f"{record['id']} already exists in {ledger}; use set-status")
    if record["status"] == "fixed":
        record["fixed_in"] = commit_on_branch(str(record["fixed_in"]))
    return append_finding(ledger, record)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append findings or change their status (never to verified)")
    parser.add_argument("--ledger", help="findings jsonl (default: findings.jsonl in the canonical .context)")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("append", help="append one finding record")
    add.add_argument("--json", required=True, help="the finding as a JSON object")
    st = sub.add_parser("set-status", help="set a finding to open|fixed|deferred|spec_gap")
    st.add_argument("--id", required=True)
    st.add_argument("--status", required=True)
    st.add_argument("--ticket")
    st.add_argument("--note")
    st.add_argument("--fixed-in", help="sha of the commit that holds the fix (required for --status fixed)")
    st.add_argument("--human", action="store_true", help="a person confirms a manual check (with --by)")
    st.add_argument("--by", help="the name of the person who confirms (with --human)")
    args = parser.parse_args(argv)
    ledger = Path(args.ledger) if args.ledger else _default_ledger()
    try:
        if args.command == "append":
            row = _append(ledger, args.json)
        else:
            held = next((r.get("ticket") for r in load_findings(ledger) if r.get("id") == args.id), None)
            problem = _deferral_problem(ledger, args.status, args.ticket or held)
            if problem:
                raise FindingsValidationError(problem)
            row = set_status(ledger, args.id, args.status, ticket=args.ticket, note=args.note, fixed_in=args.fixed_in,
                             confirmed_by=(args.by or "") if args.human else None)
    except (FindingsValidationError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
