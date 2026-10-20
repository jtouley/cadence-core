"""Git tracks the product tree only. A new top-level path is a deliberate change."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

PRODUCT = {
    ".cadence.json", ".claude-plugin/", ".claude/", ".github/", ".gitignore",
    "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "README.md", "SECURITY.md", "SKILL.md",
    "adapters/", "assets/", "cadence.defaults.json", "commands/", "docs/", "examples/", "hooks/",
    "install.sh", "packs/", "pi/", "profiles/", "pyproject.toml", "references/", "scripts/", "shared/",
    "skills/", "stages.json", "subagents/", "tests/", "uv.lock",
}

# Local evidence that git does not track.
IGNORED_LAB = (".context/", "mutants/", ".cursor/plans/")


def _top_level_tracked() -> set[str]:
    proc = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    out = proc.stdout
    return {p.split("/")[0] + "/" if "/" in p else p for p in out.splitlines()}


def test_tracked_top_level_paths_are_the_product_list():
    assert _top_level_tracked() == PRODUCT


def test_lab_paths_are_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert [p for p in IGNORED_LAB if p not in ignore] == []


def test_docs_index_names_the_product_and_the_lab():
    text = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    missing = [p for p in (*sorted(PRODUCT), *IGNORED_LAB) if f"`{p}`" not in text]
    assert missing == []


# --- skill_tree_layout_exists: the skill tree has its required paths, and SKILL.md its frontmatter.

def test_skill_tree_layout_exists():
    assert ROOT.exists(), f"Skill directory not found at {ROOT}"

    # Required files and directories
    expected_paths = [
        ROOT / "SKILL.md",
        ROOT / "stages.json",
        ROOT / "cadence.defaults.json",
        ROOT / "references" / "cadence.config.schema.json",
        ROOT / "profiles" / "standard.json",
        ROOT / "scripts",
        ROOT / "hooks" / "hooks-cursor.json",
        ROOT / "packs" / "README.md",
        ROOT / "packs" / "_sample_fail_gate" / "pack.json",
        ROOT / "adapters" / "github-issues.json",
        ROOT / "subagents",
        ROOT / "skills",
        ROOT / "commands",
        ROOT / "shared",
        ROOT / "install.sh",
        ROOT / "README.md",
    ]

    for p in expected_paths:
        assert p.exists(), f"Expected path does not exist: {p}"

    # Frontmatter check in SKILL.md
    skill_md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert skill_md.startswith("---"), "SKILL.md must start with YAML frontmatter"

    # Match name
    name_match = re.search(r"^name:\s*([^\n]+)", skill_md, re.MULTILINE)
    assert name_match is not None, "SKILL.md missing 'name:' in frontmatter"
    assert name_match.group(1).strip() == "cadence", f"Expected name 'cadence', got {name_match.group(1)}"

    # Match description
    desc_match = re.search(r"^description:\s*([^\n]+(?:\n\s+[^\n]+)*)", skill_md, re.MULTILINE)
    assert desc_match is not None, "SKILL.md missing 'description:' in frontmatter"
    description_text = desc_match.group(1).strip()
    words = description_text.split()
    assert len(words) <= 60, f"Description exceeds 60 words: {len(words)} words found"


# --- context_gitignore_includes_context_dir: Phase 0: .gitignore keeps .context/ private.

GITIGNORE = ROOT / ".gitignore"


def test_context_gitignore_includes_context_dir():
    text = GITIGNORE.read_text(encoding="utf-8")
    lines = {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}
    assert ".context/" in lines, ".gitignore must list .context/ so the local index stays private"


def test_context_dir_is_not_tracked():
    proc = subprocess.run(
        ["git", "ls-files", ".context"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "", proc.stdout
