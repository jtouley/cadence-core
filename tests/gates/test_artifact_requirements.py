"""Unit tests for artifact_requirements provenance matching (plan D2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from artifact_requirements import (  # noqa: E402
    check_plan_create_artifacts,
    check_plan_review_verdict,
    check_required_before_execution,
    clear_entry_invariant_ok,
    entry_invariant_ok_path,
    file_matches_markers,
    require_active_session,
    require_artifact,
    write_entry_invariant_ok,
)


def test_file_matches_markers_text_bold_normalized(tmp_path):
    path = tmp_path / "review.md"
    path.write_text("**RUN_BY_COMMAND:** plan-review\n", encoding="utf-8")
    assert file_matches_markers(path, ("RUN_BY_COMMAND: plan-review",))


def test_file_matches_markers_json_key_value(tmp_path):
    path = tmp_path / "bench.json"
    path.write_text(
        json.dumps({"RUN_BY_SKILL": "test-benchmark", "phase": "plan"}),
        encoding="utf-8",
    )
    assert file_matches_markers(path, ("RUN_BY_SKILL: test-benchmark",))


def test_require_artifact_json_key_value_markers_pass(tmp_path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "t_plan.json").write_text(
        json.dumps({"RUN_BY_SKILL": "test-benchmark"}),
        encoding="utf-8",
    )
    assert (
        require_artifact(
            tmp_path,
            relative_globs=("benchmarks/*_plan.json",),
            markers=("RUN_BY_SKILL: test-benchmark",),
            label="test-benchmark-plan",
        )
        == []
    )


def test_plan_create_accepts_skill_or_command_form(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "a.plan.md").write_text("RUN_BY_SKILL: plan-create\n", encoding="utf-8")
    assert check_plan_create_artifacts(tmp_path, tmp_path) == []

    plans2 = tmp_path / "wt" / ".cursor" / "plans"
    plans2.mkdir(parents=True)
    (plans2 / "b.plan.md").write_text("RUN_BY_COMMAND: plan-create\n", encoding="utf-8")
    empty_ctx = tmp_path / "empty_ctx"
    empty_ctx.mkdir()
    assert check_plan_create_artifacts(empty_ctx, tmp_path / "wt") == []


def test_entry_invariant_ok_write_and_clear(tmp_path):
    path = write_entry_invariant_ok(
        tmp_path,
        pipeline_run_id="run-1",
        task_id="t1",
        written_at="2026-01-01T00:00:00Z",
    )
    assert path == entry_invariant_ok_path(tmp_path, "run-1")  # cd-76: the marker names the run
    assert path.is_file()
    clear_entry_invariant_ok(tmp_path, "run-1")
    assert not path.is_file()


def test_require_active_session_accepts_active_cadence(tmp_path):
    (tmp_path / "cadence-session.json").write_text(
        json.dumps({"active_skill": "cadence"}), encoding="utf-8"
    )
    assert require_active_session(tmp_path) == []


def test_check_required_missing_adversarial_fails(tmp_path):
    (tmp_path / "cadence-session.json").write_text(
        json.dumps({"active_skill": "cadence", "task_id": "t", "pipeline_run_id": "r"}),
        encoding="utf-8",
    )
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "t.plan.md").write_text("RUN_BY_SKILL: plan-create\n", encoding="utf-8")
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "plan_t.md").write_text(
        "RUN_BY_COMMAND: plan-review\n", encoding="utf-8"
    )
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "t_plan.json").write_text(
        json.dumps({"RUN_BY_SKILL": "test-benchmark"}), encoding="utf-8"
    )
    (tmp_path / "deferrals").mkdir()
    (tmp_path / "deferrals" / "t.json").write_text(
        json.dumps({"RUN_BY_SCRIPT": "deferral_adjudication_check.py"}),
        encoding="utf-8",
    )
    errors = check_required_before_execution(tmp_path, worktree=tmp_path)
    assert errors
    assert any("adversarial" in e for e in errors)


def test_plan_review_verdict_not_ready_blocks(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "plan_x.md").write_text(
        "RUN_BY_COMMAND: plan-review\n**Verdict:** NOT READY\n", encoding="utf-8"
    )
    errors = check_plan_review_verdict(tmp_path)
    assert errors and "NOT READY" in errors[0]


def test_plan_review_verdict_ready_to_execute_passes(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "plan_x.md").write_text(
        "RUN_BY_COMMAND: plan-review\n**Verdict:** READY TO EXECUTE\n", encoding="utf-8"
    )
    assert check_plan_review_verdict(tmp_path) == []
