"""The P3 and P6 review loops: one loop helper, and each loop converges in its cycle limit (gh-25, cd-47 D10, cd-63)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from conftest import code_paths, git_repo, make_session, run_py

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import artifact_requirements as ar  # noqa: E402
from adversarial_loop import OTHER_RUN, RUN, Loop  # noqa: E402
from artifact_requirements import check_plan_review_verdict, plan_review_cycle_limit  # noqa: E402


# --- review_loop_helper: cd-63 Phase 1: one review-loop helper for P3 and P6 (cycles, newest verdict, carried section).

@pytest.fixture
def loop(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    return Loop(repo)


def test_p3_spec_golden_values(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    lp = Loop(repo, host="other")
    lp.history("/plan-review x", "/plan-review x", "/plan-review x", stage="plan-review")
    review = lp.ctx / "reviews" / "plan_gh-1_x.md"
    review.parent.mkdir(parents=True)
    review.write_text("RUN_BY_COMMAND: plan-review\nTicket: gh-1\n\nVerdict: READY WITH CHANGES\n", encoding="utf-8")
    assert ar.loop_cycles(lp.ctx, ar.P3_LOOP) == 3
    assert ar.plan_review_cycles(lp.ctx) == 3
    assert ar.select_newest_verdict(lp.ctx, ar.P3_LOOP) == (review, "READY WITH CHANGES")
    assert ar.select_plan_review(lp.ctx) == (review, "READY WITH CHANGES")


def test_p6_claude_count_is_distinct_attested_plan_gate_pairs(loop):
    loop.report(agent="a1")
    loop.report(agent="a2")  # same path, second agent
    assert ar.loop_cycles(loop.ctx, ar.P6_LOOP, loop.session) == 2
    loop.report(agent="a3", mode="spec-pressure", name=f"adversarial_{loop.slug}_spec-pressure.md")
    assert ar.loop_cycles(loop.ctx, ar.P6_LOOP, loop.session) == 2
    other = loop.report(agent="a4", round_n=2, attest=False)
    loop.attest(other, "a4", run=OTHER_RUN)
    assert ar.loop_cycles(loop.ctx, ar.P6_LOOP, loop.session) == 2


def test_p6_count_rows_for_agents_and_paths(loop):
    loop.report(agent="a1")
    loop.report(agent="a1", round_n=2)
    assert ar.loop_cycles(loop.ctx, ar.P6_LOOP, loop.session) == 2  # one agent, two files
    loop.report(agent="a1", round_n=2, body="### B-1. second edit")
    assert ar.loop_cycles(loop.ctx, ar.P6_LOOP, loop.session) == 2  # same agent, same file again


def test_p6_history_count_skips_spec_pressure_and_keeps_old_lines(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    lp = Loop(repo, host="other")
    lp.history("/de-adversarial-reviewer plan-gate p.md", "/de-adversarial-reviewer spec-pressure p.md", None)
    assert ar.loop_cycles(lp.ctx, ar.P6_LOOP, lp.session) == 2


def test_begin_writes_the_command_into_the_history(tmp_path):
    repo = git_repo(tmp_path / "r")
    lp = Loop(repo, host="other")
    for mode in ("plan-gate", "spec-pressure"):
        proc = run_py("scripts/subagent_provenance.py", "begin", "--stage", "de-adversarial-reviewer",
                      "--command", f"/de-adversarial-reviewer {mode} .context/plans/gh-1_x.plan.md", cwd=repo)
        assert proc.returncode == 0, proc.stderr
    lines = (lp.ctx / "subagent-runs" / RUN / "history.jsonl").read_text().splitlines()
    assert all('"command"' in line for line in lines)
    assert ar.loop_cycles(lp.ctx, ar.P6_LOOP, lp.session) == 1


def test_p6_newest_is_highest_round_then_mtime(loop):
    r3 = loop.report("APPROVE", round_n=3)
    r2 = loop.report("REQUEST CHANGES", round_n=2)
    os.utime(r3, (1000, 1000))
    os.utime(r2, (2000, 2000))
    assert ar.select_newest_verdict(loop.ctx, ar.P6_LOOP, loop.session) == (r3, "APPROVE")


@pytest.mark.parametrize(("name", "expected"), [
    ("adversarial_x_r7.md", 1),
    ("adversarial_x_r7_r2.md", 2),
    ("adversarial_x_r7_spec-pressure.md", None),
    ("adversarial_other.md", None),
])
def test_round_parse_is_anchored_to_the_slug(name, expected):
    assert ar.adversarial_round(Path(name), "x_r7") == expected


def test_carried_section_skips_fenced_headings(loop):
    heading = "## Carried adversarial findings (r.md @ 0123456789ab)"
    loop.write_plan(f"\n```\n{heading}\n- B-9 fenced\n```\n\n{heading}\n- B-1 -> Phase 1\n\n## Next\n- B-7\n")
    section = ar.carried_section(loop.ctx, heading, loop.session, exact=True)
    assert "B-1" in section and "B-9" not in section and "B-7" not in section


# --- plan_review_converges: Plan review converges in at most `plan_review_cycles` cycles (default five).
#
# After the limit, a READY WITH CHANGES verdict clears P9 only when the plan carries
# each open must-fix (MF-n) into a phase, under `## Carried review findings`: a test
# in E1 settles the edge instead of another prose review. NOT READY after the limit
# means the plan is wrong: record a hard stop.

PLAN_LIMIT = plan_review_cycle_limit()

PLAN = """RUN_BY_COMMAND: plan-create
Pipeline-Run: run-1
**Ticket:** gh-1

### Phase 1: parser
### Phase 2: wiring
"""
CARRIED = "\n## Carried review findings\n\n- MF-4 -> Phase 2 (test: payload cwd in another repo)\n- MF-5 -> Phase 1\n"


def _setup(tmp_path: Path, *, cycles: int, verdict: str, carried: bool) -> Path:
    repo = git_repo(tmp_path / "r")
    ctx = repo / ".context"
    make_session(ctx, workspace=str(repo))
    (ctx / "plans").mkdir(parents=True)
    (ctx / "plans" / "gh-1_x.plan.md").write_text(PLAN + (CARRIED if carried else ""), encoding="utf-8")
    for _ in range(cycles):
        proc = run_py("scripts/subagent_provenance.py", "begin",
                      "--stage", "plan-review", "--command", "/plan-review .context/plans/gh-1_x.plan.md", cwd=repo)
        assert proc.returncode == 0, proc.stderr
    (ctx / "reviews").mkdir(parents=True)
    (ctx / "reviews" / "plan_gh-1_x.md").write_text(
        f"RUN_BY_COMMAND: plan-review\nTicket: gh-1\n\n### MF-4: hole\n### MF-5: hole\n\nVerdict: {verdict}\n",
        encoding="utf-8",
    )
    os.chdir(repo)
    return repo


def test_before_the_limit_a_blocking_verdict_still_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=1, verdict="READY WITH CHANGES", carried=True)
    errors = check_plan_review_verdict(repo / ".context")
    assert errors and "/plan-update" in errors[0]


def test_at_the_limit_uncarried_findings_block_and_name_the_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=PLAN_LIMIT, verdict="READY WITH CHANGES", carried=False)
    errors = check_plan_review_verdict(repo / ".context")
    assert errors and "Carried review findings" in errors[0]
    assert "MF-4" in errors[0] and "MF-5" in errors[0]


def test_at_the_limit_carried_findings_clear_the_verdict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=PLAN_LIMIT, verdict="READY WITH CHANGES", carried=True)
    assert check_plan_review_verdict(repo / ".context") == []


def test_not_ready_at_the_limit_names_the_hard_stop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=PLAN_LIMIT, verdict="NOT READY", carried=True)
    errors = check_plan_review_verdict(repo / ".context")
    assert errors and "--hard-stop retries" in errors[0]


def test_the_stop_guard_says_carry_not_review_again_at_the_limit(tmp_path, monkeypatch, run_hook):
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=PLAN_LIMIT, verdict="READY WITH CHANGES", carried=False)
    record = repo / ".context" / "subagent-runs" / "run-1" / "plan-review.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["status"] = "complete"  # the peer returned; it does not hold the door open
    record.write_text(json.dumps(data), encoding="utf-8")
    code, out, _ = run_hook("pipeline_stop_guard.py", repo, {"cwd": str(repo)}, "claude")
    reason = json.loads(out)["reason"]
    assert "Carried review findings" in reason and "/plan-review" not in reason


def _attest_plan_review(repo: Path, agents: list[str]) -> None:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    import peer_attest

    for agent in agents:
        peer_attest.record(repo / ".context", "run-1", "reviews/plan_gh-1_x.md", agent, "0" * 64)


def test_on_claude_cycles_are_distinct_review_peers_not_begin_calls(tmp_path, monkeypatch):
    """The orchestrator runs `begin` itself; empty begins up to the limit must not reach it."""
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=PLAN_LIMIT, verdict="READY WITH CHANGES", carried=True)
    session = repo / ".context" / "cadence-session.json"
    data = json.loads(session.read_text(encoding="utf-8")); data["host"] = "claude"
    session.write_text(json.dumps(data), encoding="utf-8")
    _attest_plan_review(repo, ["peer-1"])
    errors = check_plan_review_verdict(repo / ".context")
    assert errors and f"cycle 1 of {PLAN_LIMIT}" in errors[0]
    _attest_plan_review(repo, [f"peer-{i}" for i in range(2, PLAN_LIMIT + 1)])
    assert check_plan_review_verdict(repo / ".context") == []


def test_at_the_limit_an_unlabeled_review_clears_with_a_carried_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=PLAN_LIMIT, verdict="READY WITH CHANGES", carried=True)
    review = repo / ".context" / "reviews" / "plan_gh-1_x.md"
    review.write_text("RUN_BY_COMMAND: plan-review\nTicket: gh-1\n\n- the hold reads the wrong session\n\nVerdict: READY WITH CHANGES\n", encoding="utf-8")
    assert check_plan_review_verdict(repo / ".context") == []


def test_four_blocking_reviews_still_allow_another(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _setup(tmp_path, cycles=PLAN_LIMIT - 1, verdict="READY WITH CHANGES", carried=True)
    errors = check_plan_review_verdict(repo / ".context")
    assert errors and f"cycle {PLAN_LIMIT - 1} of {PLAN_LIMIT}" in errors[0] and "/plan-update" in errors[0]


# --- adversarial_loop_converges: cd-63 Phase 6: a pilot-shaped P6 loop reaches E1 after round 3 (Acceptance 9, 11; F8; R6-N5-2).
#
# A pilot run ran P6 four times, each time with new edge cases, and stopped on `retries` before E1. Here each round is a REQUEST CHANGES with new B-n items. At
# round 3 the plan carries them; P6 and P9 then pass with no new review.

P6_LIMIT = ar.adversarial_cycle_limit()


def _begin(repo: Path, stage: str, command: str) -> None:
    proc = run_py("scripts/subagent_provenance.py", "begin",
                  "--stage", stage, "--command", command, cwd=repo)
    assert proc.returncode == 0, proc.stderr


def _blocking(round_n: int) -> str:
    """Each round names new edge cases, as the pilot's P6 did."""
    return f"## Blocking issues\n\n### B-1. edge {round_n}a\n\n### B-2. edge {round_n}b\n"


def _at(path: Path, t: float) -> None:
    os.utime(path, (t, t))


class Pilot:
    def __init__(self, lp: Loop, *, history: bool):
        self.lp, self.history, self.clock = lp, history, 1000.0

    def tick(self) -> float:
        self.clock += 10
        return self.clock

    def p6_round(self, n: int, verdict: str = "REQUEST CHANGES", body: str | None = None, *,
                 agent: str | None = None, mode: str | None = "plan-gate") -> Path:
        if self.history:
            _begin(self.lp.repo, "de-adversarial-reviewer", f"/de-adversarial-reviewer plan-gate {self.lp.plan_path}")
        report = self.lp.report(verdict, round_n=n, agent=agent or f"peer-{n}", mode=mode,
                                body=_blocking(n) if body is None else body)
        _at(report, self.tick())
        return report

    def p3_round(self, i: int, verdict: str = "READY TO EXECUTE") -> Path:
        if self.history:
            _begin(self.lp.repo, "plan-review", f"/plan-review {self.lp.plan_path}")
        path = self.lp.ctx / "reviews" / f"plan_{self.lp.slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {self.lp.run}\n**Ticket:** gh-1\n\n"
                        f"### MF-1. open edge\n\nVerdict: {verdict}\n", encoding="utf-8")
        if not self.history:
            self.lp.attest(path, f"reviewer-{i}")
        _at(path, self.tick())
        return path

    def plan_update(self, extra: str = "") -> None:
        self.lp.write_plan(extra)
        _at(self.lp.plan_path, self.tick())

    def p6(self):
        return ar.check_adversarial_verdict(self.lp.ctx, self.lp.session, stage="p6")

    def p9(self):
        return ar.check_adversarial_verdict(self.lp.ctx, self.lp.session, stage="p9")

    def route(self):
        row = ar.adversarial_route(self.lp.ctx, self.lp.session)
        return None if row is None else row["command"]


@pytest.fixture(params=["claude", "history"])
def pilot(request, tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    history = request.param == "history"
    return Pilot(Loop(repo, host="other" if history else "claude"), history=history)


def _carry(report: Path, ids: list[str]) -> str:
    return f"\n{ar.carried_heading(report)}\n\n" + "".join(f"- {i} -> Phase 1 (test: edge)\n" for i in ids)


def test_acceptance_9_the_pilot_shape_reaches_e1_after_round_3(pilot):
    pilot.plan_update()
    pilot.p3_round(1)
    for n in (1, 2):
        pilot.p6_round(n)
        assert pilot.p6() and f"(cycle {n} of {P6_LIMIT})" in pilot.p6()[0]
        assert pilot.route() == "/plan-update"
        pilot.plan_update()
        assert pilot.route() == "/plan-review"
        pilot.p3_round(n + 1)
        assert pilot.route() is None  # the walk's open block (P6) decides
    report = pilot.p6_round(3)
    assert ar.adversarial_state(pilot.lp.ctx, pilot.lp.session)["cycles"] == P6_LIMIT
    assert "cycle limit reached" in pilot.p9()[0]
    assert pilot.route() == "/plan-update"
    pilot.plan_update(_carry(report, ["B-1", "B-2"]))  # carry-only: no new P3 review
    assert pilot.p6() == [] and pilot.p9() == []
    assert pilot.route() is None  # F8: no fourth P6 run is asked for


def test_acceptance_9_a_fourth_round_over_the_limit_uses_the_carry_rule(pilot):
    pilot.plan_update()
    pilot.p3_round(1)
    for n in (1, 2, 3):
        pilot.p6_round(n)
        pilot.plan_update()
        pilot.p3_round(n + 1)
    report = pilot.p6_round(4)
    assert "cycle limit reached (4 cycles)" in pilot.p9()[0]
    pilot.plan_update(_carry(report, ["B-1", "B-2"]))
    assert pilot.p9() == [] and pilot.route() is None


@pytest.mark.parametrize("p3_verdict", ["READY WITH CHANGES", "NOT READY"])
def test_acceptance_11_one_budget_with_p3(pilot, p3_verdict):
    pilot.plan_update()
    for i in (1, 2, 3):
        pilot.p3_round(i)  # P3 converged at cycle 3
    pilot.p6_round(1)
    pilot.plan_update()
    pilot.p3_round(4)
    pilot.p6_round(2)
    pilot.plan_update()
    pilot.p3_round(5, verdict=p3_verdict)
    assert ar.plan_review_cycles(pilot.lp.ctx, session=pilot.lp.session) == 5
    limit = ar.check_plan_review_verdict(pilot.lp.ctx, session=pilot.lp.session)
    if p3_verdict == "NOT READY":
        assert limit and "hard-stop" in limit[0]
        return
    p3_carry = "\n## Carried review findings\n\n- MF-1 -> Phase 2 (test: open edge)\n"
    pilot.plan_update(p3_carry)
    assert ar.check_plan_review_verdict(pilot.lp.ctx, session=pilot.lp.session) == []
    # P3 is at its limit, so the update goes straight to P6 (D8 d).
    assert pilot.route() == "/de-adversarial-reviewer plan-gate"
    report = pilot.p6_round(3)
    assert "Run /plan-update" in pilot.p9()[0]
    pilot.plan_update(p3_carry + _carry(report, ["B-1", "B-2"]))
    assert ar.check_plan_review_verdict(pilot.lp.ctx, session=pilot.lp.session) == []
    assert pilot.p9() == [] and pilot.route() is None


@pytest.fixture
def updated_pilot(loop):
    """A Pilot with no history on Loop(repo), after the first /plan-update."""
    pilot = Pilot(loop, history=False)
    pilot.plan_update()
    return pilot


def test_one_resumed_agent_for_every_round_still_reaches_the_limit(updated_pilot):
    """R2-SF-2 / R4-B-1: the count is (agent, path) pairs, so one resumed peer still counts each round."""
    pilot = updated_pilot
    for n in (1, 2, 3):
        pilot.p6_round(n, agent="one-peer")
    assert ar.adversarial_state(pilot.lp.ctx, pilot.lp.session)["cycles"] == P6_LIMIT


def test_three_host_copy_rounds_stay_at_cycle_0_and_name_the_shadow(updated_pilot):
    """R5 B-1, R6-N5-2: a shadowed peer never reaches the limit; each gate and the stop guard name why."""
    pilot = updated_pilot
    for n in (1, 2, 3):
        pilot.p6_round(n, mode=None, body="## Blocking Issues\n\n### B-1. host copy\n")
        assert ar.adversarial_state(pilot.lp.ctx, pilot.lp.session)["cycles"] == 0
        assert 'has no line "Mode: plan-gate"' in pilot.p6()[0]
        row = ar.adversarial_route(pilot.lp.ctx, pilot.lp.session)
        assert row is not None and 'has no line "Mode: plan-gate"' in row["detail"]
