"""Real peer reports from Haiku and Sonnet runs: each reader reads each form that the peers wrote.

tests/fixtures/peer_reports holds the smallest real file of each distinct form per reader, copied from
real runs, and expected.json holds what the readers read from each. A gate is liberal on format and strict on
meaning: a verdict under a `## Verdict` heading, a verdict with prose after it, or a `mean` key in `scores`
is read. A reader change that reads one of them differently fails here, with no LLM call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import code_paths

code_paths()
import artifact_requirements as ar  # noqa: E402
import deferral_adjudication_check as dac  # noqa: E402
import gate_common  # noqa: E402
import test_benchmark_gate as tb  # noqa: E402
from labeled_lines import verdict  # noqa: E402

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "peer_reports"
EXPECTED = json.loads((CORPUS / "expected.json").read_text(encoding="utf-8"))


def _card(text: str) -> dict:
    card = json.loads(text)
    dimensions = tb.dimension_scores(card)
    return {"dimensions": sorted(dimensions), "verdict": card.get("verdict"),
            "extra_keys": sorted(set(card.get("scores", {})) - set(dimensions))}


READERS = {
    "plan-review": lambda t: {"verdict": ar.verdict_in_text(t)},
    "adversarial": lambda t: {"verdict": ar.adversarial_verdict_in_text(t)},
    "pr-review": lambda t: {"verdict": verdict(t, gate_common.R1_VERDICTS), "must_fix": sorted(ar.review_must_fix_ids(t))},
    "vibe-test": lambda t: {"verdict": verdict(t, ("NOT READY", "READY")), "gaps": sorted(ar.spec_gap_ids(t))},
    "deferral-register": lambda t: {"issues": sorted({i.code for i in dac.adjudicate_deferral_register(t).issues})},
    "scorecard": _card,
}


def test_the_corpus_has_each_reader_and_no_file_without_an_expectation():
    files = {p.relative_to(CORPUS).as_posix() for p in CORPUS.rglob("*") if p.is_file() and p.name != "expected.json"}
    assert files == set(EXPECTED)
    assert {name.split("/")[0] for name in files} == set(READERS)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_real_report_reads_as_recorded(name):
    text = (CORPUS / name).read_text(encoding="utf-8")
    assert READERS[name.split("/")[0]](text) == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(n for n in EXPECTED if n.startswith("scorecard/")))
def test_a_real_scorecard_with_extra_keys_in_scores_is_readable(name):
    assert tb.card_shape_problems(CORPUS / name) == []


def test_the_meaning_holds_across_the_corpus():
    for name, read in EXPECTED.items():
        if name.startswith("pr-review/"):
            assert (read["verdict"] == "NO MERGE") == bool(read["must_fix"]), name  # a NO MERGE names its must-fix
        if name.startswith("vibe-test/") and read["verdict"] == "READY":
            assert read["gaps"] == [], name  # a ready request has no blocking gap


@pytest.mark.parametrize("written", ["PR_REVIEW_#7.md", "PR_REVIEW_7.md"])
def test_the_r1_review_reads_with_or_without_the_hash(tmp_path, written):
    """A run stopped at R1: the peer wrote `PR_REVIEW_1.md` three times, and R4 and the fix round looked only
    for `PR_REVIEW_#1.md`. Both names are the review of the PR; the `#` name wins when both exist."""
    reviews = tmp_path / "prs" / "reviews"
    reviews.mkdir(parents=True)
    assert ar.pr_review_file(tmp_path, 7).name == "PR_REVIEW_#7.md"  # nothing written: the name to ask for
    (reviews / written).write_text("RUN_BY_COMMAND: pr-review-local\nVerdict: NO MERGE\n", encoding="utf-8")
    assert ar.pr_review_file(tmp_path, 7) == reviews / written
    (reviews / "PR_REVIEW_#7.md").write_text("RUN_BY_COMMAND: pr-review-local\n", encoding="utf-8")
    assert ar.pr_review_file(tmp_path, 7).name == "PR_REVIEW_#7.md"
