"""Who may write a verdict: never the orchestrator (cd-47 D2), and a report only by an attested Write or Edit (#78)."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

import pytest

from conftest import (CLAUDE_CHAT, HOOKS_DIR, activate, branch_repo, code_paths, git, git_repo, hook_denied,
                      linked_worktrees, make_session, save_worktree_session, shell_call, skill_gate_hook, write_call)

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import peer_attest  # noqa: E402
import session_store  # noqa: E402
from artifact_requirements import peer_written_filter, plan_review_cycles, require_artifact  # noqa: E402


# --- nested_orchestrator_cannot_write_verdicts: A subagent that runs activation is the orchestrator: it may not write verdicts (cd-47 D2, #47).
#
# Claude Code sets `agent_id` only inside a subagent. Before cd-47 the hook denied a
# verdict write with no `agent_id` (the top-level agent) and attested every other
# write. When a subagent ran /cadence, it was the orchestrator and had an `agent_id`,
# so it could write its own reviews and the gates accepted them.
#
# Now the shell hook leaves a note with the activating agent's id in the canonical
# `.context`. Activation takes the note into `orchestrator_agent_ids`. The hook denies
# a verdict write by any orchestrator, and the gates do not count its attest rows.

RUN_0 = str(uuid.UUID(int=0x1111_2222_3333_4444_5555_6666_7777_8888))
RUN_1 = str(uuid.UUID(int=0x9999_aaaa_bbbb_cccc_dddd_eeee_ffff_0000))
ACTIVATE = "python3 scripts/activate_session.py --from-branch --fresh"
SKILL_23 = 'python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --fresh --supersede'
SKILL_25 = 'python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --resume'
UNKNOWN = "orchestrator unknown: activate through the Bash tool"
PENDING = Path(".context") / session_store.PENDING_ORCHESTRATOR


def _notes(repo: Path) -> list[Path]:
    return sorted((repo / PENDING).glob("*.json"))


def _rows(context: Path) -> list[dict]:
    rows = []
    for log in sorted((context / "attest").glob("*.jsonl")):
        if not log.name.endswith(".agents.jsonl"):
            rows += [dict(json.loads(line), run=log.stem) for line in log.read_text(encoding="utf-8").splitlines()]
    return rows


def _note(context: Path, agent: str | None, age: float, name: str) -> Path:
    path = context / session_store.PENDING_ORCHESTRATOR / f"{name}-{agent or 'toplevel'}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agent_id": agent, "at": time.time() - age}), encoding="utf-8")
    return path


# --- seam: hook note -> activation -> verdict deny (G-D1) -------------------------------


def test_the_activating_subagent_cannot_write_a_verdict(tmp_path):
    repo = branch_repo(tmp_path / "r")
    assert not hook_denied(shell_call(repo, ACTIVATE, agent="A"))
    activate(repo, **CLAUDE_CHAT)
    session = session_store.load_worktree_session(repo)
    assert session["orchestrator_agent_ids"] == ["A"]
    assert session["orchestrator_source"] == "note"
    assert _notes(repo) == []
    review = repo / ".context" / "reviews" / "pr_x.md"
    assert hook_denied(write_call(repo, review, "A"))
    assert _rows(repo / ".context") == []
    assert not hook_denied(write_call(repo, review, "B"))
    rows = _rows(repo / ".context")
    assert [(r["agent_id"], r["run"]) for r in rows] == [("B", session["pipeline_run_id"])]


def test_a_top_level_activation_records_no_orchestrator_id(tmp_path):
    repo = branch_repo(tmp_path / "r")
    shell_call(repo, ACTIVATE)
    activate(repo, **CLAUDE_CHAT)
    session = session_store.load_worktree_session(repo)
    assert session["orchestrator_agent_ids"] == []
    assert session["orchestrator_source"] == "toplevel"
    assert hook_denied(write_call(repo, repo / ".context" / "reviews" / "pr_x.md", None))


@pytest.mark.parametrize("stale", [False, True], ids=["no_note", "stale_note"])
def test_a_missing_note_fails_the_attestation_gates_closed(tmp_path, stale):
    repo = branch_repo(tmp_path / "r")
    if stale:
        _note(repo / ".context", "A", age=200, name="20000101T000000000000Z")
    proc = activate(repo, **CLAUDE_CHAT)
    assert f"WARNING: {UNKNOWN}" in proc.stderr
    session = session_store.load_worktree_session(repo)
    assert session["orchestrator_source"] == "missing"
    status = activate(repo, "--status", **CLAUDE_CHAT)
    assert json.loads(status.stdout)["orchestrator_source"] == "missing"

    review = repo / ".context" / "prs" / "reviews" / "PR_REVIEW_#1.md"
    review.parent.mkdir(parents=True)
    review.write_text("RUN_BY_COMMAND: pr-review-local\n", encoding="utf-8")
    peer_attest.record(repo / ".context", session["pipeline_run_id"], "prs/reviews/PR_REVIEW_#1.md", "B",
                       peer_attest.sha256(review.read_bytes()))
    accept = peer_written_filter(repo / ".context", cwd=repo)
    assert accept is not None and not accept(review)
    errors = require_artifact(repo / ".context", relative_globs=("prs/reviews/*.md",),
                              markers=("RUN_BY_COMMAND: pr-review-local",), label="R1", accept=accept)
    assert errors and UNKNOWN in errors[0]


def test_a_missing_note_on_another_host_prints_no_warning(tmp_path):
    repo = branch_repo(tmp_path / "r")
    proc = activate(repo)
    assert "WARNING" not in proc.stderr
    assert peer_written_filter(repo / ".context", cwd=repo) is None


# --- activation from the primary checkout (MF-1) ---------------------------------------


@pytest.mark.parametrize("form", ["from_primary_cd", "from_primary_workspace"])
def test_activation_from_the_primary_checkout_takes_the_note(tmp_path, form):
    primary, trees = linked_worktrees(tmp_path)
    wt = trees["w1"]
    if form == "from_primary_cd":
        command, cwd, flags = f"cd {wt} && {ACTIVATE}", wt, ("--from-branch", "--fresh")
    else:
        command, cwd, flags = f"{ACTIVATE} --workspace {wt}", primary, ("--from-branch", "--fresh", "--workspace", str(wt))
    shell_call(primary, command, agent="A")
    activate(cwd, *flags, **CLAUDE_CHAT)
    session = json.loads((primary / ".context" / session_store.BY_WORKTREE / f"{session_store.worktree_key(wt)}.json")
                         .read_text(encoding="utf-8"))
    assert session["orchestrator_source"] == "note"
    assert session["orchestrator_agent_ids"] == ["A"]
    assert _notes(primary) == []


# --- the note pool (MF-1 decision) -------------------------------------------------------


def test_activation_takes_the_newest_fresh_note_and_drops_stale_ones(tmp_path):
    context = tmp_path / ".context"
    _note(context, "OLD", age=200, name="20000101T000000000000Z")
    _note(context, "NEW", age=10, name="20000101T000100000000Z")
    assert session_store.take_pending_orchestrator(context) == (True, "NEW")
    assert list((context / session_store.PENDING_ORCHESTRATOR).glob("*.json")) == []
    assert session_store.take_pending_orchestrator(context) == (False, None)


def test_two_fresh_notes_are_taken_newest_first(tmp_path):
    context = tmp_path / ".context"
    _note(context, "A", age=20, name="20000101T000000000000Z")
    _note(context, "B", age=5, name="20000101T000100000000Z")
    assert session_store.take_pending_orchestrator(context) == (True, "B")
    assert session_store.take_pending_orchestrator(context) == (True, "A")


def test_a_toplevel_note_is_found_with_no_agent(tmp_path):
    context = tmp_path / ".context"
    _note(context, None, age=1, name="20000101T000000000000Z")
    assert session_store.take_pending_orchestrator(context) == (True, None)


def test_a_payload_outside_a_git_repo_writes_no_note(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    shell_call(plain, ACTIVATE, agent="A")
    assert not any(p.name == ".context" for p in tmp_path.rglob("*"))


# --- hard stop ---------------------------------------------------------------------------


def test_a_hard_stop_writes_no_note_and_takes_none(tmp_path):
    repo = branch_repo(tmp_path / "r")
    shell_call(repo, ACTIVATE, agent="A")
    activate(repo, **CLAUDE_CHAT)
    shell_call(repo, "python3 scripts/activate_session.py --hard-stop retries --reason x", agent="A")
    assert _notes(repo) == []
    kept = _note(repo / ".context", "B", age=1, name="20990101T000000000000Z")
    activate(repo, "--hard-stop", "retries", "--reason", "x", **CLAUDE_CHAT)
    assert kept.is_file()


# --- first activation in a repo (SF-2) -----------------------------------------------------


def test_first_activation(tmp_path):
    repo = branch_repo(tmp_path / "r")
    assert not (repo / ".context").exists()
    shell_call(repo, ACTIVATE, agent="A")
    assert len(_notes(repo)) == 1
    activate(repo, **CLAUDE_CHAT)
    session = session_store.load_worktree_session(repo)
    assert session["orchestrator_source"] == "note"
    assert session["orchestrator_agent_ids"] == ["A"]


# --- anchored and quoted match (cycle-2 nit 1, MF-2 cycle 3) -----------------------------


NO_NOTE = [
    "grep -n x scripts/activate_session.py",
    "cat scripts/activate_session.py",
    'echo "python3 scripts/activate_session.py"',
    "python3 scripts/activate_session.py --status",
    "python3 scripts/activate_session.py --check",
    "python3 scripts/activate_session.py --deactivate",
]
NOTE = [
    pytest.param("python3 scripts/activate_session.py --fresh", id="plain"),
    pytest.param("cd {wt} && python3 /abs/scripts/activate_session.py --fresh", id="cd_abs"),
    pytest.param("FOO=1 uv run python3 scripts/activate_session.py --resume", id="env_uv"),
    pytest.param(SKILL_23, id="quoted_skill_23"),
    pytest.param(SKILL_25, id="quoted_skill_25"),
    pytest.param("python3 '/abs/scripts/activate_session.py' --fresh", id="quoted_single"),
    pytest.param('python3 "${CADENCE_HOME}/scripts/activate_session.py" --fresh', id="quoted_braces"),
    pytest.param("cd .\npython3 scripts/activate_session.py --fresh", id="newline"),
    pytest.param("{ python3 scripts/activate_session.py --fresh; }", id="brace_group"),
    pytest.param("env FOO=1 python3 scripts/activate_session.py --fresh", id="env_wrapper"),
    pytest.param("python3 scripts/activate_session.py --fresh\npython3 scripts/activate_session.py --status", id="status_next_line"),
]


@pytest.mark.parametrize("command", NO_NOTE)
def test_a_command_that_only_names_the_script_writes_no_note(tmp_path, command):
    repo = branch_repo(tmp_path / "r")
    shell_call(repo, command, agent="A")
    assert not (repo / ".context").exists()


@pytest.mark.parametrize("command", NOTE)
def test_an_activation_command_writes_a_note(tmp_path, command):
    repo = branch_repo(tmp_path / "r")
    shell_call(repo, command.replace("{wt}", str(repo)), agent="A")
    assert len(_notes(repo)) == 1


def test_quoted_skill_command_seam(tmp_path):
    repo = branch_repo(tmp_path / "r")
    shell_call(repo, SKILL_23, agent="A")
    activate(repo, **CLAUDE_CHAT)
    session = session_store.load_worktree_session(repo)
    assert (session["orchestrator_source"], session["orchestrator_agent_ids"]) == ("note", ["A"])


def _documented_activations() -> list[str]:
    found = []
    for doc in (ROOT / "SKILL.md", ROOT / "references" / "activation.md"):
        for match in re.finditer(r"(python3 [^`\n]*activate_session\.py[^`\n]*)", doc.read_text(encoding="utf-8")):
            command = match.group(1).strip()
            if not re.search(r"--(?:deactivate|status|check|hard-stop)\b", command):
                found.append(command)
    return found


def test_every_documented_activation_command_writes_a_note(tmp_path):
    commands = _documented_activations()
    assert SKILL_23 in commands and SKILL_25 in commands
    for i, command in enumerate(commands):
        repo = branch_repo(tmp_path / f"r{i}")
        shell_call(repo, command, agent="A")
        assert len(_notes(repo)) == 1, command


# --- the early return still takes the note (cycle-2 nit 1) ---------------------------------


def test_the_early_return_takes_the_note(tmp_path):
    repo = branch_repo(tmp_path / "r")
    shell_call(repo, ACTIVATE, agent="A")
    activate(repo, **CLAUDE_CHAT)
    shell_call(repo, "python3 scripts/activate_session.py --from-branch", agent="B")
    assert len(_notes(repo)) == 1
    proc = activate(repo, "--from-branch", **CLAUDE_CHAT)
    assert "already active" in proc.stderr
    assert _notes(repo) == []


# --- the gates do not count an orchestrator's rows ------------------------------------------


def _claude_run(repo: Path, run: str = RUN_0, ids: list[str] | None = None, **fields) -> Path:
    save_worktree_session(repo, pipeline_run_id=run, orchestrator_agent_ids=ids if ids is not None else ["A"],
                          orchestrator_source="note", workspace=str(repo.resolve()), **fields)
    return repo / ".context"


def _attest(context: Path, run: str, path: Path, agent: str) -> None:
    rel = path.resolve().relative_to(context.resolve()).as_posix()
    peer_attest.record(context, run, rel, agent, peer_attest.sha256(path.read_bytes()))


def _file(context: Path, rel: str, text: str = "RUN_BY_COMMAND: pr-review-local\n") -> Path:
    path = context / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_filter_rejects_an_orchestrator_row(tmp_path):
    repo = git_repo(tmp_path / "r")
    context = _claude_run(repo)
    review = _file(context, "prs/reviews/PR_REVIEW_#1.md")
    _attest(context, RUN_0, review, "A")
    accept = peer_written_filter(context, cwd=repo)
    assert not accept(review)
    _attest(context, RUN_0, review, "B")
    assert accept(review)


def test_plan_review_cycles_do_not_count_the_orchestrator(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "r")
    context = _claude_run(repo)
    monkeypatch.chdir(repo)
    _attest(context, RUN_0, _file(context, "reviews/plan_a.md"), "A")
    _attest(context, RUN_0, _file(context, "reviews/plan_b.md"), "B")
    assert plan_review_cycles(context) == 1


# --- concurrent runs (G-D5, SF-3) -----------------------------------------------------------


def _two_runs(tmp_path: Path) -> tuple[Path, dict[str, Path], Path]:
    primary, trees = linked_worktrees(tmp_path)
    _claude_run(trees["w0"], RUN_0, ["O0"], task_id="cd-47")
    _claude_run(trees["w1"], RUN_1, ["O1"], task_id="cd-48")  # activated last: the root redirect names RUN_1
    return primary, trees, primary / ".context"


def test_another_runs_orchestrator_is_denied(tmp_path):
    _primary, trees, context = _two_runs(tmp_path)
    review = context / "reviews" / "pr_cd-47.md"
    assert hook_denied(write_call(trees["w0"], review, "O0"))
    assert hook_denied(write_call(trees["w1"], review, "O0"))
    session_store.deactivate_session(trees["w0"])
    _file(context, "reviews/pr_cd-47.md")
    _attest(context, RUN_0, review, "O0")
    assert not peer_attest.attested_any(context, review, exclude_agents=peer_attest.orchestrator_ids(context))


def test_a_peer_row_goes_to_the_run_of_its_worktree(tmp_path):
    _primary, trees, context = _two_runs(tmp_path)
    review = context / "reviews" / "pr_cd-47.md"
    assert not hook_denied(write_call(trees["w0"], review, "P"))
    assert [r["run"] for r in _rows(context)] == [RUN_0]
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("RUN_BY_COMMAND: pr-review-local\n", encoding="utf-8")
    assert peer_written_filter(context, cwd=trees["w0"])(review)


def test_a_peer_row_from_no_workspace_goes_to_every_active_run(tmp_path):
    primary, _trees, context = _two_runs(tmp_path)
    assert not hook_denied(write_call(primary, context / "reviews" / "pr_cd-47.md", "P"))
    assert sorted(r["run"] for r in _rows(context)) == sorted([RUN_0, RUN_1])


def test_a_completed_root_record_does_not_open_the_deny(tmp_path):
    _primary, trees, context = _two_runs(tmp_path)
    session_store.deactivate_session(trees["w1"])  # the root redirect now names a completed record
    review = context / "reviews" / "pr_cd-47.md"
    assert hook_denied(write_call(trees["w0"], review, "O0"))
    assert hook_denied(write_call(trees["w0"], review, None))


def test_the_verdict_files_repo_decides_not_the_cwd(tmp_path):
    x = git_repo(tmp_path / "x")
    make_session(x / ".context", workspace=str(x), host="other")
    y = git_repo(tmp_path / "y")
    context_y = _claude_run(y, RUN_1, ["A"])
    review = context_y / "reviews" / "pr_cd-47.md"
    assert hook_denied(write_call(x, review, "A"))
    assert not hook_denied(write_call(x, review, "B"))
    assert [(r["agent_id"], r["run"]) for r in _rows(context_y)] == [("B", RUN_1)]


# --- R2 on PR #52: hook survivors (M9-M12) ------------------------------------------------------


def test_a_deny_is_the_only_decision_the_hook_prints(tmp_path):
    """M9: after a deny the hook returns; a second (allow) decision would follow otherwise."""
    _primary, trees, context = _two_runs(tmp_path)
    proc = write_call(trees["w0"], context / "reviews" / "pr_cd-47.md", "O0")
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    (trees["w0"] / ".cadence.json").write_text("{}\n", encoding="utf-8")
    git(trees["w0"], "commit", "-q", "--allow-empty", "-m", "ahead")
    proc = shell_call(trees["w0"], "gh pr create --fill")
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    # Cursor prints an allow as JSON too, so a second decision after the deny would show.
    proc = skill_gate_hook(trees["w0"], {"hook_event_name": "beforeShellExecution", "command": "gh pr create --fill",
                           "cwd": str(trees["w0"])}, "cursor")
    assert json.loads(proc.stdout)["permission"] == "deny"


def test_no_active_claude_run_means_no_verdict_deny(tmp_path):
    """M10: a completed Claude run and an active run of another host do not open the deny."""
    primary, trees = linked_worktrees(tmp_path)
    _claude_run(trees["w0"], RUN_0, ["O0"])
    session_store.deactivate_session(trees["w0"])
    save_worktree_session(trees["w1"], pipeline_run_id=RUN_1, host="other", workspace=str(trees["w1"].resolve()))
    assert not hook_denied(write_call(trees["w0"], primary / ".context" / "reviews" / "pr_cd-47.md", "O0"))
    assert _rows(primary / ".context") == []


def _hook_module(monkeypatch):
    import importlib

    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    monkeypatch.setenv("CADENCE_SKILL_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(HOOKS_DIR))
    return importlib.import_module("guard_skill_gate")


def test_the_attest_row_goes_to_the_deepest_workspace(tmp_path, monkeypatch):
    """M11: nested workspaces; the run of the innermost one owns the row."""
    hook = _hook_module(monkeypatch)
    outer, inner = tmp_path / "a", tmp_path / "a" / "b"
    (inner / "c").mkdir(parents=True)
    runs = [{"workspace": str(outer), "pipeline_run_id": "OUTER"}, {"workspace": str(inner), "pipeline_run_id": "INNER"}]
    assert hook._attest_runs(runs, inner / "c") == ["INNER"]
    assert hook._attest_runs(list(reversed(runs)), inner / "c") == ["INNER"]
    assert hook._attest_runs(runs, outer) == ["OUTER"]
    assert hook._attest_runs(runs, tmp_path) == ["OUTER", "INNER"]


def test_a_bad_payload_during_a_run_is_denied_with_exit_zero(tmp_path):
    """M12: the deny is a decision, not a hook error."""
    repo = branch_repo(tmp_path / "r")
    activate(repo, **CLAUDE_CHAT)
    proc = skill_gate_hook(repo, "{not json")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- #56: a subagent's Bash cwd resets to the parent's project ---------------------------------
# The note goes to the repo the command activates (`--workspace`, else the last `cd`), not to the
# payload cwd. Each case: payload cwd = repo A (the parent's project), activation of repo B.


@pytest.mark.parametrize("form", ["cd_abs", "cd_quoted", "cd_relative", "cd_chain", "workspace", "workspace_eq"])
def test_the_note_goes_to_the_activated_repo_not_the_bash_cwd(tmp_path, form):
    parent = branch_repo(tmp_path / "parent")
    target = branch_repo(tmp_path / "evals" / "b")
    command = {
        "cd_abs": f"cd {target} && {ACTIVATE}",
        "cd_quoted": f'cd "{target}" && {ACTIVATE}',
        "cd_relative": f"cd ../evals/b && {ACTIVATE}",
        "cd_chain": f"cd {tmp_path} && cd evals/b && {ACTIVATE}",
        "workspace": f"{ACTIVATE} --workspace {target}",
        "workspace_eq": f"{ACTIVATE} --workspace={target}",
    }[form]
    shell_call(parent, command, agent="A")
    assert _notes(parent) == [] and not (parent / ".context").exists()
    assert len(_notes(target)) == 1
    activate(target, **CLAUDE_CHAT)
    session = session_store.load_worktree_session(target)
    assert (session["orchestrator_source"], session["orchestrator_agent_ids"]) == ("note", ["A"])


def test_a_cd_into_a_dir_outside_git_writes_no_note(tmp_path):
    parent = branch_repo(tmp_path / "parent")
    plain = tmp_path / "plain"
    plain.mkdir()
    shell_call(parent, f"cd {plain} && {ACTIVATE}", agent="A")
    assert _notes(parent) == [] and not (plain / ".context").exists()


def test_a_cd_after_the_activation_does_not_move_the_note(tmp_path):
    parent = branch_repo(tmp_path / "parent")
    other = branch_repo(tmp_path / "other")
    shell_call(parent, f"{ACTIVATE} && cd {other}", agent="A")
    assert len(_notes(parent)) == 1 and _notes(other) == []


# --- R1 on PR #57: forms the target resolver must not break ---------------------------------------


@pytest.mark.parametrize("form", ["var_cd", "var_workspace", "subshell_closed", "quoted_cd", "cd_dash"])
def test_an_unreadable_or_closed_cd_falls_back_to_the_bash_cwd(tmp_path, form):
    """The hook cannot read a variable set in the same command, a `cd -`, or a cd in a closed subshell."""
    repo = branch_repo(tmp_path / "r")
    (repo / "sub").mkdir()
    other = branch_repo(tmp_path / "other")
    command = {
        "var_cd": f'D=sub; cd "$D" && {ACTIVATE}',
        "var_workspace": f'W=$(pwd); {ACTIVATE} --workspace "$W"',
        "subshell_closed": f"(cd {other} && ls); {ACTIVATE}",
        "quoted_cd": f'echo "cd {other}"; {ACTIVATE}',
        "cd_dash": f"cd - && {ACTIVATE}",
    }[form]
    shell_call(repo, command, agent="A")
    assert len(_notes(repo)) == 1 and _notes(other) == []


@pytest.mark.parametrize("template", [
    pytest.param("(cd {target} && {activate})", id="a_cd_inside_the_activations_subshell_counts"),
    pytest.param("cd -P {target} && {activate}", id="a_cd_with_a_flag_is_followed"),
    pytest.param("cd {target}\n{activate}", id="a_cd_on_the_line_before_is_followed"),
    # R1 re-review on PR #57 (Optional).
    pytest.param('cd "$UNSET_DIR" && {activate} --workspace {target}',
                 id="an_absolute_workspace_wins_over_an_unreadable_cd"),
])
def test_a_readable_target_takes_the_note(tmp_path, template):
    parent = branch_repo(tmp_path / "parent")
    target = branch_repo(tmp_path / "b")
    shell_call(parent, template.format(target=target, activate=ACTIVATE), agent="A")
    assert _notes(parent) == [] and len(_notes(target)) == 1


def test_cursor_payloads_use_the_same_target(tmp_path):
    parent = branch_repo(tmp_path / "parent")
    target = branch_repo(tmp_path / "b")
    proc = skill_gate_hook(parent, {"hook_event_name": "beforeShellExecution", "cwd": str(parent),
                                   "command": f"cd {target} && {ACTIVATE}", "conversation_id": "conv-C"}, "cursor")
    assert proc.returncode == 0
    assert _notes(parent) == [] and len(_notes(target)) == 1
    activate(target)
    assert session_store.load_worktree_session(target)["owner_session_id"] == "conv-C"


def test_the_owner_note_is_not_written_outside_git(tmp_path):
    """R1 re-review on PR #57 (Nit): a Cursor activation that cds into a plain folder creates no .context/."""
    parent = branch_repo(tmp_path / "parent")
    plain = tmp_path / "plain"
    plain.mkdir()
    proc = skill_gate_hook(parent, {"hook_event_name": "beforeShellExecution", "cwd": str(parent),
                                   "command": f"cd {plain} && {ACTIVATE}", "conversation_id": "conv-C"}, "cursor")
    assert proc.returncode == 0, proc.stderr
    assert not (plain / ".context").exists()


# --- shell_writes_to_verdict_reports_are_denied: A verdict report takes only an attested Write or Edit, from any agent (#78).
#
# On cd-64 and cd-76 a vibe-test peer started with `cat > .context/vibetest/<slug>_<date>.md`
# and replaced a 21 kB attested report with a stub. The hook denied the orchestrator's
# shell writes into `.context`, but a peer's went through.

REPORT = Path(".context") / "vibetest" / "spec_2026-10-18.md"


def _run(tmp_path: Path) -> Path:
    repo = branch_repo(tmp_path / "r")
    shell_call(repo, ACTIVATE, agent="A")
    activate(repo, **CLAUDE_CHAT)
    report = repo / REPORT
    report.parent.mkdir(parents=True)
    report.write_text("RUN_BY_SKILL: vibe-test\nthe real report\n", encoding="utf-8")
    return repo


def test_a_peer_shell_write_to_a_report_is_denied(tmp_path):
    repo = _run(tmp_path)
    proc = shell_call(repo, f"cat > {REPORT.as_posix()} <<'EOF'\nstub\nEOF", agent="B")
    assert hook_denied(proc)
    assert "attestation" in proc.stdout


def test_the_orchestrator_shell_write_to_a_report_is_denied(tmp_path):
    repo = _run(tmp_path)
    for agent in ("A", None):
        proc = shell_call(repo, f"echo stub > {REPORT.as_posix()}", agent=agent)
        assert hook_denied(proc)
        # R1 N-1: the text does not send an orchestrator to a Write that the hook also denies.
        assert "only a delegated peer writes" in proc.stdout
        assert "Write or Edit tool" not in proc.stdout


@pytest.mark.parametrize("rel", [
    "vibetest/spec_2026-10-18.md",
    "reviews/plan_spec.md",
    "adversarial/adversarial_spec.md",
    "benchmarks/spec_code.json",
    "prs/reviews/PR_REVIEW_#1.md",
    "reviews/pr_1.md",
])
def test_every_peer_artifact_glob_denies_a_peer_shell_write(tmp_path, rel):
    repo = _run(tmp_path)
    assert hook_denied(shell_call(repo, f"echo stub > .context/{rel}", agent="B"))


def test_a_peer_write_tool_call_is_still_allowed_and_attested(tmp_path):
    repo = _run(tmp_path)
    assert not hook_denied(write_call(repo, repo / REPORT, "B", "RUN_BY_SKILL: vibe-test\nrevised\n"))
    logs = [p for p in (repo / ".context" / "attest").glob("*.jsonl") if not p.name.endswith(".agents.jsonl")]
    assert any("vibetest/spec_2026-10-18.md" in p.read_text(encoding="utf-8") for p in logs)


def test_a_shell_write_elsewhere_in_context_keeps_its_old_rule(tmp_path):
    repo = _run(tmp_path)
    assert not hook_denied(shell_call(repo, "echo note > .context/proposals/idea.md", agent="B"))
