"""Commands for the agent, with absolute script paths under the skill root (cd-82, #83).

`stages.json` names gate scripts relative to the skill root (`python3 scripts/x.py`).
The agent runs a printed command in the project, where `scripts/` is absent or is
another tree. Standard library only: hooks import this module too.
"""

from __future__ import annotations

import shlex
from pathlib import Path

_PREFIX = "python3 scripts/"


def display_command(command: str, root: Path) -> str:
    """`python3 scripts/x.py ARGS` -> `python3 <root>/scripts/x.py ARGS`; other text unchanged."""
    if not command.startswith(_PREFIX):
        return command
    script, _, rest = command[len("python3 "):].partition(" ")
    path = shlex.quote(str(Path(root).resolve() / script))
    return f"python3 {path} {rest}".rstrip()
