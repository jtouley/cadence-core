"""The handoff reads `peers` from the hook's workspace, and an external peer gets no core contract."""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import project_config

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import handoff  # noqa: E402


def _project(tmp_path: Path, peers: dict) -> Path:
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    project_config(ws, {"peers": peers})
    return ws


def test_a_bound_peer_names_its_block_in_the_hook_workspace_only(tmp_path, monkeypatch):
    ws = _project(tmp_path, {"R1": "my-skills:pr-review"})
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".git").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    assert handoff.block_for_prompt("/my-skills:pr-review 12", ROOT, ws) == "R1"
    assert handoff.block_for_prompt("/my-skills:pr-review 12", ROOT) is None  # the cwd has no binding
    assert handoff.block_for_prompt("/pr-review-local 12", ROOT, ws) is None  # the bound block lost its old peer


def test_an_external_peer_has_an_external_contract(tmp_path):
    ws = _project(tmp_path, {"R1": "my-skills:pr-review"})
    assert handoff.resolve_contract("R1", ROOT, ws) == "external:my-skills:pr-review"


def test_a_bundled_peer_name_keeps_its_contract_file(tmp_path):
    ws = _project(tmp_path, {"P3": "/plan-review"})
    assert handoff.resolve_contract("P3", ROOT, ws) == "commands/plan-review.md"
