"""The inputs of a peer handoff, from the `inputs` of its stages.json row.

Each peer row names a list of input kinds. This module finds each kind in the run's
.context and worktree, with the rules of the gates. It gives the slash command with its
arguments and the input files that the packet pins by sha256. A required kind that it
cannot find gives one message that names the artifact and the block that writes it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The importer puts scripts/ and shared/scripts/ on sys.path (the hook, handoff.py, the tests).
import artifact_requirements
import handoff
from gate_common import current_branch
from load_stages import load_registry
from phase_ledger import select_plan

KINDS = ("ticket", "plan", "plan-review", "plan-review-previous", "pr-number", "pr-review", "manifest",
         "request", "vibetest", "plan-benchmark", "deferral-manifest",
         "deferral-ruling", "acceptance", "acceptance-repair")
# The request and the vibe-test report are pinned when they exist; A5 is in a pack.
# A plan scorecard is pinned while it fails, so that P4 applies its findings.
# The deferral ruling is pinned when it exists, so that P4 applies its RED and AMBER rows.
# Before P1 writes them, there are no acceptance checks.
# The acceptance repair log exists only when a check had a defect after P9.
OPTIONAL = frozenset({"plan-review-previous", "request", "vibetest", "plan-benchmark", "deferral-ruling", "acceptance",
                      "acceptance-repair"})

# What a missing kind is, and the stages.json slug of the block that writes it.
_MISSING = {
    "ticket": ("the ticket id of the run (task_id in the session)", "activate-session"),
    "plan": ("plan of the run in .cursor/plans/ or .context/plans/ with RUN_BY_COMMAND: plan-create", "plan-create"),
    "plan-review": ("plan review of the run with a verdict in .context/reviews/plan_<slug>.md", "plan-review"),
    "pr-number": ("draft-PR record of the run with a PR URL in .context/prs/", "draft-pr"),
    "pr-review": ("PR review of the run's PR in .context/prs/reviews/PR_REVIEW_#<N>.md", "pr-review-local"),
    "manifest": ("session manifest in .context/manifests/<task_id>.md", "session-close"),
    "deferral-manifest": ("deferral review manifest in .context/manifests/<task_id>_deferrals.md", "deferral-register"),
}


@dataclass(frozen=True)
class Run:
    """The run that a handoff is for: the skill root, its .context, its worktree and its session."""

    root: Path
    ctx: Path
    workspace: Path
    session: dict[str, Any]


@dataclass
class Resolved:
    command: str
    inputs: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


Found = tuple[list[str], Path | None]  # (the command arguments, the file to pin)


def _ticket(run: Run) -> Found | None:
    ticket = str(run.session.get("task_id") or "").strip()
    return ([ticket], None) if ticket else None


def _plan(run: Run) -> Found | None:
    plan = select_plan(run.ctx, run.workspace, run.session, bold=True)
    return ([_shown(plan, run)], plan) if plan is not None else None


def _newest_review(run: Run) -> Path | None:
    chosen = artifact_requirements.select_plan_review(run.ctx, run.session)
    return chosen[0] if chosen else None


def _plan_review(run: Run) -> Found | None:
    from plan_wave import p4_input

    review = p4_input(run.ctx, run.session) or _newest_review(run)  # a round file joins the reviews
    return (["--from-review", _shown(review, run)], review) if review is not None else None


def _previous_review(run: Run) -> Found | None:
    review = _newest_review(run)
    return ([], review) if review is not None else None


def _pr_number(run: Run) -> Found | None:
    record = artifact_requirements.draft_pr_record(run.ctx, run.session, current_branch(run.workspace))
    number = artifact_requirements.draft_pr_number(record) if record is not None else None
    return ([str(number)], record) if number is not None else None


def _pr_review(run: Run) -> Found | None:
    """The R1 review of the run's PR, as the fix round's one input."""
    record = artifact_requirements.draft_pr_record(run.ctx, run.session, current_branch(run.workspace))
    number = artifact_requirements.draft_pr_number(record) if record is not None else None
    review = artifact_requirements.pr_review_file(run.ctx, number) if number is not None else None
    return ([_shown(review, run)], review) if review is not None and review.is_file() else None


def _task_file(run: Run, folder: str, suffix: str) -> Path | None:
    """`.context/<folder>/<task_id><suffix>` when the session names a task and the file exists."""
    task = re.sub(r"[\\/]", "-", str(run.session.get("task_id") or ""))
    path = run.ctx / folder / f"{task}{suffix}"
    return path if task and path.is_file() else None


def _request(run: Run) -> Found | None:
    """The user's request that activation saved."""
    path = _task_file(run, "proposals", ".md")
    return ([], path) if path else None


def _vibetest(run: Run) -> Found | None:
    report = artifact_requirements.newest_vibetest(run.ctx, run.session)
    return ([], report) if report is not None else None


def _plan_benchmark(run: Run) -> Found | None:
    """The newest P7 scorecard of the run, any name that the P7 gate reads, while its verdict is not PASS."""
    accept = artifact_requirements.run_scope_accept(run.ctx, session=run.session)
    cards = [p for p in run.ctx.glob("benchmarks/*_plan.json") if accept(p)]
    path = max(cards, key=lambda p: p.stat().st_mtime) if cards else None
    try:
        verdict = json.loads(path.read_text(encoding="utf-8")).get("verdict") if path else "PASS"
    except (ValueError, AttributeError):
        verdict = None
    return ([], path) if str(verdict).strip().upper() != "PASS" else None


def _deferral_ruling(run: Run) -> Found | None:
    path = _task_file(run, "deferrals", "_deferral-review.md")
    return ([], path) if path else None


def _deferral_manifest(run: Run) -> Found | None:
    """The documents that P8 lists for /deferral-review, its one input."""
    path = _task_file(run, "manifests", "_deferrals.md")
    return ([_shown(path, run)], path) if path else None


def _manifest(run: Run) -> Found | None:
    """The run's session manifest, which `activate_session.py --deactivate` writes."""
    path = _task_file(run, "manifests", ".md")
    return ([_shown(path, run)], path) if path else None


def _acceptance(run: Run) -> Found | None:
    """The acceptance checks that P1 wrote (scripts/acceptance.py)."""
    path = _task_file(run, "acceptance", ".json")
    return ([], path) if path else None


def _acceptance_repair(run: Run) -> Found | None:
    """Each check defect and repin after P9, so R1 judges whether a check got weaker."""
    path = _task_file(run, "acceptance", ".repair.jsonl")
    return ([], path) if path else None


_RESOLVERS: dict[str, Callable[[Run], Found | None]] = {
    "ticket": _ticket, "plan": _plan, "plan-review": _plan_review, "plan-review-previous": _previous_review,
    "pr-number": _pr_number, "pr-review": _pr_review, "manifest": _manifest,
    "request": _request, "vibetest": _vibetest, "plan-benchmark": _plan_benchmark,
    "deferral-manifest": _deferral_manifest, "deferral-ruling": _deferral_ruling,
    "acceptance": _acceptance, "acceptance-repair": _acceptance_repair,
}


def _shown(path: Path, run: Run) -> str:
    """The packet path of a file: `.context/<x>` under the run's .context, else relative to the worktree."""
    shown = handoff.shown_path(path, ((run.ctx, ".context/"), (run.workspace, "")))
    if shown is None:
        raise handoff.HandoffError(f"input {path} is outside the worktree and the .context of the run")
    return shown


def _missing(kind: str, block: str, root: Path) -> str:
    what, slug = _MISSING[kind]
    writer = next((r["block"] for r in load_registry(root)["stages"] if r.get("slug") == slug), "?")
    return f"{block} needs {kind}: no {what}. Block {writer} ({slug}) writes it."


# The acceptance repair: P4 fixes one pinned check after P9, from the check and its defect, not a review.
# Without it, the hook rewrote `/plan-update <plan> --acceptance AC-4` to the round's P4 packet.
ACCEPTANCE_REPAIR_INPUTS = ["plan", "acceptance", "acceptance-repair"]
_ACCEPTANCE_ID = re.compile(r"\s--acceptance\s+(AC-\d+)\s*$")


def acceptance_repair_id(prompt: str) -> str | None:
    """`AC-n` when the first line of a P4 prompt is `/plan-update <plan> --acceptance AC-n`, else None."""
    first = (prompt.strip().splitlines() or [""])[0]
    found = _ACCEPTANCE_ID.search(first) if first.startswith("/plan-update ") else None
    return found.group(1) if found else None


def resolve(row: dict[str, Any], run: Run, acceptance: str | None = None) -> Resolved:
    """The slash command, the input paths and the missing kinds of a peer row. With ``acceptance`` (an `AC-n`),
    the P4 row is the acceptance repair: the plan, the checks and the repair log, then `--acceptance AC-n`."""
    block, kinds = row["block"], row.get("inputs")
    if acceptance is not None and block == "P4":
        kinds = ACCEPTANCE_REPAIR_INPUTS
    found = Resolved(command=row["command"])
    if not isinstance(kinds, list):
        found.missing.append(f"the stages.json row of {block} has no inputs list")
        return found
    args = [str(a) for a in row.get("command_args", [])]
    for kind in kinds:
        resolver = _RESOLVERS.get(kind)
        if resolver is None:
            found.missing.append(f"the stages.json row of {block} has an unknown input kind {kind!r}")
            continue
        hit = resolver(run)
        if hit is None:
            if kind not in OPTIONAL:
                found.missing.append(_missing(kind, block, run.root))
            continue
        args += hit[0]
        if hit[1] is not None:
            found.inputs.append(_shown(hit[1], run))
    if acceptance is not None and block == "P4":
        args += ["--acceptance", acceptance]
    found.command = " ".join([row["command"], *args])
    return found


def _covers(packet: dict[str, Any], command: str, inputs: list[str], run: Run) -> bool:
    """True when a packet has this command, pins each input, and pins no file at an old sha256.

    An optional input that the row no longer resolves, such as a plan scorecard that now passes,
    must not keep a stale pin: the handoff check would refuse the packet."""
    pinned = {(r.get("path"), r.get("sha256")) for r in packet["inputs"] if isinstance(r, dict)}
    try:
        current = {(rel, handoff.input_sha(run.workspace, str(rel), run.ctx)) for rel, _ in pinned}
    except (handoff.HandoffError, OSError):
        return False
    return packet.get("command") == command and pinned == current and set(inputs) <= {rel for rel, _ in pinned}


def packet_for(block: str, run: Run, extra: list[str] | tuple[str, ...] = (),
               acceptance: str | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """(the packet of `block`, []) built from its row, or (None, the reasons it cannot be).

    A packet on disk that has the row's command and pins each resolved input and each
    `extra` input at its current sha256 stays. That keeps a manual override that adds an
    input. Else the packet is written again from the row.
    """
    try:
        found = resolve(handoff._stage(block, run.root, run.workspace), run, acceptance)
        if found.missing:
            return None, found.missing
        inputs = found.inputs + [rel for rel in extra if rel not in found.inputs]
        run_key = str(run.session.get("pipeline_run_id") or "")
        try:
            packet = handoff.load_packet(run.ctx, run_key, block)
        except (handoff.HandoffError, ValueError):
            packet = None  # a corrupt or old-key packet is written again
        if packet is None or not _covers(packet, found.command, inputs, run):
            handoff.write_packet(workspace=run.workspace, context_dir=run.ctx, run_key=run_key, block=block,
                                 inputs=inputs, ticket=str(run.session.get("task_id") or ""), root=run.root,
                                 command=found.command)
            packet = handoff.load_packet(run.ctx, run_key, block)
    except handoff.HandoffError as exc:
        return None, [str(exc)]
    return packet, []
