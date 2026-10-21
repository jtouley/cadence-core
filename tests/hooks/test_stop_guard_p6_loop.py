"""The stop guard names the next step of the P6 loop (D7, R2-SF-1, R5-C-1, R6-MF-2, P7 F8)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import enable_all_packs, git_repo, install_config, run_py, stop_guard_module

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import artifact_requirements as ar  # noqa: E402
from adversarial_loop import Loop  # noqa: E402

BLOCKING = "## Blocking issues\n\n### B-1. one\n\n### B-2. two\n"


def _guard():
    return stop_guard_module("cd63_stop_guard")


@pytest.fixture
def lp(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    enable_all_packs(repo)  # P6 is in the spec-review pack
    monkeypatch.chdir(repo)
    return Loop(repo)


def at(path: Path, t: float) -> Path:
    os.utime(path, (t, t))
    return path


def review(lp, t: float, *, verdict="READY TO EXECUTE", n: int = 1) -> Path:
    """n plan reviews, each by a new attested reviewer; the newest has mtime t."""
    path = lp.ctx / "reviews" / f"plan_{lp.slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        path.write_text(f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {lp.run}\n**Ticket:** gh-1\n\nVerdict: {verdict}\n",
                        encoding="utf-8")
        lp.attest(path, f"reviewer-{i}")
    return at(path, t)


def rounds(lp, n: int, t: float) -> Path:
    path = None
    for i in range(1, n + 1):
        path = lp.report("REQUEST CHANGES", round_n=i, agent=f"peer-{i}", body=BLOCKING)
        at(path, t - (n - i))
    return path


def command(lp) -> str | None:
    row = ar.adversarial_route(lp.ctx, lp.session)
    return None if row is None else row["command"]


def carry(lp, report, ids, t: float) -> None:
    lp.write_plan(f"\n{ar.carried_heading(report)}\n\n" + "".join(f"- {i} -> Phase 1\n" for i in ids))
    at(lp.plan_path, t)


# --- the route table, first match wins (R5-C-1) ------------------------------------------------------


def test_row_2_report_newer_than_the_plan_names_plan_update(lp):
    at(lp.plan_path, 100)
    review(lp, 90)
    rounds(lp, 1, 200)
    assert command(lp) == "/plan-update"


def test_row_2_wins_over_a_plan_review_newer_than_the_plan(lp):
    at(lp.plan_path, 100)
    rounds(lp, 1, 200)
    review(lp, 300)
    assert command(lp) == "/plan-update"


def test_row_3_plan_newer_than_the_report_and_no_newer_review_names_plan_review(lp):
    review(lp, 50)
    rounds(lp, 1, 100)
    at(lp.plan_path, 200)
    assert command(lp) == "/plan-review"


def test_row_4_at_the_p3_limit_names_p6(lp):
    review(lp, 50, n=5)
    rounds(lp, 1, 100)
    at(lp.plan_path, 200)
    assert command(lp) == "/de-adversarial-reviewer plan-gate"


def test_row_5_review_and_plan_newer_than_the_report_let_the_walk_decide(lp):
    rounds(lp, 1, 100)
    at(lp.plan_path, 200)
    review(lp, 300)
    assert command(lp) is None


def test_the_detail_is_the_gate_message(lp):
    at(lp.plan_path, 100)
    report = rounds(lp, 1, 200)
    assert ar.adversarial_route(lp.ctx, lp.session)["detail"] == ar.check_adversarial_verdict(lp.ctx, lp.session)[0]
    assert report.name in ar.adversarial_route(lp.ctx, lp.session)["detail"]


# --- the P6 limit (R6-MF-2, P7 F8) -------------------------------------------------------------------


def test_row_1_at_the_limit_with_open_ids_names_plan_update_even_after_newer_reviews(lp):
    report = rounds(lp, 3, 100)
    carry(lp, report, ["B-1"], 200)
    review(lp, 300)
    row = ar.adversarial_route(lp.ctx, lp.session)
    assert row["command"] == "/plan-update" and "B-2" in row["detail"] and "Carried adversarial findings" in row["detail"]
    assert "/de-adversarial-reviewer" not in row["command"]


def test_row_1_with_the_heading_missing_names_plan_update(lp):
    rounds(lp, 3, 100)
    at(lp.plan_path, 200)
    review(lp, 300)
    assert command(lp) == "/plan-update"


def test_release_row_every_id_carried_gives_no_hold(lp):
    report = rounds(lp, 3, 100)
    carry(lp, report, ["B-1", "B-2"], 200)
    review(lp, 300)
    assert command(lp) is None


@pytest.mark.parametrize("p3_limit", [False, True])
def test_after_a_full_carry_with_an_older_review_p6_is_never_run_a_fourth_time(lp, p3_limit):
    """F8: rows 3 and 4 apply below the P6 limit only."""
    review(lp, 50, n=5 if p3_limit else 1)
    report = rounds(lp, 3, 100)
    carry(lp, report, ["B-1", "B-2"], 200)
    assert command(lp) is None


@pytest.mark.parametrize("p3_limit", [False, True])
def test_after_a_partial_carry_with_an_older_review_the_route_is_the_carry(lp, p3_limit):
    review(lp, 50, n=5 if p3_limit else 1)
    report = rounds(lp, 3, 100)
    carry(lp, report, ["B-1"], 200)
    assert command(lp) == "/plan-update"


@pytest.mark.parametrize("verdict", ["APPROVE", "APPROVE WITH CONDITIONS"])
def test_a_passing_verdict_gives_no_hold(lp, verdict):
    at(lp.plan_path, 100)
    at(lp.report(verdict), 200)
    assert command(lp) is None


# --- the hook (_judge_one) ---------------------------------------------------------------------------


def judge(lp, session=None, ctx=None, ws=None):
    mod = _guard()
    decision, message, _here, _s = mod._judge_one(session or lp.session, ctx or lp.ctx, ws or lp.repo,
                                                  corrupt=False, budget=30.0)
    return decision, message


def test_acceptance_7_ready_p3_and_a_newer_p6_request_changes_hold_on_plan_update(lp):
    review(lp, 50)
    at(lp.plan_path, 60)
    rounds(lp, 1, 100)
    decision, message = judge(lp)
    assert decision == "block"
    assert "/plan-update" in message and "cycle 1 of 3" in message
    assert "P5" not in message and "architecture-review-plan" not in message


def test_a_blocking_p3_verdict_wins_over_p6(lp):
    at(lp.plan_path, 60)
    review(lp, 50, verdict="READY WITH CHANGES")
    rounds(lp, 1, 100)
    decision, message = judge(lp)
    assert "verdict is REQUEST CHANGES (cycle" not in message


def test_at_the_limit_the_hook_names_the_carry_step(lp):
    report = rounds(lp, 3, 100)
    carry(lp, report, ["B-1"], 200)
    decision, message = judge(lp)
    assert decision == "block" and "Carried adversarial findings" in message and "B-2" in message


def test_two_worktree_runs_judge_run_b_with_its_own_attest_log(two_worktree_runs):
    """R4-C-1: the hook cwd is the primary; run B's attested report decides, not a newer unattested one."""
    w = two_worktree_runs
    session_b = ar.current_session(w.ctx, w.b)
    assert session_b["pipeline_run_id"] == w.run_b
    lp = Loop.__new__(Loop)
    lp.repo, lp.run, lp.slug, lp.host, lp.ctx = w.b, w.run_b, "cd-902_x", "claude", w.ctx
    lp.plan_path = w.ctx / "plans" / f"{lp.slug}.plan.md"
    lp.write_plan("")
    at(lp.plan_path, 50)
    attested = at(lp.report("REQUEST CHANGES", agent="peer-b", body=BLOCKING), 100)
    at(lp.report("APPROVE", round_n=2, attest=False), 200)
    session_b = {**session_b, "host": "claude", "orchestrator_source": "toplevel"}
    row = ar.adversarial_route(w.ctx, session_b)
    assert row is not None and attested.name in row["detail"]


def test_headless_gate_output_names_plan_update_below_the_limit(lp, tmp_path):
    """R3-C-4: a headless host reads the next step in the P6 gate output tail."""
    import json

    root = tmp_path / "skill"
    root.mkdir()
    for name in ("scripts", "shared", "hooks", "adapters", "packs"):
        if (ROOT / name).exists():
            (root / name).symlink_to(ROOT / name)
    install_config(root)
    registry = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))
    registry["stages"] = [s for s in registry["stages"] if s["slug"] == "adversarial-review"]
    (root / "stages.json").write_text(json.dumps(registry), encoding="utf-8")
    at(lp.plan_path, 100)
    rounds(lp, 1, 200)
    proc = run_py("scripts/headless.py", "next", "--json", cwd=lp.repo, home=root, timeout=120)
    out = json.loads(proc.stdout)
    assert out["next"]["block"] == "P6"
    assert "/plan-update" in out["results"][-1]["output"]


def test_a_legacy_modeless_report_does_not_replace_an_earlier_open_block(lp):
    """A report that an earlier run of the ticket left (no Mode, no run id) before P6."""
    legacy = lp.ctx / "adversarial" / "adversarial_gh-1_old.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("RUN_BY_SKILL: de-adversarial-reviewer\nTicket: gh-1\n\n**Verdict:** APPROVE\n", encoding="utf-8")
    at(lp.plan_path, 60)
    decision, message = judge(lp)
    assert decision == "block" and "Continue paceline: next" in message
    assert "host skill with the same name hides it" not in message
    assert "/de-adversarial-reviewer" not in message


# --- R2 F14 / F15 -------------------------------------------------------------------------------


def test_the_route_rows_name_their_block(lp):
    at(lp.plan_path, 100)
    rounds(lp, 1, 200)
    row = ar.adversarial_route(lp.ctx, lp.session)
    assert (row["block"], row["slug"], row["phase"]) == ("P4", "plan-update", "planning")


def test_the_shadow_row_names_p6(lp):
    lp.report("REQUEST CHANGES", mode=None, body="## Blocking Issues\n\n### B-1. host copy\n")
    row = ar.adversarial_route(lp.ctx, lp.session)
    assert (row["block"], row["slug"], row["phase"], row["command"]) == (
        "P6", "adversarial-review", "planning", "/de-adversarial-reviewer plan-gate")


def test_the_highest_round_decides_when_mtimes_tie(lp):
    """R5-C-2: sort by round, then mtime; `_r10` sorts before `_r2` by path."""
    r2 = lp.report("REQUEST CHANGES", round_n=2, agent="a2", body=BLOCKING)
    r10 = lp.report("APPROVE", round_n=10, agent="a10")
    at(r2, 500)
    at(r10, 500)
    assert ar.adversarial_state(lp.ctx, lp.session)["report"] == r10


def test_an_empty_carried_section_at_the_limit_does_not_pass(lp):
    report = rounds(lp, 3, 100)
    lp.write_plan(f"\n{ar.carried_heading(report)}\n\n")
    assert ar.check_adversarial_verdict(lp.ctx, lp.session, stage="p9")
    assert command(lp) == "/plan-update"


def test_a_hard_stopped_run_gets_no_p6_hold(lp):
    at(lp.plan_path, 100)
    rounds(lp, 1, 200)
    decision, message = judge(lp, session={**lp.session, "hard_stop": "retries"})
    assert "verdict is REQUEST CHANGES" not in message
