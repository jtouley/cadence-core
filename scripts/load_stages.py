#!/usr/bin/env python3
"""Load cadence stages.json, the one source of the block order, and resolve the order of a run."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(
    os.environ.get("CADENCE_HOME")
    or os.environ.get("CADENCE_SKILL_ROOT")
    or Path(__file__).resolve().parent.parent
)


# The tree of this module. CADENCE_HOME can name another tree (SKILL_ROOT).
CODE_ROOT = Path(__file__).resolve().parent.parent


class UnknownStageSlugError(KeyError):
    """Raised when a slug is not present in stages.json."""


def stages_path(skill_root: Path | None = None) -> Path:
    root = Path(skill_root) if skill_root is not None else SKILL_ROOT
    return root / "stages.json"


def load_registry(skill_root: Path | None = None) -> dict[str, Any]:
    path = stages_path(skill_root)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "stages" not in data:
        raise ValueError(f"invalid stages registry at {path}")
    return data


# The blocks that make the evidence that closes a run. An overlay can replace their peer and their
# text, never remove them or change their gate.
PROTECTED = ("P9", "R1", "R5")
INSERT_KEYS = ("block", "slug", "phase", "gate_script")


class OverlayError(ValueError):
    """A stage overlay or a peer binding cannot apply. load_stages fails closed on it."""


def _anchored(op: dict[str, Any], base: Path, source: str, pack: str | None = None) -> dict[str, Any]:
    """`op` with a relative gate script of its row made absolute when the script is next to the file
    that declares the operation (`base`). Any other relative path stays relative to the skill root."""
    op = dict(op)
    if isinstance(op.get("row"), dict):
        row = {**op["row"], **({"pack": pack} if pack else {})}
        script = str(row.get("gate_script") or "")
        first = script.split()[0] if script else ""
        if first and not Path(first).is_absolute() and (base / first).is_file():
            row["gate_script"] = str(base / first) + script[len(first):]
        op["row"] = row
    return {**op, "source": source}


def _pack_ops(pack: Any) -> list[dict[str, Any]]:
    """The overlay operations of a pack's manifest, anchored at the pack directory."""
    return [_anchored(op, pack.path, f"pack {pack.name}", pack.name) for op in pack.stages]


def _config_ops(resolved: Any, root: Path, workspace: Path | None) -> list[dict[str, Any]]:
    """`stages.overlays` of each config layer, in layer order, anchored at the directory of its file.

    The defaults and the profiles anchor at the skill root; the env layer at the project root.
    """
    import cadence_config

    ops = []
    for layer in resolved.layers:
        if layer.name == "default" or layer.name.startswith("profile:"):
            base = root
        elif layer.path is not None:
            base = layer.path.parent
        else:
            base = cadence_config.project_root(workspace if workspace is not None else Path.cwd()) or Path.cwd()
        ops += [_anchored(op, base, f"config {layer.name}") for op in (layer.data.get("stages") or {}).get("overlays") or []]
    return ops


def apply_overlays(rows: list[dict[str, Any]], ops: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """(the rows after each operation in order, the blocks that a `remove` dropped)."""
    rows = [dict(r) for r in rows]
    removed: list[str] = []
    for op in ops:
        where = f"stage overlay {op.get('op')} {op.get('block')} ({op.get('source', 'config')})"
        index = next((i for i, r in enumerate(rows) if r.get("block") == op.get("block")), None)
        if index is None:
            raise OverlayError(f"{where}: no block {op.get('block')!r} in the stage order")
        kind, row = op.get("op"), op.get("row")
        if kind == "remove":
            if op["block"] in PROTECTED:
                raise OverlayError(f"{where}: {op['block']} makes closure evidence; replace its peer, do not remove it")
            removed.append(rows.pop(index)["block"])
            continue
        if not isinstance(row, dict):
            raise OverlayError(f"{where}: needs a `row`")
        if kind == "replace":
            new = {**rows[index], **row}
            if new.get("block") != op["block"]:
                raise OverlayError(f"{where}: a replace keeps the block id")
            if op["block"] in PROTECTED and new.get("gate_script") != rows[index].get("gate_script"):
                raise OverlayError(f"{where}: {op['block']} keeps its gate; replace its peer or its text only")
            rows[index] = new
            continue
        missing = [k for k in INSERT_KEYS if not row.get(k)]
        if missing:
            raise OverlayError(f"{where}: the row needs {missing}")
        for key in ("block", "slug"):
            if any(r.get(key) == row[key] for r in rows):
                raise OverlayError(f"{where}: {key} {row[key]!r} is in the stage order already")
        rows.insert(index if kind == "insert_before" else index + 1, {"pack": "overlay", **row})
    return rows, removed


def bind_peers(rows: list[dict[str, Any]], peers: dict[str, str]) -> list[dict[str, Any]]:
    """Each row whose block `peers` names runs that peer: `/<name>` or `<plugin>:<name>`."""
    rows = [dict(r) for r in rows]
    by_block = {r.get("block"): r for r in rows}
    for block, peer in (peers or {}).items():
        row = by_block.get(block)
        if row is None or not str(row.get("command", "")).startswith("/"):
            raise OverlayError(f"peers.{block}: {block} is not a peer block of the stage order")
        name = peer if peer.startswith("/") else f"/{peer}"
        if not name[1:] or any(c.isspace() for c in name):
            raise OverlayError(f"peers.{block}: {peer!r} is not a peer name (`/<name>` or `<plugin>:<name>`)")
        args = str(row["command"]).split()[1:]
        row["command"] = " ".join([name, *args])
        row["peer_binding"] = peer
    return rows


def resolve_stages(skill_root: Path | None = None, workspace: Path | None = None,
                   resolved: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    """(the stage order of a run, the blocks that an overlay removed).

    The order is: the rows of stages.json whose pack is core or on (`packs`), then the stage
    operations of each pack that is on, then `stages.overlays` of the config layers, then the
    peer bindings of `peers`.
    """
    import cadence_config
    from pack_loader import enabled_packs

    root = Path(skill_root) if skill_root is not None else SKILL_ROOT
    resolved = resolved or cadence_config.load(root, Path.cwd() if workspace is None else workspace)
    try:
        packs = enabled_packs(root, workspace, resolved)
    except FileNotFoundError as exc:  # a typo must not turn rows off
        raise ValueError(f"unknown pack name: {exc}") from exc
    on = {"core", *(p.name for p in packs)}
    # A row that is not an object stays, so the reader reports it (fail closed).
    rows = [r for r in load_registry(root)["stages"] if not isinstance(r, dict) or r.get("pack", "core") in on]
    ops = [op for pack in packs for op in _pack_ops(pack)]
    ops += _config_ops(resolved, root, workspace)
    rows, removed = apply_overlays(rows, ops)
    return bind_peers(rows, resolved.get("peers") or {}), removed


def load_stages(skill_root: Path | None = None, workspace: Path | None = None) -> list[dict[str, Any]]:
    """The stage order of a run for `workspace` (the cwd when None). See resolve_stages."""
    return resolve_stages(skill_root, workspace)[0]


def stages_by_phase(skill_root: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Every registry row by phase, in registry order (a registry check, not a walk)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in load_registry(skill_root)["stages"]:
        phase = str(row.get("phase") or "")
        grouped.setdefault(phase, []).append(row)
    return grouped


def get_stage(slug: str, skill_root: Path | None = None) -> dict[str, Any]:
    """The row of `slug` in the whole registry, also when its pack is off."""
    for row in load_registry(skill_root)["stages"]:
        if row.get("slug") == slug:
            return row
    raise UnknownStageSlugError(slug)


def writer_stages(slug: str, *, skill_root: Path | None = None) -> frozenset[str]:
    """The delegation stages whose peer may write the verdict of `slug` (gh-66 PD20).

    Here `skill_root=None` means CODE_ROOT, the tree of this module, and not SKILL_ROOT
    as in ``get_stage``: the field and the code that reads it ship together. A slug that is
    not in stages.json, or a row with no `writer_stages`, raises KeyError.
    """
    root = skill_root if skill_root is not None else CODE_ROOT
    return frozenset(get_stage(slug, root)["writer_stages"])


def stage_slugs(skill_root: Path | None = None, workspace: Path | None = None) -> list[str]:
    return [str(row["slug"]) for row in load_stages(skill_root, workspace)]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect cadence stages.json registry")
    parser.add_argument("--phase", help="Filter to a single phase")
    parser.add_argument("--slug", help="Print one stage by slug")
    args = parser.parse_args()
    if args.slug:
        print(json.dumps(get_stage(args.slug), indent=2))
        return 0
    rows = load_stages()
    if args.phase:
        rows = [r for r in rows if r.get("phase") == args.phase]
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
