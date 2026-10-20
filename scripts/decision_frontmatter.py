#!/usr/bin/env python3
"""Constrained Decision frontmatter parser (no PyYAML).

ORIGIN: original for cadence
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DECISION_STATUSES = frozenset({"proposed", "accepted", "superseded", "rejected"})
REQUIRED_DECISION_FIELDS = ("decision_id", "status", "question", "choice", "rationale")
SHAREABLE_DECISION_DIRS = (("docs", "adr"), ("docs", "edrs"), ("docs", "adrs"))
_SCALAR_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
_LIST_ITEM = re.compile(r"^(\s+)-\s+([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
_LIST_FIELD = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


_SAFE_DECISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ContextIndexError(ValueError):
    """Invalid decision or index input."""


def is_safe_decision_id(decision_id: str) -> bool:
    """True when ``decision_id`` can name a file under proposals/ (no path parts)."""
    return bool(_SAFE_DECISION_ID.match(decision_id))


def parse_decision_frontmatter(text: str, *, source: Path) -> dict[str, Any] | None:
    """Parse scalar ``key: value`` plus indented ``- alt:`` / ``- rel:`` lists.

    JSON fences and other encodings fail loud. Returns None when the file has
    no ``---`` frontmatter or no Decision keys.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    body: list[str] = []
    closed = False
    for raw in lines[1:]:
        if raw.strip() == "---":
            closed = True
            break
        body.append(raw)
    if not closed:
        raise ContextIndexError(f"{source}: unclosed frontmatter")
    if any(ln.strip().startswith("```") for ln in body):
        raise ContextIndexError(f"{source}: fenced/JSON frontmatter is forbidden")

    result: dict[str, Any] = {}
    current_list: str | None = None
    current_item: dict[str, str] | None = None

    def flush_item() -> None:
        nonlocal current_item
        if current_list and current_item:
            result.setdefault(current_list, []).append(current_item)
        current_item = None

    for raw in body:
        if not raw.strip():
            continue
        item = _LIST_ITEM.match(raw)
        if item:
            if current_list is None:
                raise ContextIndexError(f"{source}: list item without parent key: {raw!r}")
            key, val = item.group(2), item.group(3).strip()
            if current_item is not None and key in {"alt", "rel"}:
                flush_item()
            if current_item is None:
                current_item = {}
            current_item[key] = val
            continue
        if current_list is not None and raw.startswith((" ", "\t")):
            field = _LIST_FIELD.match(raw)
            if field and current_item is not None:
                current_item[field.group(2)] = field.group(3).strip()
                continue
        scalar = _SCALAR_LINE.match(raw)
        if scalar and not raw.startswith((" ", "\t")):
            flush_item()
            key, val = scalar.group(1), scalar.group(2).strip()
            if key in {"rejected", "links"}:
                current_list = key
                result[key] = []
                current_item = None
            else:
                current_list = None
                result[key] = val
            continue
        raise ContextIndexError(f"{source}: unparseable frontmatter line: {raw!r}")
    flush_item()

    if not set(REQUIRED_DECISION_FIELDS) & set(result):
        return None
    missing = [k for k in REQUIRED_DECISION_FIELDS if not str(result.get(k, "")).strip()]
    if missing:
        raise ContextIndexError(f"{source}: missing required fields: {missing}")
    status = str(result["status"]).strip()
    if status not in DECISION_STATUSES:
        raise ContextIndexError(f"{source}: invalid status: {status!r}")
    result["status"] = status
    result["rejected"] = list(result.get("rejected") or [])
    result["links"] = list(result.get("links") or [])
    result["source_path"] = str(source)
    return result
