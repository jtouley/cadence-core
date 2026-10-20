"""Phase 8: no machine-parsed YAML manifests under skill (JSON only)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Config / packs / adapters / stages must be JSON. Docs may mention yaml as
# historical Gap Spec names only inside markdown.
MANIFEST_GLOBS = (
    "stages.yaml",
    "stages.yml",
    "cadence.config.yaml",
    "cadence.config.yml",
    "feature-pipeline.config.yaml",
)


def test_no_machine_readable_yaml_manifests():
    forbidden: list[str] = []
    for name in MANIFEST_GLOBS:
        p = ROOT / name
        if p.exists():
            forbidden.append(str(p))
    for sub in ("packs", "adapters"):
        d = ROOT / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in {".yaml", ".yml"} and p.is_file():
                forbidden.append(str(p.relative_to(ROOT)))
    assert not forbidden, f"machine-readable YAML manifests forbidden: {forbidden}"
    assert (ROOT / "stages.json").is_file()
    assert (ROOT / "cadence.defaults.json").is_file()
    assert (ROOT / "references" / "cadence.config.schema.json").is_file()
