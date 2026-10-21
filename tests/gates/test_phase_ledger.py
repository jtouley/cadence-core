"""E1 runs the plan's phases as the task list (ported from ship-feature stage4_phase_state).

A phase is done when a commit since the base names it: the subject starts with "phase N" after an optional conventional prefix.
E1 passes only when every plan phase is done, or, for a plan with no phases, when
at least one commit exists since the base. The stop guard names the next phase.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import commit_file, git, make_session, pushed_repo, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from artifact_requirements import write_entry_invariant_ok  # noqa: E402
from phase_ledger import assess, parse_phases, resolve_base  # noqa: E402

PLAN = """RUN_BY_COMMAND: plan-create
Pipeline-Run: run-1
**Ticket:** gh-1

## Phases

### Phase 1: parser
### Phase 2 — wiring
### Phase 3: docs
"""


def _commit(repo: Path, name: str, subject: str, when: str | None = None) -> None:
    env = {"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when} if when else {}
    commit_file(repo, subject, name, subject + "\n", **env)


def _phase_commits(repo: Path, *, same_time: bool = False, count: int = 3) -> None:
    """Commit phases 1..count, each at its own minute unless `same_time` (one shell)."""
    for n, name in list(((1, "parser"), (2, "wiring"), (3, "docs")))[:count]:
        _commit(repo, f"p{n}.py", f"feat: phase {n} {name}", f"2030-01-01T00:{0 if same_time else n:02d}:00+00:00")


@pytest.fixture
def run_repo(tmp_path):
    """A worktree on a feature branch, base origin/main, P9 passed, plan with 3 phases."""
    repo = pushed_repo(tmp_path / "repo")
    git(repo, "checkout", "-qb", "feature/gh-1-x")
    ctx = repo / ".context"
    make_session(ctx, workspace=str(repo), resume_slug="implement")
    (ctx / "plans").mkdir(parents=True)
    (ctx / "plans" / "gh-1_x.plan.md").write_text(PLAN, encoding="utf-8")
    write_entry_invariant_ok(ctx, pipeline_run_id="run-1", task_id="gh-1", written_at="t")
    return repo


def _implement_gate(repo: Path) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/implement_gate.py", "--check", cwd=repo)


def test_parse_phases_reads_numbered_headers():
    assert parse_phases(PLAN) == [(1, "parser"), (2, "wiring"), (3, "docs")]


def test_resolve_base_prefers_origin_main(run_repo):
    assert resolve_base(run_repo, {}) == "origin/main"


def test_assess_names_the_next_phase_after_phase_one(run_repo):
    _commit(run_repo, "p1.py", "feat: phase 1 parser")
    state = assess(run_repo / ".context", run_repo)
    assert state["completed"] == [1]
    assert state["next"] == (2, "wiring")


def test_implement_gate_fails_with_next_phase_while_phases_remain(run_repo):
    _commit(run_repo, "p1.py", "feat: phase 1 parser")
    proc = _implement_gate(run_repo)
    assert proc.returncode == 1
    out = proc.stdout + proc.stderr
    assert "phase 2" in out.lower() and "wiring" in out


def test_implement_gate_passes_when_every_phase_is_committed(run_repo):
    _phase_commits(run_repo)
    proc = _implement_gate(run_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_implement_gate_fails_when_phase_commits_share_one_timestamp(run_repo):
    """Nine phase commits with one timestamp were written in one shell, not phase by phase."""
    _phase_commits(run_repo, same_time=True)
    proc = _implement_gate(run_repo)
    assert proc.returncode == 1
    assert "one shell, not phased work" in proc.stdout + proc.stderr


def test_a_rebased_phased_branch_passes(run_repo):
    """A rebase gives every commit one committer time. The author time stays, so the branch passes."""
    _phase_commits(run_repo)
    git(run_repo, "rebase", "-q", "--force-rebase", "origin/main", GIT_COMMITTER_DATE="2031-01-01T00:00:00+00:00")
    times = git(run_repo, "log", "origin/main..HEAD", "--format=%ct").split()
    assert len(set(times)) == 1  # the trap is set
    proc = _implement_gate(run_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_plan_of_two_phases_may_share_one_timestamp(run_repo):
    plan = run_repo / ".context" / "plans" / "gh-1_x.plan.md"
    plan.write_text(PLAN.replace("### Phase 3: docs\n", ""), encoding="utf-8")
    _phase_commits(run_repo, same_time=True, count=2)
    proc = _implement_gate(run_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_next_phase_is_the_first_phase_with_no_commit(run_repo):
    from phase_ledger import next_phase

    plan = run_repo / ".context" / "plans" / "gh-1_x.plan.md"
    assert next_phase(plan, run_repo) == (1, "parser")
    _commit(run_repo, "p1.py", "feat: phase 1 parser")
    assert next_phase(plan, run_repo) == (2, "wiring")
    _commit(run_repo, "p2.py", "feat: phase 2 wiring")
    _commit(run_repo, "p3.py", "feat: phase 3 docs")
    assert next_phase(plan, run_repo) is None


def test_implement_gate_fails_when_the_plan_has_no_phase_headers(run_repo):
    """A plan with no `### Phase N:` headers gives E1 no task list. One commit must not clear it."""
    plan = run_repo / ".context" / "plans" / "gh-1_x.plan.md"
    plan.write_text(PLAN.split("## Phases")[0], encoding="utf-8")
    _commit(run_repo, "x.py", "feat: the change")
    proc = _implement_gate(run_repo)
    assert proc.returncode == 1
    assert "### Phase N:" in proc.stdout + proc.stderr


def test_implement_gate_without_a_plan_needs_a_commit_since_base(run_repo):
    (run_repo / ".context" / "plans" / "gh-1_x.plan.md").unlink()
    assert _implement_gate(run_repo).returncode == 1
    _commit(run_repo, "x.py", "feat: the change")
    assert _implement_gate(run_repo).returncode == 0


def test_a_revert_does_not_complete_a_phase(run_repo):
    _commit(run_repo, "p1.py", "feat: phase 1 parser")
    _commit(run_repo, "p2.py", "Revert \"feat: phase 2 wiring\"")
    assert assess(run_repo / ".context", run_repo)["next"] == (2, "wiring")


def test_resolve_base_skips_the_branch_the_run_is_on(tmp_path):
    """On `main` with no remote, `main..HEAD` is always empty: fall back to the root commit."""
    repo = tmp_path / "local"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _commit(repo, "a", "init")
    _commit(repo, "b", "feat: work")
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    assert resolve_base(repo, {}) == root


def _stop_message(repo: Path, run_hook) -> str:
    code, out, err = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)})
    assert code == 0, err
    return json.loads(out)["followup_message"]


def test_stop_guard_holds_e1_with_the_next_phase(run_repo, run_hook):
    """Three phases and one commit hold the stop with phase 2."""
    _commit(run_repo, "p1.py", "feat: phase 1 parser")
    message = _stop_message(run_repo, run_hook)
    assert "Continue E1: phase 2 (wiring). Commit it as 'phase 2: <subject>' before the next phase." in message


def test_stop_guard_moves_past_e1_when_each_phase_has_a_commit(run_repo, run_hook):
    _phase_commits(run_repo)
    message = _stop_message(run_repo, run_hook)
    assert "Continue E1" not in message and "E1 (implement)" not in message


def test_stop_guard_keeps_the_old_text_for_a_plan_with_no_phase_headings(run_repo, run_hook):
    plan = run_repo / ".context" / "plans" / "gh-1_x.plan.md"
    plan.write_text(PLAN.split("## Phases")[0], encoding="utf-8")
    _commit(run_repo, "x.py", "feat: the change")
    message = _stop_message(run_repo, run_hook)
    assert "Continue E1" not in message
    assert "E1 (implement)" in message and "### Phase N:" in message


def _impl_artifact(repo: Path) -> dict:
    proc = run_py("scripts/impl_artifact.py", cwd=repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads((repo / ".context" / "implementation" / "gh-1.json").read_text(encoding="utf-8"))


def test_impl_artifact_records_committed_work_against_the_base(run_repo):
    _commit(run_repo, "p1.py", "feat: phase 1 parser")
    assert subprocess.run(["git", "status", "--porcelain"], cwd=run_repo, capture_output=True, text=True).stdout == ""
    record = _impl_artifact(run_repo)
    assert "p1.py" in record["git_diff_stat"]
    assert record["base"] == "origin/main"


def test_impl_artifact_records_untracked_files(run_repo):
    (run_repo / "new_module.py").write_text("X = 1\n", encoding="utf-8")
    assert "new_module.py" in _impl_artifact(run_repo)["git_diff_stat"]


def test_resolve_base_falls_back_to_the_root_commit_in_a_local_only_repo(tmp_path):
    repo = tmp_path / "local"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feature/gh-7-toy")
    _commit(repo, "a", "init")
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    assert resolve_base(repo, {}) == root


def test_reverting_a_done_phase_reopens_it(run_repo):
    _commit(run_repo, "p1.py", "feat: phase 1 parser")
    _commit(run_repo, "p2.py", "feat: phase 2 wiring")
    _commit(run_repo, "p2b.py", 'Revert "feat: phase 2 wiring"')
    assert assess(run_repo / ".context", run_repo)["next"] == (2, "wiring")


def test_plan_create_header_example_matches_the_parser():
    """The format plan-create asks for is the format phase_ledger reads."""
    import re

    text = (ROOT / "commands" / "plan-create.md").read_text(encoding="utf-8")
    example = re.search(r"`(### Phase N: <title>)`", text).group(1).replace("N", "3").replace("<title>", "docs")
    assert parse_phases(example) == [(3, "docs")]


# Git writes `Reapply "..."` for a revert of a revert. Depth decides.
DEPTH = [
    ("feat: phase 2", True),
    ('Revert "feat: phase 2"', False),
    ('Revert "Revert "feat: phase 2""', True),
    ('Reapply "feat: phase 2"', True),
    ('Revert "Reapply "feat: phase 2""', False),
    ('Reapply "Reapply "feat: phase 2""', True),
    ("revert: phase 2", False),
    ('Revert "revert: phase 2"', True),
    ("phase 2: wiring", True),
    ("feat(scalar): Phase 2 gates", True),
    ("docs: a note on phase 2 risks", False),  # `phase 2` in the middle is not a phase commit
    ("chore: tidy before phase 2", False),
    ("[gh-12] phase 2: x", True),  # A leading tag
    ("fixup! feat: phase 2 wiring", True),
    ("squash! phase 2: wiring", True),
]


@pytest.mark.parametrize("subject, done", DEPTH)
def test_revert_depth_decides_the_phase_state(run_repo, subject, done):
    _commit(run_repo, "p.py", subject)
    assert (2 in assess(run_repo / ".context", run_repo)["completed"]) is done


def test_the_order_is_topological_not_by_date(run_repo):
    """The date order can list a commit before its revert; --topo-order cannot.

    C = phase 2 (late date). D = its revert (early date). E = a side commit on C (late).
    M merges E. By date, git log walks M, E, C, D, so oldest-first reads the revert
    before the phase and leaves phase 2 done.
    """
    late, later, early = "2030-01-01T00:00:00+00:00", "2030-06-01T00:00:00+00:00", "2029-01-01T00:00:00+00:00"

    def commit(name: str, subject: str, when: str) -> None:
        (run_repo / name).write_text(subject + "\n", encoding="utf-8")
        git(run_repo, "add", "-A")
        git(run_repo, "commit", "-qm", subject, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)

    commit("p2.py", "feat: phase 2 wiring", late)
    git(run_repo, "checkout", "-qb", "side")
    commit("side.txt", "chore: side", later)
    git(run_repo, "checkout", "-q", "feature/gh-1-x")
    git(run_repo, "revert", "--no-edit", "HEAD", GIT_COMMITTER_DATE=early, GIT_AUTHOR_DATE=early)
    git(run_repo, "merge", "-q", "--no-ff", "side", "-m", "merge side", GIT_COMMITTER_DATE=early, GIT_AUTHOR_DATE=early)
    by_date = git(run_repo, "log", "origin/main..HEAD", "--format=%s").splitlines()
    assert by_date.index("feat: phase 2 wiring") < by_date.index('Revert "feat: phase 2 wiring"')  # the trap is set
    assert 2 not in assess(run_repo / ".context", run_repo)["completed"]
