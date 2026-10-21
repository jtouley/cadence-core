#!/usr/bin/env python3
"""preToolUse: deny poisoned Task prompts and record lean peer prompts.

Origin: cadence hooks/guard_lean_task_prompt.py

While a cadence session is active, a Task prompt must be one line, at most
400 characters, and free of parent-briefing phrases. Bundled peer slashes
must also match the lean allowlist. Allowed peer prompts are written to the
delegation sidecar that peer gates re-check. No session: allow.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_HOOKS = Path(__file__).resolve().parent
_ROOT = _HOOKS.parent
_SCRIPTS = _ROOT / "scripts"
_LOCAL_SHARED = _ROOT / "shared" / "scripts"
for _path in (_SCRIPTS, _LOCAL_SHARED):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

os.environ.setdefault("CADENCE_HOME", str(_ROOT))
os.environ.setdefault("CADENCE_SKILL_ROOT", str(_ROOT))

from hook_protocol import candidate_contexts, has_session, write_context  # noqa: E402
from pipeline_run_binding import load_session  # noqa: E402
from prompt_shape import (  # noqa: E402
    delegation_stage_for_prompt,
    stage_for_prompt,
    task_prompt_block_reason,
    write_delegation_record,
)


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")


def allow() -> dict[str, Any]:
    return {"permission": "allow"}


def deny(message: str) -> dict[str, Any]:
    return {
        "permission": "deny",
        "user_message": message,
        "agent_message": message,
    }


def load_payload() -> tuple[dict[str, Any] | None, str | None]:
    """Return the Task object, or a deny reason. Empty and non-objects deny."""
    raw = sys.stdin.read().strip()
    if not raw:
        return None, "Task stdin is empty"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, "Task stdin is not JSON"
    if not isinstance(parsed, dict):
        return None, "Task stdin is not a JSON object"
    return parsed, None


def extract_tool_name(payload: dict[str, Any]) -> str:
    for key in ("tool_name", "tool", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    candidate = payload.get("tool_input") or payload.get("input") or payload.get("arguments") or {}
    if isinstance(candidate, dict):
        return candidate
    return {}


def extract_prompt(tool_input: dict[str, Any]) -> str:
    value = tool_input.get("prompt")
    if isinstance(value, str):
        return value
    return ""


def evaluate(payload: dict[str, Any], context_dir: Path, record: bool = True) -> dict[str, Any]:
    """Allow, or deny with a reason. Peer allows also write the delegation record,
    unless `record` is False (a check of one more candidate)."""
    tool = extract_tool_name(payload)
    if tool.lower() != "task":
        return allow()

    session = load_session(Path(context_dir))
    if not session or session.get("active_skill") != "cadence":
        return allow()

    prompt = extract_prompt(extract_tool_input(payload))
    reason = task_prompt_block_reason(prompt)
    if reason:
        return deny(f"lean Task prompt rejected: {reason}")

    stage = stage_for_prompt(prompt)
    if stage is None:
        return allow()

    run_id = session.get("pipeline_run_id")
    if not run_id:
        return deny("lean Task prompt rejected: active session has no pipeline_run_id")

    record_stage = delegation_stage_for_prompt(prompt)
    if record_stage is None:
        return deny("lean Task prompt rejected: no delegation stage")

    if not record:
        return allow()
    write_delegation_record(
        Path(context_dir),
        run_id=str(run_id),
        stage=record_stage,
        command=prompt.strip(),
        source="preToolUse",
    )
    return allow()


def main() -> int:
    payload, error = load_payload()
    if error or payload is None:
        emit(deny(f"lean Task prompt rejected: {error or 'Task stdin is empty'}"))
        return 0
    tool = extract_tool_name(payload)
    if tool.lower() != "task":
        emit(deny("lean Task prompt rejected: stdin is not a Task payload"))
        return 0

    try:
        from context_root import context_path  # type: ignore
    except ImportError:
        emit(deny("lean Task prompt hook could not import context_root"))
        return 0

    # Each host candidate with a session is checked with no write; one deny denies. The
    # record goes into one context only. context_path creates no .context.
    try:
        pairs = candidate_contexts(payload, context_path)
        for _workspace, ctx in pairs:
            if has_session(ctx):
                verdict = evaluate(payload, ctx, record=False)
                if verdict.get("permission") == "deny":
                    emit(verdict)
                    return 0
        chosen = write_context(pairs, context_path(Path.cwd()))
    except OSError as exc:
        emit(deny(f"lean Task prompt hook could not resolve .context: {exc}"))
        return 0

    if chosen is None:
        emit(allow())
        return 0
    emit(evaluate(payload, chosen[1], record=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
