"""guard_skill_gate: deny and allow paths, repos with no run, and the project from the host."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from conftest import (assert_decision, git_repo, hook_session, host_shape, linked_worktrees, make_session,
                      save_worktree_session)

pytestmark = pytest.mark.integration
HOOK = "guard_skill_gate.py"


# --- guard_skill_gate_main: Execute hooks/guard_skill_gate.main deny and allow paths.

def _run_main(monkeypatch, payload: str | dict) -> tuple[int, dict]:
    from guard_skill_gate import main

    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    code = main([])
    out = json.loads(captured.getvalue())
    return code, out


def test_guard_skill_gate_main_write_gated_path_no_entry_invariant_denied(
    scripts_path, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    make_session(tmp_path / ".context")
    code, out = _run_main(
        monkeypatch,
        {"tool_name": "Write", "tool_input": {"path": "src/x.py"}},
    )
    assert code == 0
    assert out["permission"] == "deny"
    assert "entry invariant" in out["user_message"] or "gated path" in out["user_message"]


def test_guard_skill_gate_main_no_session_write_allowed(scripts_path, tmp_path, monkeypatch):
    # Installed globally: a repo that never ran Cadence is not gated (and gets no .context/).
    monkeypatch.chdir(tmp_path)
    code, out = _run_main(
        monkeypatch,
        {"tool_name": "Write", "tool_input": {"path": "src/x.py"}},
    )
    assert code == 0
    assert out["permission"] == "allow"
    assert not (tmp_path / ".context").exists()


def test_guard_skill_gate_main_corrupt_json_with_session_denied(
    scripts_path, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    make_session(tmp_path / ".context")
    code, out = _run_main(monkeypatch, "{")
    assert code == 0
    assert out["permission"] == "deny"
    assert "not valid JSON" in out["user_message"]



def test_guard_skill_gate_main_corrupt_json_after_deactivate_allowed(
    scripts_path, tmp_path, monkeypatch
):
    # A finished run (active_skill cleared) must not block a corrupt payload.
    monkeypatch.chdir(tmp_path)
    make_session(tmp_path / ".context", active_skill=None)
    code, out = _run_main(monkeypatch, "{")
    assert code == 0
    assert out["permission"] == "allow"


def test_guard_skill_gate_main_corrupt_json_with_unreadable_session_denied(
    scripts_path, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".context").mkdir()
    (tmp_path / ".context" / "cadence-session.json").write_text("{not json", encoding="utf-8")
    code, out = _run_main(monkeypatch, "[1, 2]")
    assert code == 0
    assert out["permission"] == "deny"

def test_guard_skill_gate_main_corrupt_json_without_session_allowed(
    scripts_path, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    code, out = _run_main(monkeypatch, "{")
    assert code == 0
    assert out["permission"] == "allow"
    assert not (tmp_path / ".context").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Read", "tool_input": {"path": "README.md"}},
        "",
    ],
    ids=["read_tool", "blank_stdin"],
)
def test_guard_skill_gate_main_nongated_or_blank_stdin_allows(
    scripts_path, tmp_path, monkeypatch, payload
):
    monkeypatch.chdir(tmp_path)
    make_session(tmp_path / ".context")
    code, out = _run_main(monkeypatch, payload)
    assert code == 0
    assert out["permission"] == "allow"
    assert "deny" not in out


# --- guard_skill_gate_allows_workspace_without_cadence_session: The global preToolUse hook must not lock edits in repos not running Cadence.

@pytest.fixture
def write_hook(run_hook):
    """The Cursor hook on a Write of `path`, with no host project env; the exit code must be 0."""
    def _write_hook(cwd: Path, path: str) -> dict:
        code, out, err = run_hook(HOOK, cwd, {"tool_name": "Write", "tool_input": {"path": path}}, project_env={})
        assert code == 0, err
        return json.loads(out)
    return _write_hook


def test_hook_allows_edits_without_cadence_session(tmp_path, write_hook):
    git_repo(tmp_path)
    assert write_hook(tmp_path, "README.md")["permission"] == "allow"
    assert write_hook(tmp_path, "src/app.py")["permission"] == "allow"


def test_hook_blocks_gated_edit_before_entry_invariant(tmp_path, write_hook):
    git_repo(tmp_path)
    context = tmp_path / ".context"
    context.mkdir()
    (context / "cadence-session.json").write_text(
        json.dumps({"active_skill": "cadence", "task_id": "cd-1"}), encoding="utf-8"
    )
    denied = write_hook(tmp_path, "src/app.py")
    assert denied["permission"] == "deny"
    assert "entry_invariant.py" in denied["agent_message"]
    assert write_hook(tmp_path, "README.md")["permission"] == "allow"


# --- guard_skill_gate_host_workspace: The skill gate finds the project from the host, not from the hook cwd.
#
# A user-level Cursor hook starts outside the project. The payload has no `cwd`; the
# project is in `CURSOR_PROJECT_DIR` and `workspace_roots`. Claude Code sends payload
# `cwd`. The process cwd stays a candidate, so a call that is denied today stays denied.

BEFORE_P9 = "entry invariant"
PROTECTED = "pipeline state written only by the stage gate scripts"


@pytest.fixture
def dirs(tmp_path):
    project = git_repo(tmp_path / "project")
    (project / "src").mkdir()
    (project / "src" / "x.py").write_text("a = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    return project, outside


def _write(path) -> dict:
    return {"hook_event_name": "preToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(path), "content": "b = 2\n"}}


def _shell(command: str) -> dict:
    # No payload `cwd`: the strict case, the gate must find the project from the host.
    return {"hook_event_name": "beforeShellExecution", "command": command}


def _calls(project):
    return {
        "src-write": (_write(project / "src" / "x.py"), BEFORE_P9),
        "src-shell": (_shell("sed -i '' 's/a/b/' src/x.py"), BEFORE_P9),
        "marker-write": (_write(project / ".context" / "gates" / "entry-invariant.ok"), PROTECTED),
    }


CALLS = ("src-write", "src-shell", "marker-write")
CURSOR_SHAPES = ("cursor_env", "cursor_roots_only")


def _allowed_with_no_context(result, project: Path, outside: Path) -> None:
    """The Cursor hook allowed the call and made no .context in the project or outside it."""
    assert_decision(result, "cursor", "allow")
    assert not (project / ".context").exists()
    assert not (outside / ".context").exists()


@pytest.mark.parametrize("shape", CURSOR_SHAPES)
@pytest.mark.parametrize("call", CALLS)
def test_cursor_hook_outside_the_project_denies_before_p9(run_hook, dirs, shape, call):
    project, outside = dirs
    hook_session(project)
    payload, reason = _calls(project)[call]
    extras, cwd, env = host_shape(shape, project, outside)
    result = run_hook(HOOK, project, {**payload, **extras}, cwd=cwd, project_env=env)
    assert_decision(result, "cursor", "deny", reason=reason)


@pytest.mark.parametrize("shape", CURSOR_SHAPES)
@pytest.mark.parametrize("call", CALLS)
def test_cursor_hook_outside_a_project_with_no_run_allows_and_makes_no_context(run_hook, dirs, shape, call):
    project, outside = dirs
    payload, _reason = _calls(project)[call]
    if call == "marker-write":
        payload = _write(project / "notes.txt")
    extras, cwd, env = host_shape(shape, project, outside)
    result = run_hook(HOOK, project, {**payload, **extras}, cwd=cwd, project_env=env)
    _allowed_with_no_context(result, project, outside)


def test_bad_json_with_a_run_in_the_cursor_project_dir_is_denied(run_hook, dirs):
    project, outside = dirs
    hook_session(project)
    _extras, cwd, env = host_shape("cursor_env", project, outside)
    result = run_hook(HOOK, project, "{not json", cwd=cwd, project_env=env)
    assert_decision(result, "cursor", "deny", reason="not valid JSON")


def test_bad_json_with_the_cursor_shape_and_no_run_allows_and_makes_no_context(run_hook, dirs):
    project, outside = dirs
    _extras, cwd, env = host_shape("cursor_env", project, outside)
    result = run_hook(HOOK, project, "{not json", cwd=cwd, project_env=env)
    _allowed_with_no_context(result, project, outside)


def test_bad_json_with_a_run_at_the_hook_cwd_and_no_project_env_is_denied(run_hook, dirs):
    project, _outside = dirs
    hook_session(project)
    result = run_hook(HOOK, project, "{not json", cwd=project, project_env={})
    assert_decision(result, "cursor", "deny", reason="not valid JSON")


@pytest.mark.parametrize("shape", [
    pytest.param("claude_payload_cwd", id="claude_payload_cwd_wins_over_a_process_cwd_with_no_run"),
    # Claude Code: the agent ran `cd` to a dir with no run, then wrote P/src/x.py.
    pytest.param("reverse", id="payload_cwd_with_no_run_does_not_hide_the_process_cwd_run"),
    pytest.param("missing_payload_cwd", id="missing_payload_cwd_does_not_hide_the_process_cwd_run"),
])
def test_a_src_write_finds_the_run_from_any_claude_cwd_shape(run_hook, dirs, shape):
    project, outside = dirs
    hook_session(project)
    extras, cwd, env = host_shape(shape, project, outside)
    result = run_hook(HOOK, project, {**_write(project / "src" / "x.py"), **extras}, "claude",
                      cwd=cwd, project_env=env)
    assert_decision(result, "claude", "deny", reason=BEFORE_P9)


def test_two_runs_deny_when_one_candidate_denies(run_hook, dirs, tmp_path):
    project, _outside = dirs
    other = git_repo(tmp_path / "other")
    hook_session(other, task_id="cd-A", pipeline_run_id="run-A")
    hook_session(project, task_id="cd-P", pipeline_run_id="run-P")
    payload = {**_write(project / "src" / "x.py"), "cwd": str(other)}
    result = run_hook(HOOK, project, payload, "claude", cwd=project, project_env={})
    assert_decision(result, "claude", "deny", reason=BEFORE_P9)
    assert "cd-A" not in result[1]


@pytest.mark.parametrize("w1_run", [False, True])
def test_two_worktrees_of_one_primary_keep_the_process_cwd_run(run_hook, tmp_path, w1_run):
    _primary, trees = linked_worktrees(tmp_path)
    w1, w2 = trees["w0"], trees["w1"]
    (w2 / "src").mkdir()
    if w1_run:
        save_worktree_session(w1, task_id="cd-1", pipeline_run_id="run-1")
    save_worktree_session(w2, task_id="cd-2", pipeline_run_id="run-2")  # last: the root pointer names it
    payload = {**_write(w2 / "src" / "x.py"), "cwd": str(w1)}
    result = run_hook(HOOK, w2, payload, "claude", cwd=w2, project_env={})
    assert_decision(result, "claude", "deny", reason=BEFORE_P9)


@pytest.mark.parametrize("call", ("src-write", "src-shell"))
def test_missing_payload_cwd_with_no_run_allows_and_makes_no_context(run_hook, dirs, call):
    project, outside = dirs
    payload, _reason = _calls(project)[call]
    extras, cwd, env = host_shape("missing_payload_cwd", project, outside)
    result = run_hook(HOOK, project, {**payload, **extras}, "claude", cwd=cwd, project_env=env)
    assert_decision(result, "claude", "allow")
    assert not (project / ".context").exists()


# --- R1: a payload `cwd` must not hide the host project dirs ----------


def _cursor_case(name, project, outside, tmp_path):
    if name == "shell-cwd-outside-env":
        return {"cwd": str(outside)}, {"CURSOR_PROJECT_DIR": project}
    if name == "shell-cwd-outside-roots":
        return {"cwd": str(outside), "workspace_roots": [str(project)]}, {}
    if name == "missing-cwd-env":
        return {"cwd": str(outside / "gone")}, {"CURSOR_PROJECT_DIR": project}
    other = git_repo(tmp_path / "other-root")
    return {"workspace_roots": [str(other), str(project)]}, {}  # multi-root, run in the later root


R1_CASES = ("shell-cwd-outside-env", "shell-cwd-outside-roots", "missing-cwd-env", "multi-root-later")


@pytest.mark.parametrize("case", R1_CASES)
def test_cursor_project_dirs_are_candidates_with_any_payload_cwd(run_hook, dirs, tmp_path, case):
    project, outside = dirs
    hook_session(project)
    extras, env = _cursor_case(case, project, outside, tmp_path)
    payload = {**_shell(f"sed -i '' 's/a/b/' {project}/src/x.py"), **extras}
    result = run_hook(HOOK, project, payload, cwd=outside, project_env=env)
    assert_decision(result, "cursor", "deny", reason=BEFORE_P9)
