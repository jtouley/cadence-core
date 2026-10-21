#!/usr/bin/env python3
"""Ticket header lines for reports. The adapter name comes from one config loader."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

_LINEAR_HEADER = re.compile(r"(?im)^\*\*Linear:\*\*")
_TRACKER_LINE = re.compile(r"(?m)^\*\*Tracker:\*\*[ \t]*(\S+)\s*$")
_TICKET_LINE = re.compile(r"(?m)^\*\*Ticket:\*\*[ \t]*(.+?)\s*$")
_SKIPPED = re.compile(r"^TICKET_SKIPPED\s*\((.*)\)\s*$")
_SKILL_MARKER = "RUN_BY_SKILL: vibe-test"


def load_configured_adapters(skill_root: Path, workspace: Path | None = None) -> list[dict] | str:
    """[the adapter that the resolved config names], or one error string.

    The adapter file must exist and be an object. A config layer that is not valid is an
    error; it never falls back to github-issues.
    """
    import cadence_config

    root = Path(skill_root)
    try:
        name = cadence_config.load(root, workspace).get("adapter")
    except cadence_config.ConfigError as exc:
        return f"bad config: {exc}"
    path = root / "adapters" / f"{name}.json"
    if not path.is_file():
        return f"configured adapter {name} has no file"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"unreadable adapter {name}.json: {exc}"
    if not isinstance(data, dict):
        return f"adapter {name}.json must be an object"
    data.setdefault("name", name)
    return [data]


def no_tracker_declared(skill_root: Path, workspace: Path | None = None) -> bool:
    """True only when the resolved config names the adapter "none". A config error gives False."""
    import cadence_config

    try:
        return cadence_config.load(Path(skill_root), workspace).get("adapter") == "none"
    except cadence_config.ConfigError:
        return False


def configured_adapter_name(skill_root: Path, workspace: Path | None = None) -> str:
    """The adapter that the resolved config names."""
    loaded = load_configured_adapters(skill_root, workspace)
    if isinstance(loaded, str):
        raise ValueError(loaded)
    return str(loaded[0].get("name") or "")


def tracker_header_problems(text: str, adapter: dict | str) -> list[str]:
    """Problems when a report does not follow this adapter's header contract.

    A string adapter is a name with no ticket pattern. Callers that have the
    adapter file should pass the object so the ticket line can be checked.
    """
    if isinstance(adapter, str):
        adapter = {"name": adapter}
    name = str(adapter.get("name") or "")
    pattern = str(adapter.get("ticket_pattern") or adapter.get("pattern") or "")
    problems: list[str] = []
    if _LINEAR_HEADER.search(text) and name != "linear":
        problems.append(f"header names Linear but configured adapter is {name}")
    tracker = _TRACKER_LINE.search(text)
    if tracker is None:
        problems.append("missing **Tracker:** line")
    elif tracker.group(1) != name:
        problems.append(
            f"**Tracker:** is {tracker.group(1)} but configured adapter is {name}"
        )
    ticket = _TICKET_LINE.search(text)
    if ticket is None:
        problems.append("missing **Ticket:** line")
    else:
        problems.extend(_ticket_line_problems(ticket.group(1), name, pattern))
    return problems


def _ticket_line_problems(line: str, adapter_name: str, pattern: str) -> list[str]:
    skipped = _SKIPPED.match(line.strip())
    if skipped:
        reason = skipped.group(1)
        if adapter_name and adapter_name not in reason:
            return [
                f"TICKET_SKIPPED reason must name the adapter {adapter_name}"
            ]
        return []
    if not pattern:
        return ["ticket line has no adapter ticket_pattern to match"]
    try:
        matched = re.search(pattern, line)
    except re.error as exc:
        return [f"invalid adapter ticket_pattern: {exc}"]
    if not matched:
        return [
            f"ticket line {line!r} does not match adapter {adapter_name} ticket_pattern"
        ]
    return []


def problems_for_configured_adapters(text: str, adapters: list[dict]) -> list[str]:
    """Header problems against the adapter the Tracker line names."""
    tracker = _TRACKER_LINE.search(text)
    if tracker is None:
        return tracker_header_problems(text, {"name": ""})
    name = tracker.group(1)
    chosen = next((item for item in adapters if item.get("name") == name), None)
    if chosen is None:
        allowed = ", ".join(str(item.get("name")) for item in adapters) or "(none)"
        problems = [f"**Tracker:** is {name} but configured adapters are {allowed}"]
        if _TICKET_LINE.search(text) is None:
            problems.append("missing **Ticket:** line")
        if _LINEAR_HEADER.search(text) and name != "linear":
            problems.append(f"header names Linear but configured adapter is {name}")
        return problems
    return tracker_header_problems(text, chosen)


def marked_report_header_errors(
    context_dir: Path,
    skill_root: Path,
    session: dict | None,
    accept: Callable[[Path], bool] | None = None,
) -> list[str]:
    """Every marked vibe-test report of this run must satisfy the configured adapter.

    The caller gives the run (``run_for`` of its worktree), never the root pointer. This
    function checks a report when the report is of this run, or when ``accept`` (the A5
    artifact filter) accepts it. Thus every report that A5 counts gets a header check.
    With no run, this function checks no report. It names each file that it cannot read,
    because it cannot find the run of that file (fail closed).
    """
    loaded = load_configured_adapters(skill_root)
    if isinstance(loaded, str):
        return [loaded]
    vibe = Path(context_dir) / "vibetest"
    if not vibe.is_dir():
        return []
    errors: list[str] = []
    reports: list[tuple[Path, str]] = []
    for path in sorted(vibe.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path.name}: unreadable: {exc}")
            continue
        if _SKILL_MARKER in text:
            reports.append((path, text))
    if session is None:
        return errors
    for path, text in reports:
        if not (_of_this_run(path, text, session) or (accept is not None and accept(path))):
            continue
        problems = problems_for_configured_adapters(text, loaded)
        if problems:
            errors.append(f"{path.name}: {'; '.join(problems)}")
    return errors


def _of_this_run(path: Path, text: str, session: dict) -> bool:
    """The run-scope rule.

    A report that names run ids is of this run only when this run's id is one of them.
    A report that names no run id is of this run when its time is at or after the activation.
    """
    from pipeline_run_binding import parse_activated_at, run_ids_in_text

    named = run_ids_in_text(text)
    if named:
        run_id = str(session.get("pipeline_run_id") or "").strip().lower()
        return bool(run_id) and run_id in named
    start = parse_activated_at(session.get("activated_at"))
    if start is None:
        return False
    try:
        return path.stat().st_mtime >= start
    except OSError:
        return False
