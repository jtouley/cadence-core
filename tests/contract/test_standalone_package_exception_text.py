"""core:standalone-package names its one exception: a gate may lstat a host peer entry (cd-53, ADR-0006)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR = ROOT / "docs" / "adr" / "0006-gate-may-lstat-host-peer-entries.md"


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


def test_standalone_exception_has_accepted_adr():
    text = ADR.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    assert re.search(r"^decision_id: ADR-0006$", front, re.M)
    assert re.search(r"^status: accepted$", front, re.M)
    assert "target_id: scripts/peer_shadows.py" in front
    assert "jtouley/cadence#53" in text.split("---", 2)[2]
