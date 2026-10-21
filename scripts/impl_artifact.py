#!/usr/bin/env python3
"""E2 — impl-artifact script gate."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gate_common import cli, fail, ok  # noqa: E402
from implement_gate import run_worktree  # noqa: E402
from phase_ledger import resolve_base  # noqa: E402
from pipeline_run_binding import run_for  # noqa: E402

SLUG = "impl-artifact"


def _git(worktree: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=worktree, capture_output=True, text=True, timeout=30
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


class MergeBaseError(RuntimeError):
    """`git merge-base <base> HEAD` failed: the base does not resolve or shares no history."""


class GitError(RuntimeError):
    """A git command that a gate needs failed. A gate fails on it; it never reads it as "no change"."""


def _git_paths(worktree: Path, *args: str) -> list[str]:
    """NUL-separated paths from git, unquoted (core.quotePath off). Raises GitError on any failure."""
    try:
        result = subprocess.run(["git", "-c", "core.quotePath=false", *args, "-z"], cwd=worktree,
                                capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {args[0]} failed: {exc}") from exc
    if result.returncode != 0:
        raise GitError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()[:200]}")
    return [name for name in result.stdout.decode("utf-8", errors="surrogateescape").split("\0") if name]


def merge_base(worktree: Path, base: str) -> str:
    """The merge base of `base` and HEAD. Raises MergeBaseError when there is none."""
    fork = _git(worktree, "merge-base", base, "HEAD")
    if not fork:
        raise MergeBaseError(f"git merge-base {base} HEAD failed in {worktree}")
    return fork


def _untracked(worktree: Path) -> list[str]:
    return [name for name in _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines() if name]


def changed_files(worktree: Path, fork: str) -> list[str]:
    """Files added or changed since `fork`, committed or not, plus untracked files.

    Raises GitError when git fails: a gate must not read an error as an empty change.
    """
    tracked = _git_paths(worktree, "diff", "--name-only", "--diff-filter=d", fork)
    untracked = _git_paths(worktree, "ls-files", "--others", "--exclude-standard")
    return sorted(set(tracked + untracked))


def _git_diff_stat(worktree: Path, base: str | None) -> str:
    """Committed and uncommitted work since the merge base, plus untracked files.

    `git diff --stat HEAD` alone is empty once the work is committed (each TDD
    phase commits), so it cannot tell a finished change from no change.
    Raises MergeBaseError when the merge base cannot be found.
    """
    if base is None:
        return ""
    stat = _git(worktree, "diff", "--stat", merge_base(worktree, base))
    extra = "\n".join(f" {name} | new (untracked)" for name in _untracked(worktree))
    return "\n".join(part for part in (stat, extra) if part)


def run(context_dir: Path, *, check: bool = False) -> int:
    session = run_for(context_dir)  # This worktree's run, never the root redirect
    if session is None:
        return fail(SLUG, ["impl-artifact: missing cadence-session.json"])
    task_id = session.get("task_id")
    if not task_id:
        return fail(SLUG, ["impl-artifact: cadence-session.json missing task_id"])

    artifact = context_dir / "implementation" / f"{task_id}.json"
    if check:
        if not artifact.is_file():
            return fail(SLUG, [f"impl-artifact: {artifact} does not exist (run without --check first)"])
        try:
            recorded = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            recorded = {}
        stat = recorded.get("git_diff_stat") if isinstance(recorded, dict) else ""
        if not str(stat or "").strip():
            return fail(SLUG, ["impl-artifact: the recorded diff since the merge base is empty"])
        return ok(SLUG)

    worktree = run_worktree(context_dir)
    base = resolve_base(worktree, session)
    try:
        diff_stat = _git_diff_stat(worktree, base)
    except MergeBaseError as exc:
        return fail(SLUG, [f"impl-artifact: {exc}"])
    if not diff_stat.strip():
        if base is None:
            return fail(SLUG, ["impl-artifact: no base ref found (origin/HEAD, origin/main, ...); fetch the base branch"])
        return fail(SLUG, [f"impl-artifact: no change since the merge base with {base}"])
    payload = {
        "task_id": task_id,
        "pipeline_run_id": session.get("pipeline_run_id", ""),
        "written_at": datetime.now(timezone.utc).isoformat(),
        "RUN_BY_SCRIPT": "impl_artifact.py",
        "base": base,
        "git_diff_stat": diff_stat,
    }
    artifact.parent.mkdir(parents=True, exist_ok=True)
    tmp = artifact.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(artifact)
    return ok(SLUG, note=f"wrote {artifact}")


if __name__ == "__main__":
    raise SystemExit(cli(run))
