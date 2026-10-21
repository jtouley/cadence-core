"""Hooks answer in each host's wire format, fail closed on startup errors, and leave other repos alone."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import HOOKS_DIR, git_repo, hook_session, run_py


# --- hooks_speak_each_host_protocol: Hooks answer in each host's wire format: Cursor hooks.json and Claude Code settings.json.

def _claude_write(repo: Path, rel: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(repo),
            "tool_input": {"file_path": str(repo / rel), "content": "x\n"}}


def test_claude_deny_uses_permission_decision(tmp_path, run_hook):
    repo = git_repo(tmp_path)
    hook_session(repo, "single", resume_slug="plan-create")
    code, out, _ = run_hook("guard_skill_gate.py", repo, _claude_write(repo, "src/app.py"), "claude")
    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "entry invariant" in decision["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["Edit", "MultiEdit"])
def test_claude_edit_tools_are_gated(tmp_path, tool, run_hook):
    repo = git_repo(tmp_path)
    hook_session(repo, "single", resume_slug="plan-create")
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": {"file_path": str(repo / "src/app.py"), "old_string": "a", "new_string": "b"}}
    _, out, _ = run_hook("guard_skill_gate.py", repo, payload, "claude")
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_claude_allow_prints_nothing(tmp_path, run_hook):
    # Printing permissionDecision "allow" would skip the user's permission prompts.
    repo = git_repo(tmp_path)
    hook_session(repo, "single", resume_slug="implement")
    (repo / ".context" / "gates").mkdir()
    (repo / ".context" / "gates" / "entry-invariant-run-1.ok").write_text("ok\n")  # The run's own marker
    assert run_hook("guard_skill_gate.py", repo, _claude_write(repo, "src/app.py"), "claude")[:2] == (0, "")
    assert run_hook("guard_primary_context_artifacts.py", repo, _claude_write(repo, "README.md"), "claude")[:2] == (0, "")


@pytest.mark.parametrize("host, blocked", [
    ("cursor", {"followup_message"}),
    ("claude", {"decision", "reason"}),
])
def test_stop_guard_block_keeps_agent_working(tmp_path, host, blocked, run_hook):
    repo = git_repo(tmp_path)
    hook_session(repo, "single", resume_slug="implement")
    code, out, _ = run_hook("pipeline_stop_guard.py", repo, {"hook_event_name": "Stop"}, host)
    assert code == 0
    data = json.loads(out)
    assert set(data) == blocked
    if host == "claude":
        assert data["decision"] == "block"
    assert "Continue paceline" in (data.get("followup_message") or data.get("reason"))


@pytest.mark.parametrize("host, expected", [("cursor", "{}"), ("claude", "")])
def test_stop_guard_allows_finished_run_with_valid_output(tmp_path, host, expected, run_hook):
    # Plain-text output used to be invalid JSON, so failClosed hosts blocked even finished runs.
    repo = git_repo(tmp_path)
    hook_session(repo, "single", active_skill=None, resume_slug="session-close")
    assert run_hook("pipeline_stop_guard.py", repo, {"hook_event_name": "Stop"}, host)[:2] == (0, expected)


@pytest.mark.parametrize("host", ["cursor", "claude"])
def test_stop_guard_escalation_lets_the_stop_through(tmp_path, host, run_hook):
    repo = git_repo(tmp_path)
    hook_session(repo, "single", resume_slug="implement", stop_refusals=3)
    code, out, _ = run_hook("pipeline_stop_guard.py", repo, {"hook_event_name": "Stop"}, host)
    assert code == 0
    data = json.loads(out) if out else {}
    assert "followup_message" not in data and data.get("decision") != "block"
    if host == "claude":
        assert "escalate to user" in data["systemMessage"]


def test_stop_guard_counts_refusals(tmp_path, run_hook):
    repo = git_repo(tmp_path)
    hook_session(repo, "single", resume_slug="implement")
    for _ in range(2):
        run_hook("pipeline_stop_guard.py", repo, {"hook_event_name": "Stop"}, "claude")
    session = json.loads((repo / ".context" / "cadence-session.json").read_text())
    assert session["stop_refusals"] == 2


def test_unknown_host_is_rejected(tmp_path):
    repo = git_repo(tmp_path)
    proc = run_py(HOOKS_DIR / "guard_skill_gate.py", "--host", "vim", cwd=repo, stdin="{}")
    assert proc.returncode != 0


# --- hooks_fail_closed_on_startup_errors: Hooks fail closed (exit 2) on errors before main() runs, not only inside it.
#
# Claude Code treats exit 1 as a non-blocking error, so an import failure or a bad
# --host used to let the tool call or stop proceed.

HOOKS = ["guard_skill_gate.py", "guard_primary_context_artifacts.py", "pipeline_stop_guard.py", "guard_secret_read.py"]


@pytest.mark.parametrize("script", HOOKS)
def test_bad_host_exits_2(tmp_path, run_hook, script):
    repo = git_repo(tmp_path)
    code, _, err = run_hook(script, repo, {}, "cursor", "--host", "claude-code")
    assert code == 2, err


# Each hook's own hard dependencies (a module it imports at startup).
DEPENDENCIES = [
    ("guard_skill_gate.py", "hook_protocol.py"),
    ("guard_skill_gate.py", "../shared/skill_gate.py"),
    ("guard_primary_context_artifacts.py", "hook_protocol.py"),
    ("pipeline_stop_guard.py", "hook_protocol.py"),
    ("pipeline_stop_guard.py", "../scripts/load_stages.py"),
    ("guard_secret_read.py", "hook_protocol.py"),
    ("guard_secret_read.py", "../shared/skill_gate.py"),
]


@pytest.mark.parametrize("script, missing", DEPENDENCIES)
def test_missing_module_exits_2(tmp_path, run_hook, script, missing):
    tree = tmp_path / "skill"
    shutil.copytree(HOOKS_DIR.parent, tree, ignore=shutil.ignore_patterns(".git", ".context", "tests"))
    (tree / "hooks" / missing).unlink()
    repo = git_repo(tmp_path / "repo")
    hook_session(repo, resume_slug="implement")
    payload = {"tool_name": "Write", "tool_input": {"path": "src/a.py"}, "hook_event_name": "Stop"}
    code, _, err = run_hook(script, repo, payload, "claude", hooks_dir=tree / "hooks")
    assert code == 2, err
    assert "failed closed" in err


# --- hooks_never_create_context_outside_cadence: Global hooks run in every repo; one that never ran Cadence must not get a .context/ dir.

CASES = [
    ("guard_skill_gate.py", {"tool_name": "Write", "tool_input": {"path": "src/app.py"}}),
    ("guard_skill_gate.py", {"hook_event_name": "beforeShellExecution", "command": "echo x > src/a.py"}),
    ("guard_primary_context_artifacts.py", {"tool_name": "Write", "tool_input": {"path": ".context/reviews/x.md"}}),
    ("pipeline_stop_guard.py", {"hook_event_name": "stop"}),
    # Only a command that runs activation writes the orchestrator note.
    ("guard_skill_gate.py", {"hook_event_name": "beforeShellExecution", "command": "grep -n x scripts/activate_session.py"}),
    ("guard_skill_gate.py", {"tool_name": "Bash", "agent_id": "A", "tool_input": {"command": "grep -n x scripts/activate_session.py"}}),
    ("guard_secret_read.py", {"tool_name": "Read", "tool_input": {"path": ".env"}}),
    ("guard_secret_read.py", {"hook_event_name": "beforeShellExecution", "command": "cat .env"}),
]


@pytest.mark.parametrize("host", ["cursor", "claude"])
@pytest.mark.parametrize("script, payload", CASES, ids=[f"{s}-{i}" for i, (s, _) in enumerate(CASES)])
def test_hook_leaves_foreign_repo_untouched(tmp_path, run_hook, script, payload, host):
    code, _, err = run_hook(script, git_repo(tmp_path), payload, host)
    assert code == 0, err
    assert not (tmp_path / ".context").exists()
