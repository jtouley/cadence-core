"""One writer for marked vibe-test reports and run sessions in the A5 tests (P7 cd-93-F03)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

GOOD = "**Tracker:** github-issues\n**Ticket:** #901\n"
LINEAR = "**Linear:** ENG-9\n"
HOUR = 3600.0


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def session(run: str | None, *, activated: float, task_id: str = "cd-901", **extra) -> dict:
    """A run session dict as run_for gives it: run id, ticket and activation time."""
    data = {"active_skill": "cadence", "task_id": task_id, "activated_at": iso(activated), **extra}
    if run is not None:
        data["pipeline_run_id"] = run
    return data


def write_report(vibe: Path, name: str, *, run: str | None = None, header: str = GOOD,
                 mtime: float | None = None, label: str = "Pipeline-Run") -> Path:
    """A marked report. `run` adds a `<label>: <run>` line; `mtime` sets the file time."""
    lines = ["RUN_BY_SKILL: vibe-test"]
    if run is not None:
        lines.append(f"{label}: {run}")
    path = Path(vibe) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" + header, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def now() -> float:
    return time.time()
