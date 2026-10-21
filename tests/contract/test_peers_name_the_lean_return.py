"""Every delegated peer that closes with C.O.R.E. also names the one-line return.

A peer that obeys its own command must not be held by the SubagentStop return check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_BUNDLE = json.loads((ROOT / "references" / "bundled-peers.json").read_text(encoding="utf-8"))
PEER_DOCS = sorted({row["path"] for row in _BUNDLE["peers"] + _BUNDLE["supporting_commands"]})


@pytest.mark.parametrize("rel", PEER_DOCS)
def test_a_peer_that_closes_with_core_names_the_lean_return(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    if "C.O.R.E" in text:
        assert "one-line return" in text, f"{rel} closes with C.O.R.E. but does not name the delegated return"


def test_the_peer_contract_names_the_lean_return():
    assert "one-line return" in (ROOT / "references" / "peer-contract.md").read_text(encoding="utf-8")
