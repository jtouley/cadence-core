"""Lean Task prompt shape validation for cadence delegated blocks.

Planning/review delegated Task prompts must be slash + args only.
Fat megaprompts poison subagent judgment — fail-closed at the Task hook.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

MAX_LEAN_PROMPT_CHARS = 400

POISON_SUBSTRINGS = (
    "override general heuristics",
    "Return: (1)",
    "Return exactly",
    "Acceptance themes",
    "Blocking items to fix",
    "CONTEXT PACK:",
    "Artifact to review",
    "Hard rules of the proposal",
    "Hard constraints of the proposal",
    "Return to parent",
    "Return to the main agent",
    "Keep your response under",
    "Keep response under",
    "Must include near top",
    "You are the DE Adversarial",
    "You are running /vibe-test",
    "Fill and COMPLETE",
    "Cross-check claims",
    "Required work",
    "Prior art to SWEEP",
)

LEAN_STAGES = frozenset(
    {
        "plan-create",
        "plan-review",
        "plan-update",
        "de-adversarial-reviewer",
        "test-benchmark",
        "pr-review-local",
        "pr-review-update",
        "vibe-test",
        "spec-driven",
        "commit-push-pr",
        "session-summary",
    }
)

SLASH_TO_STAGE = {
    "/plan-create": "plan-create",
    "/plan-review": "plan-review",
    "/plan-update": "plan-update",
    "/de-adversarial-reviewer": "de-adversarial-reviewer",
    "/test-benchmark": "test-benchmark",
    "/pr-review-local": "pr-review-local",
    "/pr-review-update": "pr-review-update",
    "/vibe-test": "vibe-test",
    "/spec-driven": "spec-driven",
    "/commit-push-pr": "commit-push-pr",
    "/session-summary": "session-summary",
}

# Peer commands with no stages.json row (gh-66 PD4). R4 runs /pr-review-update as a
# delegated peer, but its stages.json row is the final_consolidate.py gate.
# handoff._peer_stages appends these rows to the stages.json peer rows, so the lean check,
# the call row and the handoff packet (ADR-0010) cover the R4 peer call.
# A review fix round (ADR-0017) runs /spec-driven --fix on the PR review between R1 and R4.
# Its longer command wins over the E1 row, so the hook does not render it as E1.
# At R7, /session-summary reads the run's manifest and writes the summary (ADR-0018).
EXTRA_PEER_ROWS = ({"slug": "pr-review-update", "block": "R4", "command": "/pr-review-update",
                    "inputs": ["pr-number"]},
                   {"slug": "review-fix", "block": "RF", "command": "/spec-driven --fix",
                    "inputs": ["pr-review"]},
                   {"slug": "session-summary", "block": "R7", "command": "/session-summary",
                    "inputs": ["manifest"]})

# A Claude Code plugin install lists the peers as /cadence:<name>. Only Cadence's
# own prefix is stripped: /other:plan-review is not a Cadence peer call.
PLUGIN_PREFIX = "/cadence:"


def bare_slash(text: str) -> str:
    """`/cadence:plan-review x` -> `/plan-review x`. Other text is unchanged."""
    text = (text or "").strip()
    return "/" + text[len(PLUGIN_PREFIX):] if text.startswith(PLUGIN_PREFIX) else text


_PATTERNS: dict[str, re.Pattern[str]] = {
    "plan-create": re.compile(
        r"^/plan-create(\s+--from-conversation\s+\S+|\s+\S+)(\s+--pack\s+\S+)?\s*$"
    ),
    "plan-review": re.compile(r"^/plan-review\s+\S+\s*$"),
    "plan-update": re.compile(
        r"^/plan-update\s+\S+(\s+--from-review\s+\S+)?\s*$"
    ),
    "de-adversarial-reviewer": re.compile(
        r"^/de-adversarial-reviewer\s+(plan-gate|spec-pressure)\s+\S+\s*$"
    ),
    "test-benchmark": re.compile(r"^/test-benchmark(\s+plan|\s+--code)?(\s+\S+)?\s*$"),
    "pr-review-local": re.compile(r"^/pr-review-local(\s+\S+)?\s*$"),
    "pr-review-update": re.compile(r"^/pr-review-update(\s+\S+)?\s*$"),
    "vibe-test": re.compile(r"^/vibe-test(\s+\S+)?\s*$"),
    "spec-driven": re.compile(r"^/spec-driven(\s+--fix)?(\s+\S+)?\s*$"),
    "commit-push-pr": re.compile(r"^/commit-push-pr(\s+--draft)?\s*$"),
    "session-summary": re.compile(r"^/session-summary\s+\S+\s*$"),
}


class PromptShapeError(ValueError):
    """Raised when a delegated Task prompt is not lean slash form."""


def validate_lean_prompt(stage: str, prompt: str) -> bool:
    """Return True if prompt is lean for stage; raise PromptShapeError otherwise."""
    if stage not in LEAN_STAGES:
        return True

    text = bare_slash(prompt)
    if "\n" in text or "\r" in text:
        raise PromptShapeError(
            f"{stage} prompt must be a single-line lean slash command; found newline"
        )
    if len(text) > MAX_LEAN_PROMPT_CHARS:
        raise PromptShapeError(
            f"{stage} prompt exceeds {MAX_LEAN_PROMPT_CHARS} chars "
            f"({len(text)}); use slash + path pointers only"
        )

    lower = text.lower()
    for needle in POISON_SUBSTRINGS:
        if needle.lower() in lower:
            raise PromptShapeError(
                f"{stage} prompt must be lean slash-only; found poison: {needle!r}"
            )

    pattern = _PATTERNS.get(stage)
    if pattern is None:
        return True
    if not pattern.match(text):
        raise PromptShapeError(
            f"{stage} prompt must be lean slash + args only; got {text!r}"
        )
    return True


def stage_for_prompt(prompt: str) -> str | None:
    """Return the lean stage when the prompt starts with a bundled peer slash."""
    text = bare_slash(prompt)
    if not text or any(ch in text for ch in "\n\r"):
        return None
    token = text.split(None, 1)[0]
    return SLASH_TO_STAGE.get(token)


def delegation_stage_for_prompt(prompt: str) -> str | None:
    """Sidecar key. `/test-benchmark plan` and `/test-benchmark` are different stages."""
    stage = stage_for_prompt(prompt)
    if stage != "test-benchmark":
        return stage
    text = bare_slash(prompt)
    if re.match(r"^/test-benchmark\s+plan\b", text):
        return "test-benchmark-plan"
    return "test-benchmark-pr"


def task_prompt_block_reason(prompt: str) -> str | None:
    """Return a deny reason for any Task prompt during an active cadence session.

    Judgment peers must match the slash allowlist. Every other Task prompt is
    still one line, size-capped, and free of parent-briefing phrases so the
    child is not handed the parent's conclusions.
    """
    text = bare_slash(prompt)
    if not text:
        return "Task prompt is empty"
    if "\n" in text or "\r" in text:
        return "Task prompt must be a single line; found newline"
    if len(text) > MAX_LEAN_PROMPT_CHARS:
        return (
            f"Task prompt exceeds {MAX_LEAN_PROMPT_CHARS} chars "
            f"({len(text)}); use slash + path pointers only"
        )
    lower = text.lower()
    for needle in POISON_SUBSTRINGS:
        if needle.lower() in lower:
            return f"Task prompt contains poison: {needle!r}"
    stage = stage_for_prompt(text)
    if stage is None:
        return None
    try:
        validate_lean_prompt(stage, text)
    except PromptShapeError as exc:
        return str(exc)
    return None


# The same set as handoff._DROP_CATEGORIES. handoff imports this module, so the
# set is copied here; a contract test keeps the two equal.
_DROP_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})

# A peer command token: /<command> or /cadence:<command>, at the start of the
# text or after whitespace, a quote, an opening bracket or a Markdown wrapper.
# A slash after a word character, "/", "." or "-" is a path, not a token. The
# token ends at whitespace, the end, closing punctuation, or "." with no word
# character after it (so "/plan-review.md" is not a token).
_PEER_TOKEN = re.compile(
    r"(?:^|(?<=[\s\"'(\[{<`*_]))/(?:cadence:)?(?P<head>[A-Za-z0-9-]+)"
    r"(?=$|\s|[)\]}>,;:!?\"'`*_]|\.(?!\w))"
)
_WORD_END = ")]}>,;:!?\"'`*_."


def _normalized(prompt: str) -> str:
    """NFKC and drop Cc/Cf/Zl/Zp on each line, as handoff._match_lines does."""
    lines = []
    for line in (prompt or "").splitlines():
        text = unicodedata.normalize("NFKC", line)
        lines.append("".join(ch for ch in text if unicodedata.category(ch) not in _DROP_CATEGORIES))
    return "\n".join(lines)


def peer_command_row(prompt: str, rows: list[dict], *, normalize: bool = True) -> dict | None:
    """The stages.json peer row of the first peer command token in the text, else None.

    `rows` are the stages.json rows whose command starts with "/". At one token,
    the longest command whose words all match wins (`/test-benchmark plan` over
    `/test-benchmark`). With no full match, the command with the fewest words wins.
    """
    text = _normalized(prompt) if normalize else (prompt or "")
    for match in _PEER_TOKEN.finditer(text):
        head = "/" + match.group("head")
        candidates = [r for r in rows if str(r.get("command", "")).split()[:1] == [head]]
        if not candidates:
            continue
        words = [head] + [w.rstrip(_WORD_END) for w in text[match.end():].split()]
        full = [r for r in candidates if words[: len(r["command"].split())] == r["command"].split()]
        if full:
            return max(full, key=lambda r: len(r["command"].split()))
        return min(candidates, key=lambda r: len(r["command"].split()))
    return None


def lean_peer_reason(prompt: str, row: dict, rows: list[dict]) -> str | None:
    """Deny reason for an orchestrator call that names the peer `row`, else None."""
    if peer_command_row(prompt, rows, normalize=False) is None:
        return "peer command is not in plain ASCII slash form"
    reason = task_prompt_block_reason(prompt)
    if reason is not None:
        return reason
    if delegation_stage_for_prompt(prompt) != delegation_stage_for_prompt(row["command"]):
        return "a peer prompt is the slash command and its arguments only"
    return None


def hook_records_only(session: object) -> bool:
    """True on a run marked at activation on Claude Code: only the hook writes the record."""
    return (
        isinstance(session, dict)
        and session.get("lean_record_by_hook") is True
        and session.get("host") == "claude"
    )


def delegation_record_path(context_dir: Path, run_id: str, stage: str) -> Path:
    """Sidecar that stores the Task prompt the hook actually allowed."""
    safe_stage = re.sub(r"[^a-z0-9._-]+", "-", stage.lower()).strip("-")
    return Path(context_dir) / "subagent-runs" / run_id / f"{safe_stage}.json"


def write_delegation_record(
    context_dir: Path,
    *,
    run_id: str,
    stage: str,
    command: str,
    source: str,
    tool_use_id: str | None = None,
) -> Path:
    """Persist the allowed prompt. Later gates re-validate this string."""
    path = delegation_record_path(context_dir, run_id, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "stage": stage,
        "command": command.strip(),
        "pipeline_run_id": run_id,
        "status": "running",
        "source": source,
        "started_at": now,
        "completed_at": "",
        "artifact_path": "",
        "manifest_entry_id": "",
    }
    if tool_use_id:
        record["tool_use_id"] = tool_use_id  # gh-66: for the report; gates join on attest/<run>.calls.jsonl
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path
