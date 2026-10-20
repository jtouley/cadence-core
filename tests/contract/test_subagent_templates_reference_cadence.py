"""The subagent templates that core peers name exist and point at Cadence."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUBAGENTS = ROOT / "subagents"

REQUIRED = (
    "deslop.md",
    "simplify-code.md",
)

FORBIDDEN_SUBSTRINGS = (
    "plan-to-pr",
    "skills/plan-to-pr",
    "/plan-to-pr",
    "~/.cursor/commands",
    "~/.cursor/rules",
    "Return to the main agent",
    "Return to parent",
    "Keep your response under",
    "Keep response under",
)


def test_subagent_templates_exist_and_reference_cadence_not_plan_to_pr():
    assert SUBAGENTS.is_dir(), "subagents/ directory missing"
    missing = [name for name in REQUIRED if not (SUBAGENTS / name).is_file()]
    assert not missing, f"missing subagent templates: {missing}"

    bad: list[str] = []
    for name in REQUIRED:
        text = (SUBAGENTS / name).read_text(encoding="utf-8")
        lower = text.lower()
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle.lower() in lower:
                bad.append(f"{name} contains {needle!r}")
        # Retarget marker: templates should acknowledge cadence or stay host-agnostic
        if "cadence" not in lower and "plan-to-pr" not in lower:
            # Host-agnostic templates (no skill path) are OK — locate-plan style
            continue
    assert not bad, "subagent templates still hardcode plan-to-pr:\n" + "\n".join(bad)
