"""The registry drives the lean handoff.

Each peer row of stages.json has `inputs`. The hook resolves them from the run's
.context and worktree, writes the packet, and renders the peer prompt: the slash
command with its arguments, then the packet lines. Nothing else reaches the peer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conftest import HANDOFF_PLAN, code_paths, permission, permission_reason, write_files

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import handoff  # noqa: E402
import handoff_inputs  # noqa: E402
import prompt_shape  # noqa: E402

PLAN = HANDOFF_PLAN
REVIEW = ".context/reviews/plan_feature.md"
RECORD = ".context/prs/draft-pr-run-1.md"
PR_REVIEW = ".context/prs/reviews/PR_REVIEW_#42.md"
REQUEST = ".context/proposals/gh-1.md"
VIBE = ".context/vibetest/gh-1_2026-10-21.md"
REPORT = ".context/adversarial/adversarial_feature.md"
SCORECARD = ".context/benchmarks/gh-1_plan.json"
CHECKS = ".context/acceptance/gh-1.json"
FILES = {
    "review": {REVIEW: "RUN_BY_COMMAND: plan-review\nTicket: gh-1\nVerdict: READY WITH CHANGES\n"},
    "record": {RECORD: "Branch: feature/gh-1-x\nPR: https://github.com/o/r/pull/42\n"},
    "spec": {REQUEST: "# Request: gh-1\n\nTicket: gh-1\n\nAdd x.\n",
             VIBE: "RUN_BY_SKILL: vibe-test\nPipeline-Run: run-1\n\n## Blocking gaps\n\n- G-1: no rule\n"},
    "pr-review": {RECORD: "Branch: feature/gh-1-x\nPR: https://github.com/o/r/pull/42\n",
                  PR_REVIEW: "RUN_BY_COMMAND: pr-review-local\nVerdict: NO MERGE\n"},
    "failing-card": {SCORECARD: '{"RUN_BY_SKILL": "test-benchmark", "verdict": "FAIL"}\n'},
    "passing-card": {SCORECARD: '{"RUN_BY_SKILL": "test-benchmark", "verdict": "PASS"}\n'},
    "checks": {CHECKS: '{"RUN_BY_COMMAND": "plan-create", "checks": []}\n'},
    "request": {REQUEST: "# Request: gh-1\n\nAnything downstream reads only out/marts/.\n"},
    "report": {REPORT: "RUN_BY_SKILL: de-adversarial-reviewer\nMode: plan-gate\nTicket: gh-1\n\n"
                       "Verdict: REQUEST CHANGES\n"},
}
PEER_ROWS = handoff._peer_stages(ROOT)
SENTINEL = "ROUTER-SENTINEL-do-not-forward"


def session(repo: Path) -> dict:
    return json.loads((repo / ".context" / "cadence-session.json").read_text(encoding="utf-8"))


def run_of(repo: Path) -> handoff_inputs.Run:
    return handoff_inputs.Run(root=ROOT, ctx=repo / ".context", workspace=repo, session=session(repo))


def pins(repo: Path, *rels: str) -> list[dict[str, str]]:
    return [{"path": rel, "sha256": hashlib.sha256((repo / rel).read_bytes()).hexdigest()} for rel in rels]


def built(repo: Path, block: str) -> dict:
    """The packet the hook builds for `block`; the test fails on a missing input."""
    packet, missing = handoff_inputs.packet_for(block, run_of(repo))
    assert missing == [] and packet is not None, missing
    return packet


def rewritten(out: dict) -> str:
    return out["hookSpecificOutput"]["updatedInput"]["prompt"]


# --- the packet of each peer row -------------------------------------------------------------------

ROWS = [
    pytest.param("A5", (), "/vibe-test gh-1", (), id="A5-ticket"),
    pytest.param("P1", (), "/plan-create gh-1", (), id="P1-ticket"),
    pytest.param("P1", ("spec",), "/plan-create gh-1", (REQUEST, VIBE), id="P1-pins-the-request-and-the-vibe-test"),
    pytest.param("P3", (), f"/plan-review {PLAN}", (PLAN,), id="P3-cycle-one-plan-only"),
    pytest.param("P3", ("review",), f"/plan-review {PLAN}", (PLAN, REVIEW), id="P3-cycle-two-plan-and-previous-review"),
    pytest.param("P4", ("review",), f"/plan-update {PLAN} --from-review {REVIEW}", (PLAN, REVIEW), id="P4-plan-and-review"),
    pytest.param("P4", ("review", "failing-card"), f"/plan-update {PLAN} --from-review {REVIEW}",
                 (PLAN, REVIEW, SCORECARD), id="P4-pins-a-failing-plan-scorecard"),
    pytest.param("P4", ("review", "passing-card"), f"/plan-update {PLAN} --from-review {REVIEW}", (PLAN, REVIEW),
                 id="P4-skips-a-passing-plan-scorecard"),
    pytest.param("P6", (), f"/de-adversarial-reviewer plan-gate {PLAN}", (PLAN,), id="P6-plan"),
    pytest.param("P7", (), f"/test-benchmark plan {PLAN}", (PLAN,), id="P7-plan"),
    pytest.param("E1", (), f"/spec-driven {PLAN}", (PLAN,), id="E1-plan"),
    pytest.param("E4", (), "/commit-push-pr --draft", (), id="E4-no-input"),
    pytest.param("R1", ("record",), "/pr-review-local 42", (RECORD,), id="R1-pr-number"),
    pytest.param("R2", ("record",), "/test-benchmark 42", (RECORD,), id="R2-pr-number"),
    pytest.param("R4", ("record",), "/pr-review-update 42", (RECORD,), id="R4-pr-number"),
    pytest.param("RF", ("pr-review",), f"/spec-driven --fix {PR_REVIEW}", (PR_REVIEW,), id="RF-fix-round-pr-review"),
    pytest.param("A5", ("request",), "/vibe-test gh-1", (REQUEST,), id="A5-request"),
    pytest.param("P3", ("checks",), f"/plan-review {PLAN}", (PLAN, CHECKS), id="P3-plan-and-checks"),
    pytest.param("P6", ("request", "checks"), f"/de-adversarial-reviewer plan-gate {PLAN}", (PLAN, REQUEST, CHECKS),
                 id="P6-plan-request-checks"),
    pytest.param("E1", ("checks",), f"/spec-driven {PLAN}", (PLAN, CHECKS), id="E1-plan-and-checks"),
    pytest.param("R1", ("record", "request", "checks"), "/pr-review-local 42", (RECORD, REQUEST, CHECKS),
                 id="R1-pr-request-checks"),
]


@pytest.mark.parametrize("block, extra, command, pinned", ROWS)
def test_the_packet_comes_from_the_row(repo, monkeypatch, block, extra, command, pinned):
    monkeypatch.chdir(repo)
    for name in extra:
        write_files(repo, FILES[name])
    packet = built(repo, block)
    assert packet["command"] == command
    assert packet["inputs"] == pins(repo, *pinned)
    assert packet["ticket"] == "gh-1" and packet["run_key"] == "run-1"
    row = next(r for r in PEER_ROWS if r["block"] == block)
    assert prompt_shape.lean_peer_reason(command, row, PEER_ROWS) is None
    assert handoff.block_for_prompt(command, ROOT) == block


def test_a_corrupt_packet_on_disk_is_written_again(repo, monkeypatch):
    """Garbage in the packet file is not a deny; the hook rebuilds the packet from the row."""
    monkeypatch.chdir(repo)
    run = run_of(repo)
    path = handoff.packet_path(repo / ".context", str(run.session["pipeline_run_id"]), "P3")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    packet, missing = handoff_inputs.packet_for("P3", run)
    assert missing == [] and packet is not None and packet["block"] == "P3"
    path.write_text(json.dumps({"old": 1}), encoding="utf-8")
    packet, missing = handoff_inputs.packet_for("P3", run)
    assert missing == [] and packet is not None


def test_a_scorecard_that_now_passes_leaves_the_p4_packet(repo, monkeypatch):
    """A stale pin of a plan scorecard would make the handoff check refuse the next P4 packet."""
    monkeypatch.chdir(repo)
    write_files(repo, {**FILES["review"], **FILES["failing-card"]})
    assert SCORECARD in [r["path"] for r in built(repo, "P4")["inputs"]]
    write_files(repo, FILES["passing-card"])
    packet = built(repo, "P4")
    assert packet["inputs"] == pins(repo, PLAN, REVIEW)
    assert handoff.verify_packet(packet, repo, ROOT, repo / ".context") == []


def test_p4_pins_a_failing_scorecard_named_after_the_plan(repo, monkeypatch):
    """k6 e2e: the peer named the card `<plan slug>_plan.json`, which the P7 gate reads too."""
    monkeypatch.chdir(repo)
    card = ".context/benchmarks/gh-1-feature_p1_plan.json"
    write_files(repo, {**FILES["review"], card: '{"RUN_BY_SKILL": "test-benchmark", "verdict": "FAIL"}\n'})
    assert built(repo, "P4")["inputs"] == pins(repo, PLAN, REVIEW, card)


# --- a required input that does not resolve --------------------------------------------------------

@pytest.mark.parametrize("block, kind, writer", [
    pytest.param("P3", "plan", "P1 (plan-create)", id="P3-no-plan"),
    pytest.param("P4", "plan-review", "P3 (plan-review)", id="P4-no-review"),
    pytest.param("R1", "pr-number", "E4 (draft-pr)", id="R1-no-draft-pr-record"),
])
def test_a_missing_input_names_the_artifact_and_the_block_that_writes_it(repo, monkeypatch, block, kind, writer):
    monkeypatch.chdir(repo)
    if kind == "plan":
        (repo / PLAN).unlink()
    packet, missing = handoff_inputs.packet_for(block, run_of(repo))
    assert packet is None
    [message] = missing
    assert f"{block} needs {kind}" in message and f"Block {writer} writes it" in message


@pytest.mark.parametrize("row, needle", [
    pytest.param({"block": "P3", "command": "/plan-review"}, "has no inputs list", id="no-inputs-field"),
    pytest.param({"block": "P3", "command": "/plan-review", "inputs": ["diagram"]}, "unknown input kind 'diagram'",
                 id="unknown-kind"),
])
def test_a_bad_row_fails_closed(repo, row, needle):
    found = handoff_inputs.resolve(row, run_of(repo))
    assert any(needle in m for m in found.missing)


def test_a_missing_input_denies_in_enforce(call, repo):
    (repo / PLAN).unlink()
    code, out, err = call("/plan-review plan.md", packet=False, mode="enforce")
    assert code == 0, err
    assert permission(out) == "deny"
    reason = permission_reason(out)
    assert "P3 needs plan" in reason and "P1 (plan-create)" in reason


# --- the hook writes the packet and renders the prompt ---------------------------------------------

def test_a_lean_prompt_is_rewritten_to_the_rendered_packet_on_claude_code(call, repo):
    code, out, err = call("/plan-review plan.md", packet=False, mode="enforce")
    assert code == 0, err
    sent = rewritten(out)
    packet = handoff.load_packet(repo / ".context", "run-1", "P3")
    assert packet is not None
    [pin] = pins(repo, PLAN)
    assert sent.splitlines() == [
        f"/plan-review {PLAN}",
        "Block: P3",
        "Contract: commands/plan-review.md",
        f"Input: {PLAN} sha256 {pin['sha256']}",
        f"Packet sha256: {handoff.packet_sha(packet)}",
    ]


def test_the_acceptance_repair_keeps_its_command_and_pins_the_check_and_its_defect(call, repo):
    """E1 sent `/plan-update <plan> --acceptance AC-4`, and the hook rewrote it each time to the round's P4
    packet (`--from-review round-1.md`), so the check was never repaired."""
    repair = ".context/acceptance/gh-1.repair.jsonl"
    write_files(repo, {**FILES["checks"], **FILES["review"], repair: '{"kind": "defect", "id": "AC-1"}\n'})
    code, out, err = call(f"/plan-update {PLAN} --acceptance AC-1", packet=False, mode="enforce")
    assert code == 0, err
    sent = out["hookSpecificOutput"]["updatedInput"]["prompt"].splitlines()
    assert sent[0] == f"/plan-update {PLAN} --acceptance AC-1"
    assert [line.split(" sha256 ")[0] for line in sent if line.startswith("Input: ")] == [
        f"Input: {PLAN}", f"Input: {CHECKS}", f"Input: {repair}"]
    assert handoff_inputs.acceptance_repair_id(f"/plan-update {PLAN} --from-review {REVIEW}") is None


def test_a_fat_prompt_is_rewritten_on_claude_code(call, repo):
    # A nested call is outside the lean check, so the handoff meets the fat prompt.
    code, out, err = call(f"/plan-review plan.md\n{SENTINEL}", packet=False, mode="enforce", agent_id="agent-peer")
    assert code == 0, err
    sent = rewritten(out)
    assert SENTINEL not in sent and sent.startswith(f"/plan-review {PLAN}\n")


def test_a_fat_prompt_is_denied_on_cursor_with_the_lean_slash_command(call, repo):
    code, out, err = call(f"/plan-review plan.md\n{SENTINEL}", host="cursor", tool="Task", packet=False,
                          mode="enforce")
    assert code == 0, err
    assert out["permission"] == "deny"
    reason = out["agent_message"]
    assert f"/plan-review {PLAN}" in reason
    assert "\n" not in reason and "Packet sha256" not in reason
    code, out, _ = call(f"/plan-review {PLAN}", host="cursor", tool="Task", packet=False, mode="enforce")
    assert code == 0 and out.get("permission", "allow") == "allow"


def test_a_stale_packet_is_written_again_from_the_row(call, repo):
    call("/plan-review plan.md", packet=False, mode="enforce")
    (repo / PLAN).write_text((repo / PLAN).read_text(encoding="utf-8") + "### Phase 2: test it\n", encoding="utf-8")
    _, out, _ = call("/plan-review plan.md", packet=False, mode="enforce")
    [pin] = pins(repo, PLAN)
    assert f"Input: {PLAN} sha256 {pin['sha256']}" in rewritten(out)


def test_a_manual_packet_that_adds_an_input_stays(call, repo, monkeypatch):
    monkeypatch.chdir(repo)
    packet, missing = handoff_inputs.packet_for("P3", run_of(repo), extra=["plan.md"])
    assert missing == [] and packet is not None
    _, out, _ = call("/plan-review plan.md", packet=False, mode="enforce")
    assert [r["path"] for r in packet["inputs"]] == [PLAN, "plan.md"]
    assert "Input: plan.md sha256 " in rewritten(out)


# --- the cap and the default ------------------------------------------------------------------------

def test_a_long_plan_name_renders_p4_and_the_prompt_holds_only_the_command_and_the_packet(repo, monkeypatch):
    """The hook renders this text itself, so a 57-character plan name is not a fat prompt."""
    monkeypatch.chdir(repo)
    write_files(repo, FILES["review"])
    long_plan = f".cursor/plans/{'x' * 57}.plan.md"
    write_files(repo, {long_plan: (repo / PLAN).read_text(encoding="utf-8")})
    (repo / PLAN).unlink()
    rendered = handoff.render_prompt(built(repo, "P4"), repo, repo / ".context")
    assert rendered.startswith(f"/plan-update {long_plan} --from-review ")
    kinds = [line.split(":", 1)[0] for line in rendered.splitlines()[1:]]
    assert kinds == ["Block", "Contract", "Input", "Input", "Packet sha256"]


def test_the_shipped_config_enforces_the_handoff_with_the_rewrite():
    cfg = handoff.load_config(ROOT)
    assert cfg["mode"] == "enforce" and cfg["rewrite_prompt"] is True



@pytest.mark.parametrize("rel", [".context/reviews/plan_feature.md", "src/a.py"], ids=["context-file", "worktree-file"])
def test_the_registry_and_the_packet_writer_give_one_packet_path(tmp_path, rel):
    """`.context/<x>` under the run's .context (a linked worktree reads the primary's), else the worktree path."""
    workspace, ctx = tmp_path / "wt", tmp_path / "primary" / ".context"
    parts = Path(rel).parts
    path = (ctx / Path(*parts[1:])) if parts[0] == ".context" else workspace / rel
    path.parent.mkdir(parents=True)
    path.write_text("x\n", encoding="utf-8")
    run = handoff_inputs.Run(root=ROOT, ctx=ctx, workspace=workspace, session={})
    assert handoff._locate(workspace, rel, ctx) == (path.resolve(), rel)
    assert handoff_inputs._shown(path, run) == rel
    assert handoff.shown_path(path, ((tmp_path / "elsewhere", ""),)) is None
