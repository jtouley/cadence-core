#!/usr/bin/env python3
"""A model setting per delegated peer stage.

A config layer may hold `peer_models`: a map from a delegated peer stage slug
(or `default`) to a model alias. A1 validates it and copies the resolved map into the
session, so a config edit during a run does not change the run. With no
`peer_models`, nothing changes.

Only Claude Code can pass a model to a peer, so A1 refuses `peer_models` on any other
host.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
for _path in (_SCRIPTS, _SCRIPTS.parent / "shared" / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# Delegated peer stages whose gate checks a delegation (stages.json slugs).
# E1 /spec-driven, E4 /commit-push-pr and P4 /plan-update are not peer-model stages.
PEER_MODEL_SLUGS = (
    "validate",
    "plan-create",
    "plan-review",
    "adversarial-review",
    "test-benchmark-plan",
    "pr-review-local",
    "test-benchmark-pr",
)
# The model aliases the host's Agent tool accepts. A new alias needs a code change.
ALIASES = ("haiku", "sonnet", "opus", "fable")
DEFAULT_KEY = "default"

MAP_ERROR = "peer_models must be a map of stage slug to model alias"
HOST_ERROR = ("peer_models is set, but this host cannot pass a model to a peer. "
              "Remove peer_models or run on Claude Code.")


def resolve(raw: object, host: str) -> tuple[dict[str, str], list[str]]:
    """The per-slug model map for `raw`, and every error in it.

    `default` fills each slug with no own entry. An empty map is the same as no
    `peer_models`. On any error the map is empty.
    """
    if not isinstance(raw, dict):
        return {}, [MAP_ERROR]
    if not raw:
        return {}, []
    allowed_keys = (*PEER_MODEL_SLUGS, DEFAULT_KEY)
    errors = []
    for key, value in raw.items():
        if key not in allowed_keys:
            errors.append(f"unknown key {key!r}; allowed keys: {', '.join(allowed_keys)}")
        elif value not in ALIASES:
            errors.append(f"{key!r} has the value {value!r}; use one of: {', '.join(ALIASES)}")
    if host != "claude":
        errors.append(HOST_ERROR)
    if errors:
        return {}, errors
    default = raw.get(DEFAULT_KEY)
    resolved = {slug: raw.get(slug, default) for slug in PEER_MODEL_SLUGS}
    return {slug: model for slug, model in resolved.items() if model}, []


def activation_snapshot(resumed: dict | None, peer_models: object, host: str) -> tuple[dict[str, str], list[str]]:
    """The `peer_models` snapshot for an activation, and its errors.

    A resumed run keeps its own snapshot, so a later config change cannot change a resumed
    run. A fresh run takes `peer_models` of the resolved config.
    """
    if resumed is not None:
        kept = resumed.get("peer_models")
        return (dict(kept) if isinstance(kept, dict) and kept else {}), []
    return resolve(peer_models if peer_models is not None else {}, host)


# --- evidence rows -------------------------------------------------------------------

DECISIONS = ("allow", "deny")


def rows_path(context_dir: Path, run_id: str) -> Path:
    return Path(context_dir) / "attest" / f"{run_id}.models.jsonl"


def record_call(context_dir: Path, run_id: str, row: dict) -> None:
    """Append one row: one write of one full line, so peers that run at once cannot mix rows.

    Only hooks/guard_subagent_handoff.py calls this; agents may not write under attest/.
    """
    path = rows_path(context_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def read_rows(context_dir: Path, run_id: str) -> list[dict]:
    """The run's good rows. A line that is not a JSON object, or that has no string stage or a
    decision outside allow/deny, is skipped. An unreadable file is the same as no rows."""
    try:
        text = rows_path(context_dir, run_id).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("stage"), str) and row.get("decision") in DECISIONS:
            found.append(row)
    return found


# --- gate check ----------------------------------------------------------------------

# Lean delegation records name the command stage; two differ from the stages.json slug.
SLUG_FOR_STAGE = {
    "vibe-test": "validate",
    "de-adversarial-reviewer": "adversarial-review",
    **{slug: slug for slug in PEER_MODEL_SLUGS},
    "pr-review-update": None,  # An R4 peer stage with no peer_models key
}


def require_peer_model(context_dir: Path, slug: str) -> list[str]:
    """Errors unless the run has an allowed peer call for `slug` on its configured model.

    Only the hook's rows count. A subagent_provenance `begin` record is the
    orchestrator's own claim and is never read here.
    """
    from pipeline_run_binding import current_session

    session = current_session(Path(context_dir))
    if "peer_models" not in session:
        return []
    configured = session["peer_models"]
    if not isinstance(configured, dict):
        return [f"{slug}: session peer_models is not a map of stage slug to model alias. Start a run with --fresh."]
    wanted = configured.get(slug)
    if wanted is None:
        return []
    run_id = str(session.get("pipeline_run_id") or "")
    if session.get("verdict_join") is True:
        allowed = _joined_allowed_calls(Path(context_dir), run_id, slug)
    else:
        allowed = [r for r in read_rows(Path(context_dir), run_id) if r["stage"] == slug and r["decision"] == "allow"]
    if not allowed:
        return [(f"{slug}: peer_models sets {wanted}, but no allowed peer call for {slug} is recorded in this run. "
                 f'Call the peer through the Agent tool with model: "{wanted}".')]
    other = next((r for r in allowed if r.get("model") != wanted), None)
    if other is not None:
        return [(f"{slug}: peer_models sets {wanted}, but a recorded peer call used {other.get('model')} "
                 f"(tool_use_id {other.get('tool_use_id')}).")]
    return []


def _joined_allowed_calls(context_dir: Path, run_id: str, slug: str) -> list[dict]:
    """Allowed call rows of `slug` whose call started a peer (a join row exists)."""
    import peer_attest

    # A join row with no agent names no started peer.
    joined = {r.get("tool_use_id") for r in peer_attest.every_run_rows(context_dir, "joins")
              if isinstance(r.get("agent_id"), str) and r.get("agent_id")}
    return [r for r in peer_attest.call_rows(context_dir, run_id)
            if SLUG_FOR_STAGE.get(str(r.get("stage"))) == slug and r.get("decision") == "allow"
            and r.get("tool_use_id") in joined]


def models_for_stages(configured: object, stages: frozenset[str]) -> dict[str, str]:
    """The configured model of each delegation stage that has a peer_models slug."""
    if not isinstance(configured, dict):
        return {}
    out = {}
    for stage in stages:
        slug = SLUG_FOR_STAGE.get(stage)
        if slug and isinstance(configured.get(slug), str):
            out[stage] = configured[slug]
    return out


# --- report --------------------------------------------------------------------------


def _default_projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def _served_model(transcript: Path) -> str:
    """`message.model` of the first assistant row, else "unknown"."""
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "unknown"
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) and entry.get("type") == "assistant" else None
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            return message["model"]
    return "unknown"


def _join(row: dict, joins: dict[str, dict], projects: Path) -> tuple[str, str]:
    """(agent id, served model) for one row, from its PostToolUse join row.

    The served model is the subagent transcript's model, else the join row's resolvedModel.
    """
    join = joins.get(row.get("tool_use_id")) if isinstance(row.get("tool_use_id"), str) else None
    agent_id = join.get("agent_id") if join else None
    if not isinstance(agent_id, str) or not agent_id:
        return "unjoined", "unknown"
    transcript = next(projects.glob(f"*/*/subagents/agent-{agent_id}.jsonl"), None)
    served = _served_model(transcript) if transcript else "unknown"
    if served == "unknown" and isinstance(join.get("resolved_model"), str):
        served = join["resolved_model"]
    return agent_id, served


def _run_session(context_dir: Path, run_id: str | None) -> dict:
    from pipeline_run_binding import current_session

    session = current_session(context_dir)
    if run_id is None or session.get("pipeline_run_id") == run_id:
        return session
    import session_store

    return next((r for r in session_store.run_records(context_dir) if r.get("pipeline_run_id") == run_id), {})


def build_report(context_dir: Path, run_id: str | None, projects: Path) -> dict:
    session = _run_session(context_dir, run_id)
    run = run_id or str(session.get("pipeline_run_id") or "")
    configured = session.get("peer_models") if isinstance(session.get("peer_models"), dict) else {}
    rows = read_rows(context_dir, run)
    import peer_attest

    joins: dict[str, dict] = {}
    for join in sorted(peer_attest.every_run_rows(context_dir, "joins"), key=lambda r: float(r.get("at") or 0)):
        joins.setdefault(join.get("tool_use_id"), join)
    stages = []
    for slug in PEER_MODEL_SLUGS:
        stage_calls = []
        for row in rows:
            if row["stage"] != slug or row["decision"] != "allow":
                continue
            agent_id, served = _join(row, joins, projects)
            stage_calls.append({"model": row.get("model"), "subagent_type": row.get("subagent_type"),
                                "tool_use_id": row.get("tool_use_id"), "agent_id": agent_id, "served_model": served})
        stages.append({"slug": slug, "configured": configured.get(slug, "-"), "calls": stage_calls})
    return {"run_id": run, "has_rows": rows_path(context_dir, run).is_file(), "stages": stages}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Peer models of a Cadence run")
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="each recorded peer call, joined to its subagent")
    rep.add_argument("--run", help="pipeline_run_id (default: this worktree's run)")
    rep.add_argument("--json", action="store_true")
    rep.add_argument("--projects-dir", type=Path, default=None, help="default: $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects")
    args = parser.parse_args(argv)
    from context_root import context_path

    data = build_report(context_path(Path.cwd()), args.run, args.projects_dir or _default_projects_dir())
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if not data["has_rows"]:
        print(f"no peer model rows for run {data['run_id']}")
        return 0
    print(f"run {data['run_id']}")
    for stage in data["stages"]:
        print(f"{stage['slug']}: configured {stage['configured']}")
        for c in stage["calls"]:
            print(f"  requested {c['model']}  served {c['served_model']}  subagent_type {c['subagent_type']}  "
                  f"tool_use_id {c['tool_use_id']}  agent {c['agent_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
