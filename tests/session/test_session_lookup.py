"""Which session a reader finds: run_for gives one worktree's run, and a completed by-worktree file does not hide an active one."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import code_paths, git_repo, save_worktree_session

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import session_store  # noqa: E402
from pipeline_run_binding import load_session, run_for  # noqa: E402


# --- run_for_resolves_worktree_run: run_for gives the run of one worktree and never follows the root redirect.

def run_id(session):
    return session["pipeline_run_id"] if session else None


def test_cwd_gives_the_worktree_run_whatever_run_activated_last(two_worktree_runs):
    w = two_worktree_runs
    assert run_id(load_session(w.ctx)) == w.run_b  # the root redirect names run B
    assert run_id(run_for(w.ctx, cwd=w.a)) == w.run_a
    assert run_id(run_for(w.ctx, cwd=w.b)) == w.run_b
    save_worktree_session(w.a, pipeline_run_id=w.run_a, task_id="cd-901")  # redirect now at A
    assert run_id(run_for(w.ctx, cwd=w.b)) == w.run_b


def test_target_gives_the_run_of_the_worktree_that_holds_it(two_worktree_runs):
    w = two_worktree_runs
    (w.b / "src").mkdir()
    (w.b / "src" / "x.py").write_text("x = 1\n", encoding="utf-8")
    assert run_id(run_for(w.ctx, cwd=w.primary, target=w.b / "src" / "x.py")) == w.run_b
    assert run_id(run_for(w.ctx, cwd=w.primary, target=w.b / "new" / "deeper" / "y.py")) == w.run_b
    assert run_id(run_for(w.ctx, cwd=w.b, target=w.a / "README")) == w.run_a


def test_primary_checkout_with_no_run_gives_none(two_worktree_runs):
    w = two_worktree_runs
    assert run_for(w.ctx, cwd=w.primary) is None
    assert run_for(w.ctx, target=w.primary / "src" / "x.py") is None


def test_primary_checkout_with_its_own_run_gives_that_run(two_worktree_runs):
    w = two_worktree_runs
    save_worktree_session(w.primary, pipeline_run_id="cccccccc-0000-4000-8000-00000000000c", task_id="cd-903")
    assert run_id(run_for(w.ctx, target=w.primary / "README")) == "cccccccc-0000-4000-8000-00000000000c"


def test_target_outside_any_git_repository_gives_none(two_worktree_runs, tmp_path):
    w = two_worktree_runs
    outside = tmp_path / "plain"
    outside.mkdir()
    assert run_for(w.ctx, target=outside / "f.txt") is None


def test_target_with_no_existing_parent_gives_none(two_worktree_runs):
    assert run_for(two_worktree_runs.ctx, target=Path("/nonexistent-cd76/a/b/c.py")) is None


def test_a_single_repo_run_is_found_from_its_cwd(tmp_path):
    repo = git_repo(tmp_path / "r")
    save_worktree_session(repo, pipeline_run_id="dddddddd-0000-4000-8000-00000000000d")
    assert run_id(run_for(repo / ".context", cwd=repo)) == "dddddddd-0000-4000-8000-00000000000d"


# --- load_session_skips_completed_worktree: Completed by-worktree sessions must not hide a newer active singleton.

def test_load_session_completed_worktree_falls_through_to_active_legacy(tmp_path, monkeypatch):
    """A finished by-worktree file must not mask cadence-session.json."""
    ctx = tmp_path / ".context"
    wt = ctx / "sessions" / "by-worktree" / "dead.json"
    wt.parent.mkdir(parents=True)
    wt.write_text(
        json.dumps(
            {
                "status": "completed",
                "active_skill": None,
                "skill": "cadence",
                "pipeline_run_id": "old",
            }
        ),
        encoding="utf-8",
    )
    (ctx / "cadence-session.json").write_text(
        json.dumps(
            {
                "active_skill": "cadence",
                "workspace": str(tmp_path),
                "pipeline_run_id": "new",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(session_store, "context_dir", lambda cwd=None: ctx)
    monkeypatch.setattr(session_store, "session_path", lambda cwd=None: wt)
    monkeypatch.setattr(session_store, "_resolve_toplevel", lambda cwd=None: tmp_path)

    got = session_store.load_session(tmp_path)

    assert got is not None
    assert got["pipeline_run_id"] == "new"
    assert got["active_skill"] == "cadence"


# --- the_root_redirect_stays_in_its_worktree: a finished worktree never reads the run of another worktree.

def test_a_finished_worktree_does_not_see_the_run_the_redirect_names(two_worktree_runs):
    """After run A ends and run B starts, the root redirect names B. From worktree A, load_session gives
    no session: B's run is not A's, whatever the redirect says. (The predecessor's concurrency test.)"""
    session_store.deactivate_session(two_worktree_runs.a)
    save_worktree_session(two_worktree_runs.b, pipeline_run_id=two_worktree_runs.run_b, task_id="cd-902")
    assert session_store.load_session(two_worktree_runs.a) is None
    assert run_id(session_store.load_session(two_worktree_runs.b)) == two_worktree_runs.run_b


def test_deactivate_in_a_worktree_with_no_run_does_not_complete_another_worktrees_run(two_worktree_runs):
    """deactivate_session on the primary checkout, which has no run, writes no copy of run B as completed."""
    done = session_store.deactivate_session(two_worktree_runs.primary)
    assert done.get("pipeline_run_id") is None
    assert run_id(session_store.load_session(two_worktree_runs.b)) == two_worktree_runs.run_b
