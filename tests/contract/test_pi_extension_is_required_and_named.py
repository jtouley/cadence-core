"""CI runs the Pi extension test with node required; the docs name the extension (cd-84)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading):], re.M)
    return text[start: start + len(heading) + (nxt.start() if nxt else len(text))]


def test_ci_installs_node_and_requires_it():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    node = ci.find("uses: actions/setup-node@v4")
    assert node != -1
    assert re.search(r'node-version:\s*"24"', ci[node:])
    assert node < ci.index("run: uv run pytest")
    assert 'CADENCE_REQUIRE_NODE: "1"' in ci


def test_skill_md_pi_section_names_the_extension_and_its_limits():
    pi = _section((ROOT / "SKILL.md").read_text(encoding="utf-8"), "## Pi CLI gate protocol")
    for needle in ("pi/cadence-gates.ts", "hooks/hooks.json", "fallback", "custom tool",
                   "event.input", "CADENCE_OWNER_ID", "CADENCE_HOME", "inside Pi", "`!`"):
        assert needle in pi, needle


def test_host_matrix_pi_row_names_the_extension():
    row = next(l for l in (ROOT / "docs" / "system-design.md").read_text(encoding="utf-8").splitlines()
               if l.startswith("| Pi"))
    assert "pi/cadence-gates.ts" in row
