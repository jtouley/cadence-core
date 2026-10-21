"""Guard `gh pr create` and `gh pr ready`, and `gh pr comment` and `gh pr review`.

On run efcd8404 an orchestrator opened a PR with no Cadence session and no review.
In a repo with `.context/` and commits ahead of the base, this guard denies those
commands unless one of these is true:

(a) the worktree's run has an E3 breadcrumb of its own run, written after activation
    (for `gh pr ready`, of the current HEAD too). With the architecture pack off,
    escape (a) is E1 done, and only for `gh pr create`: `gh pr ready` needs (b),
(b) a PR review names HEAD as its last reviewed commit (on Claude Code, attested to
    a peer that is not an orchestrator),
(c) the command starts with `CADENCE_ALLOW_PR=1` (logged to .context/issues/).

`gh pr comment` and `gh pr review` need the R5 closure breadcrumb of the worktree's run at
HEAD (scripts/closure.py), or (c). With no run and no commits ahead, they are allowed.

The hook runs on every shell call on both hosts. On a command that does not match,
`pr_guard_decision` does no file-system, git or import work.
"""

from __future__ import annotations

import re

# Where a shell command word starts: the line start, a new line, `;`, `&`, `|`, `(` or `{`,
# then env assignments and an optional `env`, `command` or `exec` wrapper.
SEGMENT_START = (
    r"(?:^|[;&|(\n{])\s*((?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)"
    r"(?:(?:env|command|exec)\s+((?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*))?"
)
# `gh` (bare or with a path), global repo flags, then `pr create`, `pr ready`, `pr comment` or `pr review`.
PR_COMMAND = re.compile(
    SEGMENT_START + r"(?:\S*/)?gh(?:\s+(?:--repo=\S+|--repo\s+\S+|-R\s+\S+))*\s+pr\s+(create|ready|comment|review)\b"
)
TALK_VERBS = ("comment", "review")
_OVERRIDE = re.compile(r"(?:^|\s)CADENCE_ALLOW_PR=1\s")

REACH = (
    "The guard applies to a repository with a Cadence footprint (.cadence.json, a run record "
    "or .context/attest/), and only to the current branch (not `gh pr ready <n>` of another branch)."
)


def pr_guard_decision(command: str, cwd, host: str) -> str | None:
    """The deny message for a guarded `gh pr` command, or None to allow."""
    matches = list(PR_COMMAND.finditer(command))
    if not matches:
        return None
    return _decide(matches, command, cwd, host)


def _cadence_footprint(primary, context) -> bool:
    """Cadence ran or is set up here. Other skills also make .context/."""
    return (
        (primary / ".cadence.json").is_file()
        or (context / "cadence-session.json").is_file()
        or any((context / "sessions" / "by-worktree").glob("*.json"))
        or (context / "attest").is_dir()
    )


def _decide(matches: list[re.Match[str]], command: str, cwd, host: str) -> str | None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for extra in (root / "scripts", root / "shared" / "scripts"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    from context_root import find_primary_checkout

    cwd = Path(cwd)
    primary = find_primary_checkout(cwd)
    context = primary / ".context"
    if not context.is_dir() or not _cadence_footprint(primary, context):
        return None

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()

    head = git("rev-parse", "HEAD")
    # Each guarded segment needs its own pass; an override covers only its own segment.
    guarded = [m.group(3) for m in matches if not _OVERRIDE.search((m.group(1) or "") + (m.group(2) or ""))]
    if len(guarded) < len(matches):
        _log_override(context, f"{git('branch', '--show-current')}\t{head}\t{command.strip()}")
    if not guarded:
        return None
    from pipeline_run_binding import worktree_run_session

    session = worktree_run_session(context, cwd)
    deny, guarded = _talk_gate(context, session, guarded, head, cwd, git)
    if deny or not guarded:
        return deny
    verb = "ready" if "ready" in guarded else "create"  # ready is the stricter check

    why_not_a = _e3_problem(context, session, verb, head, cwd) if session else None
    if session and why_not_a is None:
        return None

    claude = host == "claude" or os.environ.get("CLAUDECODE") == "1"
    unknown = bool(claude and session and session.get("orchestrator_source") == "missing")
    found, stage_lines = _review_escape(context, head, claude, cwd, unknown)
    if found:
        return None

    base, even = _even_with_base(cwd, session, git)
    if even:
        return None

    reasons = []
    if why_not_a:
        reasons.append(f"(a) failed: {why_not_a}")
    reasons += [f"(b) failed: {line}" for line in stage_lines]
    if unknown:
        reasons.append("(b) failed: orchestrator unknown: activate through the Bash tool")
    if not base:
        reasons.append("no base branch was found, so the guard cannot count the commits ahead")
    return _deny_text(verb, reasons, cwd)


def _log_override(context, line: str) -> None:
    """One line in .context/issues/pr-guard-overrides.log: the time, the branch, HEAD and the command."""
    from datetime import datetime, timezone

    log = context / "issues" / "pr-guard-overrides.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now(timezone.utc).isoformat()}\t{line}\n")


def _even_with_base(cwd, session: dict | None, git) -> tuple[str | None, bool]:
    """(the base branch, HEAD has no commits ahead of it)."""
    from phase_ledger import resolve_base

    base = resolve_base(cwd, session or {})
    return base, bool(base and git("rev-list", "--count", f"{base}..HEAD") == "0")


def _talk_gate(context, session: dict | None, guarded: list[str], head: str, cwd, git) -> tuple[str | None, list[str]]:
    """(the deny text of a `gh pr comment` or `gh pr review` segment, or None; the other verbs).

    They wait for R5 of the worktree's run at HEAD. A review or an E3 pass does not open them. With
    no run and no commits ahead, the comment is not on a branch of a run (a review of another PR).
    """
    talk = [v for v in guarded if v in TALK_VERBS]
    rest = [v for v in guarded if v not in TALK_VERBS]
    if not talk:
        return None, rest
    from pathlib import Path

    from closure import closure_problem

    why = closure_problem(context, session, head)
    if why is None or (session is None and _even_with_base(cwd, None, git)[1]):
        return None, rest
    return (
        f"BLOCKED: `gh pr {talk[0]}` waits for R5 (closure) of this worktree's run at HEAD ({why}).\n"
        "ACTION: one of:"
        f"\n  (a) run R5 in this worktree (python3 {Path(__file__).resolve().parent.parent / 'scripts' / 'closure.py'}),"
        " then write the R6 comment"
        " (references/pr-review-github-comment.md);"
        "\n  (b) prefix the command with CADENCE_ALLOW_PR=1 (logged to .context/issues/pr-guard-overrides.log).\n"
        + REACH
    ), rest


def _deny_text(verb: str, reasons: list[str], cwd) -> str:
    """The BLOCKED text. Option (a) names E3, or E1 when the architecture pack is off."""
    from load_stages import stage_slugs

    e3_on = "architecture-review-impl" in stage_slugs(workspace=cwd)
    past = "E3 (architecture-review-impl)" if e3_on else "E1 (implement)"
    also_b = "" if e3_on or verb == "create" else " (`gh pr ready` also needs (b))"
    return (
        f"BLOCKED: `gh pr {verb}` needs a Cadence run past {past.split(' ')[0]} or a peer review of HEAD"
        + (f" ({'; '.join(reasons)})" if reasons else "")
        + ".\nACTION: one of:"
        f"\n  (a) run Cadence past {past} in this worktree{also_b};"
        "\n  (b) get a review of HEAD from a review peer (`Last reviewed commit: <HEAD>`);"
        "\n  (c) prefix the command with CADENCE_ALLOW_PR=1 (logged to .context/issues/pr-guard-overrides.log).\n"
        + REACH
    )


def _e3_problem(context, session: dict, verb: str, head: str, cwd) -> str | None:
    """Why escape (a) does not apply, or None when it does."""
    import json
    from datetime import datetime

    from load_stages import stage_slugs

    if session.get("hard_stop"):
        stop = session["hard_stop"]
        return f"the run has a hard stop ({stop.get('class') if isinstance(stop, dict) else stop})"
    run_id = str(session.get("pipeline_run_id") or "")
    if "architecture-review-impl" not in stage_slugs(workspace=cwd):
        return _e1_problem(context, session, verb, cwd)
    crumb = context / "gates" / f"architecture-review-impl-{run_id}.json"
    try:
        data = json.loads(crumb.read_text(encoding="utf-8"))
        mtime = crumb.stat().st_mtime
    except (OSError, ValueError):
        return f"no E3 breadcrumb for run {run_id}"
    if not isinstance(data, dict) or data.get("pipeline_run_id") != run_id:
        return f"no E3 breadcrumb for run {run_id}"
    try:
        activated = datetime.fromisoformat(str(session.get("activated_at"))).timestamp()
    except ValueError:
        activated = 0.0
    if mtime < activated:
        return f"the E3 breadcrumb for run {run_id} is from before activation"
    if verb == "ready" and data.get("head") != head:
        return f"E3 breadcrumb is for {data.get('head')}, HEAD is {head}: run E3 again"
    return None


def _e1_problem(context, session: dict, verb: str, cwd) -> str | None:
    """With the architecture pack off (no E3), escape (a) is E1 done, read now:
    the run's own P9 marker and a commit for each plan phase (implement_gate.py).

    E1 done does not name a HEAD, so it opens `gh pr create` only. For `gh pr ready`
    escape (b), a review of HEAD, decides.
    """
    if verb == "ready":
        return "`gh pr ready` needs a peer review of HEAD"
    from artifact_requirements import entry_invariant_matches
    from phase_ledger import assess, open_work

    if not entry_invariant_matches(context, session):
        return f"no P9 marker for run {session.get('pipeline_run_id')}"
    work = open_work(assess(context, cwd))
    return f"E1 is open: {work}" if work else None


def _review_escape(context, head: str, claude: bool, cwd, unknown: bool) -> tuple[bool, list[str]]:
    """Escape (b): a review of HEAD from a review peer. Never with an unknown orchestrator."""
    if unknown or not head:
        return False, []
    return _review_of_head(context, head, claude, cwd)


def _head_reviews(context, head: str):
    """The reviews whose last `Last reviewed commit:` line names HEAD."""
    from final_consolidate import REVIEW_GLOBS, last_reviewed_sha

    for pattern in REVIEW_GLOBS:
        for review in sorted(context.glob(pattern)):
            try:
                sha = last_reviewed_sha(review.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if sha and head.startswith(sha):
                yield review


def _review_of_head(context, head: str, claude: bool, cwd) -> tuple[bool, list[str]]:
    """(a review of HEAD counts, stage lines of the attested reviews that did not count).

    On a verdict_join run, an attested review counts only from a writer of the
    R4 stages. The stage check reads the same newest attest row as the attest check, over
    every run log, and the run of the worktree at `cwd` (S-11).
    """
    from artifact_requirements import review_stage_check

    if not claude:
        return next(_head_reviews(context, head), None) is not None, []
    import peer_attest

    check = review_stage_check(context, cwd=cwd, slug="final-consolidate", claude=True)
    exclude = peer_attest.orchestrator_ids(context)
    lines: list[str] = []
    for review in _head_reviews(context, head):
        ok, reason = check(review) if check is not None else (peer_attest.attested_any(context, review, exclude), "")
        if ok:
            return True, []
        if reason:
            lines.append(f"final-consolidate: {reason}")
    return False, lines
