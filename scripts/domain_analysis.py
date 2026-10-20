#!/usr/bin/env python3
"""P2 domain-analysis — deterministic pack trigger match (Gap 6).

Writes ``.context/domain-analysis.json``. Zero enabled packs → empty selection.

ORIGIN: original for cadence
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pack_loader import enabled_packs, match_triggers  # noqa: E402

SKILL_ROOT = Path(
    os.environ.get("CADENCE_HOME")
    or os.environ.get("CADENCE_SKILL_ROOT")
    or Path(__file__).resolve().parent.parent
)


def _list_worktree_paths(worktree: Path) -> list[str]:
    paths: list[str] = []
    if not worktree.exists():
        return paths
    for p in worktree.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts:
            paths.append(str(p.relative_to(worktree)).replace("\\", "/"))
    return paths


def run_domain_analysis(
    *,
    worktree: Path,
    context_dir: Path,
    skill_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(skill_root) if skill_root is not None else SKILL_ROOT
    packs = enabled_packs(root, worktree)
    changed = _list_worktree_paths(worktree)

    matched: list[dict[str, Any]] = []
    selected: list[str] = []
    for pack in packs:
        hits = match_triggers(pack, changed)
        if hits:
            matched.extend(hits)
            selected.append(pack.name)
        elif not pack.triggers:
            # No triggers declared → always select when enabled
            selected.append(pack.name)

    # Deduplicate while preserving order
    seen: set[str] = set()
    selected_unique: list[str] = []
    for name in selected:
        if name not in seen:
            seen.add(name)
            selected_unique.append(name)

    result: dict[str, Any] = {
        "RUN_BY_SKILL": "cadence",
        "stage": "domain-analysis",
        "selected_packs": selected_unique,
        "matched_triggers": matched,
        "enabled_packs": [p.name for p in packs],
        "scanned_paths": len(changed),
    }
    context_dir.mkdir(parents=True, exist_ok=True)
    out = context_dir / "domain-analysis.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Cadence domain-analysis (P2)")
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument(
        "--context",
        type=Path,
        default=None,
        help="Context dir (default: <worktree>/.context)",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    context = args.context or (args.worktree / ".context")
    result = run_domain_analysis(worktree=args.worktree, context_dir=context)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
