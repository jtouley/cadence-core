"""Lean peer returns (gh-96).

A peer's final message goes into the orchestrator's context. On Claude Code the
stop hook holds a delegated peer once when that message is not one lean line, or
when it hands a decision back. The pilot return below is from the harness-benchmark
pilot (2026-10-13, cadence_haiku): the orchestrator then stopped to ask the user.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import activated_repo, read_session, stop_decision, stop_guard_module

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import peer_return  # noqa: E402

PILOT_RETURN = """SUMMARY
Applied plan-update for `.context/plans/ledgerlite-budgets.plan.md`.

Hard stop: I did not run `activate_session.py --hard-stop retries`. If you read the rule as
covering any blocking verdict after cycle 5, run it yourself.

DECISIONS NEEDED
1. Confirm the MF-13 choice.
"""
LEAN = "ARTIFACT: .context/reviews/plan_cd-7.md VERDICT: READY TO EXECUTE"
PEER_PROMPT = "/plan-review .context/plans/cd-7.plan.md"
RENDERED_PROMPT = "Cadence peer P3 for ticket cd-7 (run r1).\nDo the work in commands/plan-review.md."


# --- the shape ------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "ARTIFACT: .context/reviews/plan_cd-7.md",
    LEAN,
    "`ARTIFACT: .context/vibetest/cd-7.md`",
    "  ARTIFACT: .context/plans/cd-7.plan.md  \n",
    "STOPPED: the plan file is missing",
    "ARTIFACT: .context/summary/confirm-the-plan.md",  # a phrase inside the path is data
])
def test_a_lean_return_passes(message):
    assert peer_return.return_reason(message) is None


@pytest.mark.parametrize("message, why", [
    (PILOT_RETURN, "lines"),
    ("", "empty"),
    ("Done. The review is in .context/reviews/plan_cd-7.md.", "not a lean return"),
    ("ARTIFACT: " + "x" * 400, "characters"),
    ("ARTIFACT: a.md VERDICT: READY — run it yourself", "hands work back"),
    ("STOPPED: decisions needed on the date format", "hands work back"),
    ("STOPPED: confirm the MF-13 choice", "hands work back"),
    ("ARTIFACT: a.md VERDICT: Return exactly the plan", "hands work back"),
])
def test_a_report_or_a_hand_back_fails(message, why):
    reason = peer_return.return_reason(message)
    assert reason is not None and why in reason


def test_peer_prompts_are_the_slash_command_or_the_packet_rendering():
    import handoff

    def block(text):
        return handoff.block_for_prompt(text, ROOT)

    assert peer_return.is_peer_prompt(PEER_PROMPT, block)
    assert peer_return.is_peer_prompt("/cadence:plan-review x.md", block)
    assert peer_return.is_peer_prompt(RENDERED_PROMPT, block)
    assert not peer_return.is_peer_prompt("Explore the repo and list the tests.", block)
    assert not peer_return.is_peer_prompt("", block)


# --- the hook -------------------------------------------------------------------------------------

def _transcript(tmp_path: Path, prompt: str, final: str | None, name: str = "agent.jsonl") -> Path:
    rows = [{"type": "user", "message": {"role": "user", "content": prompt}}]
    rows.append({"type": "assistant", "message": {"role": "assistant",
                                                  "content": [{"type": "tool_use", "name": "Write"}]}})
    if final is not None:
        rows.append({"type": "assistant", "message": {"role": "assistant",
                                                      "content": [{"type": "text", "text": final}]}})
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def peer(tmp_path, run_hook):
    """stop(final, ...) -> (decision, stdout JSON, repo) for a started subagent of an active run."""
    repo = activated_repo(tmp_path / "proj")

    def _start(agent="p1"):
        run_hook("guard_subagent_handoff.py", repo,
                 {"hook_event_name": "SubagentStart", "agent_id": agent, "cwd": str(repo)}, "claude")

    def _stop(final, *, prompt=PEER_PROMPT, agent="p1", host="claude", repeat=False, in_payload=True,
              transcript=True, start=True):
        if start:
            _start(agent)
        body = {"hook_event_name": "SubagentStop", "agent_id": agent, "cwd": str(repo), "session_id": "c",
                "stop_hook_active": repeat}
        if transcript:
            body["agent_transcript_path"] = str(_transcript(tmp_path, prompt, final, f"{agent}.jsonl"))
        if in_payload and final is not None:
            body["last_assistant_message"] = final
        _, out, _ = run_hook("pipeline_stop_guard.py", repo, body, host, "--subagent")
        return stop_decision(host, out), (json.loads(out) if out else {}), repo

    return _stop


def _rows(repo: Path) -> list[dict]:
    path = repo / ".context" / "attest" / f"{read_session(repo)['pipeline_run_id']}.returns.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stops(repo: Path, agent: str = "p1") -> int:
    path = repo / ".context" / "attest" / f"{read_session(repo)['pipeline_run_id']}.agents.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sum(1 for r in rows if r.get("agent_id") == agent and r.get("event") == "stop")


def test_the_pilot_report_is_held_with_the_lean_shapes(peer):
    decision, out, repo = peer(PILOT_RETURN)
    assert decision == "block"
    assert "ARTIFACT: <path>" in out["reason"] and "lines" in out["reason"]
    assert [r["decision"] for r in _rows(repo)] == ["hold"]
    assert _stops(repo) == 0  # a held peer keeps running


def test_a_lean_return_goes_through_and_records_the_stop(peer):
    decision, out, repo = peer(LEAN)
    assert decision == "allow" and "systemMessage" not in out
    assert [r["decision"] for r in _rows(repo)] == ["ok"]
    assert _stops(repo) == 1


def test_a_one_line_hand_back_is_held(peer):
    decision, out, _ = peer("STOPPED: confirm the date format, then run it yourself")
    assert decision == "block" and "hands work back" in out["reason"]


def test_a_second_wrong_return_goes_through_with_a_notice(peer):
    decision, out, repo = peer(PILOT_RETURN, repeat=True)
    assert decision == "allow"
    assert "without a lean return" in out["systemMessage"]
    assert [r["decision"] for r in _rows(repo)] == ["violation"]
    assert _stops(repo) == 1


def test_the_packet_rendering_is_a_peer_prompt(peer):
    decision, _, _ = peer(PILOT_RETURN, prompt=RENDERED_PROMPT)
    assert decision == "block"


def test_the_final_message_comes_from_the_transcript_when_the_payload_has_none(peer):
    decision, _, repo = peer(PILOT_RETURN, in_payload=False)
    assert decision == "block"
    decision, _, _ = peer(LEAN, in_payload=False, agent="p2")
    assert decision == "allow"


def test_a_subagent_that_is_not_a_peer_is_not_checked(peer):
    decision, _, repo = peer(PILOT_RETURN, prompt="Explore the repo and list the tests.")
    assert decision == "allow"
    assert _rows(repo) == []


def test_an_unreadable_peer_is_not_held(peer):
    decision, _, repo = peer(PILOT_RETURN, transcript=False, in_payload=False)
    assert decision == "allow"
    assert [r["decision"] for r in _rows(repo)] == ["unknown"]


def test_a_subagent_with_no_recorded_start_is_not_checked(peer):
    decision, _, repo = peer(PILOT_RETURN, start=False)
    assert decision == "allow"
    assert not (repo / ".context" / "attest" / f"{read_session(repo)['pipeline_run_id']}.returns.jsonl").exists()


def test_cursor_stops_are_not_checked(peer):
    decision, _, repo = peer(PILOT_RETURN, host="cursor")
    assert decision == "allow"
    assert _rows(repo) == []


def test_an_orchestrator_subagent_is_not_checked(peer, tmp_path):
    repo = tmp_path / "proj"
    peer(LEAN, agent="warmup")  # creates the repo and its run
    ctx = repo / ".context"
    root = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    path = ctx / root["ref"] if "ref" in root else ctx / "cadence-session.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["orchestrator_agent_ids"] = ["orch"]
    path.write_text(json.dumps(data), encoding="utf-8")
    peer(PILOT_RETURN, agent="orch")
    assert all(r["agent_id"] != "orch" for r in _rows(repo))


def test_subagent_start_adds_the_return_rule(tmp_path, run_hook):
    repo = activated_repo(tmp_path / "proj")
    _, out, _ = run_hook("guard_subagent_handoff.py", repo,
                         {"hook_event_name": "SubagentStart", "agent_id": "p1", "cwd": str(repo)}, "claude")
    assert peer_return.RETURN_RULE in json.loads(out)["hookSpecificOutput"]["additionalContext"]



def test_a_check_that_cannot_run_does_not_hold_the_peer(tmp_path, monkeypatch, capsys):
    repo = activated_repo(tmp_path / "proj")
    guard = stop_guard_module("psg_gh96")

    def broken(*_args, **_kwargs):
        raise OSError("run records unreadable")

    monkeypatch.setattr(guard, "peer_return_check", broken)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.chdir(repo)
    payload = {"hook_event_name": "SubagentStop", "agent_id": "p1", "cwd": str(repo), "session_id": "c",
               "last_assistant_message": PILOT_RETURN}
    assert guard._guard("claude", subagent=True, repeat=False, payload=payload) == 0
    assert stop_decision("claude", capsys.readouterr().out.strip()) == "allow"


def test_one_lean_line_in_a_code_fence_passes():
    assert peer_return.return_reason("```text\nARTIFACT: .context/reviews/plan_cd-7.md\n```") is None
    assert peer_return.return_reason("```\nARTIFACT: a.md\nmore\n```") is not None


def test_subagent_start_adds_the_return_rule_when_hygiene_is_off(tmp_path, run_hook, make_root):
    from conftest import hook_session, git_repo

    repo = git_repo(tmp_path / "off")
    hook_session(repo, "single")
    root = make_root(mode="off")
    _, out, _ = run_hook("guard_subagent_handoff.py", repo,
                         {"hook_event_name": "SubagentStart", "agent_id": "p1", "cwd": str(repo)}, "claude",
                         hooks_dir=root / "hooks")
    assert peer_return.RETURN_RULE in json.loads(out)["hookSpecificOutput"]["additionalContext"]



@pytest.mark.parametrize("message", ["```text\r\nARTIFACT: a.md\r\n```", "```c++\nARTIFACT: a.md\n```", "ARTIFACT: a.md\r\n"])
def test_crlf_and_any_fence_tag_pass(message):
    assert peer_return.return_reason(message) is None


def test_an_unknown_row_keeps_the_message_size(peer):
    _, _, repo = peer(PILOT_RETURN, transcript=False)
    row = _rows(repo)[-1]
    assert row["decision"] == "unknown" and row["chars"] == len(PILOT_RETURN)



def test_text_on_the_fence_line_does_not_bypass_the_checks():
    """PR #97 R4 round 2 MF-5: the fence line holds one tag token, never text that skips the checks."""
    hidden = "```DECISIONS NEEDED: do you want me to merge? Run it yourself. " + "x" * 600 + "\nARTIFACT: a.md VERDICT: APPROVE\n```"
    assert peer_return.return_reason(hidden) is not None


def test_the_injected_rule_says_replace():
    assert "replace your closing report" in peer_return.RETURN_RULE


def test_a_tool_call_as_the_newest_row_is_no_final_message(tmp_path):
    rows = [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Now I write it."}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Handback"}]}}]
    path = tmp_path / "agent-x.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert peer_return.final_message({"agent_transcript_path": str(path)}) is None
