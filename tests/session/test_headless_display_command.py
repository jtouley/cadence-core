"""Commands for the agent name absolute scripts under the skill root (cd-82 D5, #83).

The agent runs them in the project, where `scripts/` is absent or is another tree.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FAIL, PASS, git_repo, install_config, make_skill_root

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_paths import display_command  # noqa: E402

pytestmark = pytest.mark.integration

HEADLESS = ROOT / "scripts" / "headless.py"


def _script(text: str) -> Path:
    return Path(shlex.split(text)[1])


def test_relative_script_becomes_absolute_under_the_resolved_root():
    out = display_command("python3 scripts/entry_invariant.py --phase execution", ROOT)
    assert out == f"python3 {shlex.quote(str(ROOT.resolve() / 'scripts' / 'entry_invariant.py'))} --phase execution"
    assert _script(out).is_file()


def test_root_with_a_space_stays_one_token(tmp_path):
    root = tmp_path / "skill root"
    out = display_command("python3 scripts/x.py --check", root)
    assert shlex.split(out) == ["python3", str(root.resolve() / "scripts" / "x.py"), "--check"]


@pytest.mark.parametrize("text", ["/plan-review", "/commit-push-pr --draft", "", "python3 /abs/scripts/x.py"])
def test_other_text_is_unchanged(text):
    assert display_command(text, ROOT) == text


def test_second_call_is_a_no_op():
    once = display_command("python3 scripts/closure.py", ROOT)
    assert display_command(once, ROOT) == once


def _rows():
    return json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))["stages"]


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["slug"])
def test_every_stage_command_and_gate_names_a_script_that_exists(row):
    command = str(row.get("command") or "")
    if command.startswith("python3 scripts/"):
        assert _script(display_command(command, ROOT)).is_file()
    if row.get("gate_script"):
        assert _script(display_command("python3 " + row["gate_script"], ROOT)).is_file()


def test_skill_paths_imports_only_the_standard_library():
    tree = ast.parse((ROOT / "scripts" / "skill_paths.py").read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= {"__future__", "shlex", "pathlib"}


# --- headless next / list ----------------------------------------------------


def _headless(args, cwd, root):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CADENCE_")}
    env["CADENCE_HOME"] = str(root)
    return subprocess.run([sys.executable, str(HEADLESS), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def toy(tmp_path):
    root = make_skill_root(tmp_path, [
        ("A1", "activate-session", "python3 scripts/activate_session.py", PASS),
        ("A2", "preflight", "python3 scripts/preflight_gate.py", FAIL),
    ])
    (root / "scripts" / "activate_session.py").write_text(PASS, encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return root, workspace


def _slash_toy(tmp_path):
    root = make_skill_root(tmp_path, [("A1", "validate", "/vibe-test", FAIL)])
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return root, workspace


def test_next_json_adds_absolute_run_and_check_for_a_gate_row(toy):
    root, workspace = toy
    nxt = json.loads(_headless(["next", "--json"], workspace, root).stdout)["next"]
    assert nxt["command"] == "python3 scripts/preflight_gate.py"
    assert nxt["gate_script"] == "scripts/preflight_gate.py --check"
    assert _script(nxt["run"]) == root.resolve() / "scripts" / "preflight_gate.py"
    assert _script(nxt["check"]).is_file()
    assert nxt["check"].endswith(" --check")


def test_next_json_run_is_the_command_for_a_slash_row(tmp_path):
    root, workspace = _slash_toy(tmp_path)
    nxt = json.loads(_headless(["next", "--json"], workspace, root).stdout)["next"]
    assert nxt["run"] == nxt["command"] == "/vibe-test"
    assert _script(nxt["check"]).is_file()


def test_next_text_names_absolute_paths_and_no_literal_env_var(toy):
    root, workspace = toy
    out = _headless(["next"], workspace, root).stdout
    assert "$CADENCE_HOME" not in out
    script = str(root.resolve() / "scripts" / "preflight_gate.py")
    assert re.search(rf"NEXT .*: python3 {re.escape(script)}", out)
    assert f"check:     python3 {script} --check" in out


def test_list_text_names_absolute_scripts(toy):
    root, workspace = toy
    out = _headless(["list"], workspace, root).stdout
    assert str(root.resolve() / "scripts" / "activate_session.py") in out
    assert " python3 scripts/" not in out


# --- stop guard messages (three sites) ----------------------------------------


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    sys.path.insert(0, str(ROOT / "hooks"))
    import pipeline_stop_guard

    return pipeline_stop_guard


def _via(message: str) -> str:
    return re.search(r"via `([^`]*)`", message).group(1)


GATE_ROW = {"block": "P9", "slug": "entry-invariant", "phase": "planning",
            "command": "python3 scripts/entry_invariant.py --phase execution"}


def test_planning_hold_names_the_absolute_gate_script(guard):
    session = {"active_skill": "cadence", "resume_slug": "activate-session"}
    _decision, message = guard.decide(session, open_block=GATE_ROW)
    assert _script(_via(message)) == (ROOT.resolve() / "scripts" / "entry_invariant.py")


def test_execution_hold_names_the_absolute_gate_script(guard):
    row = {**GATE_ROW, "block": "E2", "slug": "impl-artifact", "phase": "execution",
           "command": "python3 scripts/impl_artifact.py"}
    _decision, message = guard.decide({"active_skill": "cadence", "resume_slug": "implement"}, open_block=row)
    assert _script(_via(message)).is_file()


def test_ready_plan_hold_names_the_absolute_architecture_script(guard):
    message = guard.planning_continuation("plan-review", "READY TO EXECUTE")
    assert _script(_via(message)) == ROOT.resolve() / "scripts" / "architecture_review.py"


def test_slash_row_hold_is_unchanged(guard):
    row = {"block": "P3", "slug": "plan-review", "phase": "planning", "command": "/plan-review"}
    _decision, message = guard.decide({"active_skill": "cadence", "resume_slug": "activate-session"}, open_block=row)
    assert _via(message) == "/plan-review"


def test_hold_with_a_skill_root_that_has_a_space_keeps_one_token(guard, monkeypatch, tmp_path):
    root = install_config(tmp_path / "skill root")
    (root / "stages.json").write_text((ROOT / "stages.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(guard, "SKILL_ROOT", root)
    monkeypatch.chdir(git_repo(tmp_path / "proj"))  # the config layers of a project, not of this checkout
    _decision, message = guard.decide({"active_skill": "cadence", "resume_slug": "activate-session"},
                                      open_block=GATE_ROW)
    assert shlex.split(_via(message))[1] == str(root.resolve() / "scripts" / "entry_invariant.py")
