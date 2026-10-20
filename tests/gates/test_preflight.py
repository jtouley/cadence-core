"""preflight.run: git fail paths, the ok path, and peer shadows (cd-53), via a monkeypatched subprocess.

The three checks of stages.json (gh-181) run in a sandbox repo: `gh api user`, the branch, a clean tree.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from conftest import fake_gh, feature_repo, git, write_files

ROOT = Path(__file__).resolve().parents[2]
SHADOW = re.compile(r"peer shadow: (/\S+) resolves to (.+), not (.+)$")


def _fake_git(repo: Path, primary: Optional[Path] = None):
    """A subprocess.run that answers the git calls of A2 and session_store by their args."""

    def fake_run(args, *_a, **_k):
        if list(args[:3]) == ["git", "rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{repo}\n", stderr="")
        if list(args[:4]) == ["git", "worktree", "list", "--porcelain"]:
            lines = f"worktree {primary or repo}\n"
            if primary is not None:
                lines += f"worktree {repo}\n"
            return SimpleNamespace(returncode=0, stdout=lines, stderr="")
        # gh-181: gh is logged in, HEAD is on a feature branch, there is no origin/HEAD, the tree is clean.
        if list(args) in (["gh", "api", "user", "--silent"], ["git", "status", "--porcelain", "-z", "--untracked-files=all"]):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if list(args) == ["git", "symbolic-ref", "--short", "-q", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="feature/gh-1-x\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected git call")

    return fake_run


def _shadows(err: str) -> list[tuple[str, Path, str]]:
    """(slash, entry, bundled) of each peer shadow line; the entry is resolved."""
    found = []
    for line in err.splitlines():
        match = SHADOW.search(line)
        if match:
            found.append((match.group(1), Path(match.group(2)).resolve(), match.group(3)))
    return found


def _plant_both_trees(home: Path) -> tuple[Path, Path]:
    """A regular /pr-review-local command file in the Claude tree and in the Cursor tree."""
    planted = []
    for host in (".claude", ".cursor"):
        commands = home / host / "commands"
        commands.mkdir(parents=True, exist_ok=True)
        entry = commands / "pr-review-local.md"
        entry.write_text("user copy\n", encoding="utf-8")
        planted.append(entry)
    return planted[0], planted[1]


@pytest.fixture
def a2(scripts_path, tmp_path, monkeypatch):
    """(preflight module, repo, context). git answers for tmp_path/repo."""
    import preflight

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(preflight.subprocess, "run", _fake_git(repo))
    context = tmp_path / ".context"
    context.mkdir()
    return preflight, repo, context


def test_preflight_git_nonzero_fails(scripts_path, tmp_path, monkeypatch, capsys):
    import preflight

    def fake_run(*_a, **_k):
        return SimpleNamespace(returncode=1, stdout="", stderr="not a git repo")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    context = tmp_path / ".context"
    context.mkdir()
    code = preflight.run(context)
    err = capsys.readouterr().err
    assert code == 1
    assert not (context / "gates" / "preflight.json").exists()
    assert "preflight: not inside a git repository — not a git repo" in err


def test_preflight_git_missing_fails(scripts_path, tmp_path, monkeypatch):
    import preflight

    def fake_run(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    context = tmp_path / ".context"
    context.mkdir()
    code = preflight.run(context)
    assert code == 1
    assert not (context / "gates" / "preflight.json").exists()
    _, err = preflight._git_toplevel()
    assert err == "git not found on PATH"


def test_preflight_git_ok_passes(a2):
    preflight, repo, context = a2
    code = preflight.run(context)
    breadcrumb = context / "gates" / "preflight.json"
    assert code == 0
    assert breadcrumb.is_file()
    assert str(repo) in breadcrumb.read_text(encoding="utf-8")
    assert not (repo / ".context").exists()


def test_a2_lookup_creates_no_context_dir(a2):
    preflight, repo, context = a2
    assert preflight.run(context, check=True) == 0
    assert not (repo / ".context").exists()
    assert not (context / "gates" / "preflight.json").exists()
    assert preflight.run(context) == 0
    assert not (repo / ".context").exists()


def test_a2_passes_with_links_to_the_bundled_peers(a2, monkeypatch):
    preflight, _repo, context = a2
    monkeypatch.setenv("CLAUDECODE", "1")
    commands = Path.home() / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "pr-review-local.md").symlink_to(ROOT / "commands" / "pr-review-local.md")
    (Path.home() / ".claude" / "skills").mkdir()
    (Path.home() / ".claude" / "skills" / "vibe-test").symlink_to(ROOT / "skills" / "vibe-test")
    assert preflight.run(context) == 0


@pytest.mark.parametrize("check", [False, True])
def test_a2_fails_on_a_user_command_and_names_both_paths(a2, monkeypatch, capsys, check):
    preflight, _repo, context = a2
    monkeypatch.setenv("CLAUDECODE", "1")
    entry, _ = _plant_both_trees(Path.home())
    assert preflight.run(context, check=check) == 1
    assert _shadows(capsys.readouterr().err) == [
        ("/pr-review-local", entry.resolve(), str((ROOT / "commands" / "pr-review-local.md").resolve()))
    ]
    assert not (context / "gates" / "preflight.json").exists()


def test_a2_install_fix_text_names_the_install_path_and_the_new_session(a2, monkeypatch, capsys):
    preflight, _repo, context = a2
    monkeypatch.setenv("CLAUDECODE", "1")
    _plant_both_trees(Path.home())
    preflight.run(context)
    err = capsys.readouterr().err
    install = f"On an install.sh install, then run {ROOT.resolve() / 'install.sh'}."
    session = "Start a new host session before you run --resume."
    assert install in err
    assert "On a plugin install, do not run install.sh." in err
    assert err.index(install) < err.index(session)


def test_skill_shadow_names_bundled_directory(a2, monkeypatch, capsys):
    preflight, _repo, context = a2
    monkeypatch.setenv("CLAUDECODE", "1")
    (Path.home() / ".claude" / "skills" / "test-benchmark").mkdir(parents=True)
    assert preflight.run(context) == 1
    [(slash, _entry, bundled)] = _shadows(capsys.readouterr().err)
    assert slash == "/test-benchmark"
    assert bundled.endswith("/")
    assert Path(bundled).resolve() == (ROOT / "skills" / "test-benchmark").resolve()


def test_claude_host_checks_only_the_claude_tree(a2, monkeypatch, capsys):
    preflight, _repo, context = a2
    monkeypatch.setenv("CLAUDECODE", "1")
    claude, _ = _plant_both_trees(Path.home())
    preflight.run(context)
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [claude.resolve()]


def test_other_host_checks_only_the_cursor_tree(a2, capsys):
    preflight, _repo, context = a2
    _, cursor = _plant_both_trees(Path.home())
    preflight.run(context)
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [cursor.resolve()]


def test_cursor_host_fails_on_a_compatibility_skills_dir(a2, monkeypatch, capsys):
    """Cursor also loads ~/.claude/skills and ~/.agents/skills; a copy there hides the peer (U1)."""
    preflight, _repo, context = a2
    monkeypatch.delenv("CLAUDECODE", raising=False)
    planted = []
    for host in (".claude", ".agents"):
        entry = Path.home() / host / "skills" / "de-adversarial-reviewer"
        entry.mkdir(parents=True)
        (entry / "SKILL.md").write_text("user copy\n", encoding="utf-8")
        planted.append(entry.resolve())
    assert preflight.run(context, check=True) == 1
    found = _shadows(capsys.readouterr().err)
    assert sorted(e for _s, e, _b in found) == sorted(planted)
    assert {s for s, _e, _b in found} == {"/de-adversarial-reviewer"}


def test_cursor_commands_dir_override_is_read(a2, monkeypatch, tmp_path, capsys):
    preflight, _repo, context = a2
    custom = tmp_path / "cmds"
    custom.mkdir()
    (custom / "plan-review.md").write_text("user copy\n", encoding="utf-8")
    monkeypatch.setenv("CADENCE_COMMANDS_DIR", str(custom))
    assert preflight.run(context) == 1
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [(custom / "plan-review.md").resolve()]


def test_workspace_entry_is_a_shadow(a2, monkeypatch, capsys):
    preflight, repo, context = a2
    monkeypatch.setenv("CLAUDECODE", "1")
    entry = repo / ".claude" / "commands" / "plan-review.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("project copy\n", encoding="utf-8")
    assert preflight.run(context) == 1
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [entry.resolve()]


def test_session_host_claude_selects_claude_tree(a2, capsys):
    preflight, repo, context = a2
    preflight.session_store.save_session({"active_skill": "cadence", "host": "claude"}, cwd=repo)
    claude, _ = _plant_both_trees(Path.home())
    assert preflight.run(context) == 1
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [claude.resolve()]


def test_no_session_store_falls_back_to_cursor(a2, monkeypatch, capsys):
    preflight, _repo, context = a2
    monkeypatch.setattr(preflight, "session_store", None)
    _, cursor = _plant_both_trees(Path.home())
    assert preflight.run(context) == 1
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [cursor.resolve()]


def test_session_read_error_falls_back_to_cursor(a2, monkeypatch, capsys):
    preflight, _repo, context = a2

    def _raise_oserror(*_a, **_k):
        raise OSError("unreadable")

    monkeypatch.setattr(preflight.session_store, "load_worktree_session", _raise_oserror, raising=True)
    _, cursor = _plant_both_trees(Path.home())
    assert preflight.run(context) == 1
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [cursor.resolve()]


def test_session_in_other_worktree_is_not_read(a2, monkeypatch, tmp_path, capsys):
    preflight, repo, context = a2
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(preflight.subprocess, "run", _fake_git(other, primary=repo))
    preflight.session_store.save_session({"active_skill": "cadence", "host": "claude"}, cwd=other)
    monkeypatch.setattr(preflight.subprocess, "run", _fake_git(repo))
    # The root redirect in the shared .context points at the other worktree's claude run.
    assert (repo / ".context" / "cadence-session.json").exists()
    assert preflight.session_store.load_worktree_session(repo) is None
    _, cursor = _plant_both_trees(Path.home())
    assert preflight.run(context) == 1
    assert [e for _s, e, _b in _shadows(capsys.readouterr().err)] == [cursor.resolve()]


def test_broken_registry_fails_a2_without_a_traceback(a2, monkeypatch, capsys):
    preflight, _repo, context = a2

    def _broken(*_a, **_k):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(preflight, "find_peer_shadows", _broken)
    assert preflight.run(context) == 1
    registry = ROOT.resolve() / "references" / "bundled-peers.json"
    assert capsys.readouterr().err.splitlines() == [
        "FAIL  preflight",
        f"  - preflight: cannot read the peer registry {registry}: Expecting value: line 1 column 1 (char 0)",
    ]


@pytest.mark.parametrize("registry", ['{"peers": [{"slash": "/x", "path": ""}]}', "{not json", '{"peers": 1}'])
def test_bad_registry_rows_fail_a2_without_a_traceback(a2, monkeypatch, tmp_path, capsys, registry):
    preflight, _repo, context = a2
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "references" / "bundled-peers.json").write_text(registry, encoding="utf-8")
    monkeypatch.setattr(preflight, "_SCRIPTS", root / "scripts")
    assert preflight.run(context) == 1
    err = capsys.readouterr().err
    assert "cannot read the peer registry" in err
    assert "Traceback" not in err


# --- gh-181: the environment, the branch and a clean state, in a sandbox repo ---------------------------


@pytest.fixture
def sandbox(scripts_path, tmp_path, monkeypatch):
    """(preflight, run) in a repo on a feature branch off `main`, with a `gh` on PATH that exits `gh_exit`.

    run(gh_exit=0, adapter="github-issues") -> exit code. The adapter is the user config layer
    (under the test HOME), so the repo tree stays clean.
    """
    import cadence_config
    import preflight

    repo = feature_repo(tmp_path / "repo", {".gitignore": ".context/\n"})
    monkeypatch.chdir(repo)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_dir))
    context = repo / ".context"

    def run(gh_exit: int | None = 0, adapter: str = "github-issues") -> int:
        (bin_dir / "gh").unlink(missing_ok=True)
        if gh_exit is not None:
            fake_gh(bin_dir, gh_exit)
        config = cadence_config.user_config_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"adapter": adapter}), encoding="utf-8")
        return preflight.run(context, check=True)

    return preflight, repo, run


def test_a2_passes_in_a_clean_repo_on_a_feature_branch(sandbox, capsys):
    _preflight, _repo, run = sandbox
    assert run() == 0, capsys.readouterr().err


@pytest.mark.parametrize("gh_exit", [1, None], ids=["not-logged-in", "no-gh"])
def test_a2_fails_without_gh_auth_on_the_github_issues_adapter(sandbox, capsys, gh_exit):
    _preflight, _repo, run = sandbox
    assert run(gh_exit) == 1
    err = capsys.readouterr().err
    if gh_exit:
        assert "preflight: gh cannot reach GitHub as a signed-in user (not logged in to github.com)" in err
        assert "Run `gh auth login` or set GH_TOKEN, then run preflight again" in err
    else:
        assert "preflight: gh is not on PATH. Install gh (https://cli.github.com), then run preflight again" in err


def test_a2_skips_gh_auth_with_a_note_on_the_none_adapter(sandbox, capsys):
    _preflight, _repo, run = sandbox
    assert run(None, adapter="none") == 0
    assert "gh not checked: the adapter is none" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("layer", "text", "expected"),
    [("user", "{not json", "not valid JSON"), ("project", '{"no_such_key": 1}', "no_such_key is not a Cadence config key")],
)
def test_a2_fails_on_a_bad_config_layer_and_names_the_file(sandbox, capsys, layer, text, expected):
    """A config layer that is not valid fails A2 with the file and the key. It does not fall back to the defaults."""
    import cadence_config

    preflight, repo, run = sandbox
    assert run() == 0, capsys.readouterr().err
    bad = cadence_config.user_config_path() if layer == "user" else repo / ".cadence.json"
    bad.write_text(text, encoding="utf-8")
    assert preflight.run(repo / ".context", check=True) == 1
    err = capsys.readouterr().err
    assert str(bad) in err and expected in err
    assert "cadence_config.py resolve shows each layer" in err


@pytest.mark.parametrize("branch", ["main", "master"])
def test_a2_fails_on_main_or_master_with_no_origin_head(sandbox, capsys, branch):
    _preflight, repo, run = sandbox
    git(repo, "checkout", "-qB", branch)
    assert run() == 1
    err = capsys.readouterr().err
    assert f"the current branch {branch} is the default branch" in err and "git switch -c" in err


def test_a2_reads_the_default_branch_from_origin_head(sandbox, capsys):
    _preflight, repo, run = sandbox
    git(repo, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    git(repo, "checkout", "-qB", "main")
    assert run() == 0, capsys.readouterr().err
    git(repo, "checkout", "-qB", "trunk")
    assert run() == 1
    assert "the current branch trunk is the default branch" in capsys.readouterr().err


def test_a2_fails_on_a_detached_head(sandbox, capsys):
    _preflight, repo, run = sandbox
    git(repo, "checkout", "-q", "--detach")
    assert run() == 1
    assert "HEAD is not on a branch" in capsys.readouterr().err


def test_a2_fails_on_a_dirty_tree_and_lists_each_path(sandbox, capsys):
    _preflight, repo, run = sandbox
    write_files(repo, {"notes/todo.txt": "x\n", ".gitignore": ".context/\nbuild/\n",
                       ".context/plans/p.plan.md": "plan\n", ".cursor/plans/p.plan.md": "plan\n"})
    assert run() == 1
    err = capsys.readouterr().err
    assert "preflight: the tree is not clean outside .context/ and .cursor/plans/: M .gitignore; ?? notes/todo.txt." in err
    assert "Commit or stash them (`git stash -u`), then run preflight again" in err


def test_a2_passes_with_only_cadence_state_in_the_tree(sandbox, capsys):
    _preflight, repo, run = sandbox
    git(repo, "rm", "-q", "--cached", ".gitignore")
    git(repo, "commit", "-qm", "no ignore")
    (repo / ".gitignore").unlink()
    write_files(repo, {".context/cadence-session.json": "{}\n", ".cursor/plans/p.plan.md": "plan\n"})
    assert run() == 0, capsys.readouterr().err


def test_a2_lists_at_most_five_paths(sandbox, capsys):
    _preflight, repo, run = sandbox
    write_files(repo, {f"f{i}.txt": "x\n" for i in range(7)})
    assert run() == 1
    assert "; and 2 more." in capsys.readouterr().err


def test_a2_reports_every_failing_check_at_once(sandbox, capsys):
    _preflight, repo, run = sandbox
    git(repo, "checkout", "-qB", "main")
    write_files(repo, {"x.txt": "x\n"})
    assert run(1) == 1
    err = capsys.readouterr().err
    assert all(t in err for t in ("gh cannot reach GitHub", "is the default branch", "?? x.txt"))
