"""Pi payload fixtures give the expected decision from the real Claude Code hooks (cd-84).

The fixtures are the contract between the Pi extension (Node) and the hooks (Python).
`pi/cadence-gates.ts` must build each `payload` from each `pi` input (Node test 2.1).
"""

from __future__ import annotations

import pytest

from conftest import tool_decision
from tests_pi_helpers import fixtures, repo_for

pytestmark = pytest.mark.integration

FIXTURES = fixtures()
JUDGED = [(f, hook) for f in FIXTURES if "payload" in f for hook, want in f["hooks"].items() if want]
# The hooks.json PreToolUse lists for each Claude tool name (F06: literal, not a second parser).
HOOK_LISTS = {
    "Write": ["guard_skill_gate.py", "guard_primary_context_artifacts.py"],
    "Edit": ["guard_skill_gate.py", "guard_primary_context_artifacts.py"],
    "Bash": ["guard_skill_gate.py", "guard_secret_read.py"],
}


def test_there_are_pi_fixtures():
    assert FIXTURES, "no Pi fixtures"


@pytest.mark.parametrize("fixture, hook", JUDGED, ids=[f"{f['name']}-{h}" for f, h in JUDGED])
def test_each_fixture_payload_gives_the_expected_hook_decision(run_hook, tmp_path, fixture, hook):
    repo = repo_for(fixture["run_state"], tmp_path)
    payload = {**fixture["payload"], "cwd": str(repo), "session_id": "pi"}
    tool_input = dict(payload["tool_input"])
    if "file_path" in tool_input:
        tool_input["file_path"] = str(repo / tool_input["file_path"])
    payload["tool_input"] = tool_input
    code, out, err = run_hook(hook, repo, payload, "claude")
    assert code == 0, err
    assert tool_decision("claude", out) == fixture["hooks"][hook], out


def test_fixtures_cover_each_judged_pi_tool():
    assert {f["pi"]["toolName"] for f in FIXTURES} == {"write", "edit", "bash", "powershell", "read"}
    assert {"S01", "S03", "S04", "S05", "S07"} <= {f["probe"] for f in FIXTURES}


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if "payload" in f], ids=lambda f: f["name"])
def test_fixture_hooks_match_the_hooks_json_list(fixture):
    assert list(fixture["hooks"]) == HOOK_LISTS[fixture["payload"]["tool_name"]]


def test_hook_lists_match_hooks_json(repo_root):
    import json

    groups = json.loads((repo_root / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    for tool, want in HOOK_LISTS.items():
        got = [h["command"].split("/hooks/")[1].split('"')[0] for g in groups
               if tool in g["matcher"].split("|") for h in g["hooks"]]
        assert got == want, tool
