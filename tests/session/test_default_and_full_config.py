"""The five-step core against the real stages.json: the default config visits 12 blocks, the full config 25."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import activated_repo, enable_all_packs, run_py, stage_packs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from headless import load_rows, resolve_through  # noqa: E402
from load_stages import load_registry, stage_slugs  # noqa: E402
from pack_loader import enabled_packs  # noqa: E402

CORE = [
    "activate-session", "plan-create", "plan-review", "plan-update", "entry-invariant", "implement",
    "draft-pr", "pr-review-local", "test-benchmark-pr", "cognitive-complexity", "closure", "session-close",
]
PACKS = {
    "preflight": ["preflight", "ops-gate"],
    "tracker": ["ticket-gate", "pr-comment"],
    "spec-review": ["validate", "adversarial-review"],
    "architecture": ["domain-analysis", "architecture-review-plan", "architecture-review-impl"],
    "test-plan": ["test-benchmark-plan"],
    "deferrals": ["deferral-register", "impl-artifact", "final-consolidate"],
}
ALL = [r["slug"] for r in load_registry(ROOT)["stages"]]


@pytest.fixture(params=["default", "full"])
def config(request, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    if request.param == "full":
        enable_all_packs(ws)
    return request.param, ws


def test_the_pack_of_each_row_is_the_adr_table():
    rows = load_registry(ROOT)["stages"]
    assert [r["slug"] for r in rows if r["pack"] == "core"] == CORE
    for pack, slugs in PACKS.items():
        assert [r["slug"] for r in rows if r["pack"] == pack] == slugs
    assert len(rows) == 25


def test_the_defaults_turn_on_no_pack_and_this_repo_turns_on_every_pack():
    assert json.loads((ROOT / "cadence.defaults.json").read_text())["packs"] == []
    assert sorted(json.loads((ROOT / ".cadence.json").read_text())["packs"]) == sorted(PACKS)


def test_the_strict_profile_turns_on_every_stage_pack():
    assert sorted(json.loads((ROOT / "profiles" / "strict.json").read_text())["packs"]) == sorted(PACKS)


def test_each_stage_pack_has_a_manifest_that_the_pack_loader_reads(tmp_path):
    assert sorted(stage_packs()) == sorted(PACKS)
    enable_all_packs(tmp_path)
    packs = enabled_packs(ROOT, tmp_path)
    assert [p.name for p in packs] == stage_packs()
    assert all(not p.has_gate and p.phases == [] for p in packs)


def test_load_stages_default_is_core_and_full_is_every_row(config):
    name, ws = config
    assert stage_slugs(ROOT, ws) == (CORE if name == "default" else ALL)


def test_headless_list_prints_12_or_25_rows(config):
    name, ws = config
    proc = run_py("scripts/headless.py", "list", cwd=ws)
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.splitlines()) == (12 if name == "default" else 25)


def test_headless_next_in_an_activated_repo(tmp_path):
    default = activated_repo(tmp_path / "d")
    out = json.loads(run_py("scripts/headless.py", "next", "--json", cwd=default).stdout)
    assert [r["block"] for r in out["results"]] == ["A1", "P1"]
    full = activated_repo(tmp_path / "f")
    enable_all_packs(full)
    out = json.loads(run_py("scripts/headless.py", "next", "--json", cwd=full).stdout)
    assert [r["block"] for r in out["results"]][:2] == ["A1", "A2"]


def test_the_deactivate_walk_through_r6_ends_at_r5_when_the_tracker_pack_is_off(config):
    """activate_session.closure_done walks implement..pr-comment (A1 --deactivate)."""
    name, ws = config
    rows = load_rows(ROOT, ws)
    last = rows[resolve_through(rows, "pr-comment", ROOT)]["slug"]
    assert last == ("closure" if name == "default" else "pr-comment")


def test_r5_passes_with_no_ledger_when_r4_is_off(tmp_path):
    """R5 reads the findings ledger only; R4 (deferrals pack) writes no file that R5 needs."""
    repo = activated_repo(tmp_path / "r")
    proc = run_py("scripts/closure.py", "--check", cwd=repo)
    assert proc.returncode == 0, proc.stderr


def test_pipeline_status_lists_only_the_rows_that_are_on(config, monkeypatch):
    import pipeline_status

    name, ws = config
    monkeypatch.chdir(ws)
    status = pipeline_status.build_status(None)
    assert [a.slug for a in status.artifacts] == (CORE if name == "default" else ALL)
