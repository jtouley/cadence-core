#!/usr/bin/env python3
"""Sample impl-phase pack gate (gh-177): flag a planted file name in the change.

stdin is the request of run_pack_gate: {"phase", "worktree", "files"}. stdout is {"findings": [...]}.
"""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath

PLANTED = "SAMPLE_IMPL_GATE_PLANTED.txt"


def main() -> int:
    request = json.load(sys.stdin)
    findings = [
        {
            "rule_ref": "pack:_sample_impl_gate:planted-file",
            "severity": "must-fix",
            "evidence": f"{rel}: {PLANTED} is the planted file name (phase={request['phase']})",
            "status": "open",
            "class": "bug",
            "location": rel,
            "check": "manual",
        }
        for rel in request["files"]
        if PurePosixPath(rel).name == PLANTED
    ]
    print(json.dumps({"findings": findings}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
