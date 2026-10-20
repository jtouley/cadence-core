#!/usr/bin/env python3
"""P1 — plan-create peer gate."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gate_common import cli, run_plan_create_gate  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(cli(run_plan_create_gate))
