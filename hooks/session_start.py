#!/usr/bin/env python3
"""SessionStart hook (Claude Code): export CADENCE_HOME for the Bash tool.

SKILL.md, the commands and the references run `$CADENCE_HOME/scripts/...`.
A plugin install has no fixed path under ~/.claude/skills, so this hook writes
the root of this checkout to CLAUDE_ENV_FILE. Without that file it does nothing.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

# The unresolved path: a symlinked install keeps following its link.
ROOT = Path(os.path.abspath(__file__)).parent.parent


def main() -> int:
    sys.stdin.read()
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return 0
    with open(env_file, "a", encoding="utf-8") as handle:
        handle.write(f"export CADENCE_HOME={shlex.quote(str(ROOT))}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
