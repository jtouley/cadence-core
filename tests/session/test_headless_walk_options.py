"""headless.walk options the stop guard needs: since, readonly, budget (#12)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from conftest import FAIL, PASS, SENTINEL, make_skill_root  # noqa: E402

ENV_PROBE = (
    "import os, pathlib\n"
    "pathlib.Path('env-seen').write_text(repr(os.environ.get('CADENCE_GATES_READONLY')))\n"
)
HANG = "import time; time.sleep(30)\n"


@pytest.fixture
def ws(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return path


def three_gates(tmp_path, last=FAIL):
    return make_skill_root(
        tmp_path,
        [
            ("A1", "one", "python3 scripts/one.py", SENTINEL),
            ("A2", "two", "python3 scripts/two.py", SENTINEL),
            ("A3", "three", "python3 scripts/three.py", last),
        ],
    )


@pytest.mark.parametrize("since", ["A2", "a2", "two"])
def test_since_skips_the_gates_before_it(scripts_path, tmp_path, ws, since):
    from headless import walk

    report = walk(three_gates(tmp_path), ws, since=since)
    assert [r["block"] for r in report["results"]] == ["A2", "A3"]
    assert report["next"]["block"] == "A3"
    assert not (ws / "ran-one_gate").exists()
    assert (ws / "ran-two_gate").exists()


def test_since_unknown_block_is_a_usage_error(scripts_path, tmp_path, ws):
    from headless import UsageError, walk

    with pytest.raises(UsageError, match="Z9"):
        walk(three_gates(tmp_path), ws, since="Z9")
    assert not (ws / "ran-one_gate").exists()


def test_readonly_reaches_each_gate_and_leaves_the_caller_environment_alone(scripts_path, tmp_path, ws):
    from headless import walk

    root = make_skill_root(tmp_path, [("A1", "probe", "python3 scripts/probe.py", ENV_PROBE)])
    walk(root, ws, readonly=True)
    assert (ws / "env-seen").read_text() == "'1'"
    assert "CADENCE_GATES_READONLY" not in os.environ
    (ws / "env-seen").unlink()
    walk(root, ws)
    assert (ws / "env-seen").read_text() == "None"


def test_budget_used_up_before_a_gate_counts_as_a_timeout_and_runs_nothing(scripts_path, tmp_path, ws):
    from headless import walk

    report = walk(three_gates(tmp_path, last=PASS), ws, budget=0)
    assert report["done"] is False
    assert report["next"]["block"] == "A1"
    (only,) = report["results"]
    assert only["timed_out"] is True and only["ok"] is False
    assert not (ws / "ran-one_gate").exists()


def test_budget_ends_a_hung_gate_in_time(scripts_path, tmp_path, ws):
    from headless import walk

    root = make_skill_root(tmp_path, [("A1", "hang", "python3 scripts/hang.py", HANG)])
    start = time.monotonic()
    report = walk(root, ws, timeout=30, budget=1)
    assert time.monotonic() - start < 6
    assert report["results"][0]["timed_out"] is True


def test_without_a_budget_the_walk_is_unchanged(scripts_path, tmp_path, ws):
    from headless import walk

    report = walk(three_gates(tmp_path, last=PASS), ws)
    assert report["done"] is True
    assert [r["block"] for r in report["results"]] == ["A1", "A2", "A3"]


SPAWN_CHILD = (
    "import pathlib, subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "pathlib.Path('child.pid').write_text(str(child.pid))\n"
    "time.sleep(60)\n"
)


def alive(pid: int) -> bool:
    """A killed child that nobody has reaped yet is a zombie. It is not running."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def test_a_timeout_kills_the_gate_and_its_grandchild(scripts_path, tmp_path, ws):
    """The stop guard runs the walk on every stop. A leftover child would pile up (cd-12-F6)."""
    from headless import walk

    root = make_skill_root(tmp_path, [("A1", "spawn", "python3 scripts/spawn.py", SPAWN_CHILD)])
    walk(root, ws, timeout=1)
    pid = int((ws / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid), f"the child {pid} of the gate survived the timeout"


ESCAPING_CHILD = (
    "import subprocess, sys, time\n"
    # The child starts its own session, so the kill of the gate's group cannot reach it.
    # It keeps the stdout pipe of the gate open for 8 s.
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'], start_new_session=True)\n"
    "time.sleep(60)\n"
)


def test_a_child_that_escapes_the_group_cannot_hold_the_walk_past_its_timeout(scripts_path, tmp_path, ws):
    """After the kill the walk must not wait for the pipe. The hook has a 15 s limit (delta pass)."""
    from headless import walk

    root = make_skill_root(tmp_path, [("A1", "escape", "python3 scripts/escape.py", ESCAPING_CHILD)])
    start = time.monotonic()
    report = walk(root, ws, timeout=1)
    assert time.monotonic() - start < 4
    assert report["results"][0]["timed_out"] is True
