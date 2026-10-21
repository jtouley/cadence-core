"""README and SKILL.md tell the user how to clear a peer shadow and a hard stop set by mistake."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEW_SESSION = "Start a new host session"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_readme_names_the_peer_shadow_and_the_fix_for_each_install_kind():
    install = _section((ROOT / "README.md").read_text(encoding="utf-8"), "## Install")
    assert "peer shadow" in install
    assert "On a plugin install, do not run `install.sh`." in install
    assert NEW_SESSION in install


def test_skill_md_self_healing_clears_a_mistaken_hard_stop_in_order():
    healing = _section((ROOT / "SKILL.md").read_text(encoding="utf-8"), "## Self-healing")
    steps = ["Remove the cause", "scripts/preflight.py", NEW_SESSION, "--resume"]
    positions = [healing.index(step) for step in steps]
    assert positions == sorted(positions)
