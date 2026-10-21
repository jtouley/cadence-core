"""R4 counts the review's fix rounds and holds at the budget.

A fix round is one `## Update` section of the PR review. At `review_fix_rounds` (default 2)
rounds, a loop that is still open holds with the loop rule. A config value can only lower
the budget.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import commit_file, context_ignored_repo, head_sha, make_session, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

HOLD = "the review fix loop is at its budget"


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


def test_the_default_budget_is_2_and_the_doc_names_the_config_key():
    defaults = json.loads((ROOT / "cadence.defaults.json").read_text(encoding="utf-8"))
    assert defaults["review_fix_rounds"] == 2
    doc = (ROOT / "references" / "final-consolidate.md").read_text(encoding="utf-8")
    assert "`review_fix_rounds`" in doc and "default 2" in doc


@pytest.mark.parametrize("rounds", [0, 1, 3])
def test_fix_rounds_counts_the_update_sections(rounds):
    from final_consolidate import fix_rounds

    text = "RUN_BY_COMMAND: pr-review-local\n" + "".join(f"## Update 2026-10-1{n}\nbody\n" for n in range(rounds))
    assert fix_rounds(text + "### Update notes\nnot a round\n") == rounds


@pytest.mark.parametrize("config, budget", [({}, 2), ({"review_fix_rounds": 1}, 1), ({"review_fix_rounds": 5}, 2)])
def test_config_can_only_lower_the_budget(tmp_path, config, budget):
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


def test_r4_holds_at_the_budget_with_the_adr_0009_text(tmp_path):
    repo = _repo(tmp_path)
    _review(repo, 2, head_sha(repo, 7))
    commit_file(repo, "fix: one more nit", "fix.py", "X = 1\n")
    proc = _r4(repo)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert f"{HOLD}: 2 fix rounds" in out
    assert "a MERGE-READY verdict with no open must-fix ends the loop" in out and "a nit goes to the tracker" in out
    assert json.loads(proc.stdout)["fix_rounds"] == 2


def test_r4_below_the_budget_asks_for_the_next_round(tmp_path):
    repo = _repo(tmp_path)
    _review(repo, 1, head_sha(repo, 7))
    commit_file(repo, "fix: must-fix", "fix.py", "X = 1\n")
    proc = _r4(repo)
    assert proc.returncode == 1
    assert "/pr-review-update" in proc.stderr and HOLD not in proc.stderr


def test_r4_at_the_budget_passes_when_the_loop_ended(tmp_path):
    repo = _repo(tmp_path)
    _review(repo, 2, head_sha(repo, 7))
    proc = _r4(repo)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["delta_pass"]["budget_remaining_hint"] == 0


@pytest.mark.parametrize(("config", "rounds", "held"), [
    pytest.param({"review_fix_rounds": 1}, 1, True, id="a-lower-budget-holds-sooner"),
    pytest.param({"review_fix_rounds": 5}, 2, True, id="a-higher-budget-is-ignored"),
    pytest.param({"review_fix_rounds": 5}, 1, False, id="below-the-default-budget"),
])
def test_r4_reads_the_budget_from_the_project_layer(tmp_path, config, rounds, held):
    repo = _repo(tmp_path)
    commit_file(repo, "chore: project config", ".cadence.json", json.dumps(config))
    _review(repo, rounds, head_sha(repo, 7))
    commit_file(repo, "fix: one more nit", "fix.py", "X = 1\n")
    proc = _r4(repo)
    assert proc.returncode == 1
    assert (f"{HOLD}: {rounds} fix rounds" in proc.stdout + proc.stderr) is held
