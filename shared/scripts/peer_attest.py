"""Host evidence that a delegated peer wrote a verdict file.

Claude Code sets `agent_id` on a PreToolUse payload only inside a subagent
(verified on 2.1.286). For each subagent Write/Edit/MultiEdit of a peer verdict
file, `hooks/guard_skill_gate.py` computes the content the file will hold after
the tool runs and records its sha256 here. Only that hook writes `.context/attest/`
(skill_gate protects it). A gate accepts a verdict file only when its current
content hash is one a peer wrote: `touch`, `cp` and later edits do not match.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from pathlib import Path
from typing import Any

# Peer verdict files, relative to .context. A plan is not a verdict: the
# orchestrator may still edit it during /plan-update cycles.
PEER_ARTIFACT_GLOBS = (
    "vibetest/*.md",
    "reviews/plan_*.md",
    "adversarial/*.md",
    "benchmarks/*.json",
    "prs/reviews/*.md",
    "reviews/pr_*.md",
    "summaries/*.md",
)


# Other logs in attest/ (gh-66 PD12: call and join rows also carry agent_id). Only <run>.jsonl attests.
NOT_ATTEST_LOGS = (".agents.jsonl", ".models.jsonl", ".returns.jsonl", ".context.jsonl", ".calls.jsonl", ".joins.jsonl")


def is_peer_artifact(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in PEER_ARTIFACT_GLOBS)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_content(tool: str, tool_input: dict[str, Any], path: Path) -> bytes | None:
    """What `path` holds after this Write/Edit/MultiEdit, or None when it cannot be known."""
    if tool == "Write":
        content = tool_input.get("content")
        return content.encode("utf-8") if isinstance(content, str) else None
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input] if tool == "Edit" else None
    if not isinstance(edits, list):
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for edit in edits:
        old, new = edit.get("old_string"), edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or old not in text:
            return None
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text.encode("utf-8")


def _log(context_dir: Path, run_id: str) -> Path:
    return Path(context_dir) / "attest" / f"{run_id}.jsonl"


def record(context_dir: Path, run_id: str, rel: str, agent_id: str, digest: str) -> None:
    path = _log(context_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"path": rel, "agent_id": agent_id, "sha256": digest, "at": time.time()}) + "\n")


def _entries(context_dir: Path, run_id: str) -> list[dict]:
    try:
        lines = _log(context_dir, run_id).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("agent_id") and entry.get("sha256"):
            out.append(entry)
    return out


def orchestrator_ids(context_dir: Path) -> frozenset[str]:
    """Agent ids that ran activation in any run of this .context (cd-47 D2, G-D5).

    Their rows never count as a peer's: an orchestrator does not review its own work.
    A Claude agent_id belongs to one subagent, so the union excludes no real peer.
    """
    import session_store  # shared/scripts, next to this file

    ids: set[str] = set()
    for record in session_store.run_records(Path(context_dir)):
        listed = record.get("orchestrator_agent_ids")
        if isinstance(listed, list):
            ids.update(a for a in listed if isinstance(a, str) and a)
    return frozenset(ids)


def attested(context_dir: Path, run_id: str, artifact: Path, exclude_agents: frozenset[str] = frozenset()) -> bool:
    """The file's current content is the latest content a delegated peer wrote to it.

    Only the latest counts: restoring a version the peer later replaced is rejected.
    Rows from ``exclude_agents`` (orchestrators) never count.
    """
    return _is_current(artifact, newest_attest(context_dir, artifact, exclude_agents, run_id))


def attested_any(context_dir: Path, artifact: Path, exclude_agents: frozenset[str] = frozenset()) -> bool:
    """Like ``attested``, over every run's log: the file's newest attested write is this content.

    For a gate in a worktree with no session of its own (cd-47 D1). Rows from
    ``exclude_agents`` (orchestrators) never count.
    """
    return _is_current(artifact, newest_attest(context_dir, artifact, exclude_agents))


def _is_current(artifact: Path, found) -> bool:
    try:
        return found is not None and sha256(Path(artifact).read_bytes()) == found[1]["sha256"]
    except OSError:
        return False


def _of_stages(context_dir: Path, agent_id: str, stages: frozenset[str] | None) -> bool:
    return stages is None or writer_stage(context_dir, agent_id)[0] in stages


def writers(
    context_dir: Path, run_id: str, pattern: str, exclude_agents: frozenset[str] = frozenset(),
    stages: frozenset[str] | None = None,
) -> set[str]:
    """Distinct peers that wrote a file matching `pattern` in this run (gh-66: of `stages` only, when given)."""
    found = {
        e["agent_id"] for e in _entries(context_dir, run_id)
        if fnmatch.fnmatch(e["path"], pattern) and e["agent_id"] not in exclude_agents
    }
    return {a for a in found if _of_stages(context_dir, a, stages)}


def writer_paths(
    context_dir: Path, run_id: str, pattern: str, exclude_agents: frozenset[str] = frozenset(),
    stages: frozenset[str] | None = None,
) -> set[tuple[str, str]]:
    """Distinct (agent_id, path) pairs of peer writes to files matching `pattern` in this run (cd-63)."""
    found = {
        (e["agent_id"], e["path"]) for e in _entries(context_dir, run_id)
        if fnmatch.fnmatch(e["path"], pattern) and e["agent_id"] not in exclude_agents
    }
    return {pair for pair in found if _of_stages(context_dir, pair[0], stages)}


# --- peer calls (gh-66): which stage each orchestrator Agent call was for -------------


def _json_rows(path: Path) -> list[dict]:
    """The JSON objects of a log, one per line. A line that is not a JSON object is skipped
    (gh-66 PD23: a hook stopped during an append). An unreadable file is no rows."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def calls_log(context_dir: Path, run_id: str) -> Path:
    return Path(context_dir) / "attest" / f"{run_id}.calls.jsonl"


def record_call_row(context_dir: Path, run_id: str, row: dict) -> None:
    """Append one call row in one write of one full line. Only the handoff hook calls this."""
    path = calls_log(context_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(row) + "\n")


def call_rows(context_dir: Path, run_id: str) -> list[dict]:
    return _json_rows(calls_log(context_dir, run_id))


def joins_log(context_dir: Path, run_id: str) -> Path:
    return Path(context_dir) / "attest" / f"{run_id}.joins.jsonl"


def record_join(context_dir: Path, run_id: str, row: dict) -> None:
    """Append one join row (PostToolUse: tool_use_id -> agent_id). Only the handoff hook calls this."""
    path = joins_log(context_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(row) + "\n")


def join_rows(context_dir: Path, run_id: str) -> list[dict]:
    return _json_rows(joins_log(context_dir, run_id))


# --- the writer's stage (gh-66 PD1, PD21) ----------------------------------------------

NO_JOIN = "no join row: call the peer again from the orchestrator"
# A foreground peer gets its join row from PostToolUse, after it returns (PR #145 R1 MF-2).
NO_JOIN_YET = "no join row yet: the peer is still running; the orchestrator checks the join after the peer returns"
DENIED = "the peer call was denied"


def every_run_rows(context_dir: Path, suffix: str) -> list[dict]:
    """Rows of every `<run>.<suffix>` log of this .context. Hooks pick a run per event, and the
    host ids (tool_use_id, agentId) join across runs (PD21)."""
    rows: list[dict] = []
    for log in sorted((Path(context_dir) / "attest").glob(f"*.{suffix}.jsonl")):
        rows += _json_rows(log)
    return rows


def writer_stage(context_dir: Path, agent_id: str) -> tuple[str | None, dict | None, str]:
    """(stage, call row, "") for an agent that an allowed peer call started, else (None, call row or None, cause).

    The first join row of the agent (by `at`) wins: a peer continued with SendMessage keeps
    the stage of its first call.
    """
    joins = sorted((r for r in every_run_rows(context_dir, "joins")
                    if r.get("agent_id") == agent_id and isinstance(r.get("tool_use_id"), str) and r["tool_use_id"]),
                   key=lambda r: float(r.get("at") or 0))
    if not joins:
        return None, None, NO_JOIN_YET if _still_running(context_dir, agent_id) else NO_JOIN
    tool_use_id = joins[0].get("tool_use_id")
    calls = [r for r in every_run_rows(context_dir, "calls") if r.get("tool_use_id") == tool_use_id]
    if not calls:
        return None, None, f"no call row for the join (tool_use_id {tool_use_id})"
    call = calls[-1]
    if call.get("decision") != "allow" or not call.get("stage"):
        return None, call, DENIED
    return str(call["stage"]), call, ""


def _still_running(context_dir: Path, agent_id: str) -> bool:
    """The agent's newest SubagentStart/SubagentStop event, over every run's agents log, is a start."""
    events = [r for r in every_run_rows(context_dir, "agents") if r.get("agent_id") == agent_id]
    if not events:
        return False
    return max(events, key=lambda r: float(r.get("at") or 0)).get("event") == "start"


def _newest(context_dir: Path, artifact: Path, logs: list[str], exclude_agents: frozenset[str]):
    """(rel, newest attest row, its run) for the file over `logs`, else None."""
    try:
        rel = Path(artifact).resolve().relative_to(Path(context_dir).resolve()).as_posix()
    except (OSError, ValueError):
        return None
    rows = [(e, run) for run in logs for e in _entries(context_dir, run)
            if e["path"] == rel and e["agent_id"] not in exclude_agents]
    if not rows:
        return None
    # On a timestamp tie the later row in the log wins, as the old attested() did (PR #145 R1 F1).
    _, (entry, run) = max(enumerate(rows), key=lambda item: (float(item[1][0].get("at") or 0), item[0]))
    return rel, entry, run


def attest_runs(context_dir: Path) -> list[str]:
    """The run ids that have an attest log in this .context."""
    return [log.stem for log in sorted((Path(context_dir) / "attest").glob("*.jsonl"))
            if not log.name.endswith(NOT_ATTEST_LOGS)]


def newest_attest(context_dir: Path, artifact: Path, exclude_agents: frozenset[str] = frozenset(),
                  run_id: str | None = None):
    """(rel, newest attest row, run) of the file in one run's log (or in every run log), else None."""
    return _newest(context_dir, artifact, [run_id] if run_id is not None else attest_runs(context_dir),
                   exclude_agents)


def _stage_reason(context_dir: Path, artifact: Path, found, stages: frozenset[str],
                  models: dict[str, str] | None = None) -> tuple[bool, str]:
    """(True, "") when the newest attest row is this content from a peer of `stages`.

    (False, "") for no attest row or other content: the gate keeps its attest text.
    (False, reason) for a wrong stage or a writer that does not join to an allowed call.
    """
    if not _is_current(artifact, found):
        return False, ""
    rel, entry, _run = found
    stage, call, cause = writer_stage(context_dir, entry["agent_id"])
    if stage is None:
        return False, f"{rel}: {cause}"
    if stage not in stages:
        return False, f"{rel} written by a {stage} peer; this gate accepts {', '.join(sorted(stages))}"
    wanted = (models or {}).get(stage)
    if wanted is not None and call.get("model") != wanted:
        return False, f"{rel}: the {stage} peer call used model {call.get('model')}; peer_models sets {wanted}"
    return True, ""


def stage_attested(context_dir: Path, run_id: str, artifact: Path, stages: frozenset[str],
                   exclude_agents: frozenset[str] = frozenset(), models: dict[str, str] | None = None) -> tuple[bool, str]:
    """``attested`` plus the writer's stage (and, with ``models``, the model of its call), over this run's log."""
    found = newest_attest(context_dir, artifact, exclude_agents, run_id)
    return _stage_reason(context_dir, artifact, found, stages, models)


def stage_attested_any(context_dir: Path, artifact: Path, stages: frozenset[str],
                       exclude_agents: frozenset[str] = frozenset(), models: dict[str, str] | None = None) -> tuple[bool, str]:
    """``attested_any`` plus the writer's stage (and model), over every run's attest log."""
    return _stage_reason(context_dir, artifact, newest_attest(context_dir, artifact, exclude_agents), stages, models)


# --- subagent lifetimes (Claude Code SubagentStart / SubagentStop) ----------------

AGENT_WINDOW_SECONDS = 3600


def _agents_log(context_dir: Path, run_id: str) -> Path:
    return Path(context_dir) / "attest" / f"{run_id}.agents.jsonl"


def agent_event(context_dir: Path, run_id: str, agent_id: str, event: str) -> None:
    """Record a subagent start or stop. Only the hooks call this (attest/ is protected)."""
    path = _agents_log(context_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"agent_id": agent_id, "event": event, "at": time.time()}) + "\n")


def running_agents(context_dir: Path, run_id: str) -> list[str]:
    """Subagents of this run that started within the window and have not stopped."""
    try:
        lines = _agents_log(context_dir, run_id).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    started: dict[str, float] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        agent = str(entry.get("agent_id") or "")
        if entry.get("event") == "start":
            started[agent] = float(entry.get("at") or 0)
        elif entry.get("event") == "stop":
            started.pop(agent, None)
    now = time.time()
    return [a for a, at in started.items() if now - at <= AGENT_WINDOW_SECONDS]
