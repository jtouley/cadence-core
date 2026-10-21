"""The hook fragments (hooks.json, hooks-cursor.json): what each registers, in order, fail closed."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import code_paths, git_repo

ROOT = Path(__file__).resolve().parents[2]


# --- hooks_fragment_fail_closed: Phase 5: hooks-cursor.json parses; stop hooks are failClosed.


def test_hooks_fragment_fail_closed():
    data = json.loads((ROOT / "hooks" / "hooks-cursor.json").read_text())
    assert data["version"] == 1
    for key in ("stop", "subagentStop"):
        entries = data["hooks"][key]
        assert entries, key
        for entry in entries:
            assert entry.get("failClosed") is True
            assert "pipeline_stop_guard.py" in entry["command"]


# --- hooks_fragment_includes_skill_gate: Hooks fragment includes skill_gate before shadow-context guard.


def test_hooks_fragment_includes_skill_gate_before_shadow():
    data = json.loads((ROOT / "hooks" / "hooks-cursor.json").read_text(encoding="utf-8"))
    pre = data["hooks"]["preToolUse"]
    assert len(pre) >= 2
    assert all(entry.get("failClosed") is True for entry in pre)
    cmds = [e["command"] for e in pre]
    skill_idx = next(i for i, c in enumerate(cmds) if "guard_skill_gate" in c or "skill_gate" in c)
    shadow_idx = next(i for i, c in enumerate(cmds) if "guard_primary_context" in c)
    assert skill_idx < shadow_idx, "skill_gate must run before shadow-context guard"


def test_hooks_fragment_includes_lean_task_prompt_guard():
    data = json.loads((ROOT / "hooks" / "hooks-cursor.json").read_text(encoding="utf-8"))
    pre = data["hooks"]["preToolUse"]
    match = [
        entry
        for entry in pre
        if "guard_lean_task_prompt.py" in entry.get("command", "")
    ]
    assert len(match) == 1
    entry = match[0]
    assert entry.get("failClosed") is True
    assert entry.get("matcher") == "Task"


def test_guard_skill_gate_maps_write_tools():
    import sys

    sys.path.insert(0, str(ROOT / "hooks"))
    import guard_skill_gate as gsg

    assert gsg.TOOL_TO_ACTION["Write"] == "write"
    assert gsg.TOOL_TO_ACTION["StrReplace"] == "strreplace"
    assert gsg.TOOL_TO_ACTION["EditNotebook"] == "editnotebook"
    assert gsg.TOOL_TO_ACTION["ApplyPatch"] == "write"


# --- hooks_fragment_registers_subagent_handoff: Both hook fragments register the subagent handoff hook; the shipped default is shadow.


def _load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_claude_fragment_registers_pre_tool_use_and_subagent_start():
    hooks = _load("hooks/hooks.json")["hooks"]
    pre = [g for g in hooks["PreToolUse"] if any("guard_subagent_handoff" in h["command"] for h in g["hooks"])]
    assert [g["matcher"] for g in pre] == ["Agent|Task"]
    start = [h["command"] for g in hooks["SubagentStart"] for h in g["hooks"]]
    assert len(start) == 1 and "guard_subagent_handoff.py" in start[0] and "--host claude" in start[0]


def test_cursor_fragment_leaves_task_to_the_lean_hook():
    """Cursor Task already runs guard_lean_task_prompt.py. A second matcher would deny the packet rendering."""
    pre = _load("hooks/hooks-cursor.json")["hooks"]["preToolUse"]
    lean = [e for e in pre if "guard_lean_task_prompt" in e["command"]]
    handoff = [e for e in pre if "guard_subagent_handoff" in e["command"]]
    assert len(lean) == 1 and lean[0]["matcher"] == "Task"
    assert handoff == []


def test_default_config_enforces_the_handoff_without_a_classifier(tmp_path):
    """The core defaults layer and the default profile, in a project with no layer of its own."""
    code_paths()
    import cadence_config

    resolved = cadence_config.load(ROOT, git_repo(tmp_path / "proj"), env={"HOME": str(tmp_path / "home")})
    cfg = resolved.section("subagent_hygiene")
    assert [layer.name for layer in resolved.layers] == ["default", "profile:standard"]
    assert cfg["mode"] == "enforce" and cfg["classifier"] == "none" and cfg["rewrite_prompt"] is True
