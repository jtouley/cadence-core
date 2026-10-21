"""scripts/headless.py — walk stages.json gates for headless hosts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FAIL, PASS, SENTINEL, install_config, make_skill_root  # noqa: F401

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HEADLESS = REPO_ROOT / "scripts" / "headless.py"

def run_headless(
    args: list[str], *, cwd: Path, skill_root: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CADENCE_")}
    if skill_root is not None:
        env["CADENCE_HOME"] = str(skill_root)
    return subprocess.run(
        [sys.executable, str(HEADLESS), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_next_is_first_failing_gate_even_when_later_gates_pass(tmp_path, workspace):
    root = make_skill_root(
        tmp_path,
        [
            ("A1", "activate-session", "python3 scripts/activate.py", PASS),
            ("A2", "validate", "/vibe-test", FAIL),
            ("A3", "late-vacuous", "python3 scripts/late.py", SENTINEL),
        ],
    )
    proc = run_headless(["next", "--json"], cwd=workspace, skill_root=root)
    assert proc.returncode == 1, proc.stderr
    out = json.loads(proc.stdout)
    assert out["done"] is False
    assert out["next"]["block"] == "A2"
    assert out["next"]["slug"] == "validate"
    assert out["next"]["kind"] == "peer"
    assert out["next"]["command"] == "/vibe-test"
    assert out["next"]["peer_doc"] == str(root / "skills" / "vibe-test" / "SKILL.md")
    assert [r["block"] for r in out["results"]] == ["A1", "A2"]
    assert [r["ok"] for r in out["results"]] == [True, False]
    assert "nope" in out["results"][1]["output"]
    # Stop at first failure: the A3 gate never ran, so it left no sentinel.
    assert not (workspace / "ran-late_vacuous_gate").exists()


def test_run_all_pass_reports_done_and_exit_zero(tmp_path, workspace):
    root = make_skill_root(
        tmp_path,
        [
            ("A1", "one", "python3 scripts/one.py", PASS),
            ("A2", "two", "python3 scripts/two.py", SENTINEL),
        ],
    )
    proc = run_headless(["run", "--json"], cwd=workspace, skill_root=root)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["done"] is True
    assert out["next"] is None
    assert [r["ok"] for r in out["results"]] == [True, True]
    # Gates run with cwd = workspace.
    assert (workspace / "ran-two_gate").exists()


def test_next_script_block_has_no_peer_doc(tmp_path, workspace):
    root = make_skill_root(tmp_path, [("A1", "activate-session", "python3 scripts/a.py", FAIL)])
    out = json.loads(run_headless(["next", "--json"], cwd=workspace, skill_root=root).stdout)
    assert out["next"]["kind"] == "script"
    assert out["next"]["peer_doc"] is None
    assert out["next"]["gate_script"] == "scripts/activate_session_gate.py --check"


def test_run_through_stops_after_named_block(tmp_path, workspace):
    root = make_skill_root(
        tmp_path,
        [
            ("A1", "one", "python3 scripts/one.py", PASS),
            ("A2", "two", "python3 scripts/two.py", PASS),
            ("A3", "three", "python3 scripts/three.py", SENTINEL),
        ],
    )
    proc = run_headless(["run", "--through", "a2", "--json"], cwd=workspace, skill_root=root)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert [r["block"] for r in out["results"]] == ["A1", "A2"]
    assert out["done"] is True
    assert not (workspace / "ran-three_gate").exists()
    # A slug works as well as a block id.
    by_slug = json.loads(
        run_headless(["run", "--through", "one", "--json"], cwd=workspace, skill_root=root).stdout
    )
    assert [r["block"] for r in by_slug["results"]] == ["A1"]


def test_run_through_unknown_block_is_usage_error(tmp_path, workspace):
    root = make_skill_root(tmp_path, [("A1", "one", "python3 scripts/one.py", SENTINEL)])
    proc = run_headless(["run", "--through", "Z9"], cwd=workspace, skill_root=root)
    assert proc.returncode == 2
    assert "Z9" in proc.stderr and "A1" in proc.stderr
    assert not (workspace / "ran-one_gate").exists()


def test_gate_timeout_is_a_failure(tmp_path, workspace):
    slow = "import time; time.sleep(30)\n"
    root = make_skill_root(tmp_path, [("A1", "slow", "python3 scripts/slow.py", slow)])
    proc = run_headless(["next", "--json", "--timeout", "1"], cwd=workspace, skill_root=root)
    assert proc.returncode == 1
    result = json.loads(proc.stdout)["results"][0]
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["exit_code"] is None


def test_gate_output_is_tail_truncated(tmp_path, workspace):
    chatty = "import sys; print('x' * 10000); print('END'); sys.exit(1)\n"
    root = make_skill_root(tmp_path, [("A1", "chatty", "python3 scripts/c.py", chatty)])
    out = json.loads(run_headless(["next", "--json"], cwd=workspace, skill_root=root).stdout)
    output = out["results"][0]["output"]
    assert len(output) <= 2000
    assert output.rstrip().endswith("END")


def test_list_prints_registry_order_without_running_gates(tmp_path, workspace):
    root = make_skill_root(
        tmp_path,
        [
            ("A1", "one", "python3 scripts/one.py", SENTINEL),
            ("A2", "validate", "/vibe-test", SENTINEL),
        ],
    )
    proc = run_headless(["list", "--json"], cwd=workspace, skill_root=root)
    assert proc.returncode == 0
    rows = json.loads(proc.stdout)
    assert [(r["block"], r["kind"]) for r in rows] == [("A1", "script"), ("A2", "peer")]
    assert not list(workspace.glob("ran-*"))


def test_text_output_names_next_block(tmp_path, workspace):
    root = make_skill_root(
        tmp_path,
        [("A1", "one", "python3 scripts/one.py", PASS), ("A2", "validate", "/vibe-test", FAIL)],
    )
    proc = run_headless(["next"], cwd=workspace, skill_root=root)
    assert proc.returncode == 1
    assert "A2" in proc.stdout and "/vibe-test" in proc.stdout


def test_missing_registry_is_usage_error(tmp_path, workspace):
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = run_headless(["next"], cwd=workspace, skill_root=empty)
    assert proc.returncode == 2
    assert "stages.json" in proc.stderr


def test_real_registry_without_session_opens_at_a1(tmp_path):
    repo = tmp_path / "toy"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feature/gh-7-toy"], cwd=repo, check=True)
    proc = run_headless(["next", "--json"], cwd=repo)
    assert proc.returncode == 1, proc.stderr
    out = json.loads(proc.stdout)
    assert out["next"]["block"] == "A1"
    assert out["next"]["slug"] == "activate-session"
    assert out["next"]["kind"] == "script"
    assert not (repo / ".context" / "gates").exists()


@pytest.mark.parametrize(
    "registry",
    [
        {"stages": [{"slug": "a", "block": "A1", "command": "x"}]},
        {"stages": [{"block": "A1", "command": "x", "gate_script": "scripts/a.py"}]},
        {"stages": 5},
        {"stages": ["A1"]},
    ],
    ids=["no-gate-script", "no-slug", "stages-not-list", "row-not-object"],
)
def test_malformed_registry_is_usage_error(tmp_path, workspace, registry):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "stages.json").write_text(json.dumps(registry))
    for command in (["next"], ["list"]):
        proc = run_headless(command, cwd=workspace, skill_root=root)
        assert proc.returncode == 2, (command, proc.stderr)
        assert "Traceback" not in proc.stderr
        assert "stages.json" in proc.stderr


def test_missing_workspace_is_usage_error(tmp_path, workspace):
    root = make_skill_root(tmp_path, [("A1", "one", "python3 scripts/one.py", PASS)])
    proc = run_headless(
        ["next", "--workspace", str(tmp_path / "nope")], cwd=workspace, skill_root=root
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "nope" in proc.stderr


@pytest.mark.parametrize(
    ("registry", "expect"),
    [
        ({"stages": []}, "no stages"),
        (
            {"stages": [{"slug": "a", "block": "A1", "command": "x", "gate_script": "scripts/missing_gate.py"}]},
            "missing_gate.py",
        ),
        (
            {"stages": [{"slug": "a", "block": "A1", "command": "x", "gate_script": "scripts/a.py 'unclosed"}]},
            "gate_script",
        ),
    ],
    ids=["empty", "gate-script-missing", "gate-script-unparseable"],
)
def test_broken_registry_is_usage_error_before_any_gate_runs(tmp_path, workspace, registry, expect):
    """A broken registry is exit 2, never exit 1: an outer loop reads 1 as "run the open block"."""
    root = install_config(tmp_path / "skill")
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "a.py").write_text(SENTINEL)
    (root / "stages.json").write_text(json.dumps(registry))
    for command in (["next"], ["run", "--through", "Z9"], ["list"]):
        proc = run_headless(command, cwd=workspace, skill_root=root)
        assert proc.returncode == 2, (command, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr
        assert expect in proc.stderr
    assert not list(workspace.glob("ran-*"))
