"""Cadence scripts and shared Python must not point at a host skill tree.

One exception: scripts/peer_shadows.py builds host command and
skill paths with `home / ".cursor" / "commands"` to lstat a peer shadow. It never
reads the content of a host entry. No other module may build those paths.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The cadence install link is allowed. Any other host skill or command path is not,
# with or without a leading ~/ (a Path.home() join has none).
HOST_LEAK = re.compile(r"(?:~/)?\.cursor/(?:skills/(?!cadence\b)|commands/)")


def test_no_host_skill_paths_in_cadence_python():
    hits: list[str] = []
    for base in (ROOT / "scripts", ROOT / "shared"):
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in HOST_LEAK.finditer(text):
                hits.append(f"{path.relative_to(ROOT)}:{match.group(0)}")
    assert not hits, "host skill path refs:\n" + "\n".join(hits)


HOST_COMMANDS = re.compile(r"""["']\.(?:claude|cursor)["']\s*/\s*["']commands["']""")


def test_only_peer_shadows_builds_host_command_paths():
    owner = ROOT / "scripts" / "peer_shadows.py"
    assert len(HOST_COMMANDS.findall(owner.read_text(encoding="utf-8"))) >= 2
    others = [
        str(path.relative_to(ROOT))
        for base in (ROOT / "scripts", ROOT / "shared")
        for path in base.rglob("*.py")
        if path != owner and HOST_COMMANDS.search(path.read_text(encoding="utf-8"))
    ]
    assert not others, "host command paths outside scripts/peer_shadows.py: " + ", ".join(others)
