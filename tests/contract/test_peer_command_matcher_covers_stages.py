"""cd-64: stages.json, prompt_shape and peer_models agree on every peer command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import handoff  # noqa: E402
import prompt_shape  # noqa: E402
from load_stages import load_stages  # noqa: E402
from peer_models import PEER_MODEL_SLUGS, SLUG_FOR_STAGE  # noqa: E402
from prompt_shape import delegation_stage_for_prompt, peer_command_row  # noqa: E402

ROWS = [s for s in load_stages(ROOT) if str(s.get("command", "")).startswith("/")]
LEAN_FORM = {
    "test-benchmark-plan": "/test-benchmark plan x",
    "draft-pr": "/commit-push-pr --draft",
    "adversarial-review": "/de-adversarial-reviewer plan-gate x",
}


def lean(row: dict) -> str:
    return LEAN_FORM.get(row["slug"], row["command"] + " x")


def test_a_each_row_command_names_its_own_row():
    matched = {peer_command_row(row["command"] + " x", ROWS)["slug"] for row in ROWS}
    assert matched == {row["slug"] for row in ROWS}
    for row in ROWS:
        assert peer_command_row(row["command"] + " x", ROWS)["slug"] == row["slug"]


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["slug"])
def test_b_the_record_stage_of_a_row_is_the_stage_of_its_lean_form(row):
    stage = delegation_stage_for_prompt(row["command"])
    assert stage is not None
    assert stage == delegation_stage_for_prompt(lean(row))


def test_c_the_drop_categories_equal_the_handoff_set():
    assert prompt_shape._DROP_CATEGORIES == handoff._DROP_CATEGORIES


# The results of task_prompt_block_reason and stage_for_prompt before cd-64 (e5c3a92).
BEFORE = [
    ("/plan-review foo", None, "plan-review"),
    ("/plan-review foo\nReturn to the main agent ONLY", "Task prompt must be a single line; found newline", None),
    ("/test-benchmark plan x", None, "test-benchmark"),
    ("/test-benchmark --code 62", None, "test-benchmark"),
    ("Explore the repo", None, None),
    ("Return to the main agent with a list", "Task prompt contains poison: 'Return to the main agent'", None),
    ("/cadence:vibe-test x.md", None, "vibe-test"),
    ("Please run /plan-review x.md", None, None),
]


@pytest.mark.parametrize(("prompt", "reason", "stage"), BEFORE)
def test_d_the_existing_shape_functions_keep_their_results(prompt, reason, stage):
    assert prompt_shape.task_prompt_block_reason(prompt) == reason
    assert prompt_shape.stage_for_prompt(prompt) == stage


@pytest.mark.parametrize("slug", sorted(PEER_MODEL_SLUGS))
def test_e_peer_models_maps_each_record_stage_back_to_its_slug(slug):
    row = next(r for r in ROWS if r["slug"] == slug)
    assert SLUG_FOR_STAGE[delegation_stage_for_prompt(row["command"])] == slug
