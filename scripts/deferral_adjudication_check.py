#!/usr/bin/env python3
"""Validate deferral register adjudication for /cadence (P8).

Cadence: ticket regex from adapters/*.json; no hardcoded org prefixes.

Car-apart default: ship in-PR unless ripeness documents a landing dependency.
Exit 0 when ok; exit 1 with JSON issues on stdout when invoked as CLI.
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

LEGITIMATE_RIPENESS = re.compile(
    r"(?i)(blocked[- ]on|blocked[- ]by|different epic|different layer|"
    r"prod[- ]mutation|--execute|fleet[- ]ops|operator must|parent product gap|"
    r"engine redesign|explicitly out of scope|vibe-test|ripeness:\s*"
    r"(blocked-by|prod-mutation|different-epic|different-layer|fleet-ops))"
)

WEAK_DEFERRAL = re.compile(
    r"(?i)(\bnits?\b|PR-review|TB nits|YAGNI|out of AC|not needed to unblock|"
    r"test hygiene|optional polish|residual PR-review|pre-rollout|"
    r"extra parse/partition plumbing not needed)"
)

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
    ripeness_i = _col_index(header, "ripeness")
    item = cells[item_i] if item_i < len(cells) else ""
    reason = cells[reason_i] if reason_i < len(cells) else ""
    ticket = cells[ticket_i] if ticket_i < len(cells) else ""
    ripeness = (
        cells[ripeness_i] if ripeness_i is not None and ripeness_i < len(cells) else ""
    )
    return DeferralRow(
        number=num.strip(),
        item=item,
        reason=reason,
        ticket=ticket,
        ripeness=ripeness.strip().lower(),
        raw="|".join(cells),
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

    if row.ripeness:
        if row.ripeness in INVALID_RIPENESS:
            issues.append(
                AdjudicationIssue(
                    "deferral-invalid-ripeness",
                    f"{prefix}: Ripeness '{row.ripeness}' is not allowed — use blocked-by, "
                    "prod-mutation, different-epic, different-layer, or fleet-ops",
                    "Ship in-PR or document blocked-by with landing dependency ticket",
                )
            )
        elif row.ripeness not in VALID_RIPENESS:
            issues.append(
                AdjudicationIssue(
                    "deferral-unknown-ripeness",
                    f"{prefix}: unknown Ripeness '{row.ripeness}'",
                    f"Use one of: {', '.join(sorted(VALID_RIPENESS))}",
                )
            )
        return issues

    combined = f"{row.item} {row.reason}"
    weak = bool(WEAK_DEFERRAL.search(combined))
    legit = bool(LEGITIMATE_RIPENESS.search(combined))
    if weak and not legit:
        issues.append(
            AdjudicationIssue(
                "deferral-weak-ripeness",
                f"{prefix}: convenience deferral ({row.item[:60]}...) — car-apart default is in-PR",
                "Add Ripeness column (blocked-by + ticket) or move to Deferrals rejected",
            )
        )
    elif not legit and re.search(r"(?i)out of (ac|scope)", combined):
        issues.append(
            AdjudicationIssue(
                "deferral-out-of-ac-without-ripeness",
                f"{prefix}: 'out of AC/scope' without ripeness — prove different epic/layer or blocked-by",
                "Ship in-PR via /spec-driven or set Ripeness to different-epic/different-layer/blocked-by",
            )
        )
    return issues


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


def register_row_problem(context_dir: Path, session: dict | None, ticket: str) -> str | None:
    """gh-178: why ``ticket`` cannot defer a finding in review, else None. As P8 requires, the ticket matches the
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
        if (scope is None or _in_scope(path, scope)) and ticket.strip() in named and adjudicate_deferral_register(text).ok:
            return None
    return (f"no row of an adjudicated deferral register names {ticket.strip()}. Add the row under `## Review deferrals` "
            "in .context/deferrals/<task>_deferral-register.md (references/deferral-framework.md)")


def write_zero_register(context_dir: Path) -> Path | None:
    """The register of a run that approved no deferral, written by P8 itself (ADR-0018).

    A deferral is a judgment: the plan peer writes its row. With no register at all, nobody
    proposed one, so the script writes the zero register. An existing register is never changed.
    """
    from pipeline_run_binding import current_session

    session = current_session(context_dir)
    task_id = str(session.get("task_id") or "")
    if not task_id:
        return None
    path = context_dir / "deferrals" / f"{re.sub(r'[/\\]', '-', task_id)}_deferral-register.md"
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Deferral register: {task_id}\n\nTicket: {task_id}\n"
                    f"Pipeline-Run: {session.get('pipeline_run_id') or ''}\n\n"
                    "## Approved deferrals\n\nNo approved deferrals. All work ships in this PR.\n\n"
                    "## Deferrals rejected\n\nNone proposed.\n", encoding="utf-8")
    return path


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
        # stages.json P8: find this ticket's register exactly as P9 does.
        from artifact_requirements import DEFERRAL_REGISTER, require_artifact, ticket_scope
        from gate_common import ctx, fail, ok

        context = ctx()
        errors = require_artifact(context, **DEFERRAL_REGISTER, scope=ticket_scope(context))
        if errors and not args.check and write_zero_register(context) is not None:
            errors = require_artifact(context, **DEFERRAL_REGISTER, scope=ticket_scope(context))
        return fail("deferral-register", errors) if errors else ok("deferral-register")
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
