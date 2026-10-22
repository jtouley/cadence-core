#!/usr/bin/env python3
"""Validate deferral register adjudication for /cadence (P8).

Cadence: ticket regex from adapters/*.json; no hardcoded org prefixes.

Car-apart default: ship in-PR. A row defers work only when it traces to no part of the request,
names a ripeness with its evidence and a follow-up ticket, and the /deferral-review peer approved
it. Exit 0 when ok; exit 1 with the issues.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
_SHARED = _ROOT / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

os.environ.setdefault("CADENCE_HOME", str(_ROOT))

from skill_gate import ticket_pattern  # noqa: E402

VALID_RIPENESS = frozenset(
    {
        "blocked-by",
        "prod-mutation",
        "different-epic",
        "different-layer",
        "fleet-ops",
    }
)

INVALID_RIPENESS = frozenset(
    {
        "out-of-ac",
        "yagni",
        "nit",
        "nits",
        "pr-review-nit",
        "follow-up",
        "convenience",
    }
)

# In review (R1 to R5) a must-fix is fixed in the PR. Only a blocker that no code change in it can
# clear defers it.
REVIEW_RIPENESS = frozenset({"blocked-by", "prod-mutation", "fleet-ops"})
# The `Request trace` cell of a row that traces to no part of the request, the A5 report or the plan's goal.
NO_TRACE = "none"


def traces_to_nothing(cell: str) -> bool:
    """The cell says `none`, with prose after it or not: a plan peer wrote "none. The request names LOCAL-9
    only to put the upload out of this change", and P8 refused it through three plan updates."""
    first = re.split(r"[\s.,;:(—-]+", cell.strip().strip("`*_ "), maxsplit=1)[0]
    return first.lower() == NO_TRACE
# The RAG ruling of /deferral-review: GREEN defers, AMBER defers with a condition that this PR
# builds, RED builds the item in this PR. A review row is GREEN or RED.
RULINGS = ("GREEN", "AMBER", "RED")
CONDITIONS_HEADER = "## Deferral conditions"
# The documents that /deferral-review reads: the run before E1, and the review files after E4.
MANIFEST_GLOBS = ("proposals/*.md", "vibetest/*.md", "reviews/plan_*.md", "adversarial/*.md",
                  "benchmarks/*_plan.json", "deferrals/*_deferral-register.md", "issues/*.md",
                  "prs/reviews/*.md", "findings.jsonl")

ZERO_APPROVED = re.compile(
    r"(?i)(no approved deferrals|zero approved|approved deferrals:\s*0|"
    r"none\.\s*all adversarial)"
)


@dataclass
class DeferralRow:
    number: str
    item: str
    reason: str
    ticket: str
    ripeness: str
    raw: str
    trace: str = ""
    evidence: str = ""


@dataclass
class AdjudicationIssue:
    code: str
    message: str
    recovery: str = ""


@dataclass
class AdjudicationResult:
    ok: bool
    approved_count: int
    issues: list[AdjudicationIssue] = field(default_factory=list)


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {"": text}
    parts = re.split(r"(?m)^##\s+", text)
    if len(parts) <= 1:
        return sections
    preamble = parts[0]
    if preamble.strip():
        sections[""] = preamble
    for part in parts[1:]:
        line, _, body = part.partition("\n")
        sections[line.strip().lower()] = body
    return sections


def _parse_table(section: str) -> tuple[list[str], list[list[str]]]:
    lines = [ln.rstrip() for ln in section.splitlines()]
    header: list[str] = []
    rows: list[list[str]] = []
    in_table = False
    for ln in lines:
        if not ln.startswith("|"):
            if in_table and rows:
                break
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if all(re.match(r"^[-:\s]+$", c) for c in cells):
            in_table = True
            continue
        if not header:
            header = cells
            in_table = True
            continue
        if in_table and cells and not all(not c for c in cells):
            rows.append(cells)
    return header, rows


def _col_index(header: list[str], *names: str) -> int | None:
    lowered = [h.lower() for h in header]
    for name in names:
        for i, h in enumerate(lowered):
            if name in h:
                return i
    return None


def _row_to_deferral(header: list[str], cells: list[str]) -> DeferralRow | None:
    if not cells or not header:
        return None
    num = cells[0] if cells else ""
    if not re.match(r"^\d+$", num.strip()):
        return None
    item_i = _col_index(header, "item") or 1
    reason_i = _col_index(header, "why not", "why deferred") or 2
    ticket_i = _col_index(header, "follow-up ticket", "follow-up") or 4
    item = cells[item_i] if item_i < len(cells) else ""
    reason = cells[reason_i] if reason_i < len(cells) else ""
    ticket = cells[ticket_i] if ticket_i < len(cells) else ""
    def cell(*names: str) -> str:
        i = _col_index(header, *names)
        return cells[i].strip() if i is not None and i < len(cells) else ""

    return DeferralRow(
        number=num.strip(),
        item=item,
        reason=reason,
        ticket=ticket,
        ripeness=cell("ripeness").lower(),
        raw="|".join(cells),
        trace=cell("request trace", "trace"),
        evidence=cell("evidence"),
    )


def _approved_section_text(text: str) -> str:
    sections = _split_sections(text)
    for key in ("approved deferrals",):
        if key in sections:
            return sections[key]
    m = re.search(r"(?is)(##\s*deferrals rejected|##\s*review deferrals)", text)
    head = text[: m.start()] if m else text
    if "| # |" in head or "| Item |" in head:
        return head
    return ""


def _review_section_text(text: str) -> str:
    sections = _split_sections(text)
    for key in sections:
        if key.startswith("review deferrals") or key.startswith("phase 6 deferrals"):
            return sections[key]
    return ""


def parse_approved_rows(text: str, *, review_phase: bool = False) -> list[DeferralRow]:
    section = _review_section_text(text) if review_phase else _approved_section_text(text)
    if not section.strip():
        if not review_phase and ZERO_APPROVED.search(text):
            return []
        return []
    header, table_rows = _parse_table(section)
    if not header:
        return []
    out: list[DeferralRow] = []
    for cells in table_rows:
        row = _row_to_deferral(header, cells)
        if row is not None:
            out.append(row)
    return out


def _validate_row(row: DeferralRow, *, review_phase: bool) -> list[AdjudicationIssue]:
    issues: list[AdjudicationIssue] = []
    prefix = (
        f"Review deferral #{row.number}"
        if review_phase
        else f"Approved deferral #{row.number}"
    )
    pat = ticket_pattern()

    ticket_match = pat.search(row.ticket) or pat.search(row.reason)
    if not ticket_match:
        issues.append(
            AdjudicationIssue(
                "deferral-missing-ticket",
                f"{prefix}: missing follow-up ticket matching adapter pattern",
                "Create tracker sub-issue or move item to Deferrals rejected (ship in-PR)",
            )
        )

    issues.extend(_ripeness_issues(row, prefix, review_phase=review_phase))
    if not row.trace:
        issues.append(AdjudicationIssue(
            "deferral-missing-trace", f"{prefix}: no `Request trace` cell",
            f"Write `{NO_TRACE}`, or the line of the request, the A5 report or the plan goal that the item serves"))
    elif not traces_to_nothing(row.trace):
        issues.append(AdjudicationIssue(
            "deferral-core-of-request",
            f"{prefix}: the item traces to the request ({row.trace[:80]}), so it is core and is not deferred",
            "Move the row under `## Deferrals rejected` and build the item in this PR"))
    if not row.evidence:
        issues.append(AdjudicationIssue(
            "deferral-missing-evidence", f"{prefix}: no `Evidence` cell for ripeness {row.ripeness or '?'}",
            "Name what makes the ripeness true: the open blocker, the operator action, or the other epic or layer"))
    elif row.ripeness == "blocked-by" and not (pat.search(row.evidence) or re.search(r"#\d+|https?://", row.evidence)):
        issues.append(AdjudicationIssue(
            "deferral-blocker-unnamed", f"{prefix}: blocked-by names no open ticket or PR in its evidence",
            "Name the ticket or PR that blocks the item"))
    return issues


def _ripeness_issues(row: DeferralRow, prefix: str, *, review_phase: bool) -> list[AdjudicationIssue]:
    if not row.ripeness:
        return [AdjudicationIssue("deferral-missing-ripeness", f"{prefix}: no `Ripeness` cell",
                                  f"Use one of: {', '.join(sorted(VALID_RIPENESS))}, or build the item in this PR")]
    if row.ripeness in INVALID_RIPENESS:
        return [AdjudicationIssue(
            "deferral-invalid-ripeness",
            f"{prefix}: Ripeness '{row.ripeness}' is not allowed — use blocked-by, "
            "prod-mutation, different-epic, different-layer, or fleet-ops",
            "Ship in-PR or document blocked-by with landing dependency ticket")]
    if row.ripeness not in VALID_RIPENESS:
        return [AdjudicationIssue("deferral-unknown-ripeness", f"{prefix}: unknown Ripeness '{row.ripeness}'",
                                  f"Use one of: {', '.join(sorted(VALID_RIPENESS))}")]
    if review_phase and row.ripeness not in REVIEW_RIPENESS:
        return [AdjudicationIssue(
            "review-deferral-not-ripe",
            f"{prefix}: a review finding with ripeness '{row.ripeness}' is fixed in this PR",
            f"In review only {', '.join(sorted(REVIEW_RIPENESS))} defers a finding: send the fix round")]
    return []


def adjudicate_deferral_register(text: str) -> AdjudicationResult:
    issues: list[AdjudicationIssue] = []
    approved = parse_approved_rows(text, review_phase=False)
    review = parse_approved_rows(text, review_phase=True)

    for row in approved:
        issues.extend(_validate_row(row, review_phase=False))
    for row in review:
        issues.extend(_validate_row(row, review_phase=True))

    return AdjudicationResult(
        ok=not issues,
        approved_count=len(approved) + len(review),
        issues=issues,
    )


def review_path(register: Path) -> Path:
    """The /deferral-review ruling of a register: `<task>_deferral-review.md` beside it."""
    return register.with_name(register.name.replace("_deferral-register.md", "_deferral-review.md"))


def manifest_path(register: Path) -> Path:
    """The one input of /deferral-review: `.context/manifests/<task>_deferrals.md`."""
    return register.parent.parent / "manifests" / register.name.replace("_deferral-register.md", "_deferrals.md")


def _rows(text: str) -> dict[str, DeferralRow]:
    rows = {f"approved {r.number}": r for r in parse_approved_rows(text)}
    return rows | {f"review {r.number}": r for r in parse_approved_rows(text, review_phase=True)}


def write_review_manifest(register: Path, workspace: Path) -> Path:
    """The documents of the run that the /deferral-review peer rules from, with their sha256."""
    import hashlib

    from activate_session import _shown, document_table
    from pipeline_run_binding import current_session

    context, data = register.parent.parent, register.read_bytes()
    session = current_session(context)
    task = str(session.get("task_id") or "")
    lines = [f"# Deferral review manifest: {task}", "", f"Ticket: {task}",
             f"Pipeline-Run: {session.get('pipeline_run_id') or ''}",
             f"Register: {_shown(register, context, workspace)}",
             f"Register sha256: {hashlib.sha256(data).hexdigest()}",
             f"Rows: {', '.join(_rows(data.decode('utf-8', errors='replace')))}", "",
             *document_table(context, workspace, session, MANIFEST_GLOBS), "",
             f"Write the ruling to {_shown(review_path(register), context, workspace)}."]
    path = manifest_path(register)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _carried(context: Path, keys: list[str]) -> list[str]:
    """The AMBER rows that the run's plan does not map under `## Deferral conditions`."""
    from artifact_requirements import _run_plan, section
    from pipeline_run_binding import current_session

    plan = _run_plan(context, current_session(context))
    body = section(plan.read_text(encoding="utf-8", errors="replace"), CONDITIONS_HEADER).lower() if plan else ""
    return [k for k in keys if k not in body]


def _ruling_file_problem(register: Path, data: bytes, review: Path, send: str, rows: int) -> str | None:
    """Why ``review`` is not a ruling of ``register`` as it is now, by a peer on Claude Code, else None."""
    import hashlib

    import peer_attest
    from pipeline_run_binding import current_session

    if not review.is_file():
        return f"deferral-review: no ruling of the {rows} deferral rows of {register.name}: {send}"
    ruling = review.read_text(encoding="utf-8", errors="replace")
    pinned = re.search(r"(?im)^\s*Register sha256:\s*([0-9a-f]{12,64})\s*$", ruling)
    if "RUN_BY_COMMAND: deferral-review" not in ruling or not pinned \
            or not hashlib.sha256(data).hexdigest().startswith(pinned.group(1).lower()):
        return f"deferral-review: {review.name} does not rule on {register.name} as it is now: {send}"
    context, session = register.parent.parent, current_session(register.parent.parent)
    if session.get("host") == "claude" and not peer_attest.attested(
            context, str(session.get("pipeline_run_id") or ""), review, peer_attest.orchestrator_ids(context)):
        return f"deferral-review: {review.name} is not the latest write of a peer: {send}"
    return None


def _row_problem(key: str, item: str, cells: list[str], review: str, send: str) -> str | None:
    """Why the ruling ``cells`` (row, ruling, reason, condition) does not defer row ``key``, else None.
    A carried AMBER is checked by the caller."""
    verdict, reason, condition = cells[1].strip().upper(), cells[2], cells[3].strip()
    if verdict not in RULINGS or (verdict == "AMBER" and key.startswith("review")):
        return (f"deferral-review: {review} has no RAG ruling for {key} "
                f"(GREEN, AMBER or RED; a review row is GREEN or RED): {send}")
    if verdict == "RED":
        return (f"deferral-red: {key} ({item[:60]}): {reason}. It is built in this PR: before E1 "
                "send /plan-update, which moves it under `## Deferrals rejected` and plans it; in review "
                "it stays a must-fix of the fix round")
    if verdict == "AMBER" and condition.lower() in ("", "none"):
        return f"deferral-review: {key} is AMBER with no condition: {send}"
    return None


def ruling_problems(register: Path) -> list[str]:
    """Why the deferrals of ``register`` lack a GREEN or a carried AMBER of /deferral-review, else [].

    The ruling covers the register as it is now and comes from a peer write on Claude Code."""
    data = register.read_bytes()
    rows = _rows(data.decode("utf-8", errors="replace"))
    if not rows:
        return []
    review = review_path(register)
    send = (f"run `deferral_adjudication_check.py` (it writes .context/manifests/{manifest_path(register).name}), "
            f"then send the peer `/deferral-review .context/manifests/{manifest_path(register).name}`")
    problem = _ruling_file_problem(register, data, review, send, len(rows))
    if problem:
        return [problem]
    _, table = _parse_table(review.read_text(encoding="utf-8", errors="replace"))
    given = {re.sub(r"\s*#\s*", " ", c[0]).strip().lower(): c + ["", ""] for c in table if c and len(c) >= 2}
    problems, amber = [], []
    for key, row in rows.items():
        cells = given.get(key, ["", "", "", ""])
        problem = _row_problem(key, row.item, cells, review.name, send)
        if problem:
            problems.append(problem)
        elif cells[1].strip().upper() == "AMBER":
            amber.append(key)
    problems += [f"deferral-amber: the plan does not carry the condition of {key} ({given[key][3].strip()[:80]}). "
                 f"Send /plan-update: map {key} under `{CONDITIONS_HEADER}` to the phase that builds it"
                 for key in _carried(register.parent.parent, amber)]
    return problems


def register_row_problem(context_dir: Path, session: dict | None, ticket: str) -> str | None:
    """Why ``ticket`` cannot defer a finding in review, else None. As P8 requires, the ticket matches the
    adapter pattern, and a deferral register of the run that passes adjudication has a row that names it."""
    from artifact_requirements import _in_scope, ticket_scope

    pat = ticket_pattern()
    if not pat.fullmatch(ticket.strip()):
        return f"ticket {ticket!r} does not match the adapter pattern {pat.pattern}"
    scope = ticket_scope(context_dir, session=session)
    for path in sorted((Path(context_dir) / "deferrals").glob("*_deferral-register.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rows = parse_approved_rows(text) + parse_approved_rows(text, review_phase=True)
        named = {m.group(0) for row in rows for m in pat.finditer(f"{row.ticket} {row.reason}")}
        if ((scope is None or _in_scope(path, scope)) and ticket.strip() in named
                and adjudicate_deferral_register(text).ok and not ruling_problems(path)):
            return None
    return (f"no row of an adjudicated deferral register names {ticket.strip()}, with a GREEN of /deferral-review. "
            "Add the row under `## Review deferrals` in .context/deferrals/<task>_deferral-register.md, then send "
            "/deferral-review (references/deferral-framework.md)")


def _task_register(context_dir: Path) -> Path | None:
    """The register of the run's ticket, when it exists."""
    from pipeline_run_binding import current_session

    task_id = str(current_session(context_dir).get("task_id") or "")
    name = re.sub(r"[/\\]", "-", task_id)  # no backslash in an f-string expression before Python 3.12
    path = context_dir / "deferrals" / f"{name}_deferral-register.md"
    return path if task_id and path.is_file() else None


def write_zero_register(context_dir: Path) -> Path | None:
    """The register of a run that approved no deferral, written by P8 itself.

    A deferral is a judgment: the plan peer writes its row. With no register at all, nobody
    proposed one, so the script writes the zero register. An existing register is never changed.
    """
    from pipeline_run_binding import current_session

    session = current_session(context_dir)
    task_id = str(session.get("task_id") or "")
    if not task_id:
        return None
    name = re.sub(r"[/\\]", "-", task_id)  # no backslash in an f-string expression before Python 3.12
    path = context_dir / "deferrals" / f"{name}_deferral-register.md"
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Deferral register: {task_id}\n\nTicket: {task_id}\n"
                    f"Pipeline-Run: {session.get('pipeline_run_id') or ''}\n\n"
                    "## Approved deferrals\n\nNo approved deferrals. All work ships in this PR.\n\n"
                    "## Deferrals rejected\n\nNone proposed.\n", encoding="utf-8")
    return path


def _gate(*, check: bool) -> int:
    """stages.json P8: find this ticket's register exactly as P9 does. Without --check, write the zero
    register when nobody proposed a deferral, and name the /deferral-review peer when a row needs a ruling."""
    from artifact_requirements import DEFERRAL_REGISTER, require_artifact, ticket_scope
    from gate_common import ctx, fail, ok

    context = ctx()
    errors = require_artifact(context, **DEFERRAL_REGISTER, scope=ticket_scope(context))
    if errors and not check and write_zero_register(context) is not None:
        errors = require_artifact(context, **DEFERRAL_REGISTER, scope=ticket_scope(context))
    register = _task_register(context)
    if not check and register is not None and any("deferral-review:" in e for e in errors):
        print(f"NEXT: send the peer `/deferral-review .context/manifests/"
              f"{write_review_manifest(register, Path.cwd()).name}`")
    return fail("deferral-register", errors) if errors else ok("deferral-register")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate deferral register adjudication")
    parser.add_argument(
        "path",
        nargs="?",
        help="Deferral register markdown path (default: this ticket's register under .context/deferrals/)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    parser.add_argument("--check", action="store_true", help="Gate mode (stages.json P8)")
    args = parser.parse_args()

    if not args.path:
        return _gate(check=args.check)
    try:
        text = Path(args.path).read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    result = adjudicate_deferral_register(text)
    payload = {
        "ok": result.ok,
        "approved_count": result.approved_count,
        "issues": [
            {"code": i.code, "message": i.message, "recovery": i.recovery}
            for i in result.issues
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if result.ok:
            print(f"OK  deferral adjudication ({result.approved_count} approved rows)")
        else:
            for issue in result.issues:
                print(f"FAIL  {issue.code}: {issue.message}", file=sys.stderr)
                if issue.recovery:
                    print(f"      -> {issue.recovery}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
