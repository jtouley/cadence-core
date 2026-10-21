#!/usr/bin/env python3
"""Task-scoped session activation for /cadence.

MUST run before other cadence actions. Creates .context/cadence-session.json
and binds a pipeline_run_id. Resume block is a stages.json slug (never a
fractional legacy stage number).

Usage:
    python3 activate_session.py --from-branch --fresh --supersede
    python3 activate_session.py --from-branch --resume
    python3 activate_session.py --deactivate
    python3 activate_session.py --status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import clear_entry_invariant_ok  # noqa: E402
from load_stages import SKILL_ROOT, load_stages, stage_slugs  # noqa: E402
from peer_models import activation_snapshot  # noqa: E402
import cadence_config  # noqa: E402
from phase_ledger import resolve_base  # noqa: E402
from pipeline_run_binding import supersede_ticket_artifacts  # noqa: E402

_SHARED = Path(__file__).resolve().parent.parent / "shared" / "scripts"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

try:
    import session_store  # type: ignore
except ImportError:  # pragma: no cover - offline / missing shared
    session_store = None

try:
    from context_root import context_dir as canonical_context_dir  # type: ignore
    from context_root import find_primary_checkout  # type: ignore
except ImportError:  # pragma: no cover - offline / missing shared
    def canonical_context_dir(cwd: Path | None = None) -> Path:
        base = (cwd or Path.cwd()).resolve()
        return base / ".context"

    def find_primary_checkout(cwd: Path | None = None) -> Path:
        return (cwd or Path.cwd()).resolve()


SESSION_NAME = "cadence-session.json"
SKILL_NAME = "cadence"
# Tracker-agnostic slug for branch → task_id. Default adapter is github-issues (#N).
TICKET_RE = re.compile(r"(?:eng|system|cd|gh|issue)[_-]?(\d{1,6})", re.I)


def get_branch_name(workspace: Path | None = None) -> str | None:
    result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace, capture_output=True, text=True)
    if result.returncode == 0:
        name = result.stdout.strip()
        return name or None
    return None


def derive_task_id_from_branch(branch: str) -> str:
    cleaned = branch.strip().replace("/", "-")
    m = TICKET_RE.search(cleaned)
    if m:
        return f"cd-{m.group(1)}"
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", cleaned).strip("-").lower()
    return slug[:48] or f"task-{hashlib.sha1(branch.encode()).hexdigest()[:8]}"


def derive_task_id_from_description(desc: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", desc.strip().lower()).strip("-")
    return slug[:48] or f"task-{hashlib.sha1(desc.encode()).hexdigest()[:8]}"


def ticket_tokens_for(task_id: str, branch: str | None) -> set[str]:
    tokens: set[str] = set()
    for src in (task_id, branch or ""):
        for m in TICKET_RE.finditer(src):
            tokens.add(m.group(1))
        tokens.add(src.lower().replace("_", "-"))
    return {t for t in tokens if t}


def load_session_file(context_dir: Path) -> dict | None:
    path = context_dir / SESSION_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and "ref" in data:
        try:
            return json.loads((context_dir / str(data["ref"])).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return data if isinstance(data, dict) else None


def save_session_file(context_dir: Path, session: dict) -> Path:
    path = context_dir / SESSION_NAME
    path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
    return path


# Host env vars that carry the chat id the stop hook payload also carries.
OWNER_ENV = ("CADENCE_OWNER_ID", "CLAUDE_CODE_SESSION_ID")


def owner_id(workspace: Path) -> str:
    """The chat id from the host env, else the note the shell hook left (Cursor)."""
    from_env = next((os.environ[k] for k in OWNER_ENV if os.environ.get(k)), "")
    if from_env or session_store is None:
        return from_env
    return session_store.take_pending_owner(workspace)


def load_run_session(context_dir: Path, workspace: Path) -> dict | None:
    """This worktree's session. A legacy single file counts only when it is not a redirect."""
    if session_store is not None:
        own = session_store.load_worktree_session(workspace)
        if own is not None:
            return own
    data = load_session_file(context_dir)
    root = context_dir / SESSION_NAME
    try:
        is_redirect = "ref" in json.loads(root.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        is_redirect = False
    return None if is_redirect else data


HARD_STOP_CLASSES = ("destructive", "credentials", "security", "retries", "a5-gap", "not-needed")


def closure_done(workspace: Path) -> bool:
    """Every gate from E1 through R6 passes, read-only (the stop guard's evidence walk).

    R6 alone only checks "no open must-fix", which is true before any review.
    """
    from headless import walk

    try:
        report = walk(SKILL_ROOT, workspace, since="implement", through="pr-comment", readonly=True, budget=60)
    except Exception:  # noqa: BLE001 - unknown progress never ends a run
        return False
    return bool(report.get("done"))


def save_run_session(context_dir: Path, workspace: Path, session: dict) -> Path:
    """Per-worktree file plus the root redirect that every reader follows."""
    if session_store is None:
        return save_session_file(context_dir, session)
    return session_store.save_session(session, workspace)


def resumable_run(workspace: Path, task_id: str, context_dir: Path) -> dict | None:
    """This worktree's run of ``task_id`` that ``--resume`` keeps (cd-47 D9).

    Active or hard-stopped (both keep active_skill). A completed run that opened its draft PR is
    reopened: a commit after R7 goes through the review blocks again. A run that ended without a
    PR is not resumed.
    """
    record = session_store.load_worktree_session(workspace) if session_store is not None else None
    if not record or record.get("task_id") != task_id or not record.get("pipeline_run_id"):
        return None
    if record.get("status") == "completed":
        return record if (context_dir / "prs" / f"draft-pr-{record['pipeline_run_id']}.md").is_file() else None
    return record if record.get("active_skill") == SKILL_NAME else None


def first_slug() -> str:
    slugs = stage_slugs(SKILL_ROOT)
    return slugs[0] if slugs else "activate-session"


def compute_resume_slug(*, fresh_run: bool, matching: dict[str, str], reopened: bool = False) -> tuple[str, str]:
    if fresh_run:
        return first_slug(), "Fresh run — start at A1 (activate-session)"
    if reopened:
        # Deactivation removed the P9 marker. P9 writes it again; the gates then walk to the first open block.
        return "entry-invariant", "Reopen: the run closed with a draft PR. Run P9, then the first open block"
    if matching.get("review"):
        return "implement", f"Resume: matching review for this ticket: {matching['review']}"
    if matching.get("plan"):
        return "plan-review", f"Resume: matching plan for this ticket: {matching['plan']}"
    return first_slug(), "Resume requested but no ticket-scoped artifacts — start at A1"


def find_matching_artifacts(task_id: str, context_dir: Path, ticket_tokens: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    tokens = {t.lower().replace("_", "-") for t in ticket_tokens | {task_id.lower()}}
    plans = Path.cwd() / ".cursor" / "plans"
    if plans.is_dir():
        for p in plans.glob("*.plan.md"):
            name = p.name.lower().replace("_", "-")
            if any(tok in name for tok in tokens if len(tok) > 2):
                found["plan"] = str(p)
                break
    reviews = context_dir / "reviews"
    if reviews.is_dir():
        for p in reviews.glob("*.md"):
            name = p.name.lower().replace("_", "-")
            if any(tok in name for tok in tokens if len(tok) > 2):
                found["review"] = str(p)
                break
    return found


JOIN_HOOK_WARNING = (
    "WARNING: the PostToolUse Agent hook is not registered. The verdict stage check is off for this run. "
    "ACTION: install.sh install: run install.sh. Plugin or --plugin-dir session: the check is off for this "
    "session; do not run install.sh."
)


def _has_join_group(path: Path) -> bool:
    """A PostToolUse group whose matcher names Agent and whose hook runs guard_subagent_handoff.py."""
    try:
        groups = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not isinstance(groups, list):
        return False
    return any(
        isinstance(g, dict) and "Agent" in str(g.get("matcher") or "") and isinstance(g.get("hooks"), list)
        and any(isinstance(h, dict) and "guard_subagent_handoff.py" in str(h.get("command") or "")
                for h in g["hooks"])
        for g in groups
    )


def claude_join_hook_registered(workspace: Path) -> bool:
    """gh-66 PD22: the host loads the PostToolUse Agent join hook, so verdicts can be joined.

    Sources: the user settings file, the project settings, the project local settings,
    and the plugin's own hooks/hooks.json when this tree is a plugin install.
    """
    user = Path(os.environ.get("CADENCE_CLAUDE_SETTINGS_PATH") or Path.home() / ".claude" / "settings.json")
    sources = [user, workspace / ".claude" / "settings.json", workspace / ".claude" / "settings.local.json"]
    plugins = (Path.home() / ".claude" / "plugins").resolve()
    if SKILL_ROOT.resolve().is_relative_to(plugins):
        sources.append(SKILL_ROOT / "hooks" / "hooks.json")
    return any(_has_join_group(path) for path in sources)


def apply_verdict_join(session: dict, *, host: str, resumed: dict | None, workspace: Path) -> None:
    """gh-66 PD8, PD22: mark the run `verdict_join`, or warn on stderr.

    A fresh Claude Code run gets it only when the host loads the join hook. `--resume`
    keeps it only when the resumed run has it.
    """
    if resumed is not None:
        if resumed.get("verdict_join") is True:
            session["verdict_join"] = True
        return
    if host != "claude":
        return
    if not claude_join_hook_registered(workspace):
        print(JOIN_HOOK_WARNING, file=sys.stderr)
        return
    session["verdict_join"] = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Activate cadence session")
    parser.add_argument("--task-desc", help="Task description")
    parser.add_argument("--task-id", help="Explicit task ID")
    parser.add_argument("--from-branch", action="store_true", help="Derive task ID from branch")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--force", action="store_true", help="Overwrite existing session")
    parser.add_argument("--fresh", action="store_true", help="Force A1 — ignore stale artifacts")
    parser.add_argument("--resume", action="store_true", help="Resume from ticket-scoped artifacts")
    parser.add_argument(
        "--supersede",
        action="store_true",
        help="With --fresh: archive prior ticket gate artifacts",
    )
    parser.add_argument("--deactivate", action="store_true", help="Mark session completed (after R6 or a hard stop)")
    parser.add_argument(
        "--hard-stop",
        choices=HARD_STOP_CLASSES,
        help="Record a hard stop for the active run (SKILL.md: Self-healing)",
    )
    parser.add_argument("--reason", help="Why the run stops (required with --hard-stop)")
    parser.add_argument("--status", action="store_true", help="Print active session JSON")
    parser.add_argument("--check", action="store_true", help="Exit 0 if session active")
    return parser


def _is_active(session: dict | None) -> bool:
    return bool(session and session.get("active_skill") == SKILL_NAME)


def _hard_stop(args: argparse.Namespace, context_dir: Path, workspace: Path) -> int:
    if not (args.reason or "").strip():
        print("ERROR: --hard-stop needs --reason", file=sys.stderr)
        return 2
    session = load_run_session(context_dir, workspace)
    if not _is_active(session):
        print("FAIL  no active cadence session in this worktree", file=sys.stderr)
        return 1
    session["hard_stop"] = {
        "class": args.hard_stop,
        "reason": args.reason.strip(),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    save_run_session(context_dir, workspace, session)
    print(json.dumps({"hard_stop": session["hard_stop"]}, indent=2))
    return 0


def _status(args: argparse.Namespace, context_dir: Path, workspace: Path) -> int:
    session = load_run_session(context_dir, workspace)
    if not args.check:
        print(json.dumps(session or {"active": False}, indent=2))
        return 0
    if not _is_active(session):
        print("FAIL  no active cadence session", file=sys.stderr)
        return 1
    if session.get("peer_models") and session.get("host") != "claude":
        print("FAIL  the session has peer_models, but its host cannot pass a model to a peer",
              file=sys.stderr)
        return 1
    print("OK  active cadence session")
    return 0


def _deactivate(context_dir: Path, workspace: Path) -> int:
    session = load_run_session(context_dir, workspace)
    if _is_active(session) and not session.get("hard_stop") and not closure_done(workspace):
        print(
            "FAIL  the run is open: R6 (pr-comment) has not passed.\n"
            "Continue the paceline. To end the run early, record why:\n"
            f"  activate_session.py --hard-stop {{{','.join(HARD_STOP_CLASSES)}}} --reason \"...\"",
            file=sys.stderr,
        )
        return 1
    # cd-76 D3: remove only this run's P9 marker and E4 breadcrumb; keep its draft-PR record.
    run_id = str((session or {}).get("pipeline_run_id") or "")
    if run_id:
        clear_entry_invariant_ok(context_dir, run_id)
        (context_dir / "gates" / f"draft-pr-{run_id}.json").unlink(missing_ok=True)
    if session_store is not None and session_store.load_worktree_session(workspace) is not None:
        session_store.deactivate_session(workspace)
        path = session_store.session_path(workspace)
        print(json.dumps({"deactivated": True, "session_file": str(path)}, indent=2))
        return 0
    session = load_session_file(context_dir) or {}
    session["active_skill"] = None
    session["completed_at"] = datetime.now(timezone.utc).isoformat()
    session["env_exports"] = {}
    path = save_session_file(context_dir, session)
    print(json.dumps({"deactivated": True, "session_file": str(path)}, indent=2))
    return 0


def _flag_error(args: argparse.Namespace) -> str | None:
    if args.fresh and args.resume:
        return "ERROR: --fresh and --resume are mutually exclusive"
    if args.supersede and not args.fresh:
        return "ERROR: --supersede requires --fresh"
    return None


def resolve_task_id(args: argparse.Namespace, branch: str | None) -> tuple[str | None, str | None]:
    """(task_id, task_source): --task-id, then the branch (with --from-branch), then --task-desc."""
    if args.task_id:
        return args.task_id, "explicit"
    if args.from_branch and branch and branch not in ("main", "master", "HEAD"):
        return derive_task_id_from_branch(branch), f"branch:{branch}"
    if args.task_desc:
        return derive_task_id_from_description(args.task_desc), "description"
    return None, None


def _rejoin_active_run(existing: dict, noted: bool, orchestrator: str | None, context_dir: Path,
                       workspace: Path) -> int:
    """The task's run is already active here: print it, and add a new subagent orchestrator.

    cd-76 R3-D6a, R1 D-1: a new subagent that activates a subagent's run again becomes
    one of its orchestrators. A top-level run stays top level: a subagent's activation
    must not take it away from the main agent's stop guard. A top-level note adds nothing.
    """
    ids = [a for a in existing.get("orchestrator_agent_ids") or [] if isinstance(a, str)]
    if noted and orchestrator and ids and orchestrator not in ids:
        existing = {**existing, "orchestrator_agent_ids": [*ids, orchestrator]}
        save_run_session(context_dir, workspace, existing)
    print(json.dumps(existing, indent=2))
    print("\n=== Cadence session already active ===", file=sys.stderr)
    return 0


def _orchestrators(resumed: dict | None, noted: bool, orchestrator: str | None) -> tuple[list[str], str]:
    """The run's orchestrator ids (kept on resume) and where the newest one came from."""
    ids = [a for a in (resumed or {}).get("orchestrator_agent_ids") or [] if isinstance(a, str)]
    if orchestrator and orchestrator not in ids:
        ids.append(orchestrator)
    source = ("note" if orchestrator else "toplevel") if noted else "missing"
    return ids, source


def _prepare_run(args: argparse.Namespace, context_dir: Path, fresh_run: bool, run_id: str, task_id: str,
                 tokens: set[str]) -> tuple[dict[str, str], list[str]]:
    """(matching artifacts of a resumed run, superseded paths of a fresh --supersede run)."""
    matching = {} if fresh_run else find_matching_artifacts(task_id, context_dir, tokens)
    superseded: list[str] = []
    if fresh_run:
        clear_entry_invariant_ok(context_dir, run_id)  # cd-76 D3: this run's marker only
    if fresh_run and args.supersede:
        superseded = supersede_ticket_artifacts(context_dir, ticket_tokens=tokens, run_id=run_id, task_id=task_id)
    return matching, superseded


def _env_exports(task_id: str, fresh_run: bool, run_id: str) -> dict[str, str]:
    return {
        "CURSOR_ACTIVE_SKILL": SKILL_NAME,
        "CADENCE_TASK_ID": task_id,
        "CADENCE_FRESH_RUN": "1" if fresh_run else "0",
        "CADENCE_PIPELINE_RUN_ID": run_id,
        "CADENCE_HOME": str(SKILL_ROOT),
    }


def _add_run_marks(session: dict, args: argparse.Namespace, resumed: dict | None, snapshot: dict[str, str],
                   config: dict | None = None) -> None:
    """The resolved config, the peer_models snapshot, the lean-record marker, and how a --resume ended."""
    if snapshot:
        session["peer_models"] = snapshot
    kept = (resumed or {}).get("config")
    if kept or config:
        session["config"] = kept or config  # ADR-0016: each layer file with its sha256, and the values
    # cd-64 D4: on a run marked here, only the PreToolUse hook writes lean delegation records.
    if (session["host"] == "claude" if session["fresh_run"] else (resumed or {}).get("lean_record_by_hook") is True):
        session["lean_record_by_hook"] = True
    if resumed:
        session["resumed_at"] = datetime.now(timezone.utc).isoformat()
    elif args.resume:
        session["resume_fallback"] = "fresh"


def _report_activation(session: dict, session_file: Path) -> None:
    print(json.dumps(session, indent=2))
    if session["host"] == "claude" and session["orchestrator_source"] == "missing":
        print("WARNING: orchestrator unknown: activate through the Bash tool", file=sys.stderr)
    print("\n=== Cadence Session Activated ===", file=sys.stderr)
    print(f"Pipeline-Run: {session['pipeline_run_id']}", file=sys.stderr)
    print(f"Task ID: {session['task_id']}", file=sys.stderr)
    print(f"Resume: {session['resume_block']} ({session['resume_slug']})", file=sys.stderr)
    print(f"Reason: {session['resume_reason']}", file=sys.stderr)
    print(f"Session file: {session_file}", file=sys.stderr)


def _start_run(args: argparse.Namespace, ctx: dict, resumed: dict | None, snapshot: dict[str, str],
               noted: bool, orchestrator: str | None) -> int:
    """Write the session of a fresh or resumed run. ctx: workspace, primary, context_dir, branch, task."""
    workspace, context_dir, task_id = ctx["workspace"], ctx["context_dir"], ctx["task_id"]
    if args.resume and resumed is None:
        print(f"--resume: no resumable run for {task_id} in this worktree; started a fresh run", file=sys.stderr)
    fresh_run = resumed is None
    pipeline_run_id = str(resumed["pipeline_run_id"]) if resumed else str(uuid4())
    orchestrators, orchestrator_source = _orchestrators(resumed, noted, orchestrator)
    tokens = ticket_tokens_for(task_id, ctx["branch"])
    matching, superseded_paths = _prepare_run(args, context_dir, fresh_run, pipeline_run_id, task_id, tokens)

    reopened = bool(resumed and resumed.get("status") == "completed")
    resume_slug, resume_reason = compute_resume_slug(fresh_run=fresh_run, matching=matching, reopened=reopened)
    # Validate slug against registry (fail loud if registry broken)
    if resume_slug not in set(stage_slugs(SKILL_ROOT)):
        print(f"ERROR: resume slug {resume_slug!r} not in stages.json", file=sys.stderr)
        return 1

    session = {
        "skill": SKILL_NAME,
        "pipeline_run_id": pipeline_run_id,
        "task_id": task_id,
        "task_source": ctx["task_source"],
        "task_description": args.task_desc or "",
        "branch": ctx["branch"],
        "ticket_tokens": sorted(tokens),
        "fresh_run": fresh_run,
        "supersede_on_activate": bool(superseded_paths),
        "superseded_paths": superseded_paths,
        "activated_at": (resumed or {}).get("activated_at") or datetime.now(timezone.utc).isoformat(),
        "owner_session_id": owner_id(workspace),
        "orchestrator_agent_ids": orchestrators,
        "orchestrator_source": orchestrator_source,
        # Claude Code marks subagent tool calls (agent_id); peer verdicts are attested there.
        "host": ctx["host"],
        "base": resolve_base(workspace, {}),
        "workspace": str(workspace),
        "primary_checkout": str(ctx["primary"]),
        "context_root": str(context_dir),
        "matching_artifacts": matching,
        "resume_slug": resume_slug,
        "resume_block": next(
            (r["block"] for r in load_stages(SKILL_ROOT) if r["slug"] == resume_slug),
            None,
        ),
        "resume_reason": resume_reason,
        "active_skill": SKILL_NAME,
        "registry_version": 1,
        "env_exports": _env_exports(task_id, fresh_run, pipeline_run_id),
    }
    if reopened:
        session["reopened_at"] = datetime.now(timezone.utc).isoformat()
    _add_run_marks(session, args, resumed, snapshot, ctx.get("config"))
    apply_verdict_join(session, host=session["host"], resumed=resumed, workspace=workspace)
    _report_activation(session, save_run_session(context_dir, workspace, session))
    return 0


def _activate(args: argparse.Namespace, workspace: Path, primary: Path, context_dir: Path) -> int:
    error = _flag_error(args)
    if error:
        print(error, file=sys.stderr)
        return 1
    branch = get_branch_name(workspace)
    task_id, task_source = resolve_task_id(args, branch)
    if not task_id:
        print(
            "ERROR: Cannot determine task ID. Provide --task-id, --task-desc, or --from-branch",
            file=sys.stderr,
        )
        return 1

    host = "claude" if os.environ.get("CLAUDECODE") == "1" else "other"
    existing = None if args.force else load_run_session(context_dir, workspace)
    already_active = bool(
        _is_active(existing) and existing.get("task_id") == task_id and not args.fresh and not args.resume
    )
    resumed = None
    snapshot: dict[str, str] = {}
    config_record: dict | None = None
    if not already_active:
        # cd-58 D1: the config is read once per run, before anything is written or taken.
        resumed = resumable_run(workspace, task_id, context_dir) if args.resume else None
        try:
            resolved = cadence_config.load(SKILL_ROOT, workspace)
        except cadence_config.ConfigError as exc:
            print(f"FAIL  config: {exc}", file=sys.stderr)
            print("ACTION: fix the layer, then activate again (python3 scripts/cadence_config.py resolve)", file=sys.stderr)
            return 2
        config_record = resolved.record()
        snapshot, model_errors = activation_snapshot(resumed, resolved.get("peer_models"), host)
        for error in model_errors:
            print(f"FAIL  peer_models: {error}", file=sys.stderr)
        if model_errors:
            return 2

    # Take the orchestrator note after the checks, so a failed activation keeps it for the next try.
    noted, orchestrator = (
        session_store.take_pending_orchestrator(context_dir) if session_store is not None else (False, None)
    )
    if already_active:
        return _rejoin_active_run(existing, noted, orchestrator, context_dir, workspace)
    ctx = {"workspace": workspace, "primary": primary, "context_dir": context_dir, "branch": branch,
           "task_id": task_id, "task_source": task_source, "host": host, "config": config_record}
    return _start_run(args, ctx, resumed, snapshot, noted, orchestrator)


def main() -> int:
    args = _parser().parse_args()

    workspace = Path(args.workspace).resolve()
    primary = find_primary_checkout(workspace)
    context_dir = canonical_context_dir(workspace)
    context_dir.mkdir(parents=True, exist_ok=True)

    if args.hard_stop:
        return _hard_stop(args, context_dir, workspace)
    if args.status or args.check:
        return _status(args, context_dir, workspace)
    if args.deactivate:
        return _deactivate(context_dir, workspace)
    return _activate(args, workspace, primary, context_dir)


if __name__ == "__main__":
    raise SystemExit(main())
