"""The orchestrator routes, and the peers do the work (ADR-0017).

On Claude Code, during an active run, the orchestrator runs each gate, sends each peer its
command and reads the one-line return. The read guard and the edit guard deny an orchestrator
call that reads a run artifact, the code or the diff, or that writes the code. A peer is never
judged. E1 is one `/spec-driven <plan>` peer call: the stop guard holds that peer, not the
orchestrator, while a plan phase has no commit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import (
    PROBE_SESSION,
    activate,
    branch_repo,
    commit_file,
    probe_repo,
    read_session,
    stop_decision,
    tool_decision,
    write_files,
)

READ_GUARD = "guard_secret_read.py"
EDIT_GUARD = "guard_skill_gate.py"
SCOPE = "the orchestrator does not read or write"


def _claude_run(tmp_path: Path, *, host: str = "claude", config: dict | None = None) -> Path:
    repo = probe_repo(tmp_path / "proj", "executing")
    session = {**json.loads(PROBE_SESSION), "host": host}
    files = {".context/cadence-session.json": json.dumps(session), "tests/test_app.py": "def test_x(): pass\n",
             ".context/plans/gh-1.plan.md": "# Plan\n", ".context/prs/reviews/PR_REVIEW_#1.md": "# Review\n",
             "README.md": "# Readme\n"}
    if config is not None:
        files[".cadence.json"] = json.dumps(config)
    write_files(repo, files)
    return repo


def _call(run_hook, script: str, repo: Path, tool: str, tool_input: dict, agent: str | None = None) -> tuple[str, str]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(repo), "tool_input": tool_input}
    if agent:
        payload["agent_id"] = agent
    code, out, err = run_hook(script, repo, payload, "claude")
    assert code == 0, err
    reason = json.loads(out)["hookSpecificOutput"].get("permissionDecisionReason", "") if out else ""
    return tool_decision("claude", out), reason


@pytest.mark.parametrize("path, want", [
    pytest.param("src/app.py", "deny", id="source"),
    pytest.param("tests/test_app.py", "deny", id="test"),
    pytest.param(".context/plans/gh-1.plan.md", "deny", id="plan"),
    pytest.param(".context/prs/reviews/PR_REVIEW_#1.md", "deny", id="pr-review"),
    pytest.param(".cursor/plans/feature.plan.md", "deny", id="cursor-plan"),
    pytest.param("README.md", "allow", id="readme"),
    pytest.param(".context/findings.jsonl", "allow", id="ledger"),
    pytest.param(".context/cadence-session.json", "allow", id="session"),
])
def test_an_orchestrator_read_is_judged_by_its_path(tmp_path, run_hook, path, want):
    repo = _claude_run(tmp_path)
    decision, reason = _call(run_hook, READ_GUARD, repo, "Read", {"file_path": str(repo / path)})
    assert decision == want
    assert (SCOPE in reason) == (want == "deny")


@pytest.mark.parametrize("command, want", [
    pytest.param("cat src/app.py", "deny", id="cat-source"),
    pytest.param("sed -n 1,40p .context/plans/gh-1.plan.md", "deny", id="sed-plan"),
    pytest.param("cat .context/prs/reviews/PR_REVIEW_#1.md | head", "deny", id="cat-review"),
    pytest.param("git diff origin/main", "deny", id="git-diff"),
    pytest.param("git show HEAD", "deny", id="git-show"),
    pytest.param("git diff --stat origin/main", "allow", id="git-diff-stat"),
    pytest.param("git diff --name-only", "allow", id="git-diff-names"),
    pytest.param("git log --oneline -5", "allow", id="git-log"),
    pytest.param("python3 scripts/headless.py next --json", "allow", id="gate-script"),
    pytest.param("python3 scripts/ledger_verify.py --ledger .context/findings.jsonl", "allow", id="ledger-verify"),
    pytest.param("cat README.md", "allow", id="readme"),
])
def test_an_orchestrator_shell_read_is_judged(tmp_path, run_hook, command, want):
    repo = _claude_run(tmp_path)
    assert _call(run_hook, READ_GUARD, repo, "Bash", {"command": command})[0] == want


def test_an_orchestrator_grep_of_the_code_is_denied(tmp_path, run_hook):
    repo = _claude_run(tmp_path)
    assert _call(run_hook, READ_GUARD, repo, "Grep", {"pattern": "def ", "path": "src"})[0] == "deny"


@pytest.mark.parametrize("tool, tool_input", [
    pytest.param("Read", {"file_path": "src/app.py"}, id="read"),
    pytest.param("Bash", {"command": "git diff origin/main"}, id="diff"),
    pytest.param("Grep", {"pattern": "def ", "path": "src"}, id="grep"),
])
def test_a_peer_reads_what_it_needs(tmp_path, run_hook, tool, tool_input):
    repo = _claude_run(tmp_path)
    assert _call(run_hook, READ_GUARD, repo, tool, tool_input, agent="peer-1")[0] == "allow"


@pytest.mark.parametrize("host, config", [
    pytest.param("cursor", None, id="cursor-run"),
    pytest.param("pi", None, id="pi-run"),
    pytest.param("claude", {"orchestrator_scope": {"mode": "off"}}, id="turned-off"),
])
def test_the_rule_applies_to_a_claude_code_run_only(tmp_path, run_hook, host, config):
    repo = _claude_run(tmp_path, host=host, config=config)
    assert _call(run_hook, READ_GUARD, repo, "Read", {"file_path": "src/app.py"})[0] == "allow"


def test_no_run_is_not_judged(tmp_path, run_hook):
    repo = probe_repo(tmp_path / "proj", "none")
    assert _call(run_hook, READ_GUARD, repo, "Read", {"file_path": "src/app.py"})[0] == "allow"


# --- the E1 peer and the stop guard ----------------------------------------------------------------

def _transcript(tmp_path: Path, prompt: str, final: str) -> Path:
    rows = [{"type": "user", "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": final}]}}]
    path = tmp_path / "e1-peer.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def e1_run(tmp_path):
    """An activated Claude Code run in E1: P9 passed, a plan of three phases, phase 1 committed."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from artifact_requirements import write_entry_invariant_ok

    repo = branch_repo(tmp_path / "proj", "feature/gh-7-toy")
    activate(repo, "--from-branch", "--fresh", "--supersede", CLAUDECODE="1")
    session = read_session(repo)
    assert session["host"] == "claude"
    plan = (f"RUN_BY_COMMAND: plan-create\nPipeline-Run: {session['pipeline_run_id']}\n"
            f"**Ticket:** {session['task_id']}\n\n## Phases\n\n### Phase 1: parser\n### Phase 2: wiring\n### Phase 3: docs\n")
    write_files(repo, {f".context/plans/{session['task_id']}.plan.md": plan})
    write_entry_invariant_ok(repo / ".context", pipeline_run_id=session["pipeline_run_id"],
                             task_id=session["task_id"], written_at="t")
    commit_file(repo, "feat: phase 1 parser", "p1.py", "x = 1\n")
    return repo


def _peer_stop(tmp_path, run_hook, repo, prompt, *, repeat=False):
    run_hook("guard_subagent_handoff.py", repo, {"hook_event_name": "SubagentStart", "agent_id": "e1", "cwd": str(repo)},
             "claude")
    final = "ARTIFACT: .context/implementation/cd-7_stage4.md"
    body = {"hook_event_name": "SubagentStop", "agent_id": "e1", "cwd": str(repo), "session_id": "c",
            "stop_hook_active": repeat, "last_assistant_message": final,
            "agent_transcript_path": str(_transcript(tmp_path, prompt, final))}
    _, out, _ = run_hook("pipeline_stop_guard.py", repo, body, "claude", "--subagent")
    return stop_decision("claude", out), (json.loads(out) if out else {})


def test_the_e1_peer_is_held_while_a_phase_is_open(tmp_path, run_hook, e1_run):
    decision, out = _peer_stop(tmp_path, run_hook, e1_run, "/spec-driven .context/plans/cd-7.plan.md")
    assert decision == "block"
    assert "Continue E1: phase 2 (wiring). Commit it as 'phase 2: <subject>'" in out["reason"]


def test_the_e1_peer_is_held_once(tmp_path, run_hook, e1_run):
    decision, _ = _peer_stop(tmp_path, run_hook, e1_run, "/spec-driven .context/plans/cd-7.plan.md", repeat=True)
    assert decision == "allow"


def test_a_fix_round_peer_is_not_held_for_a_phase(tmp_path, run_hook, e1_run):
    decision, _ = _peer_stop(tmp_path, run_hook, e1_run, "/spec-driven --fix .context/prs/reviews/PR_REVIEW_#1.md")
    assert decision == "allow"


def test_the_orchestrator_is_told_to_send_the_e1_peer_again(run_hook, e1_run):
    _, out, err = run_hook("pipeline_stop_guard.py", e1_run, {"hook_event_name": "Stop", "cwd": str(e1_run)}, "claude")
    reason = json.loads(out)["reason"]
    assert "send the `/spec-driven` peer the plan again" in reason and "phase 2 (wiring)" in reason, err
    assert "Commit it as" not in reason


@pytest.mark.parametrize("tool, tool_input", [
    pytest.param("Write", {"file_path": "src/app.py", "content": "x = 1\n"}, id="write-source"),
    pytest.param("Edit", {"file_path": "tests/test_app.py", "old_string": "x", "new_string": "y"}, id="edit-test"),
    pytest.param("Bash", {"command": "echo 'x = 1' > src/app.py"}, id="shell-write"),
])
def test_after_p9_an_orchestrator_write_to_the_code_is_denied_and_a_peer_write_is_not(run_hook, e1_run, tool,
                                                                                         tool_input):
    write_files(e1_run, {"src/app.py": "x = 0\n", "tests/test_app.py": "x\n"})
    decision, reason = _call(run_hook, EDIT_GUARD, e1_run, tool, tool_input)
    assert decision == "deny" and SCOPE in reason and "/spec-driven" in reason
    assert _call(run_hook, EDIT_GUARD, e1_run, tool, tool_input, agent="peer-1")[0] == "allow"


def test_an_orchestrator_write_outside_the_code_is_not_judged(run_hook, e1_run):
    assert _call(run_hook, EDIT_GUARD, e1_run, "Write", {"file_path": "NOTES.md", "content": "x\n"})[0] == "allow"


# --- ADR-0018: the orchestrator writes no markdown of the run --------------------------------------

@pytest.mark.parametrize("rel", [
    ".context/proposals/cd-7-storage.md",
    ".context/deferrals/cd-7_deferral-register.md",
    ".context/prs/comments/pr-comment-run.md",
    ".context/summaries/cd-7.md",
])
def test_an_orchestrator_write_to_the_markdown_of_the_run_is_denied(run_hook, e1_run, rel):
    decision, reason = _call(run_hook, EDIT_GUARD, e1_run, "Write", {"file_path": rel, "content": "# x\n"})
    # A peer artifact (a summary) is denied first by the peer-artifact guard, with its own text.
    assert decision == "deny" and (SCOPE in reason or "only a delegated peer writes" in reason)
    shell = _call(run_hook, EDIT_GUARD, e1_run, "Bash", {"command": f"cat > {rel} <<'EOF'\n# x\nEOF"})
    assert shell[0] == "deny"


@pytest.mark.parametrize("rel", [".context/proposals/cd-7-storage.md", ".context/deferrals/cd-7_deferral-register.md"])
def test_a_peer_writes_the_markdown_that_needs_judgment(run_hook, e1_run, rel):
    assert _call(run_hook, EDIT_GUARD, e1_run, "Write", {"file_path": rel, "content": "# x\n"}, agent="peer-1")[0] == "allow"


def test_a_tracker_post_is_not_run_markdown(run_hook, e1_run):
    payload = {"file_path": ".context/issues/cd-7.md", "content": "A5 gap.\n"}
    assert _call(run_hook, EDIT_GUARD, e1_run, "Write", payload)[1].find(SCOPE) == -1
