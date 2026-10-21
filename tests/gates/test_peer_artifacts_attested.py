"""A peer verdict is accepted only when a delegated peer wrote it (Claude Code).

dcdd730c: the orchestrator recorded its own lean delegation, then wrote the R2
scorecard itself, and the gate passed. Claude Code puts `agent_id` on a PreToolUse
payload only inside a subagent. So on that host:
- the main agent may not write a peer verdict file (reviews, scorecards, ...);
- a subagent write appends an attestation that only the hook writes;
- the peer gates accept a verdict file only when an attestation covers its last write.
Cursor and Pi send no such field: their behavior is unchanged.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from conftest import activate, commit_file, git_repo, head_sha, hook_denied, make_session, run_py, skill_gate_hook

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from artifact_requirements import REVIEW_RIGOR_SECTIONS  # noqa: E402
from prompt_shape import write_delegation_record  # noqa: E402

REVIEW = ".context/prs/reviews/PR_REVIEW_#1.md"


def _repo(tmp_path: Path, host: str = "claude") -> Path:
    repo = git_repo(tmp_path / "r")
    commit_file(repo, "base")
    make_session(repo / ".context", workspace=str(repo), host=host)
    return repo


def _body(repo: Path) -> str:
    """A full R1 review of the repo's HEAD (R1 reads the reviewed commit)."""
    return (f"RUN_BY_COMMAND: pr-review-local\nLast reviewed commit: {head_sha(repo)}\n"
            + "".join(f"\n## {h}\nnone\n" for h in REVIEW_RIGOR_SECTIONS))


def _write_payload(repo: Path, rel: str, agent_id: str | None, content: str | None = None) -> dict:
    content = _body(repo) if content is None else content
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(repo),
               "session_id": "chat-A", "tool_input": {"file_path": str(repo / rel), "content": content}}
    if agent_id:
        payload["agent_id"] = agent_id
        payload["agent_type"] = "general-purpose"
    return payload


def _review_with_lean_record(repo: Path) -> Path:
    ctx = repo / ".context"
    write_delegation_record(ctx, run_id="run-1", stage="pr-review-local", command="/pr-review-local 1", source="test")
    path = repo / REVIEW
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_body(repo), encoding="utf-8")
    return path


def _r1_gate(repo: Path) -> int:
    return run_py("scripts/pr_review_local_gate.py", "--check", cwd=repo).returncode


def test_the_main_agent_may_not_write_a_peer_verdict(tmp_path):
    repo = _repo(tmp_path)
    proc = skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id=None))
    assert hook_denied(proc)
    assert "peer" in (proc.stdout + proc.stderr).lower()


def test_a_subagent_write_is_allowed_and_attested(tmp_path):
    repo = _repo(tmp_path)
    proc = skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id="agent-7"))
    assert not hook_denied(proc), proc.stdout + proc.stderr
    lines = (repo / ".context" / "attest" / "run-1.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["path"] == "prs/reviews/PR_REVIEW_#1.md" and record["agent_id"] == "agent-7"


def test_cursor_is_unchanged(tmp_path):
    repo = _repo(tmp_path, host="cursor")
    assert not hook_denied(skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id=None), host="cursor"))


def test_r1_rejects_an_unattested_review_on_claude(tmp_path):
    repo = _repo(tmp_path)
    _review_with_lean_record(repo)
    assert _r1_gate(repo) == 1


def test_r1_accepts_a_review_a_peer_wrote(tmp_path):
    repo = _repo(tmp_path)
    skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id="agent-7"))
    _review_with_lean_record(repo)
    assert _r1_gate(repo) == 0


def test_r1_rejects_a_review_changed_after_the_peer_wrote_it(tmp_path):
    repo = _repo(tmp_path)
    skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id="agent-7"))
    path = _review_with_lean_record(repo)
    path.write_text(_body(repo) + "\nforged: LGTM\n", encoding="utf-8")  # cp over it, any mtime
    past = time.time() - 3600
    os.utime(path, (past, past))
    assert _r1_gate(repo) == 1


def test_r1_ignores_mtime_when_the_content_is_the_peers(tmp_path):
    repo = _repo(tmp_path)
    skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id="agent-7"))
    path = _review_with_lean_record(repo)
    later = time.time() + 600
    os.utime(path, (later, later))
    assert _r1_gate(repo) == 0


def test_a_peer_edit_is_attested_by_the_resulting_content(tmp_path):
    repo = _repo(tmp_path)
    skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id="agent-7"))
    path = _review_with_lean_record(repo)
    edit = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": str(repo), "agent_id": "agent-7",
            "tool_input": {"file_path": str(path), "old_string": "none", "new_string": "checked", "replace_all": False}}
    assert not hook_denied(skill_gate_hook(repo, edit))
    path.write_text(_body(repo).replace("none", "checked", 1), encoding="utf-8")
    assert _r1_gate(repo) == 0


def test_a_run_outside_the_hook_cwd_is_still_covered(tmp_path):
    """Claude Code runs hooks in the chat's project. The run's repo can be elsewhere (eval, worktree)."""
    repo = _repo(tmp_path)
    elsewhere = git_repo(tmp_path / "chat-project")
    assert hook_denied(skill_gate_hook(elsewhere, _write_payload(repo, REVIEW, agent_id=None)))
    skill_gate_hook(elsewhere, _write_payload(repo, REVIEW, agent_id="agent-7"))
    _review_with_lean_record(repo)
    assert _r1_gate(repo) == 0


def test_agents_may_not_write_attestations_or_delegation_records(tmp_path):
    repo = _repo(tmp_path)
    for rel in (".context/attest/run-1.jsonl", ".context/subagent-runs/run-1/pr-review-local.json"):
        assert hook_denied(skill_gate_hook(repo, _write_payload(repo, rel, agent_id="agent-7"))), rel
    # The peer model rows are hook-only too, and the message names their writer.
    proc = skill_gate_hook(repo, _write_payload(repo, ".context/attest/run-1.models.jsonl", agent_id="agent-7"))
    assert hook_denied(proc)
    assert "guard_subagent_handoff.py" in proc.stdout


def test_protected_writer_names_both_attest_writers(tmp_path):
    sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "shared" / "scripts")]
    import skill_gate

    writer = skill_gate.protected_writer(".context/attest/run-1.models.jsonl", tmp_path)
    assert writer == ("hooks/guard_skill_gate.py (a delegated peer's write records it) and "
                      "hooks/guard_subagent_handoff.py (peer model rows)")


def test_protected_writer_names_the_hook_for_delegation_records(tmp_path):
    # On a marked Claude Code run, begin refuses; the message must not send the orchestrator there only.
    sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "shared" / "scripts")]
    import skill_gate

    writer = skill_gate.protected_writer(".context/subagent-runs/run-1/plan-review.json", tmp_path)
    assert "hooks/guard_subagent_handoff.py" in writer and "subagent_provenance.py" in writer


def test_attested_any_skips_the_models_rows(tmp_path):
    """A row in <run>.models.jsonl must never count as a peer's write."""
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    import peer_attest

    ctx = tmp_path / ".context"
    artifact = ctx / "reviews" / "plan_x.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("verdict\n", encoding="utf-8")
    rel = artifact.relative_to(ctx).as_posix()
    (ctx / "attest").mkdir()
    good = {"agent_id": "agent-1", "path": rel, "sha256": peer_attest.sha256(artifact.read_bytes()), "at": 1}
    (ctx / "attest" / "run-1.jsonl").write_text(json.dumps(good) + "\n", encoding="utf-8")
    hostile = {"agent_id": "agent-9", "path": rel, "sha256": "0" * 64, "at": 2}
    (ctx / "attest" / "run-1.models.jsonl").write_text(json.dumps(hostile) + "\n", encoding="utf-8")
    assert peer_attest.attested_any(ctx, artifact)
    # The agent event log is skipped the same way (R2 F33).
    (ctx / "attest" / "run-1.agents.jsonl").write_text(json.dumps({**hostile, "at": 3}) + "\n", encoding="utf-8")
    assert peer_attest.attested_any(ctx, artifact)


def test_activation_records_the_claude_host(tmp_path):
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    import session_store

    repo = git_repo(tmp_path / "a")
    for extra, host in (({"CLAUDECODE": "1"}, "claude"), ({}, "other")):
        activate(repo, "--task-id", "gh-4", "--fresh", "--force", **extra)
        assert session_store.load_worktree_session(repo)["host"] == host


def test_a_hash_inside_a_word_is_part_of_the_path():
    """bash starts a comment only at a word start; PR_REVIEW_#1.md is one word."""
    sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "shared" / "scripts")]
    import skill_gate

    assert skill_gate.shell_write_targets("cp x .context/prs/reviews/PR_REVIEW_#1.md") == [".context/prs/reviews/PR_REVIEW_#1.md"]
    assert skill_gate.shell_write_targets("echo hi > out.txt # a comment > nope.txt") == ["out.txt"]
    assert skill_gate.shell_write_targets("echo '# not a comment' > out.txt") == ["out.txt"]


def test_restoring_an_older_peer_version_is_rejected(tmp_path):
    """Only the latest content a peer wrote counts, not any earlier version."""
    repo = _repo(tmp_path)
    old = _body(repo) + "\nverdict: APPROVE\n"
    skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id="agent-7", content=old))
    skill_gate_hook(repo, _write_payload(repo, REVIEW, agent_id="agent-7"))  # the peer's final review
    _review_with_lean_record(repo)
    (repo / REVIEW).write_text(old, encoding="utf-8")
    assert _r1_gate(repo) == 1


def test_the_gate_says_how_a_peer_saves_a_verdict(tmp_path):
    repo = _repo(tmp_path)
    _review_with_lean_record(repo)
    proc = run_py("scripts/pr_review_local_gate.py", "--check", cwd=repo)
    assert "Write or Edit" in proc.stdout + proc.stderr
