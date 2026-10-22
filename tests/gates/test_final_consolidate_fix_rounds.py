"""R4 counts the review's fix rounds, and the loop goes on until it passes.

A fix round is one `## Update` section of the PR review. A must-fix is fixed in the PR, so the loop
has no default cap. It holds when a round changed nothing, or at a `review_fix_rounds` cap that the
config sets.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import commit_file, context_ignored_repo, head_sha, ledger_row, make_session, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

STALL = "changed nothing"


def _repo(tmp_path: Path) -> Path:
    repo = context_ignored_repo(tmp_path / "r", "feature/gh-1-x", "feat: phase 1")
    make_session(repo / ".context", workspace=str(repo))
    return repo


def _review(repo: Path, rounds: int, head: str) -> None:
    """A PR review with `rounds` update sections; the last one cites `head`."""
    updates = "".join(f"\n## Update 2026-10-{10 + n}\n\nVerdict: NO MERGE\n" for n in range(rounds))
    path = repo / ".context" / "prs" / "reviews" / "PR_REVIEW_#1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"RUN_BY_COMMAND: pr-review-local\nVerdict: NO MERGE\n{updates}\nLast reviewed commit: {head}\n",
                    encoding="utf-8")


def _r4(repo: Path) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/final_consolidate.py", "--check", cwd=repo)


def test_there_is_no_default_cap_and_the_doc_names_the_config_key(tmp_path):
    from final_consolidate import fix_round_budget

    defaults = json.loads((ROOT / "cadence.defaults.json").read_text(encoding="utf-8"))
    assert defaults["review_fix_rounds"] is None
    (tmp_path / ".git").mkdir()
    assert fix_round_budget(ROOT, tmp_path) is None
    doc = (ROOT / "references" / "final-consolidate.md").read_text(encoding="utf-8")
    assert "`review_fix_rounds`" in doc and "there is no cap" in doc


@pytest.mark.parametrize("rounds", [0, 1, 3])
def test_fix_rounds_counts_the_update_sections(rounds):
    from final_consolidate import fix_rounds

    text = "RUN_BY_COMMAND: pr-review-local\n" + "".join(f"## Update 2026-10-1{n}\nbody\n" for n in range(rounds))
    assert fix_rounds(text + "### Update notes\nnot a round\n") == rounds


@pytest.mark.parametrize("config, budget", [({}, None), ({"review_fix_rounds": 1}, 1), ({"review_fix_rounds": 5}, 5)])
def test_the_config_sets_an_optional_cap(tmp_path, config, budget):
    from final_consolidate import fix_round_budget

    (tmp_path / ".git").mkdir()
    (tmp_path / ".cadence.json").write_text(json.dumps(config), encoding="utf-8")
    assert fix_round_budget(ROOT, tmp_path) == budget


@pytest.mark.parametrize("value", [0, "2", True])
def test_a_bad_budget_value_fails_closed(tmp_path, value):
    from final_consolidate import fix_round_budget

    (tmp_path / ".git").mkdir()
    (tmp_path / ".cadence.json").write_text(json.dumps({"review_fix_rounds": value}), encoding="utf-8")
    with pytest.raises(ValueError, match="review_fix_rounds"):
        fix_round_budget(ROOT, tmp_path)


def test_r4_keeps_the_loop_open_after_many_rounds(tmp_path):
    repo = _repo(tmp_path)
    _review(repo, 5, head_sha(repo, 7))
    commit_file(repo, "fix: one more must-fix", "fix.py", "X = 1\n")
    proc = _r4(repo)
    assert proc.returncode == 1
    assert "/pr-review-update" in proc.stderr and STALL not in proc.stderr and "defer" not in proc.stderr.lower()
    assert json.loads(proc.stdout)["fix_rounds"] == 5


def test_r4_holds_when_a_fix_round_changed_nothing(tmp_path):
    repo = _repo(tmp_path)
    ledger = repo / ".context" / "findings.jsonl"
    ledger.write_text(json.dumps(ledger_row("gh-1-F1")) + "\n", encoding="utf-8")
    _review(repo, 1, head_sha(repo, 40))
    first = _r4(repo)
    assert first.returncode == 1 and STALL not in first.stderr
    _review(repo, 2, head_sha(repo, 40))
    proc = _r4(repo)
    assert proc.returncode == 1
    assert f"fix round 2 {STALL}" in proc.stderr and "gh-1-F1" in proc.stderr and "--hard-stop retries" in proc.stderr
    commit_file(repo, "fix: gh-1-F1", "fix.py", "X = 1\n")
    _review(repo, 3, head_sha(repo, 40))
    assert STALL not in _r4(repo).stderr


def test_r4_passes_when_the_loop_ended(tmp_path):
    repo = _repo(tmp_path)
    _review(repo, 2, head_sha(repo, 7))
    proc = _r4(repo)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["delta_pass"]["budget_remaining_hint"] is None


@pytest.mark.parametrize(("config", "rounds", "held"), [
    pytest.param({"review_fix_rounds": 1}, 1, True, id="a-cap-holds-the-open-loop"),
    pytest.param({"review_fix_rounds": 3}, 2, False, id="below-the-cap"),
    pytest.param({}, 6, False, id="no-cap"),
])
def test_r4_reads_the_cap_from_the_project_layer(tmp_path, config, rounds, held):
    repo = _repo(tmp_path)
    commit_file(repo, "chore: project config", ".cadence.json", json.dumps(config))
    _review(repo, rounds, head_sha(repo, 7))
    commit_file(repo, "fix: one more must-fix", "fix.py", "X = 1\n")
    proc = _r4(repo)
    assert proc.returncode == 1
    assert ("the fix loop is at review_fix_rounds" in proc.stdout + proc.stderr) is held
