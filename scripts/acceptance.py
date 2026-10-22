#!/usr/bin/env python3
"""Acceptance checks of the run: the request, as commands that exit 0.

P1 writes `.context/acceptance/<task_id>.json`. Each check quotes the words of the request
that it proves and names one command. The command runs in the worktree, so it reads the
delivered files, not the diff:

    {"RUN_BY_COMMAND": "plan-create", "task_id": "gh-7",
     "checks": [{"id": "AC-1", "claim": "downstream reads only out/marts/",
                 "run": "python3 acceptance/check_marts.py"}]}

P9 pins the sha256 of the file and of each file that a command names. R5 runs each check at
HEAD and fails when one exits non-zero, or when the file or a pinned script changed after P9.
A weaker check after P9 is a new plan review, not an edit.

A pinned check that is wrong has a repair path with no person in it: no code can pass it, or
code that breaks its claim passes it. E1, R1 or R5 records the defect with a command that shows
it. The plan peer fixes the check, keeps its claim,
and re-pins that one check. Both rows go to `.context/acceptance/<task_id>.repair.jsonl`, which R1
reads, so a reviewer sees each check that changed after P9.

    python3 acceptance.py check   # P1 and P9: the file is there, each check can prove something, its scripts are committed
    python3 acceptance.py pin     # P9: pin the checks
    python3 acceptance.py run     # E1 and R5: run the checks at HEAD
    python3 acceptance.py defect --id AC-1 --repro "<command>" --why "<reason>"   # E1, R1 or R5: AC-1 is wrong
    python3 acceptance.py repin --id AC-1                                          # the plan peer, after the fix
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

MARKERS = ("plan-create", "plan-update")
_ID = re.compile(r"^AC-\d+$")
TIMEOUT_SECONDS = 600
TAIL_LINES = 3
_CLI = 'python3 "$CADENCE_HOME/scripts/acceptance.py"'


def _session(context_dir: Path, worktree: Path | None = None) -> dict:
    """The run of `worktree` (never the root redirect when worktrees share one .context), else the session."""
    from artifact_requirements import current_session
    from pipeline_run_binding import run_for

    run = run_for(Path(context_dir), cwd=Path(worktree)) if worktree is not None else None
    return run or current_session(Path(context_dir)) or {}


def _task_file(task_id: str) -> str:
    return re.sub(r"[\\/]", "-", task_id)


def acceptance_path(context_dir: Path, session: dict | None = None) -> Path | None:
    task = str((session if session is not None else _session(context_dir)).get("task_id") or "")
    return Path(context_dir) / "acceptance" / f"{_task_file(task)}.json" if task else None


def pin_path(context_dir: Path, session: dict | None = None) -> Path | None:
    run = str((session if session is not None else _session(context_dir)).get("pipeline_run_id") or "")
    return Path(context_dir) / "gates" / f"acceptance-{run}.json" if run else None


def pinned_scripts(context_dir: Path, worktree: Path | None = None) -> tuple[str, ...]:
    """The check scripts that P9 pinned for the run. They are the plan's checks, not the change: a fix round
    cannot edit one without a repin, so R2 and R3 do not measure them (live run x2 failed R3 on one)."""
    pinned = pin_path(context_dir, _session(context_dir, worktree))
    try:
        scripts = json.loads(pinned.read_text(encoding="utf-8")).get("scripts") if pinned is not None else None
    except (OSError, ValueError, AttributeError):
        return ()
    return tuple(sorted(scripts)) if isinstance(scripts, dict) else ()


def repair_path(context_dir: Path, session: dict | None = None) -> Path | None:
    task = str((session if session is not None else _session(context_dir)).get("task_id") or "")
    return Path(context_dir) / "acceptance" / f"{_task_file(task)}.repair.jsonl" if task else None


def _repair_rows(context_dir: Path, session: dict | None = None) -> list[dict]:
    path = repair_path(context_dir, session)
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path else []
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") in ("defect", "repin"):
            rows.append(row)
    return rows


def open_defects(context_dir: Path, session: dict | None = None) -> dict[str, dict]:
    """{check id: its defect row} for each defect with no repin after it."""
    found: dict[str, dict] = {}
    for row in _repair_rows(context_dir, session):
        if row["kind"] == "defect":
            found[str(row.get("id"))] = row
        else:
            found.pop(str(row.get("id")), None)
    return found


def _append_repair(context_dir: Path, session: dict, row: dict) -> None:
    path = repair_path(context_dir, session)
    if path is None:
        raise ValueError("the session has no task_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _check_problems(index: int, check: object, seen: set[str]) -> list[str]:
    from findings_io import check_problem

    if not isinstance(check, dict):
        return [f"acceptance: check {index + 1} is not an object"]
    ident = str(check.get("id") or "")
    problems = []
    if not _ID.match(ident):
        problems.append(f"acceptance: check {index + 1} id {ident!r} is not AC-<n>")
    elif ident in seen:
        problems.append(f"acceptance: {ident} is there twice")
    seen.add(ident)
    if not str(check.get("claim") or "").strip():
        problems.append(f"acceptance: {ident or index + 1} has no claim: quote the words of the request that it proves")
    run = str(check.get("run") or "").strip()
    if run == "manual":
        problems.append(f"acceptance: {ident or index + 1} is manual: an acceptance check is a command that exits 0")
    elif (problem := check_problem(run)) is not None:
        problems.append(f"acceptance: {ident or index + 1}: {problem}")
    return problems


def load(path: Path) -> tuple[list[dict], list[str]]:
    """(checks, problems) of one acceptance file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return [], [f"acceptance: no {path.parent.name}/{path.name}. P1 writes it: one check for each "
                    "outcome that the request asks for (commands/plan-create.md)"]
    except ValueError as exc:
        return [], [f"acceptance: {path.name} is not JSON: {exc}"]
    if not isinstance(data, dict) or data.get("RUN_BY_COMMAND") not in MARKERS:
        return [], [f"acceptance: {path.name} needs \"RUN_BY_COMMAND\": \"plan-create\""]
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        return [], [f"acceptance: {path.name} has no check"]
    seen: set[str] = set()
    problems = [p for i, c in enumerate(checks) for p in _check_problems(i, c, seen)]
    return checks, problems


def problems(context_dir: Path, session: dict | None = None, worktree: Path | None = None) -> list[str]:
    """P1 and P9: the acceptance file of the run is there, each check can prove something, and each script
    that a check names is committed (A2 needs a clean tree, and the PR holds the checks)."""
    path = acceptance_path(context_dir, session if session is not None else _session(context_dir, worktree))
    if path is None:
        return ["acceptance: the session has no task_id"]
    checks, found = load(path)
    if found or worktree is None:
        return found
    return _shared(checks, Path(worktree)) + _uncommitted(checks, Path(worktree))


def _shared(checks: list[dict], worktree: Path) -> list[str]:
    """A script that two checks name: a repin of one would change the other, so each check has its own."""
    owners: dict[str, list[str]] = {}
    for check in checks:
        for rel in _named_files([check], worktree):
            owners.setdefault(rel, []).append(str(check["id"]))
    return [f"acceptance: {' and '.join(ids)} name {rel}. Write one script per check "
            f"(for example acceptance/check_{ids[0].lower().replace('-', '')}.py), so a repin of one check "
            "cannot change another" for rel, ids in sorted(owners.items()) if len(ids) > 1]


def _git(worktree: Path, *args: str) -> int:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True, check=False).returncode


def _uncommitted(checks: list[dict], worktree: Path) -> list[str]:
    """Each named script that git does not hold at HEAD as it is now. A file that git ignores is local state."""
    dirty = [rel for rel in _named_files(checks, worktree)
             if _git(worktree, "check-ignore", "-q", rel) != 0
             and (_git(worktree, "ls-files", "--error-unmatch", rel) != 0 or _git(worktree, "diff", "--quiet", "HEAD", "--", rel) != 0)]
    if not dirty:
        return []
    files = " ".join(shlex.quote(rel) for rel in dirty)
    return [f"acceptance: {', '.join(dirty)} is not committed. The P1 peer runs `git add {files} && "
            'git commit -m "test: acceptance checks of the request"` (the orchestrator does not write code)']


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _named_files(checks: list[dict], worktree: Path) -> list[str]:
    """The files in the worktree that a check command names. P9 pins them with the checks."""
    names: set[str] = set()
    for check in checks:
        try:
            words = shlex.split(str(check.get("run") or ""))
        except ValueError:
            words = str(check.get("run") or "").split()
        for word in words:
            candidate = (worktree / word)
            if not word.startswith("-") and candidate.is_file() and worktree.resolve() in candidate.resolve().parents:
                names.add(word)
    return sorted(names)


def _pin_record(path: Path, checks: list[dict], worktree: Path) -> dict:
    return {"file": _sha(path), "scripts": {rel: _sha(worktree / rel) for rel in _named_files(checks, worktree)},
            "claims": {str(c["id"]): c["claim"] for c in checks},
            "runs": {str(c["id"]): str(c["run"]) for c in checks},
            "named": {str(c["id"]): _named_files([c], worktree) for c in checks}}


def pin(context_dir: Path, worktree: Path, session: dict | None = None) -> list[str]:
    """P9: write the sha256 of the acceptance file and of each file that a check names."""
    session = session if session is not None else _session(context_dir, worktree)
    path, pinned = acceptance_path(context_dir, session), pin_path(context_dir, session)
    if path is None or pinned is None:
        return ["acceptance: the session has no task_id or pipeline_run_id"]
    checks, found = load(path)
    if found:
        return found
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_text(json.dumps(_pin_record(path, checks, worktree), indent=2) + "\n", encoding="utf-8")
    return []


TEXT_LIMIT = 20_000  # characters of one check script in the log that R1 reads


def _check_text(check: dict, worktree: Path) -> dict[str, str]:
    """The command of a check and the text of each script that it names: what R1 compares across a repin."""
    text = {"run": str(check["run"])}
    for rel in _named_files([check], worktree):
        text[rel] = (worktree / rel).read_text(encoding="utf-8", errors="replace")[:TEXT_LIMIT]
    return text


def defect(context_dir: Path, worktree: Path, ident: str, repro: str, why: str,
           session: dict | None = None) -> list[str]:
    """AC-n is wrong: no code can pass it, or code that breaks its claim passes it (E1, R1 or R5). `repro` is a
    command that shows it; `why` says what is wrong."""
    from findings_io import check_problem

    session = session if session is not None else _session(context_dir, worktree)
    path = acceptance_path(context_dir, session)
    if path is None:
        return ["acceptance: the session has no task_id"]
    checks, found = load(path)
    if found:
        return found
    check = next((c for c in checks if str(c["id"]) == ident), None)
    if check is None:
        return [f"acceptance: {ident} is not a check of {path.name}"]
    if (problem := check_problem(repro)) is not None:
        return [f"acceptance: the repro of {ident}: {problem}. Write it as a script, for example "
                f"`python3 acceptance/repro_{ident}.py`"]
    if not why.strip():
        return [f"acceptance: the defect of {ident} needs a reason: what in the check is wrong: every code fails it, or code that breaks the claim passes it"]
    _append_repair(context_dir, session, {"kind": "defect", "id": ident, "repro": repro.strip(), "why": why.strip(),
                                          "text": _check_text(check, worktree), "run": session.get("pipeline_run_id")})
    return []


def _repin_problems(ident: str, checks: list[dict], pinned: dict, worktree: Path) -> list[str]:
    """Only AC-n may change: the same ids and claims, the same commands and scripts for every other check."""
    claims = pinned.get("claims") or {}
    now = {str(c["id"]): c for c in checks}
    if not claims:
        return ["acceptance: the P9 pin has no claims to compare. Run P9 again, then repin"]
    if set(now) != set(claims):
        return [f"acceptance: the checks are {sorted(now)}, P9 pinned {sorted(claims)}. A repin changes one check"]
    errors = [f"acceptance: the claim of {cid} changed. A repin keeps the claim of the request"
              for cid in sorted(now) if now[cid]["claim"] != claims[cid]]
    others = {cid: c for cid, c in now.items() if cid != ident}
    errors += [f"acceptance: the command of {cid} changed. A repin of {ident} changes {ident} only"
               for cid, c in sorted(others.items()) if str(c["run"]) != (pinned.get("runs") or {}).get(cid)]
    kept = {rel for cid, rels in (pinned.get("named") or {}).items() if cid != ident for rel in rels}
    kept |= set(_named_files(list(others.values()), worktree))
    scripts = pinned.get("scripts") or {}
    errors += [f"acceptance: {rel} changed, and another check names it. A repin of {ident} changes {ident} only"
               for rel in sorted(kept) if rel in scripts and (not (worktree / rel).is_file() or _sha(worktree / rel) != scripts[rel])]
    return errors


def repin(context_dir: Path, worktree: Path, ident: str, session: dict | None = None) -> list[str]:
    """The plan peer fixed AC-n: pin it again, close its defect, and log the old and the new sha256 for R1."""
    session = session if session is not None else _session(context_dir, worktree)
    path, pinned_path = acceptance_path(context_dir, session), pin_path(context_dir, session)
    if path is None or pinned_path is None:
        return ["acceptance: the session has no task_id or pipeline_run_id"]
    row = open_defects(context_dir, session).get(ident)
    if row is None:
        return [f"acceptance: {ident} has no open defect. E1 records one first: {_CLI} defect --id {ident} "
                '--repro "<command>" --why "<reason>"']
    checks, found = load(path)
    if found:
        return found
    try:
        pinned = json.loads(pinned_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"acceptance: no pin of the checks for this run ({pinned_path.name}). P9 writes it"]
    errors = _repin_problems(ident, checks, pinned, worktree)
    if errors:
        return errors
    record = _pin_record(path, checks, worktree)
    mine = set((pinned.get("named") or {}).get(ident, [])) | set(record["named"].get(ident, []))
    _append_repair(context_dir, session, {
        "kind": "repin", "id": ident, "why": row.get("why"), "repro": row.get("repro"),
        "old": {"file": pinned.get("file"), **{r: (pinned.get("scripts") or {}).get(r) for r in sorted(mine)}},
        "new": {"file": record["file"], **{r: record["scripts"].get(r) for r in sorted(mine)}},
        "old_text": row.get("text"), "new_text": _check_text(next(c for c in checks if str(c["id"]) == ident), worktree),
        "run": session.get("pipeline_run_id")})
    pinned_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return []


def _changed(path: Path, pinned: dict, worktree: Path) -> list[str]:
    errors = []
    if _sha(path) != pinned.get("file"):
        errors.append(f"acceptance: {path.name} changed after P9. A change to the checks is a new plan review")
    for rel, sha in sorted((pinned.get("scripts") or {}).items()):
        target = worktree / rel
        if not target.is_file() or _sha(target) != sha:
            errors.append(f"acceptance: {rel} changed after P9. A change to a check script is a new plan review")
    return errors


def _run(check: dict, worktree: Path, defects: dict[str, dict]) -> str | None:
    ident = str(check["id"])
    if ident in defects:  # the check, not the code, is wrong: the plan peer fixes it
        return (f"acceptance: {ident} has an open defect ({defects[ident].get('why')}). Do not change the code for it. "
                f"Send /plan-update <plan> --acceptance {ident}: the plan peer fixes the check, keeps its claim, and "
                f"runs `{_CLI} repin --id {ident}`")
    try:
        done = subprocess.run(str(check["run"]), shell=True, cwd=worktree, capture_output=True, text=True,  # noqa: S602 - the pinned check
                              timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return f"acceptance: {ident} timed out after {TIMEOUT_SECONDS} s ({check['claim']})"
    if done.returncode == 0:
        return None
    tail = " | ".join((done.stdout + done.stderr).strip().splitlines()[-TAIL_LINES:]) or "no output"
    return (f"acceptance: {ident} failed (exit {done.returncode}). Claim: {check['claim']}. Output: {tail}. If no code "
            f"can pass the check as it is, write a script that shows it and record the defect: {_CLI} defect "
            f'--id {ident} --repro "python3 acceptance/repro_{ident}.py" --why "<reason>"')


def verify(context_dir: Path, worktree: Path, session: dict | None = None) -> list[str]:
    """R5: the checks are the ones P9 pinned, and each one exits 0 at HEAD.

    With no Cadence run in the worktree there is no request and no check: R5 then judges the ledger only.
    A run with no pin fails (P9 writes the pin)."""
    session = session if session is not None else _session(context_dir, worktree)
    if not session.get("pipeline_run_id"):
        return []
    path, pinned_path = acceptance_path(context_dir, session), pin_path(context_dir, session)
    if path is None or pinned_path is None:
        return ["acceptance: the session has no task_id"]
    try:
        pinned = json.loads(pinned_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"acceptance: no pin of the checks for this run ({pinned_path.name}). P9 writes it"]
    checks, found = load(path)
    if found:
        return found
    defects = open_defects(context_dir, session)
    errors = _changed(path, pinned if isinstance(pinned, dict) else {}, worktree)
    if errors and defects:
        errors.append(f"acceptance: {', '.join(sorted(defects))} has an open defect. After the fix of the check, "
                      f"run `{_CLI} repin --id <id>`")
    return errors or [e for e in (_run(c, worktree, defects) for c in checks) if e]


def main(argv: list[str] | None = None) -> int:
    from gate_common import ctx, fail, ok

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "pin", "run", "defect", "repin"))
    parser.add_argument("--id", default="")
    parser.add_argument("--repro", default="")
    parser.add_argument("--why", default="")
    args = parser.parse_args(argv)
    context_dir, worktree = ctx(), Path.cwd()
    if args.command in ("defect", "repin") and not args.id:
        parser.error(f"{args.command} needs --id AC-<n>")
    if args.command == "check":
        errors = problems(context_dir, worktree=worktree)
    elif args.command == "pin":
        errors = pin(context_dir, worktree)
    elif args.command == "defect":
        errors = defect(context_dir, worktree, args.id, args.repro, args.why)
    elif args.command == "repin":
        errors = repin(context_dir, worktree, args.id)
    else:
        errors = verify(context_dir, worktree)
    return fail("acceptance", errors) if errors else ok("acceptance")


if __name__ == "__main__":
    raise SystemExit(main())
