"""The orchestrator is told how to pass the configured peer model (cd-58 D7, AC8)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def test_delegation_doc_has_the_peer_models_rules():
    section = _section((ROOT / "references" / "delegation.md").read_text(encoding="utf-8"),
                       "## Peer models (Claude Code)")
    for needle in ("peer_models", "model:", "--status", "peer_models.py report", "fork"):
        assert needle in section, needle
    assert "`begin` record is not model evidence" in section


def test_skill_md_points_to_the_section():
    paceline = _section((ROOT / "SKILL.md").read_text(encoding="utf-8"), "## Paceline (delegation)")
    assert "peer_models" in paceline and "references/delegation.md" in paceline


def test_subagent_hygiene_doc_has_the_table_scope_and_rollback():
    section = _section((ROOT / "docs" / "subagent-hygiene.md").read_text(encoding="utf-8"), "## Peer models")
    for needle in ("peer_models", "default", "haiku", "Claude Code", "--fresh", "HOME"):
        assert needle in section, needle
