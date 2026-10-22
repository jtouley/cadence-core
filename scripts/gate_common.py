#!/usr/bin/env python3
"""Shared CLI helpers for thin Cadence stage gates."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import (  # noqa: E402
    REVIEW_RIGOR_SECTIONS,
    check_plan_create_artifacts,
    _all_of,
    adversarial_note,
    check_adversarial_verdict,
    file_matches_markers,
    require_artifact,
    peer_written_filter,
    plan_review_step,
    require_lean_delegation,
    stage_rejections,
    require_sections,
    review_must_fix_ids,
    run_scope_accept,
)
from peer_models import require_peer_model  # noqa: E402
from pipeline_run_binding import other_workspace_hint, run_for  # noqa: E402

# Planning gates count only this run's artifacts.
_RUN_SCOPED = frozenset({"validate-gate", "plan-review-gate", "adversarial-review-gate"})


_LOCAL_SHARED = Path(__file__).resolve().parent.parent / "shared" / "scripts"
if _LOCAL_SHARED.is_dir() and str(_LOCAL_SHARED) not in sys.path:
    sys.path.insert(0, str(_LOCAL_SHARED))

try:
    from context_root import context_dir as canonical_context_dir  # type: ignore
except ImportError:  # pragma: no cover

    def canonical_context_dir(cwd=None):
        return Path.cwd() / ".context"


ARTIFACT_GATES: dict[str, dict] = {
    "validate-gate": {
        "globs": ("vibetest/*.md",),
        "markers": ("RUN_BY_SKILL: vibe-test",),
        "label": "vibe-test",
        "stage_slug": "validate",
    },
    "plan-review-gate": {
        "globs": ("reviews/plan_*.md",),
        "markers": ("RUN_BY_COMMAND: plan-review",),
        "label": "plan-review",
        "stage_slug": "plan-review",
    },
    "adversarial-review-gate": {
        "globs": ("adversarial/adversarial_*.md",),
        "markers": ("RUN_BY_SKILL: de-adversarial-reviewer",),
        "label": "adversarial-review",
        "delegation_stage": "de-adversarial-reviewer",
        "stage_slug": "adversarial-review",
    },
    "pr-review-local-gate": {
        "globs": ("prs/reviews/*.md", "reviews/pr_*.md"),
        "markers": ("RUN_BY_COMMAND: pr-review-local", "RUN_BY_SKILL: pr-review"),
        "label": "pr-review-local",
        "sections": REVIEW_RIGOR_SECTIONS,
        "stage_slug": "pr-review-local",
    },
}


def current_branch(cwd: Path | None = None) -> str | None:
    """The branch of the worktree at ``cwd``; None reads the process cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def ctx() -> Path:
    return canonical_context_dir()


def ledger_gate_args(description: str) -> tuple[argparse.Namespace, Path]:
    """`--ledger` and `--check` of the ledger gates (R4, R5), and the ledger path.

    stages.json and pr-review-update.md run them without --ledger. --ledger changes the
    file, never the scope: the run of the cwd worktree decides which rows count.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--ledger", default=None,
                        help="Path to findings jsonl (default: findings.jsonl in the canonical .context)")
    parser.add_argument("--check", action="store_true", help="Check only")
    args = parser.parse_args()
    return args, Path(args.ledger) if args.ledger else ctx() / "findings.jsonl"


def ok(slug: str, note: str | None = None) -> int:
    if note:
        print(f"OK  {slug}  {note}")
    else:
        print(f"OK  {slug}")
    return 0


def measure_record(measure: str, value: float, threshold: float, offenders: list[str], *, failed: bool,
                   threshold_source: str, unmeasured: list[str] | None = None, unsupported: str = "warn") -> dict:
    """One measure of R2 or R3 with exactly one verdict. A peer reads `verdict`; it does not judge again.

    fail: `failed`, or a file the measure cannot read under `unsupported: fail`. warn: the value is over the
    threshold with no new offender (old debt that did not grow), or a file the measure cannot read.
    """
    unmeasured = list(unmeasured or [])
    if failed or (unmeasured and unsupported == "fail"):
        verdict = "fail"
    else:
        verdict = "warn" if value > threshold or unmeasured else "pass"
    return {"measure": measure, "value": value, "threshold": threshold, "threshold_source": threshold_source,
            "verdict": verdict, "offenders": offenders, "unmeasured": unmeasured}


def fail(slug: str, errors: list[str], code: int = 1) -> int:
    print(f"FAIL  {slug}", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return code


# The stop guard walks the gates on every stop attempt, and a stop must not change
# pipeline state. With this variable set to 1 a gate records no breadcrumb. It does not
# touch the P9 marker: a failed P9 must always clear it (see test_gates_readonly.py).
GATES_READONLY_ENV = "CADENCE_GATES_READONLY"


def write_gate_breadcrumb(
    slug: str,
    extra: dict | None = None,
    *,
    context_dir: Path | None = None,
) -> Path:
    context = context_dir if context_dir is not None else ctx()
    gates = context / "gates"
    path = gates / f"{slug}.json"
    if os.environ.get(GATES_READONLY_ENV) == "1":
        return path
    gates.mkdir(parents=True, exist_ok=True)
    payload = {"slug": slug, "RUN_BY_SCRIPT": Path(sys.argv[0]).name, **(extra or {})}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _p3_reopen(context_dir: Path) -> list[str]:
    """After /plan-update, a blocking review older than the plan opens P3 again."""
    step = plan_review_step(context_dir)
    return [step[1]] if step is not None and step[0] == "P3" else []


def _r1_review(context_dir: Path) -> tuple[Path | None, list[str]]:
    """(the review R1 judges, problems): the review of the run's PR when the E4 record names one, as R4
    reads it (R1 N-13), else the newest review the peer filter accepts. (None, []) with no review."""
    from final_consolidate import run_pr_review
    from implement_gate import run_worktree

    spec = ARTIFACT_GATES["pr-review-local-gate"]
    accept = peer_written_filter(context_dir, slug=spec["stage_slug"])
    reviews = [
        p for g in spec["globs"] for p in context_dir.glob(g)
        if p.is_file() and file_matches_markers(p, spec["markers"]) and (accept is None or accept(p))
    ]
    if not reviews:
        return None, []  # the artifact check fails then
    own = run_pr_review(context_dir, run_worktree(context_dir))
    if own is not None and own not in reviews:
        return None, [f"pr-review-local: no peer-written review of this run's PR at {own.relative_to(context_dir).as_posix()}; "
                      "run /pr-review-local on the PR"]
    return (own if own is not None else max(reviews, key=lambda p: p.stat().st_mtime)), []


def _r1_problems(context_dir: Path) -> list[str]:
    """The review R1 judges cites HEAD (R4 is in a pack that is off by default),
    and each of its must-fix items has a ledger row."""
    review, problems = _r1_review(context_dir)
    if review is None:
        return problems
    return _r1_head_problems(context_dir, review) + _r1_ledger_problems(context_dir, review)


def _r1_head_problems(context_dir: Path, review: Path) -> list[str]:
    from final_consolidate import head_coverage
    from implement_gate import run_worktree

    worktree = run_worktree(context_dir)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True).stdout.strip()
    if not head:
        return [f"pr-review-local: no git HEAD in the run's worktree {worktree}"]
    found = head_coverage(review, head)
    if found is None:
        return []
    return [
        f"pr-review-local: the newest review {review.relative_to(context_dir).as_posix()} {found}, "
        f"HEAD is {head}. Run /pr-review-update to review the commits up to HEAD"
    ]


# The R1 verdicts (commands/pr-review-local.md). The longest option wins, so MERGE-READY is not NO MERGE.
R1_VERDICTS = ("NO MERGE", "NO-MERGE", "MERGE-READY", "MERGE READY")


def _r1_ledger_problems(context_dir: Path, review: Path) -> list[str]:
    """Each `MF-n` of the review has a must-fix row `<task prefix>-MF-n` in any status, as R2 needs a
    row for each must-fix (test_benchmark_gate). R4 and R5 read only the ledger. A NO MERGE verdict with no
    such row fails too."""
    from findings_io import FindingsValidationError, ledger_must_fix_ids, r1_row_id
    from verdict_judge import judged_verdict

    run = run_for(context_dir) or {}
    task, run_id = str(run.get("task_id") or ""), run.get("pipeline_run_id")

    def want(n: str) -> str:  # the row of this run: a row of an earlier run of the task does not count (B-2)
        return r1_row_id(task, run_id, n)

    try:
        rows = {row_id.lower() for row_id in ledger_must_fix_ids(context_dir)}
    except FindingsValidationError as exc:
        return [f"pr-review-local: the findings ledger is unreadable, so the must-fix rows cannot be checked: {exc}"]
    text = review.read_text(encoding="utf-8", errors="replace")
    rel = review.relative_to(context_dir).as_posix()
    problems = [
        f"pr-review-local: must-fix item {mf} of {rel} has no ledger row {want(mf.split('-')[1])} (findings_io.py append)"
        for mf in sorted(review_must_fix_ids(text), key=lambda i: int(i.split("-")[1]))
        if want(mf.split("-")[1]).lower() not in rows
    ]
    if judged_verdict(text, "pr-review", R1_VERDICTS) in ("NO MERGE", "NO-MERGE") and not any(r.startswith(want("").lower()) for r in rows):
        problems.append(f"pr-review-local: the verdict of {rel} is NO MERGE and the findings ledger has no ledger row "
                        f"{want('<n>')}. Append one must-fix row for each must-fix item (findings_io.py append)")
    return problems


# The gates that read a verdict after the artifact check, and R1 reads the reviewed commit.
def _a5_gaps(context_dir: Path) -> list[str]:
    """A NOT READY vibe-test report labels each blocking gap `G-n`, so the plan can answer it."""
    from artifact_requirements import BLOCKING_GAPS_HEADER, newest_vibetest, spec_gap_ids

    report = newest_vibetest(context_dir, run_for(context_dir, cwd=Path.cwd()))
    text = report.read_text(encoding="utf-8") if report is not None else ""
    from verdict_judge import judged_verdict

    if judged_verdict(text, "vibe-test") == "NOT READY" and not spec_gap_ids(text):
        return [f"vibe-test: {report.name} is not ready for planning and lists no `G-n` under "
                f"`{BLOCKING_GAPS_HEADER}`. Send /vibe-test again"]
    return []


_VERDICT_CHECKS = {
    "adversarial-review-gate": lambda ctx: check_adversarial_verdict(ctx, stage="p6"),
    "plan-review-gate": _p3_reopen,
    "pr-review-local-gate": _r1_problems,
    "validate-gate": _a5_gaps,
}


def run_artifact_gate(slug: str, context_dir: Path, *, check: bool = False, worktree: Path | None = None) -> int:
    spec = ARTIFACT_GATES[slug]
    validate = require_sections(spec["sections"]) if "sections" in spec else None
    errors: list[str] = []
    if slug == "validate-gate":
        # A5 judges the run of its own worktree, never the root pointer.
        wt = worktree or Path.cwd()
        session = run_for(context_dir, cwd=wt)
        if session is None:
            hint = other_workspace_hint(context_dir, wt)
            errors.append("vibe-test: no Cadence run for this worktree." + (f" {hint[:1].upper()}{hint[1:]}." if hint else ""))
        where = {"cwd": wt, "session": session}
        accept = _all_of(
            peer_written_filter(context_dir, **where, slug=spec["stage_slug"]),
            run_scope_accept(context_dir, session=session) if session is not None and slug in _RUN_SCOPED else None,
        )
    else:
        where = {}
        accept = _all_of(
            peer_written_filter(context_dir, slug=spec["stage_slug"]),
            run_scope_accept(context_dir) if slug in _RUN_SCOPED else None,
        )
    errors.extend(require_artifact(
        context_dir,
        relative_globs=spec["globs"],
        markers=spec["markers"],
        label=spec["label"],
        validate=validate,
        accept=accept,
        rejections=lambda paths: stage_rejections(context_dir, paths, **where, slug=spec["stage_slug"]),
    ))
    if slug == "validate-gate":
        from tracker_header import marked_report_header_errors

        root = Path(__file__).resolve().parent.parent
        # Every report A5 accepts also has its header judged.
        errors.extend(marked_report_header_errors(context_dir, root, session, accept=accept))
    errors.extend(
        require_lean_delegation(context_dir, spec.get("delegation_stage", spec["label"]))
    )
    errors.extend(_VERDICT_CHECKS.get(slug, lambda _ctx: [])(context_dir))
    note = adversarial_note(context_dir) if slug == "adversarial-review-gate" and not errors else None
    if errors:
        return fail(slug, errors)
    if not check:
        write_gate_breadcrumb(slug, context_dir=context_dir)
    return ok(slug, note)


def run_plan_create_gate(
    context_dir: Path, *, check: bool = False, worktree: Path | None = None
) -> int:
    worktree = worktree or Path.cwd()
    errors = check_plan_create_artifacts(context_dir, worktree, accept=run_scope_accept(context_dir, worktree))
    errors += require_peer_model(context_dir, "plan-create")
    if not errors:  # the plan comes with the checks that prove the request
        from acceptance import problems as acceptance_problems

        errors += acceptance_problems(context_dir, worktree=worktree)
    if errors:
        return fail("plan-create-gate", errors)
    if not check:
        write_gate_breadcrumb("plan-create-gate", context_dir=context_dir)
    return ok("plan-create-gate")


def cli(run_fn) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return run_fn(ctx(), check=args.check)
