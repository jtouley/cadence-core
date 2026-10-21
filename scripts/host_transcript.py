"""Read Claude Code transcripts (JSONL) for the hooks.

One reader for the peer-return check and the context budget. A transcript can be
large, so a reader reads only the head or the tail. A row that a cut splits does
not parse and is skipped. Standard library only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_AGENT_ID = re.compile(r"[A-Za-z0-9_-]+")

HEAD_BYTES = 256 * 1024
TAIL_BYTES = 512 * 1024


def rows(path: object, *, tail: bool) -> list[dict[str, Any]]:
    """JSON object rows from the head or the tail of a transcript; [] when it cannot be read."""
    if not isinstance(path, str) or not Path(path).is_file():
        return []
    try:
        with Path(path).open("rb") as handle:
            if tail:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - TAIL_BYTES))
                raw = handle.read()
            else:
                raw = handle.read(HEAD_BYTES)
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def message(row: dict[str, Any], role: str) -> dict[str, Any] | None:
    """The row's message when it has this role, else None."""
    value = row.get("message")
    return value if isinstance(value, dict) and value.get("role") == role else None


def text(content: Any) -> str:
    """The text of a message content (a string, or a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def subagent_path(transcript_path: object, agent_id: str) -> str | None:
    """A subagent's own transcript: `<session>/subagents/agent-<id>.jsonl` next to `<session>.jsonl`.

    Claude Code sends `agent_transcript_path` on SubagentStop only. A PreToolUse inside
    a subagent carries `agent_id` and the main `transcript_path`, so the path is derived.
    """
    if not isinstance(transcript_path, str) or not transcript_path.endswith(".jsonl"):
        return None
    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
        return None  # an id with a path character never names a file outside subagents/
    folder = Path(transcript_path[: -len(".jsonl")]) / "subagents"
    path = folder / f"agent-{agent_id}.jsonl"
    if not path.is_file() and agent_id.startswith("agent-"):
        bare = folder / f"{agent_id}.jsonl"  # the docs' example ids carry the prefix; real ids are bare hex
        return str(bare) if bare.is_file() else str(path)
    return str(path)
