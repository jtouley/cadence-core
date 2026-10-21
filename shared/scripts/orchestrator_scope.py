"""The orchestrator routes, and the peers do the work.

On Claude Code, during an active run, the orchestrator runs each gate, sends each peer its
rendered command, and reads the peer's one-line return. A peer reads the plan, the reviews and
the code, and a peer writes the code. This module gives the deny text for an orchestrator call
that does a peer's work: a read of a run artifact or of the code, a `git diff` or `git show`
of the change, or a write to the code. A subagent that is not an orchestrator is never judged.

Pi and other headless hosts have no peers, so the main agent there does every block itself.
Config: `orchestrator_scope.mode`, `enforce` (the default) or `off`.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import peer_attest
from skill_gate import (
    ProjectConfigError,
    canonical_context_dir,
    context_relative,
    is_gated_path,
    is_test_path,
    load_config,
    load_session,
    workspace_relative,
)

# Run artifacts, relative to .context: what the peers write and read. Session, gate and
# ledger state is not here: the scripts that the orchestrator runs read it.
RUN_ARTIFACTS = (*peer_attest.PEER_ARTIFACT_GLOBS, "plans/*", "proposals/*", "implementation/*")
# The markdown of a run that a peer or a block's script writes: the orchestrator writes none.
WRITTEN_FOR_IT = (*RUN_ARTIFACTS, "deferrals/*", "prs/comments/*", "summaries/*")
WORKSPACE_PLANS = ".cursor/plans/"
# A `git diff` or `git show` prints the change. `--stat` and the name forms print only its shape.
GIT_DIFF = re.compile(
    r"(?:^|[;&|(]\s*)git\s+(?:-\S+\s+)*(?:diff|show)\b"
    r"(?![^;&|\n]*--(?:stat|shortstat|numstat|name-only|name-status)\b)"
)
ACTION = ("ACTION: run the block's gate, or send its peer the rendered command (references/delegation.md). "
          "The peer reads the files and returns one line; E1 and each fix round are `/spec-driven` peers")


def enforced(workspace: Path) -> bool:
    """An active Claude Code run whose config does not turn the rule off. A bad layer enforces."""
    session = load_session(workspace)
    if not session or session.get("active_skill") != "cadence" or session.get("host") != "claude":
        return False
    try:
        return (load_config(workspace).get("orchestrator_scope") or {}).get("mode") != "off"
    except ProjectConfigError:
        return True


def is_orchestrator(payload: dict[str, Any], workspace: Path) -> bool:
    """No agent_id (the top-level agent), or an agent that ran activation (G-D5)."""
    agent = payload.get("agent_id")
    if not (isinstance(agent, str) and agent):
        return True
    return agent in peer_attest.orchestrator_ids(canonical_context_dir(workspace))


def code_path(path: str, workspace: Path) -> str | None:
    """The workspace path of a source or test file, else None."""
    rel = workspace_relative(path, workspace)
    if not rel:
        return None
    # `src` names the directory of the prefix `src/`: a Grep or a reader of it reads the code.
    return rel if is_gated_path(rel, workspace) or is_gated_path(rel + "/", workspace) or is_test_path(rel) else None


def out_of_scope(path: str, workspace: Path, globs: tuple[str, ...] = RUN_ARTIFACTS) -> str | None:
    """The path to show when the orchestrator may not read (or, with WRITTEN_FOR_IT, write) `path`, else None."""
    rel = context_relative(path, workspace)
    if rel is not None:
        return f".context/{rel}" if any(fnmatchcase(rel, g) for g in globs) else None
    rel = workspace_relative(path, workspace)
    if rel and rel.startswith(WORKSPACE_PLANS):
        return rel
    return code_path(path, workspace)


def _denial(what: str) -> str:
    return f"BLOCKED: the orchestrator does not read or write {what}; a peer or a gate does.\n{ACTION}"


def read_denial(payload: dict[str, Any], workspace: Path, paths: list[str]) -> str | None:
    """The deny text for an orchestrator read of a run artifact, the code or the diff, else None."""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    grep = tool_input.get("path") if payload.get("tool_name") == "Grep" else None
    searched = [grep] if isinstance(grep, str) and grep.strip() else []
    hits = [h for p in [*paths, *searched] if (h := out_of_scope(p, workspace))]
    if not hits and not GIT_DIFF.search(command):
        return None
    if not is_orchestrator(payload, workspace) or not enforced(workspace):
        return None
    return _denial(f"`{hits[0]}`" if hits else "the diff of the run")


def write_denial(payload: dict[str, Any], workspace: Path, paths: list[str]) -> str | None:
    """The deny text for an orchestrator write to the code or to the markdown of the run, else None."""
    hit = next((h for p in paths if (h := out_of_scope(p, workspace, WRITTEN_FOR_IT))), None)
    if hit is None or not is_orchestrator(payload, workspace) or not enforced(workspace):
        return None
    return _denial(f"`{hit}`")
