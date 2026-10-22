#!/usr/bin/env python3
"""P7 / R2 — test-benchmark peer gate.

Both modes fail on a scorecard that does not pass. In code mode the gate also computes the DRY
lists of the changed test files itself (scripts/test_shape.py) and fails on each list
that is over its limit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import (  # noqa: E402
    peer_written_filter,
    require_artifact,
    require_lean_delegation,
    stage_rejections,
)
import test_shape  # noqa: E402
from findings_io import FindingsValidationError, ledger_must_fix_ids  # noqa: E402
from gate_common import ctx, fail, ok, write_gate_breadcrumb  # noqa: E402

SLUG = "test-benchmark-gate"
_MARKERS = ("RUN_BY_SKILL: test-benchmark",)
_MODES = {
    "plan": {"globs": ("benchmarks/*_plan.json",), "label": "test-benchmark-plan", "stage_slug": "test-benchmark-plan"},
    "code": {
        "globs": ("benchmarks/*_code.json", "benchmarks/*benchmark*.json"),
        "label": "test-benchmark-code",
        "stage_slug": "test-benchmark-pr",
    },
}


_ENTRY_KEYS = ("input", "expected", "observed")


def _card_value(path: Path, key: str) -> object:
    """One key of the scorecard, or None when the card or the key cannot be read."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key)
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _adversarial_input_problems(path: Path) -> list[str]:
    """R2 rigor (references/review-rigor.md): a non-empty list of complete entries."""
    entries = _card_value(path, "adversarial_inputs")
    complete = (
        isinstance(entries, list)
        and bool(entries)
        and all(
            isinstance(e, dict)
            and all(e.get(k) is not None and str(e[k]).strip() for k in _ENTRY_KEYS)
            for e in entries
        )
    )
    problems = [] if complete else [
        f"no adversarial_inputs entries with {list(_ENTRY_KEYS)} (references/review-rigor.md)"
    ]
    return problems + _score_problems(path) + _must_fix_ledger_problems(path)


DRY_MEASUREMENT_KEYS = ("helpers_reimplemented", "helpers_reused", "thin_files")


def _shape_problems(shape: dict | str) -> list[str]:
    """Each entry of a computed list that is over its limit. No change to read fails closed."""
    if isinstance(shape, str):
        return [f"test shape cannot be measured: {shape}"]
    lines = [f"{m['measure']}: {entry}" for m in shape["measures"] if m["verdict"] == "fail"
             for entry in m["offenders"] or m["unmeasured"]]  # an unsupported layout fails with no offender
    if lines:
        lines.append("scripts/test_shape.py computes these lists: import the conftest helper, and add the "
                     "tests of a thin file to the test file of its module")
    return lines


def _entry_key(entry: object) -> str:
    return str(entry).split(" (")[0].strip()


def _dry_mismatch(path: Path, shape: dict | str) -> list[str]:
    """The peer's optional dry_measurement against the computed lists. A note only: it does not change the verdict."""
    dry = _card_value(path, "dry_measurement")
    if dry is None or isinstance(shape, str):
        return []
    if not (isinstance(dry, dict) and all(isinstance(dry.get(k), list) for k in DRY_MEASUREMENT_KEYS)):
        return [f"dry_measurement is not three lists {list(DRY_MEASUREMENT_KEYS)}; R2 uses the computed lists"]
    computed = {m["measure"]: m["offenders"] for m in shape["measures"]} | {"helpers_reused": shape["helpers_reused"]}
    notes = []
    for key in DRY_MEASUREMENT_KEYS:
        peer, gate = {_entry_key(e) for e in dry[key]}, {_entry_key(e) for e in computed[key]}
        if peer != gate:
            notes.append(f"dry_measurement.{key} differs from the computed list: "
                         f"peer only {sorted(peer - gate)}, gate only {sorted(gate - peer)}")
    return notes


def score_floor() -> int:
    """`benchmark_score_floor` of the resolved config. A layer can only raise it."""
    import cadence_config

    return cadence_config.load(Path(__file__).resolve().parent.parent).get("benchmark_score_floor")


MUST_FIX_DIMENSIONS = ("layer", "dry", "assert")


# Keys that a peer writes next to the dimensions in `scores`: the gate computes them itself (the corpus of real
# scorecards in tests/fixtures/peer_reports puts `mean`, `floor` and `must_fix_dimensions` there).
_NOT_DIMENSIONS = ("mean", "floor", "average", "total", "overall")


def dimension_scores(card: object) -> dict[str, float]:
    """The numeric dimension scores of a scorecard; any other key of `scores` is ignored."""
    scores = card.get("scores") if isinstance(card, dict) else None
    return {k: v for k, v in (scores or {}).items() if isinstance(scores, dict)
            and isinstance(v, (int, float)) and not isinstance(v, bool) and k.lower() not in _NOT_DIMENSIONS}


def card_shape_problems(path: Path) -> list[str]:
    """The gate can read the scorecard: it is JSON with at least one numeric dimension score."""
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"{path.name} is not readable JSON"]
    if not dimension_scores(card):
        return [f"{path.name} has no dimension scores (skills/test-benchmark/SKILL.md: dimensions 1-10)"]
    return []


def _score_problems(path: Path) -> list[str]:
    """R2 and P7 pass on a scorecard with no must-fix dimension below `benchmark_score_floor` and no FAIL verdict.

    A low layer, DRY or assertion score is a must-fix: the gate measures DRY. The mean is not
    a gate: a low mean names no defect, and runs sent fix rounds at a mean that did
    not move. A defect is a finding with its evidence, and the ledger holds it.
    """
    shape = card_shape_problems(path)
    if shape:
        return shape
    card = json.loads(path.read_text(encoding="utf-8"))
    scores = dimension_scores(card)
    problems = []
    # skills/test-benchmark/SKILL.md: below 7 is must-fix for layer, DRY and assertions.
    floor = score_floor()
    must = {k: v for k, v in scores.items() if any(w in k.lower() for w in MUST_FIX_DIMENSIONS) and v < floor}
    if must:
        problems.append(f"must-fix dimensions below {floor}: {must}. Fix the tests, then score again")
    verdict = card.get("verdict")
    if isinstance(verdict, str) and verdict.strip().upper() != "PASS":
        from verdict_judge import judge

        if (verdict.strip().upper() == "FAIL" or judge(verdict, "scorecard") != "PASS"):  # `PASSED` is a pass (Jev)
            problems.append(f"verdict is {verdict}")
    return problems


_MUST_FIX_SEVERITIES = {"must-fix", "high", "critical"}


def _is_must_fix(finding: object) -> bool:
    """`must_fix: true`, or a severity of must-fix, HIGH or critical (skills/test-benchmark)."""
    if not isinstance(finding, dict):
        return False
    severity = str(finding.get("severity", "")).strip().lower()
    return _flag_true(finding.get("must_fix")) or severity in _MUST_FIX_SEVERITIES


def _flag_true(value: object) -> bool:
    """`true`, `1`, `"true"` or `"yes"` (any case). JSON writers do not agree on one form."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in ("true", "yes")


def _must_fix_ledger_problems(path: Path) -> list[str]:
    """Each must-fix in the scorecard has a must-fix row in the findings ledger.

    R4 and R5 read the ledger only. A finding that lives only in the scorecard
    JSON has no owner and cannot block closure. A row that is already fixed,
    verified or deferred still counts, so a rescore does not reopen R2.
    """
    findings = _card_value(path, "findings")
    must = [f for f in findings if _is_must_fix(f)] if isinstance(findings, list) else []
    if not must:
        return []
    try:
        ledger_ids = ledger_must_fix_ids(path.parent.parent)
    except FindingsValidationError as exc:
        return [f"findings ledger is unreadable, so must-fix rows cannot be checked: {exc}"]
    return [
        f"must-fix finding {f.get('id')!r} has no must-fix row in the findings ledger (findings_io.py append)"
        for f in must
        if f.get("id") not in ledger_ids
    ]


def _plan_problems(path: Path, *, answered: bool = False) -> list[str]:
    """P7 passes only on a passing plan scorecard. A plan finding has no ledger row: a rescore of the
    updated plan closes it, because the plan freezes at P9 and no commit can fix it after that. At its cycle
    limit (``answered``: plan_wave.answered_at_limit) P7 reviews no more, and the update answered its rows."""
    if card_shape_problems(path):  # the card, not the plan, is wrong: the P7 peer writes it again
        return card_shape_problems(path) + ["send /test-benchmark plan <plan> again"]
    if answered:
        return []
    problems = _score_problems(path)
    return problems + [f"send /plan-update <plan>: the plan peer applies the must-fix findings of {path.name}. "
                       "Then send /test-benchmark plan <plan> again"] if problems else []


def _plan_validator(context_dir: Path):
    from plan_wave import answered_at_limit

    answered = answered_at_limit(context_dir, None, "test-benchmark-plan")
    return lambda path: _plan_problems(path, answered=answered)


def _code_validator(shape: dict | str, notes: list[str]):
    def validate(path: Path) -> list[str]:
        notes[:] = _dry_mismatch(path, shape)
        return _adversarial_input_problems(path)
    return validate


def run(context_dir: Path, *, mode: str = "plan", check: bool = False) -> int:
    spec = _MODES[mode]
    slug = f"{SLUG}-{mode}"
    shape = test_shape.for_change(context_dir) if mode == "code" else None  # None in plan mode
    notes: list[str] = []
    errors = require_artifact(
        context_dir,
        relative_globs=spec["globs"],
        markers=_MARKERS,
        label=spec["label"],
        validate=_code_validator(shape, notes) if shape is not None else _plan_validator(context_dir),
        accept=peer_written_filter(context_dir, slug=spec["stage_slug"]),
        rejections=lambda paths: stage_rejections(context_dir, paths, slug=spec["stage_slug"]),
    )
    errors.extend(require_lean_delegation(context_dir, spec["stage_slug"]))
    errors.extend(_shape_problems(shape) if shape is not None else [])
    if errors:
        return fail(slug, errors)
    extra = {"mode": mode, "test_shape": shape, "dry_mismatch": notes} if shape is not None else {"mode": mode}
    if not check:
        write_gate_breadcrumb(slug, extra, context_dir=context_dir)
    if isinstance(shape, dict):  # a str shape failed above in _shape_problems
        counts = ", ".join(f"{m['measure']} {m['value']} ({m['verdict']})" for m in shape["measures"])
        return ok(slug, "; ".join([f"{counts}, helpers_reused {len(shape['helpers_reused'])}", *notes]))
    return ok(slug)


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 / R2 — test-benchmark peer gate.")
    parser.add_argument("--mode", choices=("plan", "code"), default="plan")
    parser.add_argument("--plan", action="store_true", help="Alias for --mode plan")
    parser.add_argument("--code", action="store_true", help="Alias for --mode code")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mode = "code" if args.code else ("plan" if args.plan else args.mode)
    return run(ctx(), mode=mode, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
