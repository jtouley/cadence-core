"""A deferral meets explicit criteria and gets a RAG ruling of the /deferral-review peer.

The request's work is never deferred, each ripeness names its evidence, a review finding defers only
on a blocker that no code change in the PR can clear. P8 writes the manifest that the peer rules from,
and accepts a row only when the ruling of the register as it is now is GREEN, or AMBER with its
condition in the plan.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from conftest import activated_repo, code_paths, read_session, run_py

code_paths()
from deferral_adjudication_check import adjudicate_deferral_register  # noqa: E402

HEAD = "| # | Item | Why deferred | Request trace | Ripeness | Evidence | Follow-up ticket |\n|---|---|---|---|---|---|---|\n"
GOOD = "| 1 | Retry the upload | Needs the cache layer | none | blocked-by | #41 adds the cache layer | #42 |\n"


def _codes(section: str, row: str) -> list[str]:
    return [i.code for i in adjudicate_deferral_register(f"## {section}\n\n{HEAD}{row}").issues]


@pytest.mark.parametrize("row, codes", [
    pytest.param(GOOD, [], id="a-ripe-row-with-evidence-passes"),
    pytest.param("| 1 | Export as CSV | Big | request line 2 | different-epic | EPIC-9 owns exports | #42 |\n",
                 ["deferral-core-of-request"], id="the-request-asks-for-it"),
    pytest.param("| 1 | Retry | Later | | blocked-by | #41 | #42 |\n", ["deferral-missing-trace"], id="no-trace"),
    pytest.param("| 1 | Retry | Later | none | blocked-by | | #42 |\n", ["deferral-missing-evidence"], id="no-evidence"),
    pytest.param("| 1 | Retry | Later | none | blocked-by | the cache work | #42 |\n",
                 ["deferral-blocker-unnamed"], id="a-blocker-with-no-ticket"),
    pytest.param("| 1 | Retry | out of AC | none | | #41 | #42 |\n", ["deferral-missing-ripeness"],
                 id="out-of-ac-is-no-ripeness"),
    pytest.param("| 1 | Retry | Later | none | yagni | x | #42 |\n", ["deferral-invalid-ripeness"], id="yagni"),
])
def test_an_approved_row_meets_each_criterion(row, codes):
    assert _codes("Approved deferrals", row) == codes


@pytest.mark.parametrize("ripeness, codes", [
    ("blocked-by", []), ("prod-mutation", []), ("fleet-ops", []),
    ("different-layer", ["review-deferral-not-ripe"]), ("different-epic", ["review-deferral-not-ripe"]),
])
def test_a_review_finding_defers_only_on_a_blocker_that_code_cannot_clear(ripeness, codes):
    row = f"| 1 | cd-7-F1: Retry | Later | none | {ripeness} | #41 is open | #42 |\n"
    assert _codes("Review deferrals", row) == codes


def _register(repo: Path, rows: str) -> Path:
    task = read_session(repo)["task_id"]
    path = repo / ".context" / "deferrals" / f"{task}_deferral-register.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Deferral register: {task}\n\nTicket: {task}\n\n## Approved deferrals\n\n{rows}", encoding="utf-8")
    return path


def _ruling(register: Path, *rows: tuple[str, str, str]) -> None:
    sha = hashlib.sha256(register.read_bytes()).hexdigest()[:12]
    table = "".join(f"| {key} | {verdict} | #41 is open | {condition} |\n" for key, verdict, condition in rows)
    register.with_name(register.name.replace("register", "review")).write_text(
        f"RUN_BY_COMMAND: deferral-review\nRegister sha256: {sha}\n\n| Row | Ruling | Reason | Condition |\n"
        f"|---|---|---|---|\n{table}", encoding="utf-8")


def _p8(repo: Path, *flags: str):
    return run_py("scripts/deferral_adjudication_check.py", *flags, cwd=repo)


def test_p8_writes_the_manifest_and_holds_each_deferral_until_it_is_green(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    register = _register(repo, HEAD + GOOD)
    task = read_session(repo)["task_id"]
    assert _p8(repo, "--check").returncode == 1
    assert not (repo / ".context" / "manifests").exists()
    proc = _p8(repo)
    assert proc.returncode == 1 and f"NEXT: send the peer `/deferral-review .context/manifests/{task}_deferrals.md`" in proc.stdout
    manifest = (repo / ".context" / "manifests" / f"{task}_deferrals.md").read_text(encoding="utf-8")
    assert f"Register sha256: {hashlib.sha256(register.read_bytes()).hexdigest()}" in manifest
    assert "Rows: approved 1" in manifest and f"| .context/deferrals/{register.name} |" in manifest
    assert f".context/deferrals/{task}_deferral-review.md" in manifest
    _ruling(register, ("approved 1", "RED", "none"))
    proc = _p8(repo, "--check")
    assert proc.returncode == 1 and "deferral-red: approved 1" in proc.stderr and "/plan-update" in proc.stderr
    _ruling(register, ("approved 1", "GREEN", "none"))
    assert _p8(repo, "--check").returncode == 0
    register.write_text(register.read_text(encoding="utf-8") + "\nA note.\n", encoding="utf-8")
    proc = _p8(repo, "--check")
    assert proc.returncode == 1 and "as it is now" in proc.stderr


def test_an_amber_condition_holds_until_the_plan_carries_it(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    session = read_session(repo)
    register = _register(repo, HEAD + GOOD)
    _ruling(register, ("approved 1", "AMBER", "none"))
    assert "AMBER with no condition" in _p8(repo, "--check").stderr
    _ruling(register, ("approved 1", "AMBER", "keep one upload seam"))
    plan = repo / ".cursor" / "plans" / "x.plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(f"RUN_BY_COMMAND: plan-create\nTicket: {session['task_id']}\nPipeline-Run: "
                    f"{session['pipeline_run_id']}\n\n### Phase 1: export\n", encoding="utf-8")
    proc = _p8(repo, "--check")
    assert proc.returncode == 1 and "deferral-amber" in proc.stderr and "## Deferral conditions" in proc.stderr
    plan.write_text(plan.read_text(encoding="utf-8") + "\n## Deferral conditions\n\n- approved 1: one upload seam "
                    "(Phase 1)\n", encoding="utf-8")
    assert _p8(repo, "--check").returncode == 0, _p8(repo, "--check").stderr


def test_p8_needs_no_ruling_when_nothing_is_deferred(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    _register(repo, "No approved deferrals.\n")
    assert _p8(repo, "--check").returncode == 0


def test_the_peer_contract_names_each_criterion_and_ruling():
    root = Path(__file__).resolve().parents[2]
    doc = (root / "commands" / "deferral-review.md").read_text(encoding="utf-8")
    for token in ("`GREEN`", "`AMBER`", "`RED`", "`blocked-by`", "`prod-mutation`", "Register sha256:",
                  "Read only the documents that the manifest lists", "Rows:"):
        assert token in doc


@pytest.mark.parametrize(("cell", "none"), [
    ("none", True), ("`none`", True), ("None.", True), ("**none**", True),
    ("none. The request names LOCAL-9 only to put the upload out of this change", True),
    ("none (the request puts it out of this change)", True),
    ("Request line 4: notes are quoted as RFC 4180 says", False), ("nonetheless the request asks for it", False),
])
def test_a_trace_cell_that_says_none_with_a_reason_traces_to_nothing(cell, none):
    """A plan peer explained its `none`, and P8 refused the row through three plan updates."""
    import deferral_adjudication_check as dac

    assert dac.traces_to_nothing(cell) is none
