"""gh-104: the P3 and P4 gates read the newest plan-review verdict and the plan mtime.

After /plan-update, a blocking review that is older than the plan opens P3 (the next
cycle). A blocking review that is not older than the plan opens P4. At the cycle limit
the step is the carry or the hard stop at P4, never another review (plan D5, D7, D8).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import artifact_requirements as ar  # noqa: E402
from p3_loop import NR, RTE, RWC, at, at_limit, ledger, p3_loop, p3_repo, p3_review, plan, review_sha, s1b, s2, s3  # noqa: E402

OTHER_ERRORS = ("lean delegation", "peer-model", "no plan-review artifact")


@pytest.fixture
def p3_run_repo(tmp_path, monkeypatch):
    return p3_repo(tmp_path / "r", monkeypatch)


def _gate(repo: Path, script: str, *args: str, readonly: bool = True):
    return run_py(f"scripts/{script}", *args, cwd=repo, **({"CADENCE_GATES_READONLY": "1"} if readonly else {}))


def _no_other_error(out: str) -> None:
    assert not [e for e in OTHER_ERRORS if e in out], out


# --- plan_review_step, the rule (D5) ------------------------------------------------------------------


def test_plan_review_step_s1_blocking_review_newer_than_plan_gives_p4(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=1)
    plan(lp, 50)
    p3_review(lp, 60, RWC)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4" and "/plan-update" in step[1]


def test_plan_review_step_s1b_cycle_two_review_newer_than_updated_plan_gives_p4(p3_run_repo):
    lp = s1b(p3_run_repo)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4" and "plan_gh-1_x_r2.md" in step[1]


def test_plan_review_step_s2_plan_newer_than_blocking_review_gives_p3_next_cycle(p3_run_repo):
    lp = s2(p3_run_repo)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3"
    assert "cycle 2 of 5" in step[1] and "/plan-review" in step[1]


def test_plan_review_step_s2t_equal_mtimes_give_p4(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=1)
    p3_review(lp, 60, RWC)
    plan(lp, 60, updated=True)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4"


def test_plan_review_step_s3_newest_ready_to_execute_gives_none(p3_run_repo):
    lp = s3(p3_run_repo)
    assert ar.plan_review_step(lp.ctx, lp.session) is None


def test_plan_review_step_s3n_no_ticket_scope_reads_the_newest_review(p3_run_repo):
    lp = s3(p3_run_repo, task_id=None)
    assert ar._run_plan(lp.ctx, lp.session) is not None
    chosen = ar.select_plan_review(lp.ctx, lp.session)
    assert chosen is not None and chosen[0].name == "plan_gh-1_x.md"
    assert ar.plan_review_step(lp.ctx, lp.session) is None


@pytest.mark.parametrize("verdict, plan_at, needle", [
    pytest.param(RWC, 60, "Carried review findings", id="plan_review_step_s4_limit_review_newer_gives_p4_carry"),
    pytest.param(RWC, 80, "Carried review findings",
                 id="plan_review_step_s4p_limit_plan_newer_gives_p4_carry_not_review_six"),
    pytest.param(NR, 60, "--hard-stop retries", id="plan_review_step_s5_limit_not_ready_gives_p4_hard_stop"),
])
def test_plan_review_step_at_the_limit_gives_p4(p3_run_repo, verdict, plan_at, needle):
    lp = at_limit(p3_run_repo, verdict, plan_at)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4" and needle in step[1]


def test_plan_review_step_s4c_limit_with_carried_ids_gives_none(p3_run_repo):
    lp = at_limit(p3_run_repo, RWC, 80, carried=["MF-1"])
    assert ar.plan_review_step(lp.ctx, lp.session) is None


def test_plan_review_step_s6_not_ready_plan_newer_gives_p3_next_cycle(p3_run_repo):
    lp = s2(p3_run_repo, verdict=NR)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3" and "cycle 2 of 5" in step[1]


def test_plan_review_step_s7_no_session_file_no_review_gives_none(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=1)
    (lp.ctx / "cadence-session.json").unlink()
    assert ar.plan_review_step(lp.ctx) is None


def test_plan_review_step_s7b_no_session_file_blocking_review_plan_newer_gives_none(p3_run_repo):
    lp = s2(p3_run_repo)
    (lp.ctx / "cadence-session.json").unlink()
    assert ar.plan_review_step(lp.ctx) is None


def test_plan_review_step_s7c_empty_session_gives_none(p3_run_repo):
    lp = s2(p3_run_repo)
    assert ar.plan_review_step(lp.ctx, session={}) is None


def test_plan_review_step_s8_no_plan_gives_none_without_exception(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=1)
    p3_review(lp, 50, RWC)
    lp.plan_path.unlink()
    assert ar.plan_review_step(lp.ctx, lp.session) is None


def test_plan_review_step_s9_newer_review_with_no_verdict_is_named(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=1)
    p3_review(lp, 50, RWC)
    p3_review(lp, 55, None, n=2)
    plan(lp, 60, updated=True)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3"
    assert "/plan-review" in step[1] and "plan_gh-1_x_r2.md" in step[1] and "Verdict:" in step[1]


def test_plan_review_step_s10_no_begins_gives_p3_cycle_one(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=0)
    for n, t in enumerate((30, 40, 50), start=1):
        p3_review(lp, t, RWC, n=n)
    plan(lp, 60, updated=True)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3" and "cycle 1 of 5" in step[1]


def test_plan_review_step_s11_unreadable_plan_fails_closed_at_p3(p3_run_repo, monkeypatch):
    lp = s2(p3_run_repo)
    monkeypatch.setattr(ar, "_run_plan", lambda c, s: lp.ctx / "plans" / "gone.plan.md")
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert isinstance(step, tuple) and step[0] == "P3"
    assert "cannot read" in step[1] and "gone.plan.md" in step[1]


def test_plan_review_step_s11r_unreadable_review_fails_closed_and_names_the_review(p3_run_repo, monkeypatch):
    # gh-104-S05: the message names the file that failed, not always the plan.
    lp = s2(p3_run_repo)
    gone = lp.ctx / "reviews" / "plan_gh-1_x_gone.md"
    monkeypatch.setattr(ar, "select_newest_verdict", lambda c, spec, s: (gone, RWC))
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert isinstance(step, tuple) and step[0] == "P3"
    assert "cannot read plan_gh-1_x_gone.md" in step[1]


def test_plan_review_step_s12_newer_review_of_another_run_is_ignored(p3_run_repo):
    # gh-104-S06: a READY TO EXECUTE review of another run does not close this run's P3 loop.
    lp = s2(p3_run_repo)
    p3_review(lp, 70, RTE, n=2, run="00000000-0000-4000-8000-000000000999")
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3"
    assert "plan_gh-1_x.md" in step[1] and "cycle 2 of 5" in step[1]


# --- the P3 gate (D2) ---------------------------------------------------------------------------------


def test_s2_p3_gate_fails_before_breadcrumb(p3_run_repo):
    lp = s2(p3_run_repo)
    proc = _gate(p3_run_repo, "plan_review_gate.py", readonly=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "cycle 2 of 5" in out
    _no_other_error(out)
    assert not (lp.ctx / "gates" / "plan-review-gate.json").exists()


def test_s2_p3_gate_check_fails(p3_run_repo):
    s2(p3_run_repo)
    proc = _gate(p3_run_repo, "plan_review_gate.py", "--check")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "cycle 2 of 5" in out
    _no_other_error(out)


# --- the P4 gate (D3) ---------------------------------------------------------------------------------


def test_s1b_p4_gate_fails_on_old_marker_with_newer_blocking_review(p3_run_repo):
    s1b(p3_run_repo)
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "/plan-update" in out
    _no_other_error(out)


@pytest.mark.parametrize("verdict, plan_t, reason", [
    (RWC, 60, "Carried review findings"),
    (RWC, 80, "Carried review findings"),
    (NR, 60, "--hard-stop retries"),
], ids=["s4", "s4p", "s5"])
def test_limit_p4_gate_fails_and_names_the_next_step(p3_run_repo, verdict, plan_t, reason):
    at_limit(p3_run_repo, verdict, plan_t)
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert reason in out
    _no_other_error(out)


def test_s4c2_limit_reads_open_ids_from_the_must_fix_section_only(p3_run_repo):
    """gh-1 P1 rerun: the gate asked for MF-15 and MF-16, which the review listed as applied."""
    lp = at_limit(p3_run_repo, RWC, 80, carried=["MF-3"])
    review = lp.ctx / "reviews" / f"plan_{lp.slug}_r5.md"
    body = (
        "RUN_BY_COMMAND: plan-review\n"
        f"Pipeline-Run: {lp.run}\n**Ticket:** gh-1\n\n"
        "## Cycle 4 items: status\n\n- **MF-1.** Applied.\n- **MF-2.** Applied.\n\n"
        "## Must-fix items\n\n**MF-3.** The watermark write path is not named.\n\n"
        "## Notes (not blocking)\n\n- N-1. The MF-1 text could name the file.\n\n"
        "## Verdict basis\n\nOne item blocks E1: MF-3.\n\nVerdict: READY WITH CHANGES\n"
    )
    review.write_text(body, encoding="utf-8")
    at(review, 70)
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("section, want", [
    pytest.param("- [ ] MF-1 open\n- [x] MF-2 done but listed\n", {"MF-1", "MF-2"}, id="checkbox-items"),
    pytest.param("| Id | Item |\n|---|---|\n| MF-1 | the path |\n| MF-2 | the test |\n", {"MF-1", "MF-2"}, id="table-rows"),
    pytest.param("**Item MF-1.** open\n\n**Open item MF-2.** open\n", {"MF-1", "MF-2"}, id="words-before-the-id"),
    pytest.param("- MF-1 open\n\n```\n# a comment, not a heading\nMF-9 in an example\n```\n\n- MF-2 open\n", {"MF-1", "MF-2"},
                 id="code-fence-does-not-end-the-section"),
    pytest.param("- MF-1 open. This amends MF-7 from cycle 2.\n", {"MF-1"}, id="cited-id-is-not-an-item"),
    pytest.param("- MF-1 open.\n  see MF-8 for the file.\nAlso fixes MF-9.\n", {"MF-1"}, id="continuation-lines-are-not-items"),
    # The anchor item `- MF-1` makes the fallback (every cited id) not apply, so the row fails on a reader that
    # misses a shape (PR #168 R1 N-24).
    pytest.param("- MF-1 open.\n- Item MF-2: a\n1. Item MF-3\n### Item MF-4\n| Item MF-5 | b |\n",
                 {"MF-1", "MF-2", "MF-3", "MF-4", "MF-5"}, id="a-word-after-a-marker-is-still-an-item"),
    pytest.param("- MF-1 open.\n- see MF-8 for the file.\n", {"MF-1", "MF-8"}, id="a-bulleted-citation-is-an-item-closed-side"),
    pytest.param("- MF-1 open.\nItem MF-2: a bare line with a word first reads as a citation.\n", {"MF-1"},
                 id="bare-word-first-line-is-a-citation"),
])
def test_review_must_fix_ids_reads_every_item_shape(section, want):
    """PR #168 R1 N-21: an item shape the reader missed dropped an open id, and the gate asked for fewer."""
    text = "## Cycle 4 items: status\n\n- MF-5 applied.\n\n## Must-fix items\n\n" + section + "\n## Notes\n\n- MF-6 was applied.\n"
    assert ar.review_must_fix_ids(text) == want


def test_s4c3_limit_with_a_must_fix_section_still_names_the_unmapped_id(p3_run_repo):
    lp = at_limit(p3_run_repo, RWC, 80, carried=["MF-1"])
    review = lp.ctx / "reviews" / f"plan_{lp.slug}_r5.md"
    review.write_text(
        "RUN_BY_COMMAND: plan-review\n"
        f"Pipeline-Run: {lp.run}\n**Ticket:** gh-1\n\n"
        "## Must-fix items\n\n**MF-1.** open\n\n### MF-3\n\nopen too. This amends MF-4 from cycle 2.\n\n"
        "## Notes\n\n- MF-2 was applied.\n\nVerdict: READY WITH CHANGES\n",
        encoding="utf-8",
    )
    at(review, 70)
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "map MF-3 to the phase" in out, out
    assert "MF-2" not in out and "MF-4" not in out, out


def test_s4p_p3_gate_passes_at_the_limit(p3_run_repo):
    at_limit(p3_run_repo, RWC, 80)
    proc = _gate(p3_run_repo, "plan_review_gate.py", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_s3_p4_gate_passes_after_ready_to_execute(p3_run_repo):
    s3(p3_run_repo)
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_p4_gate_review_not_utf8_gives_gate_result_not_traceback(p3_run_repo):
    # PR #143 review N-1: a review file with a byte that is not UTF-8 stopped the P4 gate with a traceback.
    lp = s2(p3_run_repo)
    bad = p3_run_repo / ".context" / "reviews" / "plan_gh-1_x_r2.md"
    bad.write_bytes(b"RUN_BY_COMMAND: plan-review\n\xff\nPipeline-Run: " + lp.run.encode() + b"\n")
    os.utime(bad, (55, 55))
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    out = proc.stdout + proc.stderr
    assert "Traceback" not in out, out
    assert proc.returncode == 0, out
    assert "OK  plan-update-gate" in out


# --- the Revision ledger (ADR-0011): each update answers every MF-n of the review, each cycle ------


def _ledger_cycle(repo: Path, rows: dict[str, str] | None, mf: tuple[str, ...] = ("MF-1", "MF-2")):
    """Cycle 1: a blocking review with `mf` at 50, then the update with the ledger `rows` at 60."""
    lp = p3_loop(repo, cycles=1)
    review = p3_review(lp, 50, RWC, mf=mf)
    plan(lp, 60, updated=True, rows=rows, review=review)
    return lp


@pytest.mark.parametrize("rows", [
    pytest.param({"MF-1": "added the test", "MF-2": "added Phase 3"}, id="a-row-for-each-id"),
    pytest.param({"MF-1": "added the test", "MF-2": "not changed: the helper exists"}, id="not-changed-row-counts"),
])
def test_p4_gate_passes_when_each_must_fix_has_a_ledger_row(p3_run_repo, rows):
    lp = _ledger_cycle(p3_run_repo, rows)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3" and "cycle 2 of 5" in step[1]
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_p4_gate_fails_and_names_the_missing_id_and_the_review(p3_run_repo):
    lp = _ledger_cycle(p3_run_repo, {"MF-1": "added the test"})
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4"
    assert "MF-2" in step[1] and "MF-1" not in step[1] and "plan_gh-1_x.md" in step[1], step
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "Revision ledger" in out and "MF-2" in out and "plan_gh-1_x.md" in out, out
    _no_other_error(out)


def test_p4_gate_fails_closed_on_an_update_with_no_ledger(p3_run_repo):
    lp = _ledger_cycle(p3_run_repo, None)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4" and "MF-1, MF-2" in step[1], step


def test_p4_gate_passes_on_a_review_with_no_must_fix(p3_run_repo):
    lp = _ledger_cycle(p3_run_repo, None, mf=())
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3", step
    proc = _gate(p3_run_repo, "plan_update_gate.py", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_older_ledger_does_not_satisfy_a_newer_review(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=2)
    first = p3_review(lp, 40, RWC, mf=("MF-1", "MF-2"))
    p3_review(lp, 50, RWC, n=2, mf=("MF-1",))
    update = f"\n## Plan update\n\nRUN_BY_COMMAND: plan-update\nPipeline-Run: {lp.run}\n"
    # The ledger names the first review; the second review has the same open id and no ledger of its own.
    at(lp.write_plan(update + ledger({"MF-1": "a", "MF-2": "b"}, review_sha(first)) + "\n## Plan update 2\n\nNew text.\n"), 60)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4" and "MF-1" in step[1] and "plan_gh-1_x_r2.md" in step[1], step


def test_the_ledger_reads_the_must_fix_section_not_the_status_of_earlier_items(p3_run_repo):
    lp = p3_loop(p3_run_repo, cycles=2)
    p3_review(lp, 40, RWC, mf=("MF-1",))
    review = lp.ctx / "reviews" / f"plan_{lp.slug}_r2.md"
    review.write_text(
        f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {lp.run}\n**Ticket:** gh-1\n\n"
        "## Status of earlier items\n\n| Id | Status | Evidence |\n|---|---|---|\n| MF-1 | closed | ledger row MF-1 |\n\n"
        "## Must-fix items\n\n- **MF-2.** The new Phase 3 has no test.\n\nVerdict: READY WITH CHANGES\n",
        encoding="utf-8",
    )
    at(review, 50)
    plan(lp, 60, updated=True, rows={"MF-2": "added the test"}, review=review)
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P3", step


def test_at_the_limit_a_ledger_row_is_not_a_carry(p3_run_repo):
    # At the limit the gate reads `## Carried review findings`: each id maps to the phase whose test settles it.
    lp = at_limit(p3_run_repo, RWC, 80, rows={"MF-1": "added the test"})
    step = ar.plan_review_step(lp.ctx, lp.session)
    assert step is not None and step[0] == "P4" and "Carried review findings" in step[1], step


A, B = "a" * 64, "b" * 64  # the sha256 of review one and of review two


@pytest.mark.parametrize("text, sha, want", [
    pytest.param("## Plan update\n" + ledger({"MF-1": "a"}, A[:12]), A, {"MF-1"}, id="one-row"),
    pytest.param("## Plan update\n", A, None, id="no-ledger"),
    pytest.param("## Plan update\n```\n## Revision ledger (review " + A[:12] + ")\n| MF-1 | a |\n```\n", A, None,
                 id="fenced-ledger-is-not-one"),
    pytest.param("## Plan update\n" + ledger({"MF-1": "a"}, A[:12]) + "\n## Notes\n\n| MF-9 | b |\n", A, {"MF-1"},
                 id="the-next-heading-ends-the-table"),
    # PR #186 R1 MF-2, MF-3: an older review's ledger never answers a newer review, whatever heading follows it.
    pytest.param("## Plan update\n" + ledger({"MF-1": "a"}, A[:12]) + "\n### Update\n\nNew text.\n", B, None,
                 id="old-ledger-under-a-new-update"),
    pytest.param("## Plan update\n" + ledger({"MF-1": "a"}, A[:12]) + "\n## Plan changes\n\nNew text.\n", B, None,
                 id="old-ledger-under-plan-changes"),
    pytest.param("## Plan update\n" + ledger({"MF-1": "a"}, A[:12]) + "\n## Plan update\n" + ledger({"MF-1": "b", "MF-4": "c"}, B[:12]), B,
                 {"MF-1", "MF-4"}, id="the-second-ledger-answers-the-second-review"),
    pytest.param("## Plan update\n" + ledger({"MF-1": "a"}, A), A, {"MF-1"}, id="the-full-sha-also-matches"),
    pytest.param("## Plan update\n## Revision ledger\n\n| Id |\n|---|\n| MF-1 |\n", A, None, id="a-ledger-with-no-review-is-none"),
    pytest.param("## Plan update\n## Revision ledger (cycle 2)\n\n| Id |\n|---|\n| MF-1 |\n", A, None, id="a-cycle-number-is-not-a-review"),
])
def test_revision_ledger_ids_reads_the_ledger_of_the_review_it_answers(text, sha, want):
    assert ar.revision_ledger_ids(text, sha) == want
