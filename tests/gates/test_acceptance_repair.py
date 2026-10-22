"""A pinned acceptance check that no code can pass has a repair path with no person in it.

A Haiku run hard-stopped at E1: P1 wrote a check that read CRLF output in text mode, so `\\r\\n`
became `\\n` and no implementation could pass. P9 had pinned it, and E1 could only ask a person.
Now E1 records the defect with a repro, the plan peer fixes the check and keeps its claim, and
`acceptance.py repin` pins the fixed check and logs the change for R1. P1 commits its check scripts.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import code_paths, git, git_repo, make_session

code_paths()
import acceptance  # noqa: E402
import handoff_inputs  # noqa: E402

TASK = "gh-1"
ROOT = Path(__file__).resolve().parents[2]
BROKEN = "import sys; sys.exit(1)\n"  # The check that no implementation can pass
FIXED = "import sys; sys.exit(0)\n"
AC1 = {"id": "AC-1", "claim": "rows end with CRLF", "run": "python3 acceptance/check_one.py"}
AC2 = {"id": "AC-2", "claim": "a bad month exits 2", "run": "python3 acceptance/check_two.py"}


def _repo(tmp_path: Path, one: str = BROKEN) -> tuple[Path, Path]:
    repo = git_repo(tmp_path / "repo")
    (repo / "acceptance").mkdir()
    (repo / "acceptance" / "check_one.py").write_text(one, encoding="utf-8")
    (repo / "acceptance" / "check_two.py").write_text(FIXED, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "test: acceptance checks of the request")
    ctx = repo / ".context"
    make_session(ctx, task_id=TASK, pipeline_run_id="run-1", workspace=str(repo))
    _write(ctx, [AC1, AC2])
    assert acceptance.pin(ctx, repo) == []
    return repo, ctx


def _write(ctx: Path, checks: list[dict]) -> None:
    path = ctx / "acceptance" / f"{TASK}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"RUN_BY_COMMAND": "plan-create", "task_id": TASK, "checks": checks}), encoding="utf-8")


REPRO = "python3 acceptance/check_one.py"
WHY = "the check reads stdout in text mode, so CRLF is LF"


def test_a_failing_check_names_how_to_record_a_defect(tmp_path):
    repo, ctx = _repo(tmp_path)
    [error] = acceptance.verify(ctx, repo)
    assert "AC-1 failed" in error and "acceptance.py\" defect --id AC-1" in error
    assert "acceptance/repro_AC-1.py" in error  # Check_problem refuses inline -c code, so the repro is a script


def test_a_defect_needs_a_known_id_a_repro_that_runs_and_a_reason(tmp_path):
    repo, ctx = _repo(tmp_path)
    assert "AC-9" in acceptance.defect(ctx, repo, "AC-9", REPRO, WHY)[0]
    assert "no-op" in acceptance.defect(ctx, repo, "AC-1", "true", WHY)[0]
    assert "reason" in acceptance.defect(ctx, repo, "AC-1", REPRO, " ")[0]
    assert acceptance.open_defects(ctx) == {}


def test_an_open_defect_sends_the_check_to_the_plan_peer_not_to_the_code(tmp_path):
    repo, ctx = _repo(tmp_path)
    assert acceptance.defect(ctx, repo, "AC-1", REPRO, WHY) == []
    [error] = acceptance.verify(ctx, repo)
    assert "AC-1 has an open defect" in error and WHY in error
    assert "/plan-update" in error and "--acceptance AC-1" in error and "repin --id AC-1" in error


def test_repin_needs_an_open_defect(tmp_path):
    repo, ctx = _repo(tmp_path)
    (repo / "acceptance" / "check_one.py").write_text(FIXED, encoding="utf-8")
    assert "no open defect" in acceptance.repin(ctx, repo, "AC-1")[0]


def test_repin_refuses_a_changed_claim(tmp_path):
    repo, ctx = _repo(tmp_path)
    acceptance.defect(ctx, repo, "AC-1", REPRO, WHY)
    _write(ctx, [{**AC1, "claim": "rows end with a line break"}, AC2])
    assert "claim of AC-1 changed" in acceptance.repin(ctx, repo, "AC-1")[0]


def test_repin_refuses_a_change_to_another_check(tmp_path):
    repo, ctx = _repo(tmp_path)
    acceptance.defect(ctx, repo, "AC-1", REPRO, WHY)
    (repo / "acceptance" / "check_two.py").write_text("print('weaker')\n", encoding="utf-8")
    assert any("acceptance/check_two.py" in e for e in acceptance.repin(ctx, repo, "AC-1"))
    (repo / "acceptance" / "check_two.py").write_text(FIXED, encoding="utf-8")
    _write(ctx, [AC1, {**AC2, "run": "python3 acceptance/check_one.py"}])
    assert any("AC-2" in e for e in acceptance.repin(ctx, repo, "AC-1"))


def test_repin_pins_the_fixed_check_closes_the_defect_and_logs_the_change(tmp_path):
    repo, ctx = _repo(tmp_path)
    acceptance.defect(ctx, repo, "AC-1", REPRO, WHY)
    (repo / "acceptance" / "check_one.py").write_text(FIXED, encoding="utf-8")
    assert acceptance.repin(ctx, repo, "AC-1") == []
    assert acceptance.verify(ctx, repo) == [] and acceptance.open_defects(ctx) == {}
    rows = [json.loads(line) for line in acceptance.repair_path(ctx).read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["defect", "repin"]
    assert rows[1]["id"] == "AC-1" and rows[1]["why"] == WHY
    assert rows[1]["old"]["acceptance/check_one.py"] != rows[1]["new"]["acceptance/check_one.py"]
    # R1 is the one review of a repin: it reads the text of the check before and after, not only the sha256.
    assert rows[1]["old_text"] == {"run": AC1["run"], "acceptance/check_one.py": BROKEN}
    assert rows[1]["new_text"] == {"run": AC1["run"], "acceptance/check_one.py": FIXED}


def test_the_r1_packet_carries_the_repair_log(tmp_path, monkeypatch):
    repo, ctx = _repo(tmp_path)
    acceptance.defect(ctx, repo, "AC-1", REPRO, WHY)
    monkeypatch.chdir(repo)
    run = handoff_inputs.Run(root=ROOT, ctx=ctx, workspace=repo,
                             session={"task_id": TASK, "pipeline_run_id": "run-1", "workspace": str(repo)})
    found = handoff_inputs.resolve(handoff_inputs.handoff._stage("R1", ROOT), run)
    assert f".context/acceptance/{TASK}.repair.jsonl" in found.inputs


def test_p1_fails_while_a_check_script_is_not_committed(tmp_path):
    repo, ctx = _repo(tmp_path, FIXED)
    (repo / "acceptance" / "check_three.py").write_text(FIXED, encoding="utf-8")
    _write(ctx, [AC1, AC2, {"id": "AC-3", "claim": "x", "run": "python3 acceptance/check_three.py"}])
    [error] = acceptance.problems(ctx, worktree=repo)
    assert "acceptance/check_three.py" in error and "git add acceptance/check_three.py" in error
    assert "The P1 peer" in error  # The orchestrator may not write code (orchestrator_scope)
    (repo / "acceptance" / "check_one.py").write_text("# edited\n" + FIXED, encoding="utf-8")
    assert any("acceptance/check_one.py" in e for e in acceptance.problems(ctx, worktree=repo))


def test_p1_fails_when_two_checks_share_a_script(tmp_path):
    """A Haiku run: P1 wrote one script for AC-1 to AC-5, AC-4 had a defect in it, and the repin of AC-4 could
    not change the shared file. One script per check keeps a repin to its own check."""
    repo, ctx = _repo(tmp_path, FIXED)
    _write(ctx, [AC1, {**AC2, "run": "python3 acceptance/check_one.py --case AC-2"}])
    [error] = acceptance.problems(ctx, worktree=repo)
    assert "AC-1 and AC-2 name acceptance/check_one.py" in error and "one script per check" in error


def test_plan_create_says_a_printed_value_is_compared_whole():
    """Four live runs: the check of "prints the row count" matched a digit, so code that printed a fixed count
    passed it, and R1 sent the repair each time. P1 now builds the expected text from its own input."""
    text = (ROOT / "commands" / "plan-create.md").read_text(encoding="utf-8")
    assert "makes the expected text from its own input" in text and "not a pattern or a digit" in text
