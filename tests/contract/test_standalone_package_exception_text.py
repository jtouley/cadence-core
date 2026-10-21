"""core:standalone-package names its one exception: a gate may lstat a host peer entry."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _rule_section() -> str:
    text = (ROOT / "references" / "architecture-core-rules.md").read_text(encoding="utf-8")
    start = text.index("### `core:standalone-package`")
    end = text.find("\n### ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_rule_text_names_the_exception():
    section = _rule_section()
    assert "lstat" in section
    assert "does not read, load, or run the content" in section
    assert "scripts/peer_shadows.py" in section


def test_thesis_names_the_exception():
    thesis = json.loads((ROOT / "references" / "bundled-peers.json").read_text(encoding="utf-8"))["thesis"]
    assert "never reads the content of" in thesis
    assert "lstat" in thesis
