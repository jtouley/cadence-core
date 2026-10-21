"""The stage order of a run: core rows, the rows of the packs that are on, pack and config overlays, peer bindings.

load_stages() keeps the core rows and the rows of the packs that `packs` turns on, in file order.
overlays insert, replace and remove blocks by id; P9, R1 and R5 cannot lose their gate; `peers`
binds another peer to a block.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import install_config, write_files

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pack_loader  # noqa: E402
from load_stages import OverlayError, load_registry, load_stages, resolve_stages, stage_slugs  # noqa: E402

ROWS = (("A1", "one", "core", None), ("A2", "two", "tracker", None), ("A3", "three", None, None),
        ("A4", "four", "preflight", None), ("P9", "entry", "core", None), ("R1", "review", "core", "/pr-review-local"))


def _skill(tmp_path: Path, packs: list[str] | None = None) -> Path:
    root = tmp_path / "skill"
    root.mkdir()
    stages = []
    for block, slug, pack, command in ROWS:
        row = {"slug": slug, "block": block, "phase": "alignment", "gate_script": f"scripts/{slug}.py",
               "command": command or f"python3 scripts/{slug}.py"}
        if pack is not None:
            row["pack"] = pack
        stages.append(row)
    (root / "stages.json").write_text(json.dumps({"version": 1, "stages": stages}), encoding="utf-8")
    for name in ("tracker", "preflight"):
        write_files(root / "packs" / name, {"pack.json": json.dumps({"name": name, "phases": [], "rules": []})})
    return install_config(root, packs=packs or [])


def _workspace(tmp_path: Path, project: object = None) -> Path:
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    if project is not None:
        text = project if isinstance(project, str) else json.dumps(project)
        (ws / ".cadence.json").write_text(text, encoding="utf-8")
    return ws


# --- packs ----------------------------------------------------------------------------------------------


def test_no_pack_on_keeps_the_core_rows_and_the_rows_with_no_pack(tmp_path):
    assert stage_slugs(_skill(tmp_path), _workspace(tmp_path)) == ["one", "three", "entry", "review"]


def test_a_pack_that_is_on_keeps_its_rows_in_file_order(tmp_path):
    skill = _skill(tmp_path, packs=["preflight", "tracker"])
    assert stage_slugs(skill, _workspace(tmp_path)) == ["one", "two", "three", "four", "entry", "review"]


def test_a_pack_in_the_project_layer_keeps_its_rows(tmp_path):
    ws = _workspace(tmp_path, {"packs": {"append": ["tracker"]}})
    assert stage_slugs(_skill(tmp_path), ws) == ["one", "two", "three", "entry", "review"]


def test_load_registry_returns_every_row(tmp_path):
    assert [r["slug"] for r in load_registry(_skill(tmp_path))["stages"]] == ["one", "two", "three", "four", "entry", "review"]


@pytest.mark.parametrize("bad", ["{not json", json.dumps({"enabled_packs_extra": ["tracker"]}),
                                 json.dumps({"packs": {"append": [1]}}), json.dumps({"packs": "tracker"})])
def test_a_bad_project_layer_fails_closed(tmp_path, bad):
    with pytest.raises(ValueError):
        load_stages(_skill(tmp_path), _workspace(tmp_path, bad))


def test_get_stage_finds_a_row_whose_pack_is_off(tmp_path):
    from load_stages import get_stage

    assert get_stage("two", _skill(tmp_path))["pack"] == "tracker"


@pytest.mark.parametrize("where", ["defaults", "project"])
def test_a_pack_name_with_no_manifest_fails_closed(tmp_path, where):
    # A typo such as "trackr" must not leave a pack off in silence.
    skill = _skill(tmp_path, packs=["trackr"] if where == "defaults" else [])
    ws = _workspace(tmp_path, {"packs": {"append": ["trackr"]}} if where == "project" else None)
    with pytest.raises(ValueError, match="trackr"):
        load_stages(skill, ws)


def test_a_rules_pack_with_a_manifest_and_no_row_is_a_known_name(tmp_path):
    skill = _skill(tmp_path, packs=["rules"])
    write_files(skill / "packs" / "rules", {"pack.json": json.dumps({"name": "rules"})})
    assert stage_slugs(skill, _workspace(tmp_path)) == ["one", "three", "entry", "review"]


# --- overlays -------------------------------------------------------------------------------------------

NEW = {"block": "A9", "slug": "house-check", "phase": "alignment", "gate_script": "scripts/one.py", "command": "x"}


@pytest.mark.parametrize("op, want", [
    ({"op": "insert_after", "block": "A1", "row": NEW}, ["one", "house-check", "three", "entry", "review"]),
    ({"op": "insert_before", "block": "A1", "row": NEW}, ["house-check", "one", "three", "entry", "review"]),
    ({"op": "remove", "block": "A3"}, ["one", "entry", "review"]),
    ({"op": "replace", "block": "A3", "row": {"slug": "three-b"}}, ["one", "three-b", "entry", "review"]),
])
def test_an_overlay_changes_the_order_by_block_id(tmp_path, op, want):
    ws = _workspace(tmp_path, {"stages": {"overlays": [op]}})
    rows, removed = resolve_stages(_skill(tmp_path), ws)
    assert [r["slug"] for r in rows] == want
    assert removed == (["A3"] if op["op"] == "remove" else [])


def test_overlays_of_each_layer_apply_in_layer_order(tmp_path):
    skill = install_config(_skill(tmp_path), stages={"overlays": [{"op": "insert_after", "block": "A1", "row": NEW}]})
    ws = _workspace(tmp_path, {"stages": {"overlays": [{"op": "remove", "block": "A9"}]}})
    assert stage_slugs(skill, ws) == ["one", "three", "entry", "review"]


@pytest.mark.parametrize("op, needle", [
    ({"op": "remove", "block": "P9"}, "closure evidence"),
    ({"op": "remove", "block": "R1"}, "closure evidence"),
    ({"op": "replace", "block": "R1", "row": {"gate_script": "scripts/one.py"}}, "keeps its gate"),
    ({"op": "replace", "block": "A3", "row": {"block": "A8"}}, "keeps the block id"),
    ({"op": "remove", "block": "Z1"}, "no block"),
    ({"op": "insert_after", "block": "A1", "row": {"block": "A9"}}, "the row needs"),
    ({"op": "insert_after", "block": "A1", "row": {**NEW, "slug": "one"}}, "is in the stage order already"),
    ({"op": "insert_after", "block": "A1"}, "needs a `row`"),
])
def test_an_overlay_that_would_lose_evidence_or_break_the_order_fails(tmp_path, op, needle):
    ws = _workspace(tmp_path, {"stages": {"overlays": [op]}})
    with pytest.raises(OverlayError, match=needle):
        load_stages(_skill(tmp_path), ws)


def test_a_protected_block_can_change_its_peer_text(tmp_path):
    ws = _workspace(tmp_path, {"stages": {"overlays": [{"op": "replace", "block": "R1", "row": {"description": "ours"}}]}})
    [r1] = [r for r in load_stages(_skill(tmp_path), ws) if r["block"] == "R1"]
    assert r1["description"] == "ours" and r1["gate_script"] == "scripts/review.py"


def test_a_pack_inserts_its_own_row_with_its_gate_path(tmp_path):
    skill = _skill(tmp_path, packs=["house"])
    write_files(skill / "packs" / "house", {
        "gate.py": "print('ok')\n",
        "pack.json": json.dumps({"name": "house", "manifest_version": 2, "version": "1.0.0", "stages": [
            {"op": "insert_after", "block": "A1", "row": {**NEW, "gate_script": "gate.py --check"}}]}),
    })
    rows = load_stages(skill, _workspace(tmp_path))
    [row] = [r for r in rows if r["block"] == "A9"]
    assert row["pack"] == "house" and row["gate_script"] == f"{skill / 'packs' / 'house' / 'gate.py'} --check"
    assert [r["block"] for r in rows][:2] == ["A1", "A9"]


def test_a_pack_row_and_a_config_overlay_compose(tmp_path):
    skill = _skill(tmp_path, packs=["house"])
    write_files(skill / "packs" / "house", {"pack.json": json.dumps({
        "name": "house", "manifest_version": 2, "version": "1.0.0",
        "stages": [{"op": "insert_after", "block": "A1", "row": NEW}]})})
    ws = _workspace(tmp_path, {"stages": {"overlays": [{"op": "remove", "block": "A9"}]}})
    rows, removed = resolve_stages(skill, ws)
    assert "A9" not in [r["block"] for r in rows] and removed == ["A9"]
    assert pack_loader.find_pack("house", skill, ws).stages


# --- peer bindings --------------------------------------------------------------------------------------


@pytest.mark.parametrize("peer, command", [("my-skills:pr-review", "/my-skills:pr-review"), ("/team-review", "/team-review")])
def test_peers_binds_another_peer_to_a_block(tmp_path, peer, command):
    ws = _workspace(tmp_path, {"peers": {"R1": peer}})
    [r1] = [r for r in load_stages(_skill(tmp_path), ws) if r["block"] == "R1"]
    assert r1["command"] == command and r1["peer_binding"] == peer
    assert r1["gate_script"] == "scripts/review.py"  # the gate, and so the evidence, stays


@pytest.mark.parametrize("peers, needle", [({"A1": "x:y"}, "not a peer block"), ({"E9": "x:y"}, "not a peer block"),
                                           ({"R1": "two words"}, "not a peer name")])
def test_a_bad_peer_binding_fails_closed(tmp_path, peers, needle):
    with pytest.raises(OverlayError, match=needle):
        load_stages(_skill(tmp_path), _workspace(tmp_path, {"peers": peers}))


def test_the_peer_block_key_must_look_like_a_block(tmp_path):
    with pytest.raises(ValueError, match="peers.r1"):
        load_stages(_skill(tmp_path), _workspace(tmp_path, {"peers": {"r1": "x:y"}}))


def test_a_project_overlay_runs_a_gate_script_next_to_its_config(tmp_path):
    ws = _workspace(tmp_path, {"stages": {"overlays": [
        {"op": "insert_after", "block": "A1", "row": {**NEW, "gate_script": ".cadence/gates/house.py --check"}}]}})
    write_files(ws, {".cadence/gates/house.py": "print('ok')\n"})
    [row] = [r for r in load_stages(_skill(tmp_path), ws) if r["block"] == "A9"]
    assert row["gate_script"] == f"{ws.resolve() / '.cadence' / 'gates' / 'house.py'} --check"


def test_an_overlay_gate_that_is_not_next_to_its_config_stays_relative_to_the_skill_root(tmp_path):
    ws = _workspace(tmp_path, {"stages": {"overlays": [{"op": "insert_after", "block": "A1", "row": NEW}]}})
    [row] = [r for r in load_stages(_skill(tmp_path), ws) if r["block"] == "A9"]
    assert row["gate_script"] == "scripts/one.py"
