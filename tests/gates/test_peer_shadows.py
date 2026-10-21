"""The peer shadow finder and its CLI: a host entry with a peer's name that is not the bundled file."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import code_paths

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import peer_shadows  # noqa: E402
from peer_shadows import find_peer_shadows, host_dirs  # noqa: E402


# --- peer_shadows: The peer shadow finder: a host entry with a peer's name that is not the bundled file.

PEERS = {
    "version": 1,
    "peers": [
        {"slash": "/plan-review", "kind": "command", "path": "commands/plan-review.md"},
        {"slash": "/spec-driven", "kind": "command", "path": "commands/spec-driven.md"},
        {"slash": "/test-benchmark", "kind": "skill", "path": "skills/test-benchmark/SKILL.md"},
    ],
    # No kind key: the kind comes from the path, as in publish_peers.
    "supporting_commands": [{"slash": "/pr-review-update", "path": "commands/pr-review-update.md"}],
}


def make_peer_root(base: Path) -> Path:
    """A toy skill root with command peers, a skill peer, and a supporting command."""
    root = base / "skill"
    (root / "references").mkdir(parents=True)
    (root / "references" / "bundled-peers.json").write_text(json.dumps(PEERS), encoding="utf-8")
    for row in PEERS["peers"] + PEERS["supporting_commands"]:
        path = root / row["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# peer\n", encoding="utf-8")
    return root


@pytest.fixture
def root(tmp_path):
    return make_peer_root(tmp_path)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "commands").mkdir(parents=True)
    (home / ".claude" / "skills").mkdir(parents=True)
    return home


def claude(home: Path) -> list:
    return [(home / ".claude" / "skills", home / ".claude" / "commands")]


def paths(shadows) -> list[Path]:
    return [s.path for s in shadows]


def test_no_entries_is_no_shadow(root, home):
    assert find_peer_shadows(root, claude(home)) == []


def test_missing_host_dirs_is_no_shadow(root, tmp_path):
    assert find_peer_shadows(root, claude(tmp_path / "nohome")) == []


def test_links_to_the_bundled_files_are_no_shadow(root, home):
    (home / ".claude" / "commands" / "plan-review.md").symlink_to(root / "commands" / "plan-review.md")
    (home / ".claude" / "commands" / "pr-review-update.md").symlink_to(root / "commands" / "pr-review-update.md")
    (home / ".claude" / "skills" / "test-benchmark").symlink_to(root / "skills" / "test-benchmark")
    assert find_peer_shadows(root, claude(home)) == []


def test_regular_command_file_is_a_shadow(root, home):
    entry = home / ".claude" / "commands" / "plan-review.md"
    entry.write_text("stale\n", encoding="utf-8")
    shadows = find_peer_shadows(root, claude(home))
    assert [(s.slash, s.path, s.bundled.resolve()) for s in shadows] == [
        ("/plan-review", entry, (root / "commands" / "plan-review.md").resolve())
    ]


def test_supporting_command_without_kind_is_checked_as_a_command(root, home):
    entry = home / ".claude" / "commands" / "pr-review-update.md"
    entry.write_text("stale\n", encoding="utf-8")
    assert paths(find_peer_shadows(root, claude(home))) == [entry]


def test_skill_directory_that_is_not_a_link_is_a_shadow(root, home):
    entry = home / ".claude" / "skills" / "test-benchmark"
    entry.mkdir()
    shadows = find_peer_shadows(root, claude(home))
    assert paths(shadows) == [entry]
    assert shadows[0].bundled.resolve() == (root / "skills" / "test-benchmark").resolve()
    assert shadows[0].kind == "skill"


def test_link_into_another_checkout_is_a_shadow(root, home, tmp_path):
    other = make_peer_root(tmp_path / "other")
    entry = home / ".claude" / "commands" / "plan-review.md"
    entry.symlink_to(other / "commands" / "plan-review.md")
    assert paths(find_peer_shadows(root, claude(home))) == [entry]


def test_entry_of_the_other_kind_is_a_shadow(root, home):
    (home / ".claude" / "commands" / "spec-driven.md").symlink_to(root / "commands" / "spec-driven.md")
    entry = home / ".claude" / "skills" / "spec-driven"
    entry.mkdir()
    assert paths(find_peer_shadows(root, claude(home))) == [entry]


def test_link_of_the_other_kind_to_the_bundled_file_is_a_shadow(root, home):
    # The kind clause: a skill entry that resolves to the bundled command file is still a shadow.
    entry = home / ".claude" / "skills" / "plan-review"
    entry.symlink_to(root / "commands" / "plan-review.md")
    assert paths(find_peer_shadows(root, claude(home))) == [entry]


def test_a_skill_that_is_not_a_peer_is_no_shadow(root, home):
    (home / ".claude" / "skills" / "my-own-skill").mkdir()
    assert find_peer_shadows(root, claude(home)) == []


def test_name_next_to_a_peer_name_is_no_shadow(root, home):
    (home / ".claude" / "commands" / "plan-review-old.md").write_text("x\n", encoding="utf-8")
    (home / ".claude" / "skills" / "test-benchmark-old").mkdir()
    assert find_peer_shadows(root, claude(home)) == []


def test_broken_link_is_a_shadow(root, home, tmp_path):
    entry = home / ".claude" / "commands" / "plan-review.md"
    entry.symlink_to(tmp_path / "gone.md")
    assert paths(find_peer_shadows(root, claude(home))) == [entry]


def test_symlink_loop_is_a_shadow(root, home):
    commands = home / ".claude" / "commands"
    (commands / "a.md").symlink_to(commands / "b.md")
    (commands / "b.md").symlink_to(commands / "a.md")
    entry = commands / "plan-review.md"
    entry.symlink_to(commands / "a.md")
    assert paths(find_peer_shadows(root, claude(home))) == [entry]


def test_resolve_error_counts_as_a_shadow(root, home, resolve_loop):
    # Python 3.9 to 3.12 raise RuntimeError on a symlink loop; 3.13+ do not.
    entry = home / ".claude" / "commands" / "plan-review.md"
    entry.symlink_to(root / "commands" / "plan-review.md")
    resolve_loop(home)
    assert paths(find_peer_shadows(root, claude(home))) == [entry]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads mode 000 entries")
def test_unreadable_host_entries_are_still_shadows(root, home):
    command = home / ".claude" / "commands" / "plan-review.md"
    command.write_text("x\n", encoding="utf-8")
    skill = home / ".claude" / "skills" / "test-benchmark"
    skill.mkdir()
    os.chmod(command, 0)
    os.chmod(skill, 0)
    try:
        assert paths(find_peer_shadows(root, claude(home))) == [command, skill]
    finally:
        os.chmod(command, 0o644)
        os.chmod(skill, 0o755)


def test_shadows_are_sorted_by_slash_then_path(root, home, tmp_path):
    work = tmp_path / "work"
    pairs = claude(home) + [(work / ".claude" / "skills", work / ".claude" / "commands")]
    for skills, commands in reversed(pairs):
        skills.mkdir(parents=True, exist_ok=True)
        commands.mkdir(parents=True, exist_ok=True)
        (skills / "test-benchmark").mkdir()
        (commands / "plan-review.md").write_text("x\n", encoding="utf-8")
    shadows = find_peer_shadows(root, pairs)
    assert [(s.slash, s.path) for s in shadows] == sorted((s.slash, s.path) for s in shadows)
    assert [s.slash for s in shadows] == ["/plan-review", "/plan-review", "/test-benchmark", "/test-benchmark"]


def test_host_dirs_claude(tmp_path):
    home, work = tmp_path / "h", tmp_path / "w"
    assert host_dirs("claude", home=home, workspace=None) == [(home / ".claude" / "skills", home / ".claude" / "commands")]
    assert host_dirs("claude", home=home, workspace=work, commands_dir=tmp_path / "ignored") == [
        (home / ".claude" / "skills", home / ".claude" / "commands"),
        (work / ".claude" / "skills", work / ".claude" / "commands"),
    ]


def _cursor_extra(base: Path) -> list:
    """The skills-only pairs that Cursor also loads (no commands dir)."""
    return [(base / d / "skills", None) for d in (".agents", ".claude", ".codex")]


def test_host_dirs_cursor(tmp_path):
    home, work, cmds = tmp_path / "h", tmp_path / "w", tmp_path / "cmds"
    assert host_dirs("cursor", home=home, workspace=None) == [
        (home / ".cursor" / "skills", home / ".cursor" / "commands"),
        *_cursor_extra(home),
    ]
    assert host_dirs("cursor", home=home, workspace=work, commands_dir=cmds) == [
        (home / ".cursor" / "skills", cmds),
        *_cursor_extra(home),
        (work / ".cursor" / "skills", work / ".cursor" / "commands"),
        *_cursor_extra(work),
    ]


CURSOR_SKILL_DIRS = [
    (base, host) for base in ("home", "work") for host in (".cursor", ".agents", ".claude", ".codex")
]


@pytest.mark.parametrize("base,host", CURSOR_SKILL_DIRS)
def test_cursor_finds_a_skill_shadow_in_each_skills_dir(root, tmp_path, base, host):
    home, work = tmp_path / "home", tmp_path / "work"
    entry = (home if base == "home" else work) / host / "skills" / "test-benchmark"
    entry.mkdir(parents=True)
    shadows = find_peer_shadows(root, host_dirs("cursor", home=home, workspace=work))
    assert paths(shadows) == [entry]


def test_skills_only_pair_checks_no_command(root, tmp_path):
    skills = tmp_path / "skills"
    (skills / "plan-review.md").parent.mkdir(parents=True)
    (skills / "plan-review.md").write_text("x\n", encoding="utf-8")
    assert find_peer_shadows(root, [(skills, None)]) == []


def test_the_same_entry_in_two_pairs_is_listed_once(root, home):
    (home / ".claude" / "skills" / "test-benchmark").mkdir()
    pairs = claude(home) + [(home / ".claude" / "skills", None)]
    assert paths(find_peer_shadows(root, pairs)) == [home / ".claude" / "skills" / "test-benchmark"]


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_host_dirs_dedupes_when_workspace_is_home(tmp_path, host):
    home = tmp_path / "h"
    home.mkdir()
    assert host_dirs(host, home=home, workspace=home) == host_dirs(host, home=home)


def test_host_dirs_dedupe_survives_symlink_loop(tmp_path, resolve_loop):
    home, work = tmp_path / "h", tmp_path / "w"
    resolve_loop(home)
    assert len(host_dirs("claude", home=home, workspace=work)) == 2


def test_format_lines_name_entry_and_bundled_path(root, home):
    (home / ".claude" / "commands" / "plan-review.md").write_text("x\n", encoding="utf-8")
    (home / ".claude" / "skills" / "test-benchmark").mkdir()
    lines = peer_shadows.shadow_lines(find_peer_shadows(root, claude(home)))
    assert lines[0].startswith("peer shadow: /plan-review resolves to ")
    assert lines[0].endswith("/commands/plan-review.md")
    assert lines[1].endswith("/skills/test-benchmark/")


def test_fix_lines_name_the_full_install_path(root):
    fix = peer_shadows.fix_lines(root)
    assert fix == [
        "Remove or rename each listed user entry.",
        f"On an install.sh install, then run {root.resolve() / 'install.sh'}.",
        "On a plugin install, do not run install.sh.",
        "Start a new host session before you run --resume.",
    ]


def test_host_dirs_rejects_an_unknown_host(tmp_path):
    with pytest.raises(ValueError, match="unknown host"):
        host_dirs("other", home=tmp_path)


def test_host_dirs_dedupes_when_workspace_links_to_home(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    (tmp_path / "w").symlink_to(home)
    assert host_dirs("claude", home=home, workspace=tmp_path / "w") == [
        (home / ".claude" / "skills", home / ".claude" / "commands")
    ]


# --- peer_shadows_cli: scripts/peer_shadows.py CLI: the install.sh warning.

CLI = ROOT / "scripts" / "peer_shadows.py"


def _field_python():
    """A Python 3.9 to 3.12 interpreter, like the system python3 that install.sh and A2 run under."""
    for name in ("python3.9", "python3.10", "python3.11", "python3.12", "/usr/bin/python3"):
        path = shutil.which(name)
        if not path:
            continue
        out = subprocess.run([path, "-c", "import sys; print(sys.version_info < (3, 13))"],
                             capture_output=True, text=True)
        if out.stdout.strip() == "True":
            return path
    return None


def _run(python: str, home: Path, root: Path = ROOT, **env) -> subprocess.CompletedProcess:
    return subprocess.run([python, str(CLI), "--root", str(root)], capture_output=True, text=True,
                          env={**os.environ, "HOME": str(home), **env})


def _home_with_shadows(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    claude = home / ".claude" / "commands"
    claude.mkdir(parents=True)
    (claude / "plan-review.md").write_text("user copy\n", encoding="utf-8")
    (claude / "a.md").symlink_to(claude / "b.md")
    (claude / "b.md").symlink_to(claude / "a.md")
    (claude / "plan-update.md").symlink_to(claude / "a.md")
    return home


def test_cli_warns_and_exits_zero(tmp_path):
    proc = _run(sys.executable, _home_with_shadows(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("WARNING: peer shadows")
    assert len([line for line in proc.stdout.splitlines() if "peer shadow:" in line]) == 2
    assert "Start a new host session before you run --resume." in proc.stdout


def test_cli_reads_the_cursor_commands_override(tmp_path):
    home = tmp_path / "home"
    custom = tmp_path / "cmds"
    custom.mkdir()
    (custom / "plan-review.md").write_text("user copy\n", encoding="utf-8")
    proc = _run(sys.executable, home, CADENCE_COMMANDS_DIR=str(custom))
    assert proc.returncode == 0, proc.stderr
    assert f"resolves to {custom / 'plan-review.md'}," in proc.stdout


def test_cli_lists_a_skill_dir_of_both_hosts_once(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "skills" / "test-benchmark").mkdir(parents=True)
    (home / ".codex" / "skills" / "test-benchmark").mkdir(parents=True)
    proc = _run(sys.executable, home)
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if "peer shadow:" in line]
    assert len(lines) == 2
    assert any(f"{home / '.claude' / 'skills' / 'test-benchmark'}," in line for line in lines)
    assert any(f"{home / '.codex' / 'skills' / 'test-benchmark'}," in line for line in lines)


def test_cli_is_silent_without_shadows(tmp_path):
    proc = _run(sys.executable, tmp_path / "home")
    assert (proc.returncode, proc.stdout) == (0, "")


def test_cli_fails_on_a_broken_registry(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "references" / "bundled-peers.json").write_text("{not json", encoding="utf-8")
    proc = _run(sys.executable, tmp_path / "home", root=root)
    assert proc.returncode != 0


def test_cli_runs_under_the_field_python(tmp_path):
    python = _field_python()
    if python is None:
        pytest.skip("no Python 3.9 to 3.12 interpreter")
    proc = _run(python, _home_with_shadows(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert len([line for line in proc.stdout.splitlines() if "peer shadow:" in line]) == 2
