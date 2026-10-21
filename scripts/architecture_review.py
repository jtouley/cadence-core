#!/usr/bin/env python3
"""P5 / E3 architecture review — core rules 1–4 (Gap 7).

Writes findings to the ledger with ``rule_ref`` values from
``references/architecture-core-rules.md``.

ORIGIN: original for cadence
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from decision_frontmatter import (  # noqa: E402
    SHAREABLE_DECISION_DIRS,
    ContextIndexError,
    parse_decision_frontmatter,
)
from artifact_requirements import ticket_norm, ticket_token_re  # noqa: E402
from findings_io import (  # noqa: E402
    append_finding,
    load_findings,
    rewrite_findings,
    rows_of_task,
    task_id_prefix,
    task_row_pattern,
)
from gate_common import GATES_READONLY_ENV, write_gate_breadcrumb  # noqa: E402
from ledger_verify import supersede_finding  # noqa: E402
from pipeline_run_binding import current_session, worktree_run_session  # noqa: E402

_SHARED = Path(__file__).resolve().parent.parent / "shared" / "scripts"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
from context_root import context_path  # noqa: E402

SKILL_ROOT = Path(
    os.environ.get("CADENCE_HOME")
    or os.environ.get("CADENCE_SKILL_ROOT")
    or Path(__file__).resolve().parent.parent
)

PHASE_TO_STAGE = {
    "plan": "architecture-review-plan",
    "impl": "architecture-review-impl",
}
PHASE_TO_BLOCK = {"plan": "P5", "impl": "E3"}

CORE_RULES = (
    "core:dependency-direction",
    "core:boundary-integrity",
    "core:decision-coverage",
    "core:plan-conformance",
)


def _load_config(skill_root: Path, workspace: Path | None = None) -> dict[str, Any]:
    import cadence_config

    return cadence_config.load(skill_root, workspace).values


def _has_architecture_docs(worktree: Path, context_dir: Path) -> bool:
    candidates = [
        worktree / "docs" / "edrs",
        worktree / "docs" / "adr",
        worktree / "docs" / "adrs",
        worktree / ".context" / "proposals",
        context_dir / "proposals",
        worktree / "ARCHITECTURE.md",
        worktree / "docs" / "architecture",
    ]
    for c in candidates:
        if c.is_file():
            return True
        if c.is_dir() and any(c.iterdir()):
            return True
    return False




def _iter_decision_doc_paths(worktree: Path, context_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for rel in SHAREABLE_DECISION_DIRS:
        folder = worktree.joinpath(*rel)
        if folder.is_dir():
            paths.extend(
                sorted(
                    p
                    for p in folder.rglob("*")
                    if p.is_file() and p.suffix.lower() in {".md", ".markdown"}
                )
            )
    seen: set[Path] = set()
    for folder in {context_dir / "proposals", worktree / ".context" / "proposals"}:
        if not folder.is_dir():
            continue
        resolved = folder.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.extend(sorted(p for p in folder.glob("*.md") if p.is_file()))
    return paths


def _live_decision_texts(worktree: Path, context_dir: Path):
    """Text of each ADR/proposal whose frontmatter is a proposed|accepted Decision."""
    for path in _iter_decision_doc_paths(worktree, context_dir):
        try:
            text = path.read_text(encoding="utf-8")
            parsed = parse_decision_frontmatter(text, source=path)
        except (ContextIndexError, OSError):
            continue
        if parsed is not None and parsed.get("status") in {"proposed", "accepted"}:
            yield text


def _frontmatter_stub_exists(worktree: Path, context_dir: Path) -> bool:
    return any(True for _ in _live_decision_texts(worktree, context_dir))


def _decision_mentions_task(worktree: Path, context_dir: Path, task_id: str) -> bool:
    token = ticket_token_re(task_id)
    return any(token.search(ticket_norm(t)) for t in _live_decision_texts(worktree, context_dir))


def _decision_coverage_satisfied(
    worktree: Path, context_dir: Path, cfg: dict[str, Any]
) -> bool:
    if cfg.get("decision_frontmatter_required", True) is False:
        return _has_architecture_docs(worktree, context_dir)
    return _frontmatter_stub_exists(worktree, context_dir)


def _has_plan_artifact(context_dir: Path, worktree: Path) -> bool:
    cursor_plans = worktree / ".cursor" / "plans"
    if cursor_plans.is_dir() and any(cursor_plans.glob("*.plan.md")):
        return True
    context_plans = context_dir / "plans"
    if context_plans.is_dir() and any(context_plans.glob("*.plan.md")):
        return True
    if (context_dir / "plan.md").is_file():
        return True
    return False


def _write_notice(context_dir: Path, stage: str) -> None:
    notice = context_dir / "architecture-review-notice.json"
    context_dir.mkdir(parents=True, exist_ok=True)
    notice.write_text(
        json.dumps(
            {
                "stage": stage,
                "skipped_rules": [
                    "core:dependency-direction",
                    "core:boundary-integrity",
                ],
                "reason": "layer_map is empty in the config",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_architecture_review(
    *,
    phase: str,
    worktree: Path,
    context_dir: Path,
    findings_path: Path | None = None,
    skill_root: Path | None = None,
    task_id: str = "CADENCE",
    record: bool = True,
) -> list[dict[str, Any]]:
    """Findings of the P5 or E3 review. In record mode they go to the ledger.

    A record-mode pass with no must-fix finding closes this stage's own earlier
    must-fix rows ("Supersede"). A non-blocking row that the ledger
    already holds open is not appended again.
    """
    if phase not in PHASE_TO_STAGE:
        raise ValueError(f"phase must be plan|impl, got {phase!r}")
    stage = PHASE_TO_STAGE[phase]
    root = Path(skill_root) if skill_root is not None else SKILL_ROOT
    cfg = _load_config(root, worktree)
    layer_map = cfg.get("layer_map") or {}
    ledger = findings_path or (context_dir / "findings.jsonl")

    # The id prefix and the own-row rule are findings_io's: one copy of each.
    id_prefix = task_id_prefix(task_id)
    findings: list[dict[str, Any]] = []
    # P5 and E3 append to one ledger. Continue after this task's highest id, so that
    # set-status (one match only) can close each finding.
    own = task_row_pattern(task_id)
    earlier = load_findings(ledger)
    seq = 1 + max((int(re.findall(r"\d+$", row["id"])[0]) for row in rows_of_task(earlier, task_id)), default=0)
    gate = f"python3 $CADENCE_HOME/scripts/architecture_review.py --phase {phase} --check"

    def emit(
        rule_ref: str,
        *,
        evidence: str,
        severity: str = "must-fix",
        finding_class: str = "spec-gap",
        location: str = "architecture",
        check: str = "manual",
    ) -> None:
        nonlocal seq
        if severity == "must-fix":
            check = gate  # a later pass of this gate closes the row (supersede)
        else:
            repeat = next((
                r for r in earlier
                if own.match(r["id"]) and r.get("stage") == stage and r.get("rule_ref") == rule_ref
                and r.get("evidence") == evidence and r.get("status") == "open" and r.get("severity") != "must-fix"
            ), None)
            if repeat is not None:
                findings.append(repeat)
                return
        row = {
            "id": f"{id_prefix}-F{seq}",
            "stage": stage,
            "rule_ref": rule_ref,
            "class": finding_class,
            "severity": severity,
            "location": location,
            "evidence": evidence,
            "check": check,
            "status": "open",
        }
        seq += 1
        if record:
            append_finding(ledger, row)
        findings.append(row)

    # Rules 1–2 require layer_map; skip with logged notice when empty.
    # --check records nothing: the stop guard runs this gate on every stop.
    if not layer_map:
        if record:
            _write_notice(context_dir, stage)
    else:
        # Skeleton enforcement: layer_map present → emit pass notice as nit only if broken
        # v1: no static import graph yet — record coverage check for future linter hook
        emit(
            "core:dependency-direction",
            evidence="layer_map present; static dependency linter not yet wired (recorded for coverage)",
            severity="nit",
            finding_class="fast-follow",
            check="manual",
        )
        emit(
            "core:boundary-integrity",
            evidence="layer_map present; boundary integrity check deferred to pack/linter",
            severity="nit",
            finding_class="fast-follow",
            check="manual",
        )

    # Rule 3: decision coverage — always on
    if not _decision_coverage_satisfied(worktree, context_dir, cfg):
        emit(
            "core:decision-coverage",
            evidence=(
                "No indexed decision (status proposed|accepted) and no ADR/proposal "
                "frontmatter stub (decision_id, status, question, choice, rationale); "
                "Memory Bank / plan prose do not satisfy coverage"
            ),
            severity="must-fix",
            finding_class="spec-gap",
            check="manual",
        )
    elif not _decision_mentions_task(worktree, context_dir, task_id):
        # Coverage stays Boolean and unscoped (architecture-core-rules.md); this nit
        # only tells the reviewer no decision names this ticket.
        emit(
            "core:decision-coverage-scope",
            evidence=(
                f"Decision coverage is satisfied only by decisions that never mention "
                f"{task_id}; add a proposal linking this ticket if the plan makes one"
            ),
            severity="nit",
        )

    # Rule 4: plan conformance — impl only
    if phase == "impl":
        if not _has_plan_artifact(context_dir, worktree):
            emit(
                "core:plan-conformance",
                evidence="No plan artifact found for conformance check at architecture-review-impl",
                severity="must-fix",
                finding_class="spec-gap",
                check="manual",
            )

    # Always ensure decision-coverage appears when docs missing — already handled.
    # When docs exist and phase is plan with no other findings, emit a coverage OK nit
    # so the stage always writes at least one ledger row with a core: rule_ref when
    # decision-coverage was the only always-on check that "passed" silently.
    if phase == "plan" and not findings:
        # Docs present and no layer_map findings — still record decision-coverage pass
        # as a non-blocking nit for auditability (optional). Prefer empty only when
        # decision coverage passed; tests require decision-coverage when docs absent.
        pass

    # Guarantee: if still no findings on plan phase with empty layer_map and missing
    # docs, decision-coverage was emitted above. If docs present, emit decision-coverage
    # as a verified-style open nit is wrong — leave empty OK.
    # Tests for plan phase with no docs assert decision-coverage present.

    _ = CORE_RULES  # documented rule id set
    if record and os.environ.get(GATES_READONLY_ENV) != "1" and not _must_fix(findings):
        _supersede(ledger, own, stage, f"{PHASE_TO_BLOCK[phase]} pass at {_head(worktree)}")
    return findings


def _must_fix(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if f["severity"] == "must-fix"]


def _head(worktree: Path) -> str:
    import subprocess

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True)
    return proc.stdout.strip()


def _supersede(ledger: Path, own: re.Pattern[str], stage: str, passed: str) -> None:
    """Close this task's open or fixed must-fix rows of this stage: this gate has just passed.

    `verified` still comes only from ledger_verify. The row's check is this gate, so
    `supersede_finding` runs nothing. Rows of another task or stage stay as they are.
    """
    rows = load_findings(ledger)
    changed = False
    for i, row in enumerate(rows):
        if (
            own.match(row["id"]) and row.get("stage") == stage and row.get("severity") == "must-fix"
            and row.get("status") in ("open", "fixed") and row.get("check") not in (None, "", "manual")
        ):
            rows[i] = supersede_finding(row, passed)
            changed = True
    if changed:
        rewrite_findings(ledger, rows)


def pack_findings(context: Path, worktree: Path, phase: str = "impl") -> list[dict[str, Any]]:
    """The gates of the enabled packs that list `phase`, on the files of the change (R3 reads it the same way).

    Each error is a must-fix finding. The ledger does not get it: it fails the gate and blocks the breadcrumb.
    """
    from pack_loader import gate_errors, phase_packs

    packs, errors = phase_packs(SKILL_ROOT, phase, worktree)
    if packs:
        from cognitive_complexity_gate import change_under_review

        change = change_under_review(context)
        if isinstance(change, str):
            errors.append(f"packs: cannot read the change for the {phase} pack gates: {change}")
        else:
            errors += gate_errors(packs, phase, change[0], sorted([*change[3], *change[4]]))
    return [{"rule_ref": "pack-gate", "severity": "must-fix", "evidence": e} for e in errors]


def update_breadcrumb(context: Path, worktree: Path, findings: list[dict[str, Any]]) -> None:
    """E3's evidence for the PR guard: one file per run, only without a must-fix finding.

    A run with a must-fix finding deletes the breadcrumb of its own run only.
    With no session for this worktree, or with gates read-only, nothing changes.
    """
    session = worktree_run_session(context, worktree)
    if session is None or os.environ.get(GATES_READONLY_ENV) == "1":
        return
    run_id = str(session.get("pipeline_run_id") or "")
    if not run_id:
        return
    slug = f"architecture-review-impl-{run_id}"
    if _must_fix(findings):
        (context / "gates" / f"{slug}.json").unlink(missing_ok=True)
        return
    write_gate_breadcrumb(slug, {"pipeline_run_id": run_id, "head": _head(worktree)}, context_dir=context)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cadence architecture review (P5/E3)")
    parser.add_argument("--phase", choices=("plan", "impl"), required=True)
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument("--context", type=Path, default=None)
    parser.add_argument("--task-id", default=None, help="Default: task_id of the active session")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Gate mode: record no findings; exit 1 when a must-fix finding would be raised",
    )
    args = parser.parse_args()
    # The canonical .context, as activation uses: in a linked worktree the
    # run's plan and ledger are in the primary checkout. No mkdir here: --check creates nothing.
    context = args.context or context_path(args.worktree)
    # stages.json runs this without --task-id; findings must name the session's ticket.
    task_id = args.task_id or str((current_session(context, args.worktree) or {}).get("task_id") or "CADENCE")
    findings = run_architecture_review(
        phase=args.phase,
        worktree=args.worktree,
        context_dir=context,
        task_id=task_id,
        record=not args.check,
    )
    packs = pack_findings(context, args.worktree) if args.phase == "impl" else []
    findings += packs
    if args.phase == "impl":
        update_breadcrumb(context, args.worktree, findings)
    print(json.dumps({"stage": PHASE_TO_STAGE[args.phase], "findings": len(findings)}))
    blocking = _must_fix(findings) if args.check else packs  # a pack error is no ledger row, so it fails here
    for f in blocking:
        print(f"  must-fix {f['rule_ref']}: {f['evidence']}", file=sys.stderr)
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
