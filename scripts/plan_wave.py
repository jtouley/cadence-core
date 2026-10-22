#!/usr/bin/env python3
"""The plan review round: the plan reviewers read one plan sha in parallel, and P4 answers all of them once.

The members are the rows of stages.json whose `review_round.round` is `plan`: a row with a slash
command is a peer that the orchestrator sends, and any other row is a script that the round runs. A pack row
that names the round joins it. Each LLM reviewer writes its findings to `.context/plan-findings/<stage>.jsonl`, one row for each
finding with the section it targets (`global` or `Phase N`), and then records that it is done. When
every reviewer that is due is done, this script runs the script members, joins the must-fix rows by section, and
writes `.context/plan-findings/round-<n>.md`, the one input of `/plan-update`. P4 may change only the
sections that the round names. In the next round only a reviewer that had a must-fix, or a finding
in a section that P4 changed, reviews again; the pass of every other reviewer stands.

    python3 scripts/plan_wave.py                 # open or close a round, print NEXT
    python3 scripts/plan_wave.py --check         # fail while a round is open (no write)
    python3 scripts/plan_wave.py add --stage plan-review --json '{"id": "MF-1", "phase": "Phase 2", ...}'
    python3 scripts/plan_wave.py done --stage plan-review
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from phase_ledger import phase_keys, phase_sections  # noqa: E402

ROUND = "plan"  # the name of this round in stages.json review_rounds
SEVERITIES = ("must-fix", "nit")
def _severity(raw: object) -> str | None:
    """`must-fix` or `nit`; a reviewer's own word (`blocking`, `should-fix`) is mapped by Jev (verdict_judge)."""
    from verdict_judge import judge

    word = str(raw or "").strip()
    return word.lower() if word.lower() in SEVERITIES else judge(word, "severity")
FINDING_ID = re.compile(r"^[A-Za-z][\w-]*\d$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Round:
    """The rounds of the run's plan review: `.context/plan-findings/` and the run's plan."""

    def __init__(self, context_dir: Path, session: dict | None = None):
        from artifact_requirements import _run_plan
        from pipeline_run_binding import current_session

        self.ctx = Path(context_dir)
        self.session = session if session is not None else current_session(self.ctx)
        self.dir = self.ctx / "plan-findings"
        self.workspace = Path(self.session.get("workspace") or Path.cwd())
        self.plan = _run_plan(self.ctx, self.session)
        self.text = self.plan.read_text(encoding="utf-8") if self.plan else ""
        self.sha = _sha(self.text) if self.plan else ""
        self.sections = {k: _sha(v) for k, v in phase_sections(self.text).items()}

    @contextmanager
    def locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def rounds(self) -> list[dict]:
        try:
            data = json.loads((self.dir / "rounds.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        run = str(self.session.get("pipeline_run_id") or "")
        return data.get("rounds", []) if isinstance(data, dict) and data.get("pipeline_run_id") == run else []

    def save(self, rounds: list[dict]) -> None:
        if os.environ.get("CADENCE_GATES_READONLY") == "1":  # the stop guard writes nothing
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        body = {"pipeline_run_id": self.session.get("pipeline_run_id"), "rounds": rounds}
        (self.dir / "rounds.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    def rows(self, stage: str, number: int) -> list[dict]:
        path = self.dir / f"{stage}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        rows = [json.loads(line) for line in lines if line.strip()]
        return [r for r in rows if r.get("round") == number]

    def members(self) -> list[dict]:
        """The enabled rows of the round, in registry order (load_stages.round_members)."""
        from load_stages import round_members

        return round_members(ROUND, _SCRIPTS.parent, self.workspace)

    def member(self, stage: str) -> dict | None:
        return next((row for row in self.members() if row["slug"] == stage), None)

    def enabled(self) -> list[str]:
        """The peer members: the reviewers that the orchestrator sends."""
        from load_stages import is_peer

        return [row["slug"] for row in self.members() if is_peer(row)]

    def shown_plan(self) -> str:
        from activate_session import _shown

        return _shown(self.plan, self.ctx, self.workspace) if self.plan else ""

    def round_file(self, number: int) -> Path:
        return self.dir / f"round-{number}.md"

    def round_plan(self, number: int) -> Path:
        """The plan that round `number` reviewed: the scope check shows P4 what it changed outside the round."""
        return self.dir / f"round-{number}.plan.md"


def _limit_of(stage: str) -> int | None:
    """The cycle limit of the stage's `review_round`, else of its round in `review_rounds`, else None."""
    import load_stages
    from artifact_requirements import cycle_limit

    registry = load_stages.load_registry(_SCRIPTS.parent)
    row: dict = next((r for r in registry["stages"] if r.get("slug") == stage), {})
    member = row.get("review_round") or {}
    spec = member.get("cycle_limit", (registry.get("review_rounds") or {}).get(member.get("round"), {}).get("cycle_limit"))
    return None if spec is None else cycle_limit(spec)


def _at_limit(rounds: list[dict], stage: str) -> bool:
    """A reviewer at its cycle limit does not review again: P4 carries its open items. The rounds count it, so
    the limit needs no attest record (references/error-recovery.md)."""
    limit = _limit_of(stage)
    return limit is not None and sum(stage in r.get("done", {}) for r in rounds) >= limit


def peers(rnd: Round) -> dict[str, str]:
    """{stage: the command that the orchestrator sends} for each peer member, from its row and its inputs."""
    import handoff_inputs
    from load_stages import is_peer

    run = handoff_inputs.Run(root=_SCRIPTS.parent, ctx=rnd.ctx, workspace=rnd.workspace, session=rnd.session)
    return {row["slug"]: handoff_inputs.resolve(row, run).command for row in rnd.members() if is_peer(row)}


def must_fix(rnd: Round, current: dict) -> list[dict]:
    rows = [r for stage in current["due"] for r in rnd.rows(stage, current["n"]) if r.get("severity") == "must-fix"]
    return rows + current.get("p5", [])


def changed_sections(rnd: Round, before: dict) -> set[str]:
    old = before.get("sections", {})
    return {k for k in set(old) | set(rnd.sections) if old.get(k) != rnd.sections.get(k)}


def p4_problems(rnd: Round, before: dict) -> list[str]:
    """P4 changed only the sections that the round names, and its ledger answers each id."""
    from artifact_requirements import revision_ledger_ids

    rows = must_fix(rnd, before)
    named = {r["phase"] for r in rows}
    outside = sorted(changed_sections(rnd, before) - named - {"record"})
    problems = [f"plan-review-round: /plan-update changed {', '.join(outside)}, which no finding of round "
                f"{before['n']} names. Send /plan-update again: undo that change, and change only "
                f"{', '.join(sorted(named))}{_shown_change(rnd, before, outside)}"] if outside else []
    path = rnd.round_file(before["n"])
    sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    missing = sorted({r["id"] for r in rows} - (revision_ledger_ids(rnd.text, sha) or set()))
    if missing:
        problems.append(f"plan-review-round: the plan has no `## Revision ledger (review {sha[:12]})` row for "
                        f"{', '.join(missing)} of {path.name}. Send /plan-update again")
    return problems + _carry_problems(rnd)


def _carry_problems(rnd: Round) -> list[str]:
    """The P6 carry that P9 checks, in the round: at its limit, P4 writes `## Carried adversarial findings (<report> @
    <sha12>)`. y3, s2 and s3 each passed the round without it and failed P9, where the repair P4 had no round."""
    from artifact_requirements import check_adversarial_verdict

    return [f"plan-review-round: {problem.removeprefix('adversarial-review: ')} Send /plan-update again with the round file"
            for problem in check_adversarial_verdict(rnd.ctx, rnd.session, stage="p9")
            if "cycle limit reached" in problem or "not carried" in problem or "not in the plan under" in problem]


def _shown_change(rnd: Round, before: dict, outside: list[str]) -> str:
    """The lines that P4 changed outside the round, from the plan that the round reviewed."""
    path = rnd.round_plan(before["n"])
    if not path.is_file():
        return ""
    old, new = phase_sections(path.read_text(encoding="utf-8")), phase_sections(rnd.text)
    lines = [f"{line[0]} {line[1:].strip()}" for key in outside
             for line in difflib.unified_diff(old.get(key, "").splitlines(), new.get(key, "").splitlines(), lineterm="", n=0)
             if line[:1] in "+-" and not line.startswith(("+++", "---")) and line[1:].strip()]
    return f". The change: {' | '.join(lines[:8])}" if lines else ""


def plan_update_problems(context_dir: Path, session: dict | None = None) -> list[str]:
    """The P4 gate in a round: the plan answered the round it changed after."""
    rnd = Round(context_dir, session)
    rounds = rnd.rounds()
    if rnd.plan is None or not rounds or not must_fix(rnd, rounds[-1]) or rounds[-1]["plan_sha256"] == rnd.sha:
        return []
    return p4_problems(rnd, rounds[-1])


def _started(rnd: Round, current: dict) -> bool:
    """A reviewer recorded in it, or it ran its scripts (a round with no reviewer due runs its scripts
    at once, and P4 answers its round file; it is not opened again on the updated plan)."""
    return (bool(current["done"]) or bool(current.get("p5")) or rnd.round_file(current["n"]).is_file()
            or any(rnd.rows(stage, current["n"]) for stage in current["due"]))


def _next_round(rnd: Round, rounds: list[dict]) -> dict:
    """The round for the plan as it is now. A reviewer is due when it had a must-fix in the round before, or did
    not finish it; the pass of any other reviewer stands, so a reviewer that passed cannot find a new issue."""
    if not rounds:
        due = rnd.enabled()
    else:
        last = rounds[-1]
        due = [s for s in rnd.enabled() if any(r.get("severity") == "must-fix" for r in rnd.rows(s, last["n"]))
               or (s in last["due"] and s not in last["done"])]
    due = [s for s in due if not _at_limit(rounds, s)]
    return {"n": len(rounds) + 1, "plan_sha256": rnd.sha, "sections": rnd.sections, "due": due, "done": {}}


def _run_scripts(rnd: Round, check: bool) -> list[dict]:
    """Each script member on this plan: its open must-fix ledger rows (stage = its slug), as `global` rows."""
    from findings_io import is_open_must_fix, load_findings, rows_of_task
    from load_stages import is_peer, script_argv

    scripts = [row for row in rnd.members() if not is_peer(row)]
    if not check:
        for row in scripts:
            subprocess.run(script_argv(row["command"], _SCRIPTS.parent), capture_output=True, text=True,
                           cwd=rnd.workspace)
    slugs = {row["slug"] for row in scripts}
    rows = rows_of_task(load_findings(rnd.ctx / "findings.jsonl"), str(rnd.session.get("task_id") or ""))
    return [{"id": r["id"], "stage": r["stage"], "phase": "global", "location": r.get("location", ""),
             "severity": "must-fix", "evidence": r.get("evidence", "")}
            for r in rows if r.get("stage") in slugs and is_open_must_fix(r)]


# A plan line that may make the run wait on a person: code finds the candidates, Jev judges each one (runs
# stopped in E1 on "the owner accepts the record"). One Choice per line, with contrasting options:
# calibrated on 86 candidate lines of six real plans, each wait has a `waits` probability of 0.42 or more and
# no other line does. A Noul question could not tell a wait from a line that records an approval. `acceptance` is
# not a person word: in y1 to y3 it sent each line that names the acceptance script to Jev.
# references/plan-review-round.md "A run has no person to wait on".
_PERSON_WORDS = re.compile(r"(?i)\b(owner|user|you|human|person|stakeholder|reviewer|approv\w*|accept(?!ance)\w*|confirm\w*"
                           r"|decid\w*|decision|sign[- ]off)\b")
_WAIT_QUESTION = "What does this line of a software plan do?"
_WAIT_OPTIONS = {
    "waits": "The line makes the work stop until a person (the owner or the user) decides, accepts, approves or "
             "confirms something, or it asks the reader to confirm or to choose.",
    "records": "The line records something already decided, approved, deferred, rejected or assumed, or names where "
               "such a record is.",
    "work": "The line describes work, a test, a file, a check, a risk or a reason.",
}
WAIT_THRESHOLD = 0.4


def _person_waits(rnd: Round, rounds: list[dict]) -> list[dict]:
    """A must-fix row for each plan line that Jev reads as a wait on a person, once per line across the rounds."""
    from verdict_judge import enabled, option_probability

    if not enabled():
        return []
    raised = {r.get("line") for r in (row for past in rounds for row in past.get("p5", [])) if r.get("stage") == "person-wait"}
    rows: list[dict] = []
    for section, line in _wait_candidates(rnd.text, raised):
        p = option_probability(line, _WAIT_QUESTION, _WAIT_OPTIONS, "waits", kind="person-wait", threshold=WAIT_THRESHOLD)
        if p is not None and p >= WAIT_THRESHOLD:
            rows.append({"id": f"W-{len(rows) + 1}", "stage": "person-wait", "phase": section, "location": line[:120],
                         "severity": "must-fix", "line": line,
                         "evidence": f"this line waits on a person (Jev {p:.2f}). A run has no person in the loop: "
                                     "settle it with an assumption row (references/plan-review-round.md)"})
    return rows


def _wait_candidates(text: str, raised: set) -> list[tuple[str, str]]:
    """(section, line) for each plan line that names a person and is not a heading, a table head, a record
    line or a line already raised: code finds the candidates, and Jev judges them."""
    out = []
    for section, body in phase_sections(text).items():
        lines = body.splitlines()
        for i, raw in enumerate(lines):
            line = raw.strip()
            table_head = line.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\s*\|[\s:|-]+\|\s*", lines[i + 1])
            if section == "record" or len(line) <= 12 or line.startswith("#") or table_head or line in raised:
                continue
            if _PERSON_WORDS.search(line):
                out.append((section, line))
    return out


def write_round_file(rnd: Round, current: dict) -> Path:
    """The joined must-fix rows of a round, by section, with each section that two reviewers name."""
    rows = must_fix(rnd, current)
    by_section: dict[str, list[dict]] = {}
    for row in rows:
        by_section.setdefault(row["phase"], []).append(row)
    lines = [f"# Plan review round {current['n']}: {rnd.session.get('task_id')}", "",
             "RUN_BY_SCRIPT: plan_wave.py", f"Pipeline-Run: {rnd.session.get('pipeline_run_id')}",
             f"Plan sha256: {current['plan_sha256']}", f"Reviewers: {', '.join(current['due']) or 'none'}", "",
             "## Must-fix by section", ""]
    for section in sorted(by_section, key=lambda k: (k != "global", int(k.split()[-1]) if k != "global" else 0)):
        lines += [f"### {section}", "", "| Id | Reviewer | Location | Evidence |", "|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['stage']} | {r.get('location', '')} | {r.get('evidence', '')} |"
                  for r in by_section[section]] + [""]
    conflicts = {k: sorted({r["stage"] for r in v}) for k, v in by_section.items() if len({r["stage"] for r in v}) > 1}
    lines += ["## Sections that two reviewers name", ""]
    lines += [f"- {k}: {', '.join(v)}. Make one change that answers each of their rows." for k, v in conflicts.items()]
    lines += ["None." if not conflicts else "", "", "## Nits (not for this update)", ""]
    lines += [f"- {r['id']} ({r['stage']}, {r['phase']}): {r.get('evidence', '')}" for s in current["due"]
              for r in rnd.rows(s, current["n"]) if r.get("severity") == "nit"] or ["None."]
    lines += ["", f"Change only these sections: {', '.join(sorted(by_section))}. Answer each id with a row of "
              "`## Revision ledger (review <the first 12 characters of the sha256 of this file>)`."]
    path = rnd.round_file(current["n"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _no_plan(rnd: Round, rounds: list[dict]) -> list[str]:
    """Why the round cannot find the run's plan, else []."""
    if rnd.plan is not None:
        return []
    if rounds:
        return ["plan-review-round: the plan lost its header lines (`RUN_BY_COMMAND: plan-create`, `Ticket:`, "
                "`Pipeline-Run:`), so the run cannot find it. Send /plan-update again: restore them as P1 wrote them"]
    return ["plan-review-round: the run has no plan. Run P1 (/plan-create) first"]


def _writes(check: bool) -> bool:
    return not check and os.environ.get("CADENCE_GATES_READONLY") != "1"


def _open_round(rnd: Round, rounds: list[dict], *, check: bool) -> list[str]:
    """Make `rounds[-1]` the round of the plan as it is now: why P4 must answer the last round first, else []."""
    if rounds and rounds[-1]["plan_sha256"] != rnd.sha and not _started(rnd, rounds[-1]):
        rounds.pop()  # a round that no reviewer started reviews nothing; it opens again on this plan
    if rounds and rounds[-1]["plan_sha256"] != rnd.sha:
        answered = p4_problems(rnd, rounds[-1]) if must_fix(rnd, rounds[-1]) else []
        if answered:
            return answered
    if not rounds or rounds[-1]["plan_sha256"] != rnd.sha:
        rounds.append(_next_round(rnd, rounds))
        if _writes(check):
            rnd.save(rounds)
            rnd.round_plan(rounds[-1]["n"]).write_text(rnd.text, encoding="utf-8")
    return []


def state(rnd: Round, *, check: bool) -> tuple[list[str], str | None]:
    """(problems, the NEXT line). No problem and no NEXT: the plan review is done for this plan."""
    rounds = rnd.rounds()
    problems = _no_plan(rnd, rounds) or _open_round(rnd, rounds, check=check)
    if problems:
        return problems, None
    current = rounds[-1]
    pending = [s for s in current["due"] if s not in current["done"]]
    plan = rnd.shown_plan()
    if pending:
        commands = peers(rnd)
        sends = " | ".join(commands[s] for s in pending if s in commands)
        return [f"plan-review-round: round {current['n']} waits on {', '.join(pending)}"], \
            f"NEXT: send these peers in one message, in parallel: {sends}"
    if _writes(check):
        _complete(rnd, current)
        rnd.save(rounds)
    open_rows = must_fix(rnd, current)
    if not open_rows:
        return [], None
    path = rnd.round_file(current["n"])
    shown = f".context/plan-findings/{path.name}"
    return [f"plan-review-round: round {current['n']} has {len(open_rows)} must-fix rows"], \
        f"NEXT: send the peer `/plan-update {plan} --from-review {shown}`"


def _open_to(rnd: Round, rounds: list[dict], stage: str) -> set[str] | None:
    """The sections where a must-fix of `stage` may stand in this round; None in round one. Each round can only
    shrink the open set: a section that did not change and had no open item of the reviewer stays passed."""
    if len(rounds) < 2:
        return None
    before = rounds[-2]
    changed = {k for k in set(before["sections"]) | set(rnd.sections) if before["sections"].get(k) != rnd.sections.get(k)}
    still = {r["phase"] for r in rnd.rows(stage, before["n"]) if r.get("severity") == "must-fix"}
    return (changed | still) - {"record"}


def add(rnd: Round, stage: str, record: dict) -> list[dict]:
    """A finding of `stage` on the plan as it is now. The script pins the plan sha and the round."""
    rounds = rnd.rounds()
    if stage not in rnd.enabled():
        raise ValueError(f"stage must be one of {sorted(rnd.enabled())}: a peer of the plan round in stages.json")
    if not rounds or rounds[-1]["plan_sha256"] != rnd.sha or stage not in rounds[-1]["due"]:
        raise ValueError(f"no open round names {stage} for this plan: run plan_wave.py first")
    phases = [p for p in phase_keys(str(record.get("phase", ""))) if p != "record"]
    if not phases or any(p not in rnd.sections for p in phases):
        raise ValueError(f"phase {record.get('phase')!r} is not a section of the plan: use one of "
                         f"{', '.join(sorted(k for k in rnd.sections if k != 'record'))}")
    severity, ident = _severity(record.get("severity")), str(record.get("id", "")).strip().rstrip(".:)")
    if severity is None or not FINDING_ID.match(ident):
        raise ValueError(f"a finding needs an id such as MF-1 and a severity of {' or '.join(SEVERITIES)}")
    allowed = _open_to(rnd, rounds, stage)
    outside = [p for p in phases if allowed is not None and p not in allowed]
    if severity == "must-fix" and outside:
        raise ValueError(f"in round {rounds[-1]['n']} a must-fix may target only a section that the update changed, "
                         f"or one where {stage} still had a must-fix: {', '.join(sorted(allowed or ())) or 'none'}. "
                         f"Record a finding in {', '.join(outside)} as a nit")
    rows = [{"id": ident, "stage": stage, "round": rounds[-1]["n"], "plan_sha256": rnd.sha, "phase": phase,
             "location": str(record.get("location", "")), "severity": severity,
             "evidence": str(record.get("evidence", ""))} for phase in phases]  # one row per section it names
    with rnd.locked(), (rnd.dir / f"{stage}.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    return rows


def done(rnd: Round, stage: str) -> int:
    """`stage` reviewed this plan: its must-fix count for the round. P3 rows must match its review's MF-n items."""
    with rnd.locked():
        rounds = rnd.rounds()
        if not rounds or rounds[-1]["plan_sha256"] != rnd.sha or stage not in rounds[-1]["due"]:
            raise ValueError(f"no open round names {stage} for this plan: run plan_wave.py first")
        rows = [r for r in rnd.rows(stage, rounds[-1]["n"]) if r["severity"] == "must-fix"]
        if ((rnd.member(stage) or {}).get("review_round") or {}).get("report_ids"):
            _check_review(rnd, {r["id"] for r in rows})
        if stage == "test-benchmark-plan":
            _check_card(rnd)
        rounds[-1]["done"][stage] = len(rows)
        if all(s in rounds[-1]["done"] for s in rounds[-1]["due"]):
            _complete(rnd, rounds[-1])
        rnd.save(rounds)
    return len(rows)


def _complete(rnd: Round, current: dict) -> None:
    """Each due reviewer is done: run P5 on this plan and write the round file, once."""
    if "p5" not in current:  # the key keeps its name, so a round of an earlier version still reads
        current["p5"] = _run_scripts(rnd, check=False) + _person_waits(rnd, rnd.rounds()[:-1])
    if must_fix(rnd, current) and not rnd.round_file(current["n"]).is_file():
        write_round_file(rnd, current)


def _check_card(rnd: Round) -> None:
    """A P7 card that the P7 gate cannot read fails at the end of the round, when the peer is gone."""
    from artifact_requirements import run_scope_accept
    from test_benchmark_gate import card_shape_problems

    accept = run_scope_accept(rnd.ctx, session=rnd.session)
    cards = [p for p in rnd.ctx.glob("benchmarks/*_plan.json") if accept(p)]
    card = max(cards, key=lambda p: p.stat().st_mtime) if cards else None
    problems = card_shape_problems(card) if card else ["write the plan scorecard first: .context/benchmarks/<task>_plan.json"]
    if problems:
        raise ValueError("; ".join(problems))


def _unread_review(rnd: Round) -> str | None:
    """Why P3 does not count a review that names this plan: its verdict, or the run that it names.

    In runs, one review was dropped without a word; another message named the verdict when the cause was
    elsewhere. The message names the cause that the review shows."""
    from artifact_requirements import PLAN_VERDICTS, verdict_in_text

    for path in sorted(rnd.ctx.glob("reviews/plan_*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if rnd.sha[:12] not in text:
            continue
        if verdict_in_text(text) is None:
            said = re.findall(r"(?im)^[\s*_`]*verdict[\s*_`]*:[\s*_`]*([^*_`\n]+)", text)
            return (f"{path.name} reviews this plan, but its verdict `{said[-1].strip() if said else 'none'}` is not a "
                    "plan-review verdict: write `Verdict:` and one of " + ", ".join(f"`{v}`" for v in PLAN_VERDICTS))
        run = str(rnd.session.get("pipeline_run_id") or "")
        return (f"{path.name} reviews this plan, but P3 does not count it for this run: write the review with the "
                f"Write tool, with `Pipeline-Run: {run}` and the ticket line of the plan")
    return None


def _check_review(rnd: Round, row_ids: set[str]) -> None:
    from artifact_requirements import review_must_fix_ids, select_plan_review

    # The peer records done before it returns, so its join row does not exist yet. The P3 gate
    # and P9 still check who wrote the review; done reads the review of this run without the join.
    chosen = select_plan_review(rnd.ctx, {**rnd.session, "verdict_join": False})
    text = chosen[0].read_text(encoding="utf-8") if chosen else ""
    if f"Last reviewed plan sha256: {rnd.sha[:12]}" not in text and rnd.sha not in text:
        raise ValueError(_unread_review(rnd) or "write the plan review of this plan first: its "
                         "`Last reviewed plan sha256:` line names it")
    missing = sorted(review_must_fix_ids(text) - row_ids)
    if missing:
        raise ValueError(f"add a row for each must-fix item of the review: {', '.join(missing)}")


def carried(context_dir: Path, session: dict | None, stage: str) -> bool:
    """True when the round for the plan as it is now does not name `stage`: its pass on an earlier sha stands."""
    rnd = Round(context_dir, session)
    rounds = rnd.rounds()
    return bool(rnd.plan and rounds and rounds[-1]["plan_sha256"] == rnd.sha and stage not in rounds[-1]["due"])


def answered_at_limit(context_dir: Path, session: dict | None, stage: str) -> bool:
    """True when `stage` reviews no more (its cycle limit) and the update answered its must-fix rows.

    each reviewer of the plan round reviews once. A must-fix row of the round has a ledger row of
    the update (p4_problems), and the round for the plan as it is now does not name the stage. Its card of the
    earlier plan then stands, as a carried P3 or P6 item does: a rescore outside the round would review twice."""
    rnd = Round(context_dir, session)
    rounds = rnd.rounds()
    if not carried(context_dir, session, stage) or not _at_limit(rounds[:-1], stage):
        return False
    return any(r.get("severity") == "must-fix" for past in rounds[:-1] for r in rnd.rows(stage, past["n"]))


def route(context_dir: Path, session: dict | None) -> dict | None:
    """The stop guard's row while a round is open, else None."""
    rnd = Round(context_dir, session)
    if rnd.plan is None or not rnd.rounds():
        return None
    problems, nxt = state(rnd, check=True)
    if not problems:
        return None
    command = (nxt or "").removeprefix("NEXT: ").strip("`") or "python3 scripts/plan_wave.py"
    current = rnd.rounds()[-1]
    pending = [s for s in current.get("due", []) if s not in current.get("done", {})] or current.get("due", [])
    row = (rnd.member(pending[0]) if pending else None) or next(iter(rnd.members()), None)
    if row is None:  # a round with no member on: nothing to route to
        return None
    return {"block": row["block"], "slug": row["slug"], "phase": "planning", "command": command, "detail": problems[0]}


def p4_input(context_dir: Path, session: dict | None) -> Path | None:
    """The round file that P4 answers: the newest round with must-fix rows. A P4 that failed the scope check
    changed the plan, and its retry answers the same round, so the plan sha does not matter here. A round that
    opened after it with no rows does not hide it: y3's P9 repair P4 got the P3 review alone, and looped."""
    rnd = Round(context_dir, session)
    for current in reversed(rnd.rounds()):
        path = rnd.round_file(current["n"])
        if path.is_file() and must_fix(rnd, current):
            return path
    return None


def main(argv: list[str] | None = None) -> int:
    from gate_common import ctx

    parser = argparse.ArgumentParser(description="The plan review round")
    parser.add_argument("action", nargs="?", choices=("add", "done"))
    parser.add_argument("--stage")
    parser.add_argument("--json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rnd = Round(ctx())
    try:
        if args.action == "add":
            for row in add(rnd, str(args.stage), json.loads(args.json or "{}")):
                print(json.dumps(row))
            return 0
        if args.action == "done":
            print(f"OK  {args.stage}: {done(rnd, str(args.stage))} must-fix rows")
            return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL  plan-review-round: {exc}", file=sys.stderr)
        return 1
    problems, nxt = state(rnd, check=args.check)
    for line in problems:
        print(f"FAIL  {line}", file=sys.stderr)
    if nxt:
        print(nxt)
    if not problems:
        print("OK  plan-review-round: each due reviewer passed this plan")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
