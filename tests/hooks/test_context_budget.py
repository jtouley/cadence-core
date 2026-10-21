"""Orchestrator context budget (gh-96).

Before each orchestrator Agent or Task call during an active run, the handoff hook
reads the size of the orchestrator's last request from the host transcript and
writes one row to .context/attest/<run>.context.jsonl, and a notice above
`context_budget.warn_tokens`. It never denies (ADR-0017). The sizes below are the final
requests of the harness-benchmark pilot (2026-10-13): 111,736 and 156,578 prompt tokens.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import HANDOFF_HOOK, git_repo, install_config, project_config, tool_decision

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import context_budget  # noqa: E402

PILOT_ALL_HAIKU = {"input_tokens": 2, "cache_read_input_tokens": 108867, "cache_creation_input_tokens": 2867}


def _transcript(tmp_path: Path, *usages: dict | None) -> Path:
    rows = [{"type": "user", "message": {"role": "user", "content": "/cadence Add budgets."}}]
    for usage in usages:
        message = {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
        if usage is not None:
            message["usage"] = usage
        rows.append({"type": "assistant", "message": message})
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# --- the size -------------------------------------------------------------------------------------

def test_the_size_is_the_newest_request_with_usage(tmp_path):
    path = _transcript(tmp_path, {"input_tokens": 10}, PILOT_ALL_HAIKU, None)
    assert context_budget.prompt_tokens(str(path)) == 111736


@pytest.mark.parametrize("transcript", [None, "", "/no/such/file.jsonl", 7])
def test_no_transcript_is_no_size(transcript):
    assert context_budget.prompt_tokens(transcript) is None


def test_a_transcript_with_no_usage_is_no_size(tmp_path):
    assert context_budget.prompt_tokens(str(_transcript(tmp_path, None))) is None


# --- the config -----------------------------------------------------------------------------------

def test_the_default_is_shadow(tmp_path):
    cfg = context_budget.load_config(install_config(tmp_path / "skill"), git_repo(tmp_path / "ws"))
    assert cfg == {"mode": "shadow", "warn_tokens": 60000}


def test_the_shipped_config_is_valid(tmp_path):
    assert context_budget.load_config(ROOT, git_repo(tmp_path / "ws"))["mode"] == "shadow"


@pytest.mark.parametrize("value", [
    {"mode": "strict"},
    {"mode": "enforce"},
    {"warn_tokens": 0},
    {"warn_tokens": "60000"},
    {"warn_tokens": True},
    {"max_tokens": 90000},
    "shadow",
])
def test_a_bad_config_raises(tmp_path, value):
    root, ws = install_config(tmp_path / "skill"), git_repo(tmp_path / "ws")
    project_config(ws, {"context_budget": value})
    with pytest.raises(ValueError):
        context_budget.load_config(root, ws)


@pytest.mark.parametrize("tokens, want", [
    (None, "unknown"),
    (50000, "ok"),
    (60000, "ok"),
    (111736, "warn"),
    (1_000_000, "warn"),
])
def test_judge_never_denies(tokens, want):
    assert context_budget.judge({"mode": "shadow", "warn_tokens": 60000}, tokens) == want


# --- the hook -------------------------------------------------------------------------------------

@pytest.fixture
def agent_call(tmp_path, run_hook, make_root, repo):
    """agent_call(usage, budget=..., ...) -> (decision, stdout JSON, stderr, rows)."""

    def _call(usage, *, budget=None, agent_id=None, prompt="Explore the repo.", host="claude"):
        root = make_root(context_budget=budget)
        body = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(repo),
                "tool_use_id": "t1",
                "tool_input": {"prompt": prompt, "subagent_type": "general-purpose"}}
        if usage != "none":
            body["transcript_path"] = str(_transcript(tmp_path, usage))
        if agent_id:
            body["agent_id"] = agent_id
        code, out, err = run_hook(HANDOFF_HOOK, repo, body, host, hooks_dir=root / "hooks")
        assert code == 0, err
        log = repo / ".context" / "attest" / "run-1.context.jsonl"
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []
        return tool_decision(host, out), (json.loads(out) if out else {}), err, rows

    return _call


def test_shadow_records_the_size_and_warns_but_never_denies(agent_call):
    decision, _, err, rows = agent_call(PILOT_ALL_HAIKU)
    assert decision == "allow"
    assert "111736 tokens" in err
    assert [(r["prompt_tokens"], r["decision"], r["mode"]) for r in rows] == [(111736, "warn", "shadow")]


def test_a_small_context_is_ok_and_silent(agent_call):
    decision, _, err, rows = agent_call({"input_tokens": 30000})
    assert decision == "allow" and "tokens" not in err
    assert rows[0]["decision"] == "ok"


@pytest.mark.parametrize("prompt", ["Explore the repo.", "/plan-review plan.md", "/spec-driven plan.md"])
def test_a_large_context_never_denies_a_call(agent_call, prompt):
    """The call hands work to a peer; denying it would keep the work in the orchestrator."""
    decision, _, _, rows = agent_call({"input_tokens": 400000}, prompt=prompt)
    assert decision == "allow" and rows[0]["decision"] == "warn"


def test_no_transcript_is_recorded_as_unknown_and_allowed(agent_call):
    decision, _, _, rows = agent_call("none")
    assert decision == "allow"
    assert rows[0]["prompt_tokens"] is None and rows[0]["decision"] == "unknown"


def test_off_writes_nothing(agent_call):
    decision, _, _, rows = agent_call(PILOT_ALL_HAIKU, budget={"mode": "off"})
    assert decision == "allow" and rows == []


def test_a_bad_config_fails_closed(agent_call, run_hook, make_root, repo):
    root = make_root(context_budget={"mode": "strict"})
    body = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(repo),
            "tool_input": {"prompt": "Explore.", "subagent_type": "general-purpose"}}
    code, _, err = run_hook(HANDOFF_HOOK, repo, body, "claude", hooks_dir=root / "hooks")
    assert code == 2 and "context_budget.mode" in err


def test_a_peer_call_inside_a_peer_is_not_judged(agent_call):
    # A peer's own nested call is not the orchestrator's context (agent_id not an orchestrator).
    decision, _, _, rows = agent_call(PILOT_ALL_HAIKU, agent_id="peer-1")
    assert decision == "allow" and rows == []


def test_no_active_run_writes_nothing(tmp_path, run_hook, make_root):
    from conftest import git_repo

    plain = git_repo(tmp_path / "plain")
    root = make_root(context_budget={"mode": "shadow"})
    body = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(plain),
            "transcript_path": str(_transcript(tmp_path, PILOT_ALL_HAIKU)),
            "tool_input": {"prompt": "Explore.", "subagent_type": "general-purpose"}}
    code, out, _ = run_hook(HANDOFF_HOOK, plain, body, "claude", hooks_dir=root / "hooks")
    assert code == 0 and tool_decision("claude", out) == "allow"
    assert not (plain / ".context").exists()


def test_the_context_size_is_no_hard_stop():
    import activate_session

    assert "context" not in activate_session.HARD_STOP_CLASSES


# --- PR #97 review: MF-1, MF-2, N4 -------------------------------------------------------------

def _host_row(tokens: int, *, sidechain: bool, agent: str | None = None) -> dict:
    """An assistant row in the shape Claude Code writes (keys copied from a real transcript, PR #97 MF-4).

    In a subagent's own file every row has isSidechain true and an agentId.
    """
    row = {"type": "assistant", "isSidechain": sidechain, "userType": "external", "requestId": "req_1",
           "message": {"model": "claude-haiku-5-5", "id": "msg_1", "type": "message", "role": "assistant",
                       "content": [{"type": "text", "text": "ok"}],
                       "usage": {"input_tokens": 2, "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": tokens - 2, "output_tokens": 8}}}
    if agent:
        row["agentId"] = agent
    return row


def _session_transcripts(tmp_path: Path, main: int | list[dict], own: int | None, agent: str = "orch") -> str:
    """The documented layout: <session>.jsonl and <session>/subagents/agent-<id>.jsonl, real row shapes."""
    session = tmp_path / "sess.jsonl"
    rows = main if isinstance(main, list) else [_host_row(main, sidechain=False)]
    session.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if own is not None:
        sub = tmp_path / "sess" / "subagents" / f"agent-{agent}.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text(json.dumps(_host_row(own, sidechain=True, agent=agent)) + "\n", encoding="utf-8")
    return str(session)


@pytest.mark.parametrize("agent, main, own, want, size", [
    (None, 120000, None, "warn", 120000),       # S1: the main agent orchestrates
    ("orch", 120000, 5000, "ok", 5000),         # S2: a small subagent orchestrator under a large main session
    ("orch", 3000, 150000, "warn", 150000),     # S3: a large subagent orchestrator under a small main session
    ("orch", 120000, None, "unknown", None),    # S4: no own transcript: unknown, never the main size
    # S5: an older main layout with a newer sidechain row: the main agent's own size counts
    (None, [_host_row(120000, sidechain=False), _host_row(5, sidechain=True, agent="x")], None, "warn", 120000),
], ids=["S1-main", "S2-small-sub", "S3-large-sub", "S4-no-own-transcript", "S5-main-with-sidechain-row"])
def test_a_subagent_orchestrator_is_measured_by_its_own_transcript(tmp_path, run_hook, make_root, repo, agent, main,
                                                                    own, want, size):
    from conftest import hook_session

    hook_session(repo, "single", resume_slug="plan-review", orchestrator_agent_ids=["orch"])
    root = make_root(context_budget={"mode": "shadow"})
    body = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(repo),
            "transcript_path": _session_transcripts(tmp_path, main, own),
            "tool_input": {"prompt": "Explore.", "subagent_type": "general-purpose"}}
    if agent:
        body["agent_id"] = agent
    code, out, err = run_hook(HANDOFF_HOOK, repo, body, "claude", hooks_dir=root / "hooks")
    assert code == 0, err
    assert tool_decision("claude", out) == "allow"
    row = json.loads((repo / ".context" / "attest" / "run-1.context.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert (row["prompt_tokens"], row["decision"], row["source"]) == (size, want, "subagent" if agent else "session")


def test_an_unknown_key_raises(tmp_path):
    root, ws = install_config(tmp_path / "skill"), git_repo(tmp_path / "ws")
    layer = project_config(ws, {"context_budget": {"max_token": 50000}})
    with pytest.raises(ValueError, match=r"context_budget\.max_token is not a Cadence config key") as err:
        context_budget.load_config(root, ws)
    assert str(layer) in str(err.value)


def test_only_main_thread_assistant_rows_with_valid_counts_count(tmp_path):
    rows = [
        {"type": "assistant", "message": {"role": "assistant", "usage": {"input_tokens": 70000}}},
        {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "usage": {"input_tokens": 5}}},
        {"type": "user", "message": {"role": "user", "usage": {"input_tokens": 6}}},
        {"type": "assistant", "message": {"role": "assistant", "usage": {"input_tokens": -9}}},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert context_budget.prompt_tokens(str(path)) == 70000


def test_a_subagent_file_counts_its_sidechain_rows(tmp_path):
    """MF-4: in its own file a subagent's rows are all sidechain rows; they are its context."""
    path = tmp_path / "agent-orch.jsonl"
    path.write_text(json.dumps(_host_row(150000, sidechain=True, agent="orch")) + "\n", encoding="utf-8")
    assert context_budget.prompt_tokens(str(path), own=True) == 150000
    assert context_budget.prompt_tokens(str(path)) is None  # read as a main file, it is another agent


@pytest.mark.parametrize("agent", ["../x", "x/../../../outside", "/etc/passwd", "", "a b", 7, "abc\n"])
def test_an_agent_id_with_path_characters_names_no_file(agent):
    import host_transcript

    assert host_transcript.subagent_path("/p/sess.jsonl", agent) is None


def test_a_real_agent_id_names_its_file():
    import host_transcript

    assert host_transcript.subagent_path("/p/sess.jsonl", "a70be0dadd78d8a4e") == "/p/sess/subagents/agent-a70be0dadd78d8a4e.jsonl"



def test_a_documented_prefixed_agent_id_finds_the_bare_file(tmp_path):
    """N14: the docs' example id is `agent-abc123`; when `agent-agent-abc123.jsonl` is absent, try the bare name."""
    import host_transcript

    folder = tmp_path / "sess" / "subagents"
    folder.mkdir(parents=True)
    (folder / "agent-abc123.jsonl").write_text("{}\n", encoding="utf-8")
    assert host_transcript.subagent_path(str(tmp_path / "sess.jsonl"), "agent-abc123") == str(folder / "agent-abc123.jsonl")
