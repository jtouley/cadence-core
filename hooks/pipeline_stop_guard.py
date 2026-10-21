#!/usr/bin/env python3
"""Fail-closed stop / subagentStop guard for cadence (Gap 3).

Blocks early exit while an active run is in execution or review phases.
Also blocks a stop while the newest plan review is READY WITH CHANGES or NOT READY.
resume_slug stays at activation until execution, so that hold reads the review file.
Allows stop when no session, deactivated, or hard-stop flag is set.

A block keeps the agent working: Cursor gets `followup_message`, Claude Code
gets `decision: block` (see hook_protocol.py). After MAX_REFUSALS refusals the
guard escalates: it lets the stop through and tells the user why, instead of
looping forever.

Where the run stands comes from evidence, not from `resume_slug`. Only activation
writes that field, so in a real run it stays at A1. The guard reads the P9 marker.
With the marker, it walks the registered gates from the first execution row, read-only,
and the first failing gate is the open block. `resume_slug` is still a lower bound:
a stop that the old guard blocked stays blocked.

Lean peer returns (gh-96): on Claude Code, a delegated peer's SubagentStop is held
once when its final message is not one lean line (scripts/peer_return.py). The final
message goes into the orchestrator's context, and the gates read the artifact, not
the message. A second wrong return goes through with a notice. Each check writes a
row to .context/attest/<run>.returns.jsonl.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SKILL_ROOT = Path(
    os.environ.get("CADENCE_HOME")
    or os.environ.get("CADENCE_SKILL_ROOT")
    or Path(__file__).resolve().parent.parent
)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(SKILL_ROOT / "shared" / "scripts"))

try:
    from artifact_requirements import (
        plan_review_cycle_limit,
        adversarial_route,
        check_plan_review_verdict,
        entry_invariant_matches,
        plan_review_cycles,
        select_plan_review,
    )
    from handoff import block_for_prompt
    from headless import walk
    from load_stages import load_stages, stage_slugs
    import peer_return
    from skill_paths import display_command

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_protocol import chat_id, fail_closed, hook_workspace, parse_host, stop_allow, stop_block
except Exception as exc:  # noqa: BLE001 - startup errors must block, not pass (exit 1 fails open)
    sys.stderr.write(f"cadence hook failed closed at startup: {exc!r}\n")
    raise SystemExit(2)

try:
    # context_path never creates .context: this hook fires on every stop, in every repo.
    from context_root import context_path as canonical_context_dir  # type: ignore
except ImportError:  # pragma: no cover
    def canonical_context_dir(cwd=None):
        return (cwd or Path.cwd()) / ".context"

EXECUTION_REVIEW_PHASES = frozenset({"execution", "review"})
BLOCKING_PLAN_VERDICTS = frozenset({"READY WITH CHANGES", "NOT READY"})
PLAN_LOOP_SLUGS = frozenset({"plan-review", "plan-update"})
MAX_REFUSALS = 3
# The hook timeout is 15 s in both fragments. Claude Code reads a killed hook as
# "do not block", so the walk must end well before that.
GATE_SECONDS = 4
WALK_SECONDS = 9


def _session_path(context_dir: Path) -> Path:
    return context_dir / "cadence-session.json"


def load_session(context_dir: Path) -> dict | None:
    path = _session_path(context_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if "ref" in data:
        try:
            data = json.loads((context_dir / str(data["ref"])).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    # Anything but an object (a list, a string, null) is a corrupt session.
    return data if isinstance(data, dict) else None


def phase_for_slug(slug: str | None) -> str | None:
    if not slug:
        return None
    for row in load_stages(SKILL_ROOT):
        if row.get("slug") == slug:
            return str(row.get("phase"))
    return None


def next_row(slug: str | None) -> dict | None:
    rows = load_stages(SKILL_ROOT)
    if not slug:
        return rows[0] if rows else None
    for i, row in enumerate(rows):
        if row.get("slug") == slug and i + 1 < len(rows):
            return rows[i + 1]
    return rows[-1] if rows else None


def write_breadcrumb(context_dir: Path, session: dict, detail: str) -> Path:
    path = context_dir / "breadcrumbs" / "phase-boundary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Cadence phase-boundary breadcrumb\n\n"
        f"- task_id: {session.get('task_id')}\n"
        f"- resume_slug: {session.get('resume_slug')}\n"
        f"- pipeline_run_id: {session.get('pipeline_run_id')}\n"
        f"- detail: {detail}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def run_started(context_dir: Path, session: dict) -> bool:
    """True when the P9 marker for this task exists: the run is in execution or later.

    activate_session.py clears the marker at each activation, and the hooks protect it
    from agent writes. A marker that names another task is not evidence for this run.
    """
    # cd-76 D3: the judged run's own marker (gates/entry-invariant-<run_id>.ok).
    return entry_invariant_matches(context_dir, session)


def find_open_block(
    session: dict, context_dir: Path, workspace: Path, budget: float = WALK_SECONDS
) -> tuple[dict | None, bool]:
    """(registry row, unknown): where the run stands, judged from evidence.

    (None, False): no evidence of execution, so the stored slug decides.
    The walk starts at the first execution row, so it never runs P9 (a failed P9 clears
    the marker, even with --check) and an early gate cannot hide progress.
    A timeout or an error is unknown, never an open block at that row.
    """
    if not run_started(context_dir, session):
        return planning_open_block(workspace, budget)
    rows = load_stages(SKILL_ROOT, workspace)
    first = next((r for r in rows if r.get("phase") == "execution"), None)
    if first is None:
        return None, False
    if budget <= 0:
        return first, True  # cd-76 R3-D5e: the shared deadline did not reach this run
    try:
        report = walk(
            SKILL_ROOT,
            workspace,
            since=str(first["slug"]),
            readonly=True,
            timeout=GATE_SECONDS,
            budget=budget,
        )
    except Exception:  # noqa: BLE001 - an unreadable registry is unknown, and unknown blocks
        return first, True
    hung = next((r for r in report["results"] if r["timed_out"]), None)
    if hung is not None:
        # Every gate before it passed, so the run stands at this row at the earliest.
        return next((r for r in rows if r.get("slug") == hung["slug"]), first), True
    if report["done"]:
        # Every gate passes, but R6 and R7 prove nothing and the session is still active.
        return rows[-1], False
    slug = report["next"]["slug"]
    return next((r for r in rows if r.get("slug") == slug), first), False


def with_next_phase(row: dict, context_dir: Path, workspace: Path, session: dict, *, worker: bool = False) -> dict:
    """E1 row that names the next plan phase (the concrete task), else the E1 gate's open work.

    The worker is the agent that writes the phase: the E1 peer, or the main agent on another
    host. A Claude Code orchestrator is told to send the peer again.
    """
    try:
        from phase_ledger import assess, next_phase, open_work

        ws = session.get("workspace")
        worktree = Path(ws) if isinstance(ws, str) and Path(ws).is_dir() else workspace
        state = assess(context_dir, worktree)
        nxt = next_phase(Path(state["plan"]), worktree, session) if state["plan"] else None
        work = open_work(state)
    except Exception:  # noqa: BLE001 - the hold stands; only the detail is lost
        return row
    if nxt is not None:  # gh-173: the next phase, not the phase list
        n, label = nxt
        if not worker and session.get("host") == "claude":
            return {**row, "continue": f"Continue E1: send the `/spec-driven` peer the plan again. "
                                       f"Its next open phase is phase {n} ({label})."}
        return {**row, "continue": f"Continue E1: phase {n} ({label}). "
                                   f"Commit it as 'phase {n}: <subject>' before the next phase."}
    return {**row, "detail": work} if work else row


# P8. P9 never runs here: a failed P9 clears its marker. With the deferrals pack off, the
# walk ends at the last row before P8 that is on (headless.resolve_through).
LAST_PLANNING_GATE = "deferral-register"


def planning_open_block(workspace: Path, budget: float = WALK_SECONDS) -> tuple[dict | None, bool]:
    """Before P9: the first failing gate from A1 through P8, else P9 itself."""
    rows = load_stages(SKILL_ROOT, workspace)
    if budget <= 0:
        return (rows[0] if rows else None), True  # cd-76 R3-D5e, N-4: no time left is unknown
    try:
        report = walk(
            SKILL_ROOT,
            workspace,
            through=LAST_PLANNING_GATE,
            readonly=True,
            timeout=GATE_SECONDS,
            budget=budget,
        )
    except Exception:  # noqa: BLE001 - unreadable registry: the stored slug decides
        return None, False
    hung = next((r for r in report["results"] if r["timed_out"]), None)
    slug = hung["slug"] if hung else ("entry-invariant" if report["done"] else report["next"]["slug"])
    if slug == "ops-gate" and not hung:
        return None, False  # SKILL.md: ops-only work stops at A3. That stop is the outcome.
    return next((r for r in rows if r.get("slug") == slug), None), hung is not None


PEER_WINDOW_SECONDS = 3600


def peer_running(context_dir: Path, session: dict) -> str | None:
    """A delegated peer of this run that began within the hour and has not completed.

    The orchestrator waits for it; holding that stop only pushes a duplicate peer (cd-32 D17).
    """
    from datetime import datetime, timezone

    run_id = str(session.get("pipeline_run_id") or "")
    if not run_id:
        return None
    if session.get("host") == "claude":
        # Host evidence: SubagentStart/SubagentStop. A begin record stays "running"
        # when the orchestrator never calls complete.
        import peer_attest

        agents = peer_attest.running_agents(context_dir, run_id)
        return f"subagent {agents[0]}" if agents else None
    now = datetime.now(timezone.utc).timestamp()
    for path in sorted((context_dir / "subagent-runs" / run_id).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(str(record.get("started_at"))).timestamp()
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if record.get("status") == "running" and 0 <= now - started <= PEER_WINDOW_SECONDS:
            return str(record.get("stage") or path.stem)
    return None


def latest_plan_verdict(context_dir: Path, session: dict | None = None) -> str | None:
    """The judged run's plan verdict, or None when no in-scope review names one."""
    chosen = select_plan_review(Path(context_dir), session=session)
    return None if chosen is None else chosen[1]


def _cmd(row: dict) -> str:
    """The row's command with an absolute script path (cd-82 D5)."""
    return display_command(str(row.get("command") or ""), SKILL_ROOT)


def _with_budget(session: dict, phase: str, cont: str) -> tuple[str, str]:
    refusals = int(session.get("stop_refusals") or 0)
    if refusals >= MAX_REFUSALS:
        return "escalate", f"stop refused {refusals} times — escalate to user. {cont}"
    return "block", f"BLOCKED early stop in {phase}. {cont}"


def planning_continuation(slug: str | None, plan_verdict: str | None) -> str | None:
    """Text that keeps P3/P4 moving, or None when this stop is not a planning hold.

    A blocking verdict holds even when resume_slug is still activate-session.
    A stored plan-review or plan-update slug with no verdict does not hold: the old
    guard allowed that stop, and a real run does not advance the slug during planning.
    READY TO EXECUTE holds only while the slug is still plan-review or plan-update.
    The next block is then the row after P4 that is on (P5, or P9 with the architecture pack off).
    """
    if plan_verdict in BLOCKING_PLAN_VERDICTS:
        if slug == "plan-update":
            return "Continue paceline: run `/plan-review` on the same plan. Do not end the turn."
        return (
            f"Continue paceline: verdict is {plan_verdict}. "
            "Run `/plan-update` on the same plan, then `/plan-review`. Do not end the turn."
        )
    if slug in PLAN_LOOP_SLUGS and plan_verdict == "READY TO EXECUTE":
        nxt = next_row("plan-update") or {}
        return (
            f"Continue paceline: next {nxt.get('block')} ({nxt.get('slug')}) "
            f"via `{_cmd(nxt)}`"
        )
    return None


def _run_note(session: dict | None) -> str:
    run_id = str((session or {}).get("pipeline_run_id") or "")
    return f" (run {run_id})" if run_id else ""


def _continue_text(row: dict, session: dict, unknown: bool) -> str:
    """The "Continue paceline" message for the open row, with its detail and an unknown-progress note."""
    cont = row.get("continue") or (
        f"Continue paceline: next {row.get('block')} ({row.get('slug')}) "
        f"via `{_cmd(row)}`"
    )
    if row.get("detail"):
        cont = f"{cont}: {row['detail']}."
    if unknown:
        cont = f"Progress unknown{_run_note(session)}: a gate timed out or could not run. {cont}"
    return cont


def _decide_planning(session: dict, slug, phase, open_block: dict | None, unknown: bool,
                     plan_verdict: str | None) -> tuple[str, str]:
    """decide() for a run before execution: hold only on a planning continuation or an open block."""
    cont = planning_continuation(str(slug) if slug else None, plan_verdict)
    if cont is None and open_block is not None:
        cont = _continue_text(open_block, session, unknown)
    if cont is None:
        return "allow", f"resume_slug={slug} phase={phase} — outside E/R"
    return _with_budget(session, "planning", cont)


def decide(
    session: dict | None,
    *,
    corrupt: bool = False,
    open_block: dict | None = None,
    unknown: bool = False,
    plan_verdict: str | None = None,
) -> tuple[str, str]:
    """Return (decision, message) where decision is allow|block|escalate.

    open_block is the row that find_open_block found. The stored resume_slug stays a
    lower bound: a stop is blocked if either one is in execution or review.
    plan_verdict is the newest plan-review verdict. A blocking one holds the stop
    before execution, because resume_slug does not move during planning.
    """
    if corrupt:
        return "block", "FAIL-CLOSED: corrupt session during execution/review"
    if not session or session.get("active_skill") != "cadence":
        return "allow", "no active cadence run"
    if session.get("hard_stop"):
        return "allow", "hard_stop flag set — allowing exit"
    slug = session.get("resume_slug")
    phase = phase_for_slug(slug)
    if open_block is not None and open_block.get("phase") in EXECUTION_REVIEW_PHASES:
        phase, nxt = str(open_block.get("phase")), open_block
    elif phase in EXECUTION_REVIEW_PHASES:
        nxt = next_row(slug) or {}
    else:
        return _decide_planning(session, slug, phase, open_block, unknown, plan_verdict)
    refusals = int(session.get("stop_refusals") or 0)
    cont = _continue_text(nxt, session, unknown)
    if refusals >= MAX_REFUSALS:
        return "escalate", f"stop refused {refusals} times — escalate to user. {cont}"
    return "block", f"BLOCKED early stop in {phase}. {cont}"


def session_data_path(context_dir: Path) -> Path:
    """File that holds the session data: the pointer's target, or the singleton itself."""
    path = _session_path(context_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return path
    if isinstance(data, dict) and isinstance(data.get("ref"), str) and data["ref"].strip():
        return context_dir / data["ref"]
    return path


def apply_refusal(
    context_dir: Path, session: dict, slug: str | None = None, *, data_path: Path | None = None
) -> dict:
    session = dict(session)
    session["stop_refusals"] = int(session.get("stop_refusals") or 0) + 1
    if slug:
        session["stop_refusal_slug"] = slug  # the block these refusals belong to
    write_breadcrumb(context_dir, session, "stop refused — hold cadence")
    # Write where the judged run's data lives: its own per-worktree file, or the
    # pointer's target, so a {"ref": ...} layout survives.
    target = data_path or session_data_path(context_dir)
    target.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
    return session


SUBAGENT_EVENTS = frozenset({"SubagentStop", "subagentStop"})


def read_stop_payload() -> dict:
    """Hook payload from stdin; empty or unreadable means a plain main-agent stop."""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None) -> int:
    host = parse_host(argv)
    payload = read_stop_payload()
    args = sys.argv[1:] if argv is None else list(argv)
    subagent = "--subagent" in args or payload.get("hook_event_name") in SUBAGENT_EVENTS
    # Claude Code sets stop_hook_active when a stop hook already sent the agent back
    # once; it has no loop_limit, so this is the only signal that bounds the loop.
    repeat = payload.get("stop_hook_active") is True
    try:
        return _guard(host, subagent=subagent, repeat=repeat, payload=payload)
    except Exception as exc:  # noqa: BLE001 - fail closed, but never loop forever
        message = f"FAIL-CLOSED: stop guard error ({exc!r})"
        if repeat:
            stop_allow(host, notice=f"Cadence: {message}; stop allowed after one push back")
        else:
            stop_block(host, message)
        return 0


NOT_OURS = object()


def _read_dict(path: Path) -> dict | None | object:
    """The JSON object in ``path``; None when absent; NOT_OURS when it is not an object."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return NOT_OURS
    return data if isinstance(data, dict) else NOT_OURS


def _toplevel(cwd: Path) -> Path:
    try:
        from context_root import git_toplevel  # type: ignore

        top = git_toplevel(cwd)
    except Exception:  # noqa: BLE001
        top = None
    return Path(top or cwd).resolve()


Run = tuple[dict, Path, Path, Path]  # (session, context_dir, workspace, data_path)


def _orchestrators(data: dict) -> list[str]:
    ids = data.get("orchestrator_agent_ids")
    return [a for a in ids if isinstance(a, str) and a] if isinstance(ids, list) else []


def _by_worktree_runs(contexts: list[Path]) -> list[Run]:
    """Every active run in the by-worktree store of these contexts, in pipeline_run_id order."""
    found: list[Run] = []
    for ctx in contexts:
        for path in sorted((ctx / "sessions" / "by-worktree").glob("*.json")):
            data = _read_dict(path)
            if isinstance(data, dict) and data.get("active_skill") == "cadence":
                ws = Path(str(data.get("workspace") or ctx.parent))
                found.append((data, ctx, ws, path))
    return sorted(found, key=lambda r: str(r[0].get("pipeline_run_id") or ""))


def judge_runs(payload: dict, *, agent: str | None = None) -> tuple[str, list[Run], Path, Path, Path | None]:
    """(verdict, runs, context_dir, workspace, data_path) for this stop.

    verdict is "judge", "corrupt" or "skip". `agent` is the agent_id of a SubagentStop.
    - A SubagentStop with an agent_id judges only the run that lists it as an orchestrator
      (tie-break: the run whose worktree holds the stop's cwd, else the first). A peer's
      stop is not judged (cd-76 r3 D4).
    - A main-agent Stop (and any stop with no agent_id, which is every Cursor stop) judges
      each active top-level run (no orchestrator_agent_ids) that this chat owns. With none,
      it falls through to this worktree's run, then the legacy single-file session; both
      skip a run that a subagent activated (cd-76 R2-D5a).
    Anything else is another chat's or another worktree's run: skip, no message.
    """
    project = _project_dir()
    # cd-82 D4: a Cursor stop can carry only workspace_roots; `project` stays as the second candidate.
    here = hook_workspace(payload)
    chat = chat_id(payload)
    cwds = list(dict.fromkeys([here, project]))
    contexts = list(dict.fromkeys(canonical_context_dir(c) for c in cwds))
    if agent:
        return _subagent_verdict(agent, contexts, here)
    owned = _owned_runs(chat, contexts) if chat else []
    if owned:
        return "judge", owned, owned[0][1], owned[0][2], owned[0][3]
    found = _worktree_verdict(cwds, chat)
    if found is not None:
        return found
    return _legacy_verdict(project, cwds)


Verdict = tuple[str, list[Run], Path, Path, Path | None]


def _project_dir() -> Path:
    """The host's project dir, else the hook's cwd."""
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CURSOR_PROJECT_DIR") or Path.cwd())


def _active(data: object) -> bool:
    return isinstance(data, dict) and data.get("active_skill") == "cadence"


def _subagent_verdict(agent: str, contexts: list[Path], here: Path) -> Verdict:
    """The run that lists `agent` as an orchestrator; the one whose worktree holds `here` wins a tie."""
    mine = [r for r in _by_worktree_runs(contexts) if agent in _orchestrators(r[0])]
    if not mine:
        return "skip", [], contexts[0], here, None
    top = _toplevel(here)
    chosen = next((r for r in mine if _toplevel(r[2]) == top), mine[0])
    return "judge", [chosen], chosen[1], chosen[2], chosen[3]


def _owned_runs(chat: str, contexts: list[Path]) -> list[Run]:
    """Active top-level runs (no orchestrator ids) that this chat owns."""
    return [r for r in _by_worktree_runs(contexts)
            if r[0].get("owner_session_id") == chat and not _orchestrators(r[0])]


def _worktree_verdict(cwds: list[Path], chat: str | None) -> Verdict | None:
    """The active by-worktree run of a candidate cwd, or None to fall through to the legacy file."""
    try:
        import session_store  # type: ignore
    except ImportError:  # pragma: no cover - vendored with the skill
        return None
    for cwd in cwds:
        ctx = canonical_context_dir(cwd)
        path = ctx / "sessions" / "by-worktree" / f"{session_store.worktree_key(cwd)}.json"
        data = _read_dict(path)
        if data is NOT_OURS:
            return "corrupt", [], ctx, cwd, path
        if not _active(data):
            continue
        owner = str(data.get("owner_session_id") or "")
        if (owner and chat and owner != chat) or _orchestrators(data):
            return "skip", [], ctx, cwd, None
        ws = Path(str(data.get("workspace") or cwd))
        return "judge", [(data, ctx, ws, path)], ctx, ws, path
    return None


def _legacy_verdict(project: Path, cwds: list[Path]) -> Verdict:
    """The single-file session of the project, or the run its {"ref": ...} redirect names."""
    ctx = canonical_context_dir(project)
    root = _session_path(ctx)
    raw = _read_dict(root)
    if raw is None:
        return "skip", [], ctx, project, None
    if raw is NOT_OURS:
        return "corrupt", [], ctx, project, root
    if "ref" not in raw:
        if _orchestrators(raw):
            return "skip", [], ctx, project, None
        return "judge", [(raw, ctx, project, root)], ctx, project, root
    return _redirect_verdict(ctx, project, cwds, raw)


def _redirect_verdict(ctx: Path, project: Path, cwds: list[Path], raw: dict) -> Verdict:
    target = ctx / str(raw["ref"])
    data = _read_dict(target)
    if not isinstance(data, dict):
        return "corrupt", [], ctx, project, target
    ws = data.get("workspace")
    mine = {_toplevel(c) for c in cwds}
    if ws and Path(str(ws)).resolve() not in mine:
        return "skip", [], ctx, project, None  # the redirect names another worktree's run
    if _orchestrators(data):
        return "skip", [], ctx, project, None
    workspace = Path(str(ws or project))
    return "judge", [(data, ctx, workspace, target)], ctx, workspace, target


def judge_run(payload: dict) -> tuple[str, dict | None, Path, Path, Path | None]:
    """The first run judge_runs gives for a main-agent stop (kept for single-run callers)."""
    verdict, runs, ctx, ws, path = judge_runs(payload)
    return verdict, (runs[0][0] if runs else None), ctx, ws, path


def _started_runs(payload: dict, agent: str) -> list[tuple[Path, str]]:
    """(context, run id) of each active run that recorded this subagent's start."""
    import peer_attest

    project = _project_dir()
    here = Path(payload.get("cwd") or project)
    contexts = list(dict.fromkeys(canonical_context_dir(c) for c in (here, project)))
    runs = [(r[1], str(r[0].get("pipeline_run_id") or "")) for r in _by_worktree_runs(contexts)]
    for ctx in contexts:
        legacy = load_session(ctx)
        if isinstance(legacy, dict) and legacy.get("active_skill") == "cadence":
            runs.append((ctx, str(legacy.get("pipeline_run_id") or "")))
    return [(ctx, run_id) for ctx, run_id in dict.fromkeys(runs)
            if run_id and _has_start(peer_attest, ctx, run_id, agent)]


def _record_peer_stop(payload: dict, agent: str) -> None:
    """Record a subagent's stop in each run that has its start (cd-76 r2 D5); independent of judging."""
    try:
        import peer_attest

        for ctx, run_id in _started_runs(payload, agent):
            peer_attest.agent_event(ctx, run_id, agent, "stop")
    except Exception:  # noqa: BLE001 - a lost record only makes the guard stricter
        pass


def _judge_return(payload: dict, repeat: bool) -> tuple[str, str | None, str | None] | None:
    """(decision, reason, message) for a peer's final message; None when the subagent is not a peer."""
    prompt = peer_return.first_prompt(payload.get("agent_transcript_path"))
    if prompt is None:
        return "unknown", "no subagent transcript", peer_return.final_message(payload)
    if not peer_return.is_peer_prompt(prompt, lambda text: block_for_prompt(text, SKILL_ROOT)):
        return None
    message = peer_return.final_message(payload)
    if message is None:
        return "unknown", "no final message", None
    reason = peer_return.return_reason(message)
    return ("ok" if reason is None else "violation" if repeat else "hold"), reason, message


def _record_return(runs: list[tuple[Path, str]], agent: str, decision: str, reason: str | None,
                   message: str | None) -> None:
    for ctx, run_id in runs:
        peer_return.record(ctx, run_id, {
            "agent_id": agent, "decision": decision, "reason": reason,
            "chars": None if message is None else len(message),
            "lines": None if message is None else len(message.strip().splitlines()),
        })


def peer_return_check(host: str, payload: dict, agent: str, repeat: bool) -> tuple[str, str] | None:
    """("hold", reason) or ("notice", text) for a delegated peer's final message, else None (gh-96).

    Only a Claude Code peer of an active run is checked: a subagent with a recorded start,
    not an orchestrator, whose first prompt is a peer call. A message the hook cannot
    read is not held. A second wrong return (`stop_hook_active`) is not held again.
    """
    if host != "claude":
        return None
    import peer_attest

    runs = [(ctx, run_id) for ctx, run_id in _started_runs(payload, agent)
            if agent not in peer_attest.orchestrator_ids(ctx)]
    judged = _judge_return(payload, repeat) if runs else None
    if judged is None:
        return None
    decision, reason, message = judged
    _record_return(runs, agent, decision, reason, message)
    if decision == "hold":
        return "hold", (
            f"BLOCKED: lean peer return: {reason}. Put the details in your artifact. "
            f"Then end with one line only: {peer_return.LEAN_SHAPES}."
        )
    if decision == "violation":
        return "notice", f"Cadence: peer {agent} ended without a lean return ({reason})"
    return None


def _has_start(peer_attest, ctx: Path, run_id: str, agent: str) -> bool:
    try:
        lines = peer_attest._agents_log(ctx, run_id).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("agent_id") == agent and entry.get("event") == "start":
            return True
    return False


def _open_block(session: dict, context_dir: Path, workspace: Path, budget: float) -> tuple[dict | None, bool]:
    """(open block, unknown) of a live run; an open E1 names its next plan phase."""
    open_block, unknown = find_open_block(session, context_dir, workspace, budget)
    if open_block is not None and open_block.get("slug") == "implement" and not unknown:
        open_block = with_next_phase(open_block, context_dir, workspace, session)
    return open_block, unknown


def _plan_loop_limit(context_dir: Path, session: dict | None, open_block: dict | None,
                     plan_verdict: str | None) -> tuple[dict | None, str | None]:
    """At the plan-review cycle limit, P4 (or a hard stop) is next, never another review cycle."""
    if plan_verdict not in BLOCKING_PLAN_VERDICTS:
        return open_block, plan_verdict
    if plan_review_cycles(context_dir, session=session) < plan_review_cycle_limit():
        return open_block, plan_verdict
    limit = check_plan_review_verdict(context_dir, session=session)
    if not limit:
        return open_block, None
    hard = "NOT READY" in limit[0]
    return {"block": "P4", "slug": "plan-update", "phase": "planning",
            "command": "activate_session.py --hard-stop retries" if hard else "/plan-update",
            "detail": limit[0]}, None


def _p6_route(context_dir: Path, session: dict, open_block: dict | None,
              plan_verdict: str | None) -> tuple[dict | None, str | None]:
    """cd-63 D7: before execution, a blocking P6 verdict names the next step of the P6 loop."""
    if open_block is not None and open_block.get("phase") in EXECUTION_REVIEW_PHASES:
        return open_block, plan_verdict
    route = adversarial_route(context_dir, session)
    # A shadowed or stale report names P6 only when P6 is the open block (PR #88 MF-2).
    early = route is not None and route["block"] == "P6" and open_block is not None \
        and open_block.get("slug") != "adversarial-review"
    if route is not None and not early:
        return route, None
    return open_block, plan_verdict


def _judge_one(session: dict | None, context_dir: Path, workspace: Path, *, corrupt: bool,
               budget: float) -> tuple[str, str, str, dict | None]:
    """(decision, message, open-block slug, session after the refusal-count reset) for one run."""
    live = bool(session and not corrupt and session.get("active_skill") == "cadence" and not session.get("hard_stop"))
    open_block, unknown = _open_block(session, context_dir, workspace, budget) if live else (None, False)
    # The refusal budget ends a loop at one block. When the run moves to another block the
    # agent made progress, so the count starts again (cd-12-F3). Without a recorded block
    # (older sessions) the count applies as before.
    here = str((open_block or {}).get("slug") or (session or {}).get("resume_slug") or "")
    if session and session.get("stop_refusal_slug") not in (None, here):
        session = {**session, "stop_refusals": 0}
    # cd-76 R2-D5c: every evidence read uses the judged run.
    plan_verdict = latest_plan_verdict(context_dir, session) if session and not corrupt else None
    open_block, plan_verdict = _plan_loop_limit(context_dir, session, open_block, plan_verdict)
    p3_holds = plan_verdict in BLOCKING_PLAN_VERDICTS or (open_block or {}).get("slug") == "plan-update"
    # With the spec-review pack off, a P6 report does not hold a stop.
    if live and not unknown and not p3_holds and "adversarial-review" in stage_slugs(SKILL_ROOT, workspace):
        open_block, plan_verdict = _p6_route(context_dir, session, open_block, plan_verdict)
    decision, message = decide(
        session,
        corrupt=corrupt,
        open_block=open_block,
        unknown=unknown,
        plan_verdict=plan_verdict,
    )
    return decision, message, here, session


def e1_peer_hold(payload: dict, agent: str, repeat: bool) -> str | None:
    """The E1 peer's stop is held once while a plan phase has no commit."""
    try:
        prompt = None if repeat else peer_return.first_prompt(payload.get("agent_transcript_path"))
        if prompt is None or block_for_prompt(prompt, SKILL_ROOT) != "E1":
            return None
        started = {run_id for _ctx, run_id in _started_runs(payload, agent)}
        project = _project_dir()
        contexts = list(dict.fromkeys(canonical_context_dir(c) for c in (Path(payload.get("cwd") or project), project)))
        for session, ctx, ws, _path in _by_worktree_runs(contexts):
            if str(session.get("pipeline_run_id") or "") in started:
                row = with_next_phase({}, ctx, ws, session, worker=True)
                return f"BLOCKED: {row['continue']}" if row.get("continue") else None
    except Exception:  # noqa: BLE001 - as for the lean return: a check that cannot run does not hold
        return None
    return None


def _peer_stop(host: str, payload: dict, agent: str | None, repeat: bool) -> tuple[bool, str | None]:
    """(held, notice) for a subagent stop (gh-96). A held peer keeps running, so its stop is not recorded.

    A check that cannot run does not hold the peer, as for a message it cannot read.
    """
    if not agent:
        return False, None
    try:
        checked = peer_return_check(host, payload, agent, repeat)
    except Exception:  # noqa: BLE001
        checked = None
    if checked is not None and checked[0] == "hold":
        stop_block(host, checked[1])
        return True, None
    phase = e1_peer_hold(payload, agent, repeat) if host == "claude" else None
    if phase is not None:
        stop_block(host, phase)
        return True, None
    _record_peer_stop(payload, agent)
    return False, checked[1] if checked is not None else None


def _guard(host: str, *, subagent: bool, repeat: bool, payload: dict | None = None) -> int:
    payload = payload or {}
    raw_agent = payload.get("agent_id")
    agent = raw_agent if subagent and isinstance(raw_agent, str) and raw_agent else None
    held, notice = _peer_stop(host, payload, agent, repeat)
    if held:
        return 0
    verdict, runs, context_dir, workspace, data_path = judge_runs(payload, agent=agent)
    if verdict == "skip":
        stop_allow(host, notice=notice)
        return 0
    corrupt = verdict == "corrupt"
    if corrupt:
        runs = [(None, context_dir, workspace, data_path)]
    peer = _running_peer(runs)
    if peer:
        stop_allow(host, notice=f"Cadence: peer `{peer}` is still running; resume when it returns")
        return 0
    decision, message, here, judged, judged_ctx, judged_path = _judge_all(runs, corrupt, context_dir, data_path)
    decision, message = _soften(decision, message, corrupt=corrupt, repeat=repeat, subagent=subagent)
    if decision == "allow":
        stop_allow(host)
    elif decision == "escalate":
        stop_allow(host, notice=f"Cadence: {message}")
    else:
        # Subagent stops are pushed back once but never spend the run's budget.
        if judged and not subagent:
            apply_refusal(judged_ctx, judged, here, data_path=judged_path)
        stop_block(host, message)
    return 0


def _running_peer(runs: list) -> str | None:
    """A peer that a live run started and that has not stopped yet."""
    for session, ctx, _ws, _path in runs:
        if session and session.get("active_skill") == "cadence" and not session.get("hard_stop"):
            peer = peer_running(ctx, session)
            if peer:
                return peer
    return None


def _judge_all(runs: list, corrupt: bool, context_dir: Path,
               data_path: Path | None) -> tuple[str, str, str, dict | None, Path, Path | None]:
    """(decision, message, slug, judged session, its context, its data path) of the first run that holds.

    cd-76 R3-D5e: one deadline for every judged run, in pipeline_run_id order; the first run
    that holds decides. A run the deadline does not reach is unknown, and unknown holds.
    """
    import time

    deadline = time.monotonic() + WALK_SECONDS
    decision, message, here, judged, judged_ctx, judged_path = "allow", "no run holds", "", None, context_dir, data_path
    for session, ctx, ws, path in runs:
        decision, message, here, session = _judge_one(
            session, ctx, ws, corrupt=corrupt, budget=deadline - time.monotonic()
        )
        judged, judged_ctx, judged_path = session, ctx, path
        if decision != "allow":
            break
    return decision, message, here, judged, judged_ctx, judged_path


def _soften(decision: str, message: str, *, corrupt: bool, repeat: bool, subagent: bool) -> tuple[str, str]:
    """A repeated corrupt block escalates; a subagent is held only once, and never for planning."""
    if decision == "block" and corrupt and repeat:
        decision, message = "escalate", f"{message}; stop allowed after one push back"
    planning_hold = decision == "block" and "early stop in planning" in message
    if decision == "block" and subagent and (planning_hold or repeat):
        decision = "allow"
    return decision, message


if __name__ == "__main__":
    raise SystemExit(fail_closed(main))
