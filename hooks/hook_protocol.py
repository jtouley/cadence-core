"""Host wire protocol for Cadence hooks (Cursor and Claude Code).

The same hook scripts serve both hosts; the host is chosen by `--host` on the
registered command (`cursor` when absent, for existing installs).

Cursor (hooks.json):
  preToolUse / beforeShellExecution -> {"permission": "allow"|"deny", ...}
  stop / subagentStop               -> {} to allow, {"followup_message": ...} to keep working
Claude Code (settings.json):
  PreToolUse  -> no output to allow, so the hook stays neutral and the user's permission
                 mode decides (Claude Code treats "allow" as advisory, but silence is simplest);
                 {"hookSpecificOutput": {"permissionDecision": "deny", ...}} to deny
  Stop        -> no output to allow; {"decision": "block", "reason": ...} to keep working;
                 {"systemMessage": ...} to allow while telling the user something

Every decision exits 0 with JSON. An unexpected error, including one at import
time or a bad --host, exits 2, which both hosts treat as a block, so hooks fail
closed. (Claude Code treats any other non-zero exit as non-blocking.)
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

HOSTS = ("cursor", "claude")


def parse_host(argv: list[str] | None) -> str:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--host" in args:
        i = args.index("--host")
        value = args[i + 1] if i + 1 < len(args) else ""
        if value not in HOSTS:
            sys.stderr.write(f"cadence hook failed closed: --host must be one of {HOSTS}, got {value!r}\n")
            raise SystemExit(2)
        return value
    return "cursor"


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_payload() -> tuple[dict[str, Any] | None, str | None]:
    """Return the tool payload, or a parse error.

    Empty stdin is not an error: there is no tool call. Invalid JSON is an
    error so an active session can fail closed instead of treating it as no tool.
    """
    raw = sys.stdin.read().strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"tool payload is not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "tool payload must be a JSON object"
    return parsed, None


def _dir_value(value: object) -> Path | None:
    if isinstance(value, str) and value and Path(value).is_dir():
        return Path(value)
    return None


def hook_workspace(payload: dict[str, Any] | None) -> Path:
    """The project of this hook call, from what the host tells the hook (cd-82).

    A user-level Cursor hook starts outside the project, so the process cwd is a last
    resort. Order: payload `cwd` (Claude Code; Cursor on shell events), env
    `CURSOR_PROJECT_DIR`, payload `workspace_roots[0]`, env `CLAUDE_PROJECT_DIR`,
    `Path.cwd()`. A payload `cwd` is used as given; the other sources must be dirs.
    """
    payload = payload if isinstance(payload, dict) else {}
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    roots = payload.get("workspace_roots")
    first_root = roots[0] if isinstance(roots, list) and roots else None
    for value in (os.environ.get("CURSOR_PROJECT_DIR"), first_root, os.environ.get("CLAUDE_PROJECT_DIR")):
        found = _dir_value(value)
        if found is not None:
            return found
    return Path.cwd()


def hook_candidates(payload: dict[str, Any] | None) -> list[Path]:
    """Every host source that is a dir, in resolver order, then Path.cwd(); no duplicates.

    Payload `cwd`, `CURSOR_PROJECT_DIR`, each `workspace_roots` entry, `CLAUDE_PROJECT_DIR`.
    A Cursor shell event can send a `cwd` outside the project, so a payload `cwd` must
    not hide the project dirs (PR #87 R1). A guard denies when one candidate denies, so
    more candidates only make it stricter. The process cwd stays a candidate, so a call
    that a guard denies today stays denied.
    """
    payload = payload if isinstance(payload, dict) else {}
    roots = payload.get("workspace_roots")
    values = [payload.get("cwd"), os.environ.get("CURSOR_PROJECT_DIR")]
    values += list(roots) if isinstance(roots, list) else []
    values.append(os.environ.get("CLAUDE_PROJECT_DIR"))
    out: list[Path] = []
    for path in [_dir_value(v) for v in values] + [Path.cwd()]:
        if path is not None and path not in out:
            out.append(path)
    return out


def has_session(ctx: Path) -> bool:
    """A session file exists in this `.context` (readable or not)."""
    return (Path(ctx) / "cadence-session.json").is_file()


def candidate_contexts(
    payload: dict[str, Any] | None, to_context: Callable[[Path], Path]
) -> list[tuple[Path, Path]]:
    """(workspace, context) for each candidate; one pair for each context, first kept."""
    pairs: list[tuple[Path, Path]] = []
    for workspace in hook_candidates(payload):
        ctx = to_context(workspace)
        if all(ctx != seen for _w, seen in pairs):
            pairs.append((workspace, ctx))
    return pairs


def write_context(pairs: list[tuple[Path, Path]], process_ctx: Path) -> tuple[Path, Path] | None:
    """The one pair a state-writing guard uses, else None.

    1. The process cwd context, when it is a candidate and has a session. Its workspace is
       `Path.cwd()`: two worktrees can share one context, and the process cwd is the one
       that today's guard reads.
    2. Else the first pair with a session.
    """
    if any(ctx == process_ctx for _w, ctx in pairs) and has_session(process_ctx):
        return Path.cwd(), process_ctx
    return next((pair for pair in pairs if has_session(pair[1])), None)


def chat_id(payload: dict[str, Any]) -> str:
    """The chat this hook call belongs to: Cursor `conversation_id`, else Claude `session_id`.

    One order for every hook, so the id that activation records is the id the stop guard reads.
    """
    for key in ("conversation_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_tool_name(payload: dict[str, Any]) -> str:
    for key in ("tool_name", "tool", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    """tool_input as a dict; a JSON string is re-parsed, any other string is a patch body."""
    candidate = payload.get("tool_input") or payload.get("input") or payload.get("arguments") or {}
    if isinstance(candidate, dict):
        return candidate
    if isinstance(candidate, str):
        try:
            reparsed = json.loads(candidate)
        except json.JSONDecodeError:
            return {"patch": candidate}
        if isinstance(reparsed, dict):
            return reparsed
        return {"patch": candidate}
    return {}


def extract_file_path(tool_input: dict[str, Any]) -> str | None:
    for key in ("path", "file_path", "file", "target_file", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


PATCH_PATH = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$", re.M)


def extract_patch_paths(tool_input: dict[str, Any]) -> list[str]:
    """Paths an apply-patch body touches (Add/Update/Delete File headers and Move to)."""
    for key in ("patch", "content", "input"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return [m.group(1).strip() for m in PATCH_PATH.finditer(value)]
    return []


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")


def tool_allow(host: str) -> None:
    if host == "cursor":
        emit({"permission": "allow"})


def tool_deny(host: str, message: str) -> None:
    if host == "cursor":
        emit({"permission": "deny", "user_message": message, "agent_message": message})
    else:
        emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": message}})


def tool_update_input(host: str, tool_input: dict[str, Any]) -> None:
    """Allow the call with a replacement tool_input (Claude Code `updatedInput`)."""
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                 "updatedInput": tool_input}})


def context_inject(host: str, event: str, text: str) -> None:
    """Add context at the start of a subagent (Claude Code SubagentStart)."""
    if host == "claude":
        emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def stop_allow(host: str, notice: str | None = None) -> None:
    if notice:
        sys.stderr.write(notice + "\n")
    if host == "cursor":
        emit({})
    elif notice:
        emit({"systemMessage": notice})


def stop_block(host: str, message: str) -> None:
    if host == "cursor":
        emit({"followup_message": message})
    else:
        emit({"decision": "block", "reason": message})


def fail_closed(main: Callable[[], int]) -> int:
    try:
        return main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - any crash must block, not pass
        sys.stderr.write(f"cadence hook failed closed: {exc!r}\n")
        return 2
