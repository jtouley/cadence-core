#!/usr/bin/env python3
"""The repo map: the tree at HEAD in one file, for each peer to read first.

A peer starts with no context, and each one used to spend 20 to 40 tool calls to learn the
same tree. The map gives the tracked files, the build targets (Makefile, pyproject scripts,
package.json scripts) and the top-level public Python names with their signatures. It is
named by the git tree sha, `.context/repo-map/<tree12>.md`, so a peer after E1 gets a new map
and two peers on one tree read one file. The peer packets pin it (input kind `repo-map`).

    python3 repo_map.py          # write the map of HEAD and print its path
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 60_000  # about 15k tokens: one Read
MAX_FILES = 600
_TARGET = re.compile(r"^([A-Za-z0-9][\w.-]*)\s*:(?!=)")


def _git(worktree: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else ""


def tree_sha(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD^{tree}").strip()


def _files(worktree: Path) -> list[str]:
    return [line for line in _git(worktree, "ls-files").splitlines() if line]


def _make_targets(worktree: Path) -> list[str]:
    try:
        text = (worktree / "Makefile").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names = [m.group(1) for line in text.splitlines() if (m := _TARGET.match(line)) and not m.group(1).startswith(".")]
    return [f"make {name}" for name in dict.fromkeys(names)]


def _pyproject_scripts(worktree: Path) -> list[str]:
    try:
        import tomllib
    except ImportError:  # Python 3.10 and older: no TOML reader in the standard library
        return []
    try:
        data = tomllib.loads((worktree / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    scripts = data.get("project", {}).get("scripts", {})
    return [f"{name} = {target}" for name, target in scripts.items()] if isinstance(scripts, dict) else []


def _npm_scripts(worktree: Path) -> list[str]:
    try:
        scripts = json.loads((worktree / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError, AttributeError):
        return []
    return [f"npm run {name}" for name in scripts] if isinstance(scripts, dict) else []


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({ast.unparse(node.args)})"


def _python_names(worktree: Path, rel: str) -> list[str]:
    try:
        tree = ast.parse((worktree / rel).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return [f"{rel}: not parsed"]
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            names.append(f"{rel}: {_signature(node)}")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            names.append(f"{rel}: class {node.name}")
    return names


def build(worktree: Path) -> str:
    """The map text of the tree at HEAD. Cut at MAX_BYTES, with a line that says so."""
    worktree = Path(worktree)
    files = _files(worktree)
    lines = [f"# Repo map (tree {tree_sha(worktree)[:12]})", "",
             "Written by scripts/repo_map.py. Read this first. Open a file only for the lines that you need.", "",
             f"## Files ({len(files)})", ""]
    lines += files[:MAX_FILES] + ([f"... {len(files) - MAX_FILES} more"] if len(files) > MAX_FILES else [])
    targets = _make_targets(worktree) + _pyproject_scripts(worktree) + _npm_scripts(worktree)
    lines += ["", "## Build targets", ""] + (targets or ["None found."])
    names = [n for rel in files if rel.endswith(".py") for n in _python_names(worktree, rel)]
    lines += ["", "## Top-level Python names", ""] + (names or ["None."])
    text = "\n".join(lines) + "\n"
    data = text.encode("utf-8")
    if len(data) <= MAX_BYTES:
        return text
    cut = data[:MAX_BYTES].decode("utf-8", errors="ignore").rsplit("\n", 1)[0]
    return f"{cut}\n\n(The map is cut at {MAX_BYTES} bytes. Use grep for the rest.)\n"


def ensure(context_dir: Path, worktree: Path) -> Path | None:
    """`.context/repo-map/<tree12>.md` for HEAD: written once per tree. None outside a git repo."""
    sha = tree_sha(Path(worktree))
    if not sha:
        return None
    path = Path(context_dir) / "repo-map" / f"{sha[:12]}.md"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build(Path(worktree)), encoding="utf-8")
    return path


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gate_common import ctx

    path = ensure(ctx(), Path.cwd())
    if path is None:
        print("FAIL  repo-map: not a git repository with a commit", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
