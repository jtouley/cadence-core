"""install.sh into a sandbox HOME: working hooks on each host, the peer shadow warning, and the Pi extension."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import clean_env, code_paths


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _install(home: Path, *flags: str, **env: object) -> subprocess.CompletedProcess:
    """bash install.sh <flags> with HOME=`home`, no CADENCE_* variables, and `env` set last."""
    return subprocess.run(["bash", str(ROOT / "install.sh"), *flags], cwd=ROOT,
                          env=clean_env(None, scrub=("CADENCE",), HOME=home, **env), capture_output=True, text=True)


# --- install_registers_working_hooks_for_each_host: install.sh into a sandbox HOME: every registered hook points at a real script, on both hosts.


def _assert_hook_scripts_exist(config: Path, home: Path) -> None:
    """The host hook file `config` names at least one hook script, and each one it names exists."""
    found = re.findall(r"\$\{CADENCE_SKILL_ROOT:-([^}]*)\}(/hooks/[\w.-]+\.py)", config.read_text())
    assert found
    for script in (Path(d.replace("$HOME", str(home)) + s) for d, s in found):
        assert script.is_file(), script


def test_first_install_without_skills_dirs_links_the_skill(tmp_path):
    # Regression: ~/.cursor/skills missing at first install left hooks pointing at nothing.
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert (home / ".cursor" / "skills" / "cadence" / "SKILL.md").is_file()
    _assert_hook_scripts_exist(home / ".cursor" / "hooks.json", home)


def test_claude_code_gets_hooks_in_its_own_schema(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "model": "opus",
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]},
    }))
    assert _install(home).returncode == 0
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert settings["model"] == "opus"
    pre = settings["hooks"]["PreToolUse"]
    assert {"type": "command", "command": "echo mine"} in pre[0]["hooks"]
    commands = [h["command"] for g in pre for h in g["hooks"]]
    assert any("guard_skill_gate.py" in c and "--host claude" in c for c in commands)
    assert any("pipeline_stop_guard.py" in h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"])
    _assert_hook_scripts_exist(home / ".claude" / "settings.json", home)


def test_reinstall_is_idempotent(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".cursor").mkdir(parents=True)
    assert _install(home).returncode == 0
    first = {p: json.loads((home / p).read_text()) for p in (".claude/settings.json", ".cursor/hooks.json")}
    assert _install(home).returncode == 0
    second = {p: json.loads((home / p).read_text()) for p in (".claude/settings.json", ".cursor/hooks.json")}
    assert first == second


@pytest.mark.parametrize("host", [".claude", ".pi"])
def test_a_home_without_the_host_dir_gets_nothing_for_that_host(tmp_path, host):
    home = tmp_path / "home"
    home.mkdir()
    assert _install(home).returncode == 0
    assert not (home / host).exists()


def test_install_fails_loudly_when_a_hook_target_is_missing(tmp_path):
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    proc = _install(home, CADENCE_SKILL_ROOT=tmp_path / "nowhere")
    assert proc.returncode != 0
    assert "hook targets missing" in proc.stderr


def test_install_writes_nothing_when_claude_targets_would_be_missing(tmp_path):
    # Regression: a custom settings path whose ~/.claude does not exist
    # got hooks pointing at ~/.claude/skills/cadence, which was never linked.
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / "alt").mkdir()
    proc = _install(home, CADENCE_CLAUDE_SETTINGS_PATH=home / "alt" / "settings.json")
    assert proc.returncode != 0
    assert "hook targets missing" in proc.stderr
    assert not (home / "alt" / "settings.json").exists()
    assert not list((home / "alt").glob("settings.json.bak.*"))


def test_install_writes_no_cursor_hooks_when_its_targets_would_be_missing(tmp_path):
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    proc = _install(home, CADENCE_SKILL_ROOT=tmp_path / "nowhere")
    assert proc.returncode != 0
    assert not (home / ".cursor" / "hooks.json").exists()


def _host_home(tmp_path: Path, *hosts: str) -> Path:
    home = tmp_path / "home"
    for host in hosts:
        (home / host).mkdir(parents=True)
    return home


def test_installed_cadence_skill_declares_its_name_on_each_host(tmp_path):
    home = _host_home(tmp_path, ".cursor", ".claude")
    assert _install(home).returncode == 0
    for host in (".cursor", ".claude"):
        skill = home / host / "skills" / "cadence" / "SKILL.md"
        assert re.search(r"^name:\s*cadence\s*$", skill.read_text(), re.M), skill


@pytest.mark.parametrize("hosts", [
    pytest.param((".cursor", ".claude"), id="clean_install_prints_the_restart_notice"),
    pytest.param((".cursor",), id="clean_install_without_claude_dir_still_passes"),
])
def test_clean_install_passes_and_prints_the_restart_notice(tmp_path, hosts):
    proc = _install(_host_home(tmp_path, *hosts))
    assert proc.returncode == 0, proc.stderr
    assert "Restart" in proc.stdout


@pytest.mark.parametrize("target", [
    pytest.param(lambda commands: ROOT / "commands" / "missing.md", id="dangling_link_into_the_checkout_fails_the_install"),
    pytest.param(lambda commands: os.path.relpath(ROOT / "commands" / "missing.md", commands),
                 id="relative_dangling_link_into_the_checkout_fails_the_install"),
])
def test_a_dangling_link_into_the_checkout_fails_the_install(tmp_path, target):
    home = _host_home(tmp_path, ".claude")
    commands = home / ".claude" / "commands"
    commands.mkdir()
    ghost = commands / "ghost.md"
    ghost.symlink_to(target(commands))
    proc = _install(home)
    assert proc.returncode != 0
    assert str(ghost) in proc.stderr


@pytest.mark.parametrize("name, target", [
    pytest.param("theirs.md", lambda tmp: tmp / "elsewhere" / "theirs.md",
                 id="dangling_link_outside_the_checkout_is_not_ours"),
    pytest.param("old.md", lambda tmp: Path(str(ROOT) + "-old") / "commands" / "old.md",
                 id="dangling_link_into_a_sibling_dir_with_the_same_prefix_is_not_ours"),
])
def test_a_dangling_link_that_is_not_ours_passes_the_install(tmp_path, name, target):
    home = _host_home(tmp_path, ".claude")
    commands = home / ".claude" / "commands"
    commands.mkdir()
    (commands / name).symlink_to(target(tmp_path))
    assert _install(home).returncode == 0


def test_dangling_link_with_dotdot_into_the_checkout_fails_the_install(tmp_path):
    home = _host_home(tmp_path, ".claude")
    commands = home / ".claude" / "commands"
    commands.mkdir()
    ghost = commands / "ghost.md"
    stored = ROOT.parent / "not-the-checkout" / ".." / ROOT.name / "commands" / "missing.md"
    assert os.path.normpath(stored) == os.path.normpath(ROOT / "commands" / "missing.md")
    assert not str(stored).startswith(str(ROOT) + os.sep)
    ghost.symlink_to(stored)
    proc = _install(home)
    assert proc.returncode != 0
    assert str(ghost) in proc.stderr


def test_cursor_reinstall_drops_a_stale_cadence_entry_and_keeps_user_hooks(tmp_path):
    """A cadence hook whose command text changed (old flags) must not survive as a duplicate."""
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    stale = "python3 ${CADENCE_SKILL_ROOT:-$HOME/.cursor/skills/cadence}/hooks/pipeline_stop_guard.py"
    (home / ".cursor" / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {"subagentStop": [
            {"command": stale, "failClosed": True},
            {"command": "python3 /opt/mine/hook.py"},
        ]},
    }))
    assert _install(home).returncode == 0
    entries = json.loads((home / ".cursor" / "hooks.json").read_text())["hooks"]["subagentStop"]
    commands = [e["command"] for e in entries]
    assert "python3 /opt/mine/hook.py" in commands
    assert stale not in commands
    assert sum("pipeline_stop_guard.py" in c for c in commands) == 1


def test_claude_settings_get_one_session_start_hook_after_reinstall(tmp_path):
    # The SessionStart hook that exports CADENCE_HOME, written once.
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    assert _install(home).returncode == 0
    assert _install(home).returncode == 0
    groups = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]["SessionStart"]
    commands = [h["command"] for g in groups for h in g["hooks"]]
    assert sum("hooks/session_start.py" in c for c in commands) == 1
    _assert_hook_scripts_exist(home / ".claude" / "settings.json", home)


def test_reinstall_keeps_a_user_hook_under_a_dir_that_ends_in_cadence(tmp_path):
    # The merge dropped any */cadence/hooks/*.py, including my-cadence/hooks/.
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".cursor").mkdir(parents=True)
    mine = "python3 /opt/my-cadence/hooks/stop.py"
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": mine}]}]}}))
    (home / ".cursor" / "hooks.json").write_text(json.dumps(
        {"version": 1, "hooks": {"stop": [{"command": mine}]}}))
    assert _install(home).returncode == 0
    claude = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]["Stop"]
    cursor = json.loads((home / ".cursor" / "hooks.json").read_text())["hooks"]["stop"]
    assert mine in [h["command"] for g in claude for h in g["hooks"]]
    assert mine in [e["command"] for e in cursor]


def test_remove_claude_drops_the_link_and_cadence_hooks_and_keeps_the_rest(tmp_path):
    # A user who moves to the plugin needs a way off the install.sh path.
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".cursor").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
    }))
    assert _install(home).returncode == 0
    cursor_before = (home / ".cursor" / "hooks.json").read_text()
    proc = _install(home, "--remove-claude")
    assert proc.returncode == 0, proc.stderr
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert settings["model"] == "opus"
    commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert commands == ["echo mine"]
    assert not (home / ".claude" / "skills" / "cadence").exists()
    assert (home / ".cursor" / "hooks.json").read_text() == cursor_before
    assert (home / ".cursor" / "skills" / "cadence").is_symlink()
    for sub in ("skills", "commands"):
        for link in (home / ".claude" / sub).iterdir():
            assert not (link.is_symlink() and str(link.resolve()).startswith(str(ROOT.resolve()))), link


def _remove_claude(home):
    return _install(home, "--remove-claude")


def test_remove_claude_keeps_an_event_value_that_is_not_a_list(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Custom": {"x": 1}}}))
    assert _remove_claude(home).returncode == 0
    assert json.loads((home / ".claude" / "settings.json").read_text()) == {"hooks": {"Custom": {"x": 1}}}


def test_remove_claude_with_nothing_to_remove_writes_nothing(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    original = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}})
    (home / ".claude" / "settings.json").write_text(original)
    assert _remove_claude(home).returncode == 0
    assert (home / ".claude" / "settings.json").read_text() == original
    assert list((home / ".claude").glob("settings.json.bak.*")) == []


def test_remove_claude_keeps_a_group_whose_hooks_is_not_a_list(tmp_path):
    # The group loop read a dict's keys or a string's letters as hook entries.
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    ours = {"type": "command", "command": "python3 /x/cadence/hooks/guard_skill_gate.py --host claude"}
    odd = [{"matcher": "a", "hooks": {"a": 1}}, {"matcher": "b", "hooks": "x"}]
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [*odd, {"matcher": "*", "hooks": [ours, {"type": "command", "command": "echo mine"}]}]},
    }))
    proc = _remove_claude(home)
    assert proc.returncode == 0, proc.stderr
    groups = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]["PreToolUse"]
    assert groups[:2] == odd
    assert groups[2]["hooks"] == [{"type": "command", "command": "echo mine"}]


def test_two_installs_give_one_post_tool_use_join_group(tmp_path):
    """The PostToolUse Agent|Task join hook is merged once, and a reinstall keeps one group."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    for _ in range(2):
        proc = _install(home)
        assert proc.returncode == 0, proc.stderr
    post = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]["PostToolUse"]
    joins = [g for g in post if any("guard_subagent_handoff.py" in h["command"] for h in g["hooks"])]
    assert len(joins) == 1 and joins[0]["matcher"] == "Agent|Task"
    assert all("--host claude" in h["command"] for h in joins[0]["hooks"])


# --- install_warns_on_peer_shadows: install.sh warns about each peer shadow, keeps the user's entries, and exits 0.

NEW_SESSION = "Start a new host session before you run --resume."


def _sandbox(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    for host in (".claude", ".cursor"):
        (home / host / "skills").mkdir(parents=True)
        (home / host / "commands").mkdir(parents=True)
    return home


def _shadow_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if "peer shadow:" in line]


def test_install_warns_on_user_commands_in_both_trees(tmp_path):
    home = _sandbox(tmp_path)
    for host in (".claude", ".cursor"):
        (home / host / "commands" / "plan-review.md").write_text("user copy\n", encoding="utf-8")
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    lines = _shadow_lines(proc.stdout)
    assert len(lines) == 2
    for host in (".claude", ".cursor"):
        entry = home / host / "commands" / "plan-review.md"
        assert any(f"resolves to {entry}," in line for line in lines)
        assert entry.read_text(encoding="utf-8") == "user copy\n"
    assert NEW_SESSION in proc.stdout


def test_install_keeps_foreign_peer_link_and_warns(tmp_path):
    home = _sandbox(tmp_path)
    other = tmp_path / "other-checkout" / "commands" / "plan-review.md"
    other.parent.mkdir(parents=True)
    other.write_text("stale\n", encoding="utf-8")
    entry = home / ".claude" / "commands" / "plan-review.md"
    entry.symlink_to(other)
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert os.readlink(entry) == str(other)
    assert any(f"resolves to {entry}," in line for line in _shadow_lines(proc.stdout))


def test_clean_install_prints_no_peer_shadow(tmp_path):
    proc = _install(_sandbox(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert _shadow_lines(proc.stdout) == []


# --- install_pi_extension: install.sh links the Pi extension and, when Pi does not list one, the skill.

EXT = Path(".pi/agent/extensions/cadence-gates.ts")
SKILL = Path(".pi/agent/skills/cadence")


def _pi_home(tmp_path: Path, settings: object | None = None) -> Path:
    home = tmp_path / "home"
    (home / ".pi" / "agent").mkdir(parents=True)
    if settings is not None:
        text = settings if isinstance(settings, str) else json.dumps(settings)
        (home / ".pi" / "agent" / "settings.json").write_text(text, encoding="utf-8")
    return home


def _tree(home: Path) -> dict[str, str]:
    return {str(p.relative_to(home)): os.readlink(p) if p.is_symlink() else "file"
            for p in sorted((home / ".pi").rglob("*"))}


def test_pi_agent_home_gets_the_extension_link(tmp_path):
    home = _pi_home(tmp_path)
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert (home / EXT).is_symlink()
    assert (home / EXT).resolve() == (ROOT / "pi" / "cadence-gates.ts").resolve()


def test_second_install_changes_nothing_for_pi(tmp_path):
    home = _pi_home(tmp_path)
    assert _install(home).returncode == 0
    before = _tree(home)
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert f"ok (already linked): {home / EXT}" in proc.stdout
    assert _tree(home) == before
    assert not list((home / ".pi").rglob("*.bak.*"))


@pytest.mark.parametrize("kind", ["file", "other-link", "dangling-link"])
def test_a_foreign_file_at_the_extension_path_is_backed_up(tmp_path, kind):
    home = _pi_home(tmp_path)
    path = home / EXT
    path.parent.mkdir(parents=True)
    if kind == "file":
        path.write_text("mine", encoding="utf-8")
    else:
        other = tmp_path / ("other.ts" if kind == "other-link" else "missing.ts")
        if kind == "other-link":
            other.write_text("other", encoding="utf-8")
        path.symlink_to(other)
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    backups = list(path.parent.glob("cadence-gates.ts.bak.*"))
    assert len(backups) == 1
    if kind == "file":
        assert backups[0].read_text(encoding="utf-8") == "mine"
    assert path.resolve() == (ROOT / "pi" / "cadence-gates.ts").resolve()
    assert "backed up" in proc.stdout


def test_pi_settings_with_a_cadence_skill_get_no_second_skill_link(tmp_path):
    home = _pi_home(tmp_path, {"skills": ["~/.cursor/skills"]})
    (home / ".cursor").mkdir()
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert (home / ".cursor" / "skills" / "cadence" / "SKILL.md").is_file()
    assert not (home / SKILL).exists()
    assert "Pi settings already list a cadence skill" in proc.stdout


@pytest.mark.parametrize("settings", [None, "not json", {"skills": "x"}])
def test_pi_without_a_listed_cadence_skill_gets_the_skill_link(tmp_path, settings):
    home = _pi_home(tmp_path, settings)
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert (home / SKILL).is_symlink()
    assert (home / SKILL).resolve() == ROOT.resolve()


def test_a_listed_cadence_skill_from_another_checkout_is_named(tmp_path):
    other = tmp_path / "other-checkout"
    (other / "cadence").mkdir(parents=True)
    (other / "cadence" / "SKILL.md").write_text("---\nname: cadence\n---\n", encoding="utf-8")
    home = _pi_home(tmp_path, {"skills": [str(other)]})
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert f"warning: Pi settings list a cadence skill from another checkout: {other / 'cadence'}" in proc.stdout


def test_the_uninstall_text_names_the_pi_links(tmp_path):
    proc = _install(_pi_home(tmp_path))
    assert "~/.pi/agent/extensions/cadence-gates.ts" in proc.stdout
    assert "~/.pi/agent/skills/cadence" in proc.stdout


def test_install_reports_a_dangling_pi_link_into_this_checkout(tmp_path):
    home = _pi_home(tmp_path)
    skills = home / ".pi" / "agent" / "skills"
    skills.mkdir(parents=True)
    (skills / "gone").symlink_to(ROOT / "no-such-dir")
    proc = _install(home)
    assert proc.returncode != 0
    assert f"dangling link: {skills / 'gone'}" in proc.stderr


def test_pi_settings_that_list_the_cadence_folder_itself_get_no_second_link(tmp_path):
    home = _pi_home(tmp_path)
    (home / ".pi" / "agent" / "settings.json").write_text(json.dumps({"skills": [str(ROOT)]}), encoding="utf-8")
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert not (home / SKILL).exists()


@pytest.mark.parametrize("entry", ["skill-md", "not-text"])
def test_pi_settings_entry_forms(tmp_path, entry):
    value = str(ROOT / "SKILL.md") if entry == "skill-md" else 7
    home = _pi_home(tmp_path, {"skills": [value]})
    proc = _install(home)
    assert proc.returncode == 0, proc.stderr
    assert (home / SKILL).exists() is (entry == "not-text")


def test_the_last_line_says_the_extension_runs_the_gates(tmp_path):
    proc = _install(_pi_home(tmp_path))
    assert "Pi: the extension at ~/.pi/agent/extensions/cadence-gates.ts runs the gates." in proc.stdout


def test_tools_fills_the_uv_cache_with_each_pinned_tool_and_installs_nothing(tmp_path):
    """`uv tool install` replaced the user's own ruff and mypy. A cache warm does not."""
    code_paths()
    from tool_measures import TOOLS, warm_command

    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path / "uv.log"}\n', encoding="utf-8")
    fake.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    proc = _install(home, "--tools", PATH=f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    assert proc.returncode == 0, proc.stderr
    calls = (tmp_path / "uv.log").read_text().splitlines()
    assert calls == [f"tool run {t}=={TOOLS[t]} --version" for t in TOOLS] == [warm_command(t)[3:] for t in TOOLS]
    assert list(home.iterdir()) == []
    proc = _install(home)
    assert "./install.sh --tools" in proc.stdout and all(warm_command(t) in proc.stdout for t in TOOLS)
    assert "uv tool install" not in proc.stdout
