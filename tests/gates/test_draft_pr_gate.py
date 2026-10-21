"""E4 draft_pr_gate: pass only on a PR record for this branch and this run, with a non-empty impl diff."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import RUN_A, RUN_B, enable_all_packs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import draft_pr_gate  # noqa: E402
import gate_common  # noqa: E402
import impl_artifact  # noqa: E402
from artifact_requirements import draft_pr_number, draft_pr_record  # noqa: E402

MARKER = "RUN_BY_COMMAND: commit-push-pr\n"


def impl(ctx: Path, task: str, run: str, stat: str = "src/x.py | 2 ++") -> None:
    path = ctx / "implementation" / f"{task}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"task_id": task, "pipeline_run_id": run, "git_diff_stat": stat}), encoding="utf-8")


# --- draft_pr_gate: draft_pr_gate.run writes or checks the breadcrumb.

def test_draft_pr_gate_check_without_breadcrumb_fails(scripts_path, tmp_path):
    context = tmp_path / ".context"
    context.mkdir()
    code = draft_pr_gate.run(context, check=True)
    assert code == 1
    assert not (context / "gates" / "draft-pr.json").exists()
    assert context.is_dir()


def test_draft_pr_gate_writes_breadcrumb_passes(scripts_path, tmp_path, monkeypatch):
    monkeypatch.setattr(draft_pr_gate, "current_branch", lambda *_a: "feature/gh-28")  # Takes the worktree
    context = tmp_path / ".context"
    record = context / "prs" / "draft-pr.md"
    record.parent.mkdir(parents=True)
    record.write_text(
        "RUN_BY_COMMAND: commit-push-pr\n"
        "PR: https://github.com/jtouley/cadence-core/pull/28\n"
        "Branch: feature/gh-28\n",
        encoding="utf-8",
    )
    (context / "cadence-session.json").write_text(
        '{"active_skill": "cadence", "task_id": "gh-28", "pipeline_run_id": "run-1"}\n', encoding="utf-8"
    )
    (context / "implementation").mkdir()
    (context / "implementation" / "gh-28.json").write_text(  # The impl record names its run
        '{"pipeline_run_id": "run-1", "git_diff_stat": "src/x.py | 1 +"}\n', encoding="utf-8")
    code = draft_pr_gate.run(context, check=False)
    breadcrumb = context / "gates" / "draft-pr-run-1.json"  # Per-run breadcrumb
    body = breadcrumb.read_text(encoding="utf-8")
    assert code == 0
    assert "https://github.com/jtouley/cadence-core/pull/28" in body
    assert "feature/gh-28" in body


def test_draft_pr_gate_without_a_task_id_fails(scripts_path, tmp_path, monkeypatch):
    """Without a task id, the gate must not read another ticket's artifact."""
    monkeypatch.setattr(draft_pr_gate, "current_branch", lambda *_a: "feature/gh-1-x")  # Takes the worktree
    context = tmp_path / ".context"
    (context / "prs").mkdir(parents=True)
    (context / "prs" / "draft-pr.md").write_text(
        "RUN_BY_COMMAND: commit-push-pr\nhttps://github.com/o/r/pull/1\nBranch: feature/gh-1-x\n", encoding="utf-8"
    )
    (context / "cadence-session.json").write_text('{"active_skill": "cadence"}\n', encoding="utf-8")
    (context / "implementation").mkdir()
    (context / "implementation" / "gh-9.json").write_text('{"git_diff_stat": "a | 1 +"}\n', encoding="utf-8")
    assert draft_pr_gate.run(context, check=False) == 1


# --- draft_pr_gate_requires_pr_record: E4 passes only after commit-push-pr recorded a PR URL for the current branch.

def _record(ctx: Path, body: str) -> None:
    path = ctx / "prs" / "draft-pr.md"
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")


def _on_branch(monkeypatch, branch: str = "feature/gh-2-x") -> None:
    monkeypatch.setattr(draft_pr_gate, "current_branch", lambda *_a: branch)  # Takes the worktree


def test_draft_pr_gate_fails_without_record(tmp_path):
    assert draft_pr_gate.run(tmp_path) == 1


def test_draft_pr_gate_fails_without_pr_url(tmp_path, monkeypatch):
    _on_branch(monkeypatch)
    _record(tmp_path, MARKER + "Branch: feature/gh-2-x\n")
    assert draft_pr_gate.run(tmp_path) == 1


def test_draft_pr_gate_rejects_other_branch_record(tmp_path, monkeypatch):
    _on_branch(monkeypatch)
    _record(tmp_path, MARKER + "PR: https://github.com/o/r/pull/1\nBranch: feature/gh-1-x\n")
    assert draft_pr_gate.run(tmp_path) == 1


def _impl(ctx: Path, stat: str) -> None:
    impl(ctx, "gh-2", "run-1", stat)
    (ctx / "cadence-session.json").write_text(
        '{"active_skill": "cadence", "task_id": "gh-2", "pipeline_run_id": "run-1"}\n',
        encoding="utf-8",
    )


def test_draft_pr_gate_rejects_empty_impl_diff(tmp_path, monkeypatch):
    _on_branch(monkeypatch)
    _record(tmp_path, MARKER + "PR: https://github.com/o/r/pull/2\nBranch: feature/gh-2-x\n")
    _impl(tmp_path, "")
    assert draft_pr_gate.run(tmp_path) == 1


def test_draft_pr_gate_passes_for_this_branch(tmp_path, monkeypatch):
    _on_branch(monkeypatch)
    _record(tmp_path, MARKER + "PR: https://github.com/o/r/pull/2\nBranch: feature/gh-2-x\n")
    _impl(tmp_path, "src/x.py | 2 ++")
    assert draft_pr_gate.run(tmp_path) == 0
    assert (tmp_path / "gates" / "draft-pr-run-1.json").is_file()  # Per-run breadcrumb


# --- per_run_draft_pr_record: E2 and E4 per run. The draft-PR record is prs/draft-pr-<run_id>.md.
#
# E4 reads the record of the run of its own worktree (legacy prs/draft-pr.md only when
# its Branch: is the run's branch), checks that run's impl artifact, and writes and
# checks gates/draft-pr-<run_id>.json. E2 writes the impl artifact of its own run.

def record(ctx: Path, run: str | None, pr: int, branch: str | None) -> Path:
    name = f"draft-pr-{run}.md" if run else "draft-pr.md"
    path = ctx / "prs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = MARKER + f"PR: https://github.com/o/r/pull/{pr}\n" + (f"Branch: {branch}\n" if branch else "")
    path.write_text(body, encoding="utf-8")
    return path


def e4(monkeypatch, cwd: Path, ctx: Path, *, check: bool = False) -> tuple[int, list[str]]:
    enable_all_packs(cwd)  # E4 reads the E2 artifact with the deferrals pack on
    monkeypatch.chdir(cwd)
    seen: list[str] = []

    def fail(_slug: str, errors: list[str]) -> int:
        seen.extend(errors)
        return 1

    monkeypatch.setattr(draft_pr_gate, "fail", fail)
    return draft_pr_gate.run(ctx, check=check), seen


# --- draft_pr_record: one rule for E4 and R4 -------------------------------------------------------


def test_draft_pr_record_rules(tmp_path):
    session = {"pipeline_run_id": "r1"}
    assert draft_pr_record(tmp_path, session, "feature/a") is None
    legacy = record(tmp_path, None, 41, "feature/a")
    assert draft_pr_record(tmp_path, session, "feature/a") == legacy
    assert draft_pr_record(tmp_path, session, "feature/b") is None
    assert draft_pr_record(tmp_path, None, "feature/a") == legacy  # no session: legacy rule only
    assert draft_pr_record(tmp_path, session, None) is None  # detached HEAD
    own = record(tmp_path, "r1", 42, "feature/a")
    assert draft_pr_record(tmp_path, session, "feature/b") == own  # the run's own record wins
    assert draft_pr_number(own) == 42


def test_a_legacy_record_with_no_branch_line_gives_none(tmp_path):
    record(tmp_path, None, 41, None)
    assert draft_pr_record(tmp_path, {"pipeline_run_id": "r1"}, "feature/a") is None
    assert draft_pr_number(tmp_path / "prs" / "draft-pr.md") == 41


def test_one_definition_of_each_record_pattern():
    texts = [p.read_text(encoding="utf-8") for p in (ROOT / "scripts").glob("*.py")]
    assert sum(t.count("_PR_URL = ") for t in texts) == 1
    assert sum(t.count("_BRANCH = ") for t in texts) == 1


def test_current_branch_reads_the_given_worktree(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.primary)
    assert gate_common.current_branch(w.a) == "feature/wa"
    assert gate_common.current_branch() == "main"


# --- E4 per run --------------------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["ab", "ba"])
def test_e4_of_each_run_passes_with_its_own_record(two_worktree_runs, monkeypatch, order):
    w = two_worktree_runs
    record(w.ctx, w.run_a, 1, "feature/wa")
    record(w.ctx, w.run_b, 2, "feature/wb")
    impl(w.ctx, "cd-901", w.run_a)
    impl(w.ctx, "cd-902", w.run_b)
    trees = {"a": (w.a, w.run_a), "b": (w.b, w.run_b)}
    for key in order:
        tree, run = trees[key]
        code, errors = e4(monkeypatch, tree, w.ctx)
        assert code == 0, errors
        crumb = json.loads((w.ctx / "gates" / f"draft-pr-{run}.json").read_text())
        assert crumb["slug"] == f"draft-pr-{run}"
        assert crumb["pr_url"] == f"https://github.com/o/r/pull/{1 if key == 'a' else 2}"
        assert crumb["branch"] == ("feature/wa" if key == "a" else "feature/wb")
    assert not (w.ctx / "gates" / "draft-pr.json").exists()


def test_e4_passes_with_a_legacy_record_for_the_runs_branch(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    record(w.ctx, None, 7, "feature/wa")
    impl(w.ctx, "cd-901", w.run_a)
    code, errors = e4(monkeypatch, w.a, w.ctx)
    assert code == 0, errors


def test_e4_from_a_worktree_with_no_run_fails(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    assert e4(monkeypatch, w.primary, w.ctx) == (
        1, ["draft-pr: no Cadence run in this worktree; run E4 from the run's worktree"]
    )
    assert e4(monkeypatch, w.primary, w.ctx, check=True)[1] == [
        "draft-pr: no Cadence run in this worktree; run E4 from the run's worktree"
    ]


def test_e4_reads_a_record_with_invalid_utf8_without_a_crash(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    path = record(w.ctx, w.run_b, 2, "feature/wb")
    path.write_bytes(path.read_bytes() + b"\xff\xfe")
    impl(w.ctx, "cd-902", w.run_b)
    code, errors = e4(monkeypatch, w.b, w.ctx)
    assert code == 0, errors


@pytest.mark.parametrize(
    "has_record, impl_task, impl_run, want",
    [
        (False, "cd-902", RUN_B,
         f"draft-pr: no draft-PR record for this run; write .context/prs/draft-pr-{RUN_B}.md after the PR exists "
         "(run id: activate_session.py --status), with RUN_BY_COMMAND: commit-push-pr, PR: and Branch: feature/wb"),
        (True, "cd-901", RUN_A, "draft-pr: impl artifact is missing"),
        (True, "cd-902", RUN_A,
         f"draft-pr: impl artifact is for run {RUN_A}, this run is {RUN_B}; run impl_artifact.py (E2) from this worktree"),
    ],
    ids=["with_no_record_names_the_expected_path", "of_run_b_does_not_pass_on_run_a_impl",
         "fails_when_the_impl_record_names_another_run"],
)
def test_e4_of_run_b_fails(two_worktree_runs, monkeypatch, has_record, impl_task, impl_run, want):
    w = two_worktree_runs
    if has_record:
        record(w.ctx, w.run_b, 2, "feature/wb")
    impl(w.ctx, impl_task, impl_run)
    assert e4(monkeypatch, w.b, w.ctx) == (1, [want])


def test_e4_check_reads_only_the_runs_breadcrumb(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    (w.ctx / "gates").mkdir(parents=True, exist_ok=True)
    (w.ctx / "gates" / "draft-pr.json").write_text("{}", encoding="utf-8")
    assert e4(monkeypatch, w.b, w.ctx, check=True)[0] == 1
    (w.ctx / "gates" / f"draft-pr-{w.run_b}.json").write_text("{}", encoding="utf-8")
    assert e4(monkeypatch, w.b, w.ctx, check=True)[0] == 0
    assert e4(monkeypatch, w.a, w.ctx, check=True)[0] == 1


# --- E2 per run --------------------------------------------------------------------------------------


def test_e2_writes_the_impl_artifact_of_its_own_run(two_worktree_runs, monkeypatch):
    w = two_worktree_runs
    monkeypatch.chdir(w.a)  # the root redirect names run B
    monkeypatch.setattr(impl_artifact, "resolve_base", lambda *_a, **_k: "main")
    monkeypatch.setattr(impl_artifact, "_git_diff_stat", lambda *_a, **_k: " src/x.py | 2 ++")
    assert impl_artifact.run(w.ctx) == 0
    payload = json.loads((w.ctx / "implementation" / "cd-901.json").read_text())
    assert payload["pipeline_run_id"] == w.run_a
    assert not (w.ctx / "implementation" / "cd-902.json").exists()
    assert impl_artifact.run(w.ctx, check=True) == 0
    monkeypatch.chdir(w.b)
    assert impl_artifact.run(w.ctx, check=True) == 1
