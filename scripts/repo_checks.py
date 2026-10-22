"""R5 runs the repository's own checks: the code works and passes them, or the run is not done.

The commands are `ci_commands` of the resolved config (scripts/cadence_config.py), else what the tree shows: `make test`,
pytest, `npm test`, `go test ./...` or `cargo test`. A failing command becomes a must-fix row of the
run, with the command as its check, so the fix round fixes it and `ledger_verify.py` closes it. A
command that the check rules do not take (`gradle test`) is the check through `repo_checks.py --run`,
which runs it only while the repo still names it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1] / "shared") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))

from findings_io import FindingsValidationError, append_finding, check_problem, load_findings, task_id_prefix  # noqa: E402

TIMEOUT_SECONDS = 1800
_PYTHON_MARKERS = ("pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini")
# The runner of a check is not there: the environment cannot run it, and no code change in the PR can fix that.
_NO_RUNNER = re.compile(r"No module named [\w.]+|command not found|not found: |is not recognized as")


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def check_commands(worktree: Path) -> list[str]:
    """`ci_commands` of the resolved config for `worktree`, else the test commands that the tree shows."""
    import cadence_config

    configured = list(cadence_config.load(Path(__file__).resolve().parent.parent, worktree).get("ci_commands") or [])
    if configured:
        return configured
    if re.search(r"(?m)^test\s*:", _text(worktree / "Makefile")):
        return ["make test"]
    found = []
    if any((worktree / m).is_file() for m in _PYTHON_MARKERS) and (worktree / "tests").is_dir():
        found.append(f"{_python(worktree)} -m pytest -q")
    try:
        test_script = json.loads(_text(worktree / "package.json") or "{}").get("scripts", {}).get("test", "")
    except (ValueError, AttributeError):
        test_script = ""
    if test_script and "no test specified" not in test_script:
        found.append("npm test")
    found += [cmd for name, cmd in (("go.mod", "go test ./..."), ("Cargo.toml", "cargo test"))
              if (worktree / name).is_file()]
    return found


def _python(worktree: Path) -> str:
    """The interpreter that has pytest: the repo's own venv, else the one that runs this gate, else `python3`.

    A child shell does not see a shell alias or a function, so `python3` there can be another interpreter."""
    for venv in (".venv", "venv"):
        if (worktree / venv / "bin" / "python").is_file():
            return f"{venv}/bin/python"
    return shlex.quote(sys.executable) if importlib.util.find_spec("pytest") else "python3"


def _run(cmd: str, worktree: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, shell=True, cwd=worktree, capture_output=True, text=True,  # noqa: S602 - the repo's own check
                              timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {TIMEOUT_SECONDS} s"
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    return proc.returncode, " / ".join(tail)


def row_check(cmd: str) -> str:
    """The ledger check of the repo check `cmd`: the command, else `--run` when the check rules do not take it."""
    return cmd if check_problem(cmd) is None else f"python3 $CADENCE_HOME/scripts/repo_checks.py --run {shlex.quote(cmd)}"


def _ledger_row(ledger: Path, task_id: str, cmd: str, evidence: str) -> str | None:
    """The id of the open row that holds the failing `cmd`: an existing one, else a new `<task>-F<n>`."""
    cmd = row_check(cmd)
    rows = load_findings(ledger)
    held = [r for r in rows if r.get("stage") == "closure" and r.get("check") == cmd and r.get("status") != "verified"]
    if held:
        return str(held[0]["id"])
    prefix = task_id_prefix(task_id)
    taken = [int(m.group(1)) for r in rows if (m := re.match(rf"^{re.escape(prefix)}-F(\d+)$", str(r.get("id"))))]
    row = {"id": f"{prefix}-F{max(taken, default=0) + 1}", "stage": "closure", "rule_ref": "core:repo-checks",
           "class": "bug", "severity": "must-fix", "location": "repository checks", "evidence": evidence,
           "check": cmd, "status": "open"}
    try:
        return str(append_finding(ledger, row)["id"])
    except FindingsValidationError:
        return None


def check_problems(worktree: Path, ledger: Path, task_id: str) -> list[str]:
    """Each repository check that fails at HEAD, with the ledger row that now holds it."""
    problems = []
    for cmd in check_commands(worktree):
        code, tail = _run(cmd, worktree)
        if code == 0:
            continue
        if code == 127 or _NO_RUNNER.search(tail):
            problems.append(f"repo check `{cmd}` cannot run here ({tail}). This is the environment, not the code: "
                            "set `ci_commands` in the project's .cadence.json to a command that runs here, "
                            "or record `--hard-stop credentials` with the missing tool. No ledger row is written")
            continue
        evidence = f"`{cmd}` exits {code} at HEAD: {tail}"
        row = _ledger_row(ledger, task_id, cmd, evidence) if task_id else None
        held = f"Ledger row {row} holds it: send the fix round" if row else "Fix it in the fix round"
        problems.append(f"repo check {evidence}. {held}")
    return problems


def main(argv: list[str]) -> int:
    """`--run <cmd>`: run a repo check of this tree. It runs only a command that the repo names now."""
    if len(argv) != 2 or argv[0] != "--run":
        print("usage: repo_checks.py --run <command of check_commands>", file=sys.stderr)
        return 2
    if argv[1] not in check_commands(Path.cwd()):
        print(f"`{argv[1]}` is not a repo check of this tree: {check_commands(Path.cwd())}", file=sys.stderr)
        return 2
    return subprocess.run(argv[1], shell=True, timeout=TIMEOUT_SECONDS).returncode  # noqa: S602 - a repo check, named above


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
