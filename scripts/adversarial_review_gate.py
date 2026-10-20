#!/usr/bin/env python3
"""P6 — adversarial-review peer gate.

`--heading <report>` prints the carried-findings heading of a report (cd-63 D2), so the
plan-update peer does not compute the hash by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gate_common import cli, run_artifact_gate  # noqa: E402


def _heading(argv: list[str]) -> int:
    from artifact_requirements import carried_heading

    path = Path(argv[0]) if argv else None
    if path is None or not path.is_file():
        print(f"FAIL  --heading: {path} not readable", file=sys.stderr)
        return 1
    print(carried_heading(path))
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--heading"]:
        raise SystemExit(_heading(sys.argv[2:]))
    raise SystemExit(
        cli(lambda ctx, check: run_artifact_gate("adversarial-review-gate", ctx, check=check))
    )
