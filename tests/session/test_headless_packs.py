"""Headless.py walks only the rows of the core pack and of the packs that are on."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import FAIL, PASS, SENTINEL, make_skill_root

pytestmark = pytest.mark.integration

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_headless_runner import run_headless  # noqa: E402


def _packed_root(tmp_path: Path) -> Path:
    """A1 core passes, A2 (pack tracker) fails, A3 core fails, A4 core leaves a sentinel."""
    root = make_skill_root(
        tmp_path,
        [
            ("A1", "one", "python3 scripts/one.py", PASS),
            ("A2", "two", "python3 scripts/two.py", FAIL),
            ("A3", "three", "python3 scripts/three.py", FAIL),
            ("A4", "four", "python3 scripts/four.py", SENTINEL),
        ],
    )
    registry = json.loads((root / "stages.json").read_text())
    for row in registry["stages"]:
        row["pack"] = "tracker" if row["slug"] == "two" else "core"
    (root / "stages.json").write_text(json.dumps(registry))
    (root / "packs" / "tracker").mkdir(parents=True)
    (root / "packs" / "tracker" / "pack.json").write_text(json.dumps({"name": "tracker"}))
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _enable_tracker(workspace: Path) -> None:
    (workspace / ".cadence.json").write_text(json.dumps({"packs": {"append": ["tracker"]}}))


def test_list_skips_a_row_whose_pack_is_off(tmp_path, workspace):
    proc = run_headless(["list", "--json"], cwd=workspace, skill_root=_packed_root(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert [r["block"] for r in json.loads(proc.stdout)] == ["A1", "A3", "A4"]


def test_list_keeps_a_row_whose_pack_is_on(tmp_path, workspace):
    _enable_tracker(workspace)
    proc = run_headless(["list", "--json"], cwd=workspace, skill_root=_packed_root(tmp_path))
    assert [r["block"] for r in json.loads(proc.stdout)] == ["A1", "A2", "A3", "A4"]


def test_list_reads_the_project_file_of_the_workspace_flag(tmp_path, workspace):
    _enable_tracker(workspace)
    other = tmp_path / "other"
    other.mkdir()
    proc = run_headless(["list", "--json", "--workspace", str(workspace)], cwd=other,
                        skill_root=_packed_root(tmp_path))
    assert [r["block"] for r in json.loads(proc.stdout)] == ["A1", "A2", "A3", "A4"]


def test_next_never_opens_a_row_whose_pack_is_off(tmp_path, workspace):
    proc = run_headless(["next", "--json"], cwd=workspace, skill_root=_packed_root(tmp_path))
    assert proc.returncode == 1, proc.stderr
    out = json.loads(proc.stdout)
    assert out["next"]["block"] == "A3"
    assert [r["block"] for r in out["results"]] == ["A1", "A3"]


def test_next_opens_a_row_whose_pack_is_on(tmp_path, workspace):
    _enable_tracker(workspace)
    out = json.loads(run_headless(["next", "--json"], cwd=workspace, skill_root=_packed_root(tmp_path)).stdout)
    assert out["next"]["block"] == "A2"


def test_through_a_row_whose_pack_is_off_ends_at_the_row_before_it(tmp_path, workspace):
    proc = run_headless(["run", "--through", "two", "--json"], cwd=workspace, skill_root=_packed_root(tmp_path))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert [r["block"] for r in out["results"]] == ["A1"]
    assert out["done"] is True


def test_since_a_row_whose_pack_is_off_starts_at_the_row_before_it(tmp_path, workspace):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import headless

    report = headless.walk(_packed_root(tmp_path), workspace, since="A2", through="A3")
    assert [r["block"] for r in report["results"]] == ["A1", "A3"]
