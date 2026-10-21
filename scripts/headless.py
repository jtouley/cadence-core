#!/usr/bin/env python3
"""Headless gate runner — walk stages.json gates for Pi, `claude -p`, and CI.

Runs each registered ``gate_script`` exactly as written, in registry order, and
stops at the first failure. The first failing gate is the open block: later
gates can pass vacuously (P4, R3, R6 ``--check``), so "last pass + 1" would skip
work. Stopping early also keeps breadcrumbs from appearing for blocks that never ran.

The runner never executes peers (slash commands); it tells the outer loop which
block is open, how to run it, and where its procedure lives.

Usage:
    python3 headless.py list [--json]
    python3 headless.py next [--json] [--timeout SECONDS]
    python3 headless.py run [--through BLOCK|SLUG] [--json] [--timeout SECONDS]

Exit codes: 0 every gate in range passes, 1 a block is open, 2 usage/registry error.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gate_common import GATES_READONLY_ENV  # noqa: E402
from load_stages import load_registry, load_stages  # noqa: E402
from skill_paths import display_command  # noqa: E402

DEFAULT_TIMEOUT = 120
OUTPUT_TAIL = 2000


class UsageError(Exception):
    """Bad arguments or unreadable registry (exit 2)."""


@dataclass
class GateResult:
    block: str
    slug: str
    ok: bool
    exit_code: int | None
    timed_out: bool
    output: str


def skill_root() -> Path:
    """Resolve from env each call so tests and installs can point elsewhere."""
    env = os.environ.get("CADENCE_HOME") or os.environ.get("CADENCE_SKILL_ROOT")
    return Path(env) if env else _SCRIPTS.parent


REQUIRED_KEYS = ("block", "slug", "gate_script")


def load_rows(root: Path, workspace: Path) -> list[dict[str, Any]]:
    """The rows of the core pack and the packs enabled for `workspace`."""
    path = root / "stages.json"
    try:
        rows = load_stages(root, workspace)
    except (OSError, ValueError, TypeError) as e:
        raise UsageError(f"cannot load {path}: {e}") from e
    if not rows:
        raise UsageError(f"{path} has no stages")
    for idx, row in enumerate(rows):
        missing = [k for k in REQUIRED_KEYS if not isinstance(row, dict) or not row.get(k)]
        if missing:
            raise UsageError(f"{path}: stage #{idx} missing {', '.join(missing)}")
        try:
            argv = shlex.split(str(row["gate_script"]))
        except ValueError as e:
            raise UsageError(f"{path}: stage {row['block']} gate_script does not parse: {e}") from e
        if not argv or not (root / argv[0]).is_file():
            raise UsageError(f"{path}: stage {row['block']} gate script not found: {root / argv[0] if argv else ''}")
    return rows


def peer_docs(root: Path) -> dict[str, str]:
    path = root / "references" / "bundled-peers.json"
    try:
        peers = json.loads(path.read_text(encoding="utf-8")).get("peers") or []
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(p["slash"]): str(root / p["path"]) for p in peers if p.get("slash") and p.get("path")}


def describe(row: dict[str, Any], root: Path, docs: dict[str, str]) -> dict[str, Any]:
    command = str(row.get("command") or "")
    is_peer = command.startswith("/")
    return {
        "block": row["block"],
        "slug": row["slug"],
        "phase": row.get("phase"),
        "kind": "peer" if is_peer else "script",
        "command": command,
        "gate_script": row.get("gate_script"),
        # cd-82 D5: what the agent runs, with absolute script paths (the old fields stay).
        "run": display_command(command, root),
        "check": display_command("python3 " + row["gate_script"], root) if row.get("gate_script") else None,
        "trigger": row.get("trigger"),
        "description": row.get("description"),
        "peer_doc": docs.get(command.split()[0]) if is_peer else None,
    }


def run_gate(
    row: dict[str, Any], root: Path, workspace: Path, timeout: float, readonly: bool = False
) -> GateResult:
    argv = shlex.split(str(row["gate_script"]))
    cmd = [sys.executable, str(root / argv[0]), *argv[1:]]
    env = {**os.environ, "CADENCE_HOME": str(root)}
    if readonly:
        env[GATES_READONLY_ENV] = "1"  # in this gate's environment only, never exported
    # Own process group, so a timeout can end the children of the gate too. The stop
    # guard runs the walk on every stop, and a leftover child would pile up (cd-12-F6).
    proc = subprocess.Popen(
        cmd,
        cwd=workspace,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        # Do not read the pipes again. A child that left the group (its own session) can
        # hold them open, and the walk must not wait for it (a hook has 15 s).
        proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                pipe.close()
        return GateResult(row["block"], row["slug"], False, None, True, f"timed out after {timeout}s")
    output = (stdout + stderr)[-OUTPUT_TAIL:]
    return GateResult(row["block"], row["slug"], proc.returncode == 0, proc.returncode, False, output)


def kill_group(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def resolve_through(rows: list[dict[str, Any]], through: str | None, root: Path | None = None) -> int:
    """Index of the last row to run. With `root`, a row whose pack is off stands for the last
    row before it that is on."""
    if through is None:
        return len(rows) - 1
    wanted = through.lower()
    on = [str(row["slug"]) for row in rows]
    order = load_registry(root)["stages"] if root is not None else rows
    for pos, row in enumerate(order):
        if wanted in (str(row["block"]).lower(), str(row["slug"]).lower()):
            before = [str(r["slug"]) for r in order[: pos + 1] if str(r["slug"]) in on]
            if before:
                return on.index(before[-1])
    first, last = rows[0]["block"], rows[-1]["block"]
    raise UsageError(
        f"unknown block {through!r}. Use a block from {first} to {last}. "
        "Run `headless.py list` to see each block."
    )


def walk(
    root: Path,
    workspace: Path,
    *,
    through: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    since: str | None = None,
    readonly: bool = False,
    budget: float | None = None,
) -> dict[str, Any]:
    """Run the gates in order. The first failure is the open block.

    since: first block or slug to run (the earlier gates are skipped).
    readonly: the gates record no breadcrumb (the stop guard needs this).
    budget: seconds for the whole walk. A gate that has no time left counts as a timeout.
    """
    rows = load_rows(root, workspace)
    last = resolve_through(rows, through, root)
    first = 0 if since is None else resolve_through(rows, since, root)  # same lookup, other end
    docs = peer_docs(root)
    deadline = None if budget is None else time.monotonic() + budget
    results: list[GateResult] = []
    open_row: dict[str, Any] | None = None
    for row in rows[first : last + 1]:
        limit = timeout
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                results.append(
                    GateResult(row["block"], row["slug"], False, None, True, "time budget used up")
                )
                open_row = row
                break
            limit = min(timeout, left)
        result = run_gate(row, root, workspace, limit, readonly)
        results.append(result)
        if not result.ok:
            open_row = row
            break
    return {
        "done": open_row is None,
        "next": describe(open_row, root, docs) if open_row else None,
        "results": [asdict(r) for r in results],
        "registry": str(root / "stages.json"),
        "workspace": str(workspace),
    }


def print_report(report: dict[str, Any]) -> None:
    # E1 and R4 judge the run of this workspace only (cd-47 G-D4); say which one.
    print(f"workspace: {report['workspace']}")
    for r in report["results"]:
        print(f"{'OK  ' if r['ok'] else 'FAIL'}  {r['block']:<3} {r['slug']}")
    nxt = report["next"]
    if nxt is None:
        print("DONE  every gate in range passes")
        return
    print(f"NEXT  {nxt['block']} ({nxt['slug']}) — {nxt['kind']}: {nxt['run']}")
    if nxt["peer_doc"]:
        print(f"      procedure: {nxt['peer_doc']}")
    if nxt["check"]:
        print(f"      check:     {nxt['check']}")
    tail = report["results"][-1]["output"].strip()
    if tail:
        print("      gate output:")
        for line in tail.splitlines()[-10:]:
            print(f"        {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cadence headless gate runner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("list", "Print registry rows in order (runs nothing)"),
        ("next", "Run gates in order; report the first open block"),
        ("run", "Run gates in order through --through (default: all)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true", help="Machine-readable output")
        p.add_argument("--workspace", default=".", help="Project worktree (default: cwd)")
        if name != "list":
            p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Seconds per gate")
        if name == "run":
            p.add_argument("--through", help="Last block id or slug to run")
    args = parser.parse_args(argv)

    root = skill_root()
    workspace = Path(args.workspace).resolve()
    try:
        if not workspace.is_dir():
            raise UsageError(f"workspace is not a directory: {workspace}")
        if args.command == "list":
            docs = peer_docs(root)
            rows = [describe(r, root, docs) for r in load_rows(root, workspace)]
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for r in rows:
                    print(f"{r['block']:<3} {r['slug']:<26} {r['kind']:<6} {r['run']}")
            return 0
        report = walk(root, workspace, through=getattr(args, "through", None), timeout=args.timeout)
    except UsageError as e:
        print(f"headless: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0 if report["done"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
