"""CADENCE_GATES_READONLY: a gate records nothing, and the variable never touches P9.

The stop guard walks the registered gates on every stop attempt. A stop must not
change pipeline state, so the gates need a mode that writes no breadcrumb.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from conftest import activated_repo, clean_env, p9_marker, passing_acceptance, snapshot

ROOT = Path(__file__).resolve().parents[2]
READONLY = "CADENCE_GATES_READONLY"


def run_script(name: str, args: list[str], cwd: Path, **env) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), *args],
        cwd=cwd,
        env=clean_env(scrub=("CADENCE_",), **env),
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def run(tmp_path: Path) -> Path:
    """A workspace with a real activation, as a fresh run leaves it."""
    return activated_repo(tmp_path / "ws")


# The gates that write a breadcrumb when they run as stages.json registers them
# (no --check). One passes with no artifact; the others fail, so a failing gate writes nothing.
def test_breadcrumb_gate_writes_by_default_and_not_when_readonly(run):
    context = run / ".context"
    before = snapshot(context)
    proc = run_script("cognitive_complexity_gate.py", [], run)
    assert proc.returncode == 0, proc.stderr
    written = {k for k, v in snapshot(context).items() if k not in before and v != "dir"}
    assert written == {"gates/cognitive-complexity.json"}

    (context / "gates" / "cognitive-complexity.json").unlink()
    before = snapshot(context)
    proc = run_script("cognitive_complexity_gate.py", [], run, **{READONLY: "1"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK  cognitive-complexity")
    assert snapshot(context) == before


def test_a_readonly_gate_does_not_even_create_the_gates_directory(run):
    """An empty directory is state too (R2 mutant 25). Remove it first, so a mkdir would show."""
    context = run / ".context"
    shutil.rmtree(context / "gates", ignore_errors=True)
    before = snapshot(context)
    proc = run_script("cognitive_complexity_gate.py", [], run, **{READONLY: "1"})
    assert proc.returncode == 0, proc.stderr
    assert not (context / "gates").exists()
    assert snapshot(context) == before


def test_implement_gate_passes_readonly_without_recording(run):
    context = run / ".context"
    (context / "gates").mkdir(exist_ok=True)
    p9_marker(run).write_text('{"task_id": "cd-7"}\n')  # The run's own marker
    passing_acceptance(context, run, "cd-7")  # P9 pins the checks, and E1 runs them
    (run / "work.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "work.py"], cwd=run, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: work"], cwd=run, check=True)
    before = snapshot(context)
    proc = run_script("implement_gate.py", [], run, **{READONLY: "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert snapshot(context) == before


@pytest.mark.parametrize("value", ["", "0", "no"])
def test_only_the_value_one_turns_readonly_on(run, value):
    proc = run_script("cognitive_complexity_gate.py", [], run, **{READONLY: value})
    assert proc.returncode == 0
    assert (run / ".context" / "gates" / "cognitive-complexity.json").is_file()


@pytest.mark.parametrize("args", [["--phase", "execution"], ["--phase", "execution", "--check"]])
@pytest.mark.parametrize("env", [{}, {READONLY: "1"}], ids=["plain", "readonly"])
def test_a_failed_p9_clears_the_marker_with_or_without_the_variable(run, args, env):
    """Why the stop guard never runs P9, and why the variable must not touch it.

    Even `--check` clears the marker when the check fails. If the variable stopped that,
    an agent could delete a plan artifact and keep its clearance (adversarial review C1).
    """
    gates = run / ".context" / "gates"
    gates.mkdir(exist_ok=True)
    marker = p9_marker(run)  # The run's own marker
    marker.write_text('{"task_id": "cd-7"}\n')
    proc = run_script("entry_invariant.py", args, run, **env)
    assert proc.returncode != 0
    assert not marker.exists()


def execution_and_review_gates() -> list[dict]:
    rows = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))["stages"]
    return [r for r in rows if r["phase"] in ("execution", "review")]


@pytest.mark.parametrize("row", execution_and_review_gates(), ids=lambda r: r["block"])
def test_every_registered_gate_from_e1_on_is_read_only_under_the_variable(run, row):
    """The stop guard runs these gates on every stop. None may write (review R1, cd-12-F1).

    The run has the P9 marker and a real implementation artifact, so E3 reaches the code
    that once wrote architecture-review-notice.json. The gates run as stages.json registers them.
    """
    gates = run / ".context" / "gates"
    gates.mkdir(exist_ok=True)
    p9_marker(run).write_text('{"task_id": "cd-7"}\n')  # The run's own marker
    (run / "changed.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "changed.txt"], cwd=run, check=True)
    assert run_script("impl_artifact.py", [], run).returncode == 0
    before = snapshot(run / ".context")
    argv = shlex.split(row["gate_script"])
    run_script(Path(argv[0]).name, argv[1:], run, **{READONLY: "1"})
    assert snapshot(run / ".context") == before
