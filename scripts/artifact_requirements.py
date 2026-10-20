"""Shared artifact + provenance checks for Cadence peer gates and P9.

Matching rules (plan D2):
- Text/Markdown: normalize bold/backticks, then look for KEY: value markers
- JSON: top-level key KEY equals string value from marker KEY: value
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pipeline_run_binding import (
    current_session,
    load_session,
    other_workspace_hint,
    run_for,
    run_ids_in_text,
    worktree_run_session,
)
from prompt_shape import (
    delegation_record_path,
    delegation_stage_for_prompt,
    hook_records_only,
    task_prompt_block_reason,
)

ENTRY_INVARIANT_OK = Path("gates") / "entry-invariant.ok"
LEGACY_DRAFT_PR_RECORD = Path("prs") / "draft-pr.md"
_BRANCH = re.compile(r"^\s*\**Branch:?\**\s*(\S+)\s*$", re.M)
_PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")


def draft_pr_record_path(context_dir: Path, run_id: str) -> Path:
    """The E4 record of one run, `prs/draft-pr-<run_id>.md` (cd-76 D2)."""
    return Path(context_dir) / "prs" / f"draft-pr-{run_id}.md"


def draft_pr_record(context_dir: Path, session: dict | None, branch: str | None) -> Path | None:
    """The draft-PR record of a run: one rule for E4 and R4 (cd-76 R2-D8a).

    The run's own `prs/draft-pr-<run_id>.md` when it exists. Else the legacy
    `prs/draft-pr.md` only when its `Branch:` is `branch` (a run started before cd-76).
    A detached HEAD (`branch` None or "HEAD") never matches a legacy record.
    """
    run_id = str((session or {}).get("pipeline_run_id") or "")
    if run_id:
        own = draft_pr_record_path(context_dir, run_id)
        if own.is_file():
            return own
    legacy = Path(context_dir) / LEGACY_DRAFT_PR_RECORD
    if not branch or branch == "HEAD" or not legacy.is_file():
        return None
    found = _BRANCH.search(legacy.read_text(encoding="utf-8", errors="replace"))
    return legacy if found and found.group(1) == branch else None


def draft_pr_number(record: Path) -> int | None:
    """The PR number of the first PR URL in a draft-PR record, else None."""
    try:
        found = _PR_URL.search(Path(record).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return int(found.group(1)) if found else None

_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_BACKTICK = re.compile(r"`([^`]+)`")
_MARKER_RE = re.compile(r"^(RUN_BY_[A-Z_]+):\s*(.+)$")
_VERDICT_RE = re.compile(
    r"^\s*Verdict:\s*(READY TO EXECUTE|READY WITH CHANGES|NOT READY)\s*$", re.M
)
_BARE_VERDICT_RE = re.compile(
    r"^\s*(READY TO EXECUTE|READY WITH CHANGES|NOT READY)\s*$", re.M
)
BLOCKING_PLAN_VERDICTS = frozenset({"READY WITH CHANGES", "NOT READY"})
# references/review-rigor.md: the `## <section>` headings every R1 review carries.
REVIEW_RIGOR_SECTIONS = (
    "Reproductions",
    "End-to-end walk",
    "Adversarial inputs",
    "Contracts",
    "Design choices",
    "Scope",
    "Lookback",
    "Reuse and extension",  # gh-99
)


def entry_invariant_ok_path(context_dir: Path, run_id: str | None = None) -> Path:
    """The P9 marker of one run, `gates/entry-invariant-<run_id>.ok` (cd-76 D2).

    With no run id, the legacy shared name. Only the plan freeze reads the legacy name, and
    only when it names the run (shared/skill_gate.py, PR #168 R1 N-4).
    """
    if run_id:
        return Path(context_dir) / "gates" / f"entry-invariant-{run_id}.ok"
    return Path(context_dir) / ENTRY_INVARIANT_OK


def clear_entry_invariant_ok(context_dir: Path, run_id: str | None = None) -> None:
    path = entry_invariant_ok_path(context_dir, run_id)
    if path.is_file():
        path.unlink()


def entry_invariant_matches(context_dir: Path, session: dict | None, marker: Path | None = None) -> bool:
    """True when the run of ``session`` passed P9: its own marker (or ``marker``) exists and names it.

    One rule for E1, the skill gate and the stop guard (cd-76 R2-D3a). A marker that
    names another `pipeline_run_id`, or another task, is not evidence. A marker that
    does not parse, or that names neither, counts as true: the e5c3a92 result. For the
    stop guard that holds the stop (fail closed); for E1 and the skill gate it allows the
    edit, as before cd-76. The hooks protect the marker from agent writes.
    No session or no run id: false.
    """
    run_id = str((session or {}).get("pipeline_run_id") or "")
    if not run_id:
        return False
    path = marker or entry_invariant_ok_path(context_dir, run_id)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    marker_run = data.get("pipeline_run_id")
    if isinstance(marker_run, str) and marker_run and marker_run != run_id:
        return False
    marker_task, session_task = data.get("task_id"), (session or {}).get("task_id")
    if isinstance(marker_task, str) and marker_task and session_task:
        return marker_task == str(session_task)
    return True


def write_entry_invariant_ok(
    context_dir: Path,
    *,
    pipeline_run_id: str,
    task_id: str,
    written_at: str,
) -> Path:
    gates = Path(context_dir) / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    path = entry_invariant_ok_path(context_dir, pipeline_run_id)
    payload = {
        "pipeline_run_id": pipeline_run_id,
        "task_id": task_id,
        "written_at": written_at,
    }
    tmp = path.with_suffix(".ok.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def normalize_text_markers(text: str) -> str:
    out = _BOLD.sub(r"\1", text)
    out = _BACKTICK.sub(r"\1", out)
    return out


def parse_marker(marker: str) -> tuple[str, str] | None:
    m = _MARKER_RE.match(marker.strip())
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def _json_matches_markers(path: Path, markers: tuple[str, ...]) -> bool:
    """A JSON object artifact matches a `KEY: value` marker when its top-level KEY equals value."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    pairs = [parsed for parsed in map(parse_marker, markers) if parsed]
    return any(data.get(key) == value for key, value in pairs)


def file_matches_markers(path: Path, markers: tuple[str, ...]) -> bool:
    if not path.is_file():
        return False
    if path.suffix == ".json":
        return _json_matches_markers(path, markers)
    try:
        # cd-76 R2: invalid UTF-8 must not crash a gate; the markers are ASCII.
        text = normalize_text_markers(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    for marker in markers:
        if marker in text:
            return True
    return False


def require_active_session(
    context_dir: Path,
    skill: str = "cadence",
    label: str = "activate-session",
    cwd: Path | None = None,
) -> list[str]:
    """The worktree at ``cwd`` has its own active run (cd-47 G-D4: never another worktree's)."""
    data = worktree_run_session(Path(context_dir), cwd)
    if data is not None:
        return [] if data.get("active_skill") == skill else [f"{label}: active_skill != {skill!r}"]
    hint = other_workspace_hint(Path(context_dir), cwd)
    root = load_session(Path(context_dir))
    if root is None:
        problem = "missing cadence-session.json"
    elif root.get("active_skill") != skill:
        problem = f"active_skill != {skill!r}"
    else:
        problem = "no active cadence session for this worktree"
    return [f"{label}: {problem}" + (f"; {hint}" if hint else "")]


def require_lean_delegation(context_dir: Path, stage: str) -> list[str]:
    """Errors unless the session has a lean delegation record for ``stage``.

    The Task hook writes the record. Peer gates re-check that stored prompt. With
    ``peer_models`` (cd-58 D4), the peer-model check is added too, so the gate names
    every reason.
    """
    from peer_models import SLUG_FOR_STAGE, require_peer_model

    errors = _lean_delegation_problems(Path(context_dir), stage)
    return errors + require_peer_model(Path(context_dir), SLUG_FOR_STAGE.get(stage, stage))


def _lean_delegation_problems(context_dir: Path, stage: str) -> list[str]:
    """The first lean-record failure for ``stage``; each check needs the one before it."""
    data = current_session(context_dir) or None
    if data is None:
        return [f"{stage}: missing cadence-session.json for lean delegation"]
    run_id = data.get("pipeline_run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return [f"{stage}: active session has no pipeline_run_id"]
    path = delegation_record_path(context_dir, run_id.strip(), stage)
    if not path.is_file():
        return [f"{stage}: missing lean delegation record"]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{stage}: unreadable lean delegation record: {exc}"]
    return _lean_record_problems(data, record, stage)


def _lean_record_problems(data: dict, record: object, stage: str) -> list[str]:
    """The first problem of a readable lean delegation record: its writer, then its command."""
    if hook_records_only(data) and (not isinstance(record, dict) or record.get("source") != "PreToolUse"):
        source = record.get("source") if isinstance(record, dict) else None
        return [
            f"{stage}: on this Claude Code run only the hook writes the lean delegation record "
            f"(source {source or 'none'}); launch the peer with the lean slash command"
        ]
    command = record.get("command") if isinstance(record, dict) else None
    if not isinstance(command, str):
        return [f"{stage}: lean delegation record has no command"]
    reason = task_prompt_block_reason(command)
    if reason:
        return [f"{stage}: lean prompt rejected: {reason}"]
    recorded = delegation_stage_for_prompt(command)
    if recorded != stage:
        return [f"{stage}: delegation record is for {recorded or 'a different command'}"]
    return []


def _artifact_candidates(context_dir: Path, relative_globs: tuple[str, ...], worktree_globs: tuple[Path, ...],
                         scope: list[re.Pattern[str]] | None) -> list[Path]:
    """Files that match the .context globs, then the worktree globs, each sorted, kept in scope."""
    candidates: list[Path] = []
    for pattern in relative_globs:
        candidates.extend(sorted(context_dir.glob(pattern)))
    for pattern in worktree_globs:
        parent, name = pattern.parent, pattern.name
        if parent.is_dir():
            candidates.extend(sorted(parent.glob(name)))
    if scope:
        candidates = [p for p in candidates if _in_scope(p, scope)]
    return candidates


def require_artifact(
    context_dir: Path,
    *,
    relative_globs: tuple[str, ...] = (),
    worktree_globs: tuple[Path, ...] = (),
    markers: tuple[str, ...] = (),
    label: str = "artifact",
    scope: list[re.Pattern[str]] | None = None,
    validate: Callable[[Path], list[str]] | None = None,
    accept: Callable[[Path], bool] | None = None,
    rejections: Callable[[list[Path]], list[str]] | None = None,
) -> list[str]:
    """Errors unless some candidate matches the globs, scope, markers, and ``validate``.

    ``validate`` returns a file's problems ([] = acceptable); the error names the
    marked file with the fewest. When ``accept`` takes no file, ``rejections`` (gh-66
    ``stage_rejections``) gives the per-file stage lines, before the attest text.
    """
    context_dir = Path(context_dir)
    candidates = _artifact_candidates(context_dir, relative_globs, worktree_globs, scope)
    if not candidates:
        where = ", ".join(relative_globs) or ", ".join(str(p) for p in worktree_globs)
        return [f"{label}: no artifact matching {where}"]

    marked = [p for p in candidates if file_matches_markers(p, markers)] if markers else candidates
    if not marked:
        return [f"{label}: no file matched markers {markers} among {[str(p) for p in candidates]}"]
    if accept is not None:
        marked, problems = _accepted(marked, label, accept, rejections)
        if problems:
            return problems
    if validate is None:
        return []
    problems = {p: validate(p) for p in marked}
    best = min(marked, key=lambda p: len(problems[p]))
    if not problems[best]:
        return []
    return [f"{label}: {best}: {'; '.join(problems[best])}"]


def _accepted(marked: list[Path], label: str, accept, rejections) -> tuple[list[Path], list[str]]:
    """(files that `accept` takes, the errors when it takes none)."""
    kept = [p for p in marked if accept(p)]
    if kept:
        return kept, []
    if getattr(accept, "reason", None):
        return kept, [f"{label}: {accept.reason}"]
    lines = rejections(marked) if rejections is not None else []
    return kept, lines or [
        f"{label}: no verdict file whose content matches what a delegated peer last wrote. "
        "The orchestrator does not write verdict files. The peer saves it with the Write or Edit "
        "tool (a shell write is not attested); if an Edit was not attested, the peer rewrites the file with Write"
    ]


def require_sections(sections: tuple[str, ...]) -> Callable[[Path], list[str]]:
    """``validate`` for require_artifact: every `## <section>` heading is present."""

    def validate(path: Path) -> list[str]:
        try:
            text = normalize_text_markers(path.read_text(encoding="utf-8"))
        except OSError as e:
            return [f"unreadable: {e}"]
        present = {m.strip() for m in re.findall(r"^##\s+(.+?)\s*$", text, re.M)}
        missing = [s for s in sections if s not in present]
        return [f"missing sections {missing}"] if missing else []

    return validate


def verdict_in_text(text: str) -> str | None:
    """Last labeled or bare verdict line. Bold and backticks are already stripped."""
    text = normalize_text_markers(text)
    found = [*_VERDICT_RE.finditer(text), *_BARE_VERDICT_RE.finditer(text)]
    if not found:
        return None
    return max(found, key=lambda match: match.start()).group(1)


def _peer_attest():
    shared = Path(__file__).resolve().parent.parent / "shared" / "scripts"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import peer_attest

    return peer_attest


def peer_written_filter(
    context_dir: Path, cwd: Path | None = None, *, session: dict | None = None, slug: str | None = None
) -> Callable[[Path], bool] | None:
    """On Claude Code, accept only verdicts whose content a peer's write was attested for.

    - The worktree has its own claude run: the file's sha256 is in that run's attest log.
    - The worktree has no run and this is Claude Code (CLAUDECODE=1): the file's sha256 is
      in any run's log. No row: not accepted (fail closed).
    - Other hosts send no subagent field: None keeps their behavior unchanged.

    Rows by an orchestrator (``peer_attest.orchestrator_ids``) never count. When
    activation found no orchestrator note, the filter accepts nothing (cd-47 G-D7).
    With ``slug`` (a stages.json slug), a ``verdict_join`` run also needs a writer of that
    stage's ``writer_stages`` (gh-66, ``_stage_check``). The filter never carries a reason.
    """
    peer_attest = _peer_attest()
    context_dir = Path(context_dir)
    if session is None:  # cd-63 R4-C-1: a caller that judges a run passes it
        session = worktree_run_session(context_dir, cwd)
    if session is not None:
        if session.get("host") != "claude":
            return None
        if session.get("orchestrator_source") == "missing":
            return Rejected(ORCHESTRATOR_UNKNOWN)
        run_id = str(session.get("pipeline_run_id") or "")
        exclude = peer_attest.orchestrator_ids(context_dir)
        base = lambda path: peer_attest.attested(context_dir, run_id, path, exclude)  # noqa: E731
    elif os.environ.get("CLAUDECODE") == "1":
        exclude = peer_attest.orchestrator_ids(context_dir)
        base = lambda path: peer_attest.attested_any(context_dir, path, exclude)  # noqa: E731
    else:
        return None
    check = _stage_check(context_dir, session=session, slug=slug)
    if check is None:
        return base
    return lambda path: base(path) and check(path)[0]


def _stage_check(
    context_dir: Path, *, cwd: Path | None = None, session: dict | None = None, slug: str | None,
    skill_root: Path | None = None,
) -> Callable[[Path], tuple[bool, str]] | None:
    """gh-66 PD18: the one writer-stage check, `path -> (ok, reason)`; None keeps the old check.

    A run reads `writer_stages` only when it has `verdict_join` (Cursor, Pi and old Claude runs
    never do). The any-run path (no worktree run, CLAUDECODE=1) decides per file, from the run
    of the file's newest attest row. A `slug` with no `writer_stages` rejects each file with a
    reason (PD14), not with a traceback.
    """
    if slug is None:
        return None
    context_dir = Path(context_dir)
    if session is None:
        session = worktree_run_session(context_dir, cwd)
    if session is not None:
        joined = session.get("host") == "claude" and session.get("verdict_join") is True
        return _run_stage_check(context_dir, session, slug, skill_root) if joined else None
    if os.environ.get("CLAUDECODE") != "1":
        return None
    return _any_run_stage_check(context_dir, slug, skill_root)


def _writer_stages_or_reason(slug: str, skill_root: Path | None) -> tuple[frozenset[str] | None, str]:
    """PD14: the slug's writer_stages, or the reason a file is rejected when it has none."""
    from load_stages import CODE_ROOT, stages_path, writer_stages

    try:
        return writer_stages(slug, skill_root=skill_root), ""
    except KeyError:
        return None, f"no writer_stages for {slug} in {stages_path(skill_root or CODE_ROOT)}"


def _stage_reason_for(context_dir: Path, path: Path, run: dict, slug: str, skill_root: Path | None,
                      exclude: frozenset[str], run_id: str | None) -> tuple[bool, str]:
    """(ok, reason) of one file against the writer stages (and models) of `slug` for `run`."""
    from peer_models import models_for_stages

    peer_attest = _peer_attest()
    allowed, bad = _writer_stages_or_reason(slug, skill_root)
    if bad:
        return False, bad
    models = models_for_stages(run.get("peer_models"), allowed)  # gh-66 (#59)
    if run_id is None:
        return peer_attest.stage_attested_any(context_dir, path, allowed, exclude, models)
    return peer_attest.stage_attested(context_dir, run_id, path, allowed, exclude, models)


def _run_stage_check(context_dir: Path, session: dict, slug: str, skill_root: Path | None):
    exclude = _peer_attest().orchestrator_ids(context_dir)
    run_id = str(session.get("pipeline_run_id") or "")
    return lambda path: _stage_reason_for(context_dir, path, session, slug, skill_root, exclude, run_id)


def _joined_runs(context_dir: Path) -> dict[str, dict]:
    return {str(r.get("pipeline_run_id")): r for r in _run_records(context_dir) if r.get("verdict_join") is True}


def _any_run_stage_check(context_dir: Path, slug: str, skill_root: Path | None):
    """Per file: the run of the file's newest attest row decides (R9 C-3)."""
    peer_attest = _peer_attest()
    exclude = peer_attest.orchestrator_ids(context_dir)
    joined = _joined_runs(context_dir)

    def check(path: Path) -> tuple[bool, str]:
        found = peer_attest.newest_attest(context_dir, path, exclude)
        if found is None or found[2] not in joined:
            return True, ""  # the attest filter decides this file (old check)
        return _stage_reason_for(context_dir, path, joined[found[2]], slug, skill_root, exclude, None)

    return check


def review_stage_check(
    context_dir: Path, *, cwd: Path | None, slug: str, claude: bool
) -> Callable[[Path], tuple[bool, str]] | None:
    """`path -> (ok, reason)` for the pr_guard review escape (gh-66 PD5), else None (old check).

    It reads the same newest attest row, over every run log, as ``attested_any``, so an
    attest row in another run's log cannot open the escape (PR #145 R1 MF-1). A run of the
    worktree decides with its own `verdict_join`; with no run, the run of that row decides.
    """
    context_dir = Path(context_dir)
    session = worktree_run_session(context_dir, cwd)
    if session is None:
        return _review_check(context_dir, slug, None) if claude else None
    if session.get("host") != "claude" or session.get("verdict_join") is not True:
        return None
    return _review_check(context_dir, slug, session)


def _review_check(context_dir: Path, slug: str, session: dict | None):
    peer_attest = _peer_attest()
    exclude = peer_attest.orchestrator_ids(context_dir)
    joined = _joined_runs(context_dir)

    def check(path: Path) -> tuple[bool, str]:
        found = peer_attest.newest_attest(context_dir, path, exclude)
        if found is None or not peer_attest.attested_any(context_dir, path, exclude):
            return False, ""
        run = session if session is not None else joined.get(found[2])
        if run is None:
            return True, ""  # a run with no verdict_join: the attest check decides
        return _stage_reason_for(context_dir, path, run, slug, None, exclude, None)

    return check


def _run_records(context_dir: Path) -> list[dict]:
    _peer_attest()
    import session_store

    return session_store.run_records(Path(context_dir))


def stage_rejections(
    context_dir: Path, paths: list[Path], *, cwd: Path | None = None, session: dict | None = None, slug: str | None,
    skill_root: Path | None = None,
) -> list[str]:
    """gh-66 PD19: `<slug>: <reason>` for each path that the stage check rejects with a reason.

    Stateless: two calls give the same lines. A file that fails only the attest check gives
    no line, so the gate keeps its attest text for that case.
    """
    check = _stage_check(context_dir, cwd=cwd, session=session, slug=slug, skill_root=skill_root)
    if check is None:
        return []
    lines = []
    for path in paths:
        ok, reason = check(path)
        if not ok and reason:
            lines.append(f"{slug}: {reason}")
    return lines


ORCHESTRATOR_UNKNOWN = "orchestrator unknown: activate through the Bash tool"


class Rejected:
    """An ``accept`` filter that accepts nothing, with the reason the gate reports."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __call__(self, _path: Path) -> bool:
        return False


def _all_of(*accepts):
    """One accept that needs every non-None accept; None when none are given."""
    active = [a for a in accepts if a is not None]
    if not active:
        return None
    rejected = next((a for a in active if getattr(a, "reason", None)), None)
    if rejected is not None:
        return rejected  # it accepts nothing; keep its reason for the gate message
    return lambda path: all(a(path) for a in active)


def config_limit(key: str) -> int:
    """A cycle limit of the resolved config (scripts/cadence_config.py). A layer can only lower it."""
    import cadence_config

    return cadence_config.load(Path(__file__).resolve().parent.parent).get(key)


def plan_review_cycle_limit() -> int:
    """`plan_review_cycles`: the most P3 cycles before P4 carries the open findings or stops."""
    return config_limit("plan_review_cycles")


def adversarial_cycle_limit() -> int:
    """`adversarial_cycles`: the most P6 cycles."""
    return config_limit("adversarial_cycles")


CARRIED_HEADER = "## Carried review findings"
_MUST_FIX_ID = re.compile(r"\bMF-\d+\b")


@dataclass(frozen=True)
class ReviewLoop:
    """One bounded review loop (P3 plan review, P6 adversarial review), cd-63 D1.

    The scope rule, the cycle count and the newest-verdict choice are one copy for both.
    """

    glob: str
    history_stage: str
    limit_key: str  # the config key of the cycle limit
    blocking: frozenset[str]
    read_verdict: Callable[[str], str | None]
    accept: Callable[[Path, dict | None], Callable[[Path], bool] | None]
    counts: Callable[[Path], bool] = lambda _path: True
    history_counts: Callable[[dict], bool] = lambda _entry: True
    per_path: bool = False  # P6: count (agent_id, path) pairs; P3: distinct agents
    keep_unread: bool = False  # P6: a counted report with no readable verdict is still chosen
    round_of: Callable[[Path, str | None], int | None] | None = None  # P6: sort by round, then mtime
    slug: str | None = None  # gh-66: the stages.json slug whose writer_stages count on a verdict_join run


def _shared_scripts() -> None:
    shared = Path(__file__).resolve().parent.parent / "shared" / "scripts"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))


def _run_plan(context_dir: Path, session: dict) -> Path | None:
    from phase_ledger import select_plan

    workspace = session.get("workspace")
    worktree = Path(workspace) if isinstance(workspace, str) and Path(workspace).is_dir() else Path.cwd()
    return select_plan(Path(context_dir), worktree, session)


def _plan_slug(context_dir: Path, session: dict) -> str | None:
    plan = _run_plan(context_dir, session)
    return plan.name[: -len(".plan.md")] if plan is not None and plan.name.endswith(".plan.md") else None


def _counted(loop: ReviewLoop, path: Path, accept, slug: str | None) -> bool:
    if accept is not None and not accept(path):
        return False
    if loop.round_of is not None and loop.round_of(path, slug) is None:
        return False
    return loop.counts(path)


def _loop_stages(loop: ReviewLoop, session: dict) -> frozenset[str] | None:
    """gh-66 PD6: on a verdict_join run, a cycle counts only a writer of the loop's stage."""
    if loop.slug is None or session.get("verdict_join") is not True:
        return None
    from load_stages import writer_stages

    return writer_stages(loop.slug)


def loop_cycles(context_dir: Path, loop: ReviewLoop, session: dict | None = None) -> int:
    """Review cycles of this run for one loop.

    Claude Code: peers whose write the hook attested (P3: distinct agents; P6: distinct
    (agent, path) pairs of counted reports, cd-63 R4-B-1). Other hosts: the begins in the
    history that subagent_provenance writes (weaker: the orchestrator runs begin itself).
    """
    context_dir = Path(context_dir)
    if session is None:  # cd-76 R2-D5c: a dict reads only that run
        session = current_session(context_dir)
    run_id = str(session.get("pipeline_run_id") or "")
    if session.get("host") == "claude" and run_id:
        _shared_scripts()
        import peer_attest

        exclude = peer_attest.orchestrator_ids(context_dir)
        stages = _loop_stages(loop, session)
        if not loop.per_path:
            return len(peer_attest.writers(context_dir, run_id, loop.glob, exclude, stages))
        accept = loop.accept(context_dir, session)
        slug = _plan_slug(context_dir, session)
        pairs = peer_attest.writer_paths(context_dir, run_id, loop.glob, exclude, stages)
        return len({(agent, rel) for agent, rel in pairs if _counted(loop, context_dir / rel, accept, slug)})
    history = context_dir / "subagent-runs" / run_id / "history.jsonl"
    if not run_id or not history.is_file():
        return 0
    count = 0
    for line in history.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            count += entry.get("stage") == loop.history_stage and loop.history_counts(entry)
        except (json.JSONDecodeError, AttributeError):
            continue
    return count


def _verdict_row(loop: ReviewLoop, path: Path, slug: str | None) -> tuple[tuple, Path, str | None] | None:
    """(sort key, path, verdict) of one counted report; None when it is unreadable or has no verdict to keep."""
    try:
        raw = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime
    except OSError:
        return None
    verdict = loop.read_verdict(raw)
    if verdict is None and not loop.keep_unread:
        return None
    key = (loop.round_of(path, slug), mtime) if loop.round_of is not None else (mtime,)
    return key, path, verdict


def select_newest_verdict(
    context_dir: Path, loop: ReviewLoop, session: dict | None = None
) -> tuple[Path, str | None] | None:
    """The report that decides the loop: accepted, counted, newest (P6: highest round first).

    With no ticket tokens, the newest blocking report wins. A report with no readable
    verdict is dropped, except in a loop with ``keep_unread`` (P6 fails closed on it).
    """
    context_dir = Path(context_dir)
    scope = ticket_scope(context_dir, session=session)
    accept = loop.accept(context_dir, session)
    slug = _plan_slug(context_dir, session if session is not None else current_session(context_dir)) \
        if loop.round_of is not None else None
    rows: list[tuple[tuple, Path, str | None]] = []
    for path in context_dir.glob(loop.glob):
        row = _verdict_row(loop, path, slug) if _counted(loop, path, accept, slug) else None
        if row is not None:
            rows.append(row)
    if not rows:
        return None
    rows.sort(key=lambda row: row[0])
    if scope is None:
        blocking = [row for row in rows if row[2] in loop.blocking]
        if blocking:
            return blocking[-1][1], blocking[-1][2]
    return rows[-1][1], rows[-1][2]


_FENCE = re.compile(r"^\s*(```|~~~)")


def carried_section(context_dir: Path, header: str, session: dict | None = None, *, exact: bool = False) -> str:
    """The body under a carried-findings heading of the run's plan, up to the next `## `.

    Lines inside a fenced code block are skipped, so an example heading does not count.
    ``exact``: the heading line must equal ``header`` (P6, cd-63 R4-C-3); else it starts with it (P3).
    """
    found = carried_body(context_dir, header, session, exact=exact)
    return found or ""


def carried_body(context_dir: Path, header: str, session: dict | None = None, *, exact: bool = False) -> str | None:
    """Like ``carried_section``, but None when the plan has no such heading."""
    if session is None:
        session = load_session(Path(context_dir)) or {}
    plan = _run_plan(context_dir, session)
    if plan is None:
        return None
    body = _section_lines(plan.read_text(encoding="utf-8").splitlines(), header, exact=exact)
    return None if body is None else "\n".join(body)


def _section_lines(lines: list[str], header: str, *, exact: bool) -> list[str] | None:
    """The lines under `header` up to the next `## ` heading; a fenced block never ends it. None: no heading."""
    body: list[str] = []
    inside = fenced = False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and inside and line.startswith("## "):
            break
        elif not fenced and not inside and (line.strip() == header if exact else line.startswith(header)):
            inside = True
            continue
        if inside:
            body.append(line)
    return body if inside else None


def _p3_accept(context_dir: Path, session: dict | None):
    """In run scope; on a verdict_join run also written by a plan-review peer (gh-66 PD15, PD16)."""
    resolved = session if session is not None else current_session(Path(context_dir))
    scoped = run_scope_accept(context_dir, session=resolved)
    if resolved.get("verdict_join") is not True:
        return scoped
    return _all_of(peer_written_filter(context_dir, session=resolved, slug="plan-review"), scoped)


P3_LOOP = ReviewLoop(
    glob="reviews/plan_*.md",
    history_stage="plan-review",
    limit_key="plan_review_cycles",
    blocking=BLOCKING_PLAN_VERDICTS,
    read_verdict=verdict_in_text,
    accept=_p3_accept,
    slug="plan-review",
)


_PLAN_GATE_MODE = re.compile(r"^\s*Mode:\s*plan-gate\s*$", re.M)


def require_plan_gate_mode(path: Path) -> list[str]:
    """``validate``: a P6 report counts only with the line `Mode: plan-gate` (cd-63 R2-MF-1)."""
    try:
        text = normalize_text_markers(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        return [f"unreadable: {e}"]
    return [] if _PLAN_GATE_MODE.search(text) else ['missing line "Mode: plan-gate"']


# cd-63 D3: the strict adversarial verdict read. The line form takes only `#`, `>`, `*`, `-`
# and spaces before the key, so a bullet that quotes a prior verdict after other text does not
# match. The longest value comes first. The JSON key is lower case. The last match wins.
_ADV_LINE = re.compile(
    r"^[ \t]*(?:[#>*-]+[ \t]*)?Verdict[ \t]*:[ \t]*(APPROVE WITH CONDITIONS|REQUEST[ _]CHANGES|APPROVE)"
    r"[ \t]*(?:[—–\-:.(].*)?$",
    re.M,
)
_ADV_JSON = re.compile(r'"verdict"\s*:\s*"(APPROVE WITH CONDITIONS|REQUEST_CHANGES|REQUEST CHANGES|APPROVE)"')


def adversarial_verdict_in_text(text: str) -> str | None:
    """The last strict P6 verdict in the text, or None (no readable verdict fails closed)."""
    text = normalize_text_markers(text)
    found = [*_ADV_LINE.finditer(text), *_ADV_JSON.finditer(text)]
    if not found:
        return None
    return max(found, key=lambda match: match.start()).group(1).replace("_", " ")


CARRIED_ADVERSARIAL_HEADER = "## Carried adversarial findings"


def _sections(text: str) -> dict[str, list[str]]:
    """`## <title>` (lower case) to its body lines, outside fenced code blocks."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    fenced = False
    for raw in text.splitlines():  # fences are found before backticks are stripped
        if _FENCE.match(raw):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = normalize_text_markers(raw)
        if line.startswith("## "):
            current = out.setdefault(line[3:].strip().lower(), [])
            continue
        if current is not None:
            current.append(line)
    return out


def _defined_ids(lines: list[str], prefix: str) -> set[str]:
    pattern = re.compile(rf"^(?:###[ \t]+|-[ \t]+)({prefix}-\d+)\.(?:\s|$)")
    return {m.group(1) for line in lines if (m := pattern.match(line))}


def adversarial_ids(text: str) -> tuple[set[str], set[str]]:
    """(B-n ids, C-n ids) that an item line defines in its own section (cd-63 R4-C-3).

    `### B-n.` or `- B-n.` under `## Blocking issues`; `### C-n.` or `- C-n.` under
    `## Conditions`. A mention in body text, or a line in the wrong section, is not an id.
    """
    sections = _sections(text)
    return (_defined_ids(sections.get("blocking issues", []), "B"),
            _defined_ids(sections.get("conditions", []), "C"))


def blocking_body(text: str) -> str:
    """The non-blank body of `## Blocking issues`, or ""."""
    return "\n".join(line for line in _sections(text).get("blocking issues", []) if line.strip())


def carried_heading(path: Path) -> str:
    """`## Carried adversarial findings (<file> @ <sha12>)` of the raw bytes (cd-63 D2, R2-SF-3)."""
    import hashlib

    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    return f"{CARRIED_ADVERSARIAL_HEADER} ({Path(path).name} @ {digest})"


def _spec_pressure(path: Path) -> bool:
    """A spec-pressure report never counts or decides (either spelling of the suffix)."""
    return Path(path).name.endswith(("_spec-pressure.md", "_spec_pressure.md"))


def adversarial_round(path: Path, slug: str | None) -> int | None:
    """Round of a plan-gate report: `adversarial_<slug>.md` is 1, `adversarial_<slug>_r<n>.md` is n.

    Another name (for example `_spec-pressure`) has no round and does not count (cd-63 R6-N5-1).
    With no known slug, the name decides alone.
    """
    name = Path(path).name
    if _spec_pressure(path):
        return None
    if slug:
        if name == f"adversarial_{slug}.md":
            return 1
        match = re.fullmatch(rf"adversarial_{re.escape(slug)}_r(\d+)\.md", name)
        return int(match.group(1)) if match else None
    match = re.fullmatch(r"adversarial_.+_r(\d+)\.md", name)
    return int(match.group(1)) if match else 1


def _p6_accept(context_dir: Path, session: dict | None):
    """Attested by a peer, in run scope, with `Mode: plan-gate` (cd-63 R3-B-1, R4-C-1, R4-N2)."""
    return _all_of(
        peer_written_filter(context_dir, session=session, slug="adversarial-review"),
        run_scope_accept(context_dir, session=session),
        lambda path: not require_plan_gate_mode(path),
    )


P6_LOOP = ReviewLoop(
    glob="adversarial/adversarial_*.md",
    history_stage="de-adversarial-reviewer",
    limit_key="adversarial_cycles",
    blocking=frozenset({"REQUEST CHANGES"}),
    read_verdict=adversarial_verdict_in_text,
    accept=_p6_accept,
    counts=lambda path: not require_plan_gate_mode(path),
    history_counts=lambda entry: "spec-pressure" not in str(entry.get("command") or ""),
    per_path=True,
    keep_unread=True,
    round_of=adversarial_round,
    slug="adversarial-review",
)


SHADOW_MESSAGE = (
    'adversarial-review: {name} has no line "Mode: plan-gate". The peer did not use '
    "$CADENCE_HOME/skills/de-adversarial-reviewer/SKILL.md. A host skill with the same name hides it. "
    "Move that skill out of the host skills directory, "
    "then run python3 $CADENCE_HOME/scripts/preflight.py until it passes."
)


def _id_key(ident: str) -> tuple[str, int]:
    kind, number = ident.split("-", 1)
    return kind, int(number)


def adversarial_state(context_dir: Path, session: dict | None = None) -> dict:
    """Where the P6 loop of this run stands (one reader for the gate, P9 and the stop guard)."""
    context_dir = Path(context_dir)
    if session is None:
        session = current_session(context_dir)
    state: dict = {"report": None, "verdict": None, "cycles": loop_cycles(context_dir, P6_LOOP, session)}
    accept = _p6_accept(context_dir, session)
    state["reason"] = getattr(accept, "reason", None)
    chosen = select_newest_verdict(context_dir, P6_LOOP, session)
    if chosen is None:
        scoped = run_scope_accept(context_dir, session=session)
        modeless = sorted(
            (p for p in context_dir.glob(P6_LOOP.glob)
             if not _spec_pressure(p) and scoped(p) and require_plan_gate_mode(p)),
            key=lambda p: p.stat().st_mtime,
        )
        state["modeless"] = modeless[-1] if modeless else None
        reports = [p for p in context_dir.glob(P6_LOOP.glob)
                   if not _spec_pressure(p) and scoped(p) and not require_plan_gate_mode(p)]
        run_id = session.get("pipeline_run_id") or "?"
        state["missing"] = stage_rejections(context_dir, reports, session=session, slug="adversarial-review") or [
            f"adversarial-review: no plan-gate report of run {run_id}. Run /de-adversarial-reviewer plan-gate."]
    # PR #88 MF-1: a counted report whose name does not use the plan stem has no round, so it
    # can never decide. Name it when it is newer than the report that decides, or when none does.
    slug = _plan_slug(context_dir, session)
    newest = chosen[0].stat().st_mtime if chosen else float("-inf")
    misnamed = sorted(
        (p for p in context_dir.glob(P6_LOOP.glob)
         if slug and not _spec_pressure(p) and adversarial_round(p, slug) is None
         and p.stat().st_mtime > newest and accept(p)),
        key=lambda p: p.stat().st_mtime,
    )
    state["misnamed"] = (misnamed[-1], slug) if misnamed else None
    if chosen is None:
        return state
    path, verdict = chosen
    text = path.read_text(encoding="utf-8", errors="replace")
    heading = carried_heading(path)
    section = carried_body(context_dir, heading, session, exact=True)
    blocking, conditions = adversarial_ids(text)

    def mapped(ident: str) -> bool:
        return section is not None and re.search(rf"\b{re.escape(ident)}\b", section) is not None

    state.update(
        report=path, verdict=verdict, heading=heading, section=section,
        blocking=blocking, conditions=conditions, blocking_body=blocking_body(text),
        open_blocking=sorted((i for i in blocking if not mapped(i)), key=_id_key),
        open_conditions=sorted((i for i in conditions if not mapped(i)), key=_id_key),
    )
    return state


def adversarial_open(state: dict) -> bool:
    """At the limit: an id is open when it is not under the carried heading (cd-63 R6-MF-2)."""
    if state.get("verdict") != "REQUEST CHANGES":
        return False
    if state["section"] is None:
        return True
    if state["open_blocking"] or state["open_conditions"]:
        return True
    if not state["blocking"] and not state["conditions"]:
        return not state["section"].strip()
    return False


def check_adversarial_verdict(context_dir: Path, session: dict | None = None, *, stage: str = "p6") -> list[str]:
    """The P6 verdict of this run must not hold (cd-63 D4). ``stage``: "p6" (the gate) or "p9"."""
    context_dir = Path(context_dir)
    if session is None:
        session = current_session(context_dir)
    state = adversarial_state(context_dir, session)
    path, verdict = state["report"], state["verdict"]
    if state.get("misnamed"):
        bad, slug = state["misnamed"]
        return [f"adversarial-review: {bad.name} does not name the plan. Rename it "
                f"adversarial_{slug}.md (round 1) or adversarial_{slug}_r<n>.md (round n)."]
    if path is None:
        return _no_report_problems(state, session)
    if verdict is None:
        return [
            f"adversarial-review: {path.name} has no readable verdict. "
            'Write one line "Verdict: APPROVE | APPROVE WITH CONDITIONS | REQUEST CHANGES".'
        ]
    if verdict == "APPROVE":
        return []
    if verdict == "APPROVE WITH CONDITIONS":
        return _condition_problems(state) if stage == "p9" else []
    return _request_changes_problems(context_dir, session, state)


def _no_report_problems(state: dict, session: dict) -> list[str]:
    """Why this run has no P6 report to judge."""
    if state.get("modeless") is not None:
        return [SHADOW_MESSAGE.format(name=state["modeless"].name)]
    if state["reason"]:
        return [f"adversarial-review: {state['reason']}"]
    return state["missing"]  # gh-66: the stage lines, else the no-report text


def _condition_problems(state: dict) -> list[str]:
    """P9: the conditions of an APPROVE WITH CONDITIONS must be carried into the plan."""
    name, heading = state["report"].name, state["heading"]
    if state["conditions"]:
        if state["open_conditions"]:
            return [
                f'adversarial-review: {name} conditions not in the plan under "{heading}": '
                f'{", ".join(state["open_conditions"])}. Run /plan-update.'
            ]
        return []
    if state["section"] and state["section"].strip():
        return []
    return [f'adversarial-review: {name} conditions are not carried under "{heading}". Run /plan-update.']


def _request_changes_problems(context_dir: Path, session: dict, state: dict) -> list[str]:
    """REQUEST CHANGES: below the cycle limit, update and review again; at the limit, carry the open ids."""
    path, heading = state["report"], state["heading"]
    cycles = state["cycles"]
    if cycles < adversarial_cycle_limit():
        review = "" if plan_review_cycles(context_dir, session=session) >= plan_review_cycle_limit() else ", then /plan-review"
        return [
            f"adversarial-review: {path.name} verdict is REQUEST CHANGES (cycle {cycles} of {adversarial_cycle_limit()}). "
            f"Run /plan-update{review}, then /de-adversarial-reviewer plan-gate."
        ]
    if state["blocking_body"] and not state["blocking"]:
        return [
            f'adversarial-review: {path.name} has blocking items with no B-n id. Write each item as "### B-n. <title>".'
        ]
    if not adversarial_open(state):
        return []
    ids = [*state["open_blocking"], *state["open_conditions"]] if state["section"] is not None else sorted(
        state["blocking"] | state["conditions"], key=_id_key)
    what = ", ".join(ids) if ids else "the blocking items"
    return [
        f"adversarial-review: cycle limit reached ({cycles} cycles). Do not run P6 again. Run /plan-update. "
        f'Add "{heading}" and map {what} to the phase whose test settles each.'
    ]


def adversarial_route(context_dir: Path, session: dict | None = None) -> dict | None:
    """The stop guard's P6 row, or None when P6 does not hold (cd-63 R5-C-1, R6-MF-2, P7 F8).

    First match wins: (1) at the limit with an open id, carry; (2) the report is newer than the
    plan, update the plan; below the limit only, (3) or (4) re-review the updated plan, by P3 or,
    at the P3 limit, by P6; (5) a plan review newer than the plan lets the walk decide.
    """
    context_dir = Path(context_dir)
    if session is None:
        session = current_session(context_dir)
    state = adversarial_state(context_dir, session)
    if state["report"] is None and state.get("modeless") is not None:
        # R6-N5-2: a shadowed peer never counts, so name the cause, not another round.
        return {"block": "P6", "slug": "adversarial-review", "phase": "planning",
                "command": "/de-adversarial-reviewer plan-gate",
                "detail": SHADOW_MESSAGE.format(name=state["modeless"].name)}
    if state["report"] is None or state["verdict"] != "REQUEST CHANGES":
        return None
    errors = check_adversarial_verdict(context_dir, session, stage="p6")
    if not errors:
        return None

    def row(command: str) -> dict:
        return {"block": "P4", "slug": "plan-update", "phase": "planning", "command": command, "detail": errors[0]}

    if state["cycles"] >= adversarial_cycle_limit():
        return row("/plan-update")
    report_time = state["report"].stat().st_mtime
    plan = _run_plan(context_dir, session)
    plan_time = plan.stat().st_mtime if plan is not None else 0.0
    if report_time >= plan_time:
        return row("/plan-update")
    if plan_review_cycles(context_dir, session=session) >= plan_review_cycle_limit():
        return row("/de-adversarial-reviewer plan-gate")
    review = select_plan_review(context_dir, session)
    if review is None or review[0].stat().st_mtime <= plan_time:
        return row("/plan-review")
    return None


def adversarial_note(context_dir: Path, session: dict | None = None) -> str | None:
    """The P6 gate's note for APPROVE WITH CONDITIONS with open conditions (cd-63 R2-MF-3)."""
    state = adversarial_state(context_dir, session)
    if state.get("verdict") != "APPROVE WITH CONDITIONS" or not state["open_conditions"]:
        return None
    return (f'conditions {", ".join(state["open_conditions"])} open: run /plan-update and add '
            f'"{state["heading"]}" before P9')


def plan_review_cycles(context_dir: Path, session: dict | None = None) -> int:
    """Plan-review cycles of this run (the P3 spec of ``loop_cycles``)."""
    return loop_cycles(context_dir, P3_LOOP, session=session)


def plan_review_step(context_dir: Path, session: dict | None = None) -> tuple[str, str] | None:
    """The open step of the P3/P4 loop after a blocking plan review, or None (gh-104 D5).

    First match wins: no run or no blocking newest review, None; at the limit, the carry
    or the hard stop at P4; no plan, None; an unreadable file, P3 (fail closed); a plan
    newer than the review, P3 (the next cycle), unless its Revision ledger for that review misses an MF-n (P4,
    ADR-0011); else P4. A tie goes to P4 (D7).
    """
    context_dir = Path(context_dir)
    if session is None:
        session = current_session(context_dir)
    run_id = session.get("pipeline_run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    any_verdict = replace(P3_LOOP, blocking=frozenset())  # D4: the newest review of any verdict decides
    chosen = select_newest_verdict(context_dir, any_verdict, session)
    if chosen is None or chosen[1] not in BLOCKING_PLAN_VERDICTS:
        return None
    review, verdict = chosen
    cycles = plan_review_cycles(context_dir, session=session)
    if cycles >= plan_review_cycle_limit():
        errors = check_plan_review_verdict(context_dir, session=session)
        return ("P4", errors[0]) if errors else None
    plan = _run_plan(context_dir, session)
    return None if plan is None else _mtime_step(context_dir, session, plan, review, verdict, cycles)


def _mtime_step(context_dir: Path, session: dict, plan: Path, review: Path, verdict: str,
                cycles: int) -> tuple[str, str]:
    """Below the limit: P3 when the plan is newer than the blocking review, else P4 (gh-104 D5, D7).

    A newer plan whose `## Revision ledger (review <sha>)` for that review lacks a row for an open MF-n is P4 (ADR-0011)."""
    limit = plan_review_cycle_limit()
    try:
        plan_t, review_t = plan.stat().st_mtime, review.stat().st_mtime
    except OSError as exc:
        return "P3", f"plan-review: cannot read {Path(exc.filename or plan).name}: {exc}"
    if plan_t <= review_t:
        return "P4", (f"plan-update: {review.name} verdict is {verdict} (cycle {cycles} of {limit}) and the plan "
                      f"did not change after it. Run /plan-update, then /plan-review")
    ledger = revision_ledger_problems(plan, review)  # ADR-0011: the update answers each MF-n, each cycle
    if ledger:
        return "P4", ledger[0]
    any_state = replace(P3_LOOP, blocking=frozenset(), keep_unread=True)
    newest = select_newest_verdict(context_dir, any_state, session)
    note = (f'; newest review {newest[0].name} has no "Verdict:" line'
            if newest is not None and newest[1] is None and newest[0] != review else "")
    return "P3", (f"plan-review: {plan.name} is newer than {review.name} (verdict {verdict}){note}. "
                  f"Run /plan-review on the updated plan (cycle {cycles + 1} of {limit})")


_MUST_FIX_HEADING = re.compile(r"^(#{1,6})\s*must-fix", re.I)
_ANY_HEADING = re.compile(r"^(#{1,6})\s")
# An item: the id first, or a list, number, heading, table or emphasis marker and at most two words before it (N-22, N-23).
_MARK, _WORDS = r"(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|#{1,6}\s+|\|\s*)", r"(?:\w+\s+){0,2}"
_MUST_FIX_LABEL = re.compile(rf"^\s*(?:{_MARK}[*_`]*{_WORDS}|[*_`]+{_WORDS})?(MF-\d+)\b")
_FENCE = re.compile(r"^\s*(```|~~~)")


def review_must_fix_ids(text: str) -> set[str]:
    """The MF-n ids a review holds open: the items under its `Must-fix` heading, up to the next heading
    of the same or a higher level. An item is a line that starts with its id; an id cited in another
    item's text, or named outside the section, is not open. With no such heading: every id in the file."""
    lines = _outside_fences(text.splitlines())  # a `#` or an id inside a code fence is neither (PR #168 N-21)
    labels: set[str] = set()
    cited: set[str] = set()
    found = False
    for i, line in enumerate(lines):
        m = _MUST_FIX_HEADING.match(line)
        if not m:
            continue
        found = True
        level = len(m.group(1))
        for later in lines[i + 1:]:
            h = _ANY_HEADING.match(later)
            if h and len(h.group(1)) <= level:
                break
            label = _MUST_FIX_LABEL.match(later)
            if label:
                labels.add(label.group(1))
            cited.update(_MUST_FIX_ID.findall(later))
    if not found:
        return set(_MUST_FIX_ID.findall("\n".join(lines)))
    return labels or cited


_LEDGER_HEADING = re.compile(r"^(#{2,3})\s+revision\s+ledger\s*\(\s*review\s+([0-9a-f]{12,64})\s*\)", re.I)
_LEDGER_ROW = re.compile(r"^\s*\|\s*[*_`]*(MF-\d+)\b")


def revision_ledger_ids(text: str, review_sha: str) -> set[str] | None:
    """The MF-n row ids of the plan's `## Revision ledger (review <sha>)` table for the review whose sha256 is
    `review_sha` (ADR-0011). The heading holds the first 12 or more hex characters of that sha, which the peer
    reads from its packet's Input line. One source for both sides, no count (PR #186 R1 MF-2, MF-3).

    None when no ledger names that review: an older review's ledger does not answer a newer one, whatever
    heading follows it. The table ends at the next heading of the same or a higher level. Fenced lines do not
    count. With two ledgers for one review, the last one counts."""
    lines = _outside_fences(text.splitlines())
    found: tuple[int, int] | None = None
    for i, line in enumerate(lines):
        heading = _LEDGER_HEADING.match(line)
        if heading and review_sha.lower().startswith(heading.group(2).lower()):
            found = (i, len(heading.group(1)))
    if found is None:
        return None
    ids: set[str] = set()
    for line in lines[found[0] + 1:]:
        h = _ANY_HEADING.match(line)
        if h and len(h.group(1)) <= found[1]:
            break
        row = _LEDGER_ROW.match(line)
        if row:
            ids.add(row.group(1))
    return ids


def revision_ledger_problems(plan: Path, review: Path) -> list[str]:
    """ADR-0011: the plan's ledger for `review` (named by its sha256) has a row for each open MF-n of it."""
    try:
        review_bytes = review.read_bytes()
        sha = hashlib.sha256(review_bytes).hexdigest()
        open_ids = review_must_fix_ids(review_bytes.decode("utf-8", errors="replace"))
        rows = revision_ledger_ids(plan.read_text(encoding="utf-8", errors="replace"), sha) if open_ids else set()
    except OSError as exc:
        return [f"plan-update: cannot read {Path(exc.filename or plan).name}: {exc}"]
    missing = ", ".join(sorted(open_ids - (rows or set()), key=_id_key))
    if not missing:
        return []
    head = f"## Revision ledger (review {sha[:12]})"
    where = (f"{plan.name} has no `{head}`" if rows is None else f"the `{head}` of {plan.name} has no row")
    return [f"plan-update: {where} for {missing} of {review.name}. Run /plan-update {plan.name} --from-review {review.name}. "
            f"End the update with `{head}` and one row for each MF-n. A `not changed: <reason>` row counts"]


def _outside_fences(lines: list[str]) -> list[str]:
    """The lines that are not inside a ``` or ~~~ code fence."""
    out, fence = [], None
    for line in lines:
        m = _FENCE.match(line)
        if m and fence in (None, m.group(1)):
            fence = None if fence else m.group(1)
        elif fence is None:
            out.append(line)
    return out


def _carried_section(context_dir: Path, session: dict | None = None) -> str:
    """The plan's carried review findings, for the given run (cd-76 D7); None reads today's run."""
    return carried_section(context_dir, CARRIED_HEADER, session=session)


def _carried_ids(context_dir: Path, session: dict | None = None) -> set[str]:
    return set(_MUST_FIX_ID.findall(_carried_section(context_dir, session=session)))


def check_plan_review_verdict(context_dir: Path, session: dict | None = None) -> list[str]:
    """This session's plan review must not carry a blocking verdict.

    The review loop is bounded (`plan_review_cycles`). At the limit a READY WITH
    CHANGES verdict clears when the plan carries each open MF-n into a phase, so a
    test in E1 settles it. NOT READY at the limit is a hard stop.
    """
    chosen = select_plan_review(Path(context_dir), session=session)
    if chosen is None:
        return _no_plan_review_peer(Path(context_dir), session)
    path, verdict = chosen
    if verdict not in BLOCKING_PLAN_VERDICTS:
        return []
    cycles = plan_review_cycles(context_dir, session=session)
    if cycles < plan_review_cycle_limit():
        return [
            f"plan-review: {path.name} verdict is {verdict} (cycle {cycles} of {plan_review_cycle_limit()}); "
            "run /plan-update and re-review before E1"
        ]
    if verdict == "NOT READY":
        return [
            f"plan-review: {path.name} is NOT READY after {cycles} cycles. The plan is wrong: "
            'activate_session.py --hard-stop retries --reason "plan not ready after review limit"'
        ]
    try:
        open_ids = review_must_fix_ids(path.read_text(encoding="utf-8"))
    except OSError:
        open_ids = set()
    if not open_ids:
        # The review gave no MF-n labels: a non-empty carried section is the evidence.
        if _carried_section(context_dir, session=session).strip():
            return []
        missing = []
    else:
        missing = sorted(open_ids - _carried_ids(context_dir, session=session))
    if not open_ids or missing:
        what = ", ".join(missing) if missing else "the review's must-fix items"
        return [
            f"plan-review: review limit reached ({cycles} cycles). Do not run another review. "
            f"Add `{CARRIED_HEADER}` to the plan and map {what} to the phase whose test settles each"
        ]
    return []


def _no_plan_review_peer(context_dir: Path, session: dict | None) -> list[str]:
    """gh-66 PD17: on a verdict_join run, a review in run scope from no plan-review peer blocks P9."""
    resolved = session if session is not None else current_session(context_dir)
    if resolved.get("verdict_join") is not True:
        return []
    scoped = run_scope_accept(context_dir, session=resolved)
    files = sorted((p for p in context_dir.glob(P3_LOOP.glob) if scoped(p)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return []
    lines = stage_rejections(context_dir, files, session=resolved, slug="plan-review")
    reason = lines[0][len("plan-review: "):] if lines else "no file from a plan-review peer is attested"
    return [f"plan-review: no review from a plan-review peer; {reason}"]


def ticket_norm(text: str) -> str:
    """Lowercase with _ as -, the form ticket tokens are matched in."""
    return text.lower().replace("_", "-")


def ticket_token_re(token: str) -> re.Pattern[str]:
    """Match ``token`` as a whole token in normalized text (cd-1 never matches cd-12)."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(ticket_norm(token))}(?![a-z0-9])")


def ticket_scope(
    context_dir: Path, cwd: Path | None = None, session: dict | None = None
) -> list[re.Pattern[str]] | None:
    """Boundary-matched ticket tokens from the session; None when it has none.

    Same naming convention as activate_session.find_matching_artifacts, but a
    token must not run into more alphanumerics, so cd-1 never matches cd-12.
    The worktree's own run comes first (cd-47 D1).
    """
    data = session if session is not None else current_session(Path(context_dir), cwd)
    raw = {str(t) for t in data.get("ticket_tokens") or []}
    if data.get("task_id"):
        raw.add(str(data["task_id"]))
    tokens: set[str] = set()
    for tok in raw:
        tok = ticket_norm(tok)
        if tok.isdigit():
            tokens.update({f"#{tok}", f"gh-{tok}", f"issue-{tok}"})
        elif len(tok) > 2:
            tokens.add(tok)
    if not tokens:
        return None
    return [ticket_token_re(t) for t in sorted(tokens)]


def _in_scope(path: Path, scope: list[re.Pattern[str]]) -> bool:
    name = ticket_norm(path.name)
    if any(p.search(name) for p in scope):
        return True
    try:
        text = ticket_norm(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False
    return any(p.search(text) for p in scope)


def run_scope_accept(
    context_dir: Path, cwd: Path | None = None, session: dict | None = None
) -> Callable[[Path], bool]:
    """A planning artifact counts for this run (cd-47 D8).

    It names this run's id; or it names no run id and is in ticket scope. A file that
    names only other runs does not count, so a plan left by another run cannot clear P1.
    """
    context_dir = Path(context_dir)
    if session is None:
        session = current_session(context_dir, cwd)
    run_id = str(session.get("pipeline_run_id") or "").strip().lower()
    scope = ticket_scope(context_dir, cwd, session=session)

    def accept(path: Path) -> bool:
        try:
            named = run_ids_in_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return False
        if named:
            return bool(run_id) and run_id in named
        return scope is None or _in_scope(path, scope)

    return accept


def select_plan_review(context_dir: Path, session: dict | None = None) -> tuple[Path, str] | None:
    """The review that decides the hold: in ticket scope, with a verdict (the P3 spec).

    A file that names a different pipeline_run_id does not count. A file with no
    verdict does not count. With no ticket tokens, any blocking file holds.
    """
    return select_newest_verdict(context_dir, P3_LOOP, session=session)


_REVIEWED_PLAN = re.compile(r"^\s*Last reviewed plan sha256:\s*([0-9a-fA-F]{12,64})\s*$", re.M)


def plan_changed_after_review(context_dir: Path, session: dict | None = None) -> list[str]:
    """gh-176: the plan must not change after the READY TO EXECUTE review that decides P3.

    The review's `Last reviewed plan sha256:` line names the plan the peer read. With no such line,
    a plan mtime newer than the review mtime fails. A tie passes. A blocking review is not this check."""
    context_dir = Path(context_dir)
    session = session if session is not None else current_session(context_dir)
    chosen = select_plan_review(context_dir, session=session)
    plan = _run_plan(context_dir, session) if chosen is not None and chosen[1] == "READY TO EXECUTE" else None
    if plan is None or chosen is None:
        return []
    review = chosen[0]
    try:
        pinned = _REVIEWED_PLAN.findall(normalize_text_markers(review.read_text(encoding="utf-8")))
        changed = (not hashlib.sha256(plan.read_bytes()).hexdigest().startswith(pinned[-1].lower()) if pinned
                   else plan.stat().st_mtime > review.stat().st_mtime)
    except OSError as exc:
        return [f"plan-review: cannot read {Path(exc.filename or plan).name}: {exc}"]
    if not changed:
        return []
    how = "its sha256 is not the one on the review's `Last reviewed plan sha256:` line" if pinned else "it is newer"
    return [f"plan-review: the plan changed after its review; run /plan-review again "
            f"({plan.name} changed after {review.name}: {how})"]


def check_plan_create_artifacts(
    context_dir: Path,
    worktree: Path,
    scope: list[re.Pattern[str]] | None = None,
    accept: Callable[[Path], bool] | None = None,
) -> list[str]:
    return require_artifact(
        context_dir,
        relative_globs=("plans/*.plan.md",),
        worktree_globs=(Path(worktree) / ".cursor" / "plans" / "*.plan.md",),
        markers=("RUN_BY_SKILL: plan-create", "RUN_BY_COMMAND: plan-create"),
        label="plan-create",
        scope=scope,
        accept=accept,
    )


def _deferral_register_problems(path: Path) -> list[str]:
    """P8 adjudication (references/deferral-framework.md) of one register file."""
    from deferral_adjudication_check import adjudicate_deferral_register

    try:
        result = adjudicate_deferral_register(path.read_text(encoding="utf-8"))
    except OSError as e:
        return [f"unreadable: {e}"]
    return [f"{i.code}: {i.message}" for i in result.issues]


# The register references/deferral-framework.md tells the agent to write; P8 and P9 share it.
DEFERRAL_REGISTER = {
    "relative_globs": ("deferrals/*_deferral-register.md",),
    "label": "deferral-register",
    "validate": _deferral_register_problems,
}

# Required before E1, in report order; plan-review's verdict is checked right after it.
_EXECUTION_ARTIFACTS = (
    {
        "relative_globs": ("reviews/plan_*.md",),
        "markers": ("RUN_BY_COMMAND: plan-review",),
        "label": "plan-review",
    },
    {
        "relative_globs": ("adversarial/adversarial_*.md",),
        "markers": ("RUN_BY_SKILL: de-adversarial-reviewer",),
        "label": "adversarial-review",
        "delegation_stage": "de-adversarial-reviewer",
    },
    {
        "relative_globs": ("benchmarks/*_plan.json",),
        "markers": ("RUN_BY_SKILL: test-benchmark",),
        "label": "test-benchmark-plan",
    },
    DEFERRAL_REGISTER,
)


def check_required_before_execution(
    context_dir: Path,
    *,
    worktree: Path | None = None,
    on: set[str] | None = None,
) -> list[str]:
    """`on`: the slugs of the rows that are on (ADR-0007); None requires every artifact."""
    wt = Path(worktree) if worktree is not None else Path.cwd()
    errors: list[str] = []
    errors.extend(require_active_session(context_dir))
    scope = ticket_scope(context_dir)
    errors.extend(check_plan_create_artifacts(context_dir, wt, scope))
    errors.extend(require_lean_delegation(context_dir, "plan-create"))
    for spec in (s for s in _EXECUTION_ARTIFACTS if on is None or s["label"] in on):
        artifact_spec = {k: v for k, v in spec.items() if k != "delegation_stage"}
        errors.extend(require_artifact(context_dir, **artifact_spec, scope=scope))
        if spec["label"] == "plan-review":
            # cd-76 D7: P9 reads the plan of the run of its own worktree.
            errors.extend(check_plan_review_verdict(context_dir, session=run_for(context_dir, cwd=wt)))
        if spec["label"] == "adversarial-review":
            # cd-63 D4: P9 reads the P6 verdict, with the conditions check.
            errors.extend(check_adversarial_verdict(context_dir, session=run_for(context_dir, cwd=wt), stage="p9"))
        if spec["label"] != "deferral-register":
            errors.extend(
                require_lean_delegation(
                    context_dir, spec.get("delegation_stage", spec["label"])
                )
            )
    return errors
