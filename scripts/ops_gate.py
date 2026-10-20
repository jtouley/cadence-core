#!/usr/bin/env python3
"""A3 — ops gate."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gate_common import cli, fail, ok, write_gate_breadcrumb  # noqa: E402

SLUG = "ops-gate"
# Markers, not prose: upper-case forms, or the hyphenated token `ops-only`.
_OPS_MARKERS = re.compile(r"CATALOG_STALE|\bOPS_ONLY\b|\bOPS HANDOFF\b|(?<![\w-])ops-only(?![\w-])")
_FIX_TYPE_OPS = re.compile(r"^\s*\**Fix type:?\**\s*ops\b", re.I | re.M)
# A declared but empty `Code paths:` line is the documented "empty code" abort.
_EMPTY_CODE_PATHS = re.compile(
    r"^[ \t]*\**Code paths:?\**[ \t]*(none)?[ \t]*$(?!\n[ \t]*[-*][ \t]+\S)", re.I | re.M
)
# SKILL.md A3: only a handoff or context-pack can route a task to ops.
_HANDOFF_DIRS = ("debug", "diagnostics")
_HANDOFF_NAME_PARTS = ("handoff", "context-pack")


def _is_handoff(md_file: Path, context_dir: Path) -> bool:
    rel = md_file.relative_to(context_dir)
    if rel.parts and rel.parts[0] in _HANDOFF_DIRS:
        return True
    name = md_file.name.lower().replace("_", "-")
    return any(part in name for part in _HANDOFF_NAME_PARTS)


def _ops_reason(text: str) -> str | None:
    if _FIX_TYPE_OPS.search(text):
        return "Fix type: ops"
    if _EMPTY_CODE_PATHS.search(text):
        return "empty Code paths"
    match = _OPS_MARKERS.search(text)
    return f"contains '{match.group(0)}'" if match else None


def _scan_for_ops_markers(context_dir: Path) -> list[str]:
    hits: list[str] = []
    if not context_dir.is_dir():
        return hits
    for md_file in sorted(context_dir.rglob("*.md")):
        if not _is_handoff(md_file, context_dir):
            continue
        try:
            reason = _ops_reason(md_file.read_text(encoding="utf-8"))
        except OSError:
            continue
        if reason:
            hits.append(f"{md_file.relative_to(context_dir.parent)}: {reason}")
    return hits


def run(context_dir: Path, *, check: bool = False) -> int:
    hits = _scan_for_ops_markers(context_dir)
    if hits:
        return fail(SLUG, hits, code=2)
    if not check:
        write_gate_breadcrumb(SLUG, context_dir=context_dir)
    return ok(SLUG)


if __name__ == "__main__":
    raise SystemExit(cli(run))
