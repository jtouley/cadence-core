"""PEER_MODEL_SLUGS and SLUG_FOR_STAGE agree with stages.json and the lean-delegation callers.

A new peer stage or a new gate that checks a delegation must be added to the
peer_models lists, or this test fails.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import artifact_requirements  # noqa: E402
import gate_common  # noqa: E402
import peer_models  # noqa: E402

STAGES = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))
ROWS = STAGES["stages"] if isinstance(STAGES, dict) else STAGES
BY_SLUG = {row["slug"]: row for row in ROWS}


def test_each_peer_model_slug_is_a_slash_stage():
    for slug in peer_models.PEER_MODEL_SLUGS:
        assert slug in BY_SLUG, slug
        assert str(BY_SLUG[slug].get("command", "")).startswith("/"), slug


def test_each_other_slash_stage_is_e1_e4_or_p4():
    others = {row["block"] for row in ROWS
              if str(row.get("command", "")).startswith("/") and row["slug"] not in peer_models.PEER_MODEL_SLUGS}
    assert others == {"E1", "E4", "P4"}


def test_each_lean_delegation_stage_maps_to_a_peer_model_slug():
    names = {spec.get("delegation_stage", spec["label"]) for spec in gate_common.ARTIFACT_GATES.values()}
    names |= {spec.get("delegation_stage", spec["label"]) for spec in artifact_requirements._EXECUTION_ARTIFACTS
              if spec["label"] != "deferral-register"}
    names |= {"plan-create", "test-benchmark-plan", "test-benchmark-pr"}
    for name in names:
        assert peer_models.SLUG_FOR_STAGE.get(name, name) in peer_models.PEER_MODEL_SLUGS, name


def test_no_new_lean_delegation_caller_hides():
    calls = 0
    for path in (ROOT / "scripts").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        calls += len(re.findall(r"(?<!def )require_lean_delegation\(", text))
    assert calls == 4
