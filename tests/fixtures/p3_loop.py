"""gh-104: one builder for the P3 re-review tests (plan, plan reviews, mtimes, begins).

After /plan-update, a blocking plan review that is older than the plan must open P3 again,
and a blocking review that is newer than the plan must open P4. Each helper returns the path
that it writes. Each test sets each mtime with ``at``; no test uses sleep.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "scripts", ROOT / "tests" / "fixtures"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from adversarial_loop import Loop  # noqa: E402
from conftest import HOST_ENV_PREFIXES, git_repo  # noqa: E402
from prompt_shape import write_delegation_record  # noqa: E402

RWC, RTE, NR = "READY WITH CHANGES", "READY TO EXECUTE", "NOT READY"


def p3_repo(path: Path, monkeypatch, *, home: Path | None = None) -> Path:
    """A git repo at `path` as the cwd, with no host env; `home` (when given) is CADENCE_HOME."""
    for name in list(os.environ):
        if name.startswith(HOST_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    if home is not None:
        monkeypatch.setenv("CADENCE_HOME", str(home))
    repo = git_repo(path)
    monkeypatch.chdir(repo)
    return repo


def at(path: Path, t: float) -> Path:
    os.utime(path, (t, t))
    return path


def p3_loop(repo: Path, *, cycles: int, task_id: str | None = "gh-1", **session) -> Loop:
    """A run on the `other` host with `cycles` plan-review begins and a lean P3 record."""
    lp = Loop(repo, host="other", task_id=task_id, **session)
    if cycles:
        lp.history(*([None] * cycles), stage="plan-review")
    # The P3 gate also reads the lean delegation record; without it the gate fails for another reason.
    record = write_delegation_record(lp.ctx, run_id=lp.run, stage="plan-review",
                                     command=f"/plan-review {lp.plan_path}", source="test")
    # The review peer returned: a running record would let the stop guard wait for it.
    record.write_text(record.read_text(encoding="utf-8").replace('"running"', '"complete"'), encoding="utf-8")
    return lp


LEDGER_HEAD = "\n## Revision ledger (review {sha})\n\n| Id | Change | Where | Verification |\n|---|---|---|---|\n"


def review_sha(review: Path) -> str:
    """The first 12 hex characters of the review file's sha256: the name of the ledger that answers it."""
    import hashlib

    return hashlib.sha256(review.read_bytes()).hexdigest()[:12]


def ledger(rows: dict[str, str], sha: str = "0" * 12) -> str:
    """ADR-0011: the `## Revision ledger (review <sha>)` table that ends an update section, one row per MF-n."""
    return LEDGER_HEAD.format(sha=sha) + "".join(f"| {i} | {change} | Phase 1 | test_x |\n" for i, change in rows.items())


def plan(lp: Loop, t: float, *, updated: bool = False, carried: list[str] | None = None,
         rows: dict[str, str] | None = None, review: Path | None = None) -> Path:
    extra = ""
    if updated:
        extra += f"\n## Plan update\n\nRUN_BY_COMMAND: plan-update\nPipeline-Run: {lp.run}\n"
    if rows is not None:
        extra += ledger(rows, review_sha(review) if review is not None else "0" * 12)
    if carried is not None:
        extra += "\n## Carried review findings\n\n" + "".join(f"- {i} -> Phase 1\n" for i in carried)
    return at(lp.write_plan(extra), t)


def p3_review(lp: Loop, t: float, verdict: str | None, *, n: int = 1, mf: tuple[str, ...] = (),
              run: str | None = None) -> Path:
    name = f"plan_{lp.slug}.md" if n == 1 else f"plan_{lp.slug}_r{n}.md"
    path = lp.ctx / "reviews" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["RUN_BY_COMMAND: plan-review", f"Pipeline-Run: {run or lp.run}", "**Ticket:** gh-1", ""]
    lines += [f"- **{i}.** open item" for i in mf]
    if verdict is not None:
        lines += ["", f"Verdict: {verdict}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return at(path, t)


# --- The scenarios of the plan table (gh-104 S1b, S2, S3, S4, S5). Each returns the Loop. ---


def s1b(repo: Path) -> Loop:
    """Cycle 2: review 2 is blocking and newer than the plan, which has the old plan-update marker."""
    lp = p3_loop(repo, cycles=2)
    p3_review(lp, 50, RWC)
    p3_review(lp, 70, RWC, n=2)
    plan(lp, 60, updated=True)
    return lp


def s2(repo: Path, verdict: str = RWC) -> Loop:
    """After P4: the plan (with the marker) is newer than blocking review 1."""
    lp = p3_loop(repo, cycles=1)
    p3_review(lp, 50, verdict)
    plan(lp, 60, updated=True)
    return lp


def s3(repo: Path, **session) -> Loop:
    """After S2: review 2 READY TO EXECUTE is the newest."""
    lp = p3_loop(repo, cycles=2, **session)
    p3_review(lp, 50, RWC)
    p3_review(lp, 70, RTE, n=2)
    plan(lp, 60, updated=True)
    return lp


def at_limit(repo: Path, last_verdict: str, plan_t: float, carried: list[str] | None = None,
             rows: dict[str, str] | None = None) -> Loop:
    """Cycle 5 of 5: reviews 1 to 4 at 10 to 40, review 5 (with MF-1) at 70, the plan at `plan_t`."""
    lp = p3_loop(repo, cycles=5)
    for n in range(1, 5):
        p3_review(lp, 10 * n, RWC, n=n)
    p3_review(lp, 70, last_verdict, n=5, mf=("MF-1",))
    plan(lp, plan_t, updated=True, carried=carried, rows=rows)
    return lp
