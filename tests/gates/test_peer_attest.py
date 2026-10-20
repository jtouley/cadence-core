"""peer_attest: the writer paths, the newest attest row, and the join rows (cd-63, gh-66)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import code_paths

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import peer_attest  # noqa: E402
import peer_models  # noqa: E402


# --- peer_attest_writer_paths: cd-63: peer_attest.writer_paths gives distinct (agent, path) pairs, orchestrators excluded.

def test_writer_paths(tmp_path):
    for agent, rel, sha in [("a1", "adversarial/x.md", "s1"), ("a1", "adversarial/x.md", "s2"),
                            ("a2", "adversarial/x.md", "s3"), ("orch", "adversarial/x_r2.md", "s4"),
                            ("a1", "reviews/plan_x.md", "s5")]:
        peer_attest.record(tmp_path, "run-1", rel, agent, sha)
    pairs = peer_attest.writer_paths(tmp_path, "run-1", "adversarial/*.md", frozenset({"orch"}))
    assert pairs == {("a1", "adversarial/x.md"), ("a2", "adversarial/x.md")}


def test_the_any_run_reader_skips_call_and_join_logs(tmp_path):
    """gh-66 PD12 (cd-66-F24 b): a call or join row that looks like an attest row never attests a file."""
    from verdict_join import peer_call, peer_join

    review = tmp_path / "reviews" / "plan_x.md"
    review.parent.mkdir(parents=True)
    review.write_text("Verdict: READY TO EXECUTE\n", encoding="utf-8")
    look_alike = {"path": "reviews/plan_x.md", "sha256": peer_attest.sha256(review.read_bytes()), "agent_id": "peer-1"}
    peer_call(tmp_path, "run-1", stage="plan-review", tool_use_id="toolu_1", **look_alike)
    peer_join(tmp_path, "run-1", tool_use_id="toolu_1", **look_alike)
    assert peer_attest.attested_any(tmp_path, review) is False


# --- gh-66: the writer's stage (call row -> join row -> attest row) ---------------------------

import pytest  # noqa: E402
from verdict_join import peer_call, peer_join, peer_wrote, tamper, verdict_join_session  # noqa: E402

REVIEW = "reviews/plan_x.md"
P3 = frozenset({"plan-review"})


def test_writer_stage_is_the_stage_of_the_joined_allowed_call(tmp_path):
    peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review")
    stage, call, cause = peer_attest.writer_stage(tmp_path, "a")
    assert (stage, call["stage"], cause) == ("plan-review", "plan-review", "")


@pytest.mark.parametrize(("switch", "cause"), [
    ({"join": False}, "no join row: call the peer again from the orchestrator"),
    ({"call": False}, "no call row for the join (tool_use_id toolu_a_plan-review)"),
    ({"decision": "deny"}, "the peer call was denied"),
])
def test_each_cause_has_its_own_reason(tmp_path, switch, cause):
    review = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review", **switch)
    assert peer_attest.writer_stage(tmp_path, "a")[0] is None
    assert peer_attest.writer_stage(tmp_path, "a")[2] == cause
    ok, reason = peer_attest.stage_attested(tmp_path, "run-1", review, P3)
    assert (ok, reason) == (False, f"{REVIEW}: {cause}")
    assert "rewrite" not in reason and "Write or Edit" not in reason


def test_a_wrong_stage_names_the_writer_and_the_accepted_stages(tmp_path):
    review = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-update")
    ok, reason = peer_attest.stage_attested(tmp_path, "run-1", review, frozenset({"plan-review", "x"}))
    assert (ok, reason) == (False, f"{REVIEW} written by a plan-update peer; this gate accepts plan-review, x")


def test_no_attest_row_and_a_changed_file_give_no_reason(tmp_path):
    review = tmp_path / REVIEW
    review.parent.mkdir(parents=True)
    review.write_text("body", encoding="utf-8")
    assert peer_attest.stage_attested(tmp_path, "run-1", review, P3) == (False, "")
    tamper(peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review"))
    assert peer_attest.stage_attested(tmp_path, "run-1", review, P3) == (False, "")


@pytest.mark.parametrize("first_run, second_run", [
    pytest.param("run-1", "run-1", id="of_an_agent_wins"),
    # cd-66-F22: the earlier row by `at` is in the log whose name sorts last.
    pytest.param("run-b", "run-a", id="wins_over_two_logs"),
])
def test_the_first_join(tmp_path, first_run, second_run):
    peer_call(tmp_path, first_run, stage="plan-update", tool_use_id="t1")
    peer_call(tmp_path, second_run, stage="plan-review", tool_use_id="t2")
    peer_join(tmp_path, first_run, tool_use_id="t1", agent_id="a", at=1.0)
    peer_join(tmp_path, second_run, tool_use_id="t2", agent_id="a", at=2.0)
    assert peer_attest.writer_stage(tmp_path, "a")[0] == "plan-update"


def test_an_edit_by_the_accepted_peer_owns_the_file(tmp_path):
    """D13: a wrong-stage write, then an accepted-stage write: the newest attest row decides."""
    peer_wrote(tmp_path, "run-1", REVIEW, agent_id="w", stage="plan-update", body="draft")
    review = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="r", stage="plan-review", body="final")
    assert peer_attest.stage_attested(tmp_path, "run-1", review, P3) == (True, "")


def test_writers_with_a_stage_set_count_only_that_stage(tmp_path):
    peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-update")
    peer_wrote(tmp_path, "run-1", "reviews/plan_x_c2.md", agent_id="b", stage="plan-review")
    assert peer_attest.writers(tmp_path, "run-1", "reviews/plan_*.md") == {"a", "b"}
    assert peer_attest.writers(tmp_path, "run-1", "reviews/plan_*.md", stages=P3) == {"b"}
    assert peer_attest.writer_paths(tmp_path, "run-1", "reviews/plan_*.md", stages=P3) == {("b", "reviews/plan_x_c2.md")}


def test_stage_attested_any_reads_every_run_log(tmp_path):
    review = peer_wrote(tmp_path, "run-x", REVIEW, agent_id="a", stage="plan-update")
    ok, reason = peer_attest.stage_attested_any(tmp_path, review, P3)
    assert not ok and "written by a plan-update peer" in reason
    review = peer_wrote(tmp_path, "run-y", REVIEW, agent_id="b", stage="plan-review")
    assert peer_attest.stage_attested_any(tmp_path, review, P3) == (True, "")


@pytest.mark.parametrize("log", ["calls", "joins"])
def test_a_corrupt_line_in_the_call_or_join_log_is_skipped(tmp_path, log):
    """cd-66-F21 (PD23): a truncated append does not raise."""
    review = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review")
    path = tmp_path / "attest" / f"run-1.{log}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"tool_use_id": "toolu_x", "ag\n["a list"]\n')
    assert peer_attest.stage_attested(tmp_path, "run-1", review, P3) == (True, "")
    other = peer_wrote(tmp_path, "run-1", "reviews/plan_y.md", agent_id="b", stage="plan-review",
                       **({"call": False} if log == "calls" else {"join": False}))
    ok, reason = peer_attest.stage_attested(tmp_path, "run-1", other, P3)
    assert not ok
    assert ("no call row for the join" if log == "calls" else "no join row") in reason


# --- MF-6: the stage reason travels by a stateless reader, never by the filter ---------------

def test_the_stage_filter_has_no_reason_and_rejected_keeps_its_meaning(tmp_path):
    import artifact_requirements as ar

    verdict_join_session(tmp_path)
    good = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review")
    bad = peer_wrote(tmp_path, "run-1", "reviews/plan_y.md", agent_id="b", stage="plan-update")
    stage_filter = ar.peer_written_filter(tmp_path, slug="plan-review")
    combined = ar._all_of(ar.Rejected("x"), stage_filter)
    assert combined(good) is False and combined.reason == "x"
    assert stage_filter(bad) is False
    assert getattr(stage_filter, "reason", None) is None
    assert ar._all_of(stage_filter, lambda _p: True)(good) is True
    first = ar.stage_rejections(tmp_path, [good, bad], slug="plan-review")
    assert first == ar.stage_rejections(tmp_path, [good, bad], slug="plan-review")
    assert first == ["plan-review: reviews/plan_y.md written by a plan-update peer; this gate accepts plan-review"]


def test_a_changed_file_gives_no_stage_line(tmp_path):
    import artifact_requirements as ar

    verdict_join_session(tmp_path)
    review = tamper(peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review"))
    assert ar.stage_rejections(tmp_path, [review], slug="plan-review") == []


@pytest.mark.parametrize("decision", ["deny", "maybe", None])
def test_only_an_allow_decision_gives_a_stage(tmp_path, decision):
    review = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review", decision=decision)
    assert peer_attest.writer_stage(tmp_path, "a")[0] is None
    assert peer_attest.stage_attested(tmp_path, "run-1", review, P3) == (False, f"{REVIEW}: the peer call was denied")


def test_the_writer_own_call_model_must_match_the_configured_model(tmp_path):
    """#59: the model of the call that started the writer, not of any allowed call."""
    review = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review", model="sonnet")
    ok, reason = peer_attest.stage_attested(tmp_path, "run-1", review, P3, models={"plan-review": "opus"})
    assert (ok, reason) == (False, f"{REVIEW}: the plan-review peer call used model sonnet; peer_models sets opus")
    assert peer_attest.stage_attested(tmp_path, "run-1", review, P3, models={"plan-review": "sonnet"}) == (True, "")


@pytest.mark.parametrize("bad_id", [None, ""])
def test_rows_with_no_tool_use_id_never_join(tmp_path, bad_id):
    """PR #145 R2 F44: a malformed host payload must not join a call to an agent."""
    review = peer_wrote(tmp_path, "run-1", REVIEW, agent_id="a", stage="plan-review", tool_use_id=bad_id or "",
                        join=False, call=False)
    peer_call(tmp_path, "run-1", stage="plan-review", tool_use_id=bad_id)
    peer_join(tmp_path, "run-1", tool_use_id=bad_id, agent_id="a")
    assert peer_attest.writer_stage(tmp_path, "a")[0] is None
    assert peer_attest.stage_attested(tmp_path, "run-1", review, P3)[0] is False


# --- attest_newest_row_and_join_agent: R1 on PR #145: the newest attest row on a timestamp tie, and a join row with no agent (gh-66).
#
# `_newest` took the row with the greatest `at`. On a tie, `max` keeps the first row, so a
# restored older version of a file was accepted. The old `attested` took the last row in
# file order. A join row whose `agent_id` is missing must not count as a started peer.

RUN = "11111111-2222-4333-8444-555555555555"


def _attest_rows(context: Path, rel: str, versions: list[tuple[str, bytes]], at: float) -> None:
    log = context / "attest" / f"{RUN}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        for agent, body in versions:
            fh.write(json.dumps({"path": rel, "agent_id": agent, "sha256": peer_attest.sha256(body), "at": at}) + "\n")


def test_on_a_timestamp_tie_the_later_row_in_the_log_is_the_newest(tmp_path):
    context = tmp_path / ".context"
    report = context / "reviews" / "plan_x.md"
    report.parent.mkdir(parents=True)
    _attest_rows(context, "reviews/plan_x.md", [("a", b"v1"), ("a", b"v2")], at=1000.0)
    report.write_bytes(b"v2")
    assert peer_attest.attested(context, RUN, report)
    assert peer_attest.attested_any(context, report)
    report.write_bytes(b"v1")  # a restored older version is not the latest write
    assert not peer_attest.attested(context, RUN, report)
    assert not peer_attest.attested_any(context, report)


def test_a_join_row_with_no_agent_does_not_make_an_allowed_call_count(tmp_path):
    context = tmp_path / ".context"
    peer_attest.record_call_row(context, RUN, {"tool_use_id": "t1", "stage": "plan-create", "decision": "allow"})
    peer_attest.record_join(context, RUN, {"tool_use_id": "t1", "agent_id": None, "at": 1.0})
    assert peer_models._joined_allowed_calls(context, RUN, "plan-create") == []
    peer_attest.record_join(context, RUN, {"tool_use_id": "t1", "agent_id": "agent-a", "at": 2.0})
    assert [r["tool_use_id"] for r in peer_models._joined_allowed_calls(context, RUN, "plan-create")] == ["t1"]
