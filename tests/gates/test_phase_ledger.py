"""E1 runs the plan's phases as the task list (ported from ship-feature stage4_phase_state).

A phase is done when a commit since the base names it: the subject starts with "phase N" after an optional conventional prefix.
A `phase N: red ...` commit holds the failing test of the phase; it does not finish the phase.
E1 passes only when every plan phase is done, or, for a plan with no phases, when
at least one commit exists since the base. The stop guard names the next phase.

Each phase also has red-before-green evidence. The check of the phase fails at its red
commit and passes at its green commit, in a clean checkout of each. The one-second author-time
check went away: commits one second apart from a finished tree passed it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import commit_file, git, make_session, passing_acceptance, pushed_repo, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from artifact_requirements import write_entry_invariant_ok  # noqa: E402
import phase_evidence  # noqa: E402
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


NAMES = ((1, "parser"), (2, "wiring"), (3, "docs"))


def _phase_commits(repo: Path, *, count: int = 3, evidence: bool = True, same_time: bool = False) -> None:
    """For each phase: a red commit with a check that fails, a green commit that makes it pass, the record."""
    for n, name in NAMES[:count]:
        when = f"2030-01-01T00:{0 if same_time else n:02d}:00+00:00"
        _commit(repo, f"check_p{n}.py", f"test: phase {n}: red {name}", when)
        (repo / f"check_p{n}.py").write_text(f"import p{n}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--amend", "--no-edit", GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
        commit_file(repo, f"feat: phase {n} {name}", f"p{n}.py", "", GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
        if evidence:
            assert phase_evidence.record(repo / ".context", n, run=f"python3 check_p{n}.py") == []


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
    passing_acceptance(ctx, repo)  # : P9 pins the checks, and E1 runs them
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


def test_phase_commits_with_one_timestamp_pass_when_each_red_commit_fails(run_repo):
    """The time of a commit proves nothing. The evidence is what the check does at each commit."""
    _phase_commits(run_repo, same_time=True)
    proc = _implement_gate(run_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_phase_with_no_check_record_fails(run_repo):
    _phase_commits(run_repo, evidence=False)
    proc = _implement_gate(run_repo)
    assert proc.returncode == 1 and "phase 1" in proc.stdout + proc.stderr and "phase_evidence.py" in proc.stdout + proc.stderr


def test_a_phase_with_no_red_commit_fails(run_repo):
    for n, name in NAMES:
        _commit(run_repo, f"p{n}.py", f"feat: phase {n} {name}")
        (run_repo / f"check_p{n}.py").write_text(f"import p{n}\n", encoding="utf-8")
        git(run_repo, "add", "-A")
        git(run_repo, "commit", "-q", "--amend", "--no-edit")
        assert phase_evidence.record(run_repo / ".context", n, run=f"python3 check_p{n}.py") == []
    out = _implement_gate(run_repo)
    assert out.returncode == 1 and "red" in out.stdout + out.stderr


def test_a_red_commit_from_a_finished_tree_fails(run_repo):
    """The shape of the benchmark run: the code was written first, then split into phase commits."""
    for n, _name in NAMES:
        (run_repo / f"p{n}.py").write_text("", encoding="utf-8")
    for n, name in NAMES:
        (run_repo / f"check_p{n}.py").write_text(f"import p{n}\n", encoding="utf-8")
        git(run_repo, "add", "-A")
        git(run_repo, "commit", "-q", "-m", f"test: phase {n}: red {name}")
        _commit(run_repo, f"n{n}.txt", f"feat: phase {n} {name}")
        assert phase_evidence.record(run_repo / ".context", n, run=f"python3 check_p{n}.py") == []
    proc = _implement_gate(run_repo)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1 and "passes at its red commit" in out


def test_a_green_commit_where_the_check_fails_fails(run_repo):
    _phase_commits(run_repo)
    (run_repo / "p3.py").unlink()
    _commit(run_repo, "x.txt", "feat: phase 3 docs again")
    proc = _implement_gate(run_repo)
    assert proc.returncode == 1 and "fails at its green commit" in proc.stdout + proc.stderr


def test_a_phase_with_no_code_records_a_reason(run_repo):
    _phase_commits(run_repo, count=2)
    _commit(run_repo, "README.md", "docs: phase 3 docs")
    assert phase_evidence.record(run_repo / ".context", 3, none="the phase changes the README only") == []
    proc = _implement_gate(run_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "phase 3 has no check: the phase changes the README only" in proc.stdout + proc.stderr


def test_a_phase_that_pins_working_behavior_records_a_reason(run_repo):
    """Live run y1 stopped at the E1 gate: its last phase added a test that pins the totals output, which
    already worked, so the phase could not be red. The gate names the --none record, which clears it."""
    _phase_commits(run_repo, count=2)
    commit_file(run_repo, "test: phase 3 pin the totals output", "check_p3.py", "import p1\n")
    assert phase_evidence.record(run_repo / ".context", 3, run="python3 check_p3.py") == []
    proc = _implement_gate(run_repo)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1 and "no red commit" in out and "--phase 3 --none" in out
    assert phase_evidence.record(run_repo / ".context", 3, none="pins the totals output; no code") == []
    assert _implement_gate(run_repo).returncode == 0


@pytest.mark.parametrize("kwargs, needle", [
    ({"run": "true"}, "no-op"), ({"none": ""}, "reason"), ({}, "give --run"),
])
def test_a_record_that_proves_nothing_is_refused(run_repo, kwargs, needle):
    errors = phase_evidence.record(run_repo / ".context", 1, **kwargs)
    assert errors and needle in errors[0]


def test_a_rebased_phased_branch_passes(run_repo):
    """The evidence names phases, not shas, so a rebase keeps it."""
    _phase_commits(run_repo)
    git(run_repo, "rebase", "-q", "--force-rebase", "origin/main", GIT_COMMITTER_DATE="2031-01-01T00:00:00+00:00")
    proc = _implement_gate(run_repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_red_commit_does_not_finish_its_phase(run_repo):
    _commit(run_repo, "check_p1.py", "test: phase 1: red parser")
    assert assess(run_repo / ".context", run_repo)["completed"] == []


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
