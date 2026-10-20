"""Each JSON example in the docs loads through the code that reads it, so a copied example works.

A config example is any JSON block whose keys are config keys: it is written as a project
`.cadence.json` and loaded by scripts/cadence_config.py. The pack manifest example of
references/pack-standard.md is read by scripts/pack_loader.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cadence_config  # noqa: E402
import pack_loader  # noqa: E402

CONFIG_DOCS = ("README.md", "docs/configuration.md", "docs/quickstart.md", "packs/README.md")
BLOCK = re.compile(r"```json\n(.*?)```", re.S)
KEYS = set(cadence_config.load_schema(ROOT)["properties"])


def config_examples() -> list[tuple[str, dict]]:
    found = []
    for rel in CONFIG_DOCS:
        for n, body in enumerate(BLOCK.findall((ROOT / rel).read_text(encoding="utf-8"))):
            data = json.loads(body)  # every json block must parse
            if isinstance(data, dict) and data and set(data) <= KEYS:
                found.append((f"{rel}#{n}", data))
    return found


EXAMPLES = config_examples()


def test_the_docs_hold_config_examples():
    assert len(EXAMPLES) >= 5, [name for name, _ in EXAMPLES]


@pytest.mark.parametrize("name, data", EXAMPLES, ids=[name for name, _ in EXAMPLES])
def test_each_config_example_loads_as_a_project_layer(tmp_path, name, data):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".cadence.json").write_text(json.dumps(data), encoding="utf-8")
    resolved = cadence_config.load(ROOT, tmp_path)
    for key in data:
        if key != "$schema":
            assert resolved.source(key) == "project" or any(
                src == "project" for path, src in resolved.sources.items() if path.startswith(f"{key}.")
            ), f"{name}: {key} did not come from the project layer"


def test_the_pack_manifest_example_reads(tmp_path):
    text = (ROOT / "references" / "pack-standard.md").read_text(encoding="utf-8")
    manifest = re.search(r"## `pack.json`\n\n```json\n(.*?)```", text, re.S).group(1)
    pack_dir = tmp_path / json.loads(manifest)["name"]
    pack_dir.mkdir()
    (pack_dir / "pack.json").write_text(manifest, encoding="utf-8")
    pack = pack_loader.read_pack(pack_dir / "pack.json")
    assert pack.gate_command and pack.manifest_version == 2 and pack.config
    assert pack_loader.version_in_range(pack_loader.core_version(ROOT), pack.requires["cadence"])
