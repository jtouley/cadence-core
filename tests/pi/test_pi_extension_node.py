"""Run the Node tests of pi/cadence-gates.ts with real repo states.

node >= 23.6 runs `.ts` with type stripping. Without a working node the test is skipped,
except with CADENCE_REQUIRE_NODE=1 (CI), where it fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests_pi_helpers import repo_for

from conftest import activate

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MIN_NODE = (23, 6)


def node_or_reason(env: dict[str, str]) -> tuple[str | None, str]:
    """(node path, "") for a working node >= 23.6, else (None, why)."""
    node = env.get("CADENCE_TEST_NODE") or shutil.which("node", path=env.get("PATH"))
    if not node:
        return None, "no node: set CADENCE_TEST_NODE or put node on PATH"
    try:
        out = subprocess.run([node, "-e", "process.stdout.write(process.versions.node)"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"node {node} does not start: {exc}"
    if out.returncode != 0:
        return None, f"node {node} does not start: {out.stderr.strip()[-300:]}"
    version = tuple(int(p) for p in out.stdout.strip().split(".")[:2])
    if version < MIN_NODE:
        return None, f"node {out.stdout.strip()} is older than 23.6"
    return node, ""


def require_node(env: dict[str, str]) -> str:
    node, why = node_or_reason(env)
    if node is None:
        if env.get("CADENCE_REQUIRE_NODE") == "1":
            pytest.fail(why)
        pytest.skip(why)
    return node


def test_the_extension_node_tests_pass(tmp_path):
    node = require_node(dict(os.environ))
    states = ("before_p9", "e1", "e1_owned", "finished", "none")
    repos = {state: str(repo_for(state, tmp_path)) for state in states}
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CADENCE_HOME", "CADENCE_SKILL_ROOT"))}
    env.update({"CADENCE_PI_REPOS": json.dumps(repos), "CLAUDECODE": "1", "CADENCE_PYTHON": sys.executable})
    proc = subprocess.run([node, "--test", "tests/pi/cadence_gates.test.ts"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]


def test_a_missing_node_skips(monkeypatch):
    env = {"CADENCE_TEST_NODE": "/nonexistent/node", "PATH": ""}
    with pytest.raises(pytest.skip.Exception):
        require_node(env)


def test_a_missing_node_fails_when_node_is_required():
    env = {"CADENCE_TEST_NODE": "/nonexistent/node", "PATH": "", "CADENCE_REQUIRE_NODE": "1"}
    with pytest.raises(pytest.fail.Exception):
        require_node(env)


def _activate(repo: Path, **extra: str) -> dict:
    activate(repo, "--fresh", "--task-id", "gh-1", "--workspace", str(repo), **extra)
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    import session_store

    return session_store.load_worktree_session(repo) or json.loads(
        (repo / ".context" / "cadence-session.json").read_text(encoding="utf-8"))


def test_activation_takes_the_owner_from_cadence_owner_id(tmp_path):
    # MF-5: the extension sets CADENCE_OWNER_ID; it must win over an inherited Claude id.
    session = _activate(repo_for("fresh", tmp_path), CLAUDE_CODE_SESSION_ID="claude-x", CADENCE_OWNER_ID="pi-sess-1")
    assert session["owner_session_id"] == "pi-sess-1"


def test_activation_without_claudecode_is_not_a_claude_run(tmp_path):
    # C2: the extension removes CLAUDECODE, so a Pi bash activation is not a Claude Code run.
    assert _activate(repo_for("fresh", tmp_path)).get("host") != "claude"
    assert _activate(repo_for("fresh", tmp_path / "c"), CLAUDECODE="1").get("host") == "claude"
