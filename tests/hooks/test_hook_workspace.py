"""The hooks find the project and the skill root from the host, not from the hook cwd (cd-82)."""

from __future__ import annotations

import ast
import json
import re
import shlex
import sys
from pathlib import Path

import pytest

from conftest import (_REPO_ROOT, HANDOFF_HOOK, HANDOFF_PLAN, HANDOFF_PLAN_TEXT, HOST_SHAPES, assert_decision, code_paths, git_repo, hook_session,
                      host_shape, linked_worktrees, make_session, save_worktree_session, write_files)

pytestmark = pytest.mark.integration
code_paths()


# --- hook_workspace: hook_workspace and hook_candidates: the project comes from the host, not the hook cwd (cd-82).
#
# A user-level Cursor hook starts outside the project, with the project in
# `CURSOR_PROJECT_DIR` and `workspace_roots`. Claude Code sends payload `cwd`.

@pytest.fixture
def hp(scripts_path):
    import hook_protocol

    return hook_protocol


@pytest.fixture
def outside_cwd(tmp_path, monkeypatch):
    """A dir outside any repo, as the pytest process cwd."""
    path = tmp_path / "outside"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    """Five distinct dirs, one for each source, in resolver order."""
    dirs = {name: tmp_path / name for name in ("d_cwd", "d_cursor", "d_roots", "d_claude", "d_proc")}
    for path in dirs.values():
        path.mkdir()
    monkeypatch.chdir(dirs["d_proc"])
    return dirs


SOURCES = ("cwd", "cursor", "roots", "claude")


def _apply(dirs, monkeypatch, removed: int) -> dict:
    payload: dict = {}
    if removed < 1:
        payload["cwd"] = str(dirs["d_cwd"])
    if removed < 2:
        monkeypatch.setenv("CURSOR_PROJECT_DIR", str(dirs["d_cursor"]))
    if removed < 3:
        payload["workspace_roots"] = [str(dirs["d_roots"])]
    if removed < 4:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(dirs["d_claude"]))
    return payload


@pytest.mark.parametrize("removed", range(5))
def test_hook_workspace_takes_the_highest_source_that_is_present(hp, ladder, monkeypatch, removed):
    payload = _apply(ladder, monkeypatch, removed)
    want = ("d_cwd", "d_cursor", "d_roots", "d_claude", "d_proc")[removed]
    assert hp.hook_workspace(payload).resolve() == ladder[want].resolve()


def test_payload_cwd_wins_over_cursor_project_dir(hp, ladder, monkeypatch):
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(ladder["d_cursor"]))
    assert hp.hook_workspace({"cwd": str(ladder["d_cwd"])}) == ladder["d_cwd"]


@pytest.mark.parametrize("bad", ["", 7, None, ["x"]])
def test_payload_cwd_that_is_empty_or_not_text_is_skipped(hp, ladder, monkeypatch, bad):
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(ladder["d_cursor"]))
    assert hp.hook_workspace({"cwd": bad}) == ladder["d_cursor"]


def test_payload_cwd_that_does_not_exist_is_used(hp, ladder, monkeypatch, tmp_path):
    # Claude Code paths stay byte-equal to the old `payload.get("cwd") or Path.cwd()`.
    gone = tmp_path / "gone"
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(ladder["d_cursor"]))
    assert hp.hook_workspace({"cwd": str(gone)}) == gone


@pytest.mark.parametrize("value", ["", "missing"])
def test_env_and_roots_values_that_are_not_dirs_are_skipped(hp, ladder, monkeypatch, tmp_path, value):
    bad = str(tmp_path / value) if value else ""
    monkeypatch.setenv("CURSOR_PROJECT_DIR", bad)
    payload = {"workspace_roots": [bad]}
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ladder["d_claude"]))
    assert hp.hook_workspace(payload) == ladder["d_claude"]
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", bad)
    assert hp.hook_workspace(payload).resolve() == ladder["d_proc"].resolve()


@pytest.mark.parametrize("roots", [[], "not-a-list", [7]])
def test_workspace_roots_that_is_not_a_list_of_text_is_skipped(hp, ladder, monkeypatch, roots):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ladder["d_claude"]))
    assert hp.hook_workspace({"workspace_roots": roots}) == ladder["d_claude"]


def test_none_payload_skips_the_payload_steps(hp, ladder, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ladder["d_claude"]))
    assert hp.hook_workspace(None) == ladder["d_claude"]


def test_result_is_not_resolved(hp, ladder, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(ladder["d_cwd"])
    assert hp.hook_workspace({"cwd": str(link)}) == link


def test_candidates_are_payload_cwd_then_process_cwd(hp, ladder):
    got = hp.hook_candidates({"cwd": str(ladder["d_cwd"])})
    assert [p.resolve() for p in got] == [ladder["d_cwd"].resolve(), ladder["d_proc"].resolve()]


def test_candidates_have_no_duplicate(hp, ladder):
    from pathlib import Path

    assert hp.hook_candidates({"cwd": str(Path.cwd())}) == [Path.cwd()]


def test_candidates_with_no_source_are_the_process_cwd(hp, outside_cwd):
    from pathlib import Path

    assert hp.hook_candidates(None) == [Path.cwd()]


def test_candidates_drop_a_payload_cwd_that_does_not_exist(hp, outside_cwd, tmp_path):
    from pathlib import Path

    gone = {"cwd": str(tmp_path / "gone")}
    assert hp.hook_candidates(gone) == [Path.cwd()]
    assert hp.hook_workspace(gone) == tmp_path / "gone"


# --- candidate_contexts / write_context / has_session (U1, V2) ----------------


def _ctx(path):
    return path / ".context"


def test_has_session_is_a_file_check(hp, tmp_path):
    ctx = tmp_path / ".context"
    assert hp.has_session(ctx) is False
    ctx.mkdir()
    (ctx / "cadence-session.json").write_text("{not json", encoding="utf-8")
    assert hp.has_session(ctx) is True


def test_candidate_contexts_keep_the_workspace_and_dedupe_by_context(hp, ladder, tmp_path):
    shared = tmp_path / "shared-ctx"
    pairs = hp.candidate_contexts({"cwd": str(ladder["d_cwd"])}, lambda _w: shared)
    assert len(pairs) == 1
    assert pairs[0][0] == ladder["d_cwd"]
    assert pairs[0][1] == shared


def test_candidate_contexts_map_each_candidate(hp, ladder):
    pairs = hp.candidate_contexts({"cwd": str(ladder["d_cwd"])}, _ctx)
    assert [ctx for _w, ctx in pairs] == [_ctx(ladder["d_cwd"]), _ctx(pairs[1][0])]


def test_write_context_takes_the_process_context_first(hp, tmp_path, monkeypatch):
    a, p = tmp_path / "a", tmp_path / "p"
    make_session(_ctx(a))
    make_session(_ctx(p))
    monkeypatch.chdir(p)
    pairs = [(a, _ctx(a)), (p, _ctx(p))]
    assert hp.write_context(pairs, _ctx(p)) == (Path.cwd(), _ctx(p))


def test_write_context_gives_the_process_cwd_for_a_shared_context(hp, tmp_path, monkeypatch):
    # Two worktrees share one context; the dedupe kept the payload cwd pair (P6 C2).
    w1, w2, shared = tmp_path / "w1", tmp_path / "w2", tmp_path / "shared"
    w2.mkdir()
    make_session(shared)
    monkeypatch.chdir(w2)
    assert hp.write_context([(w1, shared)], shared) == (Path.cwd(), shared)


def test_write_context_falls_back_to_the_first_pair_with_a_session(hp, tmp_path):
    a, p = tmp_path / "a", tmp_path / "p"
    make_session(_ctx(a))
    pairs = [(a, _ctx(a)), (p, _ctx(p))]
    assert hp.write_context(pairs, _ctx(p)) == (a, _ctx(a))


def test_write_context_is_none_with_no_session(hp, tmp_path):
    a, p = tmp_path / "a", tmp_path / "p"
    assert hp.write_context([(a, _ctx(a)), (p, _ctx(p))], _ctx(p)) is None


def test_candidates_list_every_host_source_that_is_a_dir(hp, ladder, monkeypatch, tmp_path):
    second_root = tmp_path / "d_roots2"
    second_root.mkdir()
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(ladder["d_cursor"]))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ladder["d_claude"]))
    payload = {"cwd": str(ladder["d_cwd"]), "workspace_roots": [str(ladder["d_roots"]), str(second_root)]}
    got = [p.resolve() for p in hp.hook_candidates(payload)]
    want = [ladder[n].resolve() for n in ("d_cwd", "d_cursor", "d_roots")] + [second_root.resolve()]
    want += [ladder[n].resolve() for n in ("d_claude", "d_proc")]
    assert got == want


def test_candidates_skip_a_missing_payload_cwd_but_keep_the_env(hp, outside_cwd, monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(project))
    assert hp.hook_candidates({"cwd": str(tmp_path / "gone")}) == [project, Path.cwd()]


# --- run_hook_host_shape: run_hook starts the hook with the cwd and the project env of each host shape (cd-82).

PROBE = (
    "import json, os, sys\n"
    "print(json.dumps({'cwd': os.getcwd(), 'cursor': os.environ.get('CURSOR_PROJECT_DIR'),\n"
    "                  'claude': os.environ.get('CLAUDE_PROJECT_DIR'), 'stdin': sys.stdin.read()}))\n"
)


@pytest.fixture
def probe_dir(tmp_path):
    hooks = tmp_path / "probe" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "probe.py").write_text(PROBE, encoding="utf-8")
    return hooks


def _dirs(tmp_path):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    return project, outside


def test_defaults_are_the_repo_cwd_and_claude_project_dir(run_hook, probe_dir, tmp_path):
    project, _ = _dirs(tmp_path)
    code, out, _err = run_hook("probe.py", project, {}, hooks_dir=probe_dir)
    seen = json.loads(out)
    assert code == 0
    assert Path(seen["cwd"]).resolve() == project.resolve()
    assert seen["claude"] == str(project)
    assert seen["cursor"] is None


@pytest.mark.parametrize("name", HOST_SHAPES)
def test_each_host_shape_reaches_the_hook_process(run_hook, probe_dir, tmp_path, name):
    project, outside = _dirs(tmp_path)
    extras, cwd, env = host_shape(name, project, outside)
    code, out, _err = run_hook("probe.py", project, {"x": 1, **extras}, cwd=cwd, project_env=env,
                               hooks_dir=probe_dir)
    seen = json.loads(out)
    assert code == 0
    assert Path(seen["cwd"]).resolve() == Path(cwd).resolve()
    assert seen["cursor"] == (str(env["CURSOR_PROJECT_DIR"]) if "CURSOR_PROJECT_DIR" in env else None)
    assert seen["claude"] is None
    assert json.loads(seen["stdin"]) == {"x": 1, **extras}


# --- guards_follow_the_host_workspace: The primary-context, lean and handoff guards find the project from the host (cd-82, #82).
#
# The process cwd stays a candidate (D8), so a call that is denied today stays denied.
# A guard that writes state writes it into one context only (U1 to U3).



PRIMARY_HOOK = "guard_primary_context_artifacts.py"
LEAN_HOOK = "guard_lean_task_prompt.py"
SHADOW = "must live in primary .context"
FAT = "lean Task prompt rejected"


def _outside(tmp_path) -> Path:
    path = tmp_path / "outside"
    path.mkdir(exist_ok=True)
    return path


def _write(path) -> dict:
    return {"hook_event_name": "preToolUse", "tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x"}}


def _task(prompt: str) -> dict:
    return {"hook_event_name": "preToolUse", "tool_name": "Task", "tool_input": {"prompt": prompt}}


FAT_PROMPT = "/plan-review plan.md\nHere is what matters. Return to the main agent ONLY"


def _records(ctx: Path) -> list[Path]:
    return sorted((ctx / "subagent-runs").rglob("*.json")) if (ctx / "subagent-runs").is_dir() else []


# --- primary-context guard ---------------------------------------------------


@pytest.mark.parametrize("with_run", [True, False])
def test_shadow_write_in_the_cursor_worktree_is_denied_from_outside(run_hook, tmp_path, with_run):
    primary, trees = linked_worktrees(tmp_path)
    linked = trees["w0"]
    if with_run:
        hook_session(primary)
    extras, cwd, env = host_shape("cursor_env", linked, _outside(tmp_path))
    result = run_hook(PRIMARY_HOOK, linked, {**_write(linked / ".context" / "reviews" / "x.md"), **extras},
                      cwd=cwd, project_env=env)
    assert_decision(result, "cursor", "deny", reason=SHADOW)


def test_shadow_write_stays_denied_when_payload_cwd_is_outside(run_hook, tmp_path):
    _primary, trees = linked_worktrees(tmp_path)
    linked = trees["w0"]
    extras, cwd, env = host_shape("reverse", linked, _outside(tmp_path))
    result = run_hook(PRIMARY_HOOK, linked, {**_write(linked / ".context" / "reviews" / "x.md"), **extras},
                      "claude", cwd=cwd, project_env=env)
    assert_decision(result, "claude", "deny", reason=SHADOW)


def test_primary_guard_with_a_missing_payload_cwd_allows_and_makes_no_context(run_hook, tmp_path):
    project = git_repo(tmp_path / "project")
    extras, cwd, env = host_shape("missing_payload_cwd", project, _outside(tmp_path))
    result = run_hook(PRIMARY_HOOK, project, {**_write(project / ".context" / "reviews" / "x.md"), **extras},
                      "claude", cwd=cwd, project_env=env)
    assert_decision(result, "claude", "allow")
    assert not (project / ".context").exists()


# --- lean guard ----------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    return git_repo(tmp_path / "project")


@pytest.mark.parametrize("shape", [
    pytest.param("cursor_env", id="cursor_env"),
    pytest.param("cursor_roots_only", id="cursor_roots_only"),
    pytest.param("reverse", id="fat_task_prompt_stays_denied_when_payload_cwd_has_no_run"),
])
def test_fat_task_prompt_is_denied_from_outside_the_cursor_project(run_hook, project, tmp_path, shape):
    hook_session(project)
    extras, cwd, env = host_shape(shape, project, _outside(tmp_path))
    result = run_hook(LEAN_HOOK, project, {**_task(FAT_PROMPT), **extras}, cwd=cwd, project_env=env)
    assert_decision(result, "cursor", "deny", reason=FAT)


@pytest.mark.parametrize("shape", ["cursor_env", "missing_payload_cwd"])
def test_lean_guard_with_no_run_allows_and_makes_no_context(run_hook, project, tmp_path, shape):
    outside = _outside(tmp_path)
    extras, cwd, env = host_shape(shape, project, outside)
    result = run_hook(LEAN_HOOK, project, {**_task(FAT_PROMPT), **extras}, cwd=cwd, project_env=env)
    assert_decision(result, "cursor", "allow")
    assert not (project / ".context").exists()
    assert not (outside / ".context").exists()


def test_lean_prompt_with_two_runs_writes_one_record_in_the_process_run(run_hook, project, tmp_path):
    other = git_repo(tmp_path / "other")
    hook_session(other, pipeline_run_id="run-A")
    hook_session(project, pipeline_run_id="run-P")
    payload = {**_task("/plan-review plan.md"), "cwd": str(other)}
    result = run_hook(LEAN_HOOK, project, payload, cwd=project, project_env={})
    assert_decision(result, "cursor", "allow")
    assert _records(other / ".context") == []
    assert [p.relative_to(project / ".context").as_posix() for p in _records(project / ".context")] == [
        "subagent-runs/run-P/plan-review.json"
    ]


def test_two_runs_where_one_denies_write_no_record(run_hook, project, tmp_path):
    other = git_repo(tmp_path / "other")
    hook_session(other, pipeline_run_id="")
    hook_session(project, pipeline_run_id="run-P")
    payload = {**_task("/plan-review plan.md"), "cwd": str(other)}
    result = run_hook(LEAN_HOOK, project, payload, cwd=project, project_env={})
    assert_decision(result, "cursor", "deny", reason="no pipeline_run_id")
    assert _records(other / ".context") == []
    assert _records(project / ".context") == []


def test_one_repo_by_two_paths_writes_one_record(run_hook, project, tmp_path):
    hook_session(project, pipeline_run_id="run-P")
    link = tmp_path / "link"
    link.symlink_to(project)
    payload = {**_task("/plan-review plan.md"), "cwd": str(link)}
    result = run_hook(LEAN_HOOK, project, payload, cwd=project, project_env={})
    assert_decision(result, "cursor", "allow")
    assert len(_records(project / ".context")) == 1


# --- handoff guard (Claude Code only) ------------------------------------------


@pytest.fixture
def handoff_mod():
    scripts = str(_REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import handoff

    return handoff


def _agent(prompt="/plan-review plan.md", **extra) -> dict:
    tool_input = {"prompt": prompt, "description": "review", "subagent_type": "general-purpose", "model": "sonnet"}
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": tool_input, **extra}


@pytest.mark.parametrize("payload_in_project", [
    pytest.param(True, id="handoff_guard_reads_the_payload_cwd_run_when_the_process_cwd_has_none"),
    pytest.param(False, id="handoff_guard_keeps_the_process_cwd_run_first"),
])
def test_handoff_guard_finds_the_run_from_either_cwd(run_hook, make_root, project, tmp_path, payload_in_project):
    root = make_root(mode="enforce")
    hook_session(project)
    outside = _outside(tmp_path)
    payload_cwd, process_cwd = (project, outside) if payload_in_project else (outside, project)
    result = run_hook(HANDOFF_HOOK, project, _agent(cwd=str(payload_cwd)), "claude", hooks_dir=root / "hooks",
                      cwd=process_cwd, project_env={})
    assert_decision(result, "claude", "deny", reason="P3 needs plan")  # ADR-0010: the run has no plan


def test_handoff_guard_with_two_runs_uses_the_process_run(run_hook, make_root, handoff_mod, project, tmp_path):
    import peer_models

    root = make_root(mode="enforce")
    other = git_repo(tmp_path / "other")
    (other / "plan.md").write_text("Step.\n", encoding="utf-8")
    hook_session(other, pipeline_run_id="run-A", peer_models={"plan-review": "opus"})
    handoff_mod.write_packet(workspace=other, context_dir=other / ".context", run_key="run-A", block="P3",
                             inputs=["plan.md"], ticket="gh-1", root=root)
    hook_session(project, pipeline_run_id="run-P", peer_models={"plan-review": "opus"})
    result = run_hook(HANDOFF_HOOK, project, _agent(cwd=str(other)), "claude", hooks_dir=root / "hooks",
                      cwd=project, project_env={})
    assert_decision(result, "claude", "deny", reason="peer_models sets plan-review to opus")
    assert peer_models.read_rows(project / ".context", "run-P")
    assert not peer_models.read_rows(other / ".context", "run-A")


def test_handoff_model_row_keys_on_the_chosen_worktree_run(run_hook, make_root, tmp_path):
    import peer_models

    root = make_root(mode="enforce")
    primary, trees = linked_worktrees(tmp_path)
    w0, w1 = trees["w0"], trees["w1"]
    save_worktree_session(w0, pipeline_run_id="run-W", peer_models={"plan-review": "opus"})
    save_worktree_session(w1, pipeline_run_id="run-R", peer_models={"plan-review": "opus"})  # root pointer
    result = run_hook(HANDOFF_HOOK, w0, _agent(cwd=str(w0)), "claude", hooks_dir=root / "hooks",
                      cwd=_outside(tmp_path), project_env={})
    assert_decision(result, "claude", "deny", reason="peer_models sets plan-review to opus")
    ctx = primary / ".context"
    assert peer_models.read_rows(ctx, "run-W")
    assert not peer_models.read_rows(ctx, "run-R")


def test_handoff_packet_check_uses_the_process_worktree(run_hook, make_root, handoff_mod, tmp_path):
    root = make_root(mode="enforce")
    primary, trees = linked_worktrees(tmp_path)
    w0, w1 = trees["w0"], trees["w1"]
    write_files(w1, {HANDOFF_PLAN: HANDOFF_PLAN_TEXT})  # ADR-0010: the plan input exists only in w1
    save_worktree_session(w1, pipeline_run_id="run-1")
    result = run_hook(HANDOFF_HOOK, w1, _agent(cwd=str(w0)), "claude", hooks_dir=root / "hooks",
                      cwd=w1, project_env={})
    assert_decision(result, "claude", "allow")
    rendered = handoff_mod.render_prompt(handoff_mod.load_packet(primary / ".context", "run-1", "P3"), w1)
    assert json.loads(result[1])["hookSpecificOutput"]["updatedInput"]["prompt"] == rendered
    assert rendered.startswith(f"/plan-review {HANDOFF_PLAN}\n")


def test_handoff_guard_with_a_missing_payload_cwd_and_no_run_allows(run_hook, make_root, project, tmp_path):
    root = make_root(mode="enforce")
    extras, cwd, env = host_shape("missing_payload_cwd", project, _outside(tmp_path))
    result = run_hook(HANDOFF_HOOK, project, {**_agent(), **extras}, "claude", hooks_dir=root / "hooks",
                      cwd=cwd, project_env=env)
    assert_decision(result, "claude", "allow")
    assert not (project / ".context").exists()



def test_handoff_packet_check_uses_the_payload_repo_when_the_process_cwd_has_no_run(
    run_hook, make_root, handoff_mod, project, tmp_path
):
    # R2 (PR #87): pre_agent must use the workspace that main resolves, not Path.cwd().
    root = make_root(mode="enforce", rewrite_prompt=True)
    write_files(project, {HANDOFF_PLAN: HANDOFF_PLAN_TEXT})  # the input exists only in the project
    hook_session(project)
    result = run_hook(HANDOFF_HOOK, project, _agent(cwd=str(project)), "claude",
                      hooks_dir=root / "hooks", cwd=_outside(tmp_path), project_env={})
    assert_decision(result, "claude", "allow")
    rendered = handoff_mod.render_prompt(handoff_mod.load_packet(project / ".context", "run-1", "P3"), project)
    updated = json.loads(result[1])["hookSpecificOutput"]["updatedInput"]
    assert updated["prompt"] == rendered


# --- hook_text_names_resolved_skill_root: Hook deny text names the resolved skill root, not the literal `$CADENCE_HOME` (cd-82 D6).
#
# The agent shell does not export CADENCE_HOME (install.sh sets it only for itself), so
# `"$CADENCE_HOME/scripts/x.py"` expands to `/scripts/x.py`.


HOOKS = sorted((_REPO_ROOT / "hooks").glob("*.py"))
# session_start.py writes the env export itself; it prints no deny text.
SKIP = {"session_start.py"}


def _emitted_strings(tree: ast.AST) -> list[str]:
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


@pytest.mark.parametrize("path", [p for p in HOOKS if p.name not in SKIP], ids=lambda p: p.name)
def test_no_hook_string_has_the_literal_env_var(path):
    found = [s for s in _emitted_strings(ast.parse(path.read_text(encoding="utf-8"))) if "$CADENCE_HOME" in s]
    assert found == []


def _commands(text: str) -> list[Path]:
    return [Path(shlex.split(m)[0]) for m in re.findall(r"python3 ((?:'[^']*'|\S)+)", text)]


def _assert_runnable(out: str) -> None:
    text = json.dumps(json.loads(out))
    assert "$CADENCE_HOME" not in text
    paths = _commands(json.loads(out).get("user_message")
                      or json.loads(out).get("hookSpecificOutput", {}).get("permissionDecisionReason", ""))
    assert paths, out
    assert all(p.is_file() for p in paths), paths


def test_skill_gate_recover_text_names_a_script_that_exists(run_hook, tmp_path):
    repo = git_repo(tmp_path / "repo")
    (repo / ".context").mkdir()
    (repo / ".context" / "cadence-session.json").write_text("{not json", encoding="utf-8")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(repo / "src" / "x.py")}}
    result = run_hook("guard_skill_gate.py", repo, payload)
    assert_decision(result, "cursor", "deny", reason="unreadable")
    _assert_runnable(result[1])


def test_handoff_missing_input_text_names_the_block_that_writes_it(run_hook, make_root, tmp_path):
    root = make_root(mode="enforce")
    repo = git_repo(tmp_path / "repo")
    hook_session(repo)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(repo),
               "tool_input": {"prompt": "/plan-review plan.md", "subagent_type": "general-purpose", "model": "x"}}
    result = run_hook(HANDOFF_HOOK, repo, payload, "claude", hooks_dir=root / "hooks")
    assert_decision(result, "claude", "deny", reason="Block P1 (plan-create) writes it")
    assert "$CADENCE_HOME" not in result[1]


def test_handoff_unreadable_session_text_names_a_script_that_exists(run_hook, make_root, tmp_path):
    root = make_root(mode="enforce")
    repo = git_repo(tmp_path / "repo")
    (repo / ".context").mkdir()
    (repo / ".context" / "cadence-session.json").write_text("{not json", encoding="utf-8")
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(repo),
               "tool_input": {"prompt": "/plan-review plan.md", "subagent_type": "general-purpose", "model": "x"}}
    result = run_hook(HANDOFF_HOOK, repo, payload, "claude", hooks_dir=root / "hooks")
    assert_decision(result, "claude", "deny", reason="unreadable")
    _assert_runnable(result[1])
