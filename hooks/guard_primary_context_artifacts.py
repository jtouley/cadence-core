#!/usr/bin/env python3
"""Block cadence planning artifacts in worktree-local .context (preToolUse).

Cadence: cadence-session.json; uses in-skill shared/scripts/context_root.py.

Writes must target primary context root, not worktree-local shadow .context.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_HOOKS = Path(__file__).resolve().parent
_ROOT = _HOOKS.parent
_LOCAL_SHARED = _ROOT / "shared" / "scripts"
if _LOCAL_SHARED.is_dir() and str(_LOCAL_SHARED) not in sys.path:
    sys.path.insert(0, str(_LOCAL_SHARED))

if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

try:
    from hook_protocol import (
        extract_file_path,
        extract_patch_paths,
        extract_tool_input,
        extract_tool_name,
        fail_closed,
        hook_candidates,
        parse_host,
        read_payload,
        tool_allow,
        tool_deny,
    )
except Exception as exc:  # noqa: BLE001 - startup errors must block, not pass (exit 1 fails open)
    sys.stderr.write(f"cadence hook failed closed at startup: {exc!r}\n")
    raise SystemExit(2)

os.environ.setdefault("CADENCE_HOME", str(_ROOT))
os.environ.setdefault("CADENCE_SKILL_ROOT", str(_ROOT))


GATED_REL_PREFIXES = (
    ".context/reviews/",
    ".context/adversarial/",
    ".context/deferrals/",
    ".context/test-benchmarks/",
    ".context/prs/reviews/",
    ".context/vibetest/",
    ".context/implementation/",
    ".context/design-review/",
    ".context/issues/",
    ".context/sessions/",
)

GATED_ROOT_FILES = (
    ".context/cadence-session.json",
    ".context/.active_skill",
)


def load_payload() -> dict[str, Any]:
    return read_payload()


def git_toplevel(start: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    root = proc.stdout.strip()
    return Path(root).resolve() if root else None


def normalize_relative(path_text: str, *, repo_root: Path) -> str | None:
    path_text = path_text.strip().strip('"').strip("'")
    if not path_text:
        return None
    path_obj = Path(path_text)
    if path_obj.is_absolute():
        try:
            resolved = path_obj.resolve()
        except OSError:
            resolved = path_obj
        try:
            return resolved.relative_to(repo_root).as_posix()
        except ValueError:
            return None
    normalized = Path(path_text).as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("../"):
        return None
    return normalized


def is_gated_context_rel(rel: str) -> bool:
    norm = rel.replace("\\", "/")
    if norm in GATED_ROOT_FILES:
        return True
    for prefix in GATED_REL_PREFIXES:
        if norm.startswith(prefix):
            return True
    if norm.startswith(".context/") and norm.endswith(".json") and "gate" in norm:
        return True
    return False


def primary_context_paths(workspace: Path) -> tuple[Path, Path] | None:
    try:
        from context_root import context_path, find_primary_checkout
    except ImportError:
        return None
    primary = find_primary_checkout(workspace)
    # context_path never creates .context: this hook runs in every repo, Cadence or not.
    primary_ctx = context_path(workspace)
    return primary, primary_ctx.resolve()


def repo_common_dir(path: Path) -> Path | None:
    start = path if path.is_dir() else path.parent
    while not start.is_dir() and start != start.parent:
        start = start.parent
    if not start.is_dir():
        return None
    try:
        from context_root import git_common_dir
    except ImportError:
        return None
    return git_common_dir(start)


def shadow_write_blocked(
    rel: str, *, repo_root: Path, primary: Path, primary_ctx: Path
) -> str | None:
    if not is_gated_context_rel(rel):
        return None
    abs_target = (repo_root / rel).resolve()
    try:
        abs_target.relative_to(repo_root.resolve())
    except ValueError:
        return None

    try:
        abs_target.relative_to(primary_ctx)
        return None
    except ValueError:
        pass

    shadow_root = repo_root.resolve()
    if shadow_root == primary.resolve():
        return None

    if repo_common_dir(shadow_root) != repo_common_dir(primary):
        return None

    subpath = rel.removeprefix(".context/").removeprefix(".context")
    suggested = primary_ctx / subpath if subpath else primary_ctx / Path(rel).name
    skill = os.environ.get("CADENCE_HOME") or os.environ.get("CADENCE_SKILL_ROOT") or str(_ROOT)
    return (
        "Blocked: cadence artifact must live in primary .context (not worktree shadow).\n"
        f"Attempted: {rel}\n"
        f"Write instead: {suggested}\n"
        f"Primary root: python3 {skill}/shared/scripts/context_root.py"
    )


def check_paths(
    raw_paths: list[str], *, repo_root: Path, primary: Path, primary_ctx: Path
) -> str | None:
    for raw in raw_paths:
        rel = normalize_relative(raw, repo_root=repo_root)
        if rel is None:
            continue
        msg = shadow_write_blocked(
            rel, repo_root=repo_root, primary=primary, primary_ctx=primary_ctx
        )
        if msg:
            return msg
    return None


WRITE_TOOLS = {
    "strreplace", "write", "editnotebook", "applypatch",  # Cursor
    "edit", "multiedit", "notebookedit",                  # Claude Code
}


def main(argv: list[str] | None = None) -> int:
    host = parse_host(argv)
    payload = load_payload()
    tool = extract_tool_name(payload).lower().replace("-", "_")
    if tool not in WRITE_TOOLS:
        tool_allow(host)
        return 0

    tool_input = extract_tool_input(payload)

    paths: list[str] = []
    if tool == "applypatch":
        paths = extract_patch_paths(tool_input)
    else:
        single = extract_file_path(tool_input)
        if single:
            paths = [single]

    if not paths:
        tool_allow(host)
        return 0

    # Each host candidate is checked; one deny denies (cd-82 D8). No session is read.
    for workspace in hook_candidates(payload):
        ctx = primary_context_paths(workspace)
        if ctx is None:
            continue
        primary, primary_ctx = ctx
        repo_root = git_toplevel(workspace) or primary
        blocked = check_paths(paths, repo_root=repo_root, primary=primary, primary_ctx=primary_ctx)
        if blocked:
            tool_deny(host, blocked)
            return 0

    tool_allow(host)
    return 0


if __name__ == "__main__":
    raise SystemExit(fail_closed(main))
