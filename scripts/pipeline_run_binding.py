#!/usr/bin/env python3
"""Pipeline run binding — version gate artifacts to activate_session runs.

Fresh runs get a unique pipeline_run_id. Gated artifacts must either carry that
id (Pipeline-Run: markdown / pipeline_run_id JSON) or be written after activation.
--supersede archives prior ticket-scoped gate artifacts before a fresh restart.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from labeled_lines import labeled_values  # noqa: E402

RUN_LABELS = ("pipeline-run", "pipeline run", "pipeline_run_id")
UUID36 = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
JSON_RUN_KEYS = ("pipeline_run_id", "pipelineRunId", "run_id")


def parse_activated_at(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def load_session(context_dir: Path) -> dict | None:
    """Load the active session anchored at ``context_dir``.

    Backward compatible with the legacy full singleton. When the singleton has
    become a ``{"ref": ...}`` redirect (per-worktree session store), the ref is
    followed relative to ``context_dir``.
    """
    path = context_dir / "cadence-session.json"
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
    return data


def run_ids_in_text(text: str) -> set[str]:
    """Every run id a Markdown artifact names, in any of the labeled_lines forms."""
    return set(labeled_values(text, RUN_LABELS, UUID36))


def read_run_id_from_text(text: str) -> str | None:
    values = labeled_values(text, RUN_LABELS, UUID36)
    return values[0] if values else None


def _session_store():
    shared = Path(__file__).resolve().parent.parent / "shared" / "scripts"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import context_root
    import session_store

    return context_root, session_store


def _is_active(data: object) -> bool:
    return isinstance(data, dict) and data.get("active_skill") == "cadence" and data.get("status") != "completed"


def worktree_run_session(context_dir: Path, cwd: Path | None = None) -> dict | None:
    """The active run of the worktree at ``cwd``; never another worktree's (G-D4).

    - When ``context_dir`` is the cwd repo's canonical .context: that worktree's own
      by-worktree session, or a legacy single-file session whose workspace is this worktree.
    - When ``context_dir`` is another context (an explicit --context, a test): its legacy
      single-file session, as before.
    The root redirect to another worktree's run is never followed. Read-only: creates nothing.
    """
    context_root, session_store = _session_store()
    context_dir = Path(context_dir)
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    try:
        own_context = context_root.context_path(cwd).resolve() == context_dir.resolve()
    except OSError:
        own_context = False
    if own_context:
        data = session_store._read_json(context_dir / session_store.BY_WORKTREE / f"{session_store.worktree_key(cwd)}.json")
        if _is_active(data) and "ref" not in data:
            return data
    legacy = session_store._read_json(context_dir / "cadence-session.json")
    if not _is_active(legacy) or "ref" in legacy:
        return None
    workspace = legacy.get("workspace")
    if not own_context or not workspace:
        return legacy
    try:
        same = Path(workspace).resolve() == session_store._resolve_toplevel(cwd)
    except OSError:
        same = False
    return legacy if same else None


def run_for(context_dir: Path, *, cwd: Path | None = None, target: Path | str | None = None) -> dict | None:
    """The active run of one worktree, never the root redirect.

    With ``target``, the worktree is the git top level that holds it; a path that does
    not exist yet resolves through its first existing parent. Else the worktree of
    ``cwd`` (default: the process cwd). No run, or a target outside any repository,
    gives None. Per-run readers then treat the run's state as absent.
    """
    if target is not None:
        path = Path(target)
        anchor = next((p for p in [path, *path.parents] if p.is_dir()), None)
        if anchor is None:
            return None
        context_root, _ = _session_store()
        top = context_root.git_toplevel(anchor)
        if top is None:
            return None
        cwd = top
    return worktree_run_session(context_dir, cwd)


def current_session(context_dir: Path, cwd: Path | None = None) -> dict:
    """The worktree's own run, else the session the root redirect names (planning gates)."""
    return worktree_run_session(context_dir, cwd) or load_session(Path(context_dir)) or {}


def known_task_ids(context_dir: Path) -> set[str]:
    """The task id of every run record in ``context_dir``, completed runs too."""
    _context_root, store = _session_store()
    return {str(r["task_id"]) for r in store.run_records(Path(context_dir)) if r.get("task_id")}


def other_workspace_hint(context_dir: Path, cwd: Path | None = None) -> str:
    """A pointer to the active run of another worktree, for E1 and R4 failures (SF-2)."""
    data = load_session(Path(context_dir))
    if not _is_active(data):
        return ""
    workspace = data.get("workspace")
    if not isinstance(workspace, str) or not workspace:
        return ""
    _, session_store = _session_store()
    here = session_store._resolve_toplevel(Path(cwd) if cwd is not None else Path.cwd())
    there = Path(workspace).resolve()
    if there == here:
        return ""
    return f"the active run is in {there}: run the gates from that worktree or with --workspace {there}"


def read_run_id_from_json(data: dict) -> str | None:
    for key in JSON_RUN_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    return None


def read_run_id(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if path.suffix == ".json":
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                rid = read_run_id_from_json(data)
                if rid:
                    return rid
        except json.JSONDecodeError:
            pass
    return read_run_id_from_text(text)


def plan_content_hash(plan_path: Path) -> str | None:
    try:
        import hashlib

        digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        return digest[:12]
    except OSError:
        return None


def artifact_is_current(
    path: Path,
    *,
    session: dict,
    plan_path: Path | None = None,
    slack_seconds: float = 2.0,
) -> tuple[bool, str]:
    """Return (ok, detail) for fresh-run artifact binding."""
    if not path.is_file():
        return True, "missing (not stale)"

    run_id = str(session.get("pipeline_run_id") or "").lower()
    if not run_id:
        return True, "no session run_id — skip binding"

    artifact_run = read_run_id(path)
    if artifact_run == run_id:
        return True, f"run_id match ({run_id[:8]}…)"

    activated_ts = parse_activated_at(session.get("activated_at"))
    if activated_ts is not None:
        mtime = path.stat().st_mtime
        if mtime >= activated_ts - slack_seconds:
            return True, "mtime after activation (unstamped new artifact)"

    if plan_path and path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            bound_plan = data.get("plan_path") or data.get("planPath")
            if bound_plan and Path(str(bound_plan)).resolve() == plan_path.resolve():
                plan_hash = plan_content_hash(plan_path)
                stored_hash = data.get("plan_content_hash") or data.get("planContentHash")
                if plan_hash and stored_hash and plan_hash == stored_hash:
                    return True, "plan hash match (legacy gate JSON)"
        except (OSError, json.JSONDecodeError):
            pass

    return False, (
        f"stale prior run (artifact run={artifact_run or 'none'}, "
        f"session run={run_id[:8]}…) — activate_session --fresh --supersede"
    )


def stamp_markdown(text: str, run_id: str) -> str:
    if read_run_id_from_text(text):
        return text
    marker = f"Pipeline-Run: {run_id}\n"
    lines = text.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("RUN_BY_"):
            insert_at = i + 1
            break
    else:
        insert_at = 0
    lines.insert(insert_at, marker)
    return "".join(lines)


def stamp_json(data: dict, run_id: str, *, plan_path: Path | None = None) -> dict:
    out = dict(data)
    out["pipeline_run_id"] = run_id
    if plan_path is not None:
        out["plan_path"] = str(plan_path.resolve())
        ph = plan_content_hash(plan_path)
        if ph:
            out["plan_content_hash"] = ph
    return out


def _token_at_boundary(token: str, name: str) -> bool:
    """True when ``token`` is a whole token in ``name`` (107 never matches gh-1070)."""
    norm = token.lower().replace("_", "-")
    return re.search(rf"(?<![a-z0-9]){re.escape(norm)}(?![a-z0-9])", name.lower().replace("_", "-")) is not None


def _benchmark_hits(context_dir: Path, tokens: set[str]) -> list[Path]:
    """Ticket files in benchmarks/. Only tokens with a digit count: a word branch
    token such as ``plan`` or ``code`` is a whole token in the files of all tickets."""
    keep = [t for t in tokens if re.search(r"[0-9]", t)]
    bench = context_dir / "benchmarks"
    if not keep or not bench.is_dir():
        return []
    return [
        p for p in bench.iterdir() if p.is_file() and any(_token_at_boundary(t, p.name) for t in keep)
    ]


def _ticket_globs(context_dir: Path, ticket_tokens: set[str]) -> list[Path]:
    hits: list[Path] = []
    dirs = [
        context_dir / "deferrals",
        context_dir / "adversarial",
        context_dir / "reviews",
        context_dir / "test-benchmarks",
    ]
    json_names = [
        "pt-schema-gate-plan.json",
        "proof-harness-gate.json",
    ]
    for name in json_names:
        p = context_dir / name
        if p.is_file():
            hits.append(p)
    for extra in context_dir.glob("*-gate-plan*.json"):
        if extra.is_file():
            hits.append(extra)

    tokens = {t.lower().replace("_", "-") for t in ticket_tokens if t}
    for d in dirs:
        if not d.is_dir():
            continue
        for path in d.iterdir():
            if not path.is_file():
                continue
            name = path.name.lower().replace("_", "-")
            if any(tok.replace("_", "-") in name for tok in tokens):
                hits.append(path)
    hits.extend(_benchmark_hits(context_dir, tokens))

    # Dedupe preserve order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in hits:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def supersede_ticket_artifacts(
    context_dir: Path,
    *,
    ticket_tokens: set[str],
    run_id: str,
    task_id: str,
) -> list[str]:
    """Move ticket-scoped gate artifacts into .context/superseded/{run_id}/."""
    dest_root = context_dir / "superseded" / run_id
    dest_root.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []

    for src in _ticket_globs(context_dir, ticket_tokens):
        rel = src.relative_to(context_dir)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest_root / f"{rel.as_posix().replace('/', '__')}"
        shutil.move(str(src), str(dest))
        moved.append(str(rel))

    manifest = {
        "pipeline_run_id": run_id,
        "task_id": task_id,
        "superseded_at": datetime.now(timezone.utc).isoformat(),
        "paths": moved,
    }
    runs_dir = context_dir / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "superseded.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return moved


def check_fresh_run_artifacts(
    *,
    session: dict,
    plan: Path | None,
    artifact_paths: dict[str, Path | None],
) -> list[tuple[str, bool, str]]:
    """Returns list of (check_name, passed, detail) for fresh-run binding."""
    if not session.get("fresh_run"):
        return []

    run_id = session.get("pipeline_run_id")
    if not run_id:
        return [("session-run-id", False, "fresh_run session missing pipeline_run_id")]

    results: list[tuple[str, bool, str]] = []
    for name, path in artifact_paths.items():
        if path is None:
            continue
        ok, detail = artifact_is_current(path, session=session, plan_path=plan)
        if not ok:
            results.append((f"artifact-stale-run-{name}", False, detail))
        else:
            results.append((f"artifact-run-binding-{name}", True, detail))
    return results
