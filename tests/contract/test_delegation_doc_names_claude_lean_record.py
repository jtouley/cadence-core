"""cd-64 D6, C-2, C-3, C-4: the guidance names the Claude Code lean record, its recovery, and its limits."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert match, heading
    return match.group(1)


def test_delegation_doc_has_the_claude_lean_record_section():
    body = section((ROOT / "references" / "delegation.md").read_text(encoding="utf-8"), "Claude Code (lean record)")
    for needle in ("PreToolUse", "do not run", "begin", "fork", "slash command and its arguments only",
                   "plugin hooks", "source: PreToolUse"):
        assert needle.lower() in body.lower(), needle


def test_skill_md_paceline_points_at_the_section():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    paceline = section(text, "Paceline (delegation)")
    assert any("Claude Code" in line and "Claude Code (lean record)" in line for line in paceline.splitlines())


def test_hygiene_mode_row_names_the_lean_check():
    rows = [line for line in (ROOT / "docs" / "subagent-hygiene.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("| `mode`")]
    assert rows and "lean check" in rows[0] and "every mode" in rows[0]


def test_hook_docstring_no_longer_says_the_whole_hook_never_denies():
    text = (ROOT / "hooks" / "guard_subagent_handoff.py").read_text(encoding="utf-8")
    assert "never denies and never exits non-zero" not in text


def test_adr_0004_records_the_cd64_consequences():
    text = (ROOT / "docs" / "adr" / "0004-peer-prompts-come-from-handoff-packets.md").read_text(encoding="utf-8")
    body = section(text, "Consequences (cd-64)")
    for needle in ("cd-64", "every mode", "shadow", "452", "#73"):
        assert needle in body, needle
