"""The inputs of a peer handoff, from the `inputs` of its stages.json row (ADR-0010).

Each peer row names a list of input kinds. This module finds each kind in the run's
.context and worktree, with the rules of the gates. It gives the slash command with its
arguments and the input files that the packet pins by sha256. A required kind that it
cannot find gives one message that names the artifact and the block that writes it.
"""

from __future__ import annotations

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

KINDS = ("ticket", "plan", "plan-review", "plan-review-previous", "pr-number", "pr-review")
OPTIONAL = frozenset({"plan-review-previous"})

# What a missing kind is, and the stages.json slug of the block that writes it.
_MISSING = {
    "ticket": ("the ticket id of the run (task_id in the session)", "activate-session"),
    "plan": ("plan of the run in .cursor/plans/ or .context/plans/ with RUN_BY_COMMAND: plan-create", "plan-create"),
    "plan-review": ("plan review of the run with a verdict in .context/reviews/plan_<slug>.md", "plan-review"),
    "pr-number": ("draft-PR record of the run with a PR URL in .context/prs/", "draft-pr"),
    "pr-review": ("PR review of the run's PR in .context/prs/reviews/PR_REVIEW_#<N>.md", "pr-review-local"),
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
    review = _newest_review(run)
    return (["--from-review", _shown(review, run)], review) if review is not None else None


def _previous_review(run: Run) -> Found | None:
    review = _newest_review(run)
    return ([], review) if review is not None else None


def _pr_number(run: Run) -> Found | None:
    record = artifact_requirements.draft_pr_record(run.ctx, run.session, current_branch(run.workspace))
    number = artifact_requirements.draft_pr_number(record) if record is not None else None
    return ([str(number)], record) if number is not None else None


def _pr_review(run: Run) -> Found | None:
    """The R1 review of the run's PR, as the fix round's one input (ADR-0017)."""
    record = artifact_requirements.draft_pr_record(run.ctx, run.session, current_branch(run.workspace))
    number = artifact_requirements.draft_pr_number(record) if record is not None else None
    review = run.ctx / "prs" / "reviews" / f"PR_REVIEW_#{number}.md" if number is not None else None
    return ([_shown(review, run)], review) if review is not None and review.is_file() else None


_RESOLVERS: dict[str, Callable[[Run], Found | None]] = {
    "ticket": _ticket, "plan": _plan, "plan-review": _plan_review, "plan-review-previous": _previous_review,
    "pr-number": _pr_number, "pr-review": _pr_review,
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


def resolve(row: dict[str, Any], run: Run) -> Resolved:
    """The slash command, the input paths and the missing kinds of a peer row."""
    block, kinds = row["block"], row.get("inputs")
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
    found.command = " ".join([row["command"], *args])
    return found


def _covers(packet: dict[str, Any], command: str, inputs: list[str], run: Run) -> bool:
    """True when a packet has this command and pins each input at its current sha256."""
    pinned = {(r.get("path"), r.get("sha256")) for r in packet["inputs"] if isinstance(r, dict)}
    want = {(rel, handoff.input_sha(run.workspace, rel, run.ctx)) for rel in inputs}
    return packet.get("command") == command and want <= pinned


def packet_for(block: str, run: Run, extra: list[str] | tuple[str, ...] = ()) -> tuple[dict[str, Any] | None, list[str]]:
    """(the packet of `block`, []) built from its row, or (None, the reasons it cannot be).

    A packet on disk that has the row's command and pins each resolved input and each
    `extra` input at its current sha256 stays. That keeps a manual override that adds an
    input. Else the packet is written again from the row.
    """
    try:
        found = resolve(handoff._stage(block, run.root, run.workspace), run)
        if found.missing:
            return None, found.missing
        inputs = found.inputs + [rel for rel in extra if rel not in found.inputs]
        run_key = str(run.session.get("pipeline_run_id") or "")
        try:
            packet = handoff.load_packet(run.ctx, run_key, block)
        except (handoff.HandoffError, ValueError):
            packet = None  # a corrupt or old-key packet is written again (PR #186 R1 N-2)
        if packet is None or not _covers(packet, found.command, inputs, run):
            handoff.write_packet(workspace=run.workspace, context_dir=run.ctx, run_key=run_key, block=block,
                                 inputs=inputs, ticket=str(run.session.get("task_id") or ""), root=run.root,
                                 command=found.command)
            packet = handoff.load_packet(run.ctx, run_key, block)
    except handoff.HandoffError as exc:
        return None, [str(exc)]
    return packet, []
