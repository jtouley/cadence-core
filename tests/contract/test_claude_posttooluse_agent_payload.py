"""The Claude Code Agent hook chain joins a call to its subagent (gh-66 B-1, MF-2).

The verdict stage check joins three hook events on host ids: the orchestrator's
PreToolUse Agent call, its PostToolUse, and a tool call inside the subagent.
These fixtures are recorded from a real host (see each file's `host_version` and
`method`). Do not edit them by hand to make this test pass. A false identity is
a hard stop for the cd-66 design.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1] / "fixtures" / "host"
CHAINS = {"completed": "claude_agent_chain_completed.json", "async_launched": "claude_agent_chain_async.json"}


def _chain(name: str) -> dict:
    return json.loads((HOST / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("status", "name"), sorted(CHAINS.items()))
def test_the_chain_joins_the_call_to_its_subagent(status, name):
    chain = _chain(name)
    pre, post, inner = chain["pre"], chain["post"], chain["inner_write"]
    assert chain["host_version"]
    assert post["hook_event_name"] == "PostToolUse" and post["tool_name"] == "Agent"
    assert isinstance(pre["tool_use_id"], str) and pre["tool_use_id"]
    assert pre["tool_use_id"] == post["tool_use_id"]
    agent = post["tool_response"]["agentId"]
    assert isinstance(agent, str) and agent
    assert agent == inner["agent_id"]
    assert post["tool_response"]["status"] == status


@pytest.mark.parametrize("name", sorted(CHAINS.values()))
def test_the_orchestrator_events_have_no_agent_id(name):
    chain = _chain(name)
    assert "agent_id" not in chain["pre"]
    assert "agent_id" not in chain["post"]
