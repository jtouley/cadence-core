#!/usr/bin/env python3
"""Pre-tool hook: deny a read of a secret file during a Cadence run.

Registered for Claude Code (PreToolUse on Read, Bash and Grep, with `--host claude`) and for Cursor
(preToolUse on Read, beforeReadFile, beforeShellExecution). Output format per host: hook_protocol.py.
On Claude Code the same paths go to orchestrator_scope: the orchestrator reads no run
artifact and no code.
The hook judges a call while a workspace candidate has an active run, or a session file that
does not read. Then a payload that does not parse is denied. With no run the hook allows.

Known limits: the guard judges the paths that a call names. It does not expand a glob
(`cat .e*`), and it does not look into a directory that a recursive reader or the Grep tool
searches (`grep -r KEY .`, `rg KEY`, Grep with a directory `path`). A bare file name in a
code string (`-c "open('server.key')"`) is not a path; `./server.key` and a whole word
that names a file, quoted or not, are. A bare word is looked up in the workspace and in each
`cd` or `pushd` target of the command; a target the hook cannot resolve makes every whole word
a path. A `git diff .env` counts because `.env` is a path; a bare revision name does not.
Not judged: `git stash show -p` (no path), an uploader's `-F name=@file` where the file is in
a later word, and a word with `@` that is an address (`-d "a@b.key"` is denied: the closed side).
"""

from __future__ import annotations

import os
import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Optional

_HOOKS = Path(__file__).resolve().parent
_ROOT = _HOOKS.parent
for _dir in (_ROOT / "shared", _HOOKS):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))
os.environ.setdefault("CADENCE_HOME", str(_ROOT))

try:
    from skill_gate import ProjectConfigError, gate_anchors, load_config, load_session, shell_reads, workspace_relative
    from context_root import find_primary_checkout  # on sys.path via skill_gate
    import orchestrator_scope
    from hook_protocol import (
        extract_file_path,
        extract_tool_input,
        extract_tool_name,
        fail_closed,
        hook_candidates,
        load_payload,
        parse_host,
        tool_allow,
        tool_deny,
    )
except Exception as exc:  # startup errors must block, not pass (exit 1 fails open)
    sys.stderr.write(f"cadence hook failed closed at startup: {exc!r}\n")
    raise SystemExit(2) from exc

# Matched on the workspace-relative path and on the basename. A config layer adds more in
# `secret_paths`; config adds patterns, it never removes one (x-cadence-grow-only).
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".aws/credentials", ".ssh/*", ".docker/config.json",
    "*credentials*.json", "secrets.*", "*.secret",
)
EXAMPLE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})  # not matched by `.env.*`
# `secrets.*` is not a code or doc file: scripts/secrets.py is code.
CODE_SUFFIXES = (".py", ".js", ".ts", ".go", ".rs", ".java")  # a .txt, .md or .rst named secrets is a secret (R1 MF-2)
EXTRA_KEY = "secret_paths"
SHELL_TOOLS = {"Bash", "Shell"}
# A reader prints or uses the files it names. An interpreter reads the paths its code names.
READERS = frozenset({
    "cat", "tac", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "sed", "awk", "gawk",
    "cut", "sort", "uniq", "nl", "strings", "xxd", "od", "hexdump", "base64", "diff", "cmp", "jq", "bat",
    "source", ".", "tar", "zip", "7z", "gzip", "bzip2", "xz", "zstd", "cpio", "paste", "openssl",
})
# A copier reads its sources: each operand except the destination.
COPIERS = frozenset({"cp", "mv", "scp", "rsync", "install"})
TARGET_DIR_FLAG = frozenset({"cp", "mv", "install"})  # `-t DIR`; for rsync `-t` keeps the times (R1 MF-3)
UPLOADERS = frozenset({"curl", "wget", "http", "https"})  # `-d @.env`, `-F f=@.env`, `-T .env`, `--post-file=.env`
GIT_OPTS_WITH_ARG = frozenset({"-C", "-c", "--git-dir", "--work-tree"})
GIT_READERS = frozenset({"show", "cat-file", "diff", "log", "blame", "grep", "archive", "format-patch"})
UNRESOLVED = re.compile(r"[$`]")  # a `cd` target with a variable or a substitution
INTERPRETER = re.compile(r"python[\d.]*|node|ruby|perl|php|bash|sh|zsh|dash")
PIECE = re.compile(r"[^\s'\"`(),;:=+<>\[\]{}|&]+")  # a path inside a word: --file=.env, open('.env')
DENY = ("BLOCKED: {0} matches a secret path ({1}). A Cadence run does not read a secret file.\n"
        "ACTION: ask the user for the value through an environment variable, or read the documented example file.")
NO_TARGET = "BLOCKED: this read or shell call names no path or command\nACTION: retry the call with its path or command"


def secret_patterns(workspace: Path) -> tuple[str, ...]:
    """The default patterns plus `secret_paths` of the config of each repo root. Raises ProjectConfigError."""
    extra = []
    for anchor in gate_anchors(None, workspace):
        try:
            extra += load_config(anchor).get(EXTRA_KEY) or []
        except ValueError as exc:
            raise ProjectConfigError(str(exc)) from exc
    return SECRET_PATTERNS + tuple(dict.fromkeys(p for p in extra if p not in SECRET_PATTERNS))


def _matches(form: str, pattern: str) -> bool:
    name = form.rsplit("/", 1)[-1]
    if pattern == ".env.*" and name in EXAMPLE_NAMES or pattern == "secrets.*" and name.endswith(CODE_SUFFIXES):
        return False
    return fnmatchcase(name, pattern) or fnmatchcase(form, pattern) or fnmatchcase(form, "*/" + pattern)


def secret_match(path: str, workspace: Path, patterns: tuple[str, ...]) -> Optional[tuple[str, str]]:
    """(the path to show, the pattern) when `path` names a secret file, else None.

    The forms: the path relative to the workspace (workspace_relative, as the edit gate reads
    it), the same for the real path of a link, and the real path itself.
    """
    raw = path.strip().strip("\"'")
    if not raw:
        return None
    real = os.path.realpath(workspace / raw)
    forms = [f for f in (workspace_relative(raw, workspace), workspace_relative(real, workspace), real) if f]
    pattern = next((p for p in patterns for form in forms if _matches(form, p)), None)
    return None if pattern is None else (forms[0], pattern)


def _verb(words: list[str]) -> tuple[str, list[str]]:
    if words[:2] == ["uv", "run"]:
        words = words[2:]
    return (words[0].rsplit("/", 1)[-1], words[1:]) if words else ("", [])


def _is(root: Path, name: str, kind: str) -> bool:
    """`root/name` is a file or a dir (`kind`). A name the filesystem rejects (too long) is neither (R1 N-14)."""
    try:
        return bool(getattr(root / name, kind)())
    except (OSError, ValueError):
        return False


class Dirs:
    """The workspace and each `cd` or `pushd` target of the command: where a bare word can name a
    file (R1 N-11, N-16). A target the hook cannot resolve (`cd $X`) makes every whole word a path."""

    def __init__(self, workspace: Path) -> None:
        self.workspace, self.rels, self.unresolved = workspace, [""], False

    def enter(self, *args: str) -> None:
        """`cd [-P|-L|--] [DIR]`: the first word that is not a flag is the target (R1 N-16)."""
        target = next((a for a in args if not a.startswith("-") or a == "-"), "-")
        if UNRESOLVED.search(target):
            self.unresolved = True
        elif target != "-":
            self.rels.append(os.path.join(self.rels[-1], target))

    def path_of(self, piece: str, whole: bool) -> Optional[str]:
        """The piece as a path, with the `cd` directory it is found in, or None when it is no path."""
        if "/" in piece or piece.startswith((".", "~")):
            return piece if piece.startswith(("/", "~")) else os.path.join(self.rels[-1], piece)
        if not whole:
            return None
        found = next((rel for rel in self.rels if _is(self.workspace / rel if rel else self.workspace, piece, "is_file")), None)
        if found is not None:
            return os.path.join(found, piece)
        return piece if self.unresolved else None


def _arg_paths(args: list[str], dirs: Dirs) -> list[str]:
    """The paths among `args`: `cat "server.key"` reads a file; `grep "item.key"` and `-c "print(d.key)"`
    name no file. A pattern that equals a secret file name is denied: that is the closed side."""
    out = []
    for arg in args:
        pieces = PIECE.findall(arg)
        out += [path for piece in pieces if (path := dirs.path_of(piece, pieces == [arg])) is not None]
    return out


def _sources(verb: str, args: list[str]) -> list[str]:
    """The operands a copier reads: all but the destination. For cp, mv and install, `-t DIR`, `-t/DIR`,
    `--target-directory[=DIR]` or a cluster with `t` (`-rt DIR`) names it, so every operand is a source."""
    operands: list[str] = []
    skip = target_dir = False
    for arg in args:
        if skip:
            skip = False
        elif verb in TARGET_DIR_FLAG and arg.startswith("--target-directory"):
            target_dir, skip = True, "=" not in arg
        elif verb in TARGET_DIR_FLAG and arg.startswith("-") and not arg.startswith("--") and "t" in arg[1:]:
            target_dir, skip = True, arg.endswith("t")  # `-rt DIR` takes the next word; `-t/tmp` holds it
        elif not arg.startswith("-"):
            operands.append(arg)
    return operands if target_dir else operands[:-1]


def _git_subcommand(args: list[str], dirs: Dirs) -> tuple[str, list[str]]:
    """(subcommand, its args) after the global options: `git --no-pager -C dir show ...` (R1 N-12).
    `-C dir` moves the bare words of that command (R1 N-16)."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            dirs.enter(args[i + 1])
        i += 2 if args[i] in GIT_OPTS_WITH_ARG else 1
    return (args[i], args[i + 1:]) if i < len(args) else ("", [])


def _named_files(verb: str, args: list[str], dirs: Dirs) -> list[str]:
    """Files the syntax names: `git <reader> <rev>:<path>` or a path operand, `dd if=`, an uploader's
    `@<path>`, `--post-file=` or `-T` (R1 N-12, N-17)."""
    if verb == "git":
        sub, rest = _git_subcommand(args, dirs)
        if sub not in GIT_READERS:
            return []
        revs = [a.split(":", 1)[1] for a in rest if ":" in a and not a.startswith("-")]
        return revs + _arg_paths([a for a in rest if a != "--"], dirs)
    if verb == "dd":
        return [a[3:] for a in args if a.startswith("if=")]
    if verb in UPLOADERS:
        named = [a.split("@", 1)[1] for a in args if "@" in a and "://" not in a]
        named += [a.split("=", 1)[1] for a in args if a.startswith(("--upload-file=", "--post-file=", "--body-file="))]
        named += [a[2:] for a in args if a.startswith("-T") and len(a) > 2]
        return named + [args[i + 1] for i, a in enumerate(args[:-1])
                        if a in ("-T", "--upload-file", "--post-file", "--body-file")]
    return []


def shell_read_paths(command: str, workspace: Path) -> list[str]:
    """Paths a shell command reads: `<` targets, and each path in the words of a reader or interpreter.

    The tokenizer of the edit gate (skill_gate.shell_reads) splits the command. A command it
    cannot split gives every path-like piece of its text.
    """
    parsed = shell_reads(command)
    if parsed is None:
        dirs = Dirs(workspace)
        return [path for piece in PIECE.findall(command) if (path := dirs.path_of(piece, True)) is not None]
    commands, paths = parsed
    dirs = Dirs(workspace)
    for words in commands:
        verb, args = _verb(words)
        if verb in ("cd", "pushd"):
            dirs.enter(*args)
            continue
        if verb in READERS or INTERPRETER.fullmatch(verb):
            paths += _arg_paths(args, dirs)
        elif verb in COPIERS:
            paths += _arg_paths(_sources(verb, args), dirs)
        paths += [p for p in _named_files(verb, args, dirs) if p]
    return paths


def read_paths(payload: dict[str, Any], workspace: Path) -> Optional[list[str]]:
    """Paths this call reads: [] for a call the hook does not judge, None for a read or shell call with no target."""
    event, tool = payload.get("hook_event_name"), extract_tool_name(payload)
    if event == "beforeShellExecution" or tool in SHELL_TOOLS:
        command = payload.get("command") or extract_tool_input(payload).get("command")
        return shell_read_paths(command, workspace) if isinstance(command, str) else None
    if tool == "Grep":  # a directory search is a known limit (module docstring)
        path = extract_tool_input(payload).get("path")
        return [path] if isinstance(path, str) and path.strip() and not _is(workspace, path, "is_dir") else []
    if event == "beforeReadFile":
        path = payload.get("file_path")
    elif tool == "Read":
        path = extract_file_path(extract_tool_input(payload))
    else:
        return []
    return [path] if isinstance(path, str) and path.strip() else None


def _in_run(workspace: Path) -> bool:
    """A session file is there: the run is active, or the file does not read (fail closed)."""
    if not (find_primary_checkout(workspace) / ".context" / "cadence-session.json").is_file():
        return False
    session = load_session(workspace.resolve())
    return session is None or session.get("active_skill") == "cadence"


def deny_message(payload: dict[str, Any], workspace: Path, host: str = "cursor") -> Optional[str]:
    """The deny text for this call in one workspace that has a run, or None to allow."""
    paths = read_paths(payload, workspace)
    if paths is None:
        return NO_TARGET
    if not paths and not (host == "claude" and extract_tool_name(payload) in (*SHELL_TOOLS, "Grep")):
        return None
    try:
        patterns = secret_patterns(workspace)
    except ProjectConfigError as exc:
        return f"BLOCKED: {exc}; the secret paths are not known\nACTION: set {EXTRA_KEY} to a list of path patterns"
    hits = (secret_match(path, workspace, patterns) for path in paths)
    hit = next((h for h in hits if h is not None), None)
    if hit is not None:
        return DENY.format(*hit)
    return orchestrator_scope.read_denial(payload, workspace, paths) if host == "claude" else None


def main(argv: list[str] | None = None) -> int:
    host = parse_host(argv)
    payload, error = load_payload()
    for workspace in [w for w in hook_candidates(payload) if _in_run(w)]:
        message = (f"BLOCKED: {error or 'tool payload is missing'}\nACTION: retry the tool call with a JSON object"
                   if payload is None else deny_message(payload, workspace, host))
        if message is not None:
            tool_deny(host, message)
            return 0
    tool_allow(host)
    return 0


if __name__ == "__main__":
    raise SystemExit(fail_closed(main))
