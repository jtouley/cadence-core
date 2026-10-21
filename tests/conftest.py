"""Shared factories for session JSON, ADR trees, and CoT fixtures."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import TypedDict
from pathlib import Path

import pytest

# The golden corpus (ADR-0008) holds sample repos with their own tests/ and conftest.py.
# They are data for the measures, not tests of this repo.
collect_ignore_glob = ["fixtures/golden/*"]

# Tests commit in throwaway repos. The developer's own git config (commit.gpgsign,
# hooks, identity) must not change what those commits do (#46).
_GIT_ISOLATION = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git_env(**extra: str) -> dict[str, str]:
    """os.environ with the developer's git config and identity replaced."""
    return {**os.environ, **_GIT_ISOLATION, **extra}


def git(repo: Path, *args: str, **extra_env: str) -> str:
    """Run git in `repo` under git_env(); return stdout, raise on failure."""
    return subprocess.run(
        ["git", *args], cwd=repo, env=git_env(**extra_env), check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch):
    """A git call that does not use the helper is isolated too.

    CLAUDECODE is cleared: when the suite runs inside Claude Code it would turn on the
    Claude-only attestation path in every gate. A test that needs it sets it.
    The host project dirs are cleared too: the hooks read them (cd-82), so a suite that
    runs inside Cursor or Claude Code must not point a hook at the developer's checkout.
    """
    for key, value in _GIT_ISOLATION.items():
        monkeypatch.setenv(key, value)
    for key in ("CLAUDECODE", "CURSOR_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "CADENCE_BASE_REF"):
        monkeypatch.delenv(key, raising=False)
    # The env layer of the config (scripts/cadence_config.py): no developer setting reaches a test.
    for key in [k for k in os.environ if k.startswith("CADENCE_") and k not in ("CADENCE_HOME", "CADENCE_SKILL_ROOT")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _hermetic_home(monkeypatch, tmp_path_factory):
    """HOME is a temporary directory for each test (cd-53).

    A2 and install.sh look for peer shadows under HOME. The developer's own
    ~/.claude and ~/.cursor must not change a result. A test that needs
    CADENCE_COMMANDS_DIR sets it.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)  # the user config layer is under the test HOME
    monkeypatch.delenv("CADENCE_COMMANDS_DIR", raising=False)
    # R3 runs the pinned ruff and mypy through uv. A test sees them only with the pinned_tools fixture.
    monkeypatch.setenv("UV_CACHE_DIR", str(home / "uv-cache"))
    monkeypatch.setenv("UV_TOOL_DIR", str(home / "uv-tools"))


# The uv settings of the developer, read before _hermetic_home replaces them.
_REAL_UV_ENV = {k: v for k, v in os.environ.items() if k in ("HOME", "UV_CACHE_DIR", "UV_TOOL_DIR", "XDG_CACHE_HOME",
                                                             "XDG_DATA_HOME")}


@pytest.fixture
def pinned_tools(monkeypatch) -> None:
    """Let uv find the developer's installed ruff and mypy at the pins of tool_measures.TOOLS.

    Without a pinned tool the test is skipped, except with CADENCE_REQUIRE_TOOLS=1 (CI), where it fails.
    """
    code_paths()
    import tool_measures

    env = {**{k: v for k, v in os.environ.items() if k not in ("UV_CACHE_DIR", "UV_TOOL_DIR")}, **_REAL_UV_ENV}
    why = ""
    try:
        for key, kind in (("UV_CACHE_DIR", "cache"), ("UV_TOOL_DIR", "tool")):
            monkeypatch.setenv(key, subprocess.run(["uv", kind, "dir"], env=env, capture_output=True, text=True,
                                                   timeout=30, check=True).stdout.strip())
    except (OSError, subprocess.SubprocessError) as exc:
        why = f"uv does not start: {exc}"
    for tool, version in tool_measures.TOOLS.items():
        if not why and subprocess.run(["uv", "tool", "run", "--offline", f"{tool}=={version}", "--version"],
                                      capture_output=True, timeout=60).returncode != 0:
            why = f"{tool} {version} is not in the uv cache: {tool_measures.warm_command(tool)}"
    if why and os.environ.get("CADENCE_REQUIRE_TOOLS") == "1":
        pytest.fail(why)
    if why:
        pytest.skip(why)

@pytest.fixture
def resolve_loop(monkeypatch):
    """resolve_loop(prefix): Path.resolve() raises RuntimeError under prefix.

    Python 3.9 to 3.12 raise it on a symlink loop; 3.13+ do not (cd-53).
    """
    real = Path.resolve

    def _arm(prefix: Path) -> None:
        def resolve(self, *args, **kwargs):
            if str(self).startswith(str(prefix)):
                raise RuntimeError("Symlink loop")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)

    return _arm


VALID_ADR = """\
---
decision_id: ADR-0001
status: accepted
question: Why SQLite for Cadence context index?
choice: stdlib sqlite3 projection at .context/cadence.db
rationale: Stdlib-only install stays transactional enough for why joins
rejected:
  - alt: DuckDB in core
    why_not: breaks stdlib-only install
  - alt: Neo4j
    why_not: operational overhead
links:
  - rel: implements
    target_type: file
    target_id: scripts/context_index.py
---
# Title
"""

VALID_COT_FIXTURE = (
    "---\n"
    "span_type: chain_of_thought\n"
    "pipeline_run_id: run-123\n"
    "role: assistant\n"
    "source_file: session.jsonl\n"
    "source_line: 1\n"
    "---\n"
    "\n"
    "Check the join key before aggregating.\n"
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


# cd-49: one builder for every ledger row a test writes (no per-file row dicts or writers).
_FINDING_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "finding_open_must_fix.json"


def ledger_row(finding_id: str, **fields) -> dict:
    """A valid open must-fix row with ``id`` set and ``fields`` applied."""
    return {**json.loads(_FINDING_FIXTURE.read_text(encoding="utf-8")), "id": finding_id, **fields}


def pr_review(context: Path, head: str, name: str = "PR_REVIEW_#1.md") -> Path:
    """An approving R1 review under `context`/prs/reviews that names `head` as its last reviewed commit."""
    path = context / "prs" / "reviews" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"RUN_BY_COMMAND: pr-review-local\nVerdict: APPROVE\nLast reviewed commit: {head}\n", encoding="utf-8")
    return path


def write_ledger(path: Path, *rows: dict) -> Path:
    """Append each row through the ledger validator; a verified row skips only the writer's verified rule."""
    scripts = str(_REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from findings_io import append_finding, validate_finding

    for row in rows:
        if row.get("status") == "verified":
            validate_finding(row, allow_verified=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        else:
            append_finding(path, row)
    return path


# Toy skill roots for scripts/headless.py.
PASS = "import sys; sys.exit(0)\n"
FAIL = "import sys; print('nope'); sys.exit(1)\n"
# A later gate that leaves a sentinel: proves whether the runner went past a failure.
SENTINEL = (
    "import pathlib, sys\n"
    "pathlib.Path('ran-' + pathlib.Path(sys.argv[0]).stem).write_text('x')\n"
    "sys.exit(0)\n"
)


CONFIG_FILES = ("cadence.defaults.json", "references/cadence.config.schema.json", "profiles",
                ".claude-plugin/plugin.json")


def install_config(root: Path, **defaults: object) -> Path:
    """Copy the core config files into the skill root copy `root`; merge `defaults` into its defaults layer.

    A section value that is a dict merges key by key into that section; any other value replaces.
    """
    import shutil

    for rel in CONFIG_FILES:
        src, dest = _REPO_ROOT / rel, root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy(src, dest)
    if defaults:
        path = root / "cadence.defaults.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in defaults.items():
            data[key] = {**data[key], **value} if isinstance(value, dict) and isinstance(data.get(key), dict) else value
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return root


def project_config(repo: Path, data: dict) -> Path:
    """Write `data` as the project layer `.cadence.json` of `repo`."""
    path = Path(repo) / ".cadence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_skill_root(tmp_path: Path, gates: list[tuple[str, str, str, str]]) -> Path:
    """gates: (block, slug, command, gate script body)."""
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "references").mkdir()
    install_config(root)
    stages = []
    for block, slug, command, body in gates:
        name = f"{slug.replace('-', '_')}_gate.py"
        (root / "scripts" / name).write_text(body, encoding="utf-8")
        stages.append(
            {
                "slug": slug,
                "block": block,
                "phase": "alignment",
                "command": command,
                "gate_script": f"scripts/{name} --check",
                "trigger": "always",
                "description": f"{slug} stage",
            }
        )
    (root / "stages.json").write_text(json.dumps({"version": 1, "stages": stages}))
    peers = {
        "version": 1,
        "peers": [{"slash": "/vibe-test", "kind": "skill", "path": "skills/vibe-test/SKILL.md"}],
    }
    (root / "references" / "bundled-peers.json").write_text(json.dumps(peers))
    return root


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: writes on-disk artifacts and crosses production modules",
    )


@pytest.fixture
def repo_root() -> Path:
    return _REPO_ROOT


def code_paths() -> None:
    """Put the product code and the test fixture modules on sys.path (one copy for every test)."""
    for rel in ("scripts", "hooks", "shared", "shared/scripts", "tests/fixtures"):
        path = str(_REPO_ROOT / rel)
        if path not in sys.path:
            sys.path.insert(0, path)


@pytest.fixture
def scripts_path() -> Path:
    code_paths()
    return _REPO_ROOT / "scripts"


def permission(out: dict) -> str:
    """allow | deny of a Claude PreToolUse hook's JSON output."""
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


def permission_reason(out: dict) -> str:
    return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def activation_repo(path: Path) -> Path:
    """A git repo on a feature branch with one commit, ready for activate_session.py."""
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "feature/gh-7-toy")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def worktree_session(repo: Path) -> dict:
    code_paths()
    import session_store

    return session_store.load_worktree_session(repo)


def make_session(context: Path, **fields) -> Path:
    context.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_skill": "cadence",
        "task_id": "gh-1",
        "pipeline_run_id": "run-1",
    }
    payload.update(fields)
    path = context / "cadence-session.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_adr_tree(tmp_path: Path, body: str) -> Path:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True, exist_ok=True)
    (adr / "0001.md").write_text(body, encoding="utf-8")
    context = tmp_path / ".context"
    context.mkdir(parents=True, exist_ok=True)
    return context


def make_cot_fixture(context: Path, run_id: str = "run-123") -> Path:
    cot_dir = context / "cot"
    cot_dir.mkdir(parents=True, exist_ok=True)
    body = VALID_COT_FIXTURE.replace(
        "pipeline_run_id: run-123",
        f"pipeline_run_id: {run_id}",
    )
    path = cot_dir / "span.md"
    path.write_text(body, encoding="utf-8")
    return path


# --- Hook drivers (shared by the hook tests; both session layouts) -----------

HOOKS_DIR = _REPO_ROOT / "hooks"
SESSION_LAYOUTS = ("single", "pointer")

# --- Subprocess drivers: one host-free env for every CLI a test runs (#98) ----

# The suite may run inside Claude Code or Cursor. These prefixes carry the host
# identity (CLAUDECODE, CLAUDE_CODE_SESSION_ID, CURSOR_*) and Cadence overrides.
# A child process must not inherit them unless the test sets them.
HOST_ENV_PREFIXES = ("CADENCE_", "CLAUDE", "CURSOR_")


class HostEnv(TypedDict, total=False):
    """Host identity variables that a test sets on a child process (keys, so `**` binds them to `**extra`)."""

    CLAUDECODE: str
    CLAUDE_CODE_SESSION_ID: str


CLAUDE_CHAT: HostEnv = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "chat-1"}


def clean_env(home: Path | None = _REPO_ROOT, *, scrub: tuple[str, ...] = HOST_ENV_PREFIXES,
              **extra: object) -> dict[str, str]:
    """os.environ without the `scrub` prefixes (default: host identity); CADENCE_HOME is `home`
    (None: unset); `extra` is set last."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(scrub)}
    if home is not None:
        env["CADENCE_HOME"] = str(home)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def run_py(script: Path | str, *args: str, cwd: Path, home: Path | None = _REPO_ROOT, stdin: object = None,
           timeout: float | None = None, **extra: object) -> subprocess.CompletedProcess[str]:
    """`python <script> <args>` in `cwd` under clean_env(home, **extra).

    A relative `script` is under `home` (or this repo), e.g. "scripts/activate_session.py".
    A dict or list `stdin` is sent as JSON.
    """
    path = Path(script) if Path(script).is_absolute() else Path(home or _REPO_ROOT) / script
    raw = stdin if stdin is None or isinstance(stdin, str) else json.dumps(stdin)
    return subprocess.run([sys.executable, str(path), *args], input=raw, cwd=cwd, env=clean_env(home, **extra),
                          capture_output=True, text=True, timeout=timeout)


def activate(cwd: Path, *flags: str, home: Path = _REPO_ROOT, **extra: object) -> subprocess.CompletedProcess[str]:
    """Run activate_session.py (default flags: --from-branch --fresh) and assert that it passed."""
    proc = run_py("scripts/activate_session.py", *(flags or ("--from-branch", "--fresh")), cwd=cwd, home=home, **extra)
    assert proc.returncode == 0, proc.stderr
    return proc


def branch_repo(path: Path, branch: str = "feature/gh-7-toy") -> Path:
    """A git repo on `branch` with one empty commit."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", branch)
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def skill_gate_hook(cwd: Path, payload: dict, host: str = "claude", **extra: object) -> subprocess.CompletedProcess[str]:
    """The real guard_skill_gate.py on one payload, under clean_env(**extra)."""
    flags = ("--host", "claude") if host == "claude" else ()
    return run_py(HOOKS_DIR / "guard_skill_gate.py", *flags, cwd=cwd, stdin=payload, **extra)


def shell_call(cwd: Path, command: str, host: str = "claude", agent: str | None = None) -> subprocess.CompletedProcess[str]:
    """The real skill gate on one shell command in the host's payload shape; `agent` sets agent_id (Claude)."""
    if host == "claude":
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}
        if agent:
            payload["agent_id"] = agent
    else:
        payload = {"hook_event_name": "beforeShellExecution", "command": command, "cwd": str(cwd)}
    return skill_gate_hook(cwd, payload, host)


def write_call(cwd: Path, target: Path, agent: str | None = None,
               content: str = "RUN_BY_COMMAND: pr-review-local\n") -> subprocess.CompletedProcess[str]:
    """The real skill gate on one Claude Code Write of `target`; `agent` sets agent_id."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(cwd),
               "tool_input": {"file_path": str(target), "content": content}}
    if agent:
        payload["agent_id"] = agent
    return skill_gate_hook(cwd, payload)


def hook_denied(proc: subprocess.CompletedProcess[str]) -> bool:
    """A hook call is denied: exit 2 (failed closed) or a deny decision on stdout."""
    return proc.returncode == 2 or '"deny"' in proc.stdout


def snapshot(context: Path) -> dict[str, str]:
    """Every file (sha256) and every directory under .context.

    Directories count: a gate that only creates an empty directory still changed state.
    """
    import hashlib

    return {
        str(p.relative_to(context)): "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(context.rglob("*"))
    }


def activated_repo(path: Path, branch: str = "feature/gh-7-toy") -> Path:
    """A repo that a real `activate_session.py` activated (task cd-7).

    Like every real run, it leaves resume_slug at `activate-session`.
    """
    activate(branch_repo(path, branch), "--from-branch", "--fresh", "--supersede")
    return path


def write_files(root: Path, files: dict[str, str]) -> None:
    """Write each relative path under `root` with its text, and make its folders."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def feature_repo(path: Path, base: dict[str, str], branch: str = "feature/gh-1-x") -> Path:
    """A repo whose `main` holds the `base` files, checked out on a new `branch`."""
    repo = branch_repo(path, "main")
    write_files(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", branch)
    return repo


def head_sha(repo: Path, short: int | None = None) -> str:
    """The HEAD sha of `repo`; `short` abbreviates it (git rev-parse --short=<short>)."""
    return git(repo, "rev-parse", *([f"--short={short}"] if short else []), "HEAD").strip()


def commit_file(repo: Path, message: str, name: str | None = None, text: str | None = None, **env: str) -> str:
    """Write `text` (default: `message`) to repo/`name`, commit all with `message`, return HEAD.

    The default name is unique per message, so each call adds a commit. `env` goes to the
    commit, e.g. GIT_COMMITTER_DATE for a commit time.
    """
    (repo / (name or f"{abs(hash(message))}.txt")).write_text(message if text is None else text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, **env)
    return head_sha(repo)


def fake_gh(bin_dir: Path, code: int = 0) -> Path:
    """A `gh` in `bin_dir` that exits `code` (1: not logged in). A2 runs `gh api user` (gh-181)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\n[ {code} -eq 0 ] || echo 'not logged in to github.com' >&2\nexit {code}\n",
                  encoding="utf-8")
    gh.chmod(0o755)
    return gh


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    return path


def hook_session(repo: Path, layout: str = "single", **fields) -> Path:
    """Write an active cadence session with the production writer for `layout`.

    single  -> .context/cadence-session.json holds the session (activate_session.py)
    pointer -> session_store: by-worktree file + {"ref": ...} pointers
    Returns the file that holds the session data.
    """
    payload = {"active_skill": "cadence", "task_id": "gh-1", "pipeline_run_id": "run-1"}
    payload.update(fields)
    if layout == "single":
        return make_session(repo / ".context", **payload)
    shared = str(_REPO_ROOT / "shared" / "scripts")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import session_store

    return session_store.save_session(payload, repo)


def peer_summary(workspace: Path) -> Path:
    """The /session-summary peer's write for the worktree's run, attested as the hook does (ADR-0018)."""
    import re as _re

    shared = str(_REPO_ROOT / "shared" / "scripts")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import peer_attest
    import session_store
    from context_root import context_dir

    session = session_store.load_worktree_session(workspace) or read_session(workspace)
    ctx = context_dir(workspace)
    rel = f"summaries/{_re.sub(r'[\\/]', '-', str(session['task_id']))}.md"
    body = b"# Session summary\n\nRUN_BY_COMMAND: session-summary\n"
    (ctx / rel).parent.mkdir(parents=True, exist_ok=True)
    (ctx / rel).write_bytes(body)
    peer_attest.record(ctx, str(session["pipeline_run_id"]), rel, "summary-peer", peer_attest.sha256(body))
    return ctx / rel


def read_session(repo: Path) -> dict:
    ctx = repo / ".context"
    data = json.loads((ctx / "cadence-session.json").read_text(encoding="utf-8"))
    if "ref" in data:
        data = json.loads((ctx / data["ref"]).read_text(encoding="utf-8"))
    return data


@pytest.fixture
def run_hook():
    """run_hook(script, repo, payload, host="cursor", *flags) -> (exit, stdout, stderr)."""
    import os
    import subprocess

    def _run(script, repo, payload, host="cursor", *flags, hooks_dir=HOOKS_DIR, cwd=None, project_env=None):
        """cwd: the hook process cwd (default `repo`). project_env: the host project
        env (default {"CLAUDE_PROJECT_DIR": repo}); {} gives a hook with no project env."""
        argv = [sys.executable, str(Path(hooks_dir) / script), *flags]
        if host == "claude":
            argv += ["--host", "claude"]
        # Like an install: CADENCE_HOME is the tree the hooks run from.
        env = {**os.environ, "CADENCE_HOME": str(Path(hooks_dir).parent)}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.pop("CURSOR_PROJECT_DIR", None)
        env.update({"CLAUDE_PROJECT_DIR": str(repo)} if project_env is None else
                   {k: str(v) for k, v in project_env.items()})
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run(argv, input=raw, capture_output=True, text=True,
                              cwd=repo if cwd is None else cwd, env=env)
        return proc.returncode, proc.stdout.strip(), proc.stderr

    return _run


def tool_decision(host: str, stdout: str) -> str:
    """allow | deny for a pre-tool hook's stdout (exit code checked by the caller)."""
    if not stdout:
        return "allow"
    data = json.loads(stdout)
    if host == "claude":
        return data.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return data["permission"]


def stop_decision(host: str, stdout: str) -> str:
    """block | allow for a stop hook's stdout."""
    data = json.loads(stdout) if stdout else {}
    if data.get("followup_message") or data.get("decision") == "block":
        return "block"
    return "allow"


# Host shapes (cd-82): how each host starts a hook. Each value is
# (payload extras, hook process cwd, project_env for run_hook).
HOST_SHAPES = ("cursor_env", "cursor_roots_only", "claude_payload_cwd", "reverse", "missing_payload_cwd")


def host_shape(name: str, project: Path, outside: Path) -> tuple[dict, Path, dict]:
    """A user-level Cursor hook runs outside the project; Claude Code sends payload `cwd`."""
    shapes = {
        "cursor_env": ({}, outside, {"CURSOR_PROJECT_DIR": project}),
        "cursor_roots_only": ({"workspace_roots": [str(project)]}, outside, {}),
        "claude_payload_cwd": ({"cwd": str(project)}, outside, {}),
        "reverse": ({"cwd": str(outside)}, project, {}),
        "missing_payload_cwd": ({"cwd": str(outside / "gone")}, project, {}),
    }
    return shapes[name]


def assert_decision(result, host: str, want: str, *, reason: str | None = None) -> None:
    """Exit 0 and the decision; a crash (exit 2, empty stdout) never passes as allow.

    want: allow | deny for a pre-tool hook; block | pass for a stop hook.
    """
    code, out, err = result
    assert code == 0, f"hook exit {code}: {err}"
    if want in ("block", "pass"):
        got = stop_decision(host, out)
        assert got == ("block" if want == "block" else "allow"), out
    else:
        assert tool_decision(host, out) == want, out
    if reason is not None:
        assert reason in out, out


def context_ignored_repo(path: Path, branch: str = "main", message: str = "base") -> Path:
    """A git repo on `branch` with one commit (`message`) of a .gitignore that ignores .context/."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", branch)
    (path / ".gitignore").write_text(".context/\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)
    return path


def pushed_repo(path: Path) -> Path:
    """A context_ignored_repo on `main` (commit "init") with a bare origin next to it, main pushed.

    The origin is path.parent / "origin.git", so origin/main is the base of a later branch.
    """
    origin = path.parent / "origin.git"
    git(path.parent, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = context_ignored_repo(path, message="init")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def linked_worktrees(root: Path, names: tuple[str, ...] = ("w0", "w1")) -> tuple[Path, dict[str, Path]]:
    """A primary checkout on `main` (one commit) and linked worktrees, one branch each.

    All share the primary checkout's .context, as real Cadence worktree runs do.
    Returns (primary, {name: worktree}).
    """
    primary = context_ignored_repo(root / "primary")
    trees = {}
    for name in names:
        path = root / name
        git(primary, "worktree", "add", "-q", "-b", f"feature/{name}", str(path))
        trees[name] = path
    return primary, trees


def save_worktree_session(worktree: Path, **fields) -> Path:
    """An active cadence session for `worktree`, written by session_store (by-worktree + root ref)."""
    shared = str(_REPO_ROOT / "shared" / "scripts")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import session_store

    payload = {"active_skill": "cadence", "task_id": "gh-1", "pipeline_run_id": "run-1", "host": "claude"}
    payload.update(fields)
    return session_store.save_session(payload, worktree)


HANDOFF_HOOK = "guard_subagent_handoff.py"


@pytest.fixture
def make_root(tmp_path):
    """A copy of the skill tree whose defaults layer carries the given settings.

    `peer_models` goes to the top level of the config (cd-58); `**hygiene` goes to
    `subagent_hygiene`. `context_budget` (gh-96) goes to the top level.
    """
    import shutil

    def _make(*, peer_models=None, context_budget=None, **hygiene) -> Path:
        root = tmp_path / "skill"
        if root.exists():
            shutil.rmtree(root)
        for name in ("hooks", "scripts", "shared", "commands", "skills", "adapters"):
            shutil.copytree(_REPO_ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(_REPO_ROOT / "stages.json", root / "stages.json")
        # The handoff tests take shadow mode unless they ask for another (the defaults say enforce).
        settings: dict = {"subagent_hygiene": {"mode": "shadow", **hygiene}}
        if peer_models is not None:
            settings["peer_models"] = peer_models
        if context_budget is not None:
            settings["context_budget"] = context_budget
        return install_config(root, **settings)
    return _make


# The run's plan in the `repo` fixture: the P3 `plan` input of the handoff resolves to it (ADR-0010).
HANDOFF_PLAN = ".cursor/plans/feature.plan.md"
HANDOFF_PLAN_TEXT = "# Plan\n\nRUN_BY_COMMAND: plan-create\nTicket: gh-1\n\n### Phase 1: build it\n"


@pytest.fixture
def repo(tmp_path):
    repo = git_repo(tmp_path / "repo")
    hook_session(repo, "single", resume_slug="plan-review")
    write_files(repo, {"plan.md": "Step one. Step two.\n", HANDOFF_PLAN: HANDOFF_PLAN_TEXT})
    return repo


@pytest.fixture
def call(run_hook, make_root, repo):
    """call(prompt, ...) -> (exit, stdout JSON, stderr) of the handoff hook on one Agent call."""
    scripts = str(_REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import handoff

    def _call(prompt, *, host="claude", tool="Agent", packet=True, event="PreToolUse", payload=None,
              model="sonnet", tool_use_id=None, agent_id=None, session_id=None, subagent_type="general-purpose",
              peer_models=None, **hygiene):
        root = make_root(peer_models=peer_models, **hygiene)
        if packet:
            handoff.write_packet(workspace=repo, context_dir=repo / ".context", run_key="run-1",
                                 block="P3", inputs=["plan.md"], ticket="gh-1", root=root)
        if payload is not None:
            body = payload
        else:
            tool_input = {"prompt": prompt, "description": "review", "subagent_type": subagent_type}
            if model is not None:
                tool_input["model"] = model
            body = {"hook_event_name": event, "tool_name": tool, "cwd": str(repo), "tool_input": tool_input}
            for key, value in (("tool_use_id", tool_use_id), ("agent_id", agent_id), ("session_id", session_id)):
                if value is not None:
                    body[key] = value
        code, out, err = run_hook(HANDOFF_HOOK, repo, body, host, hooks_dir=root / "hooks")
        return code, (json.loads(out) if out else {}), err
    return _call


RUN_A = "aaaaaaaa-0000-4000-8000-00000000000a"
RUN_B = "bbbbbbbb-0000-4000-8000-00000000000b"


@pytest.fixture
def two_worktree_runs(tmp_path):
    """cd-76: a primary checkout (no run) and linked worktrees A and B, each with an active run.

    All share the primary's .context. Run B is saved last, so the root redirect names run B.
    The hook cwd in the tests is the primary checkout unless a test says otherwise.
    """
    from types import SimpleNamespace

    primary, trees = linked_worktrees(tmp_path, ("wa", "wb"))
    save_worktree_session(trees["wa"], pipeline_run_id=RUN_A, task_id="cd-901")
    save_worktree_session(trees["wb"], pipeline_run_id=RUN_B, task_id="cd-902")
    return SimpleNamespace(primary=primary, a=trees["wa"], b=trees["wb"], ctx=primary / ".context",
                           run_a=RUN_A, run_b=RUN_B)


def stop_guard_module(name: str = "stop_guard"):
    """A fresh import of hooks/pipeline_stop_guard.py under `name`, so a test can patch one copy."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "hooks" / "pipeline_stop_guard.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def p9_marker(repo: Path) -> Path:
    """cd-76: the P9 marker of the run of `repo` (gates/entry-invariant-<pipeline_run_id>.ok)."""
    return repo / ".context" / "gates" / f"entry-invariant-{read_session(repo)['pipeline_run_id']}.ok"


def stage_packs() -> list[str]:
    """ADR-0007: the opt-in packs that own rows of this tree's stages.json, in first-row order."""
    rows = json.loads((_REPO_ROOT / "stages.json").read_text(encoding="utf-8"))["stages"]
    return list(dict.fromkeys(r["pack"] for r in rows if r.get("pack", "core") != "core"))


def enable_all_packs(workspace: Path) -> Path:
    """The full config (ADR-0007): `.cadence.json` in `workspace` enables every stage pack.

    Without the file a workspace has the default config: the core rows only.
    """
    path = Path(workspace) / ".cadence.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["packs"] = stage_packs()
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# The golden hook probes (from an external benchmark harness):
# the Cadence state fixtures, the payloads each host sends for one action, and the run of every
# hook that the host fragment registers for that payload.
PROBE_SESSION = json.dumps({"active_skill": "cadence", "task_id": "gh-1", "pipeline_run_id": "run-1",
                            "resume_slug": "activate-session"})
PROBE_MARKER = json.dumps({"pipeline_run_id": "run-1", "task_id": "gh-1", "written_at": "2026-01-01T00:00:00Z"},
                          indent=2) + "\n"
PROBE_PLAN = ".cursor/plans/feature.plan.md"
PROBE_STATES = {
    "none": {},
    "planning": {".context/cadence-session.json": PROBE_SESSION, PROBE_PLAN: "# Plan\n\n- Step 1: build it.\n"},
    "executing": {".context/cadence-session.json": PROBE_SESSION, ".context/gates/entry-invariant.ok": PROBE_MARKER,
                  ".context/findings.jsonl": '{"id": "F-1", "severity": "must-fix", "status": "fixed"}\n',
                  PROBE_PLAN: "# Plan\n\n- Step 1: build it.\n"},
}
PROBE_EVERY_STATE = {"src/app.py": "print('hello')\n", ".env": "API_KEY=sk-cbench-not-a-real-key-0000\n"}
_FRAGMENTS = {"claude": "hooks/hooks.json", "cursor": "hooks/hooks-cursor.json"}
_RANK = ("no_hook", "allow", "error", "block")


def probe_repo(path: Path, state: str) -> Path:
    """A committed git repo with the files of every probe state plus those of `state`."""
    repo = branch_repo(path, "main")
    write_files(repo, {**PROBE_EVERY_STATE, **PROBE_STATES[state]})
    return repo


def probe_calls(host: str, proj: Path, kind: str, target: str) -> list[tuple[str, str | None, dict]]:
    """(event, tool, payload) for each call the host makes (cbench payloads()); `target` is a path or a command."""
    path = str(proj / target)
    if host == "claude":
        base: dict[str, object] = {"session_id": "cbench", "transcript_path": "/dev/null", "cwd": str(proj),
                                   "hook_event_name": "PreToolUse"}
        tool, tool_input = {"read": ("Read", {"file_path": path}), "shell": ("Bash", {"command": target}),
                            "write": ("Write", {"file_path": path, "content": "# Plan\n\nTBD\n"})}[kind]
        return [("PreToolUse", tool, {**base, "tool_name": tool, "tool_input": tool_input})]
    base = {"conversation_id": "cbench", "generation_id": "cbench", "workspace_roots": [str(proj)]}
    table: dict[str, list[tuple[str, str | None, dict]]] = {
        "read": [("preToolUse", "Read", {"tool_name": "Read", "tool_input": {"path": path}}),
                 ("beforeReadFile", None, {"file_path": path})],
        "shell": [("preToolUse", "Shell", {"tool_name": "Shell", "tool_input": {"command": target}}),
                  ("beforeShellExecution", None, {"command": target, "cwd": str(proj)})],
        "write": [("preToolUse", "Write", {"tool_name": "Write", "tool_input": {"path": path, "contents": "# Plan\n\nTBD\n"}})],
    }
    return [(event, tool, {**base, "hook_event_name": event, **payload}) for event, tool, payload in table[kind]]


def registered_hooks(host: str, event: str, tool: str | None) -> list[tuple[str, tuple[str, ...]]]:
    """(script, flags) of each hook the host fragment registers for `event` and `tool`, in order (cbench matches())."""
    groups = json.loads((_REPO_ROOT / _FRAGMENTS[host]).read_text(encoding="utf-8"))["hooks"].get(event, [])
    entries = [(g.get("matcher"), h["command"]) for g in groups for h in (g["hooks"] if host == "claude" else [g])]
    out = []
    for matcher, command in entries:
        if matcher and matcher != "*" and tool is not None and re.fullmatch(matcher, tool) is None:
            continue
        script = re.search(r"hooks/(\w+\.py)", command)
        assert script is not None, command
        out.append((script.group(1), tuple(f for f in ("--subagent",) if f in command)))
    return out


def _probe_verdict(host: str, code: int, out: str) -> tuple[str, str]:
    """(block | allow | error, the deny text) of one hook run (cbench classify())."""
    if code == 2:
        return "block", ""
    if code != 0:
        return "error", ""
    if tool_decision(host, out) != "deny":
        return "allow", ""
    data = json.loads(out)
    return "block", permission_reason(data) if host == "claude" else data["agent_message"]


@pytest.fixture
def probe(run_hook):
    """probe(host, repo, kind, target) -> (verdict, deny texts): every registered hook of every host call.

    The verdict is the strongest of block, error, allow (no_hook when no hook matches), as cbench aggregates.
    """
    def _probe(host: str, repo: Path, kind: str, target: str) -> tuple[str, list[str]]:
        env = {"CLAUDE_PROJECT_DIR": repo, "CURSOR_PROJECT_DIR": repo}
        results = [_probe_verdict(host, *run_hook(script, repo, payload, host, *flags, project_env=env)[:2])
                   for event, tool, payload in probe_calls(host, repo, kind, target)
                   for script, flags in registered_hooks(host, event, tool)]
        verdict = max([v for v, _ in results] + ["no_hook"], key=_RANK.index)
        return verdict, [text for _, text in results if text]

    return _probe
