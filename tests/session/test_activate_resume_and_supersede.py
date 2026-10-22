"""activate_session.py --resume keeps the run id, and --supersede moves the old run's benchmarks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import CLAUDE_CHAT, activate, branch_repo, code_paths, hook_denied, shell_call, write_call

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import peer_attest  # noqa: E402
import session_store  # noqa: E402
from artifact_requirements import (check_plan_create_artifacts, plan_review_cycles,  # noqa: E402
                                   require_lean_delegation, run_scope_accept)
from prompt_shape import write_delegation_record  # noqa: E402


# --- resume_keeps_run_id: `--resume` keeps the run it resumes (G-D8).
#
# On run efcd8404, `--resume` minted a new pipeline_run_id. The plan, the reviews and
# the lean delegation records of the run then named an old id, and A5 failed. Now
# `--resume` keeps the id, `activated_at` and the orchestrator list of an active (or
# hard-stopped) run of the same task in this worktree. With nothing to resume, it
# starts a fresh run and says so.

FALLBACK = "--resume: no resumable run for cd-7 in this worktree; started a fresh run"


def _activate(repo: Path, *flags: str, agent: str | None = None) -> subprocess.CompletedProcess[str]:
    """The hook sees the command (with the agent's id), then activation runs."""
    command = "python3 scripts/activate_session.py " + " ".join(flags)
    shell_call(repo, command, agent=agent)
    return activate(repo, *flags, **CLAUDE_CHAT)


def _write_denied(repo: Path, agent: str) -> bool:
    target = repo / ".context" / "reviews" / "pr_cd-7.md"
    return hook_denied(write_call(repo, target, agent))


def _plan(repo: Path, run: str) -> None:
    path = repo / ".context" / "plans" / "x_cd-7.plan.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"RUN_BY_COMMAND: plan-create\nPipeline-Run: {run}\n# Plan\n### Phase 1: x\n", encoding="utf-8")


def _p1(repo: Path) -> list[str]:
    context = repo / ".context"
    return check_plan_create_artifacts(context, repo, accept=run_scope_accept(context, repo))


def test_resume_keeps_the_run_and_adds_the_new_orchestrator(tmp_path, monkeypatch):
    repo = branch_repo(tmp_path / "r")
    _activate(repo, "--task-id", "cd-7", "--fresh", agent="A")
    first = session_store.load_worktree_session(repo)
    run = first["pipeline_run_id"]
    _plan(repo, run)
    context = repo / ".context"
    review = context / "reviews" / "plan_x_cd-7.md"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("RUN_BY_COMMAND: plan-review\n", encoding="utf-8")
    peer_attest.record(context, run, "reviews/plan_x_cd-7.md", "P", peer_attest.sha256(review.read_bytes()))
    # A Claude Code run is marked lean_record_by_hook, so the record is the hook's.
    write_delegation_record(context, run_id=run, stage="plan-review", command="/plan-review x", source="PreToolUse")

    _activate(repo, "--task-id", "cd-7", "--resume", agent="B")
    resumed = session_store.load_worktree_session(repo)
    assert resumed["pipeline_run_id"] == run
    assert resumed["activated_at"] == first["activated_at"]
    assert resumed["orchestrator_agent_ids"] == ["A", "B"]
    assert resumed["fresh_run"] is False and resumed["resumed_at"]
    assert _write_denied(repo, "A") and _write_denied(repo, "B")
    assert not _write_denied(repo, "C")
    assert _p1(repo) == []
    monkeypatch.chdir(repo)
    assert plan_review_cycles(context) == 1
    assert require_lean_delegation(context, "plan-review") == []


def test_resume_after_a_hard_stop_keeps_the_run_and_drops_the_stop(tmp_path):
    repo = branch_repo(tmp_path / "r")
    _activate(repo, "--task-id", "cd-7", "--fresh", agent="A")
    run = session_store.load_worktree_session(repo)["pipeline_run_id"]
    _activate(repo, "--hard-stop", "retries", "--reason", "cap")
    assert session_store.load_worktree_session(repo)["hard_stop"]
    _activate(repo, "--task-id", "cd-7", "--resume", agent="A")
    resumed = session_store.load_worktree_session(repo)
    assert resumed["pipeline_run_id"] == run
    assert "hard_stop" not in resumed
    assert resumed["orchestrator_agent_ids"] == ["A"]


def _fallback(repo: Path, run: str | None) -> None:
    proc = _activate(repo, "--task-id", "cd-7", "--resume", agent="B")
    session = session_store.load_worktree_session(repo)
    assert FALLBACK in proc.stderr
    assert session["resume_fallback"] == "fresh"
    assert session["fresh_run"] is True
    assert session["pipeline_run_id"] != run
    assert session["orchestrator_agent_ids"] == ["B"]


def test_resume_with_no_session_starts_fresh(tmp_path):
    _fallback(branch_repo(tmp_path / "r"), None)


def test_resume_of_a_completed_run_starts_fresh(tmp_path):
    repo = branch_repo(tmp_path / "r")
    _activate(repo, "--task-id", "cd-7", "--fresh", agent="A")
    run = session_store.load_worktree_session(repo)["pipeline_run_id"]
    _activate(repo, "--hard-stop", "not-needed", "--reason", "done")
    _activate(repo, "--deactivate")
    _fallback(repo, run)


def test_resume_of_another_task_starts_fresh(tmp_path):
    repo = branch_repo(tmp_path / "r")
    _activate(repo, "--task-id", "cd-8", "--fresh", agent="A")
    _fallback(repo, session_store.load_worktree_session(repo)["pipeline_run_id"])


def test_fresh_starts_a_new_run_and_a_new_list(tmp_path):
    repo = branch_repo(tmp_path / "r")
    _activate(repo, "--task-id", "cd-7", "--fresh", agent="A")
    old = session_store.load_worktree_session(repo)["pipeline_run_id"]
    _plan(repo, old)
    _activate(repo, "--task-id", "cd-7", "--fresh", agent="B")
    session = session_store.load_worktree_session(repo)
    assert session["pipeline_run_id"] != old
    assert session["orchestrator_agent_ids"] == ["B"]
    assert _p1(repo)


# --- supersede_moves_benchmarks: --supersede moves the ticket files in .context/benchmarks/.

RUN_ID = "11111111-2222-3333-4444-555555555555"
BRANCH = "claude/gh-107-supersede-benchmarks"


def _bench(context: Path, *names: str) -> Path:
    bench = context / "benchmarks"
    bench.mkdir(parents=True, exist_ok=True)
    for name in names:
        (bench / name).write_text("{}\n", encoding="utf-8")
    return bench


def _supersede(context: Path, task_id: str, branch: str) -> list[str]:
    from activate_session import ticket_tokens_for
    from pipeline_run_binding import supersede_ticket_artifacts

    return supersede_ticket_artifacts(
        context,
        ticket_tokens=ticket_tokens_for(task_id, branch),
        run_id=RUN_ID,
        task_id=task_id,
    )


def _manifest_paths(context: Path) -> list[str]:
    data = json.loads((context / "runs" / RUN_ID / "superseded.json").read_text(encoding="utf-8"))
    return data["paths"]


@pytest.fixture(autouse=True)
def _scripts(scripts_path):
    return scripts_path


RUN1 = ("gh-107-a_plan.json", "gh-107-a_code.json", "gh-107-a_benchmark.json")


def test_supersede_moves_ticket_benchmark_files(tmp_path):
    # Arrange
    context = tmp_path / ".context"
    bench = _bench(context, *RUN1)
    (bench / "gh-107-old").mkdir()

    # Act
    _supersede(context, "gh-107", BRANCH)

    # Assert
    dest = context / "superseded" / RUN_ID / "benchmarks"
    for name in RUN1:
        assert not (bench / name).exists()
        assert (dest / name).is_file()
    assert sorted(_manifest_paths(context)) == sorted(f"benchmarks/{n}" for n in RUN1)
    assert (bench / "gh-107-old").is_dir(), "a subdirectory of benchmarks/ must not move"


def test_new_p7_file_is_the_only_one_the_gates_glob(tmp_path):
    # Arrange
    from test_benchmark_gate import _MODES

    context = tmp_path / ".context"
    _bench(context, *RUN1)
    _supersede(context, "gh-107", BRANCH)

    # Act
    _bench(context, "gh-107_plan.json")
    plan_hits = [p.name for g in _MODES["plan"]["globs"] for p in context.glob(g)]
    code_hits = [p.name for g in _MODES["code"]["globs"] for p in context.glob(g)]

    # Assert
    assert plan_hits == ["gh-107_plan.json"]
    assert code_hits == []


def test_supersede_keeps_neighbor_ticket_benchmarks(tmp_path):
    # Arrange
    context = tmp_path / ".context"
    names = ("gh-1070_plan.json", "domain_plan.json", "main_plan.json")
    bench = _bench(context, *names)

    # Act
    _supersede(context, "gh-107", "main")

    # Assert
    for name in names:
        assert (bench / name).is_file(), name


def test_existing_dirs_keep_substring_rule(tmp_path):
    """Characterization test. The four current directories keep the
    substring rule. F1 changes this assertion."""
    # Arrange
    context = tmp_path / ".context"
    reviews = context / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "gh-1070_review.md").write_text("x\n", encoding="utf-8")

    # Act
    _supersede(context, "gh-107", "main")

    # Assert
    assert not (reviews / "gh-1070_review.md").exists()
    assert (context / "superseded" / RUN_ID / "reviews" / "gh-1070_review.md").is_file()


@pytest.mark.parametrize(
    ("token", "name"),
    [
        ("107", "gh-107_plan.json"),
        ("107", "gh-1070_plan.json"),
        ("main", "domain_plan.json"),
        ("gh-107", "gh-107-a_code.json"),
        ("gh_107", "GH-107_benchmark.json"),
        ("gh-107", "gh_107_plan.json"),
        (BRANCH, "claude-gh-107-supersede-benchmarks_plan.json"),
        ("gh-107-supersede-benchmarks", "gh-107-supersede-benchmarks_plan.json"),
    ],
)
def test_boundary_helper_agrees_with_ticket_token_re(token, name):
    # Arrange
    from artifact_requirements import ticket_norm, ticket_token_re
    from pipeline_run_binding import _token_at_boundary

    # Act
    expected = bool(ticket_token_re(token).search(ticket_norm(name)))

    # Assert
    assert _token_at_boundary(token, name) is expected


@pytest.mark.parametrize(
    ("branch", "others"),
    [
        ("plan", ("gh-108_plan.json", "domain_plan.json")),
        ("code", ("gh-108_code.json",)),
        ("benchmark", ("gh-108_benchmark.json",)),
    ],
)
def test_word_branch_token_keeps_other_ticket_benchmarks(tmp_path, branch, others):
    # Arrange
    context = tmp_path / ".context"
    bench = _bench(context, "gh-107_plan.json", *others)

    # Act
    _supersede(context, "gh-107", branch)

    # Assert
    for name in others:
        assert (bench / name).is_file(), name
    assert not (bench / "gh-107_plan.json").exists()
    assert (context / "superseded" / RUN_ID / "benchmarks" / "gh-107_plan.json").is_file()


def test_task_id_without_digit_moves_no_benchmark(tmp_path):
    # Arrange
    context = tmp_path / ".context"
    bench = _bench(context, "fix-foo_plan.json")

    # Act
    _supersede(context, "fix-foo", "main")

    # Assert
    assert (bench / "fix-foo_plan.json").is_file()


def test_collision_writes_flattened_name_and_manifest_keeps_rel(tmp_path):
    # Arrange
    context = tmp_path / ".context"
    _bench(context, "gh-107_plan.json")
    taken = context / "superseded" / RUN_ID / "benchmarks" / "gh-107_plan.json"
    taken.parent.mkdir(parents=True)
    taken.write_text("earlier\n", encoding="utf-8")

    # Act
    _supersede(context, "gh-107", BRANCH)

    # Assert
    assert (context / "superseded" / RUN_ID / "benchmarks__gh-107_plan.json").is_file()
    assert taken.read_text(encoding="utf-8") == "earlier\n"
    assert _manifest_paths(context) == ["benchmarks/gh-107_plan.json"]
