"""A peer stage gate with a configured model passes only on hook evidence of that model (cd-58 D4).

The evidence is an `allow` row in .context/attest/<run>.models.jsonl, written by the
handoff hook. A `begin` record of subagent_provenance.py is the orchestrator's own
claim and never counts. Denied rows do not count, so a corrected retry passes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import code_paths, git, git_repo, head_sha, make_session, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import gate_common  # noqa: E402
import peer_models  # noqa: E402
import test_benchmark_gate  # noqa: E402
from artifact_requirements import check_required_before_execution, require_lean_delegation  # noqa: E402
from prompt_shape import write_delegation_record  # noqa: E402

code_paths()
from verdict_join import peer_call, peer_join, peer_wrote, verdict_join_session  # noqa: E402

OPUS = {"plan-review": "opus"}
NO_ROW = "no allowed peer call for"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    return repo / ".context"


def session(ctx: Path, value=OPUS, run: str = "run-1") -> None:
    make_session(ctx, workspace=str(ctx.parent), pipeline_run_id=run, host="claude", peer_models=value)


def row(ctx: Path, stage="plan-review", model="opus", decision="allow", run="run-1", tool_use_id="toolu_1",
        join_agent: str | None = None) -> None:
    peer_models.record_call(ctx, run, {"stage": stage, "block": "P3", "model": model, "subagent_type": "general-purpose",
                                       "decision": decision, "tool_use_id": tool_use_id, "agent_id": None,
                                       "session_id": "chat-1", "at": 1.0})
    if join_agent is not None:  # gh-66: the same call as a call row and a join row
        delegation = {v: k for k, v in peer_models.SLUG_FOR_STAGE.items() if v and k != v}.get(stage, stage)
        peer_call(ctx, run, stage=delegation, tool_use_id=tool_use_id, decision=decision, model=model, model_slug=stage)
        peer_join(ctx, run, tool_use_id=tool_use_id, agent_id=join_agent)


# --- require_peer_model -------------------------------------------------------------------------


def test_no_row_fails_and_names_the_slug_and_model(ctx):
    session(ctx)
    [error] = peer_models.require_peer_model(ctx, "plan-review")
    assert NO_ROW in error and "plan-review" in error and "opus" in error


def test_an_allowed_row_of_another_model_fails(ctx):
    session(ctx)
    row(ctx, model="sonnet", tool_use_id="toolu_9")
    [error] = peer_models.require_peer_model(ctx, "plan-review")
    assert "sonnet" in error and "toolu_9" in error


@pytest.mark.parametrize("case", ["deny_only", "other_slug", "older_run"])
def test_rows_that_do_not_count(ctx, case):
    session(ctx)
    if case == "deny_only":
        row(ctx, decision="deny")
    elif case == "other_slug":
        row(ctx, stage="validate")
    else:
        row(ctx, run="run-0")
    [error] = peer_models.require_peer_model(ctx, "plan-review")
    assert NO_ROW in error


def test_right_and_other_model_fails_on_the_other(ctx):
    session(ctx)
    row(ctx, model="opus")
    row(ctx, model="sonnet", tool_use_id="toolu_2")
    [error] = peer_models.require_peer_model(ctx, "plan-review")
    assert "sonnet" in error


def test_corrupt_rows_fail_closed(ctx):
    session(ctx)
    path = peer_models.rows_path(ctx, "run-1")
    path.parent.mkdir(parents=True)
    path.write_text('not json\n["a list"]\n{"stage": "plan-review", "decision": "maybe", "model": "opus"}\n',
                    encoding="utf-8")
    [error] = peer_models.require_peer_model(ctx, "plan-review")
    assert NO_ROW in error
    row(ctx)
    assert peer_models.require_peer_model(ctx, "plan-review") == []


def test_deny_then_retry_passes(ctx):
    session(ctx)
    row(ctx, model="sonnet", decision="deny")
    row(ctx, model="opus")
    assert peer_models.require_peer_model(ctx, "plan-review") == []


def test_a_begin_record_alone_fails(ctx):
    session(ctx)
    write_delegation_record(ctx, run_id="run-1", stage="plan-review", command="/plan-review plan.md", source="begin")
    assert peer_models.require_peer_model(ctx, "plan-review")


@pytest.mark.parametrize("slug", peer_models.PEER_MODEL_SLUGS)
def test_no_peer_models_returns_nothing(ctx, slug):
    make_session(ctx, workspace=str(ctx.parent), host="claude")
    assert peer_models.require_peer_model(ctx, slug) == []
    session(ctx, {})
    assert peer_models.require_peer_model(ctx, slug) == []


@pytest.mark.parametrize("value", ["opus", ["opus"], None])
def test_session_not_a_map(ctx, capsys, value):
    session(ctx, value)
    [error] = peer_models.require_peer_model(ctx, "plan-review")
    assert "plan-review" in error and "peer_models" in error
    assert gate_common.run_artifact_gate("plan-review-gate", ctx, check=True) == 1
    assert "peer_models" in capsys.readouterr().err


# --- gate entry points --------------------------------------------------------------------------


def _gate(slug: str, ctx: Path) -> int:
    if slug == "plan-create":
        return gate_common.run_plan_create_gate(ctx, check=True)
    if slug in ("test-benchmark-plan", "test-benchmark-pr"):
        return test_benchmark_gate.run(ctx, mode="plan" if slug.endswith("plan") else "code", check=True)
    gate = {"validate": "validate-gate", "plan-review": "plan-review-gate",
            "adversarial-review": "adversarial-review-gate", "pr-review-local": "pr-review-local-gate"}[slug]
    return gate_common.run_artifact_gate(gate, ctx, check=True)


@pytest.mark.parametrize("slug", peer_models.PEER_MODEL_SLUGS)
def test_each_gate_fails_with_no_model_row(ctx, capsys, slug):
    session(ctx, {slug: "opus"})
    assert _gate(slug, ctx) == 1
    err = capsys.readouterr().err
    assert f"{slug}: peer_models sets opus" in err and NO_ROW in err


def test_plan_create_gate_keeps_its_artifact_errors_when_the_model_row_passes(ctx, capsys):
    """R2 F30: `errors = require_peer_model(...)` would drop the plan errors and pass P1 with no plan."""
    session(ctx, {"plan-create": "haiku"})
    row(ctx, stage="plan-create", model="haiku")
    assert peer_models.require_peer_model(ctx, "plan-create") == []
    assert gate_common.run_plan_create_gate(ctx, check=True, worktree=ctx.parent) == 1
    err = capsys.readouterr().err
    assert "FAIL  plan-create-gate" in err and "peer_models" not in err


def test_e1_names_a_missing_plan_create_model_row(ctx):
    session(ctx, {"plan-create": "haiku"})
    errors = check_required_before_execution(ctx, worktree=ctx.parent)
    assert any("plan-create: peer_models sets haiku" in e for e in errors)


def test_e1_names_a_missing_review_model_row(ctx):
    session(ctx, {"plan-create": "haiku", "plan-review": "opus"})
    row(ctx, stage="plan-create", model="haiku")
    for stage, command in (("plan-create", "/plan-create x"), ("plan-review", "/plan-review x"),
                           ("de-adversarial-reviewer", "/de-adversarial-reviewer plan-gate x"),
                           ("test-benchmark-plan", "/test-benchmark plan x")):
        write_delegation_record(ctx, run_id="run-1", stage=stage, command=command, source="test")
    errors = check_required_before_execution(ctx, worktree=ctx.parent)
    model_errors = [e for e in errors if "peer_models sets" in e]
    assert model_errors == [f"plan-review: peer_models sets opus, but {NO_ROW} plan-review is recorded in this run. "
                            'Call the peer through the Agent tool with model: "opus".']


def test_lean_and_model_errors_are_both_named(ctx):
    session(ctx)
    errors = require_lean_delegation(ctx, "plan-review")
    assert any("missing lean delegation record" in e for e in errors)
    assert any(NO_ROW in e for e in errors)


def test_lean_short_circuit(ctx):
    session(ctx)
    row(ctx)
    assert require_lean_delegation(ctx, "plan-review") == ["plan-review: missing lean delegation record"]


def test_lean_stage_names_map_to_slugs():
    assert peer_models.SLUG_FOR_STAGE["vibe-test"] == "validate"
    assert peer_models.SLUG_FOR_STAGE["de-adversarial-reviewer"] == "adversarial-review"
    assert peer_models.SLUG_FOR_STAGE["plan-review"] == "plan-review"


# --- snapshot (subprocess: SKILL_ROOT is read at import) ----------------------------------------


@pytest.mark.parametrize("model, passes", [("opus", True), ("haiku", False)])
def test_snapshot_gate_uses_the_session_not_the_live_config(ctx, make_root, model, passes):
    session(ctx)
    row(ctx, model=model)
    root = make_root(peer_models={"plan-review": "haiku"})
    import cadence_config

    live = cadence_config.load(root, ctx.parent)
    assert live.get("peer_models") == {"plan-review": "haiku"} and live.source("peer_models") == "default"
    proc = run_py("scripts/plan_review_gate.py", "--check", cwd=ctx.parent, home=root)
    model_lines = [line for line in proc.stderr.splitlines() if "peer_models" in line]
    if passes:
        assert model_lines == []
    else:
        assert model_lines and "opus" in model_lines[0]


# --- gh-66 (#59): on a verdict_join run, the model check reads joined call rows ---------------

REVIEW = "RUN_BY_COMMAND: plan-review\nTicket: gh-1\n\nVerdict: READY TO EXECUTE\n"


def joined_session(ctx: Path, value) -> None:
    verdict_join_session(ctx, workspace=str(ctx.parent), peer_models=value)
    write_delegation_record(ctx, run_id="run-1", stage="plan-review", command="/plan-review x", source="PreToolUse")


@pytest.mark.parametrize(("model", "decision", "passes"), [("opus", "allow", True), ("sonnet", "allow", False),
                                                          ("opus", "deny", False)])
def test_p3_needs_the_review_from_an_allowed_call_on_the_configured_model(ctx, capsys, model, decision, passes):
    joined_session(ctx, OPUS)
    row(ctx, model=model, decision=decision, join_agent="a")
    peer_attest_path = ctx / "reviews" / "plan_x.md"
    peer_attest_path.parent.mkdir(parents=True, exist_ok=True)
    peer_attest_path.write_text(REVIEW, encoding="utf-8")
    import peer_attest

    peer_attest.record(ctx, "run-1", "reviews/plan_x.md", "a", peer_attest.sha256(peer_attest_path.read_bytes()))
    code = gate_common.run_artifact_gate("plan-review-gate", ctx, check=True)
    err = capsys.readouterr().err
    assert (code == 0) is passes, err
    if model == "sonnet":
        assert "sonnet" in err and "opus" in err
    if decision == "deny":
        assert "the peer call was denied" in err


@pytest.mark.parametrize("evidence", ["none", "call-only", "call-and-join"])
def test_p1_needs_a_joined_plan_create_call(ctx, evidence):
    verdict_join_session(ctx, workspace=str(ctx.parent), peer_models={"plan-create": "opus"})
    if evidence == "call-only":  # #59: an allowed call row, and no peer ever started for it
        row(ctx, stage="plan-create", tool_use_id="t1")
        peer_call(ctx, "run-1", stage="plan-create", tool_use_id="t1", model="opus", model_slug="plan-create")
    elif evidence == "call-and-join":
        row(ctx, stage="plan-create", tool_use_id="t1", join_agent="p")
    errors = peer_models.require_peer_model(ctx, "plan-create")
    assert (errors == []) is (evidence == "call-and-join"), errors


def test_r4_takes_an_allowed_pr_review_update_call_under_peer_models(tmp_path, monkeypatch):
    import final_consolidate

    repo = git_repo(tmp_path / "r4")
    git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    monkeypatch.chdir(repo)
    ctx = repo / ".context"
    verdict_join_session(ctx, workspace=str(repo), peer_models={"pr-review-local": "opus"})
    head = head_sha(repo)
    peer_wrote(ctx, "run-1", "reviews/pr_x.md", agent_id="u", stage="pr-review-update",
               body=f"RUN_BY_COMMAND: pr-review-update\nLast reviewed commit: {head}\n")
    assert final_consolidate.head_review_problems(ctx, repo) == []


def test_the_writer_own_call_must_use_the_configured_model(ctx, capsys):
    """#59: an allowed opus call that started another agent does not cover a writer started on sonnet."""
    joined_session(ctx, OPUS)
    row(ctx, model="opus", tool_use_id="t_other")
    peer_wrote(ctx, "run-1", "reviews/plan_x.md", agent_id="w", stage="plan-review", model="sonnet", body=REVIEW)
    assert gate_common.run_artifact_gate("plan-review-gate", ctx, check=True) == 1
    err = capsys.readouterr().err
    assert "sonnet" in err and "opus" in err
