#!/usr/bin/env python3
"""Cadence skill gate — portable fail-closed checker for Cursor + Pi.

Resolves scripts under $CADENCE_HOME / the skill root only.
Pi hosts must invoke CLI explicitly (no hooks.json):

    python3 $CADENCE_HOME/shared/skill_gate.py state --skill cadence
    python3 $CADENCE_HOME/shared/skill_gate.py check --skill cadence \\
        --action strreplace --file "src/foo.py"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

SHARED_DIR = Path(__file__).resolve().parent
_SCRIPTS = SHARED_DIR / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from context_root import context_dir as canonical_context_dir  # noqa: E402
from context_root import find_primary_checkout  # noqa: E402


def skill_root() -> Path:
    """Resolve skill root from env each call (test-safe; no import-time freeze)."""
    env = os.environ.get("CADENCE_HOME") or os.environ.get("CADENCE_SKILL_ROOT")
    root = Path(env) if env else SHARED_DIR.parent
    scripts = root / "scripts"
    if scripts.is_dir() and str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    return root

WRITE_ACTIONS = {"write", "strreplace", "editnotebook", "write_impl", "write_test", "shell"}
# gh-178: these wait for R5 (closure) of the workspace's run at HEAD, as hooks/pr_guard.py does.
PR_TALK_ACTIONS = {"gh_pr_comment", "gh_pr_review"}


@dataclass
class GateResult:
    allowed: bool
    message: str
    action_required: str | None = None


def _config():
    skill_root()  # puts the skill root's scripts/ on sys.path
    import cadence_config

    return cadence_config


class ProjectConfigError(ValueError):
    """A config layer is not valid: gated writes fail closed. It carries the ConfigError text."""


def load_config(workspace: Optional[Path] = None) -> dict:
    """The resolved config for `workspace` (the cwd when None). Raises ProjectConfigError."""
    cfg = _config()
    try:
        return cfg.load(skill_root(), workspace).values
    except cfg.ConfigError as exc:
        raise ProjectConfigError(str(exc)) from exc


def gated_prefixes(workspace: Optional[Path] = None) -> tuple[str, ...]:
    """`gated_prefixes` of the resolved config for `workspace`; the core defaults when `workspace` is None.

    A prefix matches from the repo root (#48). So the project and local layers count only when
    `workspace` is the project root; a subdirectory anchor gets the other layers only.
    Raises ProjectConfigError when a config layer is not valid.
    """
    cfg = _config()
    if workspace is None:
        return tuple(json.loads((skill_root() / cfg.DEFAULTS).read_text(encoding="utf-8"))["gated_prefixes"])
    anchor = Path(workspace).resolve()
    try:
        at_root = cfg.project_root(anchor) == anchor
        return tuple(cfg.load(skill_root(), anchor, project_layers=at_root).get("gated_prefixes") or ())
    except cfg.ConfigError as exc:
        raise ProjectConfigError(str(exc)) from exc


def load_adapter() -> dict:
    name = load_config().get("adapter") or "github-issues"
    path = skill_root() / "adapters" / f"{name}.json"
    if not path.is_file():
        return {"name": name, "ticket_pattern": r"#\d+"}
    return json.loads(path.read_text(encoding="utf-8"))


def _adapter_name() -> str | None:
    """The adapter name for the state report; None when a config layer is not valid (the note says so)."""
    try:
        return load_adapter().get("name")
    except ProjectConfigError:
        return None


def ticket_pattern() -> re.Pattern[str]:
    adapter = load_adapter()
    pattern = adapter.get("ticket_pattern") or r"#\d+"
    return re.compile(pattern)


def script_path(name: str) -> Path:
    """Resolve a cadence script under the skill root."""
    root = skill_root().resolve()
    path = (root / "scripts" / name).resolve()
    if root not in path.parents:
        raise RuntimeError(f"refusing script outside skill root: {path}")
    return path


def load_session(workspace: Path) -> dict | None:
    ctx = canonical_context_dir(workspace)
    # Prefer local cadence-session; also follow session_store if present
    direct = ctx / "cadence-session.json"
    if direct.is_file():
        try:
            data = json.loads(direct.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict) and "ref" in data:
            try:
                return json.loads((ctx / str(data["ref"])).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return data if isinstance(data, dict) else None
    try:
        import session_store  # type: ignore

        return session_store.load_session(workspace)
    except Exception:
        return None


NOTE_INVALID_CONFIG = ".cadence.json is not valid; all writes are gated"


def _prefixes_for_message(anchors: list[Path]) -> tuple[list[str], Optional[str]]:
    """The prefixes of each anchor in order, no duplicates (#48 D5, D11).

    A bad `.cadence.json` gives the built-in prefixes and NOTE_INVALID_CONFIG.
    """
    prefixes: list[str] = []
    try:
        for anchor in anchors:
            prefixes.extend(p for p in gated_prefixes(anchor) if p not in prefixes)
    except ProjectConfigError:
        return list(gated_prefixes()), NOTE_INVALID_CONFIG
    return prefixes, None


def _prefix_matches(norm: str, prefix: str) -> bool:
    """A prefix with a trailing `/` is a directory; any other prefix is a name or a directory (#48 D4)."""
    if prefix.endswith("/"):
        return norm.startswith(prefix)
    return norm == prefix or norm.startswith(prefix + "/")


@lru_cache(maxsize=32)
def _folds_case(directory: str) -> bool:
    """True when the filesystem under `directory` reads `SRC/` and `src/` as one name (#119).

    The macOS default does. The probe compares the directory with its case-swapped
    spelling and creates nothing.
    """
    swapped = directory.swapcase()
    if swapped == directory:
        return False
    try:
        return os.path.samefile(directory, swapped)
    except OSError:
        return False


def is_gated_path(target_file: Optional[str], workspace: Optional[Path] = None) -> bool:
    """Under a gated prefix, from the path start (#48). With `workspace`, its `.cadence.json`
    extras count; a bad file gates all. On a filesystem that folds case, so does the match (#119)."""
    if not target_file:
        return False
    norm = target_file.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    try:
        prefixes = gated_prefixes(workspace)
    except ProjectConfigError:
        return True
    if workspace is not None and _folds_case(str(workspace)):
        norm = norm.lower()
        prefixes = tuple(p.lower() for p in prefixes)
    return any(_prefix_matches(norm, p) for p in prefixes)


def is_test_path(target_file: Optional[str]) -> bool:
    if not target_file:
        return False
    norm = target_file.replace("\\", "/")
    name = norm.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{norm}"
        or norm.startswith("tests/")
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


@lru_cache(maxsize=32)
def _primary_checkout(workspace: str) -> Path:
    """find_primary_checkout runs git; one call per workspace per hook process."""
    return find_primary_checkout(Path(workspace))


def _resolved(path: Path) -> list[Path]:
    """The normpath form, then the resolved form when it differs. A list: no hash-seed order."""
    forms = [Path(os.path.normpath(path))]
    try:
        real = path.resolve()
    except OSError:
        return forms
    if real not in forms:
        forms.append(real)
    return forms


def _relative(form: Path, bases: list[Path]) -> Optional[str]:
    for base in bases:
        try:
            return form.relative_to(base).as_posix()
        except ValueError:
            continue
    return None


def _target_path(target_file: str, workspace: Path) -> Path:
    raw = target_file.strip().strip('"').strip("'").replace("\\", "/")
    return Path(raw) if Path(raw).is_absolute() else Path(workspace) / raw


def workspace_relative(target_file: Optional[str], workspace: Path) -> Optional[str]:
    """`target_file` relative to the workspace (or its primary checkout); None when outside it.

    A relative path is read from the workspace, so `src/../../proj/src/a.py` and, from a
    subdirectory workspace, `../src/a.py` resolve to the file they name (#119).
    """
    if not target_file:
        return None
    bases = [base for root in (workspace, _primary_checkout(str(workspace))) for base in _resolved(Path(root))]
    for form in _resolved(_target_path(target_file, workspace)):
        rel = _relative(form, bases)
        if rel is not None:
            return rel
    return None


@lru_cache(maxsize=64)
def _toplevel(directory: str) -> Optional[Path]:
    from context_root import git_toplevel

    return git_toplevel(Path(directory))


def gate_anchor(target_file: Optional[str], workspace: Path) -> Path:
    """Git toplevel of the written file (G-C14), else the primary checkout of the workspace.

    With no `target_file`, the start directory is the workspace.
    """
    workspace = Path(workspace)
    start = workspace
    if target_file:
        target = _target_path(target_file, workspace)
        start = next((p for p in [target.parent, *target.parents] if p.is_dir()), workspace)
    return _toplevel(str(start)) or _primary_checkout(str(workspace))


def gate_anchors(target_file: Optional[str], workspace: Path) -> list[Path]:
    """The workspace repo root, its primary checkout, the file's repo root and the workspace (#48 D7).

    Each anchor gives a prefix match from its own root, with its own `.cadence.json`. The
    workspace itself is an anchor too, so a workspace at <mono>/services/api gates its src/.
    """
    workspace = Path(workspace)
    anchors: list[Path] = []
    seen: set[Path] = set()
    for anchor in (_toplevel(str(workspace)) or workspace, _primary_checkout(str(workspace)),
                   gate_anchor(target_file, workspace), workspace):
        key = anchor.resolve()
        if key not in seen:
            seen.add(key)
            anchors.append(anchor)
    return anchors


def gate_pairs(target_file: Optional[str], workspace: Path) -> list[tuple[Path, str]]:
    """(anchor, path relative to it) for each anchor and each form of the target (#48 D8, D10).

    A target form inside this worktree gives no pair for a primary checkout that holds
    the worktree, so the primary prefixes do not apply to the worktree's own files.
    """
    if not target_file:
        return []
    workspace = Path(workspace)
    forms = _resolved(_target_path(target_file, workspace))
    own = _toplevel(str(workspace)) or workspace
    own_forms = _resolved(own)
    primary = _primary_checkout(str(workspace)).resolve()
    pairs: list[tuple[Path, str]] = []
    for anchor in gate_anchors(target_file, workspace):
        other_primary = anchor.resolve() == primary and primary != own.resolve()
        for form in forms:
            if other_primary and _relative(form, own_forms) is not None:
                continue
            rel = _relative(form, _resolved(anchor))
            if rel is not None and (anchor, rel) not in pairs:
                pairs.append((anchor, rel))
    return pairs


def gated_write(target_file: Optional[str], workspace: Path) -> bool:
    """A write the P9 gate holds back: inside the workspace, and a gate pair that is not a test path
    and matches a prefix of its anchor's repo (#48).
    """
    if workspace_relative(target_file, workspace) is None:
        return False
    return any(not is_test_path(rel) and is_gated_path(rel, workspace=anchor)
               for anchor, rel in gate_pairs(target_file, workspace))


def context_relative(target_file: Optional[str], workspace: Path) -> Optional[str]:
    """Path of `target_file` inside `.context` (this checkout's or the primary's), else None."""
    if not target_file:
        return None
    raw = target_file.strip().strip('"').strip("'").replace("\\", "/")
    candidate = Path(raw) if Path(raw).is_absolute() else workspace / raw
    roots = {workspace / ".context", _primary_checkout(str(workspace)) / ".context"}
    forms = _path_forms(candidate)
    for root in roots:
        rel = _relative_to_any(forms, _path_forms(root))
        if rel is not None:
            return rel
    return None


def _path_forms(path: Path) -> set[Path]:
    """The normalized path and, when it resolves, the resolved one."""
    forms = {Path(os.path.normpath(path))}
    try:
        forms.add(path.resolve())
    except OSError:
        pass
    return forms


def _relative_to_any(forms: set[Path], bases: set[Path]) -> Optional[str]:
    """The first form's path under one of `bases` ("" for the base itself), else None."""
    for form in forms:
        for base in bases:
            try:
                rel = form.relative_to(base).as_posix()
            except ValueError:
                continue
            return "" if rel == "." else rel
    return None


def protected_writer(target_file: Optional[str], workspace: Path) -> Optional[str]:
    """Name the only sanctioned writer of a protected `.context` file, or None if unprotected.

    These files carry the pipeline's trust: the session (which phase the run is
    in), gate markers (e.g. P9's entry-invariant.ok) and the findings ledger.
    An agent editing them directly could skip the plan gate, escape the stop
    guard or mark its own findings verified.
    """
    rel = context_relative(target_file, workspace)
    if rel is None:
        return None
    if rel in ("", "cadence-session.json", ".active_skill") or rel == "sessions" or rel.startswith("sessions/"):
        return "scripts/activate_session.py"
    if rel == "gates" or rel.startswith("gates/"):
        return "the stage gate scripts (for example scripts/entry_invariant.py)"
    if rel == "handoff" or rel.startswith("handoff/"):
        return "scripts/handoff.py (write)"
    if rel == "attest" or rel.startswith("attest/"):
        # cd-58 D2: the handoff hook writes the peer model rows (<run>.models.jsonl).
        return ("hooks/guard_skill_gate.py (a delegated peer's write records it) and "
                "hooks/guard_subagent_handoff.py (peer model rows)")
    if rel == "subagent-runs" or rel.startswith("subagent-runs/"):
        # cd-64 C-1: on Claude Code the handoff hook writes the record for a lean peer call.
        return ("hooks/guard_subagent_handoff.py (Claude Code: a lean peer call) and "
                "scripts/subagent_provenance.py (begin on Pi and headless hosts, complete)")
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(".jsonl") and (rel.startswith("findings/") or name.startswith("findings")):
        return "scripts/findings_io.py (append, set-status) and scripts/ledger_verify.py"
    return None


_REDIRECT_RE = re.compile(r"(?:^|[^<>&0-9])\d?(?:>>|>\||&>>|&>|>)(?!&)\s*([^\s;&|<>()]+)")
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&"}
_SEPARATOR_RUN = re.compile(r"[;&|()]+")  # shlex joins `);` or `&&(` into one token
_PREFIX_WORDS = {"sudo", "env", "command", "nohup", "time", "exec", "builtin"}


_REDIRECT_OPS = {">", ">>", ">|", "&>", "&>>"}
_DUP_OPS = {">&", "<&"}          # fd duplication (2>&1): no file is written
_INPUT_OPS = {"<", "<<", "<<<"}  # the next word is read, not written


_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies: they are data written by the command, not commands."""
    out, pending = [], []
    for line in command.split("\n"):
        if pending:
            dash, word = pending[0]
            if (line.lstrip("\t") if dash else line) == word:
                pending.pop(0)
            continue
        out.append(line)
        pending = [(m.group(1) == "-", m.group(3)) for m in _HEREDOC.finditer(line)]
    return "\n".join(out)


def _strip_comments(text: str) -> str:
    """Drop shell comments. As in bash, `#` starts one only at a word start, outside quotes.

    shlex treats `#` as a comment anywhere, so `PR_REVIEW_#1.md` lost its tail.
    """
    out, quote, i = [], "", 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            elif ch == "\\" and quote == '"' and i + 1 < len(text):
                out.append(ch)
                i += 1
                ch = text[i]
        elif ch in "'\"":
            quote = ch
        elif ch == "\\" and i + 1 < len(text):
            out.append(ch)
            i += 1
            ch = text[i]
        elif ch == "#" and (i == 0 or text[i - 1].isspace() or text[i - 1] in ";&|("):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _redirect_kind(token: str, current: list[str]) -> str | None:
    """Return "write", "read" (`<`) or "skip" for the word after a redirect operator, or None for another token.

    A bare fd number right before a redirect (2>/dev/null) is not an argument: it leaves `current`.
    """
    if token not in _REDIRECT_OPS and token not in _DUP_OPS and token not in _INPUT_OPS:
        return None
    if token not in _INPUT_OPS and current and current[-1].isdigit():
        current.pop()
    return "write" if token in _REDIRECT_OPS else "read" if token == "<" else "skip"


@lru_cache(maxsize=64)
def _parse(command: str) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...], tuple[str, ...]]:
    """Quote-aware split into simple commands, plus the files redirections write to and `<` reads."""
    text = _strip_comments(_strip_heredocs(command)).replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.commenters = ""
    lexer.whitespace_split = True
    commands: list[tuple[str, ...]] = []
    current: list[str] = []
    redirects: list[str] = []
    reads: list[str] = []
    words: dict[str, list[str]] = {"write": redirects, "read": reads}
    pending = None  # "write" | "read" | "skip" for the word after an operator
    for token in lexer:
        if pending:
            words.get(pending, []).append(token)
            pending = None
            continue
        pending = _redirect_kind(token, current)
        if pending:
            continue
        if token in _SEPARATORS or _SEPARATOR_RUN.fullmatch(token):
            if current:
                commands.append(tuple(current))
            current = []
        else:
            current.append(token)
    if current:
        commands.append(tuple(current))
    return tuple(commands), tuple(redirects), tuple(reads)


def _split_commands(command: str) -> tuple[list[list[str]], list[str]]:
    commands, redirects, _reads = _parse(command)
    return [list(c) for c in commands], list(redirects)


def _operands(args: list[str], takes_value: set[str] = frozenset()) -> list[str]:
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in takes_value:
            skip = True
            continue
        if arg.startswith("-") and arg != "-":
            continue
        out.append(arg)
    return out


def shell_write_targets(command: str) -> list[str]:
    """Best-effort list of paths a shell command writes, creates or deletes.

    Covers redirections (> >> >| &>), tee, sed -i / perl -i, cp/mv/install/ln
    destinations, rm/unlink/shred/truncate/touch operands and dd of=. Writes made
    inside an interpreter (python -c, a script) are not visible here.
    """
    try:
        commands, targets = _split_commands(command)
    except ValueError:
        # Not tokenizable (e.g. a heredoc body with an apostrophe): fall back to a
        # plain scan for redirections, the common way such commands write files.
        commands, targets = [], [m.group(1) for m in _REDIRECT_RE.finditer(_strip_heredocs(command))]
    for words in commands:
        words = _drop_prefix_words(words)
        writer = _COMMAND_TARGETS.get(Path(words[0]).name) if words else None
        if writer is not None:
            targets += writer(Path(words[0]).name, words[1:])
    return _unique_targets(targets)


def _drop_prefix_words(words: list[str]) -> list[str]:
    """Drop leading VAR=value assignments and prefix words (env, sudo, ...)."""
    while words and ("=" in words[0] and not words[0].startswith("-") or words[0] in _PREFIX_WORDS):
        words = words[1:]
    return words


def _short_flag_has_i(arg: str) -> bool:
    return arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]


def _sed_targets(_cmd: str, args: list[str]) -> list[str]:
    """sed -i / --in-place: the files; the first operand is the script unless -e/-f gave it."""
    if not any(a.startswith("--in-place") or _short_flag_has_i(a) for a in args):
        return []
    has_script_opt = any(a in ("-e", "-f") or a.startswith(("--expression", "--file")) for a in args)
    ops = _operands(args, {"-e", "-f"})
    return ops if has_script_opt else ops[1:]


def _perl_targets(_cmd: str, args: list[str]) -> list[str]:
    return _operands(args, {"-e", "-E"}) if any(_short_flag_has_i(a) for a in args) else []


def _copy_targets(cmd: str, args: list[str]) -> list[str]:
    """cp/mv/install/ln: the destination (-t DIR or the last operand); mv also deletes its sources."""
    targets = []
    ops = _operands(args, {"-t", "--target-directory"})
    if "-t" in args:
        i = args.index("-t")
        if i + 1 < len(args):
            targets.append(args[i + 1])
    elif len(ops) >= 2:
        targets.append(ops[-1])
        ops = ops[:-1]
    if cmd == "mv":
        targets += ops  # a move deletes its sources
    return targets


def _operand_targets(_cmd: str, args: list[str]) -> list[str]:
    return _operands(args)


def _truncate_targets(_cmd: str, args: list[str]) -> list[str]:
    return _operands(args, {"-s", "--size", "-r", "--reference"})


def _dd_targets(_cmd: str, args: list[str]) -> list[str]:
    return [a[3:] for a in args if a.startswith("of=")]


# Command name -> the paths it writes. A new writer is one entry here.
_COMMAND_TARGETS = {
    "tee": _operand_targets,
    "sed": _sed_targets,
    "gsed": _sed_targets,
    "perl": _perl_targets,
    **{cmd: _copy_targets for cmd in ("cp", "mv", "install", "ln")},
    **{cmd: _operand_targets for cmd in ("rm", "unlink", "shred", "touch")},
    "truncate": _truncate_targets,
    "dd": _dd_targets,
}


def _unique_targets(targets: list[str]) -> list[str]:
    """Targets in first-seen order, without empties, duplicates or /dev/null."""
    seen, out = set(), []
    for t in targets:
        if t and t not in seen and t != "/dev/null":
            seen.add(t)
            out.append(t)
    return out


# Backstop for what the tokenizer can't resolve (cd, $VARS, ~, ../): a command that
# names protected pipeline state, writes something, and has a target the parser
# cannot place is denied. Resolvable targets are judged precisely instead.
_PROTECTED_MENTION = re.compile(
    r"cadence-session\.json|\.active_skill\b|sessions/by-(?:worktree|task)|\.context/sessions\b|"
    r"\.context/gates\b|\.context/handoff\b|entry-invariant(?:-[\w-]+)?\.ok|draft-pr(?:-[\w-]+)?\.json|findings[\w.-]*\.jsonl|\.context/findings\b|"
    r"\.context/attest\b|\.context/subagent-runs\b|\.(?:cursor|context)/plans\b|"
    r"\.context/?(?=$|[\s;&|)'\"])"
)
_WRITE_COMMANDS = {"tee", "cp", "mv", "install", "ln", "rm", "unlink", "shred", "truncate", "touch", "dd"}
_RAW_WRITE = re.compile(r"(?:>|\btee\b|\bsed\s+(?:-\w*i|--in-place)|\bperl\s+-\w*i|\b(?:cp|mv|install|ln|rm|unlink|shred|truncate|touch|dd)\b)")
_UNRESOLVED = ("$", "`", "~", "..")


def _command_words(command: str) -> list[list[str]] | None:
    try:
        commands, _ = _split_commands(command)
    except ValueError:
        return None
    out = []
    for words in commands:
        while words and ("=" in words[0] and not words[0].startswith("-") or words[0] in _PREFIX_WORDS):
            words = words[1:]
        if words:
            out.append(words)
    return out


def shell_reads(command: str) -> Optional[tuple[list[list[str]], list[str]]]:
    """The simple commands (prefix words dropped) and the files that `<` reads; None when not tokenizable."""
    commands = _command_words(command)
    return None if commands is None else (commands, list(_parse(command)[2]))


def _has_write_construct(command: str) -> bool:
    commands = _command_words(command)
    if commands is None:
        return bool(_RAW_WRITE.search(_strip_heredocs(command)))
    if any(r != "/dev/null" for r in _split_commands(command)[1]):
        return True
    for words in commands:
        cmd = Path(words[0]).name
        if cmd in _WRITE_COMMANDS:
            return True
        if cmd in ("sed", "gsed", "perl") and any(
            a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--") and "i" in a[1:])
            for a in words[1:]
        ):
            return True
    return False


def _has_unresolved_target(command: str) -> bool:
    commands = _command_words(command)
    if commands is None:
        return True  # unparseable: assume the worst
    if any(Path(words[0]).name in ("cd", "pushd") for words in commands):
        return True
    return any(any(mark in t for mark in _UNRESOLVED) for t in shell_write_targets(command))


def protected_shell_mention(command: str) -> Optional[str]:
    """The protected name a writing shell command mentions when its targets can't be resolved.

    A plan directory is protected only after P9 (plan_document), so any other name comes first.
    """
    names = [m.group(0).strip(" /'\"=") for m in _PROTECTED_MENTION.finditer(_strip_heredocs(command))]
    if names and _has_write_construct(command) and _has_unresolved_target(command):
        return next((name for name in names if not name.endswith("/plans")), names[0])
    return None


# The plan freeze after P9: the two places commands/plan-review.md resolves a plan. While the run's
# P9 marker exists, a write there is denied: a plan change in execution is a new review cycle.
PLAN_DIRS = (".cursor/plans", ".context/plans")
PLAN_FROZEN_ACTION = "stop and tell the user what the plan must change; do not edit the plan"


def plan_document(target_file: Optional[str], workspace: Path) -> Optional[str]:
    """The workspace-relative path of `target_file` when it is under a plan directory, else None."""
    rel = workspace_relative(target_file, workspace)
    if rel is None or not any(rel == d or rel.startswith(d + "/") for d in PLAN_DIRS):
        return None
    return rel


class CadenceGate:
    skill_name = "cadence"

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.session = load_session(self.workspace)

    def get_state(self) -> dict:
        active = bool(self.session and self.session.get("active_skill") == self.skill_name)
        prefixes, note = _prefixes_for_message(gate_anchors(None, self.workspace))
        state = {
            "skill": self.skill_name,
            "skill_root": str(skill_root()),
            "active": active,
            "session": {
                "task_id": (self.session or {}).get("task_id"),
                "resume_slug": (self.session or {}).get("resume_slug"),
                "pipeline_run_id": (self.session or {}).get("pipeline_run_id"),
            }
            if self.session
            else None,
            "gated_prefixes": prefixes,
            "adapter": _adapter_name(),
            "activate_script": str(script_path("activate_session.py")),
            "entry_invariant_script": str(script_path("entry_invariant.py")),
        }
        if note:
            state["gated_prefixes_note"] = note
        return state

    def check_action(self, action: str, target_file: Optional[str] = None) -> GateResult:
        action_l = action.lower()
        activate = script_path("activate_session.py")

        if not self.session or self.session.get("active_skill") != self.skill_name:
            return GateResult(
                allowed=False,
                message="BLOCKED: no active cadence session",
                action_required=f'python3 "{activate}" --from-branch --fresh --supersede',
            )

        # Read-only always ok
        if action_l in {"read", "grep", "glob", "shell_readonly"}:
            return GateResult(True, "ALLOWED: read-only")
        if action_l in PR_TALK_ACTIONS:
            return self._before_closure(action_l)

        if action_l in WRITE_ACTIONS:
            writer = protected_writer(target_file, self.workspace)
            if writer:
                return GateResult(
                    allowed=False,
                    message=f"BLOCKED: `{target_file}` is pipeline state written only by {writer}",
                    action_required=f"run {writer} instead of editing the file",
                )
            frozen = self._plan_frozen(target_file)
            if frozen:
                return frozen

        # Gated source edits require entry invariant clearance flag or prior OK
        if action_l in WRITE_ACTIONS and gated_write(target_file, self.workspace):
            held = self._held_before_p9(target_file)
            if held:
                return held

        return GateResult(True, f"ALLOWED: {action_l}")

    def _before_closure(self, action: str) -> GateResult:
        """BLOCKED until the R5 breadcrumb of the workspace's run names HEAD (gh-178)."""
        import subprocess

        closure = script_path("closure.py")  # puts the skill's scripts/ on sys.path
        from closure import closure_problem
        from pipeline_run_binding import run_for

        ctx = canonical_context_dir(self.workspace)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.workspace, capture_output=True, text=True).stdout.strip()
        why = closure_problem(ctx, run_for(ctx, cwd=self.workspace), head)
        if why is None:
            return GateResult(True, f"ALLOWED: {action}")
        return GateResult(allowed=False, message=f"BLOCKED: `{action}` waits for R5 (closure) of this run at HEAD: {why}",
                          action_required=f'python3 "{closure}"')

    def _run_of(self, target_file: Optional[str]) -> tuple[Path, Optional[dict]]:
        """The context dir and the run of the target's worktree when it has one, else the gate's run."""
        # cd-76 rev5 D1: only the marker read changes. Lazy import (R3-D3d).
        from pipeline_run_binding import run_for

        ctx = canonical_context_dir(self.workspace)
        return ctx, run_for(ctx, target=_target_path(str(target_file), self.workspace)) or self.session

    def _plan_frozen(self, target_file: Optional[str]) -> Optional[GateResult]:
        """BLOCKED when `target_file` is a plan document and its run has a P9 marker, else None.

        The per-run marker counts by entry_invariant_matches. The legacy shared marker counts
        only when it is JSON that names this run's `pipeline_run_id` and `task_id` (PR #168 R1 N-4).
        """
        from artifact_requirements import entry_invariant_matches, entry_invariant_ok_path

        rel = plan_document(target_file, self.workspace)
        if rel is None:
            return None
        ctx, run = self._run_of(target_file)
        run_id = str((run or {}).get("pipeline_run_id") or "")
        own, legacy = entry_invariant_ok_path(ctx, run_id), entry_invariant_ok_path(ctx)
        marker = own if entry_invariant_matches(ctx, run, own) else legacy if _names_run(legacy, run) else None
        if marker is None:
            return None
        shown = workspace_relative(str(marker), self.workspace) or str(marker)
        return GateResult(
            allowed=False,
            message=(f"BLOCKED: the plan is frozen while the P9 marker exists ({shown}). "
                     f"A plan change in execution is a new review cycle. Plan file: `{rel}`"),
            action_required=PLAN_FROZEN_ACTION,
        )

    def _held_before_p9(self, target_file: Optional[str]) -> Optional[GateResult]:
        """BLOCKED when the run of the target has no entry invariant marker, else None."""
        from artifact_requirements import entry_invariant_matches

        ctx, run = self._run_of(target_file)
        if entry_invariant_matches(ctx, run):
            return None
        anchors = list(dict.fromkeys(anchor for anchor, _rel in gate_pairs(target_file, self.workspace)))
        prefixes, note = _prefixes_for_message(anchors)
        return GateResult(
            allowed=False,
            message=(
                f"BLOCKED: gated path `{target_file}` requires entry invariant "
                f"(prefixes={prefixes})" + (f"\nNOTE: {note}" if note else "")
            ),
            action_required=f'python3 "{script_path("entry_invariant.py")}" --phase execution',
        )


def _names_run(marker: Path, run: Optional[dict]) -> bool:
    """The legacy marker is JSON that names the run's `pipeline_run_id` and `task_id`. Unlike
    entry_invariant_matches, a file that does not parse counts as no: the legacy name is not per
    run, and a stale or empty file must not freeze the plan of a new run (PR #168 R1 N-4, N-18)."""
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not run:
        return False
    return all(data.get(k) and data.get(k) == run.get(k) for k in ("pipeline_run_id", "task_id"))


GATES = {"cadence": CadenceGate}


def main() -> int:
    parser = argparse.ArgumentParser(description="Cadence skill gate (Cursor + Pi CLI)")
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="Check if action is allowed")
    check_p.add_argument("--skill", required=True, choices=GATES.keys())
    check_p.add_argument("--action", required=True)
    check_p.add_argument("--file", help="Target file")
    check_p.add_argument("--workspace", default=".")

    state_p = sub.add_parser("state", help="Show current gate state")
    state_p.add_argument("--skill", required=True, choices=GATES.keys())
    state_p.add_argument("--workspace", default=".")

    sub.add_parser("list", help="List skills with gates")

    args = parser.parse_args()
    if args.command == "list":
        for name in GATES:
            print(f"  - {name}")
        return 0

    gate = GATES[args.skill](Path(args.workspace))
    if args.command == "state":
        print(json.dumps(gate.get_state(), indent=2))
        return 0

    result = gate.check_action(args.action, args.file)
    print(result.message)
    if result.action_required:
        print(f"ACTION: {result.action_required}")
    return 0 if result.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
