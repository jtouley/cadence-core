"""pipeline.md points at the phase model. It does not keep a second exit table."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STAY_HEADINGS = (
    "## Provenance",
    "## Enforcement matrix (Cursor vs Pi)",
    "## Findings ledger",
    "## Backtrack posture",
    "## Packs",
)

LINK_FILES = (
    ROOT / "docs" / "index.md",
    ROOT / "docs" / "quickstart.md",
)

def test_pipeline_doc_points_at_system_design_and_drops_phase_table():
    """pipeline.md keeps the host matrix and drops its own phase-exit heading."""
    pipeline = (ROOT / "docs" / "pipeline.md").read_text(encoding="utf-8")
    assert "## Phase model" not in pipeline
    assert "system-design.md" in pipeline
    assert "stages.json" in pipeline
    missing = [heading for heading in STAY_HEADINGS if heading not in pipeline]
    assert missing == []

    system = (ROOT / "docs" / "system-design.md").read_text(encoding="utf-8")
    assert "Narrative exits" not in system
    assert "Host enforcement and the findings ledger" in system

    for path in LINK_FILES:
        text = path.read_text(encoding="utf-8")
        assert "compact phase exits" not in text.lower(), path.name
        assert "Host enforcement matrix" in text, path.name
