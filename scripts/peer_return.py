"""Lean peer returns: what a delegated peer may hand back to the orchestrator.

A peer's final message becomes part of the orchestrator's context for the rest of
the run. The gates judge from the artifact on disk, so the orchestrator needs only
a pointer. A report (C.O.R.E., decisions, evidence) grows the context and hands the
orchestrator a peer's conclusions: the same poison that prompt_shape blocks on the
way out.

The return is one line:

    ARTIFACT: <path>
    ARTIFACT: <path> VERDICT: <words>
    STOPPED: <reason>

Only the hooks call this module. It uses the standard library only.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import host_transcript
from prompt_shape import POISON_SUBSTRINGS, bare_slash

MAX_RETURN_CHARS = 300

RETURN_SHAPE = re.compile(
    r"^(?:ARTIFACT: \S+(?: VERDICT: [^\r\n]{1,80})?|STOPPED: [^\r\n]{1,200})$"
)

# Phrases that hand a decision or an instruction back to the orchestrator.
RETURN_POISON = (
    "decisions needed",
    "decision needed",
    "actions required",
    "action required",
    "run it yourself",
    "if you read the rule",
    "confirm the",
    "do you want",
    "should i ",
    "your call",
)

RETURN_RULE = (
    "If you run a Cadence peer command, replace your closing report with one line only. "
    "Write `ARTIFACT: <path>`, or `ARTIFACT: <path> VERDICT: <words>`, or `STOPPED: <reason>`. "
    "Put the details in the artifact. Do not ask the orchestrator for a decision."
)

LEAN_SHAPES = "ARTIFACT: <path>, or ARTIFACT: <path> VERDICT: <words>, or STOPPED: <reason>"

_RENDERED_PEER = re.compile(r"^Cadence peer \S+ for ticket ")


_FENCE = re.compile(r"^```[^\s`]*\n([^\n]*)\n```$")  # a tag is one token: no text hides on the fence line


def _unwrap(text: str) -> str:
    """The message without outer whitespace, one code fence around one line, or one pair of backticks."""
    text = text.replace("\r\n", "\n").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if len(text) > 1 and text.startswith("`") and text.endswith("`") and "\n" not in text:
        text = text[1:-1].strip()
    return text


def return_reason(message: str) -> str | None:
    """Why `message` is not a lean return, else None."""
    text = _unwrap(message or "")
    if not text:
        return "the final message is empty"
    if "\n" in text or "\r" in text:
        lines = len(text.splitlines())
        return f"the final message has {lines} lines; a peer returns one line"
    if len(text) > MAX_RETURN_CHARS:
        return f"the final message has {len(text)} characters; the limit is {MAX_RETURN_CHARS}"
    if not RETURN_SHAPE.match(text):
        return f"the final message is not a lean return ({LEAN_SHAPES})"
    # The artifact path is data. Check the words around it only.
    words = re.sub(r"^ARTIFACT: \S+", "ARTIFACT:", text).lower()
    for needle in RETURN_POISON + tuple(p.lower() for p in POISON_SUBSTRINGS):
        if needle in words:
            return f"the final message hands work back to the orchestrator: {needle.strip()!r}"
    return None


def is_peer_prompt(prompt: str, block_for_prompt) -> bool:
    """True when a subagent's first prompt is a Cadence peer call.

    A peer starts from the lean slash command, or from the packet rendering that
    the handoff hook puts in its place. `block_for_prompt` is handoff.block_for_prompt
    bound to the skill root.
    """
    text = (prompt or "").strip()
    if not text:
        return False
    if _RENDERED_PEER.match(text):
        return True
    return block_for_prompt(bare_slash(text)) is not None


def first_prompt(transcript: object) -> str | None:
    """The first user text in a subagent transcript: the prompt that started it."""
    for row in host_transcript.rows(transcript, tail=False):
        message = host_transcript.message(row, "user")
        if message is not None and host_transcript.text(message.get("content")).strip():
            return host_transcript.text(message.get("content"))
    return None


def final_message(payload: dict[str, Any]) -> str | None:
    """The subagent's final message: the payload field, else the last assistant text."""
    value = payload.get("last_assistant_message")
    if isinstance(value, str):
        return value
    for row in reversed(host_transcript.rows(payload.get("agent_transcript_path"), tail=True)):
        message = host_transcript.message(row, "assistant")
        if message is not None:
            # The newest assistant row decides. When it is a tool call (a hand-back), there is no final
            # text, and an earlier narration line is not the reply: the result is unknown.
            return host_transcript.text(message.get("content")).strip() or None
    return None


def log_path(context_dir: Path, run_id: str) -> Path:
    return Path(context_dir) / "attest" / f"{run_id}.returns.jsonl"


def record(context_dir: Path, run_id: str, row: dict[str, Any]) -> None:
    """Append one return check. Only the hooks call this (attest/ is protected)."""
    path = log_path(context_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({**row, "at": time.time()}) + "\n")
