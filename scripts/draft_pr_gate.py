#!/usr/bin/env python3
"""E4 — draft-pr gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import (  # noqa: E402
    _BRANCH,
    _PR_URL,
    draft_pr_record,
    draft_pr_record_path,
    require_artifact,
)
from gate_common import cli, current_branch, fail, ok, write_gate_breadcrumb  # noqa: E402
from pipeline_run_binding import run_for  # noqa: E402

SLUG = "draft-pr"


def _impl_diff_errors(context_dir: Path, session: dict | None) -> list[str]:
    """E4 needs a recorded diff of this run. An empty snapshot is not a pull request."""
    task_id = session.get("task_id") if isinstance(session, dict) else None
    if not (isinstance(task_id, str) and task_id.strip()):
        # Without a task id any implementation file could belong to another ticket.
        return ["draft-pr: the session has no task_id, so the impl artifact cannot be found"]
    artifact = context_dir / "implementation" / f"{task_id}.json"
    if not artifact.is_file():
        return ["draft-pr: impl artifact is missing"]
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["draft-pr: impl artifact is unreadable"]
    # cd-76 r4 D5: the impl record must be this run's.
    run_id = str(session.get("pipeline_run_id") or "")
    recorded = str(payload.get("pipeline_run_id") or "") if isinstance(payload, dict) else ""
    if recorded != run_id:
        return [f"draft-pr: impl artifact is for run {recorded or 'none'}, this run is {run_id}; run impl_artifact.py (E2) from this worktree"]
    stat = payload.get("git_diff_stat") if isinstance(payload, dict) else ""
    if not str(stat or "").strip():
        return ["draft-pr: impl artifact git_diff_stat is empty"]
    return []


def _diff_errors(context_dir: Path, session: dict) -> list[str]:
    """E2's record of the diff, or with the deferrals pack off (no E2) the diff of the run's worktree (ADR-0007)."""
    from impl_artifact import MergeBaseError, _git_diff_stat
    from implement_gate import run_worktree
    from load_stages import stage_slugs
    from phase_ledger import resolve_base

    worktree = run_worktree(context_dir)
    if "impl-artifact" in stage_slugs(workspace=worktree):
        return _impl_diff_errors(context_dir, session)
    try:
        stat = _git_diff_stat(worktree, resolve_base(worktree, session))
    except MergeBaseError as exc:
        return [f"draft-pr: {exc}"]
    return [] if stat.strip() else ["draft-pr: no change since the merge base (or no base ref found)"]


def _worktree_run(context_dir: Path) -> tuple[dict, str] | None:
    """The session and run id of this worktree (cd-76 D4: never the root redirect), or None."""
    session = run_for(context_dir)
    run_id = str((session or {}).get("pipeline_run_id") or "")
    return (session, run_id) if session is not None and run_id else None


def run(context_dir: Path, *, check: bool = False) -> int:
    owned = _worktree_run(context_dir)
    if owned is None:
        return fail(SLUG, ["draft-pr: no Cadence run in this worktree; run E4 from the run's worktree"])
    session, run_id = owned
    breadcrumb = context_dir / "gates" / f"{SLUG}-{run_id}.json"
    if check:
        if breadcrumb.is_file():
            return ok(SLUG)
        return fail(
            SLUG,
            [f"draft-pr: {breadcrumb} not found; run draft_pr_gate.py (without --check) first"],
        )
    workspace = session.get("workspace")
    branch = current_branch(Path(workspace) if isinstance(workspace, str) and Path(workspace).is_dir() else None)
    record = draft_pr_record(context_dir, session, branch)
    if record is None:
        expected = draft_pr_record_path(context_dir, run_id).relative_to(context_dir)
        return fail(SLUG, [
            f"draft-pr: no draft-PR record for this run; write .context/{expected} after the PR exists "
            f"(run id: activate_session.py --status), with RUN_BY_COMMAND: commit-push-pr, PR: and Branch: {branch}"
        ])
    errors = require_artifact(
        context_dir,
        relative_globs=(str(record.relative_to(context_dir)),),
        markers=("RUN_BY_COMMAND: commit-push-pr",),
        label=SLUG,
    )
    if errors:
        return fail(SLUG, errors)
    text = record.read_text(encoding="utf-8", errors="replace")
    url = _PR_URL.search(text)
    if url is None:
        return fail(SLUG, [f"draft-pr: {record} has no PR URL"])
    # A record left by the previous ticket names a different branch.
    recorded = _BRANCH.search(text)
    found = recorded.group(1) if recorded else None
    if found is None or found != branch:
        return fail(SLUG, [f"draft-pr: record is for branch {found!r}, current branch is {branch!r}"])
    diff_errors = _diff_errors(context_dir, session)
    if diff_errors:
        return fail(SLUG, diff_errors)
    write_gate_breadcrumb(f"{SLUG}-{run_id}", {"pr_url": url.group(0), "branch": branch}, context_dir=context_dir)
    return ok(SLUG)


if __name__ == "__main__":
    raise SystemExit(cli(run))
