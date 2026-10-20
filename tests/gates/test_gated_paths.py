"""Gated paths: a prefix matches a path relative to the workspace, after it is resolved, and only from the path start (cd-10-F4, #119, #48)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (assert_decision, git, git_env, git_repo, hook_session, linked_worktrees, save_worktree_session,
                      tool_decision)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
import skill_gate  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_gate_caches():
    """The fixtures change repo layouts and `.cadence.json` files in one process."""
    caches = (skill_gate._toplevel, skill_gate._primary_checkout, skill_gate._folds_case)
    for cache in caches:
        cache.cache_clear()
    yield
    for cache in caches:
        cache.cache_clear()


# --- gated_prefixes_match_from_the_path_start: A gated prefix matches only from the start of the path, relative to a fixed anchor (#48).
#
# The anchors are the workspace repo root, its primary checkout, the git toplevel of
# the file, and the workspace directory. The gate holds a write when any (anchor, path form) pair matches a
# prefix of that anchor's repo from the path start. A copy into
# `cbench-cache/src/cadence@<sha>/` is not gated by `src/`. The result does not
# depend on PYTHONHASHSEED.
#
# Spec: `.context/proposals/gh-48.md` (D1-D14). The test numbers follow the plan.

HOOK = "guard_skill_gate.py"
BEFORE_P9 = "entry invariant"
NOTE = ".cadence.json is not valid; all writes are gated"
SEEDS = ("1", "2", "3", "4", "5", "6")


def gated_write_per_seed(target: str, workspace: Path) -> dict[str, str]:
    """`gated_write(target, workspace)` in a new interpreter for each seed in SEEDS."""
    code = (
        f"import sys; from pathlib import Path; sys.path.insert(0, {str(ROOT / 'shared')!r}); "
        f"import skill_gate; print(skill_gate.gated_write({target!r}, Path({str(workspace)!r})))"
    )
    results = {}
    for seed in SEEDS:
        proc = subprocess.run([sys.executable, "-c", code], cwd=workspace, capture_output=True, text=True,
                              env=git_env(CADENCE_HOME=str(ROOT), PYTHONHASHSEED=seed))
        assert proc.returncode == 0, f"seed {seed}: {proc.stderr}"
        results[seed] = proc.stdout.strip()
    return results


def every_seed(value: bool) -> dict[str, str]:
    return {seed: str(value) for seed in SEEDS}


def cli(*args: str, workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "shared" / "skill_gate.py"), *args, "--workspace", str(workspace)],
                          cwd=workspace, capture_output=True, text=True, env=git_env(CADENCE_HOME=str(ROOT)))


def check(path: str, workspace: Path) -> subprocess.CompletedProcess:
    return cli("check", "--skill", "cadence", "--action", "write", "--file", path, workspace=workspace)


def write(path: Path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x = 1\n"}}


def shell(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def cadence_json(repo: Path, data) -> None:
    (repo / ".cadence.json").write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    return path


def worktree_inside_the_primary(tmp_path: Path) -> tuple[Path, Path]:
    """A linked worktree at <primary>/packages/w2, below the primary's `packages/` prefix."""
    primary, _ = linked_worktrees(tmp_path, names=())
    (primary / "packages").mkdir()
    w2 = primary / "packages" / "w2"
    git(primary, "worktree", "add", "-q", "-b", "feature/w2", str(w2))
    return primary, w2


@pytest.fixture
def ws(tmp_path):
    return git_repo(tmp_path / "ws")


# --- S1-S4 and the ticket shape ---------------------------------------------------


@pytest.mark.parametrize("target, gated", [
    pytest.param("cbench-cache/src/cadence@abc/x.py", False, id="1_the_ticket_copy_is_not_gated"),
    pytest.param("src/x.py", True, id="2_s1_src_is_gated"),
    pytest.param("{ws}/cbench-cache/src/cadence@sha/x.py", False,
                 id="3_s2_an_absolute_copy_below_another_directory_is_not_gated"),
    pytest.param("docs/src/x.md", False, id="4_s3_a_prefix_below_another_directory_is_not_gated"),
    pytest.param("{tmp}/elsewhere/src/x.py", False, id="5_s4_a_path_outside_the_workspace_is_not_gated"),
])
def test_s1_to_s4_a_prefix_gates_only_from_the_path_start(ws, tmp_path, target, gated):
    assert bool(skill_gate.gated_write(target.format(ws=ws, tmp=tmp_path), ws)) is gated


# --- S5: a subdirectory workspace, six seeds ----------------------------------------


def _subdirectory_workspace(tmp_path: Path, extra: str) -> tuple[str, Path]:
    repo = git_repo(tmp_path / "repo")
    cadence_json(repo, {"gated_prefixes": {"append": [extra]}})
    sub = repo / "skills" / "read-memories"
    sub.mkdir(parents=True)
    return str(sub / "scripts" / "x.py"), sub


@pytest.mark.parametrize("extra, gated", [
    pytest.param("scripts/", False, id="6a_s5_a_subdirectory_workspace_anchors_at_the_repo_root"),
    pytest.param("skills/read-memories/scripts/", True, id="6b_s5_a_deeper_extra_gates_for_every_seed"),
])
def test_6_s5_a_subdirectory_workspace_matches_the_extra_from_the_repo_root(tmp_path, extra, gated):
    target, sub = _subdirectory_workspace(tmp_path, extra)
    assert gated_write_per_seed(target, sub) == every_seed(gated)


def test_6c_a_subdirectory_workspace_also_anchors_at_itself(tmp_path):
    """R1 D-1: a workspace at <mono>/services/api still gates its own src/ (built-in prefixes)."""
    mono = git_repo(tmp_path / "mono")
    api = mono / "services" / "api"
    api.mkdir(parents=True)
    assert skill_gate.gated_write("src/x.py", api)
    assert skill_gate.gated_write(str(api / "src" / "x.py"), api)
    assert not skill_gate.gated_write("docs/x.md", api)
    assert gated_write_per_seed(str(api / "src" / "x.py"), api) == every_seed(True)


def test_5b_a_file_in_another_repo_outside_the_workspace_is_not_gated(ws, tmp_path):
    """R2: the file's own repo root is an anchor only inside the workspace (gh-48-R2-F02)."""
    other = git_repo(tmp_path / "other")
    assert not skill_gate.gated_write(str(touch(other / "src" / "x.py")), ws)


# --- D3, D4: the prefix rule ----------------------------------------------------------


def test_7_s6_a_dot_directory_prefix_gates(ws):
    cadence_json(ws, {"gated_prefixes": {"append": [".github/"]}})
    assert skill_gate.gated_write(".github/workflows/ci.yml", ws)


def test_8_a_prefix_without_a_slash_matches_the_name_or_a_directory(ws):
    cadence_json(ws, {"gated_prefixes": {"append": ["install.sh", "src"]}})
    assert skill_gate.gated_write("install.sh", ws)
    assert skill_gate.gated_write("src", ws)
    assert skill_gate.gated_write("src/y.py", ws)
    assert not skill_gate.gated_write("install.sh.bak", ws)
    assert not skill_gate.gated_write("srcfoo/y.py", ws)


def test_8b_a_leading_dot_slash_is_removed(ws):
    assert skill_gate.is_gated_path("./src/x.py", workspace=ws)
    assert skill_gate.is_gated_path("././src/x.py", workspace=ws)


# --- D1, D7: nested repos ---------------------------------------------------------------


def test_9_a_nested_repo_uses_its_own_root_and_the_workspace_root(ws):
    git_repo(ws / "vendor" / "x")
    assert skill_gate.gated_write(str(ws / "vendor" / "x" / "src" / "a.py"), ws)
    assert not skill_gate.gated_write(str(ws / "vendor" / "y.py"), ws)
    assert skill_gate.gated_write(str(ws / "src" / "b.py"), ws)


def test_10_a_workspace_outside_git_anchors_at_the_workspace(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert skill_gate._primary_checkout(str(plain)) == plain.resolve()
    assert skill_gate.gated_write("src/x.py", plain)
    assert not skill_gate.gated_write("docs/src/x.md", plain)


def _nested_cadence_clone(ws: Path, config) -> Path:
    clone = git_repo(ws / "cbench-cache" / "src" / "cadence@abc")
    cadence_json(clone, config)
    return clone


def test_14_a_nested_cadence_clone_uses_its_own_prefixes(ws):
    clone = _nested_cadence_clone(ws, {"gated_prefixes": {"append": ["scripts/"]}})
    assert not skill_gate.gated_write(str(clone / "x.py"), ws)
    assert skill_gate.gated_write(str(clone / "scripts" / "x.py"), ws)


def test_14b_a_bad_config_in_a_nested_clone_gates_only_inside_it(ws):
    clone = _nested_cadence_clone(ws, "{not json")
    assert skill_gate.gated_write(str(clone / "x.py"), ws)
    assert not skill_gate.gated_write(str(ws / "docs" / "x.md"), ws)


def test_16_a_nested_repo_below_src_stays_gated(ws, run_hook):
    git_repo(ws / "src" / "sub")
    target = ws / "src" / "sub" / "x.py"
    assert skill_gate.gated_write(str(target), ws)
    hook_session(ws)
    assert_decision(run_hook(HOOK, ws, write(target), "claude"), "claude", "deny", reason=BEFORE_P9)


def test_17_git_init_src_does_not_open_the_gate(ws, run_hook):
    target = touch(ws / "src" / "a.py")
    git(ws / "src", "init", "-q")
    assert skill_gate.gated_write(str(target), ws)
    hook_session(ws)
    assert_decision(run_hook(HOOK, ws, write(target), "claude"), "claude", "deny", reason=BEFORE_P9)


# --- D8: symlinks, six seeds -----------------------------------------------------------


def _alias(ws: Path, name: str = "alias") -> None:
    touch(ws / "src" / "a.py")
    (ws / name).symlink_to(ws / "src", target_is_directory=True)


@pytest.mark.parametrize("name, target", [
    pytest.param("alias", "{ws}/alias/a.py", id="18a_an_absolute_path_through_a_symlink_to_src_is_gated"),
    pytest.param("alias", "alias/a.py", id="18b_a_relative_path_through_a_symlink_to_src_is_gated"),
    # Test paths (P7 gh-48-F02): a symlink named like a test directory does not exempt src/.
    pytest.param("tests", "{ws}/tests/a.py", id="25_a_test_like_symlink_to_src_is_gated"),
])
def test_18_a_path_through_a_symlink_to_src_is_gated(ws, name, target):
    _alias(ws, name=name)
    assert gated_write_per_seed(target.format(ws=ws), ws) == every_seed(True)


def test_26_a_src_symlink_to_a_dir_outside_the_workspace_is_gated(ws, tmp_path):
    """The name the writer used counts too (D8, gh-48-R2-F01): src/ points outside every anchor."""
    (tmp_path / "outside").mkdir()
    (ws / "src").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert skill_gate.gated_write("src/x.py", ws)
    assert gated_write_per_seed(str(ws / "src" / "x.py"), ws) == every_seed(True)


# --- D7, D10: linked worktrees ---------------------------------------------------------


@pytest.mark.parametrize("layout", ["nested", "git-init"])
def test_20_a_nested_repo_in_the_primary_stays_gated_from_a_linked_worktree(tmp_path, run_hook, layout):
    primary, trees = linked_worktrees(tmp_path)
    w0 = trees["w0"]
    touch(primary / "src" / "a.py")
    if layout == "nested":
        git_repo(primary / "src" / "sub")
        target = touch(primary / "src" / "sub" / "x.py")
    else:
        git(primary / "src", "init", "-q")
        target = primary / "src" / "a.py"
    control = touch(primary / "docs" / "d.md")
    assert skill_gate.gated_write(str(target), w0)
    assert not skill_gate.gated_write(str(control), w0)
    save_worktree_session(w0)
    deny = run_hook(HOOK, w0, write(target), "claude", cwd=w0, project_env={})
    assert_decision(deny, "claude", "deny", reason=BEFORE_P9)
    allow = run_hook(HOOK, w0, write(control), "claude", cwd=w0, project_env={})
    assert_decision(allow, "claude", "allow")


def test_21a_a_worktree_inside_the_primary_is_not_gated_by_the_primary_prefix(tmp_path):
    primary, w2 = worktree_inside_the_primary(tmp_path)
    assert not skill_gate.gated_write("README.md", w2)
    assert skill_gate.gated_write("src/a.py", w2)
    assert skill_gate.gated_write(str(primary / "src" / "a.py"), w2)


def test_21b_a_worktree_inside_the_primary_keeps_its_docs_writable(tmp_path):
    _primary, w2 = worktree_inside_the_primary(tmp_path)
    assert gated_write_per_seed(str(w2 / "docs" / "x.md"), w2) == every_seed(False)


@pytest.mark.parametrize("layout", ["outside", "inside"])
def test_23_a_worktree_symlink_into_the_primary_src_stays_gated(tmp_path, layout):
    if layout == "outside":
        primary, trees = linked_worktrees(tmp_path)
        work = trees["w0"]
    else:
        primary, work = worktree_inside_the_primary(tmp_path)
    touch(primary / "src" / "a.py")
    (work / "alias").symlink_to(primary / "src", target_is_directory=True)
    assert gated_write_per_seed(str(work / "alias" / "a.py"), work) == every_seed(True)


# --- Test paths (P7 gh-48-F02) ---------------------------------------------------------


def test_24_test_paths_below_src_stay_exempt(ws):
    assert not skill_gate.gated_write("src/tests/test_a.py", ws)
    assert not skill_gate.gated_write("src/test_a.py", ws)
    assert skill_gate.gated_write("src/a.py", ws)


# --- D5, D11: the BLOCKED message ------------------------------------------------------


def test_11_the_message_lists_the_repo_extras(ws):
    cadence_json(ws, {"gated_prefixes": {"append": ["scripts/"]}})
    hook_session(ws)
    result = skill_gate.CadenceGate(ws).check_action("write", "scripts/x.py")
    assert not result.allowed
    assert "'scripts/'" in result.message


def _bad_config_repo(ws: Path) -> Path:
    cadence_json(ws, "{not json")
    hook_session(ws)
    return ws


def test_13a_a_bad_config_stays_blocked(ws, run_hook):
    repo = _bad_config_repo(ws)
    proc = check("src/x.py", repo)
    assert proc.returncode == 1, proc.stdout
    assert "BLOCKED" in proc.stdout and "gated path" in proc.stdout
    assert "Traceback" not in proc.stderr
    assert_decision(run_hook(HOOK, repo, write(repo / "src" / "x.py"), "claude"), "claude", "deny", reason=BEFORE_P9)


def test_13b_a_bad_config_names_the_note(ws):
    proc = check("src/x.py", _bad_config_repo(ws))
    assert proc.returncode == 1
    assert NOTE in proc.stdout


def test_15_state_names_the_note(ws):
    cadence_json(ws, "{not json")
    proc = cli("state", "--skill", "cadence", workspace=ws)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert NOTE in proc.stdout
    assert skill_gate.NOTE_INVALID_CONFIG == NOTE


def test_22_the_message_lists_only_the_anchors_that_hold_the_target(tmp_path):
    primary, trees = linked_worktrees(tmp_path)
    w0 = trees["w0"]
    cadence_json(primary, "{not json")
    cadence_json(w0, {"gated_prefixes": {"append": ["scripts/"]}})
    save_worktree_session(w0)
    proc = check(str(w0 / "scripts" / "x.py"), w0)
    assert proc.returncode == 1, proc.stdout
    assert "'scripts/'" in proc.stdout
    assert NOTE not in proc.stdout
    assert "Traceback" not in proc.stderr
    assert not skill_gate.gated_write(str(w0 / "docs" / "x.md"), w0)


# --- Exit criterion 2: three surfaces agree -------------------------------------------

SURFACE_CASES = [
    pytest.param("cbench-cache/src/cadence@sha/x.py", "allow", id="S2"),
    pytest.param("docs/src/x.md", "allow", id="S3"),
    pytest.param("src/x.py", "deny", id="control"),
]


@pytest.mark.parametrize("rel, want", SURFACE_CASES)
def test_12_the_edit_hook_the_shell_guard_and_the_cli_agree(ws, run_hook, rel, want):
    touch(ws / "a.txt")
    hook_session(ws)  # no P9 marker
    path = ws / rel
    reason = BEFORE_P9 if want == "deny" else None
    assert_decision(run_hook(HOOK, ws, write(path), "claude"), "claude", want, reason=reason)
    assert_decision(run_hook(HOOK, ws, shell(f"cp a.txt {path}"), "claude"), "claude", want)
    proc = check(str(path), ws)
    if want == "deny":
        assert proc.returncode == 1 and "gated path" in proc.stdout, proc.stdout
    else:
        assert proc.returncode == 0 and "ALLOWED" in proc.stdout, proc.stdout


# --- gated_paths_are_relative_to_the_workspace: Gated prefixes (src/, lib/, ...) apply to paths inside the workspace, relative to its root.
#
# cd-10-F4: hosts send absolute paths, so a repo under ~/src/ had every write
# blocked before P9, and paths outside the repo containing /lib/ were gated.



@pytest.fixture
def src_repo(tmp_path):
    repo = git_repo(tmp_path / "home" / "me" / "src" / "proj")
    hook_session(repo, resume_slug="plan-create")
    return repo


@pytest.mark.parametrize("rel", ["docs/plan.md", ".cursor/plans/x.plan.md", "README.md"])
def test_ungated_files_in_a_repo_under_src_stay_writable(src_repo, run_hook, rel):
    _, out, _ = run_hook(HOOK, src_repo, {"tool_name": "Write", "tool_input": {"file_path": str(src_repo / rel)}}, "claude")
    assert tool_decision("claude", out) == "allow"


@pytest.mark.parametrize("rel", ["src/app.py", "lib/x.py", "packages/a/b.py"])
def test_gated_files_are_still_gated_by_absolute_path(src_repo, run_hook, rel):
    _, out, _ = run_hook(HOOK, src_repo, {"tool_name": "Write", "tool_input": {"file_path": str(src_repo / rel)}}, "claude")
    assert tool_decision("claude", out) == "deny"


def test_paths_outside_the_workspace_are_not_gated(src_repo, run_hook):
    payload = {"tool_name": "Bash", "tool_input": {"command": "cp out.txt /tmp/build/lib/x"}}
    _, out, _ = run_hook(HOOK, src_repo, payload, "claude")
    assert tool_decision("claude", out) == "allow"


def test_relative_gated_paths_still_gated(src_repo, run_hook):
    _, out, _ = run_hook(HOOK, src_repo, {"tool_name": "Write", "tool_input": {"path": "src/app.py"}}, "cursor")
    assert tool_decision("cursor", out) == "deny"


# --- gated_paths_resolve_before_the_prefix_match: The gate resolves a path against the repo before it matches a prefix (#119).
#
# Three paths passed ungated on main: `src/../../proj/src/a.py` (every `../` form was
# read as outside the workspace), `../src/a.py` from a subdirectory workspace, and
# `SRC/a.py` on a filesystem that folds case.

@pytest.fixture
def proj(tmp_path):
    repo = git_repo(tmp_path / "home" / "me" / "proj")
    (repo / "api").mkdir()
    hook_session(repo, resume_slug="plan-create")
    return repo


@pytest.mark.parametrize("path, sub, relative, gated", [
    pytest.param("src/../../proj/src/a.py", "", "src/a.py", True, id="a_dotdot_form_that_names_a_gated_file_is_gated"),
    pytest.param("../other/src/a.py", "", None, False, id="a_dotdot_form_that_leaves_the_repo_is_not_gated"),
    pytest.param("../src/a.py", "api", "src/a.py", True, id="dotdot_from_a_subdirectory_workspace_is_gated"),
])
def test_a_dotdot_form_resolves_before_the_prefix_match(proj, path, sub, relative, gated):
    workspace = proj / sub if sub else proj
    assert skill_gate.workspace_relative(path, workspace) == relative
    assert bool(skill_gate.gated_write(path, workspace)) is gated


def test_plain_relative_paths_still_resolve(proj):
    assert skill_gate.workspace_relative("src/a.py", proj) == "src/a.py"
    assert skill_gate.workspace_relative('"docs/x.md"', proj) == "docs/x.md"


def test_upper_case_prefix_is_gated_only_where_the_filesystem_folds_case(proj, monkeypatch):
    # The real probe decides on this host: macOS folds case by default, Linux does not.
    assert skill_gate.gated_write("SRC/a.py", proj) is skill_gate._folds_case(str(proj))
    monkeypatch.setattr(skill_gate, "_folds_case", lambda directory: True)
    assert skill_gate.gated_write("SRC/a.py", proj)
    assert not skill_gate.gated_write("DOCS/a.md", proj)
    monkeypatch.setattr(skill_gate, "_folds_case", lambda directory: False)
    assert not skill_gate.gated_write("SRC/a.py", proj)


def test_folds_case_probe_matches_the_host_filesystem(tmp_path):
    lower = tmp_path / "abc"
    lower.mkdir()
    assert skill_gate._folds_case(str(lower)) is (Path(str(lower).swapcase()).exists())


def test_hook_denies_the_dotdot_form_before_p9(proj, run_hook):
    payload = {"tool_name": "Write", "tool_input": {"path": "src/../../proj/src/app.py"}}
    _, out, _ = run_hook("guard_skill_gate.py", proj, payload, "cursor")
    assert tool_decision("cursor", out) == "deny"


def test_hook_denies_dotdot_from_a_subdirectory_workspace(proj, run_hook):
    payload = {"tool_name": "Write", "tool_input": {"path": "../src/app.py"}}
    _, out, _ = run_hook("guard_skill_gate.py", proj, payload, "cursor", cwd=proj / "api",
                         project_env={"CURSOR_PROJECT_DIR": proj / "api"})
    assert tool_decision("cursor", out) == "deny"
