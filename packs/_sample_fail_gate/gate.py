#!/usr/bin/env python3
"""Sample pack gate — always fails (proves pack plugin contract)."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="planning")
    parser.add_argument("--worktree", default=".")
    args = parser.parse_args()
    findings = [
        {
            "rule_ref": "pack:_sample_fail_gate:_sample_fail_gate:always-fails",
            "severity": "must-fix",
            "evidence": (
                f"_sample_fail_gate always fails (phase={args.phase}, "
                f"worktree={args.worktree})"
            ),
            "status": "open",
            "class": "bug",
            "location": "packs/_sample_fail_gate",
            "check": "false",
        }
    ]
    print(json.dumps({"findings": findings}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
