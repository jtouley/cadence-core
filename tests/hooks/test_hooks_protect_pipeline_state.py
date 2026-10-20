"""During a run, the agent cannot hand-edit the files that carry the pipeline's trust.

Covers both session layouts and both hosts. R1 review of PR #10 (cd-10): M2, M3, M7,
F1, F2, N1. Shell detection design: docs/adr/0001-*.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import SESSION_LAYOUTS, git_repo, hook_session, tool_decision

ROOT = Path(__file__).resolve().parents[2]
GATE = "guard_skill_gate.py"


@pytest.fixture(params=SESSION_LAYOUTS)
def plan_create_repo(request, tmp_path):
    repo = git_repo(tmp_path / "repo")
    hook_session(repo, request.param, resume_slug="plan-create")
    return repo


@pytest.fixture
def decide(run_hook):
    def _decide(repo: Path, payload: dict, host: str = "cursor") -> str:
        code, out, err = run_hook(GATE, repo, payload, host)
        assert code == 0, err
        return tool_decision(host, out)

    return _decide


def _write(path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"path": path, "file_path": path}}


def _shell(command: str, host: str) -> dict:
    if host == "claude":
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
    return {"hook_event_name": "beforeShellExecution", "command": command}


def _session_files(repo: Path) -> list[str]:
    ctx = repo / ".context"
    return sorted(str(p.relative_to(repo)) for p in ctx.rglob("*.json") if "session" in str(p))


PROTECTED = [
    ".context/gates/entry-invariant.ok",
    ".context/gates/entry-invariant-281fe311-3aaf-4403-9725-ca2cc72f9816.ok",  # cd-76: per-run marker
    ".context/gates/draft-pr-281fe311-3aaf-4403-9725-ca2cc72f9816.json",  # cd-76: per-run E4 breadcrumb
    ".context/findings.jsonl",
    ".context/findings/gh-1.jsonl",
    ".context/findings/gh-12.jsonl",
    ".context/cadence-session.json",
    ".context/sessions/by-worktree/0123456789abcdef.json",
    ".context/sessions/by-task/gh-1.json",
    ".context/handoff/run-1/P3.json",
]


@pytest.mark.parametrize("target", PROTECTED)
def test_direct_writes_to_pipeline_state_are_denied(plan_create_repo, decide, target):
    assert decide(plan_create_repo, _write(target)) == "deny"
    assert decide(plan_create_repo, _write(str(plan_create_repo / target))) == "deny"
    assert decide(plan_create_repo, _write(str(plan_create_repo / target)), "claude") == "deny"


def test_every_real_session_file_is_protected(plan_create_repo, decide):
    for rel in _session_files(plan_create_repo):
        assert decide(plan_create_repo, _write(rel), "claude") == "deny", rel


@pytest.mark.parametrize("host", ["cursor", "claude"])
@pytest.mark.parametrize("command", [
    "echo ok > .context/gates/entry-invariant.ok",
    "touch .context/gates/entry-invariant.ok",
    "sed -i 's/fixed/verified/' .context/findings.jsonl",
    "python3 -c pass && rm .context/cadence-session.json",
    "cp forged.json .context/cadence-session.json 2>/dev/null",
    "cp forged.json .context/cadence-session.json 2>&1",
    "mv .context/cadence-session.json /tmp/x",
    "mv .context/findings.jsonl /tmp/ledger",
    "cd .context && echo {} > cadence-session.json",
    "cd .context/gates; touch entry-invariant.ok",
    'echo {} > "$PWD/.context/cadence-session.json"',
    "printf x | tee $HOME/../repo/.context/findings.jsonl",
    "rm -rf .context",
    "cd .context && rm cadence-session.json 2>/dev/null",
    "cd .context/gates && cp /tmp/ok entry-invariant.ok 2>/dev/null",
    "echo '{}' > .context/handoff/run-1/P3.json",
    "cd .context/handoff/run-1 && cp /tmp/forged P3.json 2>/dev/null",
    "touch .context/gates/entry-invariant-281fe311-3aaf-4403-9725-ca2cc72f9816.ok",  # cd-76
    "cd $CTX/gates; touch entry-invariant-281fe311-3aaf-4403-9725-ca2cc72f9816.ok",  # cd-76: unresolved cd
    "cd .context/gates && cp /tmp/ok draft-pr-281fe311-3aaf-4403-9725-ca2cc72f9816.json 2>/dev/null",  # cd-76
])
def test_shell_writes_to_pipeline_state_are_denied(plan_create_repo, decide, command, host):
    assert decide(plan_create_repo, _shell(command, host), host) == "deny"


@pytest.mark.parametrize("command", [
    "python3 scripts/ledger_verify.py --ledger .context/findings.jsonl",
    "python3 scripts/findings_io.py set-status --id gh-1-F01 --status fixed",
    "python3 scripts/findings_io.py append --json '{\"evidence\": \"a > b\"}'",
    "python3 scripts/entry_invariant.py --phase execution",
    "python3 scripts/handoff.py write --block P3 --input .context/plans/x.plan.md",
    "cat .context/handoff/run-1/P3.json",
    "cat .context/findings.jsonl | python3 -m json.tool",
    "ls .context/gates",
    "git status 2>&1 | head",
    "echo 'all gates pass' > log.txt",
    "ls .context > /tmp/ls.txt",
    "python3 scripts/findings_io.py --ledger .context/findings/T.jsonl set-status --id T-F1 --status fixed | tee out.log",
    "python3 scripts/ledger_verify.py --ledger .context/findings.jsonl > /tmp/verify.log 2>&1",
    "cat > docs/notes.md <<EOF\nrm src/old.py\ntouch .context/gates/x\nEOF",
    "cat > docs/notes.md <<'EOF'\nit's fine: rm src/old.py\nEOF",
])
def test_reads_and_sanctioned_scripts_stay_allowed(plan_create_repo, decide, command):
    assert decide(plan_create_repo, _shell(command, "claude"), "claude") == "allow"
    assert decide(plan_create_repo, _shell(command, "cursor")) == "allow"


def test_other_context_files_stay_writable(plan_create_repo, decide):
    assert decide(plan_create_repo, _write(".context/reviews/plan-review.md")) == "allow"
    assert decide(plan_create_repo, _write("docs/notes.md")) == "allow"


def test_no_session_means_no_protection(tmp_path, decide):
    bare = git_repo(tmp_path / "bare")
    assert decide(bare, _write(".context/findings.jsonl")) == "allow"


@pytest.mark.parametrize("command, expected", [
    ("sed -i 's/a/b/' src/app.py", "deny"),
    ("cat > src/app.py <<'EOF'\nprint('it''s')\nEOF", "deny"),
    ("cp /tmp/x.py lib/x.py", "deny"),
    ("mv src/app.py /tmp/app.py", "deny"),
    ("echo hi > tests/test_app.py", "allow"),
    ("python3 -m pytest -q 2>&1 | tee /tmp/log", "allow"),
])
def test_shell_edits_to_gated_sources_follow_the_plan_gate(plan_create_repo, decide, command, expected):
    assert decide(plan_create_repo, _shell(command, "cursor")) == expected
    gates = plan_create_repo / ".context" / "gates"
    gates.mkdir(exist_ok=True)
    (gates / "entry-invariant-run-1.ok").write_text("ok\n")  # cd-76: the run's own marker
    assert decide(plan_create_repo, _shell(command, "cursor")) == "allow"


@pytest.mark.parametrize("header", ["Add File", "Update File", "Delete File"])
def test_cursor_apply_patch_paths_are_gated(plan_create_repo, decide, header):
    patch = f"*** Begin Patch\n*** {header}: src/app.py\n+x\n*** End Patch\n"
    assert decide(plan_create_repo, {"tool_name": "ApplyPatch", "tool_input": patch}) == "deny"
    marker = f"*** Begin Patch\n*** {header}: .context/gates/entry-invariant.ok\n+ok\n*** End Patch\n"
    assert decide(plan_create_repo, {"tool_name": "ApplyPatch", "tool_input": {"patch": marker}}) == "deny"


@pytest.mark.parametrize("wrap", ["json_string", "path_key"])
def test_cursor_apply_patch_other_shapes_are_gated(plan_create_repo, decide, wrap):
    patch = "*** Begin Patch\n*** Update File: src/app.py\n+x\n*** End Patch\n"
    tool_input = json.dumps({"patch": patch}) if wrap == "json_string" else {"path": "src/app.py", "patch": "+x"}
    assert decide(plan_create_repo, {"tool_name": "ApplyPatch", "tool_input": tool_input}) == "deny"


def test_heredoc_body_is_data_not_commands():
    sys.path.insert(0, str(ROOT / "shared"))
    from skill_gate import protected_shell_mention, shell_write_targets

    command = "cat > docs/notes.md <<EOF\nrm src/old.py\ntouch .context/gates/x\nEOF"
    assert shell_write_targets(command) == ["docs/notes.md"]
    assert protected_shell_mention(command) is None
    quoted = "cat > docs/notes.md <<'EOF'\nit's here: rm src/old.py\nEOF\necho done > out.txt"
    assert sorted(shell_write_targets(quoted)) == ["docs/notes.md", "out.txt"]


def test_cursor_apply_patch_move_destination_is_gated(plan_create_repo, decide):
    patch = "*** Begin Patch\n*** Update File: docs/a.md\n*** Move to: src/a.py\n*** End Patch\n"
    assert decide(plan_create_repo, {"tool_name": "ApplyPatch", "tool_input": patch}) == "deny"


@pytest.mark.parametrize("host", ["cursor", "claude"])
def test_corrupt_session_denies_gated_and_protected_writes_with_recovery_hint(tmp_path, run_hook, host):
    bad = git_repo(tmp_path / "bad")
    (bad / ".context").mkdir()
    (bad / ".context" / "cadence-session.json").write_text('{"active_skill": "cad')
    for target in ("src/app.py", ".context/findings.jsonl"):
        _, out, _ = run_hook(GATE, bad, _write(str(bad / target)), host)
        assert tool_decision(host, out) == "deny"
        assert "activate_session.py" in out
    _, out, _ = run_hook(GATE, bad, _write(str(bad / "docs/notes.md")), host)
    assert tool_decision(host, out) == "allow"


@pytest.mark.parametrize("command, targets", [
    ("cp a .context/s.json 2>/dev/null", [".context/s.json"]),
    ("cp a b 2>&1", ["b"]),
    ("mv a b", ["a", "b"]),
    ("mv -t dir a b", ["a", "b", "dir"]),
    ("echo x 2>err.log", ["err.log"]),
    ("git commit -m 'a > b'", []),
    ("ls 2>&1 >/dev/null", []),
])
def test_shell_write_targets_unit(command, targets):
    sys.path.insert(0, str(ROOT / "shared"))
    from skill_gate import shell_write_targets

    assert sorted(shell_write_targets(command)) == sorted(targets)
