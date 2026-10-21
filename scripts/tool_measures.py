"""R3 `bugs` and `types`: the findings of two pinned tools on the added lines of a change (docs/tool-gates.md).

A finding counts only on an added line (`git diff -U0` against the merge base, and each untracked
file; with no merge base, `--path`, every line). A count above `max` (0) fails the measure. Each tool
is an external process, `uv tool run --offline <tool>==<pin>`, with its caches in a temporary
directory: a gate run never fetches. A missing tool and a timeout are `unsupported` (the record says
why and names the cache warm command), and the `unsupported` setting gives the verdict. An exit code
that is not on the tool's list, or a report that is not JSON, fails. Under CADENCE_GATES_READONLY
(the stop guard walk, 4 s for R3) the tools do not run: the record says so and the verdict is `warn`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from gate_common import GATES_READONLY_ENV, measure_record
from impl_artifact import GitError

# The version pin is the rule-set pin: the rules of ruff and mypy are part of the pinned binary.
TOOLS = {"ruff": "0.17.0", "mypy": "2.4.0"}
# docs/tool-gates.md says why each ignored rule is out (B008, B904, S108).
RUFF_RULES = ["--select", "E9,F63,F7,F82,F811,B,PLE,E722,PGH004,S",
              "--ignore", "S101,S603,S607,S404,S105,S106,B008,B904,S108"]
MYPY_RULES = ["--check-untyped-defs", "--ignore-missing-imports"]
# measure -> (tool, rule arguments, the other arguments; "{tmp}" is the run's cache directory, exit codes allowed)
MEASURES = {
    "bugs": ("ruff", RUFF_RULES, ["check", "--no-cache", "--isolated", "--output-format", "json"], (0, 1)),
    "types": ("mypy", MYPY_RULES, ["--no-incremental", "--cache-dir", "{tmp}", "--config-file=", "--output", "json"],
              (0, 1, 2)),
}
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def warm_command(tool: str) -> str:
    """The command that fills the uv cache with the pinned tool once, with the network. It puts
    nothing on PATH, so a user's own ruff or mypy stays."""
    return f"uv tool run {tool}=={TOOLS[tool]} --version"


def _target(line: str) -> str | None:
    """The file of a `+++ ` line; None for /dev/null. git C-quotes a path with a quote, a backslash or a control byte."""
    target = line[4:].rstrip("\t")
    if target.startswith('"'):
        target = target[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        target = target.encode("latin-1").decode("utf-8", "replace")
    return None if target == "/dev/null" else target[2:]


def parse_added(diff: str) -> dict[str, set[int]]:
    """The added line numbers of each file in a `git diff -U0` text. A deleted file is not listed."""
    out: dict[str, set[int]] = {}
    path = None
    for line in diff.splitlines():
        match = _HUNK.match(line)
        if line.startswith("+++ "):
            path = _target(line)
        elif match and path is not None:
            start, count = int(match[1]), int(match[2] if match[2] is not None else 1)
            out.setdefault(path, set()).update(range(start, start + count))
    return out


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root, capture_output=True, text=True,
                            timeout=30, errors="replace")
    if result.returncode != 0:
        raise GitError(f"git {args[0]} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def added_lines(root: Path, fork: str | None, files: list[str]) -> dict[str, set[int] | None]:
    """The added lines of each file since `fork`; None: every line (no fork, or an untracked file)."""
    if fork is None:
        return {rel: None for rel in files}
    diff = parse_added(_git(root, "diff", "-U0", "--no-color", "--no-ext-diff", "--src-prefix=a/",
                            "--dst-prefix=b/", fork))
    untracked = set(_git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return {rel: None if rel in untracked else diff.get(rel, set()) for rel in files}


def on_added(findings: list[tuple[str, int, str]], added: dict[str, set[int] | None]) -> list[tuple[str, int, str]]:
    """The findings whose line is an added line of a changed file."""
    kept = []
    for path, line, text in findings:
        lines = added.get(path, set())
        if lines is None or line in lines:
            kept.append((path, line, text))
    return kept


def _run(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess | None:
    """The finished process, or None after a timeout. A timeout ends the tool and its children."""
    proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def _ruff(stdout: str, root: Path) -> list[tuple[str, int, str]]:  # ruff names each file by its absolute path
    return [(Path(f["filename"]).resolve().relative_to(root.resolve()).as_posix(), f["location"]["row"],
             f"{f['code']} {f['message']}") for f in json.loads(stdout)]


def _mypy(stdout: str, root: Path) -> list[tuple[str, int, str]]:
    rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    fatal = [r["message"] for r in rows if r["line"] < 1]
    if fatal:  # a file-level error, such as a duplicate module: no line can be judged
        raise ValueError(f"mypy cannot check the files: {fatal[0]}")
    return [(Path(r["file"]).as_posix(), r["line"], f"{r['code']} {r['message']}") for r in rows
            if r["severity"] == "error"]


def _groups(files: list[str]) -> list[list[str]]:
    """Groups of files with no two of the same name. mypy stops on two modules of one name (two conftest.py)."""
    groups: list[list[str]] = []
    for rel in files:
        spot = next((g for g in groups if all(Path(o).name != Path(rel).name for o in g)), None)
        if spot is None:
            groups.append([rel])
        else:
            spot.append(rel)
    return groups


@dataclass
class Outcome:
    findings: list[tuple[str, int, str]] = field(default_factory=list)
    unsupported: str | None = None  # the reason the tool did not run
    error: str | None = None  # the tool ran and its report cannot be read: the measure fails
    timed_out: bool = False
    readonly: bool = False  # not run under CADENCE_GATES_READONLY: the verdict is warn, whatever `unsupported` says


def _call(measure: str, files: list[str], root: Path, timeout: float, tmp: str) -> Outcome:
    tool, rules, other, codes = MEASURES[measure]
    prefix = ["uv", "tool", "run", "--offline", f"{tool}=={TOOLS[tool]}"]
    try:
        probe = _run([*prefix, "--version"], root, timeout)
        if probe is not None and (probe.returncode != 0 or TOOLS[tool] not in probe.stdout):
            return Outcome(unsupported=f"{tool} {TOOLS[tool]} is not in the uv cache: run `{warm_command(tool)}` once")
        done = None if probe is None else _run([*prefix, *(a.replace("{tmp}", tmp) for a in other), *rules, *files],
                                               root, timeout)
    except OSError as exc:
        return Outcome(unsupported=f"uv does not start ({exc}): install uv, then run `{warm_command(tool)}` once")
    if done is None:
        return Outcome(unsupported=f"{tool} {TOOLS[tool]} timed out after {timeout:g} s", timed_out=True)
    if done.returncode not in codes or (done.returncode == 2 and not done.stdout.strip()):
        return Outcome(error=f"{tool} exited {done.returncode}: {(done.stderr or done.stdout).strip()[-300:]}")
    try:
        return Outcome(findings=(_ruff if tool == "ruff" else _mypy)(done.stdout, root))
    except (ValueError, KeyError, TypeError) as exc:
        return Outcome(error=f"{tool} report cannot be read: {exc}")


def _outcome(measure: str, files: list[str], root: Path, timeout: float) -> Outcome:
    if not files:
        return Outcome()
    if os.environ.get(GATES_READONLY_ENV) == "1":
        return Outcome(unsupported=f"not run under {GATES_READONLY_ENV}=1: the stop guard gives R3 4 s", readonly=True)
    total = Outcome()
    with tempfile.TemporaryDirectory(prefix=f"cadence-{measure}-") as tmp:
        for group in _groups(files) if measure == "types" else [files]:
            one = _call(measure, group, root, timeout, tmp)
            if one.unsupported or one.error:
                return one
            total.findings += one.findings
    return total


def _sha(items: list[str]) -> str:
    return hashlib.sha256(json.dumps(items).encode("utf-8")).hexdigest()


def measure(root: Path, fork: str | None, files: list[str], cfg: dict) -> list[dict]:
    """One record for `bugs` and one for `types` on the changed code `files` (`cfg`: section -> config)."""
    python = [rel for rel in files if rel.endswith(".py")]
    others = [f"{rel}: not Python" for rel in files if not rel.endswith(".py")]
    added = added_lines(root, fork, python)
    targets = [rel for rel in python if added[rel] is None or added[rel]]
    head = None if fork is None else _git(root, "rev-parse", "HEAD").strip()
    out = []
    for name, (tool, rules, other, _codes) in MEASURES.items():
        section = cfg[name]
        run = _outcome(name, targets, root, section["timeout_seconds"])
        found = sorted(on_added(run.findings, added))
        offenders = [f"{path}:{line} {text}" for path, line, text in found] + ([run.error] if run.error else [])
        record = measure_record(name, len(found), section["max"], offenders,
                                failed=len(found) > section["max"] or run.error is not None,
                                threshold_source=section["threshold_source"]["max"],
                                unmeasured=others + ([run.unsupported] if run.unsupported else []),
                                unsupported="warn" if run.readonly else section["unsupported"])
        record.update(tool=tool, version=TOOLS[tool], args_sha256=_sha([tool, TOOLS[tool], *other, *rules]),
                      rules_sha256=_sha(rules), base=fork, head=head, timed_out=run.timed_out,
                      unsupported=run.unsupported, files=len(targets))
        out.append(record)
    return out
