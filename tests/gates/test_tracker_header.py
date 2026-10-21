"""Vibe-test ticket lines follow the adapter of the resolved config, not a Linear default."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import vibe_reports as vr  # noqa: E402
from conftest import RUN_A, RUN_B, install_config, project_config  # noqa: E402
from gate_common import run_artifact_gate  # noqa: E402
from tracker_header import (  # noqa: E402
    configured_adapter_name,
    load_configured_adapters,
    marked_report_header_errors,
    tracker_header_problems,
)

GITHUB = {
    "name": "github-issues",
    "ticket_pattern": r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+",
}


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """The config is resolved for the cwd: the workspace is tmp_path, not this checkout."""
    monkeypatch.chdir(tmp_path)


def test_configured_adapter_name_reads_config(tmp_path):
    install_config(tmp_path)
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "only-zzz.json").write_text(
        json.dumps({"name": "only-zzz", "ticket_pattern": r"^zzz$"}),
        encoding="utf-8",
    )
    project_config(tmp_path, {"adapter": "only-zzz"})
    assert configured_adapter_name(tmp_path, tmp_path) == "only-zzz"
    assert configured_adapter_name(tmp_path) == "only-zzz"  # workspace None: the cwd


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(json.dumps({"adapter": ""}), "adapter must not be empty", id="empty"),
        pytest.param("{", "not valid JSON", id="unparsable"),
        pytest.param("[]", "must be a JSON object", id="not_object"),
        pytest.param(json.dumps({"adapter": 1}), "adapter must be string", id="not_string"),
    ],
)
def test_load_configured_adapters_rejects_a_bad_layer_and_names_the_file(tmp_path, text, expected):
    """A bad config layer is an error that names the file; it never falls back to github-issues."""
    install_config(tmp_path)
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "github-issues.json").write_text(json.dumps(GITHUB), encoding="utf-8")
    project = tmp_path / ".cadence.json"
    project.write_text(text, encoding="utf-8")
    loaded = load_configured_adapters(tmp_path, tmp_path)
    assert isinstance(loaded, str)
    assert expected in loaded and str(project) in loaded


def test_load_configured_adapters_rejects_a_missing_adapter_file(tmp_path):
    install_config(tmp_path)
    project_config(tmp_path, {"adapter": "missing-adapter"})
    assert "configured adapter missing-adapter has no file" in load_configured_adapters(tmp_path, tmp_path)


def test_tracker_header_problems_linear_header_fails_for_github_adapter():
    text = "**Linear:** LINEAR_MCP_UNAVAILABLE\nRUN_BY_SKILL: vibe-test\n"
    problems = tracker_header_problems(text, "github-issues")
    assert any("Linear" in problem for problem in problems)


def test_tracker_header_problems_github_lines_pass():
    text = (
        "RUN_BY_SKILL: vibe-test\n"
        "**Tracker:** github-issues\n"
        "**Ticket:** #22\n"
    )
    assert tracker_header_problems(text, GITHUB) == []


def test_tracker_header_problems_skip_token_passes():
    text = (
        "**Tracker:** github-issues\n"
        "**Ticket:** TICKET_SKIPPED (github-issues cli missing)\n"
    )
    assert tracker_header_problems(text, GITHUB) == []


def test_tracker_header_problems_skip_without_adapter_name_fails():
    text = "**Tracker:** github-issues\n**Ticket:** TICKET_SKIPPED (none)\n"
    problems = tracker_header_problems(text, GITHUB)
    assert problems and "adapter" in problems[0]


def test_tracker_header_problems_rejects_foreign_ticket_id():
    text = "**Tracker:** github-issues\n**Ticket:** ENG-1\n"
    problems = tracker_header_problems(text, GITHUB)
    assert problems and "ticket_pattern" in problems[0]


def test_tracker_header_problems_linear_token_fails():
    text = (
        "**Tracker:** github-issues\n"
        "**Ticket:** LINEAR_TICKET_SKIPPED (no mcp)\n"
    )
    problems = tracker_header_problems(text, GITHUB)
    assert problems


def test_marked_report_header_errors_flags_every_report(tmp_path):
    install_config(tmp_path)  # the default adapter is github-issues
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "github-issues.json").write_text(
        json.dumps(GITHUB), encoding="utf-8"
    )
    vibe = tmp_path / "vibetest"
    vibe.mkdir()
    (vibe / "good.md").write_text(
        f"RUN_BY_SKILL: vibe-test\nPipeline-Run: {RUN_A}\n**Tracker:** github-issues\n**Ticket:** #22\n"
        "The body may mention LINEAR_MCP_UNAVAILABLE without failing.\n",
        encoding="utf-8",
    )
    (vibe / "bad.md").write_text(
        f"RUN_BY_SKILL: vibe-test\nPipeline-Run: {RUN_A}\n**Tracker:** linear\n**Ticket:** ENG-9\n",
        encoding="utf-8",
    )
    errors = marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=vr.now()))
    assert len(errors) == 1
    assert "bad.md" in errors[0]


def test_run_artifact_gate_validate_rejects_linear_header(tmp_path, monkeypatch, capsys):
    """A legacy run whose own report has a Linear header; only the header check fails it."""
    from prompt_shape import write_delegation_record

    monkeypatch.delenv("CLAUDECODE", raising=False)
    run = vr.session(RUN_A, activated=vr.now() - 600, host="cursor")
    (tmp_path / "cadence-session.json").write_text(json.dumps(run), encoding="utf-8")
    write_delegation_record(tmp_path, run_id=RUN_A, stage="vibe-test", command="/vibe-test docs/spec.md",
                            source="PreToolUse")
    vr.write_report(tmp_path / "vibetest", "t.md", run=RUN_A, header="**Linear:** LINEAR_MCP_UNAVAILABLE\n")
    assert run_artifact_gate("validate-gate", tmp_path, check=True) == 1
    err = capsys.readouterr().err
    assert "header names Linear" in err and "lean delegation" not in err


@pytest.mark.parametrize(
    "adapter_name",
    ["github-issues", "linear"],
)
def test_tracker_header_problems_accepts_configured_name(adapter_name):
    text = (
        f"**Tracker:** {adapter_name}\n"
        f"**Ticket:** TICKET_SKIPPED ({adapter_name} unavailable)\n"
    )
    assert tracker_header_problems(text, {"name": adapter_name}) == []


def _adapter_tree(root):
    install_config(root)  # the default adapter is github-issues
    (root / "adapters").mkdir()
    (root / "adapters" / "github-issues.json").write_text(json.dumps(GITHUB), encoding="utf-8")
    vibe = root / "vibetest"
    vibe.mkdir()
    return vibe


def test_marked_report_header_errors_skips_reports_from_other_runs(tmp_path):
    vibe = _adapter_tree(tmp_path)
    start = vr.now()
    (vibe / "this-run.md").write_text(
        f"RUN_BY_SKILL: vibe-test\nPipeline-Run: {RUN_A}\n**Tracker:** github-issues\n**Ticket:** #32\n",
        encoding="utf-8",
    )
    vr.write_report(vibe, "older-run.md", header="**Linear:** LINEAR_TICKET_SKIPPED\n", mtime=start - vr.HOUR)
    assert marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=start)) == []


def test_marked_report_header_errors_still_flags_a_bad_report_of_this_run(tmp_path):
    vibe = _adapter_tree(tmp_path)
    (vibe / "this-run.md").write_text(
        f"RUN_BY_SKILL: vibe-test\nPipeline-Run: {RUN_A}\n**Linear:** ENG-9\n", encoding="utf-8"
    )
    errors = marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=vr.now()))
    assert len(errors) == 1 and "this-run.md" in errors[0]


def test_a_report_written_after_activation_counts_as_this_runs(tmp_path):
    """Reports do not carry the run id (vibe-test asks only for Tracker/Ticket). Time decides."""
    import os
    import time

    vibe = _adapter_tree(tmp_path)
    (vibe / "older-run.md").write_text("RUN_BY_SKILL: vibe-test\n**Linear:** ENG-1\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(vibe / "older-run.md", (old, old))
    from datetime import datetime, timedelta, timezone

    activated = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    (vibe / "this-run.md").write_text(
        "RUN_BY_SKILL: vibe-test\n**Tracker:** github-issues\n**Ticket:** #32\n", encoding="utf-8"
    )
    run = {"active_skill": "cadence", "pipeline_run_id": RUN_A, "activated_at": activated}
    assert marked_report_header_errors(tmp_path, tmp_path, run) == []


# --- the header check judges only the reports of its own run --------------------------------


def _skill(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    _adapter_tree(root)
    return root


def test_t1_each_worktree_run_judges_only_its_own_report(tmp_path, two_worktree_runs):
    """Acceptance 1: B activated last; A's check judges A's report only, B's check B's only."""
    from pipeline_run_binding import run_for

    w = two_worktree_runs
    vibe = w.ctx / "vibetest"
    vr.write_report(vibe, "a.md", run=w.run_a)
    vr.write_report(vibe, "b.md", run=w.run_b, header=vr.LINEAR)
    root = _skill(tmp_path)
    assert marked_report_header_errors(w.ctx, root, run_for(w.ctx, cwd=w.a)) == []
    errors = marked_report_header_errors(w.ctx, root, run_for(w.ctx, cwd=w.b))
    assert len(errors) == 1 and errors[0].startswith("b.md: ")


def test_t1b_run_ids_compare_in_lower_case_and_in_every_label_form(tmp_path):
    """P7 cd-93-F01: the session id in upper case; the report uses another RUN_LABELS form."""
    vibe = _adapter_tree(tmp_path)
    vr.write_report(vibe, "own.md", run=RUN_A, label="pipeline_run_id", header=vr.LINEAR,
                    mtime=vr.now() - vr.HOUR)
    errors = marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A.upper(), activated=vr.now()))
    assert len(errors) == 1 and errors[0].startswith("own.md: ")


def test_t2_a_run_with_no_report_judges_no_old_report(tmp_path):
    """Acceptance 2: no fallback to every report."""
    vibe = _adapter_tree(tmp_path)
    start = vr.now()
    vr.write_report(vibe, "old1.md", header=vr.LINEAR, mtime=start - vr.HOUR)
    vr.write_report(vibe, "old2.md", header=vr.LINEAR, mtime=start - vr.HOUR)
    assert marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=start)) == []


def test_t3_a_bad_own_report_is_named(tmp_path):
    vibe = _adapter_tree(tmp_path)
    vr.write_report(vibe, "own.md", run=RUN_A, header=vr.LINEAR)
    errors = marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=vr.now()))
    assert len(errors) == 1 and errors[0].startswith("own.md: ") and "header names Linear" in errors[0]


def test_t4_an_old_report_of_another_ticket_does_not_fail_this_run(tmp_path):
    vibe = _adapter_tree(tmp_path)
    start = vr.now()
    vr.write_report(vibe, "other.md", header=vr.LINEAR, mtime=start - vr.HOUR)
    vr.write_report(vibe, "own.md", run=RUN_A)
    assert marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=start)) == []


def test_t5_a_report_naming_another_run_does_not_count_even_when_newer(tmp_path):
    """Acceptance 7 (G1): the run id decides before the activation time."""
    vibe = _adapter_tree(tmp_path)
    start = vr.now()
    vr.write_report(vibe, "theirs.md", run=RUN_B, header=vr.LINEAR, mtime=start + vr.HOUR)
    assert marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=start)) == []


def test_t6_no_run_gives_no_report_errors(tmp_path):
    """Acceptance 8 (D4): a valid adapter tree, bad reports, no session."""
    vibe = _adapter_tree(tmp_path)
    vr.write_report(vibe, "x.md", run=RUN_A, header=vr.LINEAR)
    vr.write_report(vibe, "y.md", header=vr.LINEAR)
    assert marked_report_header_errors(tmp_path, tmp_path, None) == []
    assert marked_report_header_errors(tmp_path, tmp_path, None, accept=lambda _p: True) == []  # R2 F01


@pytest.mark.parametrize("offset, named", [(-vr.HOUR, False), (0, True), (vr.HOUR, True)])
def test_t7_a_non_uuid_run_value_falls_back_to_activation_time(tmp_path, offset, named):
    """MF-3: `Pipeline-Run: run-9` names no run id, so time decides. At activation counts (R2 F02)."""
    vibe = _adapter_tree(tmp_path)
    start = float(int(vr.now()))
    vr.write_report(vibe, "r.md", run="run-9", header=vr.LINEAR, mtime=start + offset)
    errors = marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=start))
    assert (len(errors) == 1 and errors[0].startswith("r.md: ")) if named else errors == []


def test_t8_a_report_the_artifact_check_accepts_is_also_judged(tmp_path):
    """B-1 (D8): accept widens the judged set to what A5 accepts."""
    vibe = _adapter_tree(tmp_path)
    start = vr.now()
    vr.write_report(vibe, "cd-901_old.md", header=vr.LINEAR, mtime=start - vr.HOUR)
    run = vr.session(RUN_A, activated=start)
    assert marked_report_header_errors(tmp_path, tmp_path, run) == []
    errors = marked_report_header_errors(tmp_path, tmp_path, run, accept=lambda p: p.name == "cd-901_old.md")
    assert len(errors) == 1 and errors[0].startswith("cd-901_old.md: ") and "header names Linear" in errors[0]


def test_a_non_utf8_report_is_named_unreadable_not_a_traceback(tmp_path):
    """P6 r2 C-2, P7 cd-93-F02: fail closed with one line, no traceback."""
    vibe = _adapter_tree(tmp_path)
    (vibe / "bin.md").write_bytes(b"\xff\xfe")
    errors = marked_report_header_errors(tmp_path, tmp_path, vr.session(RUN_A, activated=vr.now()))
    assert len(errors) == 1 and errors[0].startswith("bin.md: ") and "unreadable" in errors[0]
