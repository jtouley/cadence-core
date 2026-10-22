"""The plan review round: P3, P6 and P7 review one plan sha in parallel, and P4 answers them once.

Each reviewer records its findings by section. The round joins the must-fix rows into one file for
/plan-update, which may change only the named sections. In the next round only a reviewer that had a
must-fix, or a finding in a section that the update changed, reviews again.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import activated_repo, code_paths, read_session, run_py, write_files

code_paths()
from phase_ledger import phase_key, phase_keys, phase_sections  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PLAN = """# Plan: export

RUN_BY_COMMAND: plan-create
Ticket: {task}
Pipeline-Run: {run}

## Goal

Export the ledger as CSV.

### Phase 1: query

Add `Ledger.expenses(month)`.

### Phase 2: render

Write the CSV.
"""


@pytest.fixture
def repo(tmp_path):
    repo = activated_repo(tmp_path / "proj")
    session = read_session(repo)
    # The round mechanics need a second P3 round, and P6 has one cycle: the project sets both.
    write_files(repo, {".cadence.json": json.dumps({"packs": {"append": ["spec-review", "test-plan"]},
                                                     "plan_review_cycles": 2, "adversarial_cycles": 1}),
                       ".cursor/plans/export_ab12.plan.md": PLAN.format(task=session["task_id"],
                                                                        run=session["pipeline_run_id"])})
    return repo


def _plan(repo: Path) -> Path:
    return repo / ".cursor" / "plans" / "export_ab12.plan.md"


def _wave(repo: Path, *args: str, **env):
    return run_py("scripts/plan_wave.py", *args, cwd=repo, **env)


def _add(repo: Path, stage: str, ident: str, phase: str, severity: str = "must-fix"):
    row = {"id": ident, "phase": phase, "location": "x", "severity": severity, "evidence": "e"}
    return _wave(repo, "add", "--stage", stage, "--json", json.dumps(row))


def _review(repo: Path, must_fix: str = "None.", verdict: str = "READY WITH CHANGES") -> None:
    session = read_session(repo)
    sha = hashlib.sha256(_plan(repo).read_bytes()).hexdigest()
    write_files(repo, {".context/reviews/plan_export_ab12.md":
                       f"RUN_BY_COMMAND: plan-review\nTicket: {session['task_id']}\nPipeline-Run: "
                       f"{session['pipeline_run_id']}\nLast reviewed plan sha256: {sha}\n\n## Must-fix items\n\n"
                       f"{must_fix}\n\nVerdict: {verdict}\n"})


def _card(repo: Path, scores: dict | None = None) -> None:
    session = read_session(repo)
    card = {"RUN_BY_SKILL": "test-benchmark", "task_id": session["task_id"], "Pipeline-Run": session["pipeline_run_id"],
            "scores": scores or {"coverage_of_behavior": 8}, "verdict": "PASS", "findings": []}
    write_files(repo, {f".context/benchmarks/{session['task_id']}_plan.json": json.dumps(card)})


def _round_one(repo: Path) -> None:
    """P3 and P6 each name Phase 2; P7 has a nit in Phase 1."""
    assert "send these peers in one message, in parallel" in _wave(repo).stdout
    _review(repo, "- MF-1. Name the quote rule.")
    assert _add(repo, "plan-review", "MF-1", "Phase 2").returncode == 0
    assert _wave(repo, "done", "--stage", "plan-review").returncode == 0
    assert _add(repo, "adversarial-review", "B-1", "Phase 2").returncode == 0
    assert _wave(repo, "done", "--stage", "adversarial-review").returncode == 0
    assert _add(repo, "test-benchmark-plan", "F01", "Phase 1", "nit").returncode == 0
    _card(repo)
    assert _wave(repo, "done", "--stage", "test-benchmark-plan").returncode == 0


def test_the_phase_names_are_exact_and_the_ledger_is_a_record_section():
    # Liberal on format: a reviewer's own spelling of a section names it (`Phases 1 to 4`).
    assert [phase_key(x) for x in ("global", "Phase 2", "phase 2", "P2", "Phase 02")] == ["global"] + ["Phase 2"] * 4
    assert phase_keys("Phases 1 to 4") == ["Phase 1", "Phase 2", "Phase 3", "Phase 4"]
    assert phase_keys("Phase 1 and Phase 3") == phase_keys("phases 1, 3") == ["Phase 1", "Phase 3"]
    assert phase_keys("Assumptions row G-1") == phase_keys("the test spec") == []
    text = PLAN.format(task="t", run="r") + "\n## Revision ledger (review abc)\n\n| MF-1 |\n"
    assert set(phase_sections(text)) == {"global", "Phase 1", "Phase 2", "record"}


def test_round_one_sends_every_reviewer_in_one_message(repo):
    proc = _wave(repo)
    assert proc.returncode == 1
    plan = ".cursor/plans/export_ab12.plan.md"
    for command in ("/plan-review", "/de-adversarial-reviewer plan-gate", "/test-benchmark plan"):
        assert f"{command} {plan}" in proc.stdout
    assert _wave(repo, "--check").returncode == 1


def test_a_finding_names_a_section_of_the_plan_exactly(repo):
    _wave(repo)
    for bad in ("Phase 9", "record", "the CSV writer"):
        proc = _add(repo, "plan-review", "MF-1", bad)
        assert proc.returncode == 1 and "is not a section of the plan" in proc.stderr
    assert _add(repo, "plan-review", "MF-1", "Phase 2").returncode == 0


def test_p3_is_done_only_with_a_row_for_each_must_fix_item_of_its_review(repo):
    _wave(repo)
    assert "write the plan review of this plan first" in _wave(repo, "done", "--stage", "plan-review").stderr
    _review(repo, "- MF-1. Name the quote rule.\n- MF-2. Test an empty month.")
    _add(repo, "plan-review", "MF-1", "Phase 2")
    proc = _wave(repo, "done", "--stage", "plan-review")
    assert proc.returncode == 1 and "MF-2" in proc.stderr


def test_the_round_file_joins_the_rows_by_section_for_one_update(repo):
    _round_one(repo)
    proc = _wave(repo)
    assert proc.returncode == 1
    assert "/plan-update .cursor/plans/export_ab12.plan.md --from-review .context/plan-findings/round-1.md" in proc.stdout
    text = (repo / ".context" / "plan-findings" / "round-1.md").read_text(encoding="utf-8")
    assert "### Phase 2" in text and "| MF-1 | plan-review |" in text and "| B-1 | adversarial-review |" in text
    assert "- Phase 2: adversarial-review, plan-review." in text  # two reviewers name one section
    assert "F01 (test-benchmark-plan, Phase 1)" in text and "### Phase 1" not in text  # a nit is not for P4


def _answer(repo: Path, change: str) -> None:
    sha = hashlib.sha256((repo / ".context" / "plan-findings" / "round-1.md").read_bytes()).hexdigest()[:12]
    plan = _plan(repo).read_text(encoding="utf-8").replace("Write the CSV.", change)
    _plan(repo).write_text(plan + f"\n## Revision ledger (review {sha})\n\n| Id | Change |\n|---|---|\n"
                                  "| MF-1 | quote rule |\n| B-1 | quote rule |\n", encoding="utf-8")


def test_the_update_changes_only_the_named_sections_and_answers_each_id(repo):
    _round_one(repo)
    _wave(repo)
    _answer(repo, "Write the CSV with the RFC 4180 quote rule.")
    _plan(repo).write_text(_plan(repo).read_text(encoding="utf-8").replace("Add `Ledger.expenses(month)`.", "x"),
                           encoding="utf-8")
    proc = _wave(repo)
    assert proc.returncode == 1 and "changed Phase 1, which no finding of round 1 names" in proc.stderr
    gate = run_py("scripts/plan_update_gate.py", cwd=repo)
    assert gate.returncode == 1 and "changed Phase 1" in gate.stderr + gate.stdout


def test_round_two_reviews_again_only_where_a_finding_was_open(repo):
    _round_one(repo)
    _wave(repo)
    _answer(repo, "Write the CSV with the RFC 4180 quote rule.")
    proc = _wave(repo)
    assert proc.returncode == 1
    assert "/plan-review" in proc.stdout  # P3 had a must-fix and is below its limit of two
    assert "/de-adversarial-reviewer" not in proc.stdout  # P6 is at its limit of one: P4 carries B-1
    assert "/test-benchmark plan" not in proc.stdout  # its nit is in Phase 1, which did not change
    from plan_wave import carried

    context = repo / ".context"
    assert carried(context, read_session(repo), "test-benchmark-plan")
    assert carried(context, read_session(repo), "adversarial-review")
    assert not carried(context, read_session(repo), "plan-review")


def test_a_clear_round_passes_and_a_p3_pass_stands_when_p3_is_not_due(repo):
    _wave(repo)
    _review(repo, verdict="READY TO EXECUTE")
    _card(repo)
    for stage in ("plan-review", "adversarial-review"):
        assert _wave(repo, "done", "--stage", stage).returncode == 0
    _add(repo, "test-benchmark-plan", "F01", "Phase 2")  # P7 has no cycle limit, so it reviews again
    _wave(repo, "done", "--stage", "test-benchmark-plan")
    _wave(repo)
    sha = hashlib.sha256((repo / ".context" / "plan-findings" / "round-1.md").read_bytes()).hexdigest()[:12]
    plan = _plan(repo).read_text(encoding="utf-8").replace("Write the CSV.", "Write the CSV, quoted.")
    _plan(repo).write_text(plan + f"\n## Revision ledger (review {sha})\n\n| F01 | quoted |\n", encoding="utf-8")
    assert "/test-benchmark plan" in _wave(repo).stdout
    from artifact_requirements import plan_changed_after_review

    assert plan_changed_after_review(repo / ".context", read_session(repo)) == []  # P3 is not due: its pass stands
    _wave(repo, "done", "--stage", "test-benchmark-plan")
    proc = _wave(repo)
    assert proc.returncode == 0 and "OK  plan-review-round" in proc.stdout


def test_the_stop_guard_reads_the_round_and_writes_nothing(repo):
    _wave(repo)
    before = (repo / ".context" / "plan-findings" / "rounds.json").read_text(encoding="utf-8")
    from plan_wave import route

    row = route(repo / ".context", read_session(repo))
    assert row and row["block"] == "P3" and "send these peers in one message" in row["command"]
    _wave(repo, "--check", CADENCE_GATES_READONLY="1")
    assert (repo / ".context" / "plan-findings" / "rounds.json").read_text(encoding="utf-8") == before


def test_parallel_status_changes_keep_every_row(tmp_path):
    """Peers of a round run in parallel: a set-status must not lose another writer's change."""
    from conftest import ledger_row

    ledger = tmp_path / "findings.jsonl"
    ids = [f"gh-1-F{i}" for i in range(1, 13)]
    ledger.write_text("".join(json.dumps(ledger_row(i)) + "\n" for i in ids), encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(ROOT / "scripts" / "findings_io.py"), "--ledger", str(ledger),
                               "set-status", "--id", i, "--status", "deferred", "--ticket", "#9"],
                              cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in ids]
    assert all(p.wait() == 0 for p in procs), [p.stderr.read() for p in procs]
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["id"] for r in rows) == sorted(ids) and {r["status"] for r in rows} == {"deferred"}


def test_the_round_file_is_written_when_the_last_reviewer_is_done(repo):
    """The orchestrator ran only --check, so the round file never existed and P4 answered nothing."""
    _round_one(repo)
    assert (repo / ".context" / "plan-findings" / "round-1.md").is_file()
    assert "round-1.md" in _wave(repo, "--check").stdout


def test_from_round_two_a_new_must_fix_targets_only_a_changed_or_open_section(repo):
    """P6 raised 6, 4, 3, 4 must-fix items, new ones in sections that no update touched."""
    _round_one(repo)
    _wave(repo)
    _answer(repo, "Write the CSV with the RFC 4180 quote rule.")
    _wave(repo)
    proc = _add(repo, "plan-review", "MF-2", "Phase 1")
    assert proc.returncode == 1 and "Record a finding in Phase 1 as a nit" in proc.stderr
    assert _add(repo, "plan-review", "MF-2", "Phase 1", "nit").returncode == 0
    assert _add(repo, "plan-review", "MF-3", "Phase 2").returncode == 0  # the update changed Phase 2


def test_the_cycle_limit_counts_the_rounds_a_reviewer_reviewed(tmp_path, monkeypatch):
    """An attest-based count read 0 P6 cycles after four reports, so P6 ran a fifth round."""
    from plan_wave import _at_limit

    (tmp_path / ".git").mkdir()  # the project root: its .cadence.json sets one cycle for P3 and P6
    (tmp_path / ".cadence.json").write_text(json.dumps({"plan_review_cycles": 1, "adversarial_cycles": 1}))
    monkeypatch.chdir(tmp_path)

    rounds = [{"done": {"adversarial-review": 2}}, {"done": {"adversarial-review": 1}}]
    assert _at_limit(rounds, "adversarial-review") and not _at_limit(rounds, "plan-review")
    assert _at_limit(rounds + [{"done": {"plan-review": 0}}], "plan-review")  # one review cycle
    assert not _at_limit(rounds, "test-benchmark-plan")
    assert _at_limit(rounds + [{"done": {"test-benchmark-plan": 3}}], "test-benchmark-plan")  # the round's limit


def test_a_plan_that_lost_its_header_is_named_as_such(repo):
    """An in-place update dropped the plan-create header, and the round said the run had no plan."""
    _wave(repo)
    _plan(repo).write_text(_plan(repo).read_text(encoding="utf-8").replace("RUN_BY_COMMAND: plan-create\n", ""),
                           encoding="utf-8")
    proc = _wave(repo)
    assert proc.returncode == 1 and "lost its header lines" in proc.stderr


def test_a_reviewer_of_an_open_round_is_held_until_it_records_done(repo):
    """Haiku reviewers wrote their report but no rows, and the orchestrator resumed each one."""
    code_paths()
    sys.path.insert(0, str(ROOT / "hooks"))
    from pipeline_stop_guard import _round_hold

    _wave(repo)
    context, session = repo / ".context", read_session(repo)
    assert "plan_wave.py\" add --stage test-benchmark-plan" in _round_hold(context, session, "test-benchmark-plan")
    assert "$CADENCE_HOME" not in _round_hold(context, session, "test-benchmark-plan")
    _card(repo)
    _wave(repo, "done", "--stage", "test-benchmark-plan")
    assert _round_hold(context, session, "test-benchmark-plan") is None


def test_the_p4_marker_is_not_a_change_and_a_change_outside_the_round_is_shown(repo):
    """P4 wrote `RUN_BY_COMMAND: plan-update` under the header three times, and the scope check failed
    each time with no line to undo. Its retry got the P3 review, not the round file, as its input."""
    from plan_wave import p4_input

    _round_one(repo)
    _wave(repo)
    _answer(repo, "Write the CSV with the RFC 4180 quote rule.")
    text = _plan(repo).read_text(encoding="utf-8")
    _plan(repo).write_text(text.replace("Pipeline-Run:", "RUN_BY_COMMAND: plan-update\nPipeline-Run:")
                           .replace("Export the ledger as CSV.", "Export the ledger as CSV or JSON."), encoding="utf-8")
    proc = _wave(repo)
    assert proc.returncode == 1 and "changed global" in proc.stderr
    assert "The change: - Export the ledger as CSV. | + Export the ledger as CSV or JSON." in proc.stderr
    assert "RUN_BY_COMMAND" not in proc.stderr
    assert p4_input(repo / ".context", read_session(repo)).name == "round-1.md"  # the retry answers the same round
    _plan(repo).write_text(text.replace("Pipeline-Run:", "RUN_BY_COMMAND: plan-update\nPipeline-Run:"), encoding="utf-8")
    assert "round 2 waits on" in _wave(repo).stderr
    # y3: a round that opened after it with no rows does not hide it, so a repair P4 still gets the round file
    assert p4_input(repo / ".context", read_session(repo)).name == "round-1.md"


def test_p7_is_done_only_with_a_scorecard_that_the_p7_gate_can_read(repo):
    """P7 put `mean`, `floor` and `must_fix_dimensions: []` in `scores`, and the P7 gate failed after the
    round, when the peer was gone. The gate now ignores the extra keys, and fails only a card with no dimension."""
    _wave(repo)
    assert "write the plan scorecard first" in _wave(repo, "done", "--stage", "test-benchmark-plan").stderr
    _card(repo, {"mean": 8.0, "must_fix_dimensions": []})
    proc = _wave(repo, "done", "--stage", "test-benchmark-plan")
    assert proc.returncode == 1 and "has no dimension scores" in proc.stderr
    from test_benchmark_gate import _plan_problems

    card = next((repo / ".context" / "benchmarks").glob("*_plan.json"))
    assert _plan_problems(card)[-1] == "send /test-benchmark plan <plan> again"
    _card(repo, {"coverage_of_behavior": 8, "mean": 8.0, "floor": 7, "must_fix_dimensions": []})  # extra keys are ignored
    assert _plan_problems(card) == []
    assert _wave(repo, "done", "--stage", "test-benchmark-plan").returncode == 0


def test_request_changes_is_a_p3_verdict_and_an_unknown_verdict_is_named(repo):
    """A Haiku run wrote `Verdict: REQUEST CHANGES`, a verdict that the skills offer: P3 dropped the review without a
    word, and the done check said to write the review that was already there."""
    _wave(repo)
    _review(repo, "- MF-1. Name the quote rule.", verdict="REQUEST CHANGES")
    _add(repo, "plan-review", "MF-1", "Phase 2")
    assert _wave(repo, "done", "--stage", "plan-review").returncode == 0
    _review(repo, "- MF-1. Name the quote rule.", verdict="NEEDS WORK")
    proc = _wave(repo, "done", "--stage", "plan-review")
    assert proc.returncode == 1 and "its verdict `NEEDS WORK` is not a plan-review verdict" in proc.stderr


def test_a_plan_that_changes_while_p4_writes_it_is_still_reviewed(repo):
    """The orchestrator ran plan_wave.py every 5 s while P4 edited the plan. Round 2 opened on a half-made
    update, the next edit opened round 3 with no reviewer due, and the round 1 fixes were never reviewed."""
    assert "send these peers in one message, in parallel" in _wave(repo).stdout
    _review(repo, "- MF-1. Name the quote rule.")
    _add(repo, "plan-review", "MF-1", "Phase 2")
    _wave(repo, "done", "--stage", "plan-review")
    _wave(repo, "done", "--stage", "adversarial-review")  # P6 has one cycle: it does not review again
    _add(repo, "test-benchmark-plan", "B-1", "Phase 2")  # the id that _answer answers
    _card(repo)
    _wave(repo, "done", "--stage", "test-benchmark-plan")
    _wave(repo)
    _answer(repo, "Write the CSV with the RFC 4180 quote rule.")
    assert "round 2 waits on" in _wave(repo).stderr
    _plan(repo).write_text(_plan(repo).read_text(encoding="utf-8").replace("quote rule.", "quote rule, CRLF ends."),
                           encoding="utf-8")
    proc = _wave(repo)
    assert "round 2 waits on plan-review, test-benchmark-plan" in proc.stderr  # opened again, not round 3
    rounds = json.loads((repo / ".context" / "plan-findings" / "rounds.json").read_text(encoding="utf-8"))["rounds"]
    assert [r["n"] for r in rounds] == [1, 2]
    _review(repo)
    assert _wave(repo, "done", "--stage", "plan-review").returncode == 0  # started: P7 is still due
    _plan(repo).write_text(_plan(repo).read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
    assert "round 3 waits on test-benchmark-plan" in _wave(repo).stderr


def test_a_finding_in_the_reviewers_own_words_is_recorded_once_per_section(repo):
    """A must-fix for `Phases 1 to 2` was refused, so its peer could not record it. (A severity in the
    reviewer's own word, such as `blocking`, goes to Jev: test_verdict_judge.py.)"""
    _wave(repo)
    row = {"id": "B-1.", "phase": "Phases 1 to 2", "location": "x", "severity": "Must-Fix", "evidence": "e"}
    proc = _wave(repo, "add", "--stage", "adversarial-review", "--json", json.dumps(row))
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [(r["id"], r["phase"], r["severity"]) for r in rows] == [("B-1", "Phase 1", "must-fix"), ("B-1", "Phase 2", "must-fix")]


def test_p3_done_counts_the_review_before_its_peer_returns(repo):
    """In a --plugin-dir session, verdict_join is on: P3 counts a review only with its peer's join
    row, which the host writes when the peer returns. The peer records done before that."""
    import peer_attest

    path = next((repo / ".context" / "sessions" / "by-worktree").glob("*.json"))
    session = {**json.loads(path.read_text(encoding="utf-8")), "verdict_join": True, "host": "claude",
               "orchestrator_source": "toplevel"}
    path.write_text(json.dumps(session), encoding="utf-8")
    _wave(repo)
    _review(repo, "- MF-1. Name the quote rule.", verdict="REQUEST CHANGES")
    review = repo / ".context" / "reviews" / "plan_export_ab12.md"
    _add(repo, "plan-review", "MF-1", "Phase 2")
    peer_attest.record(repo / ".context", session["pipeline_run_id"], "reviews/plan_export_ab12.md", "p3-peer",
                       peer_attest.sha256(review.read_bytes()))  # the hook's row for the peer's Write; no join row yet
    proc = _wave(repo, "done", "--stage", "plan-review")
    assert proc.returncode == 0, proc.stderr


def test_at_its_limit_p7_does_not_score_the_updated_plan_again(repo):
    """Stub replay of r5 at the shipped limit (one cycle): P7's round-one card failed a dimension, P4 answered its
    rows, and the next round did not name P7. The P7 gate asked for a rescore that the round no longer takes."""
    write_files(repo, {".cadence.json": json.dumps({"packs": {"append": ["spec-review", "test-plan"]},
                                                     "plan_review_cycles": 1, "adversarial_cycles": 1})})
    _wave(repo)
    _review(repo, "- MF-1. Name the quote rule.")
    _add(repo, "plan-review", "MF-1", "Phase 2")
    _wave(repo, "done", "--stage", "plan-review")
    _wave(repo, "done", "--stage", "adversarial-review")
    _add(repo, "test-benchmark-plan", "F01", "Phase 2")
    _card(repo, {"coverage_of_behavior": 8, "dry": 6})
    _wave(repo, "done", "--stage", "test-benchmark-plan")
    _wave(repo)
    gate = run_py("scripts/test_benchmark_gate.py", "--plan", cwd=repo)
    assert gate.returncode == 1 and "dry" in gate.stderr + gate.stdout  # the round is open: the card decides
    sha = hashlib.sha256((repo / ".context" / "plan-findings" / "round-1.md").read_bytes()).hexdigest()[:12]
    _plan(repo).write_text(_plan(repo).read_text(encoding="utf-8").replace("Write the CSV.", "Write the CSV, quoted.")
                           + f"\n## Revision ledger (review {sha})\n\n| Id | Change |\n|---|---|\n| MF-1 | quotes |\n"
                           "| F01 | one table |\n\n## Carried review findings\n\n- MF-1 -> Phase 2\n", encoding="utf-8")
    assert _wave(repo).returncode == 0  # the next round names no reviewer: each one is at its limit
    from prompt_shape import write_delegation_record

    session = read_session(repo)
    write_delegation_record(repo / ".context", run_id=session["pipeline_run_id"], stage="test-benchmark-plan",
                            command="/test-benchmark plan .cursor/plans/export_ab12.plan.md", source="test")
    gate = run_py("scripts/test_benchmark_gate.py", "--plan", cwd=repo)
    assert gate.returncode == 0, gate.stderr + gate.stdout


def test_a_round_that_ran_its_scripts_is_started_and_not_opened_again(tmp_path):
    """With one plan cycle, round 2 has no reviewer due, so it runs its scripts at once and P4 answers
    its round file. The round counted as never started, so the next poll dropped it and judged the update against
    round 1, and P4 could not pass."""
    from types import SimpleNamespace

    from plan_wave import _started

    rnd = SimpleNamespace(round_file=lambda n: tmp_path / f"round-{n}.md", rows=lambda stage, n: [])
    assert _started(rnd, {"n": 2, "due": [], "done": {}, "p5": [{"id": "W-1"}]})
    assert not _started(rnd, {"n": 2, "due": [], "done": {}, "p5": []})
    (tmp_path / "round-2.md").write_text("x", encoding="utf-8")
    assert _started(rnd, {"n": 2, "due": [], "done": {}})


def test_the_round_names_the_p6_carry_that_p9_checks(monkeypatch):
    """Runs passed the round with no `## Carried adversarial findings` heading and failed P9, where the
    repair P4 had no round file. The round now runs the carry part of P9's check and names it, with the round file."""
    from types import SimpleNamespace

    import artifact_requirements
    import plan_wave

    found = ['adversarial-review: cycle limit reached (1 cycles). Do not run P6 again. Run /plan-update. Add '
             '"## Carried adversarial findings (adversarial_t.md @ abc123abc123)" and map B-1 to the phase whose test '
             'settles each.',
             "adversarial-review: adversarial_t.md has no readable verdict."]
    monkeypatch.setattr(artifact_requirements, "check_adversarial_verdict", lambda *_a, **_k: found)
    [problem] = plan_wave._carry_problems(SimpleNamespace(ctx=Path("."), session={}))
    assert problem.startswith("plan-review-round: cycle limit reached")
    assert "## Carried adversarial findings (adversarial_t.md @ abc123abc123)" in problem
    assert problem.endswith("Send /plan-update again with the round file")
    monkeypatch.setattr(artifact_requirements, "check_adversarial_verdict", lambda *_a, **_k: [])
    assert plan_wave._carry_problems(SimpleNamespace(ctx=Path("."), session={})) == []
