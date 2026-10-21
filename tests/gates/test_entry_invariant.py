"""entry_invariant named fail-closed branches."""

from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

from conftest import install_config, make_session, project_config

ROOT = Path(__file__).resolve().parents[2]


def _write_skill(tmp_path: Path, *, stages: list[dict] | None = None, packs: list[str] | None = None) -> Path:
    """A skill root with the core config files. `packs` goes to the project layer of the worktree tmp_path."""
    skill = tmp_path / "skill"
    skill.mkdir()
    if stages is None:
        shutil.copy(ROOT / "stages.json", skill / "stages.json")
    else:
        (skill / "stages.json").write_text(
            json.dumps({"version": 1, "stages": stages}),
            encoding="utf-8",
        )
    install_config(skill)
    if packs is not None:
        project_config(tmp_path, {"packs": packs})
    return skill


def _alignment_stages(*slugs: str) -> list[dict]:
    return [
        {"slug": slug, "block": slug, "phase": "alignment", "command": "true"}
        for slug in slugs
    ]


def test_entry_invariant_missing_pack_dir_records_error(scripts_path, tmp_path):
    from entry_invariant import check_execution_entry

    skill = _write_skill(tmp_path, packs=["ghost"])
    context = tmp_path / ".context"
    make_session(context)
    errors = check_execution_entry(context, skill_root=skill, worktree=tmp_path)
    assert errors
    assert any("ghost" in item and "missing" in item for item in errors)
    assert not (skill / "packs" / "ghost").exists()


def test_entry_invariant_session_vanished_fails(scripts_path, tmp_path, monkeypatch):
    import entry_invariant

    monkeypatch.chdir(tmp_path)
    context = tmp_path / ".context"
    context.mkdir()
    ok_path = context / "gates" / "entry-invariant-run-1.ok"  # The run's own marker
    ok_path.parent.mkdir(parents=True)
    ok_path.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr(entry_invariant, "check_execution_entry", lambda *_a, **_k: [])
    # P9 resolves the run with run_for, once before the check and once to write.
    runs = iter([{"pipeline_run_id": "run-1", "task_id": "t"}, None])
    monkeypatch.setattr(entry_invariant, "run_for", lambda *_a, **_k: next(runs))
    monkeypatch.setattr(sys, "argv", ["entry_invariant.py"])
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    code = entry_invariant.main()
    assert code == 1
    assert "session vanished" in err.getvalue()
    assert not ok_path.exists()


def test_entry_invariant_registry_missing_required_slug_fails(scripts_path, tmp_path):
    from entry_invariant import check_execution_entry

    stages = _alignment_stages("activate-session", "plan-review") + [
        {"slug": "implement", "block": "E1", "phase": "execution", "command": "true"}
    ]
    skill = _write_skill(tmp_path, stages=stages, packs=[])
    context = tmp_path / ".context"
    make_session(context)
    errors = check_execution_entry(context, skill_root=skill, worktree=tmp_path)
    assert any("registry missing required slug" in item for item in errors)
    assert any("plan-create" in item for item in errors)
    assert errors


def test_entry_invariant_registry_missing_execution_phase_fails(scripts_path, tmp_path):
    from entry_invariant import check_execution_entry

    stages = _alignment_stages(
        "activate-session",
        "plan-create",
        "plan-review",
        "adversarial-review",
        "test-benchmark-plan",
        "deferral-register",
    )
    skill = _write_skill(tmp_path, stages=stages, packs=[])
    context = tmp_path / ".context"
    make_session(context)
    errors = check_execution_entry(context, skill_root=skill, worktree=tmp_path)
    assert any("registry missing execution phase" in item for item in errors)
    assert errors
    assert (skill / "stages.json").is_file()


def test_entry_invariant_pack_gate_script_missing_records_error(scripts_path, tmp_path):
    from entry_invariant import check_execution_entry

    skill = _write_skill(tmp_path, packs=["broken"])
    pack_dir = skill / "packs" / "broken"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.json").write_text(
        json.dumps(
            {
                "name": "broken",
                "phases": ["planning"],
                "gate_script": "missing.py",
                "rules": [],
            }
        ),
        encoding="utf-8",
    )
    context = tmp_path / ".context"
    make_session(context)
    errors = check_execution_entry(context, skill_root=skill, worktree=tmp_path)
    assert errors
    assert any("gate script missing" in item for item in errors)
    assert not (pack_dir / "missing.py").exists()
