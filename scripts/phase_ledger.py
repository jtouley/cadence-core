#!/usr/bin/env python3
"""E1 phase ledger: the plan's phases are the task list.

A phase is done when a commit
since the base names it: the subject starts with "phase N", after an optional
conventional prefix (`feat(x): phase 2 ...`). The E1 gate and the stop
guard read `assess()`, so the agent is told the next concrete phase, and E1
does not pass while a phase is open. The stop guard holds an open E1 with
`next_phase()`. A `phase N: red ...` commit holds the failing test of the
phase and does not finish it. The red-before-green evidence of each phase is in
scripts/phase_evidence.py.

    python3 phase_ledger.py --json     # state for the active run
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from artifact_requirements import normalize_text_markers, run_scope_accept  # noqa: E402
from pipeline_run_binding import worktree_run_session  # noqa: E402

PHASE_HEADER = re.compile(r"^#{2,4}\s+Phase\s+(\d+)\b[ \t]*[:.\-–—]?[ \t]*(.*?)[ \t]*$", re.M)
# `phase 2 ...`, `feat(x): phase 2 ...`, `phase 2: ...`. Not `docs: a note on phase 2`.
PHASE_COMMIT = re.compile(r"^(?:\[[^\]]*\]\s*)?(?:(?:fixup|squash)!\s*)?(?:\w+(?:\([^)]*\))?!?:\s*)?phase[\s_-]*(\d+)\b",
                          re.I)
PLAN_MARKERS = ("RUN_BY_COMMAND: plan-create", "RUN_BY_SKILL: plan-create")
BASE_CHAIN = ("origin/HEAD", "origin/main", "origin/master", "main", "master")


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True, check=False)


def parse_phases(plan_text: str) -> list[tuple[int, str]]:
    seen: dict[int, str] = {}
    for match in PHASE_HEADER.finditer(plan_text):
        seen.setdefault(int(match.group(1)), match.group(2).strip())
    return sorted(seen.items())


_GLOBAL = ("global", "all", "plan", "whole plan", "overall", "general")
_PHASE_WORDS = re.compile(r"(?:phases?|p|and|to|through|[\s,&/+\-–—])*")


def phase_keys(raw: str) -> list[str]:
    """The sections that a finding names: `["global"]`, or each `Phase N`. A reviewer writes them in its own
    words (as plans write them: `Phase 2`, `phase 2`, `P2`, `Phases 1 to 4`, `Phase 1 and Phase 3`); [] when the text
    names no section, such as a location (`Assumptions row G-1`)."""
    text = raw.strip().lower()
    if text in _GLOBAL:
        return ["global"]
    if not re.search(r"\d", text) or not _PHASE_WORDS.fullmatch(re.sub(r"\d+", "", text)):
        return []
    numbers: set[int] = {int(n) for n in re.findall(r"\d+", text)}
    for low, high in re.findall(r"(\d+)\s*(?:-|–|—|to|through)\s*(?:phase\s*|p)?(\d+)", text):
        numbers |= set(range(int(low), int(high) + 1))
    return [f"Phase {n}" for n in sorted(numbers) if n > 0]


def phase_key(raw: str) -> str | None:
    """The one section that ``raw`` names, else None."""
    keys = phase_keys(raw)
    return keys[0] if len(keys) == 1 else None


# The sections that P4 writes for its answer, not for the build: P4 may always change them.
RECORD_HEADER = re.compile(r"^#{2,3}\s+(?:Revision ledger|Carried |Deferral conditions|Plan update)", re.I)


PROVENANCE = re.compile(r"^\s*[*_`]*RUN_BY_(?:COMMAND|SKILL|SCRIPT):")


def phase_sections(plan_text: str) -> dict[str, str]:
    """The plan by section: each `Phase N` from its header to the next heading of its level or higher,
    `record` (the ledger and carried sections), and `global`, the rest. A plan finding names `global` or
    `Phase N`, and P4 may change only the named ones and `record`. A provenance line (`RUN_BY_COMMAND:
    plan-update`) is in no section: P4 writes its marker, and that is not a change of the plan."""
    sections: dict[str, list[str]] = {"global": []}
    key, level = "global", 0
    for line in plan_text.splitlines(keepends=True):
        if PROVENANCE.match(line):
            continue
        head = re.match(r"(#{1,6})\s", line)
        phase = PHASE_HEADER.match(line.rstrip("\n"))
        if phase:
            key, level = f"Phase {int(phase.group(1))}", len(head.group(1)) if head else 3
        elif head and RECORD_HEADER.match(line):
            key, level = "record", len(head.group(1))
        elif head and key != "global" and len(head.group(1)) <= level:
            key = "global"
        sections.setdefault(key, []).append(line)
    return {k: "".join(v) for k, v in sections.items()}


def resolve_base(worktree: Path, session: dict) -> str | None:
    """The session's base, else the first ref in BASE_CHAIN that resolves.

    The branch the run is on is never its own base (`main..HEAD` on `main` is
    always empty). A local-only repo falls back to its root commit, so the work
    on the branch still counts.
    """
    current = _git(worktree, "branch", "--show-current").stdout.strip()
    for ref in ([session["base"]] if session.get("base") else []) + list(BASE_CHAIN):
        if current and ref == current:
            continue
        if _git(worktree, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0:
            return ref
    roots = _git(worktree, "rev-list", "--max-parents=0", "HEAD").stdout.split()
    return roots[0] if len(roots) == 1 else None


def select_plan(context_dir: Path, worktree: Path, session: dict, *, bold: bool = False) -> Path | None:
    """Newest plan-create plan in this run's ticket scope that does not name another run.

    `bold`: read a `**RUN_BY_SKILL:**` marker too, as the P1 gate does (the handoff).
    """
    candidates = list(Path(context_dir).glob("plans/*.plan.md"))
    candidates += list((Path(worktree) / ".cursor" / "plans").glob("*.plan.md"))
    accept = run_scope_accept(Path(context_dir), Path(worktree))
    rows: list[tuple[float, Path]] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
            text = normalize_text_markers(text) if bold else text
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if not any(m in text for m in PLAN_MARKERS):
            continue
        if not accept(path):
            continue
        rows.append((mtime, path))
    return max(rows)[1] if rows else None


def commit_log(worktree: Path, base: str) -> list[tuple[int, str]]:
    """(author time, subject) since the base, newest first. --topo-order: a revert never comes
    before its commit."""
    proc = _git(worktree, "log", "--topo-order", f"{base}..HEAD", "--format=%at %s")
    if proc.returncode != 0:
        return []
    return [(int(when), subject) for when, _, subject in (line.partition(" ") for line in proc.stdout.splitlines())]


_PEEL = re.compile(r'^(Revert|Reapply) "(.*)"$', re.S)
_CONVENTIONAL_REVERT = re.compile(r"^revert(\([^)]*\))?!?:", re.I)


def _inner(subject: str) -> str:
    """The subject inside its Revert and Reapply wrappers."""
    text = subject.strip()
    while match := _PEEL.match(text):
        text = match.group(2)
    return text


def phase_of(subject: str) -> int | None:
    """The phase number a commit subject names, else None."""
    match = PHASE_COMMIT.match(_inner(subject))
    return int(match.group(1)) if match else None


def revert_depth(subject: str) -> int:
    """How many reverts wrap the subject. `Reapply "x"` is git's name for a revert of a revert (2)."""
    depth = 0
    text = subject.strip()
    while match := _PEEL.match(text):
        depth += 1 if match.group(1) == "Revert" else 2
        text = match.group(2)
    if _CONVENTIONAL_REVERT.match(text):  # `revert: phase 2 ...` (conventional commits)
        depth += 1
    return depth


def done_phases(subjects: list[str]) -> set[int]:
    """The phase numbers that the subjects (newest first) complete."""
    from phase_evidence import is_red

    done: set[int] = set()
    for subject in reversed(subjects):  # oldest first, so a later revert undoes the phase
        number = phase_of(subject) if not is_red(_inner(subject)) else None
        numbers = {number} if number is not None else set()
        if revert_depth(subject) % 2:
            done -= numbers
        else:
            done |= numbers
    return done


def next_phase(plan: Path, worktree: Path, session: dict | None = None) -> tuple[int, str] | None:
    """(number, label) of the first plan phase that no commit since the base names, else None.

    None too for a plan with no phase headings or a worktree with no base.
    """
    phases = parse_phases(Path(plan).read_text(encoding="utf-8"))
    base = resolve_base(Path(worktree), session or {})
    if not phases or base is None:
        return None
    done = done_phases([subject for _, subject in commit_log(Path(worktree), base)])
    return next(((n, title) for n, title in phases if n not in done), None)


def assess(context_dir: Path, worktree: Path) -> dict:
    """Where E1 stands: phases, completed, next, commits since base, phase commits with one time."""
    session = worktree_run_session(Path(context_dir), Path(worktree)) or {}
    base = resolve_base(worktree, session)
    plan = select_plan(context_dir, worktree, session)
    phases = parse_phases(plan.read_text(encoding="utf-8")) if plan else []
    log = commit_log(worktree, base) if base else []
    done = done_phases([subject for _, subject in log])
    return {
        "plan": str(plan) if plan else None,
        "base": base,
        "phases": phases,
        "completed": [n for n, _ in phases if n in done],
        "next": next(((n, title) for n, title in phases if n not in done), None),
        "commits": len(log),
    }


def open_work(state: dict) -> str | None:
    """The next concrete E1 task, or None when E1 is done."""
    if state["base"] is None:
        return "no base ref resolves (tried session base, " + ", ".join(BASE_CHAIN) + ")"
    if state["plan"] and not state["phases"]:
        return (
            f"the plan {Path(state['plan']).name} has no `### Phase N:` headers, so E1 has no task list. "
            "Run /plan-update to add them"
        )
    if state["phases"]:
        if state["next"] is None:
            return None  # every phase has a green commit; implement_gate.py then reads the evidence
        n, title = state["next"]
        total = len(state["phases"])
        return (
            f"phase {n} of {total} ({title}) is not done. "
            f"Implement it with TDD and commit it with a subject that starts with 'phase {n}:' "
            f"(or a conventional prefix, then 'phase {n}')"
        )
    if state["commits"] == 0:
        return f"no commits since {state['base']}. Implement the plan and commit"
    return None


def main() -> int:
    from context_root import context_dir as canonical_context_dir

    worktree = Path.cwd()
    state = assess(canonical_context_dir(worktree), worktree)
    print(json.dumps({**state, "open": open_work(state)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_SCRIPTS.parent / "shared" / "scripts"))
    raise SystemExit(main())
