"""P7 and E5 test_benchmark_gate: the plan and code modes, and the code mode's adversarial inputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import code_paths, feature_repo, ledger_row, make_session, project_config, write_files, write_ledger

ROOT = Path(__file__).resolve().parents[2]
code_paths()
from test_benchmark_gate import DRY_MEASUREMENT_KEYS, run  # noqa: E402


# --- test_benchmark_gate: test_benchmark_gate.run artifact, lean record, and pipeline_run_id contracts.

pytestmark = pytest.mark.integration

_PLAN_MARKER = '{"RUN_BY_SKILL": "test-benchmark"}\n'


def test_test_benchmark_gate_missing_artifact_fails(scripts_path, tmp_path):
    from test_benchmark_gate import run

    context = tmp_path / ".context"
    make_session(context)
    code = run(context, mode="plan")
    assert code == 1
    assert not (context / "benchmarks").exists()
    assert (context / "cadence-session.json").is_file()


def test_test_benchmark_gate_file_without_marker_fails(scripts_path, tmp_path):
    from test_benchmark_gate import run

    context = tmp_path / ".context"
    make_session(context)
    benches = context / "benchmarks"
    benches.mkdir()
    (benches / "x_plan.json").write_text("{}\n", encoding="utf-8")
    code = run(context, mode="plan")
    assert code == 1
    assert (benches / "x_plan.json").is_file()


def test_test_benchmark_gate_code_mode_missing_artifact_fails(scripts_path, tmp_path):
    from test_benchmark_gate import run

    context = tmp_path / ".context"
    make_session(context)
    code = run(context, mode="code")
    assert code == 1
    assert not list((context / "benchmarks").glob("*")) if (context / "benchmarks").exists() else True
    assert (context / "cadence-session.json").is_file()


def test_test_benchmark_gate_wrong_marker_value_fails(scripts_path, tmp_path):
    from test_benchmark_gate import run

    context = tmp_path / ".context"
    make_session(context)
    benches = context / "benchmarks"
    benches.mkdir()
    (benches / "x_plan.json").write_text(
        '{"RUN_BY_SKILL": "other"}\n', encoding="utf-8"
    )
    code = run(context, mode="plan")
    assert code == 1
    assert (benches / "x_plan.json").is_file()


def test_test_benchmark_gate_plan_artifact_passes(scripts_path, tmp_path):
    from prompt_shape import write_delegation_record
    from test_benchmark_gate import run

    context = tmp_path / ".context"
    make_session(context)
    write_delegation_record(
        context,
        run_id="run-1",
        stage="test-benchmark-plan",
        command="/test-benchmark plan benchmarks/x_plan.json",
        source="test",
    )
    benches = context / "benchmarks"
    benches.mkdir()
    (benches / "x_plan.json").write_text(_PLAN_MARKER, encoding="utf-8")
    code = run(context, mode="plan")
    breadcrumb = context / "gates" / "test-benchmark-gate-plan.json"
    assert code == 0
    assert breadcrumb.is_file()


# #43 (cd-47 D7): the scorecard finding shape of skills/test-benchmark/SKILL.md, through the R2 rule.


def _scorecard(context, finding: dict):
    import json

    path = context / "benchmarks" / "cd-52_code.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"RUN_BY_SKILL": "test-benchmark", "task_id": "cd-52", "scores": {},
                                "findings": [finding]}), encoding="utf-8")
    return path


def test_a_scorecard_must_fix_needs_its_ledger_row(scripts_path, tmp_path):
    from findings_io import append_finding
    from test_benchmark_gate import _must_fix_ledger_problems

    context = tmp_path / ".context"
    path = _scorecard(context, {"id": "cd-52-F01", "severity": "must-fix", "evidence": "weak asserts"})
    assert _must_fix_ledger_problems(path)
    append_finding(context / "findings.jsonl", {
        "id": "cd-52-F01", "stage": "test-benchmark-pr", "rule_ref": "r2", "class": "spec-gap",
        "severity": "must-fix", "location": "tests", "evidence": "weak asserts", "check": "pytest", "status": "open",
    })
    assert _must_fix_ledger_problems(path) == []


@pytest.mark.parametrize("value, counts", [
    (True, True), (1, True), ("true", True), ("YES", True), (" yes ", True),
    (False, False), (0, False), ("false", False), ("no", False), ("0", False), (None, False), (2, False),
])
def test_must_fix_values(scripts_path, value, counts):
    from test_benchmark_gate import _is_must_fix

    assert _is_must_fix({"id": "x", "severity": "low", "must_fix": value}) is counts


# --- benchmark_gate_code_requires_adversarial_inputs: R2 code-mode benchmark must record adversarial inputs (references/review-rigor.md).

ENTRY = {"input": "another ticket's leftovers", "expected": "FAIL", "observed": "FAIL"}
PASSING = {"coverage": 8, "assertions": 8, "dry": 8, "layer": 9, "failure_paths": 7, "mutation": 7}
DRY = {"helpers_reimplemented": [], "helpers_reused": ["tests/conftest.py: make_session"], "thin_files": []}
SHAPE_BASE = {"tests/conftest.py": "def make_thing(path):\n    path.mkdir()\n    return path\n",
              "scripts/mod.py": "X = 1\n", "tests/test_mod.py": "import mod\n\n\ndef test_a():\n    pass\n"}
THREE = "".join(f"\n\ndef test_{i}():\n    assert {i}\n" for i in "abc")


@pytest.fixture(autouse=True)
def worktree(tmp_path, monkeypatch):
    """The run's worktree (ADR-0008: R2 measures its changed test files). `main` holds SHAPE_BASE."""
    repo = feature_repo(tmp_path / "repo", SHAPE_BASE)
    monkeypatch.chdir(repo)
    return repo


def _session_and_lean_record(context: Path, *, mode: str) -> None:
    from prompt_shape import write_delegation_record

    (context / "cadence-session.json").write_text(
        json.dumps(
            {"active_skill": "cadence", "pipeline_run_id": "run-1", "task_id": "t1"}
        ),
        encoding="utf-8",
    )
    stage = "test-benchmark-pr" if mode == "code" else "test-benchmark-plan"
    command = (
        "/test-benchmark benchmarks/cd-1_code.json"
        if mode == "code"
        else "/test-benchmark plan benchmarks/cd-1_plan.json"
    )
    write_delegation_record(
        context,
        run_id="run-1",
        stage=stage,
        command=command,
        source="test",
    )


def _bench(tmp_path: Path, name: str, **extra) -> None:
    path = tmp_path / "benchmarks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"RUN_BY_SKILL": "test-benchmark", "dry_measurement": DRY, **extra}), encoding="utf-8")


def test_code_mode_without_adversarial_inputs_fails(tmp_path, capsys):
    _bench(tmp_path, "cd-1_code.json")
    assert run(tmp_path, mode="code", check=True) == 1
    err = capsys.readouterr().err
    assert "no adversarial_inputs entries" in err
    assert "expected" in err and "observed" in err


def test_code_mode_with_incomplete_entry_fails(tmp_path, capsys):
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[{"input": "corrupt"}])
    assert run(tmp_path, mode="code", check=True) == 1
    err = capsys.readouterr().err
    assert "no adversarial_inputs entries" in err
    assert "expected" in err and "observed" in err


@pytest.mark.parametrize("mode, extra, code", [
    pytest.param("code", {"adversarial_inputs": [ENTRY], "scores": PASSING}, 0,
                 id="code_mode_with_adversarial_inputs_passes"),
    pytest.param("plan", {}, 0, id="plan_mode_does_not_need_adversarial_inputs"),
    pytest.param("code", {"adversarial_inputs": [{"input": "missing ledger", "expected": 0, "observed": 0}],
                          "scores": PASSING}, 0, id="code_mode_accepts_numeric_exit_codes"),
    # The deliverable carries the scores; a FAIL scorecard must not clear R2 (dcdd730c, 9a956068).
    pytest.param("code", {"adversarial_inputs": [ENTRY], "scores": {**PASSING, "dry": 2}}, 1,
                 id="code_mode_fails_when_a_score_is_below_seven"),
    pytest.param("code", {"adversarial_inputs": [ENTRY]}, 1, id="code_mode_fails_without_scores"),
    pytest.param("code", {"adversarial_inputs": [ENTRY], "scores": PASSING, "verdict": "FAIL"}, 1,
                 id="code_mode_fails_on_a_fail_verdict"),
    # skills/test-benchmark: below 7 is must-fix for layer, DRY and assertions; the rest count in the mean.
    pytest.param("code", {"adversarial_inputs": [ENTRY],
                          "scores": {**PASSING, "coverage": 6, "failure_paths": 6, "mutation": 9}}, 0,
                 id="code_mode_follows_the_skill_contract_for_non_blocking_dimensions"),
    pytest.param("code", {"adversarial_inputs": [ENTRY],
                          "scores": {"coverage": 5, "assertions": 7, "dry": 7, "layer": 7, "failure_paths": 5,
                                     "mutation": 5}}, 1,
                 id="code_mode_fails_when_the_mean_is_below_seven"),
    # #30: a must-fix that lives only in the scorecard has no owner in the ledger.
    pytest.param("code", {"adversarial_inputs": [ENTRY], "scores": PASSING,
                          "findings": [{"id": "cd-1-F1", "must_fix": True}]}, 1,
                 id="code_mode_must_fix_without_an_open_ledger_row_fails"),
])
def test_the_gate_judges_the_benchmark_deliverable(tmp_path, mode, extra, code):
    _session_and_lean_record(tmp_path, mode=mode)
    _bench(tmp_path, f"cd-1_{mode}.json", **extra)
    assert run(tmp_path, mode=mode, check=True) == code


@pytest.mark.parametrize(("floor", "scores", "needle"), [
    pytest.param(6, {**PASSING, "dry": 6}, "must-fix dimensions below 7: {'dry': 6}", id="a-lower-floor-is-ignored"),
    pytest.param(8, PASSING, "is below 8 (benchmark_score_floor)", id="a-higher-floor-applies"),
])
def test_a_project_can_only_raise_the_score_floor(worktree, tmp_path, capsys, floor, scores, needle):
    project_config(worktree, {"benchmark_score_floor": floor})
    _session_and_lean_record(tmp_path, mode="code")
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=scores)
    assert run(tmp_path, mode="code", check=True) == 1
    assert needle in capsys.readouterr().err


BENCH = {"stage": "test-benchmark-pr", "rule_ref": "test-benchmark:dry", "location": "tests/",
         "evidence": "cloned setup", "check": "python3 -m pytest tests/"}


def test_code_mode_must_fix_with_an_open_ledger_row_passes(tmp_path):
    _session_and_lean_record(tmp_path, mode="code")
    write_ledger(tmp_path / "findings.jsonl", ledger_row("cd-1-F1", **BENCH))
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING,
           findings=[{"id": "cd-1-F1", "must_fix": True}, {"id": "cd-1-F2", "must_fix": False}])
    assert run(tmp_path, mode="code", check=True) == 0


def test_code_mode_with_an_unreadable_ledger_fails_closed(tmp_path, capsys):
    _session_and_lean_record(tmp_path, mode="code")
    (tmp_path / "findings.jsonl").write_text('{"id": "bad id"}\n', encoding="utf-8")
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING,
           findings=[{"id": "cd-1-F1", "must_fix": True}])
    assert run(tmp_path, mode="code", check=True) == 1
    assert "findings ledger" in capsys.readouterr().err


@pytest.mark.parametrize("status", ["fixed", "verified", "deferred"])
def test_code_mode_must_fix_closed_in_the_ledger_still_passes(tmp_path, status):
    """#30 review B1: a rescore that still lists a fixed finding must not reopen R2."""
    _session_and_lean_record(tmp_path, mode="code")
    row = ledger_row("cd-1-F1", **{**BENCH, "status": status})
    if status == "deferred":
        row["ticket"] = "#99"
    if status == "fixed":
        row["fixed_in"] = "0" * 40
    write_ledger(tmp_path / "findings.jsonl", row)
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING,
           findings=[{"id": "cd-1-F1", "severity": "must-fix"}])
    assert run(tmp_path, mode="code", check=True) == 0


@pytest.mark.parametrize("finding", [
    {"id": "cd-1-F1", "severity": "must-fix"},
    {"id": "cd-1-F1", "severity": "HIGH"},
    {"id": "cd-1-F1", "must_fix": True},
])
def test_code_mode_every_must_fix_shape_needs_a_ledger_row(tmp_path, finding):
    """#30 review B2: the skill says severity must-fix; the gate read only must_fix."""
    _session_and_lean_record(tmp_path, mode="code")
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING, findings=[finding])
    assert run(tmp_path, mode="code", check=True) == 1


def test_code_mode_without_must_fix_findings_ignores_a_corrupt_ledger(tmp_path):
    """#30 review nit: another task's corrupt ledger is not this scorecard's problem."""
    _session_and_lean_record(tmp_path, mode="code")
    (tmp_path / "findings.jsonl").write_text('{"id": "bad id"}\n', encoding="utf-8")
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING,
           findings=[{"id": "cd-1-F2", "severity": "nit"}])
    assert run(tmp_path, mode="code", check=True) == 0


# ADR-0008: R2 computes the DRY lists (scripts/test_shape.py) and fails on measured duplication.
# The peer's dry_measurement is optional; a mismatch is a note, not a verdict.


@pytest.mark.parametrize(("head", "entry"), [
    pytest.param("def make_thing(p):\n    return p\n" + THREE,
                 "helpers_reimplemented: tests/test_new.py: make_thing (tests/conftest.py: make_thing)", id="reimplemented"),
    pytest.param("import mod\n\n\ndef test_x():\n    assert mod.X\n",
                 "thin_files: tests/test_new.py (1 tests of scripts/mod.py, also tested by tests/test_mod.py)", id="thin"),
])
def test_code_mode_fails_on_measured_test_duplication(worktree, tmp_path, capsys, head, entry):
    _session_and_lean_record(tmp_path, mode="code")
    write_files(worktree, {"tests/test_new.py": head})
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING)
    assert run(tmp_path, mode="code", check=True) == 1
    assert entry in capsys.readouterr().err


def test_code_mode_fails_on_an_unsupported_layout_when_the_config_says_fail(worktree, tmp_path, capsys):
    project_config(worktree, {"test_shape": {"unsupported": "fail", "layout": {"conftest": []}}})
    _session_and_lean_record(tmp_path, mode="code")
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING)
    assert run(tmp_path, mode="code", check=True) == 1
    assert ("helpers_reimplemented: no conftest.py under the test directories: set test_shape.layout.conftest"
            in capsys.readouterr().err)


def test_code_mode_writes_the_computed_lists_and_notes_a_peer_mismatch(worktree, tmp_path, capsys):
    _session_and_lean_record(tmp_path, mode="code")
    write_files(worktree, {"tests/test_new.py": "from conftest import make_thing\n" + THREE})
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING)
    assert run(tmp_path, mode="code") == 0
    crumb = json.loads((tmp_path / "gates" / "test-benchmark-gate-code.json").read_text(encoding="utf-8"))
    assert crumb["test_shape"]["helpers_reused"] == ["tests/test_new.py: make_thing"]
    assert [(m["measure"], m["verdict"]) for m in crumb["test_shape"]["measures"]] == [
        ("helpers_reimplemented", "pass"), ("thin_files", "pass")]
    assert crumb["dry_mismatch"] == ["dry_measurement.helpers_reused differs from the computed list: "
                                     "peer only ['tests/conftest.py: make_session'], gate only ['tests/test_new.py: make_thing']"]
    assert "dry_measurement.helpers_reused differs" in capsys.readouterr().out


@pytest.mark.parametrize(("dry", "note"), [
    pytest.param(None, None, id="absent"),
    pytest.param(["tests/x.py: _run"], "not three lists", id="not-an-object"),
    pytest.param({"helpers_reimplemented": [], "helpers_reused": []}, "not three lists", id="no-thin-files"),
])
def test_the_peer_dry_measurement_is_optional(tmp_path, capsys, dry, note):
    _session_and_lean_record(tmp_path, mode="code")
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING, dry_measurement=dry)
    assert run(tmp_path, mode="code", check=True) == 0
    assert (note in capsys.readouterr().out) if note else True


def test_code_mode_fails_closed_when_the_change_cannot_be_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CADENCE_BASE_REF", "nope")
    _session_and_lean_record(tmp_path, mode="code")
    _bench(tmp_path, "cd-1_code.json", adversarial_inputs=[ENTRY], scores=PASSING)
    assert run(tmp_path, mode="code", check=True) == 1
    assert "test shape cannot be measured: base ref nope does not resolve" in capsys.readouterr().err


def test_the_skill_names_each_key_that_the_gate_cross_checks():
    skill = (ROOT / "skills" / "test-benchmark" / "SKILL.md").read_text(encoding="utf-8")
    assert all(f"`{key}`" in skill for key in DRY_MEASUREMENT_KEYS)
