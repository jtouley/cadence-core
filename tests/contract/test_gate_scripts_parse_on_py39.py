"""A2 and install.sh run under the system python3, which can be 3.9.

The suite runs under a newer Python, so a 3.10+ syntax error would pass every
other test. Each module of the A2 import chain must parse with the 3.9 grammar.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
A2_CHAIN = (
    "scripts/peer_shadows.py",
    "scripts/preflight.py",
    "scripts/publish_peers.py",
    "scripts/gate_common.py",
    "scripts/artifact_requirements.py",
    "scripts/peer_models.py",
    "shared/scripts/session_store.py",
    "shared/scripts/context_root.py",
)


@pytest.mark.parametrize("rel", A2_CHAIN)
def test_parses_with_the_python_39_grammar(rel):
    source = (ROOT / rel).read_text(encoding="utf-8")
    ast.parse(source, filename=rel, feature_version=(3, 9))


def test_peer_shadows_postpones_annotations():
    lines = (ROOT / "scripts" / "peer_shadows.py").read_text(encoding="utf-8").splitlines()
    assert "from __future__ import annotations" in lines
