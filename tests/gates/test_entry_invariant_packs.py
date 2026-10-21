"""P9 reads the artifact of a planning row (P6, P7, P8) only when its pack is on."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "stubs"))

from entry_invariant import check_execution_entry  # noqa: E402
from stub_pipeline import run_stub_alignment_through_entry  # noqa: E402
from conftest import enable_all_packs  # noqa: E402

OPT_IN_ARTIFACTS = ("adversarial", "benchmarks", "deferrals")


def _core_artifacts_only(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A toy run whose stub peers wrote every artifact; then the P6, P7 and P8 ones go."""
    worktree = tmp_path / "toy"
    shutil.copytree(ROOT / "tests" / "fixtures" / "toy_repo", worktree)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("CADENCE_HOME", str(ROOT))
    context = worktree / ".context"
    run_stub_alignment_through_entry(worktree=worktree, context_dir=context)
    for name in OPT_IN_ARTIFACTS:
        shutil.rmtree(context / name, ignore_errors=True)
    return worktree, context


def test_p9_passes_without_p6_p7_p8_artifacts_when_their_packs_are_off(tmp_path, monkeypatch):
    worktree, context = _core_artifacts_only(tmp_path, monkeypatch)
    assert check_execution_entry(context, skill_root=ROOT, worktree=worktree) == []


def test_p9_requires_p6_p7_p8_artifacts_when_their_packs_are_on(tmp_path, monkeypatch):
    worktree, context = _core_artifacts_only(tmp_path, monkeypatch)
    enable_all_packs(worktree)
    errors = "\n".join(check_execution_entry(context, skill_root=ROOT, worktree=worktree))
    for label in ("adversarial-review", "test-benchmark-plan", "deferral-register"):
        assert label in errors


def test_p9_still_requires_the_plan_review_in_the_default_config(tmp_path, monkeypatch):
    worktree, context = _core_artifacts_only(tmp_path, monkeypatch)
    shutil.rmtree(context / "reviews")
    errors = "\n".join(check_execution_entry(context, skill_root=ROOT, worktree=worktree))
    assert "plan-review" in errors
