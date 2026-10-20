#!/usr/bin/env python3
"""Resolve the canonical gitignored .context directory.

`.context/` is local-only (never committed). Linked git worktrees each have
their own untracked tree, so writing `.context` relative to cwd drops artifacts
into a directory that disappears when the worktree is deleted.

Canonical location: `<primary-checkout>/.context` where primary-checkout is
the first `git worktree list --porcelain` entry (the main working tree).

Code edits and committed `plans/` stay in the linked worktree. Session files,
reviews, gates, vibe-tests, and PR review markdown always go to the primary
`.context`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd)


def git_toplevel(cwd: Path | None = None) -> Path | None:
    result = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def git_common_dir(cwd: Path | None = None) -> Path | None:
    result = run(["git", "rev-parse", "--git-common-dir"], cwd=cwd)
    if result.returncode != 0:
        return None
    raw = Path(result.stdout.strip())
    if not raw.is_absolute():
        base = cwd or Path.cwd()
        raw = (base / raw).resolve()
    return raw.resolve()


def find_primary_checkout(cwd: Path | None = None) -> Path:
    """Main working tree for the repo containing cwd (or cwd if not a git repo)."""
    start = cwd.resolve() if cwd is not None else Path.cwd()
    result = run(["git", "worktree", "list", "--porcelain"], cwd=start)
    if result.returncode != 0:
        top = git_toplevel(start)
        return top if top is not None else start
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1])
    top = git_toplevel(start)
    return top if top is not None else start


def context_path(cwd: Path | None = None) -> Path:
    """Canonical `.context` location without creating it (for read-only callers such as hooks)."""
    return find_primary_checkout(cwd) / ".context"


def context_dir(cwd: Path | None = None) -> Path:
    path = context_path(cwd)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_artifact_path(path: str | Path | None, *, default_name: str, cwd: Path | None = None) -> Path:
    """Map `.context/foo` (relative) onto the primary checkout `.context/foo`."""
    if path is None or str(path).strip() == "":
        return context_dir(cwd) / default_name
    p = Path(path)
    if p.is_absolute():
        return p
    parts = p.parts
    if parts and parts[0] == ".context":
        return context_dir(cwd).joinpath(*parts[1:])
    return find_primary_checkout(cwd) / p


def same_repo(a: Path, b: Path) -> bool:
    da = git_common_dir(a)
    db = git_common_dir(b)
    if da is None or db is None:
        return a.resolve() == b.resolve()
    return da == db


def main() -> int:
    parser = argparse.ArgumentParser(description="Print canonical .context or primary checkout")
    parser.add_argument(
        "--checkout",
        action="store_true",
        help="Print primary checkout root instead of the .context directory",
    )
    args = parser.parse_args()
    root = find_primary_checkout()
    print(root if args.checkout else root / ".context")
    return 0


if __name__ == "__main__":
    sys.exit(main())
