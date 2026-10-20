#!/usr/bin/env python3
"""A2 — preflight gate: a git work tree, no peer shadow (cd-53), a valid config, and the three checks of stages.json.

The config: each layer is valid (scripts/cadence_config.py), and each pack that is on loads from its lock.

The environment: `gh api user` passes when the adapter is github-issues (adapter none: a note).
`gh auth status` is not the probe: with a token in GH_TOKEN it can report the token as invalid
while `gh api` works (gh-188 MF-2).
The branch: HEAD is on a branch that is not the default branch (origin/HEAD, else main and master).
A clean state: `git status` lists nothing outside Cadence's own state (.context/, .cursor/plans/).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gate_common import cli, fail, ok, write_gate_breadcrumb  # noqa: E402
from peer_shadows import find_peer_shadows, fix_lines, host_dirs, shadow_lines  # noqa: E402

# gate_common puts this skill root's shared/scripts on sys.path, never a host tree
# (core:standalone-package).
try:
    import session_store  # type: ignore
except ImportError:  # pragma: no cover - a vendored copy without shared/
    session_store = None

SLUG = "preflight"
# Cadence's own state. The stop guard runs A2 again during planning, after A1 and P1 wrote here.
STATE_PREFIXES = (".context/", ".cursor/plans/")
MAX_LISTED = 5
AGAIN = "then run preflight again"


def _git_toplevel() -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip(), None
        return None, result.stderr.strip() or "git rev-parse --show-toplevel failed"
    except FileNotFoundError:
        return None, "git not found on PATH"
    except subprocess.TimeoutExpired:
        return None, "git rev-parse timed out"


def _run(workspace: Path, *argv: str) -> tuple[int, str]:
    """(exit code, stdout; else the error text) of one command in `workspace`. A missing tool is 127."""
    try:
        result = subprocess.run(list(argv), cwd=workspace, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return 127, f"{argv[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(argv)} timed out"
    return result.returncode, result.stdout if result.returncode == 0 else (result.stderr or result.stdout).strip()


def _config_problems(workspace: Path) -> tuple[list[str], str | None]:
    """(problems, the adapter): each config layer is valid, and each pack that is on loads from its lock."""
    import cadence_config
    from pack_loader import enabled_packs

    try:
        resolved = cadence_config.load(_SCRIPTS.parent, workspace)
        enabled_packs(_SCRIPTS.parent, workspace, resolved)
    except (OSError, ValueError) as exc:
        return [f"preflight: {exc}. Fix it (python3 scripts/cadence_config.py resolve shows each layer), {AGAIN}"], None
    return [], resolved.get("adapter")


def _gh_problems(workspace: Path, adapter: str | None) -> tuple[list[str], str | None]:
    """(problems, note): `gh api user` must pass when the adapter is github-issues."""
    if (adapter or "github-issues") != "github-issues":
        return [], f"gh not checked: the adapter is {adapter}"
    code, out = _run(workspace, "gh", "api", "user", "--silent")
    if code == 0:
        return [], None
    if code == 127:
        return [f"preflight: gh is not on PATH. Install gh (https://cli.github.com), {AGAIN}"], None
    why = out.strip().splitlines()[-1] if out.strip() else f"exit {code}"
    return [f"preflight: gh cannot reach GitHub as a signed-in user ({why}). Run `gh auth login` or set GH_TOKEN, "
            f"{AGAIN}"], None


def _branch_problems(workspace: Path) -> tuple[list[str], str]:
    """(problems, branch): HEAD is on a branch, and it is not the default branch."""
    code, out = _run(workspace, "git", "symbolic-ref", "--short", "-q", "HEAD")
    branch = out.strip() if code == 0 else ""
    if not branch:
        return [f"preflight: HEAD is not on a branch ({out.strip() or f'exit {code}'}). "
                f"Run `git switch -c <type>/<ticket>-<slug>`, {AGAIN}"], branch
    code, out = _run(workspace, "git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    defaults = {out.strip().split("/", 1)[-1]} if code == 0 and out.strip() else {"main", "master"}
    if branch in defaults:
        return [f"preflight: the current branch {branch} is the default branch. "
                f"Run `git switch -c <type>/<ticket>-<slug>`, {AGAIN}"], branch
    return [], branch


def _dirty(porcelain: str) -> list[str]:
    """`XY path` of each change outside Cadence's own state, from `git status --porcelain -z`."""
    out: list[str] = []
    entries = iter(porcelain.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            next(entries, None)  # with -z, the old path of a rename or a copy comes next
        if not entry[3:].startswith(STATE_PREFIXES):
            out.append(f"{entry[:2].strip()} {entry[3:]}")
    return out


def _tree_problems(workspace: Path) -> list[str]:
    """The tree is clean except Cadence's own state. Each untracked or changed path is listed."""
    code, out = _run(workspace, "git", "status", "--porcelain", "-z", "--untracked-files=all")
    if code != 0:
        return [f"preflight: git status failed ({out.strip() or f'exit {code}'}). Run `git status`, fix the "
                f"repository, {AGAIN}"]
    dirty = _dirty(out)
    if not dirty:
        return []
    more = f"; and {len(dirty) - MAX_LISTED} more" if len(dirty) > MAX_LISTED else ""
    return [f"preflight: the tree is not clean outside .context/ and .cursor/plans/: {'; '.join(dirty[:MAX_LISTED])}{more}. "
            f"Commit or stash them (`git stash -u`), {AGAIN}"]


def _host(workspace: Path) -> str:
    """claude for Claude Code (CLAUDECODE=1, or this worktree's session says so); else cursor.

    The session read is this worktree's own file. It creates no directory and never
    follows the root redirect to another worktree's run.
    """
    if os.environ.get("CLAUDECODE") == "1":
        return "claude"
    if session_store is None:
        return "cursor"
    try:
        session = session_store.load_worktree_session(workspace)
    except Exception:  # noqa: BLE001 - an unreadable session never fails A2
        return "cursor"
    if isinstance(session, dict) and session.get("host") == "claude":
        return "claude"
    return "cursor"


def run(context_dir: Path, *, check: bool = False) -> int:
    toplevel, err = _git_toplevel()
    if toplevel is None:
        return fail(SLUG, [f"preflight: not inside a git repository — {err}"])
    workspace = Path(toplevel)
    commands_dir = os.environ.get("CADENCE_COMMANDS_DIR")
    pairs = host_dirs(
        _host(workspace),
        home=Path.home(),
        workspace=workspace,
        commands_dir=Path(commands_dir) if commands_dir else None,
    )
    registry = _SCRIPTS.parent / "references" / "bundled-peers.json"
    try:
        shadows = find_peer_shadows(_SCRIPTS.parent, pairs)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        return fail(SLUG, [f"preflight: cannot read the peer registry {registry}: {exc}"])
    if shadows:
        return fail(SLUG, shadow_lines(shadows) + fix_lines(_SCRIPTS.parent))
    config, adapter = _config_problems(workspace)
    if config:
        return fail(SLUG, config)
    gh, note = _gh_problems(workspace, adapter)
    on_branch, branch = _branch_problems(workspace)
    problems = gh + on_branch + _tree_problems(workspace)
    if problems:
        return fail(SLUG, problems)
    if not check:
        write_gate_breadcrumb(SLUG, {"git_toplevel": toplevel, "branch": branch}, context_dir=context_dir)
    return ok(SLUG, note)


if __name__ == "__main__":
    raise SystemExit(cli(run))
