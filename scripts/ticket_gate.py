#!/usr/bin/env python3
"""A4 — ticket gate."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from activate_session import TICKET_RE  # noqa: E402
from gate_common import cli, current_branch, fail, ok, write_gate_breadcrumb  # noqa: E402
from pipeline_run_binding import load_session  # noqa: E402
from tracker_header import load_configured_adapters, no_tracker_declared  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parent.parent
SLUG = "ticket-gate"


def _adapters_for_gate(skill_root: Path) -> list[dict] | str:
    """Same loader as the vibe-test header check."""
    loaded = load_configured_adapters(skill_root)
    if isinstance(loaded, str):
        return f"ticket-gate: {loaded}"
    return loaded


def _extra_candidates(value: str, adapters: list[dict]) -> list[str]:
    """#N for a GitHub adapter. ENG-N only when the linear adapter is in play.

    cd-1 is the normalized form of a GitHub issue. eng-22 must not become #22
    for github-issues.
    """
    names = {str(item.get("name") or "") for item in adapters}
    extra: list[str] = []
    for match in TICKET_RE.finditer(value):
        prefix = match.group(0).lower()
        digits = match.group(1)
        if prefix.startswith("eng"):
            if "linear" in names:
                extra.append(f"ENG-{digits}")
            continue
        if "github-issues" in names:
            extra.append(f"#{digits}")
    return extra


def _matches_pattern(value: str, pattern: str) -> bool:
    return bool(re.search(pattern, value))


def _no_tracker_problem(adapter: dict) -> str | None:
    """C-6 name check and C-1 template check of the loaded none adapter."""
    name = adapter.get("name")
    if name != "none":
        return (
            "ticket-gate: the config names the adapter none, but adapters/none.json "
            f"has the name {name!r}"
        )
    template = adapter.get("local_tracker")
    if not isinstance(template, str) or "{task_id}" not in template:
        return "ticket-gate: adapters/none.json local_tracker must be a string with {task_id}"
    return None


def _no_tracker(context_dir: Path, session: dict | None, adapter: dict, *, check: bool) -> int:
    """A4 in no-tracker mode: record TICKET_SKIPPED for adapter none."""
    problem = _no_tracker_problem(adapter)
    if problem:
        return fail(SLUG, [problem])
    if session is None:
        return fail(SLUG, ["ticket-gate: missing cadence-session.json"])
    task_id = session.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return fail(SLUG, ["ticket-gate: adapter none needs a non-empty task_id in the session"])
    file_id = re.sub(r"[/\\_]", "-", task_id)
    if not check:
        write_gate_breadcrumb(
            SLUG,
            {
                "tracker": "none",
                "ticket": "TICKET_SKIPPED (none: no tracker declared in the config)",
                "task_id": task_id,
                "local_tracker": adapter["local_tracker"].replace("{task_id}", file_id),
            },
            context_dir=context_dir,
        )
    return ok(SLUG, "tracker=none TICKET_SKIPPED")


def _early_exit(
    root: Path, context_dir: Path, session: dict | None, adapters, *, check: bool
) -> int | None:
    """A failed adapter load, or the no-tracker mode. None means: match the patterns."""
    if isinstance(adapters, str):
        return fail(SLUG, [adapters])
    if no_tracker_declared(root):
        return _no_tracker(context_dir, session, adapters[0], check=check)
    return None


def run(context_dir: Path, *, check: bool = False, skill_root: Path | None = None) -> int:
    root = skill_root or SKILL_ROOT
    session = load_session(context_dir)
    adapters = _adapters_for_gate(root)
    early = _early_exit(root, context_dir, session, adapters, check=check)
    if early is not None:
        return early
    branch = current_branch()

    if not adapters:
        if session and session.get("task_id"):
            if not check:
                write_gate_breadcrumb(
                    SLUG,
                    {"reason": "no_adapters", "task_id": session["task_id"]},
                    context_dir=context_dir,
                )
            return ok(SLUG)
        return fail(SLUG, ["ticket-gate: no adapters configured and no task_id in session"])

    if session is None:
        return fail(SLUG, ["ticket-gate: missing cadence-session.json"])

    task_id = session.get("task_id", "")
    candidates = [c for c in [task_id, branch] if c]
    candidates += [
        extra
        for value in list(candidates)
        for extra in _extra_candidates(value, adapters)
    ]

    # The none pattern never satisfies A4. Only the no-tracker mode passes for none.
    tried = [adapter for adapter in adapters if adapter.get("name") != "none"]
    try:
        for adapter in tried:
            pattern = adapter.get("ticket_pattern") or adapter.get("pattern") or ""
            if not pattern:
                continue
            for candidate in candidates:
                if _matches_pattern(candidate, pattern):
                    if not check:
                        write_gate_breadcrumb(
                            SLUG,
                            {"adapter": adapter.get("name", "?"), "matched": candidate},
                            context_dir=context_dir,
                        )
                    return ok(SLUG)
    except re.error as e:
        return fail(SLUG, [f"ticket-gate: invalid adapter ticket_pattern: {e}"])

    return fail(
        SLUG,
        [
            f"ticket-gate: task_id={task_id!r} and branch={branch!r} "
            f"did not match any of {len(tried)} adapter pattern(s)"
        ],
    )


if __name__ == "__main__":
    raise SystemExit(cli(run))
