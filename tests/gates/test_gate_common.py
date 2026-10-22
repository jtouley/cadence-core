"""gate_common and the gates built on it: breadcrumbs, the artifact table, the shim CLI, and the implement, plan-update and R1 gates."""

from __future__ import annotations

import fnmatch
import importlib
import json
import re
from pathlib import Path

import pytest

from conftest import branch_repo, code_paths, commit_file, git, git_repo, head_sha, make_session, passing_acceptance

ROOT = Path(__file__).resolve().parents[2]
code_paths()
from artifact_requirements import DEFERRAL_REGISTER, REVIEW_RIGOR_SECTIONS, require_active_session  # noqa: E402
from gate_common import ARTIFACT_GATES, run_artifact_gate, write_gate_breadcrumb  # noqa: E402
from skill_gate import CadenceGate  # noqa: E402
from test_benchmark_gate import _MODES  # noqa: E402


# --- gate_common: Shared gate runner: breadcrumbs, artifact table, session helper.

def test_cadence_gate_constructor_takes_workspace_only(tmp_path):
    CadenceGate(tmp_path)
    with pytest.raises(TypeError):
        CadenceGate(skill_name="cadence", workspace=tmp_path)


def test_guard_skill_gate_does_not_pass_skill_name():
    text = (ROOT / "hooks" / "guard_skill_gate.py").read_text(encoding="utf-8")
    assert "CadenceGate(skill_name=" not in text
    assert "CadenceGate(workspace" in text


def test_write_gate_breadcrumb_uses_passed_context_dir(tmp_path):
    path = write_gate_breadcrumb("validate-gate", context_dir=tmp_path)
    assert path == tmp_path / "gates" / "validate-gate.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["slug"] == "validate-gate"


def test_require_active_session_missing_file_fails(tmp_path):
    errors = require_active_session(tmp_path)
    assert errors
    assert "cadence-session.json" in errors[0]


def test_require_active_session_wrong_skill_fails(tmp_path):
    (tmp_path / "cadence-session.json").write_text(
        json.dumps({"active_skill": "other"}), encoding="utf-8"
    )
    errors = require_active_session(tmp_path)
    assert errors
    assert "active_skill" in errors[0]


@pytest.mark.parametrize("slug", list(ARTIFACT_GATES))
def test_run_artifact_gate_missing_artifact_fails(tmp_path, slug):
    code = run_artifact_gate(slug, tmp_path, check=True)
    assert code == 1


def test_run_artifact_gate_validate_passes_with_vibetest_and_lean_record(tmp_path):
    (tmp_path / "cadence-session.json").write_text(
        json.dumps(
            {"active_skill": "cadence", "pipeline_run_id": "run-1", "task_id": "t1"}
        ),
        encoding="utf-8",
    )
    from prompt_shape import write_delegation_record

    write_delegation_record(
        tmp_path,
        run_id="run-1",
        stage="vibe-test",
        command="/vibe-test docs/spec.md",
        source="preToolUse",
    )
    vibe = tmp_path / "vibetest"
    vibe.mkdir()
    (vibe / "t.md").write_text(
        "RUN_BY_SKILL: vibe-test\n"
        "**Tracker:** github-issues\n"
        "**Ticket:** TICKET_SKIPPED (github-issues gate fixture)\n",
        encoding="utf-8",
    )
    assert run_artifact_gate("validate-gate", tmp_path, check=True) == 0
    assert not (tmp_path / "gates" / "validate-gate.json").exists()
    assert run_artifact_gate("validate-gate", tmp_path, check=False) == 0
    assert (tmp_path / "gates" / "validate-gate.json").is_file()


# --- shim_gates_wire_cli: Shim gate modules import gate_common.cli — no per-shim behavior suite.

@pytest.mark.parametrize(
    "modname",
    [
        "plan_create_gate",
        "plan_review_gate",
        "validate_gate",
        "adversarial_review_gate",
        "pr_review_local_gate",
    ],
)
def test_shim_gate_module_imports_cli(scripts_path, modname):
    from gate_common import cli

    mod = importlib.import_module(modname)
    assert hasattr(mod, "cli")
    assert callable(mod.cli)
    assert mod.cli is cli


# --- peer_deliverables_match_gate_globs: Every bundled peer's documented deliverable must be a path its gate accepts.
#
# A peer that follows its own doc must be able to clear its gate. Placeholders
# like {slug} are filled with a sample ticket before matching.

# doc -> the .context-relative globs the pipeline checks for that peer
PEER_GLOBS = {
    "skills/vibe-test/SKILL.md": ARTIFACT_GATES["validate-gate"]["globs"],
    "commands/plan-create.md": ("plans/*.plan.md",),
    "commands/plan-review.md": ARTIFACT_GATES["plan-review-gate"]["globs"],
    "skills/de-adversarial-reviewer/SKILL.md": ARTIFACT_GATES["adversarial-review-gate"]["globs"],
    "skills/test-benchmark/SKILL.md": (*_MODES["plan"]["globs"], *_MODES["code"]["globs"]),
    "commands/pr-review-local.md": ARTIFACT_GATES["pr-review-local-gate"]["globs"],
    "commands/commit-push-pr.md": ("prs/draft-pr-*.md", "prs/draft-pr.md"),  # Per-run record, legacy
    "references/deferral-framework.md": DEFERRAL_REGISTER["relative_globs"],
}
_PATH = re.compile(r"`\.context/([^`\s]+\.(?:md|json))`")


def _documented(doc: str) -> list[str]:
    text = (ROOT / doc).read_text(encoding="utf-8")
    return [re.sub(r"\{[^}]+\}|<[^>]+>", "cd-1", m) for m in _PATH.findall(text)]


@pytest.mark.parametrize("doc", sorted(PEER_GLOBS))
def test_peer_deliverable_matches_gate_glob(doc):
    paths = _documented(doc)
    assert paths, f"{doc} documents no .context deliverable"
    matched = [p for p in paths if any(fnmatch.fnmatch(p, g) for g in PEER_GLOBS[doc])]
    assert matched, f"{doc} writes {paths} but its gate reads {PEER_GLOBS[doc]}"


# --- implement_gate: implement_gate.run requires session plus entry-invariant.ok.

def test_implement_gate_missing_session_fails(scripts_path, tmp_path):
    from implement_gate import run

    context = tmp_path / ".context"
    context.mkdir()
    code = run(context)
    assert code == 1
    assert not (context / "cadence-session.json").exists()
    assert not (context / "gates" / "implement-gate.json").exists()


def test_implement_gate_missing_entry_invariant_fails(scripts_path, tmp_path):
    from implement_gate import run

    context = tmp_path / ".context"
    make_session(context)
    code = run(context)
    assert code == 1
    assert (context / "cadence-session.json").is_file()
    assert not (context / "gates" / "entry-invariant.ok").exists()


def _repo_one_commit_ahead(tmp_path):
    """A repo of its own. The gate must not read this checkout: on main, HEAD is the base."""
    repo = branch_repo(tmp_path / "repo", "main")
    git(repo, "checkout", "-q", "-b", "feature/gh-1-x")
    commit_file(repo, "feat: the change", "x.py", "X = 1\n")
    return repo


def test_implement_gate_session_and_ok_file_passes(scripts_path, tmp_path, monkeypatch):
    from implement_gate import run

    # An explicit context, and a cwd that is neither that context's checkout nor a repo:
    # the gate must read the session's workspace, not the cwd.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    context = tmp_path / "ctx" / ".context"
    repo = _repo_one_commit_ahead(tmp_path)
    make_session(context, workspace=str(repo), base="main")
    ok_path = context / "gates" / "entry-invariant-run-1.ok"  # The run's own marker
    ok_path.parent.mkdir(parents=True)
    ok_path.write_text("ok\n", encoding="utf-8")
    passing_acceptance(context, repo)  # P9 pins the checks, and E1 runs them
    code = run(context)
    breadcrumb = context / "gates" / "implement-gate.json"
    assert code == 0
    assert ok_path.is_file()
    assert breadcrumb.is_file()


# --- plan_update_gate: plan_update_gate.run requires a plan-update marker when review asks changes.

def test_plan_update_gate_ready_with_changes_missing_marker_fails(
    scripts_path, tmp_path, monkeypatch
):
    from plan_update_gate import run

    monkeypatch.chdir(tmp_path)
    context = tmp_path / ".context"
    reviews = context / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "plan_floor.md").write_text("READY WITH CHANGES\n", encoding="utf-8")
    code = run(context)
    assert code == 1
    assert "READY WITH CHANGES" in (reviews / "plan_floor.md").read_text(encoding="utf-8")
    assert not (context / "gates" / "plan-update-gate.json").exists()


def test_plan_update_gate_no_review_phrases_passes(scripts_path, tmp_path):
    from plan_update_gate import run

    context = tmp_path / ".context"
    reviews = context / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "plan_floor.md").write_text("READY TO EXECUTE\n", encoding="utf-8")
    code = run(context)
    breadcrumb = context / "gates" / "plan-update-gate.json"
    assert code == 0
    assert breadcrumb.is_file()
    assert "no_on_review_changes_evidence" in breadcrumb.read_text(encoding="utf-8")


# --- pr_review_local_gate_requires_rigor_sections: R1 review must carry the review-rigor sections, and the doc must list the same ones.

def _review(tmp_path: Path, sections: tuple[str, ...]) -> None:
    path = tmp_path / "prs" / "reviews" / "PR_REVIEW_#2.md"
    path.parent.mkdir(parents=True)
    body = "RUN_BY_COMMAND: pr-review-local\n\n" + "".join(f"## {s}\n\nNone.\n\n" for s in sections)
    path.write_text(body, encoding="utf-8")


def _delegated(tmp_path: Path) -> None:
    """A run with the lean R1 delegation record, so only the sections decide the gate."""
    from prompt_shape import write_delegation_record

    make_session(tmp_path)
    write_delegation_record(tmp_path, run_id="run-1", stage="pr-review-local", command="/pr-review-local 2", source="test")


def test_review_without_rigor_sections_fails(tmp_path):
    _review(tmp_path, ())
    assert run_artifact_gate("pr-review-local-gate", tmp_path, check=True) == 1


def test_review_missing_one_section_fails(tmp_path):
    _review(tmp_path, REVIEW_RIGOR_SECTIONS[:-1])
    assert run_artifact_gate("pr-review-local-gate", tmp_path, check=True) == 1


def test_review_without_reuse_and_extension_fails(tmp_path, capsys):
    """R1 records the reuse, the duplication and the extension point of the change."""
    assert "Reuse and extension" in REVIEW_RIGOR_SECTIONS
    _delegated(tmp_path)
    _review(tmp_path, tuple(s for s in REVIEW_RIGOR_SECTIONS if s != "Reuse and extension"))
    assert run_artifact_gate("pr-review-local-gate", tmp_path, check=True) == 1
    assert "missing sections ['Reuse and extension']" in capsys.readouterr().err


# --- R4 is off by default, so R1 checks that the newest review cites HEAD.

def _reviewed(tmp_path: Path, sha: str | None) -> tuple[Path, Path, str]:
    """(context, worktree, HEAD): a run whose worktree is a git repo, and a full R1 review of `sha`."""
    worktree = git_repo(tmp_path / "w")
    head = commit_file(worktree, "base")
    context = tmp_path / "ctx"
    _delegated(context)
    make_session(context, workspace=str(worktree))
    _review(context, REVIEW_RIGOR_SECTIONS)
    if sha is not None:
        path = context / "prs" / "reviews" / "PR_REVIEW_#2.md"
        path.write_text(f"Last reviewed commit: {head if sha == 'HEAD' else sha}\n" + path.read_text(encoding="utf-8"),
                        encoding="utf-8")
    return context, worktree, head


def test_review_with_all_rigor_sections_of_head_passes(tmp_path, capsys):
    context, _, _ = _reviewed(tmp_path, "HEAD")
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 0, capsys.readouterr().err


def test_r1_fails_when_the_newest_review_cites_an_older_commit(tmp_path, capsys):
    context, worktree, old = _reviewed(tmp_path, "HEAD")
    head = commit_file(worktree, "fix after R1")
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 1
    err = capsys.readouterr().err
    assert old in err and head in err


def test_r1_fails_when_the_newest_review_has_no_reviewed_commit_line(tmp_path, capsys):
    context, _, head = _reviewed(tmp_path, None)
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 1
    err = capsys.readouterr().err
    assert "no `Last reviewed commit:` line" in err and head in err


def _own_pr(context: Path, worktree: Path, number: int) -> None:
    """The run's E4 record names PR `number`, so R1 reads PR_REVIEW_#<number>.md and no other (R1 N-13)."""
    import json

    run = json.loads((context / "cadence-session.json").read_text(encoding="utf-8"))["pipeline_run_id"]
    (context / "prs" / f"draft-pr-{run}.md").write_text(f"PR: https://github.com/o/r/pull/{number}\n", encoding="utf-8")


def test_r1_reads_the_review_of_the_runs_own_pr_not_the_newest(tmp_path, capsys):
    import os
    import time

    context, worktree, head = _reviewed(tmp_path, "HEAD")  # PR_REVIEW_#2.md cites HEAD
    _own_pr(context, worktree, 2)
    other = context / "prs" / "reviews" / "PR_REVIEW_#9.md"
    other.write_text((context / "prs" / "reviews" / "PR_REVIEW_#2.md").read_text(encoding="utf-8")
                     .replace(head, "0" * 40), encoding="utf-8")
    later = time.time() + 60
    os.utime(other, (later, later))  # the other PR's review is newer and cites another commit
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 0, capsys.readouterr().err


def test_r1_fails_when_the_runs_own_pr_has_no_review(tmp_path, capsys):
    context, worktree, _ = _reviewed(tmp_path, "HEAD")  # only PR_REVIEW_#2.md exists
    _own_pr(context, worktree, 7)
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 1
    assert "no peer-written review of this run's PR at prs/reviews/PR_REVIEW_#7.md" in capsys.readouterr().err


# --- each R1 must-fix item is a ledger row, and a NO MERGE verdict with no row fails R1.

NO_MERGE = "## Must-fix items\n\n- MF-1: the gate passes a corrupt file.\n- MF-2: the hook skips a symlink.\n\nVerdict: NO MERGE (2 must-fix)\n"


def _no_merge(tmp_path: Path, body: str = NO_MERGE) -> Path:
    context, _, _ = _reviewed(tmp_path, "HEAD")
    path = context / "prs" / "reviews" / "PR_REVIEW_#2.md"
    path.write_text(path.read_text(encoding="utf-8") + body, encoding="utf-8")
    return context


@pytest.mark.parametrize("body, missing", [
    (NO_MERGE, ["gh-1-run1-MF-1", "gh-1-run1-MF-2"]),
    ("Verdict: NO MERGE (1 must-fix)\n", ["gh-1-run1-MF-<n>"]),
])
def test_r1_fails_on_a_must_fix_item_with_no_ledger_row(tmp_path, capsys, body, missing):
    context = _no_merge(tmp_path, body)
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 1
    err = capsys.readouterr().err
    assert all(f"ledger row {row}" in err for row in missing), err


def test_r1_passes_with_a_row_per_must_fix_and_r5_holds_on_the_open_rows(tmp_path, capsys):
    from closure import closure_errors
    from conftest import ledger_row, write_ledger
    from findings_io import load_findings

    context = _no_merge(tmp_path)
    ledger = write_ledger(context / "findings.jsonl", ledger_row("gh-1-run1-MF-1"))
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 1
    assert "ledger row gh-1-run1-MF-2" in capsys.readouterr().err
    write_ledger(ledger, ledger_row("gh-1-run1-MF-2", stage="pr-review-local"))
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 0, capsys.readouterr().err
    errors = closure_errors(load_findings(ledger))
    assert [e.split(":")[0] for e in errors] == ["gh-1-run1-MF-1", "gh-1-run1-MF-2"]


def test_r1_does_not_accept_the_rows_of_an_earlier_run_of_the_task(tmp_path, capsys):
    """Run 2's MF-1 is not run 1's row, so a stale verified row cannot satisfy R1."""
    from conftest import ledger_row, write_ledger

    context = _no_merge(tmp_path)
    write_ledger(context / "findings.jsonl", ledger_row("gh-1-MF-1", status="verified"),
                 ledger_row("gh-1-0ldrun00-MF-2", status="verified"))
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 1
    err = capsys.readouterr().err
    assert "no ledger row gh-1-run1-MF-1" in err and "no ledger row gh-1-run1-MF-2" in err


def test_r1_fails_closed_on_an_unreadable_ledger(tmp_path, capsys):
    context = _no_merge(tmp_path)
    (context / "findings.jsonl").write_text("{not json\n", encoding="utf-8")
    assert run_artifact_gate("pr-review-local-gate", context, check=True) == 1
    assert "findings ledger is unreadable" in capsys.readouterr().err


def test_review_rigor_doc_lists_the_enforced_sections():
    text = (ROOT / "references" / "review-rigor.md").read_text(encoding="utf-8")
    documented = tuple(re.findall(r"^### `## ([^`]+)`$", text, re.M))
    assert documented == REVIEW_RIGOR_SECTIONS
