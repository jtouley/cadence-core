"""The P6 adversarial verdict: how it is read, which ids count, which reports count, and how the P6 gate and P9 use it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import code_paths, git_repo, run_py

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import artifact_requirements as ar  # noqa: E402
import gate_common  # noqa: E402
from adversarial_loop import OTHER_RUN, RUN, Loop  # noqa: E402
from artifact_requirements import (adversarial_ids, adversarial_verdict_in_text as read, blocking_body,  # noqa: E402
                                   carried_heading)


# The loop mechanics in process run with five P3 and three P6 cycles (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("legacy_cycle_limits")


# --- adversarial_verdict_read: the strict P6 verdict read (D3, R3-C-2).

@pytest.mark.parametrize(("text", "expected"), [
    ("**Verdict:** REQUEST CHANGES", "REQUEST CHANGES"),
    ("Verdict: APPROVE", "APPROVE"),
    ("- **Verdict:** APPROVE WITH CONDITIONS", "APPROVE WITH CONDITIONS"),
    ("**Verdict: REQUEST CHANGES** — fix X", "REQUEST CHANGES"),
    ("## Verdict: APPROVE", "APPROVE"),
    ('{"verdict": "REQUEST_CHANGES"}', "REQUEST CHANGES"),
    ("Verdict: APPROVE WITH CONDITIONS", "APPROVE WITH CONDITIONS"),
    ("Verdict: REQUEST_CHANGES", "REQUEST CHANGES"),
    # Liberal on format, strict on meaning: one meaning each, so they are read (the corpus of real reports).
    ("## Verdict\n\nAPPROVE", "APPROVE"),
    ("verdict: APPROVE", "APPROVE"),
    ("## Verdict\n\nREQUEST CHANGES. B-1 and B-2 block.", "REQUEST CHANGES"),
])
def test_verdict_shapes(text, expected):
    assert read(text) == expected


@pytest.mark.parametrize("text", [
    "Verdict: APPROVED",
    "Verdict: BLOCK",
    '{"Verdict": "APPROVE"}',
    "- Round 1 verdict: APPROVE",
    "no verdict here",
])
def test_unreadable_verdicts_are_none(text):
    assert read(text) is None


@pytest.mark.parametrize("text", [
    pytest.param("Verdict: REQUEST CHANGES\n\nVerdict: APPROVE", id="the_last_verdict_line_wins"),
    pytest.param("- prior round: Verdict: REQUEST CHANGES\n\nVerdict: APPROVE",
                 id="a_quoted_prior_verdict_in_a_bullet_does_not_count"),
    pytest.param("Verdict: APPROVE\n\n| r1 | Verdict: REQUEST CHANGES |",
                 id="a_quoted_prior_verdict_in_a_table_does_not_count"),
])
def test_the_deciding_verdict_line(text):
    assert read(text) == "APPROVE"


# --- adversarial_ids_by_section: P6 ids come only from item lines in their own section; the carried heading names the bytes.

REPORT = """Mode: plan-gate

## Findings on the prior round

| C-1 | closed |

## Blocking issues

### B-1. The verdict read skips the filter
Body that names R3-B-1 and B-7.

- **B-2.** list form
- B-12. second list form

## Conditions

### C-1. Name the step
The body of C-1 mentions C-2.
### B-3. wrong section

```
### C-9. fenced example
```

Verdict: REQUEST CHANGES
"""


def test_ids_are_defined_only_by_item_lines_in_their_section():
    blocking, conditions = adversarial_ids(REPORT)
    assert blocking == {"B-1", "B-2", "B-12"}
    assert conditions == {"C-1"}


def test_a_colon_heading_is_not_an_id_but_the_body_is_not_empty():
    text = "## Blocking issues\n\n### B-1: title\nbody\n"
    assert adversarial_ids(text) == (set(), set())
    assert blocking_body(text)


def test_carried_heading_names_file_and_raw_byte_hash(tmp_path):
    lf = tmp_path / "adversarial_x.md"
    lf.write_bytes(b"Verdict: APPROVE\n")
    crlf = tmp_path / "adversarial_x_crlf.md"
    crlf.write_bytes(b"Verdict: APPROVE\r\n")
    heading = carried_heading(lf)
    assert heading.startswith("## Carried adversarial findings (adversarial_x.md @ ")
    assert len(heading.rsplit("@ ", 1)[1].rstrip(")")) == 12
    assert carried_heading(lf).rsplit("@", 1)[1] != carried_heading(crlf).rsplit("@", 1)[1]
    lf.write_bytes(b"Verdict: REQUEST CHANGES\n")
    assert carried_heading(lf) != heading


# --- adversarial_gate_reads_verdict: the P6 gate and P9 read the adversarial verdict (D4, R2 to R6).

BLOCKING = "## Blocking issues\n\n### B-1. one\n\n### B-2. two\n"
CONDS = "## Conditions\n\n### C-1. one\n\n### C-2. two\n"


@pytest.fixture
def lp(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    return Loop(repo)


def p6(lp):
    return ar.check_adversarial_verdict(lp.ctx, lp.session, stage="p6")


def p9(lp):
    return ar.check_adversarial_verdict(lp.ctx, lp.session, stage="p9")


def rounds(lp, n, verdict="REQUEST CHANGES", body=BLOCKING):
    """n rounds, each by a new agent, each a new file; the last report is returned."""
    path = None
    for i in range(1, n + 1):
        path = lp.report(verdict, round_n=i, agent=f"peer-{i}", body=body)
    return path


def carry(lp, report, ids):
    lp.write_plan(f"\n{ar.carried_heading(report)}\n\n" + "".join(f"- {i} -> Phase 1\n" for i in ids))


# --- below the limit (Acceptance 1, 14) -------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 2])
def test_request_changes_below_the_limit_fails_with_the_count_and_next_step(lp, n):
    report = rounds(lp, n)
    expected = [
        f"adversarial-review: {report.name} verdict is REQUEST CHANGES (cycle {n} of 3). "
        "Run /plan-update, then /plan-review, then /de-adversarial-reviewer plan-gate."
    ]
    assert p6(lp) == expected and p9(lp) == expected


def test_at_the_p3_limit_the_message_drops_plan_review(lp):
    review = lp.ctx / "reviews" / "plan_gh-1_x.md"
    review.parent.mkdir(parents=True)
    for i in range(5):
        review.write_text(f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {RUN}\nVerdict: READY TO EXECUTE {i}\n")
        lp.attest(review, f"reviewer-{i}")
    report = rounds(lp, 1)
    assert p6(lp) == [
        f"adversarial-review: {report.name} verdict is REQUEST CHANGES (cycle 1 of 3). "
        "Run /plan-update, then /de-adversarial-reviewer plan-gate."
    ]


# --- at the limit (Acceptance 2, R2-MF-3, R5-N4-3, F2, G-C2) ----------------------------------------


def test_at_the_limit_each_id_must_be_carried(lp):
    report = rounds(lp, 3)
    heading = ar.carried_heading(report)
    assert p9(lp) == [
        "adversarial-review: cycle limit reached (3 cycles). Do not run P6 again. Run /plan-update. "
        f'Add "{heading}" and map B-1, B-2 to the phase whose test settles each.'
    ]
    carry(lp, report, ["B-1", "B-2"])
    assert p6(lp) == [] and p9(lp) == []


def test_at_the_limit_conditions_count_too(lp):
    report = rounds(lp, 3, body="## Blocking issues\n\n### B-1. one\n\n" + "## Conditions\n\n### C-1. one\n")
    carry(lp, report, ["B-1"])
    assert p6(lp)[0].endswith('map C-1 to the phase whose test settles each.')
    carry(lp, report, ["B-1", "C-1"])
    assert p9(lp) == []


def test_at_the_limit_with_no_ids_a_non_empty_section_is_the_evidence(lp):
    report = rounds(lp, 3, body="")
    assert p9(lp)
    lp.write_plan(f"\n{ar.carried_heading(report)}\n\n- the blocking items -> Phase 1\n")
    assert p9(lp) == []


def test_blocking_items_with_no_defined_id_fail_closed(lp):
    report = rounds(lp, 3, body="## Blocking issues\n\n### B-1: colon title\nbody\n")
    lp.write_plan(f"\n{ar.carried_heading(report)}\n\n- something -> Phase 1\n")
    assert p9(lp) == [
        f'adversarial-review: {report.name} has blocking items with no B-n id. Write each item as "### B-n. <title>".'
    ]


def test_adjacent_ids_do_not_clear_each_other(lp):
    report = rounds(lp, 3, body="## Blocking issues\n\n### B-1. one\n### B-12. twelve\n\n## Conditions\n\n### C-1. a\n### C-10. b\n")
    carry(lp, report, ["B-12", "C-10"])
    assert p9(lp)[0].endswith("map B-1, C-1 to the phase whose test settles each.")


def test_a_fourth_report_over_the_limit_uses_the_carry_rule(lp):
    report = rounds(lp, 4)
    carry(lp, report, ["B-1", "B-2"])
    assert p9(lp) == []


# --- APPROVE WITH CONDITIONS and APPROVE (Acceptance 3, 4, R2-MF-3, R3-C-2, F5) --------------------


def test_conditions_pass_p6_and_need_the_plan_at_p9(lp):
    report = lp.report("APPROVE WITH CONDITIONS", body=CONDS)
    assert p6(lp) == []
    carry(lp, report, ["C-1"])
    assert p9(lp) == [
        f'adversarial-review: {report.name} conditions not in the plan under "{ar.carried_heading(report)}": '
        "C-2. Run /plan-update."
    ]
    carry(lp, report, ["C-1", "C-2"])
    assert p9(lp) == []


def test_the_gate_note_names_open_conditions_and_is_gone_when_all_are_carried(lp):
    report = lp.report("APPROVE WITH CONDITIONS", body=CONDS)
    note = ar.adversarial_note(lp.ctx, lp.session)
    assert note == (f'conditions C-1, C-2 open: run /plan-update and add "{ar.carried_heading(report)}" before P9')
    carry(lp, report, ["C-1", "C-2"])
    assert ar.adversarial_note(lp.ctx, lp.session) is None


def test_conditions_with_no_ids_need_a_non_empty_section_at_p9(lp):
    report = lp.report("APPROVE WITH CONDITIONS")
    assert p6(lp) == []
    assert p9(lp) == [
        f'adversarial-review: {report.name} conditions are not carried under "{ar.carried_heading(report)}". '
        "Run /plan-update."
    ]
    lp.write_plan(f"\n{ar.carried_heading(report)}\n\n- the conditions -> Phase 2\n")
    assert p9(lp) == []


def test_approve_passes_both(lp):
    lp.report("APPROVE")
    assert p6(lp) == [] and p9(lp) == []


# --- what counts (Acceptance 5, 10, 12, 13, R2-MF-2, R3-B-1, R4-C-2, R6-MF-1) -----------------------


def test_another_runs_report_does_not_count(lp):
    lp.report("APPROVE")
    newer = lp.report("REQUEST CHANGES", round_n=2, run=OTHER_RUN, agent="peer-9")
    os.utime(newer, (9_999_999_999, 9_999_999_999))
    assert p9(lp) == []


def test_p9_fails_when_this_run_has_no_counted_report(lp):
    lp.report("APPROVE", run=OTHER_RUN)
    assert p9(lp) == [f"adversarial-review: no plan-gate report of run {RUN}. Run /de-adversarial-reviewer plan-gate."]


def test_p9_keeps_the_filter_reason(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    lp = Loop(repo, orchestrator_source="missing")
    lp.report("APPROVE")
    errors = p9(lp)
    assert errors == [f"adversarial-review: {ar.ORCHESTRATOR_UNKNOWN}"]
    assert "no plan-gate report" not in errors[0]


def test_an_unattested_newer_approve_does_not_decide_on_claude(lp):
    attested = lp.report("REQUEST CHANGES", agent="peer-1")
    newer = lp.report("APPROVE", round_n=2, attest=False)
    os.utime(newer, (9_999_999_999, 9_999_999_999))
    assert p6(lp) == [
        f"adversarial-review: {attested.name} verdict is REQUEST CHANGES (cycle 1 of 3). "
        "Run /plan-update, then /plan-review, then /de-adversarial-reviewer plan-gate."
    ]


def test_on_another_host_the_newer_approve_decides(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    lp = Loop(repo, host="other")
    lp.report("REQUEST CHANGES")
    lp.report("APPROVE", round_n=2)
    assert p6(lp) == []


def test_a_round_two_report_that_reuses_an_id_needs_its_own_heading(lp):
    r1 = lp.report("APPROVE WITH CONDITIONS", body="## Conditions\n\n### C-1. first\n", agent="peer-1")
    carry(lp, r1, ["C-1"])
    assert p9(lp) == []
    r2 = lp.report("APPROVE WITH CONDITIONS", round_n=2, body="## Conditions\n\n### C-1. new text\n", agent="peer-2")
    assert p9(lp)[0].startswith(f"adversarial-review: {r2.name} conditions not in the plan")
    lp.write_plan(f"\n{ar.carried_heading(r1)}\n\n- C-1 -> Phase 1\n\n{ar.carried_heading(r2)}\n\n- C-1 -> Phase 2\n")
    assert p9(lp) == []


def test_no_readable_verdict_fails(lp):
    report = lp.report(None, body="## Verdict\n\nAPPROVED, mostly")
    expected = [
        f"adversarial-review: {report.name} has no readable verdict. "
        'Write one line "Verdict: APPROVE | APPROVE WITH CONDITIONS | REQUEST CHANGES".'
    ]
    assert p6(lp) == expected and p9(lp) == expected


def test_a_spec_pressure_report_does_not_decide(lp):
    lp.report("APPROVE")
    sp = lp.report("REQUEST CHANGES", mode="spec-pressure", name=f"adversarial_{lp.slug}_spec-pressure.md", agent="peer-2")
    os.utime(sp, (9_999_999_999, 9_999_999_999))
    assert p6(lp) == [] and p9(lp) == []


def test_a_report_with_no_mode_line_names_the_shadow_cause(lp):
    report = lp.report("REQUEST CHANGES", mode=None, body="## Blocking Issues\n\n### B-1. host copy\n")
    expected = [
        f'adversarial-review: {report.name} has no line "Mode: plan-gate". The peer did not use '
        "$CADENCE_HOME/skills/de-adversarial-reviewer/SKILL.md. A host skill with the same name hides it. "
        "Move that skill out of the host skills directory, then run python3 $CADENCE_HOME/scripts/preflight.py until it passes."
    ]
    assert p6(lp) == expected and p9(lp) == expected
    assert "install.sh" not in expected[0]


# --- P9 integration and the CLI (R3-C-1, F4) -------------------------------------------------------


def test_p9_runs_the_adversarial_check(lp, monkeypatch):
    rounds(lp, 1)
    errors = ar.check_required_before_execution(lp.ctx, worktree=lp.repo)
    assert any("verdict is REQUEST CHANGES (cycle 1 of 3)" in e for e in errors)


def test_the_gate_prints_the_note(lp, monkeypatch, capsys):
    lp.report("APPROVE WITH CONDITIONS", body=CONDS)
    monkeypatch.setattr(gate_common, "require_lean_delegation", lambda *_a, **_k: [])
    assert gate_common.run_artifact_gate("adversarial-review-gate", lp.ctx, check=True) == 0
    assert "conditions C-1, C-2 open: run /plan-update" in capsys.readouterr().out


def _heading_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/adversarial_review_gate.py", *args, cwd=cwd)


def test_heading_cli(lp):
    report = lp.report("APPROVE")
    proc = _heading_cli(lp.repo, "--heading", str(report))
    assert proc.returncode == 0 and proc.stdout == ar.carried_heading(report) + "\n"
    proc = _heading_cli(lp.repo, "--heading", str(lp.ctx / "adversarial" / "missing.md"))
    assert proc.returncode == 1 and "not readable" in proc.stderr


# --- R2 F12: both review gates are run-scoped; F13: P9 checks conditions exactly once -------------

NO_PEER_FILE = "no verdict file whose content matches what a delegated peer last wrote"


def _plan_review(lp, run: str) -> Path:
    path = lp.ctx / "reviews" / f"plan_{lp.slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"RUN_BY_COMMAND: plan-review\nPipeline-Run: {run}\n**Ticket:** gh-1\n\nVerdict: READY TO EXECUTE\n",
                    encoding="utf-8")
    lp.attest(path, "reviewer-1")
    return path


@pytest.mark.parametrize("gate", ["adversarial-review-gate", "plan-review-gate"])
@pytest.mark.parametrize("own_run", [False, True])
def test_a_review_gate_counts_only_its_own_runs_report(lp, monkeypatch, capsys, gate, own_run):
    monkeypatch.setattr(gate_common, "require_lean_delegation", lambda *_a, **_k: [])
    run = RUN if own_run else OTHER_RUN
    if gate == "adversarial-review-gate":
        lp.report("APPROVE", run=run)
    else:
        _plan_review(lp, run)
    rc = gate_common.run_artifact_gate(gate, lp.ctx, check=True)
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert (NO_PEER_FILE in out) is (not own_run), out
    if own_run:
        assert rc == 0, out


def test_p9_names_open_conditions_exactly_once(lp):
    report = lp.report("APPROVE WITH CONDITIONS", body=CONDS)
    carry(lp, report, ["C-1"])
    errors = ar.check_required_before_execution(lp.ctx, worktree=lp.repo)
    mine = [e for e in errors if e.startswith("adversarial-review:")]
    assert mine == [
        f'adversarial-review: {report.name} conditions not in the plan under "{ar.carried_heading(report)}": '
        "C-2. Run /plan-update."
    ]


# --- p6_report_name_matrix: a counted P6 report whose name does not use the plan stem is named, never dropped.

SLUG = "p6_limit_ab12"
BLOCK = "## Blocking issues\n\n### B-1. one\n"


def misnamed(name: str) -> str:
    return (f"adversarial-review: {name} does not name the plan. Rename it "
            f"adversarial_{SLUG}.md (round 1) or adversarial_{SLUG}_r<n>.md (round n).")


@pytest.fixture
def named_lp(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    return Loop(repo, slug=SLUG)


def at(path: Path, t: float) -> Path:
    os.utime(path, (t, t))
    return path


@pytest.mark.parametrize("kwargs, expected", [
    pytest.param({}, [], id="a_report_named_after_the_plan_stem_decides"),
    pytest.param({"name": "adversarial_p6_limit.md"}, [misnamed("adversarial_p6_limit.md")],
                 id="a_report_without_the_short_id_is_named"),
    pytest.param({"name": "adversarial_p6_limit.md", "attest": False},
                 ["adversarial-review: no plan-gate report of run {run}. Run /de-adversarial-reviewer plan-gate."],
                 id="an_unattested_misnamed_report_is_not_named"),
])
def test_one_approve_report_by_name(named_lp, kwargs, expected):
    named_lp.report("APPROVE", **kwargs)
    assert p6(named_lp) == [e.format(run=named_lp.run) for e in expected]


def test_a_misnamed_round_newer_than_the_deciding_report_is_named(named_lp):
    at(named_lp.report("APPROVE", agent="a1"), 100)
    at(named_lp.report("REQUEST CHANGES", agent="a2", body=BLOCK, name="adversarial_p6_limit_r2.md"), 200)
    assert p6(named_lp) == [misnamed("adversarial_p6_limit_r2.md")]


def test_an_older_misnamed_report_does_not_block_a_newer_correct_one(named_lp):
    at(named_lp.report("REQUEST CHANGES", agent="a1", body=BLOCK, name="adversarial_p6_limit.md"), 100)
    at(named_lp.report("APPROVE", round_n=2, agent="a2"), 200)
    assert p6(named_lp) == []


@pytest.mark.parametrize("name", [f"adversarial_{SLUG}_spec-pressure.md", f"adversarial_{SLUG}_spec_pressure.md"])
def test_a_spec_pressure_report_in_either_spelling_never_decides(named_lp, name):
    at(named_lp.report("APPROVE", agent="a1"), 100)
    at(named_lp.report("REQUEST CHANGES", mode="plan-gate", agent="a2", body=BLOCK, name=name), 200)
    assert p6(named_lp) == []
    assert ar.adversarial_round(named_lp.ctx / "adversarial" / name, SLUG) is None
