#!/usr/bin/env python3
"""Publish bundled Cadence peers into a host skills/ and commands/ tree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class PublishError(RuntimeError):
    pass


def load_registry(cadence_home: Path) -> dict[str, Any]:
    return json.loads((cadence_home / "references" / "bundled-peers.json").read_text())


def load_peer_rows(cadence_home: Path) -> list[dict[str, Any]]:
    data = load_registry(cadence_home)
    return list(data["peers"]) + list(data.get("supporting_commands") or [])


def peer_kind(row: dict[str, Any]) -> str:
    """`skill` or `command`: the row's `kind`, else the first part of its path."""
    return row.get("kind") or ("skill" if Path(row["path"]).parts[0] == "skills" else "command")


def _link(src: Path, dest: Path, *, skip_existing: bool) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        try:
            same = dest.resolve() == src.resolve()
        except (OSError, RuntimeError):  # a symlink loop on Python 3.9 to 3.12
            same = False
        if same:
            return f"ok (already linked): {dest}"
        if skip_existing:
            return f"skip (foreign symlink): {dest} -> {dest.readlink()}"
        dest.unlink()
    elif dest.exists():
        if skip_existing:
            return f"skip (exists): {dest}"
        raise PublishError(f"refusing to overwrite non-symlink: {dest}")
    dest.symlink_to(src)
    return f"linked: {dest} -> {src}"


def publish_peers(
    cadence_home: Path,
    *,
    skills_dir: Path,
    commands_dir: Path,
    skip_existing: bool = False,
) -> dict[str, Any]:
    home = Path(cadence_home).resolve()
    messages: list[str] = []
    for row in load_peer_rows(home):
        rel = Path(row["path"])
        src = home / rel
        if not src.is_file():
            raise PublishError(f"missing peer file: {src}")
        name = str(row["slash"]).lstrip("/")
        if peer_kind(row) == "skill":
            messages.append(
                _link(src.parent, Path(skills_dir) / name, skip_existing=skip_existing)
            )
        else:
            messages.append(
                _link(src, Path(commands_dir) / f"{name}.md", skip_existing=skip_existing)
            )
    return {"ok": True, "messages": messages}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", type=Path, required=True)
    p.add_argument("--skills-dir", type=Path, required=True)
    p.add_argument("--commands-dir", type=Path, required=True)
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip non-symlink collisions instead of failing (host already has the name).",
    )
    args = p.parse_args(argv)
    result = publish_peers(
        args.home,
        skills_dir=args.skills_dir,
        commands_dir=args.commands_dir,
        skip_existing=args.skip_existing,
    )
    for line in result["messages"]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
