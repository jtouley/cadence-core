"""stages.json is the one stage registry: order, blocks, gate scripts and commands."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "scripts"))

from load_stages import load_stages, stage_slugs  # noqa: E402
from load_stages import stages_by_phase  # noqa: E402
from load_stages import UnknownStageSlugError, get_stage  # noqa: E402


# --- load_stages_is_sole_order_source: stages.json is the sole order SSOT for scripts.

def test_load_stages_is_sole_order_source():
    # Act
    rows = load_stages(ROOT)
    slugs = stage_slugs(ROOT)
    registry = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))
    expected = [row["slug"] for row in registry["stages"]]

    # Assert: both readers follow the stages.json array, not each other
    assert [r["slug"] for r in rows] == expected
    assert slugs == expected
    assert len(slugs) >= 20
    # No alternate registry files under scripts/
    alt = list((ROOT / "scripts").glob("*stages*.yaml")) + list(
        (ROOT / "scripts").glob("*stages*.yml")
    )
    assert alt == [], f"alternate stage registries forbidden: {alt}"
    # Root must not ship machine-parsed yaml stages
    assert not (ROOT / "stages.yaml").exists()
    assert (ROOT / "stages.json").is_file()


# --- load_stages_returns_ordered_blocks_by_phase: Phase 2: load_stages returns ordered blocks grouped by phase.

def test_load_stages_returns_ordered_blocks_by_phase():
    rows = load_stages(ROOT)
    assert rows, "registry must be non-empty"
    assert [r["slug"] for r in rows[:3]] == [
        "activate-session",
        "preflight",
        "ops-gate",
    ]

    by_phase = stages_by_phase(ROOT)
    assert list(by_phase.keys()) == ["alignment", "planning", "execution", "review"]
    assert [r["block"] for r in by_phase["alignment"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ]
    # Within-phase order must match registry order
    flat = [r["slug"] for phase in by_phase.values() for r in phase]
    assert flat == [r["slug"] for r in rows]


# --- load_stages_unknown_slug_fails_loud: Phase 2: unknown slug raises UnknownStageSlugError.

def test_load_stages_unknown_slug_fails_loud():
    with pytest.raises(UnknownStageSlugError, match="not-a-real-slug"):
        get_stage("not-a-real-slug", ROOT)


# --- stages_gate_scripts_exist: Every stages.json gate_script/command scripts/*.py token must resolve on disk.

_SCRIPT_TOKEN = re.compile(r"^scripts/[A-Za-z0-9_./-]+\.py$")


def _first_script_token(field: str | None) -> str | None:
    if not field:
        return None
    for tok in field.split():
        if _SCRIPT_TOKEN.match(tok):
            return tok
    return None


def test_all_script_gate_paths_resolve():
    stages = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))["stages"]
    missing: list[str] = []
    checked: list[str] = []
    for row in stages:
        tokens: set[str] = set()
        for field in (row.get("gate_script"), row.get("command")):
            tok = _first_script_token(field if isinstance(field, str) else None)
            if tok:
                tokens.add(tok)
        assert tokens or str(row.get("command", "")).startswith(("/", "gh")), (
            f"{row['slug']}: no scripts/*.py token and not peer/gh allowlist"
        )
        for tok in tokens:
            checked.append(f"{row['slug']}:{tok}")
            path = ROOT / tok
            if not path.is_file():
                missing.append(f"{row['slug']}: {tok}")
    assert not missing, "missing gate scripts:\n  " + "\n  ".join(missing)
    assert checked, "expected at least one scripts/*.py token in stages.json"


# --- registered_stage_commands_parse: Every python command and gate_script in stages.json must run as registered.
#
# An argparse usage error (exit 2 + "usage:") means the pipeline can never
# execute that stage the way SKILL.md tells the agent to.

def _registered() -> list[tuple[str, str]]:
    data = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))
    rows = data["stages"] if isinstance(data, dict) else data
    out = []
    for row in rows:
        for key in ("command", "gate_script"):
            argv = shlex.split(row.get(key) or "")
            if argv and argv[0] == "python3":
                argv = argv[1:]
            if argv and argv[0].startswith("scripts/") and argv[0].endswith(".py"):
                out.append((f"{row['block']} {key}", " ".join(argv)))
    return out


@pytest.mark.parametrize("label,line", _registered(), ids=[r[0] for r in _registered()])
def test_registered_stage_command_parses(tmp_path, label, line):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    argv = shlex.split(line)
    env = {**os.environ, "CADENCE_HOME": str(ROOT), "HOME": str(tmp_path / "home")}
    proc = subprocess.run(
        [sys.executable, str(ROOT / argv[0]), *argv[1:]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert not (proc.returncode == 2 and "usage:" in proc.stderr), (
        f"{label} `{line}` is not a valid invocation:\n{proc.stderr.strip()}"
    )
