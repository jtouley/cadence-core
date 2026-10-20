#!/usr/bin/env python3
"""Per-worktree cadence session store.

Concurrent cadence runs share one primary ``.context`` (resolved via
``context_root``) but must not clobber each other's active session. Each git
worktree gets its own session file, keyed by a hash of its toplevel path:

    .context/sessions/by-worktree/<sha256(toplevel)[:16]>.json   # full session
    .context/sessions/by-task/<task_id>.json                     # {"ref": ...}
    .context/cadence-session.json                           # {"ref": ...}

The legacy singleton (``cadence-session.json`` holding a full session) is
still readable for backward compatibility and can be migrated in place: after
migration the singleton holds only a ``{"ref": ...}`` redirect.

Durable artifacts (plans, reviews, gates) continue to live on the primary
``.context`` via ``context_root``; only the *active session + active skill +
env exports* are sharded per worktree here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
from context_root import context_dir, context_path, find_primary_checkout, git_toplevel  # noqa: E402,F401

LEGACY_NAME = "cadence-session.json"
BY_WORKTREE = "sessions/by-worktree"
BY_TASK = "sessions/by-task"


def _resolve_toplevel(cwd: Path | None) -> Path:
    """Git toplevel of ``cwd`` (resolved), or the resolved cwd if not a repo."""
    top = git_toplevel(cwd)
    if top is not None:
        return top.resolve()
    base = cwd if cwd is not None else Path.cwd()
    return base.resolve()


def worktree_key(cwd: Path | None = None) -> str:
    top = _resolve_toplevel(cwd)
    return hashlib.sha256(str(top).encode("utf-8")).hexdigest()[:16]


def _safe_task_name(task_id: str) -> str:
    return task_id.replace("/", "__").replace("\\", "__")


def session_path(cwd: Path | None = None) -> Path:
    """Where this worktree's session lives. Reading it creates no directory; writers make the parents."""
    return context_path(cwd) / BY_WORKTREE / f"{worktree_key(cwd)}.json"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _follow_ref(ctx: Path, data: dict) -> dict | None:
    ref = data.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return None
    return _read_json(ctx / ref)


def _session_is_finished(data: dict) -> bool:
    """A completed record is not the active session."""
    return data.get("status") == "completed"


def load_worktree_session(cwd: Path | None = None) -> dict | None:
    """This worktree's own session file, or None. Never another worktree's.

    ``load_session`` falls back to the root redirect, which points at the last
    activation in any worktree. Owner checks must not use that fallback.
    """
    return _read_json(session_path(cwd))


PENDING_OWNER = "sessions/pending-owner.json"
PENDING_OWNER_SECONDS = 120


def write_pending_owner(owner: str, cwd: Path) -> Path:
    """Note the chat id that is about to run activation (hosts with no id in the env)."""
    import time

    path = context_dir(cwd) / PENDING_OWNER
    _write_json(path, {"owner": owner, "toplevel": str(_resolve_toplevel(cwd)), "at": time.time()})
    return path


def take_pending_owner(cwd: Path) -> str:
    """The noted chat id for this worktree when fresh; the note is used once."""
    import time

    path = context_path(cwd) / PENDING_OWNER
    data = _read_json(path)
    if data is None:
        return ""
    path.unlink(missing_ok=True)
    fresh = time.time() - float(data.get("at") or 0) <= PENDING_OWNER_SECONDS
    if fresh and data.get("toplevel") == str(_resolve_toplevel(cwd)):
        return str(data.get("owner") or "")
    return ""


PENDING_ORCHESTRATOR = "sessions/pending-orchestrator"
PENDING_ORCHESTRATOR_SECONDS = 120


def write_pending_orchestrator(agent_id: str | None, context: Path) -> Path:
    """Note the agent that is about to run activation (cd-47 D2). One pool per canonical .context."""
    import time

    now = time.time()
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = Path(context) / PENDING_ORCHESTRATOR / f"{stamp}-{agent_id or 'toplevel'}.json"
    _write_json(path, {"agent_id": agent_id, "at": now})
    return path


def take_pending_orchestrator(context: Path) -> tuple[bool, str | None]:
    """(found, agent_id) of the newest note of 120 s or less; that note and every stale one are deleted."""
    import time

    now = time.time()
    fresh = []
    for path in (Path(context) / PENDING_ORCHESTRATOR).glob("*.json"):
        data = _read_json(path) or {}
        try:
            at = float(data.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        if now - at <= PENDING_ORCHESTRATOR_SECONDS:
            fresh.append((at, path.name, path, data.get("agent_id")))
        else:
            path.unlink(missing_ok=True)
    if not fresh:
        return False, None
    _at, _name, path, agent = max(fresh, key=lambda note: (note[0], note[1]))
    path.unlink(missing_ok=True)
    return True, agent if isinstance(agent, str) and agent else None


def worktree_sessions(ctx: Path) -> list[tuple[Path, dict]]:
    """Every per-worktree session file in ``ctx`` with its data."""
    out: list[tuple[Path, dict]] = []
    for path in sorted((Path(ctx) / BY_WORKTREE).glob("*.json")):
        data = _read_json(path)
        if data is not None and "ref" not in data:
            out.append((path, data))
    return out


def run_records(ctx: Path) -> list[dict]:
    """Every run record in ``ctx``: the per-worktree files and a legacy full root session."""
    records = [data for _path, data in worktree_sessions(ctx)]
    legacy = _read_json(Path(ctx) / LEGACY_NAME)
    if legacy is not None and "ref" not in legacy:
        records.append(legacy)
    return records


def load_session(cwd: Path | None = None) -> dict | None:
    """Resolve the active session for ``cwd``.

    Order:
      1. by-worktree file for this cwd (follow ``ref`` if it is a redirect)
      2. legacy ``cadence-session.json`` redirect (follow its ``ref``)
      3. legacy full singleton, only when its ``workspace`` resolves to the
         cwd git toplevel (so we never leak another worktree's session).

    A by-worktree file with ``status: completed`` is skipped so it cannot hide
    a newer active singleton. The lean Task hook keys off that active record.
    """
    ctx = context_dir(cwd)

    wt = session_path(cwd)
    data = _read_json(wt)
    if data is not None and not _session_is_finished(data):
        if "ref" in data:
            followed = _follow_ref(ctx, data)
            if followed is not None and not _session_is_finished(followed):
                return followed
        else:
            return data

    legacy = ctx / LEGACY_NAME
    legacy_data = _read_json(legacy)
    if legacy_data is not None:
        if "ref" in legacy_data:
            # The redirect names the last activation in any worktree. It is this worktree's
            # session only when its workspace is this worktree (the predecessor's concurrency test).
            followed = _follow_ref(ctx, legacy_data)
            if followed is not None and not _session_is_finished(followed) and _owns(followed, cwd):
                return followed
        elif _owns(legacy_data, cwd):
            return legacy_data
    return None


def _owns(session: dict, cwd: Path | None) -> bool:
    """True when the session's ``workspace`` is the git toplevel of ``cwd``. No workspace: False."""
    ws = session.get("workspace")
    if not ws:
        return False
    try:
        return Path(ws).resolve() == _resolve_toplevel(cwd)
    except OSError:
        return False


def resolve_session_at(ctx: Path, cwd: Path | None = None) -> dict | None:
    """Best-effort resolve for readers that already know their ``.context`` dir.

    Tries the per-worktree store first (concurrent isolation, redirect-aware),
    then falls back to a direct read of ``ctx/cadence-session.json`` —
    following a ``{"ref": ...}`` redirect — so legacy full singletons and tests
    that monkeypatch a script's own ``ctx_root`` keep working.
    """
    try:
        resolved = load_session(cwd)
    except Exception:
        resolved = None
    if resolved:
        return resolved
    data = _read_json(ctx / LEGACY_NAME)
    if data is None:
        return None
    if "ref" in data:
        return _follow_ref(ctx, data)
    return data


def _ref_for(key: str) -> dict:
    return {"ref": f"{BY_WORKTREE}/{key}.json"}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_session(session: dict, cwd: Path | None = None) -> Path:
    """Persist ``session`` for this worktree.

    Writes the full session to the by-worktree file, a ``{"ref": ...}`` pointer
    under by-task/<task_id>, and the root redirect. Returns the by-worktree path.
    """
    ctx = context_dir(cwd)
    key = worktree_key(cwd)
    session = dict(session)
    session["worktree_key"] = key
    session.setdefault("workspace", str(_resolve_toplevel(cwd)))

    wt_path = ctx / BY_WORKTREE / f"{key}.json"
    _write_json(wt_path, session)

    ref = _ref_for(key)
    task_id = session.get("task_id")
    if task_id:
        _write_json(ctx / BY_TASK / f"{_safe_task_name(str(task_id))}.json", ref)
    _write_json(ctx / LEGACY_NAME, ref)
    return wt_path


def deactivate_session(cwd: Path | None = None) -> dict:
    """Mark this worktree's session complete and clear its active markers.

    Scoped to the cwd worktree only: other worktrees' sessions, the by-task
    pointers, and the root redirect are left untouched.
    """
    wt = session_path(cwd)
    session = _read_json(wt)
    if session is None:
        session = load_session(cwd) or {}
    session = dict(session)
    session["status"] = "completed"
    session["completed_at"] = datetime.now(timezone.utc).isoformat()
    session["active_skill"] = None
    session["env_exports"] = {}
    _write_json(wt, session)
    return session


def migrate_legacy_singleton(ctx: Path) -> Path | None:
    """Convert a legacy full singleton into by-worktree + redirect + by-task.

    Idempotent: if the legacy file is already a redirect, follows it and
    returns the existing target (or None if the target is missing / there is
    nothing to migrate).
    """
    legacy = ctx / LEGACY_NAME
    data = _read_json(legacy)
    if data is None:
        return None
    if "ref" in data:
        target = ctx / str(data["ref"])
        return target if target.is_file() else None

    ws = data.get("workspace")
    if not ws:
        return None
    key = worktree_key(Path(ws))

    data = dict(data)
    data["worktree_key"] = key
    wt_path = ctx / BY_WORKTREE / f"{key}.json"
    _write_json(wt_path, data)

    ref = _ref_for(key)
    task_id = data.get("task_id")
    if task_id:
        _write_json(ctx / BY_TASK / f"{_safe_task_name(str(task_id))}.json", ref)
    _write_json(legacy, ref)
    return wt_path


def _print_env(session: dict) -> None:
    env = session.get("env_exports")
    if isinstance(env, dict):
        for key, value in env.items():
            print(f"export {key}={value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-worktree cadence session store")
    parser.add_argument("command", nargs="?", choices=["migrate-legacy"], default=None)
    parser.add_argument("--print-path", action="store_true", help="Print the by-worktree session path for cwd")
    parser.add_argument("--print-active-skill", action="store_true", help="Print active_skill for cwd session")
    parser.add_argument("--print-env", action="store_true", help="Print env_exports as shell export lines")
    parser.add_argument("--context", default=None, help="Explicit .context dir (migrate-legacy)")
    parser.add_argument("--cwd", default=None, help="Resolve session relative to this cwd")
    args = parser.parse_args()

    cwd = Path(args.cwd).resolve() if args.cwd else None

    if args.command == "migrate-legacy":
        ctx = Path(args.context).resolve() if args.context else context_dir(cwd)
        result = migrate_legacy_singleton(ctx)
        print(result if result else "")
        return 0
    if args.print_active_skill:
        session = load_session(cwd)
        print((session or {}).get("active_skill") or "")
        return 0
    if args.print_env:
        _print_env(load_session(cwd) or {})
        return 0
    # Default (and --print-path): print the by-worktree session path.
    print(session_path(cwd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
