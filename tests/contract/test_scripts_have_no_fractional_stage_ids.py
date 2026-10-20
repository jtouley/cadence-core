"""Phase 2: scripts/ must not embed legacy fractional stage tokens."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FORBIDDEN = re.compile(
    r"(?<![\w./-])(?:3\.5b?|3\.45|3\.7|3\.75|3\.8|3\.85|6\.45|6\.5|6\.6|6\.65|6\.7|\bstage4\b)(?![\w./-])",
    re.IGNORECASE,
)


def test_scripts_have_no_fractional_stage_ids():
    hits: list[str] = []
    for path in sorted(SCRIPTS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in FORBIDDEN.finditer(text):
            hits.append(f"{path.name}:{m.start()}:{m.group(0)!r}")
    assert not hits, "fractional stage ids in scripts:\n" + "\n".join(hits)
