"""A3 ops_gate: fail closed on an ops handoff marker; read only handoffs and context packs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from ops_gate import run  # noqa: E402


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# --- ops_gate: ops_gate.run fail-closed on handoff markers; pass when none present.

@pytest.mark.parametrize(
    "marker",
    ["CATALOG_STALE", "OPS_ONLY", "OPS HANDOFF"],
)
def test_ops_gate_marker_in_handoff_fails(scripts_path, tmp_path, marker, capsys):
    context = tmp_path / ".context"
    _write(context, "debug/deploy-handoff.md", f"{marker} present\n")
    code = run(context)
    err = capsys.readouterr().err
    assert code == 2
    assert f"contains '{marker}'" in err
    assert not (context / "gates" / "ops-gate.json").exists()


def test_ops_gate_marker_outside_handoff_passes(scripts_path, tmp_path):
    context = tmp_path / ".context"
    _write(context, "note.md", "CATALOG_STALE present\n")
    code = run(context)
    assert code == 0
    assert (context / "gates" / "ops-gate.json").is_file()


def test_ops_gate_no_marker_passes(scripts_path, tmp_path):
    context = tmp_path / ".context"
    _write(context, "note.md", "# clean note\n")
    code = run(context)
    assert code == 0
    breadcrumb = context / "gates" / "ops-gate.json"
    assert breadcrumb.is_file()
    check_code = run(context, check=True)
    assert check_code == 0


# --- ops_gate_scans_only_handoffs: A3 reads handoffs / context-packs only, and honours `Fix type: ops`.

def test_ops_gate_ignores_markers_outside_handoffs(tmp_path):
    _write(tmp_path, "plans/a.plan.md", "This change is code, not ops-only.\n")
    _write(tmp_path, "reviews/plan_x.md", "e.g. a CATALOG_STALE handoff would abort\n")
    assert run(tmp_path, check=True) == 0


@pytest.mark.parametrize("rel, body, code", [
    pytest.param("debug/deploy-handoff.md", "# Handoff\nFix type: ops\n", 2,
                 id="ops_gate_aborts_on_fix_type_ops_handoff"),
    pytest.param("debug/deploy-handoff.md", "# Handoff\nFix type: code\nCode paths:\n", 2,
                 id="ops_gate_aborts_on_empty_code_paths"),
    pytest.param("debug/notes.md", "tried restarting the worker\n", 0, id="ops_gate_ignores_free_form_debug_notes"),
    pytest.param("debug/deploy-handoff.md", "Fix type: code\nCode paths:\n- src/a.py\n", 0,
                 id="ops_gate_accepts_code_paths_as_following_list"),
])
def test_ops_gate_reads_the_fix_type_of_a_handoff(tmp_path, rel, body, code):
    _write(tmp_path, rel, body)
    assert run(tmp_path, check=True) == code
