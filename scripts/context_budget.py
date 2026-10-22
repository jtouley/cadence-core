"""Orchestrator context budget.

The orchestrator sends its whole context again on each turn. Peer returns, hook
messages and file reads stay in it, so a long run grows it without a limit. Haiku 5.5
costs five times more per token above 100K prompt tokens, and a large context
dilutes the agent's attention.

Before each orchestrator Agent or Task call during an active run, the hook reads the
size of the orchestrator's last request from the host transcript and writes one row
to .context/attest/<run>.context.jsonl. Config: `context_budget` (scripts/cadence_config.py).

  mode         off | shadow | enforce (default shadow)
  warn_tokens  shadow and enforce write a notice above this size (default 60000)
  max_tokens   enforce denies the call above this size (default 90000)

Only the hooks call this module. It uses the standard library only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cadence_config
import host_transcript

USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def load_config(root: Path, workspace: Path | None = None) -> dict[str, Any]:
    """The context_budget settings for `workspace`. A bad value raises (fail closed)."""
    cfg = cadence_config.load(root, workspace).section("context_budget")
    if cfg["warn_tokens"] > cfg["max_tokens"]:
        raise ValueError("context_budget.warn_tokens must not be more than max_tokens")
    return cfg


def _usage_tokens(row: dict[str, Any], *, own: bool) -> int | None:
    """Prompt tokens of one assistant row, else None.

    In the main transcript a sidechain row is another agent's request and is skipped.
    In a subagent's own transcript the host marks every row as a sidechain.
    """
    message = host_transcript.message(row, "assistant")
    usage = message.get("usage") if message is not None and (own or not row.get("isSidechain")) else None
    if not isinstance(usage, dict):
        return None
    counts = [usage.get(k) for k in USAGE_KEYS]
    valid = [c for c in counts if isinstance(c, int) and not isinstance(c, bool) and c >= 0]
    return sum(valid) if valid else None


def prompt_tokens(transcript: object, *, own: bool = False) -> int | None:
    """Prompt tokens of the last request in a host transcript, else None.

    The size is input + cache read + cache write of the newest assistant row with usage.
    The host can write the transcript after the request, so the size can be one request
    late. The next request is at least this large, because the context only grows.
    """
    for row in reversed(host_transcript.rows(transcript, tail=True)):
        tokens = _usage_tokens(row, own=own)
        if tokens is not None:
            return tokens
    return None


def orchestrator_transcript(payload: dict[str, Any]) -> tuple[str | None, str]:
    """(transcript, source) of the agent that makes this call.

    The main agent reads the session transcript. A subagent orchestrator reads only its
    own transcript: never the main one, which is another context.
    """
    agent = payload.get("agent_id")
    if not agent:
        return payload.get("transcript_path"), "session"
    own = payload.get("agent_transcript_path") or host_transcript.subagent_path(payload.get("transcript_path"), agent)
    return own, "subagent"


def judge(cfg: dict[str, Any], tokens: int | None) -> str:
    """unknown | ok | warn | deny for one call."""
    if tokens is None:
        return "unknown"
    if cfg["mode"] == "enforce" and tokens > cfg["max_tokens"]:
        return "deny"
    if tokens > cfg["warn_tokens"]:
        return "warn"
    return "ok"


def log_path(context_dir: Path, run_id: str) -> Path:
    return Path(context_dir) / "attest" / f"{run_id}.context.jsonl"


def record(context_dir: Path, run_id: str, row: dict[str, Any]) -> None:
    """Append one budget row. Only the hooks call this (attest/ is protected)."""
    path = log_path(context_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({**row, "at": time.time()}) + "\n")
