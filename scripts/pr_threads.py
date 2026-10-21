#!/usr/bin/env python3
"""The GitHub review threads of a PR as JSON, for /pr-review-update.

    python3 pr_threads.py <PR> [--repo OWNER/NAME]

With the github-issues adapter, one `gh api graphql` call lists the PR's review threads. Each
thread has its state (open, outdated or resolved), its author and the first line of its first
comment. An unresolved thread from a human is a must-fix. With `adapter: none`, no `gh` on PATH,
or a failed call, the status is `unsupported` with the reason, and the exit code is 2.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

ADAPTER = "github-issues"
QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { id isResolved isOutdated path line
      comments(first: 1) { nodes { author { login __typename } body url } } } } } } }"""


def unsupported(reason: str) -> dict:
    return {"status": "unsupported", "reason": reason}


def adapter_problem(root: Path) -> str | None:
    """None when the configured adapter is github-issues, else why the threads cannot be read."""
    from tracker_header import load_configured_adapters

    loaded = load_configured_adapters(root)
    if isinstance(loaded, str):
        return loaded
    names = [str(a.get("name")) for a in loaded]
    return None if ADAPTER in names else f"the adapter is {', '.join(names) or 'not set'}, not {ADAPTER}"


def thread(node: dict) -> dict:
    """One review thread: its state, author, first comment line, and whether it is a must-fix."""
    first = ((node.get("comments") or {}).get("nodes") or [{}])[0]
    author = first.get("author") or {}
    lines = str(first.get("body") or "").strip().splitlines()
    human = author.get("__typename") == "User"
    resolved = bool(node.get("isResolved"))
    return {
        "id": node.get("id"), "path": node.get("path"), "line": node.get("line"),
        "state": "resolved" if resolved else "outdated" if node.get("isOutdated") else "open",
        "author": author.get("login"), "human": human,
        "first_line": lines[0] if lines else "", "url": first.get("url"),
        "must_fix": human and not resolved,
    }


def _call(gh: str, pr: int, repo: str | None) -> str | dict:
    """The answer of `gh api graphql`, or the unsupported result of a failed call."""
    owner, name = repo.split("/", 1) if repo else ("{owner}", "{repo}")  # gh fills a placeholder from the cwd
    command = [gh, "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={pr}",
               "-f", f"query={QUERY}"]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return unsupported(f"gh api graphql failed: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[0]
        return unsupported(f"gh api graphql failed: {detail}")
    return proc.stdout


def report(pr: int, repo: str | None, root: Path) -> dict:
    """The review threads of PR `pr`, or `unsupported` with the reason (fail closed: never an empty list)."""
    problem = adapter_problem(root)
    if problem:
        return unsupported(problem)
    gh = shutil.which("gh")
    if gh is None:
        return unsupported("gh is not on PATH")
    answer = _call(gh, pr, repo)
    if isinstance(answer, dict):
        return answer
    try:
        found = json.loads(answer)["data"]["repository"]["pullRequest"]["reviewThreads"]
        threads = [thread(node) for node in found["nodes"]]
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return unsupported(f"gh api graphql gave no review threads: {exc!r}")
    return {"status": "supported", "pr": pr, "threads": threads,
            "must_fix": [t["id"] for t in threads if t["must_fix"]],
            "truncated": bool((found.get("pageInfo") or {}).get("hasNextPage"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr", type=int, help="the PR number")
    parser.add_argument("--repo", default=None, help="OWNER/NAME (default: the repository of the cwd)")
    args = parser.parse_args(argv)
    result = report(args.pr, args.repo, _SCRIPTS.parent)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "supported" else 2


if __name__ == "__main__":
    raise SystemExit(main())
