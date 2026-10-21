"""shared/skill_gate.py: the skill root, the ticket pattern, the Pi CLI, and the gated prefixes from config."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import git, install_config, linked_worktrees, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
os.environ["CADENCE_HOME"] = str(ROOT)
import skill_gate  # noqa: E402
from skill_gate import script_path, skill_root, ticket_pattern  # noqa: E402


# --- skill_gate_resolves_under_cadence: Gate script paths resolve under the cadence skill root.

def test_skill_gate_resolves_under_cadence():
    assert skill_root() == ROOT
    activate = script_path("activate_session.py")
    assert activate == ROOT / "scripts" / "activate_session.py"
    assert activate.is_relative_to(skill_root().resolve())


# --- adapter_ticket_pattern_loads_from_json: Phase 3: ticket regex loads from adapters/*.json (stdlib json only).

def test_adapter_ticket_pattern_loads_from_json():
    pat = ticket_pattern()
    assert pat.search("#123")
    assert pat.search("acme/widgets#99")


# --- skill_gate_pi_cli_state_and_check: Phase 3: Pi CLI state/check invocations work without Cursor hooks.

GATE = ROOT / "shared" / "skill_gate.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return run_py(GATE, *args, cwd=ROOT)


def test_skill_gate_pi_cli_state_and_check(tmp_path):
    state = _run("state", "--skill", "cadence", "--workspace", str(tmp_path))
    assert state.returncode == 0, state.stderr
    data = json.loads(state.stdout)
    assert data["skill"] == "cadence"
    assert data["skill_root"] == str(ROOT)
    assert str(data["activate_script"]).startswith(str(ROOT))

    check = _run(
        "check",
        "--skill",
        "cadence",
        "--action",
        "strreplace",
        "--file",
        "src/x.py",
        "--workspace",
        str(tmp_path),
    )
    # No session → blocked (fail-closed)
    assert check.returncode == 1
    assert "BLOCKED" in check.stdout


# --- skill_gate_gated_prefixes_from_config: Gated prefixes come from the defaults layer
# (cadence.defaults.json), plus the layers of a repo such as `.cadence.json`.
#
# Cadence's own code is in scripts/, hooks/ and shared/, which the default prefixes
# (src/, lib/, app/, packages/) do not hold back before P9. `.cadence.json` at the repo
# root adds them with `"gated_prefixes": {"append": [...]}`. It is read from the git
# toplevel of the written file, not from the process cwd (G-C14).

def test_skill_gate_gated_prefixes_from_config(tmp_path, monkeypatch):
    # skill_root() puts $CADENCE_HOME/scripts on sys.path. Restore it, or the stub
    # activate_session.py below shadows the real one in later tests.
    monkeypatch.setattr(sys, "path", list(sys.path))
    install_config(tmp_path, packs=[], adapter="github-issues", gated_prefixes=["services/", "apps/"])
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "github-issues.json").write_text(
        json.dumps({"name": "github-issues", "ticket_pattern": r"#\\d+"}),
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "activate_session.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "scripts" / "entry_invariant.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("CADENCE_HOME", str(tmp_path))
    assert skill_gate.gated_prefixes() == ("services/", "apps/")
    assert skill_gate.is_gated_path("services/foo.py")
    assert not skill_gate.is_gated_path("docs/readme.md")


def _cadence_json(repo: Path, data) -> None:
    (repo / ".cadence.json").write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_no_project_file_gives_exactly_the_configured_prefixes(tmp_path):
    git(tmp_path, "init", "-q")
    assert skill_gate.gated_prefixes(tmp_path) == skill_gate.gated_prefixes()


def test_project_extra_adds_to_the_defaults(tmp_path):
    git(tmp_path, "init", "-q")
    _cadence_json(tmp_path, {"gated_prefixes": {"append": ["scripts/", "install.sh"]}})
    prefixes = skill_gate.gated_prefixes(tmp_path)
    assert set(skill_gate.gated_prefixes()) <= set(prefixes)
    assert "scripts/" in prefixes and "install.sh" in prefixes


@pytest.mark.parametrize("bad", ["{not json", json.dumps({"gated_prefixes_extra": "scripts/"}),
                                 json.dumps({"gated_prefixes": {"append": [1]}}), json.dumps([])])
def test_a_bad_project_file_fails_closed(tmp_path, bad):
    git(tmp_path, "init", "-q")
    _cadence_json(tmp_path, bad)
    assert skill_gate.gated_write("docs/readme_notes.txt", tmp_path)


def test_a_repo_without_the_file_does_not_gate_tool_scripts(tmp_path):
    git(tmp_path, "init", "-q")
    assert not skill_gate.gated_write("tools/scripts/x.py", tmp_path)
    assert not skill_gate.is_gated_path("scripts/x.py", workspace=tmp_path)


def test_the_target_toplevel_decides_not_the_cwd(tmp_path, monkeypatch):
    """G-C14: the cwd is repo A; the file is in a linked worktree of repo B."""
    a = tmp_path / "a"
    a.mkdir()
    git(a, "init", "-q")
    primary, trees = linked_worktrees(tmp_path / "b", ("wt",))
    wt = trees["wt"]
    _cadence_json(primary, {"gated_prefixes": {"append": ["scripts/"]}})
    git(primary, "add", ".cadence.json")
    git(primary, "commit", "-q", "-m", "gate scripts")
    git(wt, "merge", "-q", "main")
    monkeypatch.chdir(a)
    assert skill_gate.gated_write(str(wt / "scripts" / "x.py"), wt)
    assert skill_gate.is_gated_path("scripts/x.py", workspace=primary)
    assert not skill_gate.is_gated_path("scripts/x.py", workspace=a)


def test_a_target_outside_the_workspace_stays_not_gated(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    _cadence_json(repo, {"gated_prefixes": {"append": ["scripts/"]}})
    other = tmp_path / "other"
    other.mkdir()
    assert not skill_gate.gated_write(str(other / "scripts" / "x.py"), repo)


def test_this_repo_gates_its_own_code():
    for path in ("scripts/x.py", "hooks/x.py", "shared/x.py", "install.sh"):
        assert skill_gate.is_gated_path(path, workspace=ROOT), path
    assert not skill_gate.is_gated_path("docs/x.md", workspace=ROOT)


def test_each_skill_scripts_dir_is_gated():
    """A prefix matches from the repo root only, so each skill's scripts/ needs its own extra."""
    for d in sorted((ROOT / "skills").glob("*/scripts")):
        path = f"skills/{d.parent.name}/scripts/x.py"
        assert skill_gate.is_gated_path(path, workspace=ROOT), f"add skills/{d.parent.name}/scripts/ to the gated_prefixes append of .cadence.json"


def test_the_hook_holds_a_project_prefix_write_before_p9(tmp_path, run_hook):
    from conftest import assert_decision, hook_session

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    _cadence_json(repo, {"gated_prefixes": {"append": ["scripts/"]}})
    hook_session(repo, layout="pointer", workspace=str(repo))
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(repo / "scripts" / "x.py"), "content": "x"}}
    assert_decision(run_hook("guard_skill_gate.py", repo, payload, "claude"), "claude", "deny")
    payload["tool_input"]["file_path"] = str(repo / "docs" / "x.md")
    assert_decision(run_hook("guard_skill_gate.py", repo, payload, "claude"), "claude", "allow")


# --- the Pi and Cursor check before `gh pr comment` and `gh pr review` waits for R5 of the run at HEAD.

@pytest.mark.parametrize("action", ["gh_pr_comment", "gh_pr_review"])
def test_a_pr_comment_waits_for_closure_of_the_run_at_head(tmp_path, action):
    from conftest import commit_file, context_ignored_repo, save_worktree_session

    repo = context_ignored_repo(tmp_path / "r")
    head = commit_file(repo, "work")
    save_worktree_session(repo, pipeline_run_id="run-178", workspace=str(repo))
    result = skill_gate.CadenceGate(repo).check_action(action, "pr")
    assert not result.allowed and "no R5 closure breadcrumb for run run-178" in result.message
    assert str(result.action_required).endswith('closure.py"')
    crumb = repo / ".context" / "gates" / "closure-run-178.json"
    crumb.parent.mkdir(parents=True, exist_ok=True)
    crumb.write_text(json.dumps({"pipeline_run_id": "run-178", "head": head}), encoding="utf-8")
    assert skill_gate.CadenceGate(repo).check_action(action, "pr").allowed
    commit_file(repo, "a fix after R5")
    assert not skill_gate.CadenceGate(repo).check_action(action, "pr").allowed
