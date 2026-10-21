#!/usr/bin/env python3
"""Find peer shadows: host entries with a peer's slash name that are not the bundled peer (cd-53).

A host runs a user-level command or skill with a peer's name in place of the
Cadence peer. For each peer of `references/bundled-peers.json` (`peers` and
`supporting_commands`), the finder checks the command
entry `<commands>/<name>.md` and the skill entry `<skills>/<name>` of each host
directory pair. A pair with no commands directory is a skills-only pair. An
entry that exists is a peer shadow, unless it is the peer's own kind and
resolves to the bundled file (a command) or the bundled skill directory (a skill).

Exception to `core:standalone-package`: this module may lstat and
resolve() a host entry with a peer's name. It never reads, loads, or runs the
content of a host entry.

The runtime interpreter can be Python 3.9 (the system python3 on macOS).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from publish_peers import load_peer_rows, peer_kind  # noqa: E402

Pair = Tuple[Path, Optional[Path]]  # (skills dir, commands dir or None for a skills-only dir)

# Cursor also loads skills from these dirs, at home and in the workspace
# (https://cursor.com/docs/context/skills). The docs give no precedence when two
# dirs have a skill with the same name, so A2 fails closed: a copy in any of them
# is a peer shadow. Cursor reads commands only from the .cursor tree.
_CURSOR_EXTRA_SKILLS = (".agents", ".claude", ".codex")


class Shadow(NamedTuple):
    slash: str
    path: Path
    bundled: Path
    kind: str  # the kind of the peer: "command" or "skill"


def _resolved(path: Path) -> Optional[Path]:
    """resolve() of a host path, or None. Python 3.9 to 3.12 raise RuntimeError on a symlink loop."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def host_dirs(
    host: str,
    *,
    home: Path,
    workspace: Optional[Path] = None,
    commands_dir: Optional[Path] = None,
) -> List[Pair]:
    """The (skills, commands) pairs that a host reads slash names from: home first, then workspace.

    For Cursor, each base also gets a skills-only pair for each dir of _CURSOR_EXTRA_SKILLS.
    """
    if host == "claude":
        pairs = [(home / ".claude" / "skills", home / ".claude" / "commands")]
        if workspace is not None:
            pairs.append((workspace / ".claude" / "skills", workspace / ".claude" / "commands"))
    elif host == "cursor":
        cursor_commands = Path(commands_dir) if commands_dir else home / ".cursor" / "commands"
        pairs = [(home / ".cursor" / "skills", cursor_commands)]
        pairs += [(home / d / "skills", None) for d in _CURSOR_EXTRA_SKILLS]
        if workspace is not None:
            pairs.append((workspace / ".cursor" / "skills", workspace / ".cursor" / "commands"))
            pairs += [(workspace / d / "skills", None) for d in _CURSOR_EXTRA_SKILLS]
    else:
        raise ValueError(f"unknown host {host!r}: expected 'claude' or 'cursor'")
    unique: List[Pair] = []
    seen = set()
    for skills, commands in pairs:
        key = tuple(None if p is None else (_resolved(p) or p) for p in (skills, commands))
        if key not in seen:
            seen.add(key)
            unique.append((skills, commands))
    return unique


def find_peer_shadows(root: Path, pairs: Sequence[Pair]) -> List[Shadow]:
    """Each peer shadow in `pairs`, sorted by slash name, then path.

    An entry that two pairs share is listed once.
    """
    root = Path(root).resolve()
    shadows: List[Shadow] = []
    seen = set()
    for row in load_peer_rows(root):
        name = str(row["slash"]).lstrip("/")
        kind = peer_kind(row)
        bundled = (root / row["path"]).resolve()
        if kind == "skill":
            bundled = bundled.parent
        for skills, commands in pairs:
            entries = [("skill", skills / name)]
            if commands is not None:
                entries.insert(0, ("command", commands / f"{name}.md"))
            for entry_kind, entry in entries:
                if entry in seen or not os.path.lexists(entry):
                    continue
                seen.add(entry)
                if entry_kind == kind and _resolved(entry) == bundled:
                    continue
                shadows.append(Shadow(str(row["slash"]), entry, bundled, kind))
    return sorted(shadows, key=lambda s: (s.slash, str(s.path)))


def shadow_lines(shadows: Sequence[Shadow]) -> List[str]:
    """One line for each peer shadow. A skill's bundled path ends with '/'."""
    lines = []
    for s in shadows:
        bundled = f"{s.bundled}/" if s.kind == "skill" else str(s.bundled)
        lines.append(f"peer shadow: {s.slash} resolves to {s.path}, not {bundled}")
    return lines


def fix_lines(root: Path) -> List[str]:
    """The fix text for each install kind (spec cd-53 Design 3)."""
    return [
        "Remove or rename each listed user entry.",
        f"On an install.sh install, then run {Path(root).resolve() / 'install.sh'}.",
        "On a plugin install, do not run install.sh.",
        "Start a new host session before you run --resume.",
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The install.sh warning: peer shadows in the Claude Code and Cursor trees of HOME. Exits 0."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True, help="the Cadence checkout that install.sh links")
    args = parser.parse_args(argv)
    home = Path.home()
    commands_dir = os.environ.get("CADENCE_COMMANDS_DIR")
    pairs = host_dirs("claude", home=home) + host_dirs(
        "cursor", home=home, commands_dir=Path(commands_dir) if commands_dir else None
    )
    shadows = find_peer_shadows(args.root, pairs)
    if shadows:
        print("WARNING: peer shadows. A host runs these user entries in place of the Cadence peers.")
        for line in shadow_lines(shadows) + fix_lines(args.root):
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
