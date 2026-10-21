#!/usr/bin/env python3
"""Pre-tool hook: enforce CadenceGate.check_action on edits and shell writes.

Registered for Cursor (preToolUse, beforeShellExecution) and Claude Code
(PreToolUse with `--host claude`). Output format per host: hook_protocol.py.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

_HOOKS = Path(__file__).resolve().parent
_ROOT = _HOOKS.parent
_SHARED = _ROOT / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

os.environ.setdefault("CADENCE_HOME", str(_ROOT))
os.environ.setdefault("CADENCE_SKILL_ROOT", str(_ROOT))

if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))
# Appended, not inserted: no scripts/ module may hide a hooks/ or shared/ module.
if str(_ROOT / "scripts") not in sys.path:
    sys.path.append(str(_ROOT / "scripts"))

try:
    from skill_gate import (
        CadenceGate,
        gated_write,
        plan_document,
        protected_shell_mention,
        protected_writer,
        shell_write_targets,
    )
    from context_root import find_primary_checkout  # on sys.path via skill_gate
    import peer_attest  # shared/scripts, on sys.path via skill_gate
    import orchestrator_scope  # shared/scripts, on sys.path via skill_gate
    from hook_protocol import (
        chat_id,
        extract_file_path,
        extract_patch_paths,
        extract_tool_input,
        extract_tool_name,
        fail_closed,
        hook_candidates,
        hook_workspace,
        load_payload,
        parse_host,
        tool_allow,
        tool_deny,
    )
    from skill_paths import display_command
except Exception as exc:  # noqa: BLE001 - startup errors must block, not pass (exit 1 fails open)
    sys.stderr.write(f"cadence hook failed closed at startup: {exc!r}\n")
    raise SystemExit(2)

SESSION_FILE = "cadence-session.json"

TOOL_TO_ACTION = {
    # Cursor
    "Write": "write",
    "StrReplace": "strreplace",
    "EditNotebook": "editnotebook",
    "ApplyPatch": "write",
    # Claude Code ("Write" is shared)
    "Edit": "strreplace",
    "MultiEdit": "strreplace",
    "NotebookEdit": "editnotebook",
}
SHELL_TOOLS = {"Bash", "Shell"}
RECOVER = (
    "BLOCKED: the cadence session file is unreadable or points to a missing file.\n"
    f"ACTION: {display_command('python3 scripts/activate_session.py --from-branch --fresh', _ROOT)} "
    "(or --deactivate to end the run)"
)


def planned_checks(payload: dict[str, Any]) -> tuple[list[tuple[str, str]], str | None]:
    """(action, path) pairs this call would write, plus a protected name a shell write mentions."""
    tool = extract_tool_name(payload)
    if payload.get("hook_event_name") == "beforeShellExecution" or tool in SHELL_TOOLS:
        command = payload.get("command")
        if not isinstance(command, str):
            command = extract_tool_input(payload).get("command")
        if not isinstance(command, str):
            return [], None
        return [("shell", t) for t in shell_write_targets(command)], protected_shell_mention(command)
    action = TOOL_TO_ACTION.get(tool)
    if action is None:
        return [], None
    tool_input = extract_tool_input(payload)
    path = extract_file_path(tool_input)
    paths = extract_patch_paths(tool_input) if tool == "ApplyPatch" else []
    if path and path not in paths:
        paths.append(path)
    return [(action, p) for p in paths], None


from pr_guard import SEGMENT_START  # noqa: E402 - one rule for where a shell command starts

ACTIVATION = re.compile(r"\bactivate_session\.py\b(?![^;&|\n]*--(?:deactivate|status|check|hard-stop)\b)")
# A shell segment that runs activation (cd-47 D2): the command word is python, uv run
# python or the script path itself. The path may be quoted (SKILL.md quotes it). A
# command that only names the script (grep, cat, echo "...") does not match.
ACTIVATION_RUN = re.compile(
    SEGMENT_START +
    r"(?:uv\s+run\s+)?(?:python3?(?:\.\d+)?\s+)?"
    r"(?:\"[^\"]*activate_session\.py\"|'[^']*activate_session\.py'|[^\s;&|'\"]*activate_session\.py)"
    r"(?=$|[\s;&|)])"
    r"(?![^;&|\n]*--(?:deactivate|status|check|hard-stop)\b)"
)


def note_activation_owner(payload: dict[str, Any]) -> None:
    """Pass the chat id to an activation that is about to run (Cursor has no id in the env).

    Fail-open: a failure here never blocks the command.
    """
    try:
        command = payload.get("command") or extract_tool_input(payload).get("command")
        owner = chat_id(payload)
        if not (isinstance(command, str) and ACTIVATION.search(command) and isinstance(owner, str) and owner):
            return
        import session_store

        from context_root import git_toplevel

        target = activation_target(command, hook_workspace(payload))
        if git_toplevel(target) is None:
            return  # activation there makes no run of a repo; create no .context/
        session_store.write_pending_owner(owner, target)
    except Exception:  # noqa: BLE001
        return


_CD = re.compile(r"""(?:^|[;&|(\n{])\s*cd(?:\s+-[LPe@]+)*\s+("[^"]*"|'[^']*'|[^\s;&|)]+)""")
_WORKSPACE = re.compile(r"""--workspace(?:=|\s+)("[^"]*"|'[^']*'|[^\s;&|)]+)""")


def _shell_path(token: str, base: Path | None) -> Path | None:
    """The directory a `cd` or `--workspace` token names, or None when the hook cannot know it.

    A variable set in the same command, a command substitution and `cd -` are not
    readable here: the hook expands only its own environment (R1 on PR #57).
    With no `base`, only an absolute path counts.
    """
    raw = token.strip("\"'")
    if raw == "-":
        return None
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if "$" in expanded or "`" in expanded:
        return None
    path = Path(expanded)
    if path.is_absolute():
        return path
    return base / path if base is not None else None


def _in_quotes(command: str, pos: int) -> bool:
    before = command[:pos]
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1


def _closed_before(command: str, start: int, end: int) -> bool:
    """A subshell that is open at `start` closes before `end`: its `cd` does not reach `end`."""
    depth = 0
    for char in command[start:end]:
        depth += char == "("
        depth -= char == ")"
        if depth < 0:
            return True
    return False


def activation_target(command: str, cwd: Path) -> Path:
    """The directory an activation command works on (#56).

    `--workspace <dir>` in the activation segment, else the directory the `cd`
    commands before it lead to, else the payload cwd. A token the hook cannot
    read (a variable of the same command, `cd -`) makes it fall back to the cwd.
    """
    match = ACTIVATION_RUN.search(command)
    if match is None:
        return cwd
    segment = re.split(r"[;&|\n]", command[match.end():], maxsplit=1)[0]
    workspace = _WORKSPACE.search(segment)
    absolute = _shell_path(workspace.group(1), None) if workspace is not None else None
    if absolute is not None:
        return absolute  # an absolute --workspace does not depend on any cd
    here = cwd
    for cd in _CD.finditer(command[: match.start() + 1]):
        if _in_quotes(command, cd.start(1)) or _closed_before(command, cd.end(), match.start() + 1):
            continue
        here = _shell_path(cd.group(1), here)
        if here is None:
            return cwd
    if workspace is None:
        return here
    return _shell_path(workspace.group(1), here) or cwd


def note_activation_orchestrator(payload: dict[str, Any]) -> None:
    """Leave the activating agent's id for activation (cd-47 D2): the orchestrator's id.

    The note goes to the canonical .context of the repo the command activates
    (activation_target): a Claude Code subagent's Bash cwd resets to the parent's
    project, so the payload cwd is often another repo (#56). Only an
    anchored activation command writes it (and may create .context/, as activation
    itself does). Fail-open: a failure here never blocks the command.
    """
    try:
        command = payload.get("command") or extract_tool_input(payload).get("command")
        if not (isinstance(command, str) and ACTIVATION_RUN.search(command)):
            return
        from context_root import git_toplevel
        import session_store

        cwd = hook_workspace(payload)
        target = activation_target(command, cwd)
        if git_toplevel(target) is None:
            return
        agent = payload.get("agent_id")
        session_store.write_pending_orchestrator(
            agent if isinstance(agent, str) and agent else None, find_primary_checkout(target) / ".context"
        )
    except Exception:  # noqa: BLE001
        return


def pr_guard_denied(host: str, payload: dict[str, Any]) -> bool:
    """`gh pr create` / `gh pr ready` before planned_checks: they write no file (cd-47 D4, SF-1)."""
    if not (payload.get("hook_event_name") == "beforeShellExecution" or extract_tool_name(payload) in SHELL_TOOLS):
        return False
    command = payload.get("command") or extract_tool_input(payload).get("command")
    if not isinstance(command, str):
        return False
    from pr_guard import pr_guard_decision

    message = pr_guard_decision(command, hook_workspace(payload), host)
    if message is None:
        return False
    tool_deny(host, message)
    return True


def main(argv: list[str] | None = None) -> int:
    host = parse_host(argv)
    payload, payload_error = load_payload()
    if payload_error or payload is None:
        for workspace in _session_workspaces(None):
            gate = CadenceGate(workspace)
            # Fail closed while a run is active, or when the session cannot be read.
            # A finished run (active_skill cleared by --deactivate) does not block.
            if gate.session is None or gate.session.get("active_skill") == gate.skill_name:
                tool_deny(
                    host,
                    "BLOCKED: "
                    + (payload_error or "tool payload is missing")
                    + "\nACTION: retry the tool call with a JSON object",
                )
                return 0
        tool_allow(host)
        return 0
    note_activation_owner(payload)
    note_activation_orchestrator(payload)
    if pr_guard_denied(host, payload):
        return 0
    checks, mention = planned_checks(payload)
    if not checks and not mention:
        tool_allow(host)
        return 0
    if host == "claude" and peer_verdict_denied(host, payload, checks):
        return 0

    # Installed globally: only enforce while a candidate workspace runs a Cadence session.
    # Each candidate with a session is judged; one deny denies (cd-82 D8).
    for workspace in _session_workspaces(payload):
        message = _gate_message(workspace, checks, mention)
        if message is None and host == "claude":  # ADR-0017: a peer writes the code
            message = orchestrator_scope.write_denial(payload, workspace, [p for _, p in checks])
        if message is not None:
            tool_deny(host, message)
            return 0
    tool_allow(host)
    return 0


def _session_workspaces(payload: dict[str, Any] | None) -> list[Path]:
    """The candidates (hook_candidates) whose primary checkout has a session file.

    Check the file first so repos that never ran Cadence get no .context/ dir.
    """
    return [w for w in hook_candidates(payload) if (find_primary_checkout(w) / ".context" / SESSION_FILE).is_file()]


def _gate_message(workspace: Path, checks: list[tuple[str, str]], mention: str | None) -> str | None:
    """The deny text for one workspace that has a session file, or None to allow."""
    gate = CadenceGate(workspace)
    if gate.session is None:
        # The session file exists but can't be read (or its pointer dangles): fail
        # closed for gated sources and pipeline state, leave everything else alone.
        risky = mention or any(
            protected_writer(path, gate.workspace) or gated_write(path, gate.workspace)
            for _, path in checks
        )
        return RECOVER if risky else None
    if gate.session.get("active_skill") != gate.skill_name:
        return None
    if mention and plan_document(mention, gate.workspace) is None:
        return (
            f"BLOCKED: this shell command writes and names protected pipeline state (`{mention}`).\n"
            "ACTION: change the session, gates and findings ledger only through their scripts "
            "(activate_session.py, the stage gate scripts, findings_io.py, ledger_verify.py)"
        )
    # A plan directory that a shell write names is judged as a write to it (frozen after P9).
    for action, path in [*checks, *([("shell", mention)] if mention else [])]:
        result = gate.check_action(action, path)
        if not result.allowed:
            message = result.message
            if result.action_required:
                message += f"\nACTION: {result.action_required}"
            return message
    return None


def _claude_runs(context: Path) -> list[dict]:
    """Active Claude Code runs whose records are in this .context (any worktree)."""
    import session_store

    return [
        r for r in session_store.run_records(context)
        if r.get("active_skill") == "cadence" and r.get("status") != "completed" and r.get("host") == "claude"
    ]


def _attest_runs(runs: list[dict], cwd: Path) -> list[str]:
    """Run ids for a peer's attest row (cd-47 SF-3).

    1. The run whose workspace holds the payload cwd (the deepest one).
    2. Else the only active run.
    3. Else every active run.
    """
    here = cwd.resolve()
    holding = []
    for run in runs:
        try:
            workspace = Path(str(run.get("workspace") or "")).resolve()
        except OSError:
            continue
        if run.get("workspace") and (here == workspace or workspace in here.parents):
            holding.append((len(workspace.parts), run))
    if holding:
        return [str(max(holding, key=lambda pair: pair[0])[1].get("pipeline_run_id") or "")]
    return [str(run.get("pipeline_run_id") or "") for run in runs]


_PEER_ACTION = "ACTION: run the stage's peer with its lean prompt (references/delegation.md)"


def _verdict_write_denial(action: str, agent: object, rel: str, context: Path) -> str | None:
    """The deny text for a write to the peer artifact `rel`, or None when a peer may write it.

    A write with no agent_id is the top-level agent, and an agent that ran activation is
    the run's orchestrator (G-D5): neither writes verdicts, with any tool. #78: a peer's
    shell write leaves no content to attest, and it can replace an earlier report, so a
    peer writes a report with Write or Edit only. The orchestrator cases come first, so
    the text never sends an orchestrator to a Write that the next rule denies (R1 N-1).
    """
    if not (isinstance(agent, str) and agent):
        return f"BLOCKED: only a delegated peer writes `{rel}`.\n{_PEER_ACTION}"
    if agent in peer_attest.orchestrator_ids(context):
        return (f"BLOCKED: this agent ran activation, so it is the run's orchestrator; "
                f"only a delegated peer writes `{rel}`.\n{_PEER_ACTION}")
    if action == "shell":
        return (f"BLOCKED: a shell write to `{rel}` carries no attestation.\n"
                "ACTION: write the report with the Write or Edit tool")
    return None


def peer_verdict_denied(host: str, payload: dict, checks: list[tuple[str, str]]) -> bool:
    """Peer verdict files on a Claude Code run: deny the orchestrator, attest a peer.

    The runs come from the repo the file is in, not from the hook's cwd (the
    chat's project): a run in another repo or worktree is still covered (MF-4).
    The rule applies while any Claude run in that .context is active, not only
    the run the root redirect names (SF-3). A write with no agent_id (the top-level
    agent) or by an agent that ran activation in any run (G-D5) is denied.
    This only adds denials and attestations; every other check still runs after it.
    Returns True when this call was denied.
    """
    base = hook_workspace(payload)
    agent = payload.get("agent_id")
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    for action, path in checks:
        target = Path(path) if Path(path).is_absolute() else base / path
        anchor = next((p for p in [target.parent, *target.parents] if p.is_dir()), base)
        context = find_primary_checkout(anchor) / ".context"
        try:
            rel = target.resolve().relative_to(context.resolve()).as_posix()
        except ValueError:
            continue
        if not peer_attest.is_peer_artifact(rel):
            continue
        runs = _claude_runs(context)
        if not runs:
            continue
        reason = _verdict_write_denial(action, agent, rel, context)
        if reason is not None:
            tool_deny(host, reason)
            return True
        content = peer_attest.expected_content(tool, tool_input, target) if action != "shell" else None
        if content is not None:
            for run_id in _attest_runs(runs, base):
                peer_attest.record(context, run_id, rel, agent, peer_attest.sha256(content))
    return False


if __name__ == "__main__":
    raise SystemExit(fail_closed(main))
