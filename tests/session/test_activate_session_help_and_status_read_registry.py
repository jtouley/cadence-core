"""Phase 2 exit: activate_session --help and pipeline_status read registry."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import run_py

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return run_py(SCRIPTS / script, *args, cwd=ROOT)


def test_activate_session_help_exits_zero():
    result = _run("activate_session.py", "--help")
    assert result.returncode == 0
    assert "cadence" in result.stdout.lower()


def test_pipeline_status_reads_registry():
    result = _run("pipeline_status.py")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["registry_path"].endswith("stages.json")
    assert data["resume_slug"]
    assert data["resume_block"]
    slugs = [a["slug"] for a in data["artifacts"]]
    assert "activate-session" in slugs
    assert "session-close" in slugs
    # No fractional resume fields
    assert "resume_stage" not in data
