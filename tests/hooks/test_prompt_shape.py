"""prompt_shape: the lean peer prompt, the peer-command matcher over stages.json, and the /cadence: plugin prefix."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from handoff import block_for_prompt  # noqa: E402
from load_stages import load_stages  # noqa: E402
from prompt_shape import (PromptShapeError, delegation_stage_for_prompt, hook_records_only, lean_peer_reason,  # noqa: E402
                          peer_command_row, stage_for_prompt, task_prompt_block_reason, validate_lean_prompt)


# --- prompt_shape: Lean Task prompt shape: slash + path, no parent briefing.

@pytest.mark.parametrize("command, prompt", [
    pytest.param("plan-review", "/plan-review .cursor/plans/foo.plan.md",
                 id="validate_lean_prompt_plan_review_path_returns_true"),
    pytest.param("de-adversarial-reviewer", "/de-adversarial-reviewer plan-gate .cursor/plans/foo.plan.md",
                 id="validate_lean_prompt_adversarial_plan_gate_path_returns_true"),
    pytest.param("vibe-test", "/vibe-test docs/spec.md", id="validate_lean_prompt_vibe_test_single_token_returns_true"),
])
def test_validate_lean_prompt_returns_true(command, prompt):
    assert validate_lean_prompt(command, prompt) is True


@pytest.mark.parametrize("command, prompt, match", [
    pytest.param("plan-review", "/plan-review foo\nReturn to the main agent", "newline",
                 id="validate_lean_prompt_newline_raises"),
    pytest.param("plan-review", "/plan-review foo Return to the main agent ONLY", "poison",
                 id="validate_lean_prompt_return_to_main_agent_raises"),
    pytest.param("de-adversarial-reviewer",
                 "/de-adversarial-reviewer based on the parent's summary of what matters " + ("x" * 20), None,
                 id="validate_lean_prompt_based_on_essay_raises"),
    pytest.param("plan-review", "/plan-review " + ("a" * 400), "400", id="validate_lean_prompt_over_400_chars_raises"),
])
def test_validate_lean_prompt_raises(command, prompt, match):
    with pytest.raises(PromptShapeError, match=match):
        validate_lean_prompt(command, prompt)


def test_task_prompt_block_reason_non_peer_one_line_allows():
    assert task_prompt_block_reason("Find callers of require_artifact and return paths only.") is None


def test_task_prompt_block_reason_multiline_blocks():
    reason = task_prompt_block_reason("Find callers.\nThen summarize what I think matters.")
    assert reason is not None
    assert "newline" in reason


# --- peer_command_matcher: cd-64: one peer-command matcher over stages.json rows, the lean reason, the hook-only marker.

ROWS = [s for s in load_stages(ROOT) if str(s.get("command", "")).startswith("/")]


def slug(prompt: str, *, normalize: bool = True) -> str | None:
    row = peer_command_row(prompt, ROWS, normalize=normalize)
    return row["slug"] if row else None


def row_for(name: str) -> dict:
    return next(r for r in ROWS if r["slug"] == name)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["slug"])
def test_each_peer_command_at_the_start_names_its_row(row):
    assert slug(row["command"] + " x.md") == row["slug"]
    assert slug("/cadence:" + row["command"][1:] + " x.md") == row["slug"]


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("/test-benchmark plan x", "test-benchmark-plan"),
        ("/test-benchmark --code x", "test-benchmark-pr"),
        ("/test-benchmark", "test-benchmark-pr"),
        ("/commit-push-pr --draft", "draft-pr"),
        ("/commit-push-pr", "draft-pr"),
        ("Run `/test-benchmark plan` on x.md", "test-benchmark-plan"),
    ],
)
def test_two_word_commands_take_the_longest_full_match(prompt, expected):
    assert slug(prompt) == expected


@pytest.mark.parametrize(
    "prompt",
    [
        "Please run /plan-review x.md",
        "You are the peer for Cadence block P3 (plan-review). Run the /plan-review procedure from the skill.",
        '("/plan-review x")',
        "Run `/plan-review` on x.md",
        "*/plan-review*",
        "_/plan-review_",
        "line one\n/plan-review x.md",
        "Run /plan-review.",
    ],
)
def test_a_peer_command_inside_text_is_a_token(prompt):
    assert slug(prompt) == "plan-review"


@pytest.mark.parametrize(
    "prompt",
    [
        "commands/plan-review.md",
        "skills/vibe-test/SKILL.md",
        "foo/plan-review",
        "x-/plan-review",
        "a./plan-review",
        "/plan-reviewer x",
        "/plan-review.md",
        "/plan-review/x",
        "/other:plan-review x",
        "Explore the repo and list the gate steps",
        "",
    ],
)
def test_paths_and_other_names_are_not_tokens(prompt):
    assert slug(prompt) is None


@pytest.mark.parametrize("prompt", ["\u200b/plan-review x.md", "／plan-review x.md"])  # \u200b: ZERO WIDTH SPACE
def test_a_non_ascii_form_matches_only_after_normalization(prompt):
    assert slug(prompt) == "plan-review"
    assert slug(prompt, normalize=False) is None


def test_the_first_token_in_the_text_wins():
    assert slug("Run /vibe-test, then /plan-review x.md") == "validate"


@pytest.mark.parametrize("prompt", ["/plan-review x.md", "/cadence:plan-review x.md", "  /plan-review x.md  "])
def test_a_lean_peer_prompt_has_no_reason(prompt):
    assert lean_peer_reason(prompt, row_for("plan-review"), ROWS) is None


@pytest.mark.parametrize(
    "row",
    [r for r in ROWS],
    ids=lambda r: r["slug"],
)
def test_each_documented_lean_form_has_no_reason(row):
    lean = {
        "test-benchmark-plan": "/test-benchmark plan x.md",
        "draft-pr": "/commit-push-pr --draft",
        "adversarial-review": "/de-adversarial-reviewer plan-gate x.md",
    }.get(row["slug"], row["command"] + " x.md")
    assert lean_peer_reason(lean, row, ROWS) is None


ARGS_ONLY = "a peer prompt is the slash command and its arguments only"


@pytest.mark.parametrize(
    ("prompt", "needle"),
    [
        ("Please run /plan-review x.md", ARGS_ONLY),
        ("Run `/plan-review` on x.md", ARGS_ONLY),
        ("/plan-review x.md please", "lean slash + args only"),
        ("/plan-review x.md\nmore", "newline"),
        ("/plan-review " + "x" * 400, "exceeds"),
        ("/plan-review x.md Return to the main agent", "poison"),
        ("／plan-review x.md", "peer command is not in plain ASCII slash form"),
        ("\u200b/plan-review x.md", "peer command is not in plain ASCII slash form"),  # \u200b: ZERO WIDTH SPACE
        ("/vibe-test x.md", ARGS_ONLY),
    ],
)
def test_a_non_lean_peer_prompt_has_a_reason(prompt, needle):
    reason = lean_peer_reason(prompt, row_for("plan-review"), ROWS)
    assert reason is not None and needle in reason


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        ({"lean_record_by_hook": True, "host": "claude"}, True),
        ({"lean_record_by_hook": True, "host": "other"}, False),
        ({"host": "claude"}, False),
        ({"lean_record_by_hook": "true", "host": "claude"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_hook_records_only_needs_the_marker_and_claude(session, expected):
    assert hook_records_only(session) is expected


# --- plugin_prefixed_peer_prompts: A plugin install lists peer commands as /cadence:<name>. The hooks treat it as /<name>.
#
# Regression (PR #38 R1): `/cadence:plan-review x` was not a peer call to the
# PreToolUse matchers, so it passed with no handoff packet and no lean check.

PREFIXED = [
    ("/cadence:plan-review x.plan.md", "plan-review", "P3"),
    ("/cadence:pr-review-local 38", "pr-review-local", "R1"),
    ("/cadence:test-benchmark plan", "test-benchmark", "P7"),
]


@pytest.mark.parametrize("prompt,stage,block", PREFIXED)
def test_prefixed_peer_call_is_the_same_peer_call(prompt, stage, block):
    bare = "/" + prompt.split(":", 1)[1]
    assert stage_for_prompt(prompt) == stage_for_prompt(bare) == stage
    assert delegation_stage_for_prompt(prompt) == delegation_stage_for_prompt(bare)
    assert validate_lean_prompt(stage, prompt) is True
    assert block_for_prompt(prompt, ROOT) == block_for_prompt(bare, ROOT) == block


def test_prefixed_fat_prompt_is_still_denied():
    reason = task_prompt_block_reason("/cadence:plan-review x.plan.md and also fix the tests")
    assert reason and "lean" in reason
    with pytest.raises(PromptShapeError):
        validate_lean_prompt("plan-review", "/cadence:plan-review x.plan.md please be thorough")


def test_another_plugin_prefix_is_not_a_cadence_peer():
    assert stage_for_prompt("/other:plan-review x.plan.md") is None
    assert block_for_prompt("/other:plan-review x.plan.md", ROOT) is None
