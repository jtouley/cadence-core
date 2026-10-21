"""Ported scripts carry no external origin header, and core names no org, owner or predecessor term."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Phase 6 newly ported Python (not on the Phase 2–5 exclude list).
PHASE6_SCRIPTS = (
    "scripts/deferral_adjudication_check.py",
    "scripts/subagent_provenance.py",
    "scripts/prompt_shape.py",
    "hooks/guard_primary_context_artifacts.py",
)

ORIGIN_RE = re.compile(
    r"(?im)^(?:#\s*)?(?:ORIGIN|Origin)\s*:\s*.+",
)

# The public core holds no org, owner or predecessor name (core holds no owner value).
# An org rule goes in a pack, and a personal setup goes in a config layer outside this repo.
ORG_LEAK_RE = re.compile(
    r"(?i)\bskills/plan-to-pr\b|\bpsgen\b|\bscalar-lib\b|\bjolli\b|harness-benchmark|ship-feature"
    r"|read-memories|asd-ste100|writing-contract|\.cursor/skills/_shared"
)
SCANNED = ("*.md", "*.json", "*.sh", "scripts/**/*.py", "hooks/**/*", "shared/**/*.py", "references/**/*",
           "commands/*.md", "subagents/*.md", "skills/**/*.md", "docs/**/*.md", "packs/**/*", "adapters/*",
           "examples/**/*", "pi/*", "profiles/*", ".claude-plugin/*", ".github/**/*")


def test_phase6_ported_python_has_no_external_origin():
    leaked: list[str] = []
    for rel in PHASE6_SCRIPTS:
        path = ROOT / rel
        assert path.is_file(), f"script missing: {rel}"
        if ORIGIN_RE.search(path.read_text(encoding="utf-8")):
            leaked.append(rel)
    assert not leaked, "Python must not carry an external Origin/ORIGIN header:\n" + "\n".join(leaked)


def test_core_names_no_org_owner_or_predecessor():
    leaks: list[str] = []
    seen: set[Path] = set()
    for pattern in SCANNED:
        for path in sorted(ROOT.glob(pattern)):
            if path in seen or not path.is_file() or "__pycache__" in path.parts or path.suffix in (".png", ".pyc"):
                continue
            seen.add(path)
            for m in ORG_LEAK_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
                leaks.append(f"{path.relative_to(ROOT)}: {m.group(0)!r}")
    assert not leaks, "core names an org, owner or predecessor term:\n" + "\n".join(leaks)
