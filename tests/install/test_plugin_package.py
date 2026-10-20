"""Cadence installs as a Claude Code plugin from this repo root.

The plugin manifest, the marketplace entry and hooks/hooks.json are one package.
install.sh derives the ~/.claude/settings.json hooks from the same hooks.json.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import clean_env

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT_REF = re.compile(r'"\$\{CLAUDE_PLUGIN_ROOT\}(/hooks/[\w.-]+\.py)"')


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_plugin_manifest_loads_the_root_skill():
    manifest = _json(".claude-plugin/plugin.json")
    assert manifest["name"] == "cadence"
    assert manifest["skills"] == ["./"]
    assert manifest["version"] == re.search(
        r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M
    ).group(1)


def test_marketplace_lists_this_repo_as_the_plugin_source():
    market = _json(".claude-plugin/marketplace.json")
    [entry] = market["plugins"]
    assert entry["name"] == "cadence"
    assert entry["source"] == "./"


def test_plugin_hooks_target_scripts_through_the_plugin_root():
    hooks = _json("hooks/hooks.json")["hooks"]
    commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
    assert commands
    for command in commands:
        [script] = PLUGIN_ROOT_REF.findall(command)
        assert (ROOT / script.lstrip("/")).is_file(), command
    assert {"PreToolUse", "Stop", "SubagentStop", "SubagentStart", "SessionStart"} <= set(hooks)
    sub = [h["command"] for g in hooks["SubagentStop"] for h in g["hooks"]]
    main = [h["command"] for g in hooks["Stop"] for h in g["hooks"]]
    assert all("--subagent" in c for c in sub)
    assert all("--subagent" not in c for c in main)


def test_session_start_exports_cadence_home_for_bash(tmp_path):
    env_file = tmp_path / "env.sh"
    env = clean_env(None, scrub=("CADENCE",), CLAUDE_ENV_FILE=env_file)
    proc = subprocess.run(
        ["python3", str(ROOT / "hooks" / "session_start.py")],
        input="{}", env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert env_file.read_text(encoding="utf-8") == f"export CADENCE_HOME={shlex.quote(str(ROOT))}\n"


def test_session_start_without_env_file_writes_nothing(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
    proc = subprocess.run(
        ["python3", str(ROOT / "hooks" / "session_start.py")],
        input="{}", env=env, capture_output=True, text=True, cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
@pytest.mark.parametrize("manifest", ["plugin.json", "marketplace.json"])
def test_claude_plugin_validate_strict_passes(manifest):
    target = ROOT / ".claude-plugin" / manifest
    proc = subprocess.run(["claude", "plugin", "validate", "--strict", str(target)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Validation passed" in proc.stdout, proc.stdout
