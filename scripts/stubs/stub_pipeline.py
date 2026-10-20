#!/usr/bin/env python3
"""Stub peer runner for fixture evals (honest Alignment → P9).

Writes minimal artifacts with RUN_BY markers so entry_invariant can clear
without invoking real vibe-test / plan-create / adversarial peers.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from load_stages import load_registry  # noqa: E402

# Stages through entry-invariant (P9) — mechanical stub peers only.
_THROUGH_ENTRY = (
    "activate-session",
    "preflight",
    "ops-gate",
    "ticket-gate",
    "validate",
    "plan-create",
    "domain-analysis",
    "plan-review",
    "plan-update",
    "architecture-review-plan",
    "adversarial-review",
    "test-benchmark-plan",
    "deferral-register",
    "entry-invariant",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


_LEAN_BY_SLUG = {
    "validate": ("vibe-test", "/vibe-test {task_id}"),
    "plan-create": ("plan-create", "/plan-create {task_id}"),
    "plan-review": ("plan-review", "/plan-review plans/{task_id}.plan.md"),
    "adversarial-review": (
        "de-adversarial-reviewer",
        "/de-adversarial-reviewer plan-gate plans/{task_id}.plan.md",
    ),
    "test-benchmark-plan": (
        "test-benchmark-plan",
        "/test-benchmark plan benchmarks/{task_id}_plan.json",
    ),
}


def _write_lean_delegation(context_dir: Path, task_id: str, slug: str) -> None:
    spec = _LEAN_BY_SLUG.get(slug)
    if spec is None:
        return
    from prompt_shape import write_delegation_record

    stage, template = spec
    write_delegation_record(
        context_dir,
        run_id=f"stub-{task_id}",
        stage=stage,
        command=template.format(task_id=task_id),
        source="stub",
    )


def _artifact_for(slug: str, context_dir: Path, task_id: str) -> None:
    """Write a stub artifact matching manifest expectations for required peers."""
    stamp = _now()
    if slug == "activate-session":
        session = {
            "active_skill": "cadence",
            "task_id": task_id,
            "pipeline_run_id": f"stub-{task_id}",
            "resume_block": "entry-invariant",
            "activated_at": stamp,
        }
        _write(context_dir / "cadence-session.json", json.dumps(session, indent=2) + "\n")
        return
    if slug == "plan-create":
        body = (
            f"# Plan: {task_id}\n\n"
            f"**RUN_BY_SKILL:** plan-create\n"
            f"**Date:** {stamp}\n\n"
            "Stub plan for cadence fixture pipeline.\n"
        )
        _write(context_dir / "plans" / f"{task_id}.plan.md", body)
        return
    if slug == "plan-review":
        body = (
            f"# Plan review: {task_id}\n\n"
            f"**RUN_BY_COMMAND:** plan-review\n"
            f"**Verdict:** READY TO EXECUTE\n"
            f"**Date:** {stamp}\n"
        )
        _write(context_dir / "reviews" / f"plan_{task_id}.md", body)
        return
    if slug == "adversarial-review":
        body = (
            f"# Adversarial Review: {task_id}\n\n"
            f"RUN_BY_SKILL: de-adversarial-reviewer\n"
            f"Mode: plan-gate\n\n"
            f"**Verdict:** APPROVE\n"
        )
        _write(context_dir / "adversarial" / f"adversarial_{task_id}.md", body)
        return
    if slug == "validate":
        body = (
            f"## Vibe Test Report: {task_id}\n\n"
            f"**RUN_BY_SKILL:** vibe-test\n"
            f"**Date:** {stamp}\n"
        )
        _write(context_dir / "vibetest" / f"{task_id}_stub.md", body)
        return
    if slug == "test-benchmark-plan":
        payload = {
            "RUN_BY_SKILL": "test-benchmark",
            "phase": "plan",
            "task_id": task_id,
            "verdict": "pass",
        }
        _write(
            context_dir / "benchmarks" / f"{task_id}_plan.json",
            json.dumps(payload, indent=2) + "\n",
        )
        return
    if slug == "deferral-register":
        body = f"# Deferral register: {task_id}\n\n## Approved deferrals\n\nZero approved deferrals.\n"
        _write(context_dir / "deferrals" / f"{task_id}_deferral-register.md", body)
        return
    if slug == "domain-analysis":
        payload = {
            "RUN_BY_SCRIPT": "domain_analysis.py",
            "task_id": task_id,
            "selected_packs": [],
        }
        _write(context_dir / "domain-analysis.json", json.dumps(payload, indent=2) + "\n")
        return
    # Generic gate breadcrumb for remaining alignment/planning rows
    payload = {
        "slug": slug,
        "RUN_BY_STUB": "stub_pipeline",
        "at": stamp,
        "task_id": task_id,
    }
    _write(
        context_dir / "gates" / f"{slug}.json",
        json.dumps(payload, indent=2) + "\n",
    )


def run_stub_alignment_through_entry(
    *,
    worktree: Path,
    context_dir: Path,
    task_id: str = "toy-fixture",
    skill_root: Path | None = None,
) -> dict[str, Any]:
    """Execute stub peers from A1 through P9; return a machine-readable report."""
    root = Path(skill_root) if skill_root is not None else Path(
        __file__
    ).resolve().parent.parent.parent
    registry = {row["slug"]: row for row in load_registry(root)["stages"]}  # every row (ADR-0007)
    executed: list[str] = []
    context_dir = Path(context_dir)
    context_dir.mkdir(parents=True, exist_ok=True)

    for slug in _THROUGH_ENTRY:
        if slug not in registry:
            raise KeyError(f"stages.json missing stub target slug: {slug}")
        _artifact_for(slug, context_dir, task_id)
        _write_lean_delegation(context_dir, task_id, slug)
        executed.append(slug)

    return {
        "reached": "entry-invariant",
        "slugs_executed": executed,
        "worktree": str(worktree),
        "context_dir": str(context_dir),
        "task_id": task_id,
    }


__all__ = ["run_stub_alignment_through_entry"]
