"""The handoff hook checks and records the model of each configured peer call (cd-58 D2, D3).

With `peer_models` in the session, an orchestrator's peer call must pass the
configured model and must not be a fork (a fork ignores `model`). The hook writes one
row per recorded call to .context/attest/<run>.models.jsonl, after the final decision.
With no `peer_models`, nothing changes and no file is written.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from conftest import (
    HANDOFF_HOOK,
    HANDOFF_PLAN,
    hook_session,
    linked_worktrees,
    project_config,
    read_session,
    save_worktree_session,
    tool_decision,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import cadence_config  # noqa: E402
import peer_models  # noqa: E402
import pipeline_run_binding  # noqa: E402

OPUS = {"plan-review": "opus"}


def decision(out: dict) -> str:
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


def reason(out: dict) -> str:
    return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def rows(ctx: Path, run: str = "run-1") -> list[dict]:
    path = ctx / "attest" / f"{run}.models.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def configured(repo: Path, value=OPUS, **fields) -> None:
    hook_session(repo, "single", resume_slug="plan-review", peer_models=value, **fields)
    assert read_session(repo)["peer_models"] == value


# --- no peer_models -----------------------------------------------------------------------------


def test_no_config_writes_nothing_and_keeps_todays_output(call, repo):
    code, out, _ = call("/plan-review plan.md", model="opus", tool_use_id="toolu_1", session_id="chat-1")
    assert code == 0
    assert decision(out) == "allow"
    assert not list((repo / ".context").glob("attest/*.models.jsonl"))


def test_empty_map_writes_no_rows(call, repo):
    configured(repo, {})
    code, out, _ = call("/plan-review plan.md", model="haiku", mode="shadow")
    assert code == 0 and decision(out) == "allow"
    assert not list((repo / ".context").glob("attest/*.models.jsonl"))


# --- allow and deny -----------------------------------------------------------------------------


def test_the_configured_model_is_allowed_and_recorded(call, repo):
    configured(repo)
    code, out, _ = call("/plan-review plan.md", model="opus", tool_use_id="toolu_1", session_id="chat-1",
                        mode="shadow")
    assert code == 0 and decision(out) == "allow"
    [row] = rows(repo / ".context")
    assert row["decision"] == "allow" and row["model"] == "opus" and row["stage"] == "plan-review"
    assert row["block"] == "P3" and row["subagent_type"] == "general-purpose"
    assert row["tool_use_id"] == "toolu_1" and row["session_id"] == "chat-1" and row["agent_id"] is None
    assert isinstance(row["at"], float)


@pytest.mark.parametrize("model", [None, "sonnet"])
def test_another_or_no_model_is_denied_and_recorded(call, repo, model):
    configured(repo)
    code, out, _ = call("/plan-review plan.md", model=model, mode="enforce")
    assert code == 0 and decision(out) == "deny"
    assert "opus" in reason(out) and "plan-review" in reason(out)
    assert "updatedInput" not in out.get("hookSpecificOutput", {})
    [row] = rows(repo / ".context")
    assert row["decision"] == "deny" and row["model"] == model


def test_snapshot_beats_the_live_config(call, repo, tmp_path):
    configured(repo)
    project_config(repo, {"peer_models": {"plan-review": "haiku"}})  # the live project layer changes after A1
    code, out, _ = call("/plan-review plan.md", model="opus", tool_use_id="toolu_a", session_id="chat-a",
                        mode="shadow")
    live = cadence_config.load(tmp_path / "skill", repo)
    assert live.get("peer_models") == {"plan-review": "haiku"} and live.source("peer_models") == "project"
    assert decision(out) == "allow"
    assert [r["decision"] for r in rows(repo / ".context")] == ["allow"]
    code, out, _ = call("/plan-review plan.md", model="haiku", mode="shadow")
    assert decision(out) == "deny" and "opus" in reason(out)
    assert [(r["decision"], r["model"]) for r in rows(repo / ".context")] == [("allow", "opus"), ("deny", "haiku")]


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_model_deny_in_each_hook_mode(call, repo, mode):
    configured(repo)
    code, out, _ = call("/plan-review plan.md", model="sonnet", mode=mode)
    assert code == 0 and decision(out) == "deny"
    assert [r["decision"] for r in rows(repo / ".context")] == ["deny"]


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_model_allow_row_in_each_hook_mode(call, repo, mode):
    configured(repo)
    code, out, _ = call("/plan-review plan.md", model="opus", mode=mode)
    assert code == 0 and decision(out) == "allow"
    assert [r["decision"] for r in rows(repo / ".context")] == ["allow"]


def test_enforce_with_a_missing_input_records_the_handoff_deny(call, repo):
    configured(repo)
    (repo / HANDOFF_PLAN).unlink()
    code, out, _ = call("/plan-review plan.md", model="opus", packet=False, mode="enforce")
    assert decision(out) == "deny" and "P3 needs plan" in reason(out)
    assert [r["decision"] for r in rows(repo / ".context")] == ["deny"]


# --- fork (B-1) ---------------------------------------------------------------------------------


def test_fork_denied_even_with_the_configured_model(call, repo):
    configured(repo)
    code, out, _ = call("/plan-review plan.md", model="opus", subagent_type="fork", mode="enforce")
    assert code == 0 and decision(out) == "deny"
    assert "fork" in reason(out) and "opus" in reason(out)
    assert "updatedInput" not in out.get("hookSpecificOutput", {})
    [row] = rows(repo / ".context")
    assert row["decision"] == "deny" and row["subagent_type"] == "fork"
    code, out, _ = call("/plan-review plan.md", model="opus", subagent_type="general-purpose", mode="shadow")
    assert decision(out) == "allow"
    assert rows(repo / ".context")[-1]["subagent_type"] == "general-purpose"


def test_fork_unconfigured_slug_is_not_denied_by_the_model_check(call, repo):
    configured(repo)
    code, out, _ = call("/vibe-test spec.md", model="opus", subagent_type="fork", mode="shadow")
    # cd-64 r2 Decision 3: the lean check denies every peer fork; the model check does not.
    assert decision(out) == "deny" and "peer_models" not in reason(out) and "fork" in reason(out)
    [row] = rows(repo / ".context")
    assert row["decision"] == "deny" and row["stage"] == "validate"


def test_an_unconfigured_slug_in_a_configured_map_writes_an_allow_row(call, repo):
    configured(repo)
    code, out, _ = call("/pr-review-local", model="haiku", mode="shadow")
    assert decision(out) == "allow"
    [row] = rows(repo / ".context")
    assert row == {**row, "decision": "allow", "model": "haiku", "stage": "pr-review-local"}


# --- who is checked -----------------------------------------------------------------------------


def test_nested_peer_call_writes_no_row(call, repo):
    configured(repo, orchestrator_agent_ids=["agent-orch"])
    code, out, _ = call("/plan-review plan.md", model="sonnet", agent_id="agent-peer", mode="shadow")
    assert decision(out) == "allow"
    assert rows(repo / ".context") == []


def test_orchestrator_subagent_call_is_checked(call, repo):
    configured(repo, orchestrator_agent_ids=["agent-orch"])
    code, out, _ = call("/plan-review plan.md", model="sonnet", agent_id="agent-orch", mode="shadow")
    assert decision(out) == "deny"
    assert rows(repo / ".context")[0]["agent_id"] == "agent-orch"


def test_orchestrator_of_an_earlier_run_is_checked(call, repo):
    import session_store

    session_store.save_session({"active_skill": None, "task_id": "gh-0", "pipeline_run_id": "run-0",
                                "orchestrator_agent_ids": ["agent-old"], "status": "completed"}, repo)
    configured(repo)
    code, out, _ = call("/plan-review plan.md", model="sonnet", agent_id="agent-old", mode="shadow")
    assert decision(out) == "deny"
    assert [r["agent_id"] for r in rows(repo / ".context")] == ["agent-old"]


@pytest.mark.parametrize("prompt", ["/spec-driven plan.md", "/commit-push-pr --draft", "/plan-update plan.md"])
def test_not_a_key_gets_no_row_and_no_model_deny(call, repo, prompt):
    configured(repo, {"default": "haiku"})
    code, out, _ = call(prompt, model="sonnet", mode="shadow")
    assert decision(out) == "allow"
    assert rows(repo / ".context") == []


@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_task_tool_is_checked_the_same(call, repo, tool):
    configured(repo)
    _, out, _ = call("/plan-review plan.md", tool=tool, model="sonnet", mode="shadow")
    assert decision(out) == "deny"
    _, out, _ = call("/plan-review plan.md", tool=tool, model="opus", mode="shadow")
    assert decision(out) == "allow"
    assert [r["decision"] for r in rows(repo / ".context")] == ["deny", "allow"]


def test_plugin_prefix_is_checked(call, repo):
    configured(repo)
    _, out, _ = call("/cadence:plan-review plan.md", model="sonnet", mode="shadow")
    assert decision(out) == "deny"
    assert rows(repo / ".context")[0]["stage"] == "plan-review"


# --- fail closed --------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@pytest.mark.parametrize("value", ["opus", ["opus"], None])
def test_a_session_peer_models_that_is_not_a_map_fails_closed(call, repo, mode, value):
    hook_session(repo, "single", resume_slug="plan-review", peer_models=value)
    code, _, err = call("/plan-review plan.md", model="opus", mode=mode)
    assert code == 2 and "peer_models" in err
    code, out, _ = call("/spec-driven plan.md", model="opus", mode=mode)
    assert code == 0
    assert rows(repo / ".context") == []


@pytest.mark.parametrize("case", ["inactive_legacy", "completed", "deactivated_pointer", "list_file"])
def test_a_closed_run_does_not_check_or_record(call, repo, case):
    """R1 MF-1 on PR #62: after --deactivate the session keeps peer_models; it must not apply."""
    ctx = repo / ".context"
    if case == "inactive_legacy":
        hook_session(repo, "single", peer_models=OPUS, active_skill=None)
    elif case == "completed":
        hook_session(repo, "single", peer_models=OPUS, status="completed")
    elif case == "deactivated_pointer":
        import session_store

        hook_session(repo, "pointer", peer_models=OPUS)
        session_store.deactivate_session(repo)
    else:
        (ctx / "cadence-session.json").write_text("[1]", encoding="utf-8")  # [] reads as {} (review N-2)
    code, out, _ = call("/plan-review plan.md", model=None, mode="shadow")
    assert code == 0 and decision(out) == "allow"
    assert not list(ctx.glob("attest/*.models.jsonl"))


def test_row_write_failure_blocks_the_call(call, repo):
    configured(repo)
    (repo / ".context" / "attest").write_text("not a directory", encoding="utf-8")
    code, _, _ = call("/plan-review plan.md", model="opus", mode="shadow")
    assert code == 2


# --- worktree run, not the root redirect (MF-2) -------------------------------------------------


@pytest.mark.parametrize("case", ["wrong_model", "right_model"])
def test_model_check_reads_worktree_run_not_root_redirect(tmp_path, run_hook, make_root, case):
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w0"], pipeline_run_id="run-w0", peer_models=OPUS)
    save_worktree_session(trees["w1"], pipeline_run_id="run-w1")
    ctx = primary / ".context"
    assert pipeline_run_binding.load_session(ctx)["pipeline_run_id"] == "run-w1"
    assert pipeline_run_binding.current_session(ctx, trees["w0"])["pipeline_run_id"] == "run-w0"
    root = make_root(mode="shadow")
    model = "sonnet" if case == "wrong_model" else "opus"
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(trees["w0"]),
               "tool_use_id": "toolu_w0", "session_id": "chat-w0",
               "tool_input": {"prompt": "/plan-review plan.md", "subagent_type": "general-purpose", "model": model}}
    code, out, _ = run_hook(HANDOFF_HOOK, trees["w0"], payload, "claude", hooks_dir=root / "hooks")
    assert code == 0
    found = rows(ctx, "run-w0")
    if case == "wrong_model":
        assert tool_decision("claude", out) == "deny"
        assert "opus" in out and "plan-review" in out
        assert [r["decision"] for r in found] == ["deny"]
    else:
        assert tool_decision("claude", out) == "allow"
        assert [(r["decision"], r["model"]) for r in found] == [("allow", "opus")]
    assert not (ctx / "attest" / "run-w1.models.jsonl").exists()


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_unreadable_root_session_with_worktree_run(tmp_path, run_hook, make_root, mode):
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w0"], pipeline_run_id="run-w0", peer_models=OPUS)
    save_worktree_session(trees["w1"], pipeline_run_id="run-w1")
    ctx = primary / ".context"
    (ctx / "cadence-session.json").write_text("not json", encoding="utf-8")
    assert pipeline_run_binding.current_session(ctx, trees["w0"])["pipeline_run_id"] == "run-w0"
    root = make_root(mode=mode)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(trees["w0"]),
               "tool_input": {"prompt": "/plan-review plan.md", "subagent_type": "general-purpose", "model": "opus"}}
    code, out, _ = run_hook(HANDOFF_HOOK, trees["w0"], payload, "claude", hooks_dir=root / "hooks")
    assert code == 0
    expected = "allow" if mode == "shadow" else "deny"
    assert tool_decision("claude", out) == expected
    assert [r["decision"] for r in rows(ctx, "run-w0")] == [expected]


# --- writer -------------------------------------------------------------------------------------


def test_one_write_per_row(tmp_path, monkeypatch):
    writes: list[str] = []
    real_open = open

    class Recorder(io.StringIO):
        def write(self, text):
            writes.append(text)
            return super().write(text)

    def fake_open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".models.jsonl"):
            assert mode == "a"
            handle = Recorder()
            handle.close = lambda: None
            return handle
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    peer_models.record_call(tmp_path, "run-1", {"stage": "plan-review", "decision": "allow"})
    peer_models.record_call(tmp_path, "run-1", {"stage": "plan-review", "decision": "deny"})
    assert len(writes) == 2
    assert all(w.endswith("\n") and w.count("\n") == 1 for w in writes)
    assert [json.loads(w)["decision"] for w in writes] == ["allow", "deny"]


# --- in process (mapped tests for mutation runs) ------------------------------------------------


@pytest.fixture
def hook_module(make_root, monkeypatch):
    root = make_root(mode="shadow")
    monkeypatch.setenv("CADENCE_HOME", str(root))
    monkeypatch.setenv("CADENCE_SKILL_ROOT", str(root))
    spec = importlib.util.spec_from_file_location("guard_subagent_handoff_under_test", ROOT / "hooks" / HANDOFF_HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(prompt="/plan-review plan.md", model="opus", **extra) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent",
            "tool_input": {"prompt": prompt, "subagent_type": extra.pop("subagent_type", "general-purpose"),
                           "model": model}, **extra}


def _model_check(hook_module, payload: dict, ctx: Path):
    """cd-64 U-12: main() computes the peer row and the lean reason; the check takes them."""
    prompt = payload["tool_input"]["prompt"]
    rows = hook_module.handoff._peer_stages(Path(hook_module.os.environ["CADENCE_HOME"]))
    row = hook_module.prompt_shape.peer_command_row(prompt, rows)
    lean = hook_module.prompt_shape.lean_peer_reason(prompt, row, rows) if row else None
    return hook_module.model_check("claude", payload, ctx, row, lean)


def test_model_check_in_process_decisions(hook_module, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    configured(repo, orchestrator_agent_ids=["agent-orch"])
    ctx = repo / ".context"
    assert _model_check(hook_module, _payload(model="sonnet"), ctx)[0] == "deny"
    assert '"deny"' in capsys.readouterr().out
    assert [r["decision"] for r in rows(ctx)] == ["deny"]
    assert _model_check(hook_module, _payload(model="opus"), ctx)[0] == "allow"
    assert capsys.readouterr().out == ""
    assert len(rows(ctx)) == 1  # the allow row is written by main after the final decision
    assert _model_check(hook_module, _payload(subagent_type="fork"), ctx)[0] == "deny"
    assert _model_check(hook_module, _payload(prompt="hello"), ctx)[0] is None
    assert _model_check(hook_module, _payload(agent_id="agent-peer"), ctx)[0] is None


def test_handoff_flow_in_process_decisions(hook_module, repo, monkeypatch, make_root):
    monkeypatch.chdir(repo)
    configured(repo)
    ctx = repo / ".context"
    make_root(mode="off")
    assert hook_module.handoff_flow("claude", _payload(), ctx, False) == (0, "allow")
    make_root(mode="enforce")
    (repo / HANDOFF_PLAN).unlink()  # a missing input is the handoff deny
    assert hook_module.handoff_flow("claude", _payload(), ctx, False) == (0, "deny")


def test_shadow_swallow_path_records_allow(hook_module, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    configured(repo)

    def boom(*_args, **_kwargs):
        raise RuntimeError("pre_agent failed")

    monkeypatch.setattr(hook_module, "pre_agent", boom)
    monkeypatch.setattr(hook_module, "read_payload", lambda: _payload())
    assert hook_module.handoff_flow("claude", _payload(), repo / ".context", False) == (0, "allow")
    assert hook_module.main(["--host", "claude"]) == 0
    assert [r["decision"] for r in rows(repo / ".context")] == ["allow"]
