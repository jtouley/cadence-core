"""SKILL.md stays a short, registry-true router: description size, stop pointer, slugs and stage ids."""

from __future__ import annotations

import json
import re
from pathlib import Path


# --- skill_md_allows_context_plans_wip: Phase 0: SKILL.md Canonical .context allows .context/plans/ WIP (L04-G-B1).

ROOT = Path(__file__).resolve().parents[2]


SKILL = ROOT / "SKILL.md"


def test_skill_md_allows_context_plans_wip():
    text = SKILL.read_text(encoding="utf-8")
    assert "## Canonical `.context`" in text
    assert ".context/plans/" in text, "SKILL.md must allow .context/plans/ as WIP plan home"
    exclusive = re.search(
        r"Plans stay\s+in\s+`\.cursor/plans/`\.\s+Do not invent parallel artifact trees",
        text,
    )
    assert exclusive is None, (
        "SKILL.md still treats .cursor/plans/ as the only plan home; "
        "WIP plans must be allowed under .context/plans/"
    )


# --- skill_md_description_word_count_le_60: Phase 1b: frontmatter description must stay ≤60 words (Gap 4).

SKILL_MD = Path(__file__).resolve().parents[2] / "SKILL.md"


def _frontmatter_description(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    assert match is not None, "SKILL.md missing YAML frontmatter"
    fm = match.group(1)
    desc = re.search(
        r"^description:\s*(.+?)(?=\n[a-zA-Z_]|\Z)", fm, re.DOTALL | re.MULTILINE
    )
    assert desc is not None, "frontmatter missing description"
    raw = desc.group(1).strip()
    if raw.startswith(">") or raw.startswith("|"):
        lines = raw.splitlines()
        body = (
            "\n".join(line.lstrip() for line in lines[1:])
            if len(lines) > 1
            else raw[1:].strip()
        )
        return body.strip()
    return raw.strip().strip("\"'")


def test_skill_md_description_word_count_le_60():
    text = SKILL_MD.read_text(encoding="utf-8")
    description = _frontmatter_description(text)
    words = description.split()
    assert len(words) <= 60, (
        f"description has {len(words)} words (max 60):\n{description}"
    )


# --- skill_md_has_no_fractional_stage_ids: Phase 1b: SKILL.md must not use legacy fractional stage ids (Gap 5).

_FORBIDDEN = re.compile(
    r"(?<![\w./-])(?:"
    r"3\.5b?|3\.45|3\.7|3\.75|3\.8|3\.85|"
    r"6\.45|6\.5|6\.6|6\.65|6\.7|"
    r"stage\s*[0-9]+(?:\.\d+)?"
    r")(?![\w./-])",
    re.IGNORECASE,
)


def test_skill_md_has_no_fractional_stage_ids():
    text = SKILL_MD.read_text(encoding="utf-8")
    hits = list(_FORBIDDEN.finditer(text))
    assert not hits, (
        "SKILL.md contains legacy fractional/numeric stage ids:\n"
        + "\n".join(f"  {h.group(0)!r} at offset {h.start()}" for h in hits)
    )


# --- skill_md_pipeline_slugs_match_stages_json: Phase 1b: SKILL.md pipeline table slugs must equal stages.json order.

STAGES_JSON = ROOT / "stages.json"


def _registry_slugs() -> list[str]:
    data = json.loads(STAGES_JSON.read_text(encoding="utf-8"))
    return [s["slug"] for s in data["stages"]]


def _skill_pipeline_slugs(text: str) -> list[str]:
    """Extract slug column values from the Pipeline markdown table."""
    section = text
    pipe = re.search(r"^## Pipeline\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if pipe:
        section = pipe.group(0)

    slugs: list[str] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        if set(cells[0]) <= {"-", ":"}:
            continue
        if len(cells) < 2:
            continue
        if cells[0].lower() in {"block", "stage"} and cells[1].lower() == "slug":
            continue
        slug = cells[1]
        if re.fullmatch(r"[a-z][a-z0-9-]*", slug):
            slugs.append(slug)
    return slugs


def test_skill_md_pipeline_slugs_match_stages_json():
    text = SKILL_MD.read_text(encoding="utf-8")
    expected = _registry_slugs()
    actual = _skill_pipeline_slugs(text)
    assert actual == expected, (
        "Pipeline table slugs must match stages.json order.\n"
        f"missing={sorted(set(expected) - set(actual))}\n"
        f"extra={sorted(set(actual) - set(expected))}\n"
        f"expected={expected}\nactual={actual}"
    )


# --- skill_md_stop_pointer_line_count_le_5: Phase 1b: anti-stop / stop-hook pointer section must be ≤5 lines (Gap 3).

_SECTION_RE = re.compile(
    r"^## (?:Cadence \(anti-stop\)|Stop-hook pointer|Anti-stop)\s*\n(.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def test_skill_md_stop_pointer_line_count_le_5():
    text = SKILL_MD.read_text(encoding="utf-8")
    match = _SECTION_RE.search(text)
    assert match is not None, (
        "SKILL.md needs a stop-hook pointer section titled "
        "'## Cadence (anti-stop)', '## Stop-hook pointer', or '## Anti-stop'"
    )
    body = match.group(1).strip("\n")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) <= 5, (
        f"stop pointer has {len(lines)} non-empty lines (max 5):\n" + "\n".join(lines)
    )


def test_skill_md_open_execution_shell_is_not_a_status_turn():
    """A finished shell must not replace an open execution slice with a status reply."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "Do not answer a finished or aborted background shell with status only." in text
    assert "Do not end the turn after one slice while a later phase or the draft PR is still open." in text
    spec = SKILL_MD.parent / "commands" / "spec-driven.md"
    assert "Do not answer a finished or aborted shell with status only." in spec.read_text(
        encoding="utf-8"
    )
