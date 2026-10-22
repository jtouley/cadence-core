"""architecture_review: the plan artifact, finding ids the ledger accepts, unique across runs, from the session's ticket, and no import of the indexer."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from conftest import code_paths, git_repo, ledger_row, run_py, write_ledger

ROOT = Path(__file__).resolve().parents[2]
code_paths()
from architecture_review import _has_plan_artifact, run_architecture_review  # noqa: E402
from findings_io import ID_RE, load_findings, set_status, task_id_prefix  # noqa: E402


# --- has_plan_artifact_context_plans: architecture_review._has_plan_artifact accepts .context/plans/.

def test_has_plan_artifact_accepts_context_plans_dir(tmp_path):
    context = tmp_path / ".context"
    plans = context / "plans"
    plans.mkdir(parents=True)
    (plans / "toy.plan.md").write_text("# plan\n", encoding="utf-8")
    assert _has_plan_artifact(context, tmp_path) is True


def test_has_plan_artifact_accepts_cursor_plans(tmp_path):
    cursor = tmp_path / ".cursor" / "plans"
    cursor.mkdir(parents=True)
    (cursor / "toy.plan.md").write_text("# plan\n", encoding="utf-8")
    context = tmp_path / ".context"
    context.mkdir()
    assert _has_plan_artifact(context, tmp_path) is True


# --- architecture_review_finding_ids_unique_across_runs: Each run of the architecture review appends findings with new ids.
#
# Regression (ledgerlite budgets eval, iteration 2): P5 and E3 each started at
# F1, so the ledger held three `cd-1-F1` rows. set-status refuses an id with more
# than one match, so no allowed path could close the P5 must-fix, and R4-R6 failed.

def _review(tmp_path: Path, phase: str, task_id: str = "cd-1") -> list[dict]:
    context = tmp_path / ".context"
    context.mkdir(exist_ok=True)
    worktree = tmp_path / "wt"
    worktree.mkdir(exist_ok=True)
    return run_architecture_review(
        phase=phase, worktree=worktree, context_dir=context,
        findings_path=context / "findings.jsonl", skill_root=ROOT, task_id=task_id,
    )


def test_three_runs_give_three_distinct_ids_and_each_can_be_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    runs = [_review(tmp_path, "plan"), _review(tmp_path, "impl"), _review(tmp_path, "impl")]
    assert all(runs)
    ledger = tmp_path / ".context" / "findings.jsonl"
    ids = [row["id"] for row in load_findings(ledger)]
    assert len(ids) == len(set(ids)) == sum(len(r) for r in runs)
    from conftest import commit_file, git_repo

    repo = git_repo(tmp_path / "repo")
    sha = commit_file(repo, "the fix")
    for finding_id in ids:
        assert set_status(ledger, finding_id, "fixed", fixed_in=sha, cwd=repo)["status"] == "fixed"


def test_numbering_continues_after_the_highest_id_for_this_task_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    _review(tmp_path, "plan", task_id="other")
    first = _review(tmp_path, "plan")
    second = _review(tmp_path, "impl")
    assert first[0]["id"] == "cd-1-F1"
    assert second[0]["id"] == f"cd-1-F{len(first) + 1}"


# --- architecture_review_finding_id_for_github_ticket: A GitHub ticket id (`owner/repo#N`) must still give finding ids the ledger accepts.

def test_finding_ids_are_ledger_valid_for_owner_repo_ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    ledger = tmp_path / ".context" / "findings.jsonl"
    findings = _review(tmp_path, "plan", task_id="jtouley/cadence#16")
    assert findings
    assert all(ID_RE.match(f["id"]) for f in findings)
    assert [f["id"] for f in load_findings(ledger)] == [f["id"] for f in findings]
    assert findings[0]["id"].startswith("jtouley-cadence-16-F")


# --- the id prefix and the own-row rule come from findings_io -------------------------------

def test_ids_come_from_the_shared_prefix_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    ledger = tmp_path / ".context" / "findings.jsonl"
    write_ledger(ledger, ledger_row("OWNER-REPO-12-F4", stage="other-stage", severity="nit", status="deferred",
                                    ticket="#1"))
    first = _review(tmp_path, "plan", task_id="owner/repo#12")
    assert first and first[0]["id"] == task_id_prefix("owner/repo#12") + "-F5"


def test_supersede_closes_an_upper_case_own_row(tmp_path, monkeypatch):
    """Supersede uses the shared own-row rule, so the task part ignores case."""
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    import architecture_review as ar

    ledger = tmp_path / ".context" / "findings.jsonl"
    gate = "python3 scripts/architecture_review.py --phase plan --check"
    write_ledger(ledger, ledger_row("CD-63-F2", stage="architecture-review-plan", check=gate),
                 ledger_row("cd-64-F1", stage="architecture-review-plan", check=gate))
    ar._supersede(ledger, ar.task_row_pattern("cd-63"), "architecture-review-plan", "P5 pass at abc")
    status = {r["id"]: r["status"] for r in load_findings(ledger)}
    assert status == {"CD-63-F2": "verified", "cd-64-F1": "open"}


# --- architecture_review_cli_defaults_task_id_from_session: The registered P5/E3 command passes no --task-id; findings must use the session's ticket.

LINKED = (
    "---\ndecision_id: D-7\nstatus: proposed\nquestion: q\nchoice: c\nrationale: r\n"
    "links:\n  - rel: covers\n    target_type: task\n    target_id: cd-7\n---\n"
)


def _run(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/architecture_review.py", "--phase", "plan", *extra, cwd=tmp_path)


def test_cli_uses_session_task_id(tmp_path):
    git_repo(tmp_path)
    ctx = tmp_path / ".context"
    (ctx / "proposals").mkdir(parents=True)
    (ctx / "proposals" / "D-7.md").write_text(LINKED, encoding="utf-8")
    (ctx / "cadence-session.json").write_text(
        json.dumps({"active_skill": "cadence", "task_id": "cd-7"}), encoding="utf-8"
    )
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    ledger = ctx / "findings.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    assert rows == [], f"expected no findings for a ticket-linked decision, got {rows}"


def test_check_exits_nonzero_on_must_fix_without_recording(tmp_path):
    git_repo(tmp_path)
    (tmp_path / ".context").mkdir()
    proc = _run(tmp_path, "--check")
    assert proc.returncode == 1
    assert "core:decision-coverage" in proc.stdout + proc.stderr
    assert not (tmp_path / ".context" / "findings.jsonl").exists()


# --- architecture_review_does_not_import_context_index: Coverage review must parse Decision frontmatter without loading the indexer.

REVIEW = ROOT / "scripts" / "architecture_review.py"


def test_architecture_review_does_not_import_context_index():
    tree = ast.parse(REVIEW.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "context_index" not in imported
    assert "decision_frontmatter" in imported
