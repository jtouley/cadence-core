#!/usr/bin/env python3
"""R6 — guard-gh-pr-comment gate.

gh-178: the comment body holds a `## Fixes` table (finding, commit) and a `## Deferrals`
table (finding, ticket). The gate checks each row against the ledger and fails closed.
ADR-0018: without --check, the script writes the body from the ledger itself, then checks it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from findings_io import ID_RE, FindingsValidationError, is_open_must_fix, ledger_scope, load_findings, scope_summary  # noqa: E402
from gate_common import ctx, fail, ok, write_gate_breadcrumb  # noqa: E402

SLUG = "guard-gh-pr-comment"
_FINDINGS_GLOBS = ("findings/*.jsonl", "findings.jsonl")
FIXES, DEFERRALS = "Fixes", "Deferrals"
_GUIDE = "references/pr-review-github-comment.md"


def _all_findings(context_dir: Path) -> list[dict]:
    # load_findings fails loud on a corrupt ledger; a guard must not skip bad lines.
    return [record for pattern in _FINDINGS_GLOBS for path in sorted(context_dir.glob(pattern))
            for record in load_findings(path)]


def comment_body_path(context_dir: Path, cwd: Path) -> Path:
    """`prs/comments/pr-comment-<run id>.md`: the body that R6 checks and `gh pr comment --body-file` posts."""
    from pipeline_run_binding import run_for

    run_id = str((run_for(context_dir, cwd=cwd) or {}).get("pipeline_run_id") or "")
    return context_dir / "prs" / "comments" / (f"pr-comment-{run_id}.md" if run_id else "pr-comment.md")


def comment_tables(text: str) -> dict[str, dict[str, str]]:
    """{`Fixes` or `Deferrals`: {finding id: the second cell}}. A heading that the body does not have has no key."""
    tables: dict[str, dict[str, str]] = {}
    rows: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("#"):
            name = line.lstrip("#").strip()
            rows = tables.setdefault(name, {}) if name in (FIXES, DEFERRALS) else None
        elif rows is not None and line.lstrip().startswith("|"):
            cells = [cell.strip().strip("`*").strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and ID_RE.match(cells[0]):
                rows[cells[0]] = cells[1]
    return tables


def _same_sha(cell: str, sha: str) -> bool:
    return len(cell) >= 7 and sha.lower().startswith(cell.lower())


def _table_problems(name: str, table: dict[str, str] | None, want: dict[str, str],
                    same: Callable[[str, str], bool], kind: str) -> list[str]:
    if table is None:
        return [f"{SLUG}: the comment body has no `## {name}` table ({_GUIDE})"]
    problems = [f"{SLUG}: `## {name}` has no row for {fid} (the ledger has {value})"
                for fid, value in sorted(want.items()) if fid not in table]
    problems += [f"{SLUG}: `## {name}` row {fid} names {table[fid]}, the ledger has {value}"
                 for fid, value in sorted(want.items()) if fid in table and not same(table[fid], value)]
    return problems + [f"{SLUG}: `## {name}` row {fid} is not {kind} of this run in the ledger"
                       for fid in sorted(set(table) - set(want))]


def body_problems(body: Path, rows: list[dict]) -> list[str]:
    """Each verified must-fix with `fixed_in` is a Fixes row with that sha. Each deferred or spec_gap must-fix
    is a Deferrals row with its ticket. A row that the ledger does not hold fails."""
    try:
        tables = comment_tables(body.read_text(encoding="utf-8"))
    except OSError:
        return [f"{SLUG}: no comment body at {body}. Write it with the `## {FIXES}` and `## {DEFERRALS}` tables ({_GUIDE})"]
    must = [row for row in rows if row.get("severity") == "must-fix"]
    fixes = {row["id"]: str(row["fixed_in"]) for row in must if row.get("status") == "verified" and row.get("fixed_in")}
    deferrals = {row["id"]: str(row.get("ticket") or "") for row in must if row.get("status") in ("deferred", "spec_gap")}
    return (_table_problems(FIXES, tables.get(FIXES), fixes, _same_sha, "a verified must-fix with fixed_in")
            + _table_problems(DEFERRALS, tables.get(DEFERRALS), deferrals, lambda a, b: a == b,
                              "a deferred or spec_gap must-fix"))


def render_body(rows: list[dict], assumptions: dict[str, list[str]] | None = None) -> str:
    """The closure comment of a run with no open must-fix, from its ledger rows and logged assumptions."""
    must = [row for row in rows if row.get("severity") == "must-fix"]
    fixes = [(row["id"], str(row["fixed_in"])[:12]) for row in must
             if row.get("status") == "verified" and row.get("fixed_in")]
    deferrals = [(row["id"], str(row.get("ticket") or "")) for row in must if row.get("status") in ("deferred", "spec_gap")]
    others = [row["id"] for row in rows if row.get("severity") != "must-fix" and row.get("status") == "open"]
    lines = ["## Closure summary", "", "The ledger is `.context/findings.jsonl`.",
             f"{len(must)} must-fix findings are closed. No must-fix finding is open.",
             f"{len(others)} other findings are open. They go to the tracker." if others else "No other finding is open.",
             "The PR is ready for review by a person.", "",
             f"## {FIXES}", "", "| Finding | Commit |", "|---|---|", *[f"| {i} | {c} |" for i, c in fixes], "",
             f"## {DEFERRALS}", "", "| Finding | Ticket |", "|---|---|", *[f"| {i} | {t} |" for i, t in deferrals]]
    if assumptions:
        lines += ["", "## Assumptions", "", "| Gap | Assumption | Settled by |", "|---|---|---|",
                  *[f"| {gap} | {' | '.join((cells + ['', ''])[1:3])} |" for gap, cells in sorted(assumptions.items())]]
    if others:
        lines += ["", "## Open findings for the tracker", "", *[f"- {i}" for i in others]]
    return "\n".join(lines) + "\n"


def _post_command(context_dir: Path, cwd: Path, body: Path) -> str:
    from artifact_requirements import draft_pr_number, draft_pr_record
    from gate_common import current_branch
    from pipeline_run_binding import run_for

    record = draft_pr_record(context_dir, run_for(context_dir, cwd=cwd) or {}, current_branch(cwd))
    number = draft_pr_number(record) if record is not None else None
    return f"gh pr comment {number if number is not None else '<N>'} --body-file {body}"


def run(context_dir: Path, *, check: bool = False, cwd: Path | None = None, body: Path | None = None) -> int:
    """Judge the must-fix rows of the run of ``cwd`` (cd-49); with no run, every row."""
    cwd = cwd or Path.cwd()
    try:
        scope = ledger_scope(_all_findings(context_dir), context_dir, cwd=cwd)
    except (OSError, FindingsValidationError) as e:
        return fail(SLUG, [f"guard-gh-pr-comment: unreadable findings ledger: {e}"])
    summary = scope_summary(scope, is_open_must_fix)
    open_findings = [row for row in scope["rows"] if is_open_must_fix(row)]
    if open_findings:
        errors = [f"guard-gh-pr-comment: {len(open_findings)} open must-fix finding(s) block PR comment"]
        errors += [f"  must-fix [{f.get('id', '?')}] at {f.get('location', '?')}" for f in open_findings]
    else:
        if not check and body is None:
            body = comment_body_path(context_dir, cwd)
            body.parent.mkdir(parents=True, exist_ok=True)
            from artifact_requirements import logged_assumptions
            from pipeline_run_binding import run_for

            assumptions = logged_assumptions(context_dir, run_for(context_dir, cwd=cwd))
            body.write_text(render_body(scope["rows"], assumptions), encoding="utf-8")
        errors = body_problems(body or comment_body_path(context_dir, cwd), scope["rows"])
    if errors:
        code = fail(SLUG, errors)
        print(json.dumps(summary), file=sys.stderr)
        return code
    if not check:
        write_gate_breadcrumb(SLUG, {"open_must_fix_count": 0, **summary}, context_dir=context_dir)
    print(json.dumps(summary))
    if not check:
        print(f"NEXT: {_post_command(context_dir, cwd, body or comment_body_path(context_dir, cwd))}")
    return ok(SLUG)


def main() -> int:
    parser = argparse.ArgumentParser(description="R6: the PR comment maps each fix to its commit and each deferral to its ticket")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--body-file", help="the comment body (default: .context/prs/comments/pr-comment-<run id>.md)")
    args = parser.parse_args()
    return run(ctx(), check=args.check, body=Path(args.body_file) if args.body_file else None)


if __name__ == "__main__":
    raise SystemExit(main())
