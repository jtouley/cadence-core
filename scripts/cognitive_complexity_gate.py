#!/usr/bin/env python3
"""R3 — cognitive complexity, duplication and code shape of the change (gh-100).

The gate scores each function in the files that changed since the merge base
with the run's base branch (committed, uncommitted and untracked). A function
fails when its score is above `cognitive_complexity.max` and it is new or its
score went up since the merge base. A function that was already over the limit
and did not get worse does not block the change: the debt cannot grow.

Same-named functions in one file (Rust `new` in two `impl` blocks, a property
getter and setter, TS `<arrow>`) are compared as sorted lists of scores: the
highest new score pairs with the highest base score of that name, and so on.

The base is `--base <ref>`, else the env var CADENCE_BASE_REF, else the run's base chain
(`resolve_base`). A given base that does not resolve fails the gate; it never falls back. On the
base branch itself the merge base is HEAD, so only uncommitted changes are judged. CADENCE_BASE_REF
also applies to the local R3 stage and the stop guard, because both call `run()`.

The same ratchet holds for two code-shape measures of a Python function:
its statement count (`code_shape.max_function_statements`) and its longest branch chain
(`code_shape.max_branch_chain`, the open/closed proxy). For duplication the unit is
the file: a changed Python file fails when its duplicated share (duplication.py, over
the changed Python files) is above `duplication.max_percent` and it is new or grew
since the merge base.

A changed code file with no analyzer, or one where the C-family scanner lost the
brace structure, is listed as unmeasured with its reason. With
`cognitive_complexity.unsupported` = "fail" it fails the gate. A changed code file
that is not Python gets no duplication or code-shape measure; `duplication.unsupported`
and `code_shape.unsupported` say if that fails. A changed Python file that does not
parse, and a git error, fail the gate (fail closed).

Two more measures count the findings of pinned tools on the added lines of the changed Python
files: `bugs` (ruff) and `types` (mypy), with a limit of 0 (tool_measures.py, docs/tool-gates.md).
A tool that is missing or times out follows `bugs.unsupported` and `types.unsupported`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cognitive_complexity as cc  # noqa: E402
import duplication as dup  # noqa: E402
import tool_measures as tm  # noqa: E402
from cognitive_complexity import DEFAULTS, load_config  # noqa: E402,F401  (gate.load_config, gate.DEFAULTS)
from gate_common import ctx, fail, measure_record, ok, write_gate_breadcrumb  # noqa: E402
from impl_artifact import GitError, MergeBaseError, changed_files, merge_base  # noqa: E402
from implement_gate import run_worktree  # noqa: E402
from pack_loader import gate_errors, phase_packs  # noqa: E402
from phase_ledger import resolve_base  # noqa: E402
from pipeline_run_binding import run_for  # noqa: E402

SLUG = "cognitive-complexity"
MAX_LISTED = 5
BASE_ENV = "CADENCE_BASE_REF"
R3_SECTIONS = ("cognitive_complexity", "duplication", "code_shape", "bugs", "types")
# Per function: measure -> (FunctionScore field, config section, config key, finding text after "path:line name").
METRICS = {
    "complexity": ("score", "cognitive_complexity", "max", "= {score} ({was})"),
    "statements": ("statements", "code_shape", "max_function_statements", "has {score} statements ({was}): split it"),
    "branch_chain": ("chain", "code_shape", "max_branch_chain", "has a branch chain of {score} ({was}): a new "
                     "branch on a long dispatch chain belongs in an extension point (a table, a registry or a pack)"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    name: str
    line: int
    score: int  # the value of `metric`
    base: int | None  # None: the function is new
    same_named: int = 1  # functions of this name in the file; above 1 they pair by score rank
    metric: str = "complexity"

    def text(self) -> str:
        was = "new" if self.base is None else f"was {self.base}"
        if self.same_named > 1:
            was += f"; {self.same_named} functions named {self.name}, paired by score"
        return f"{self.path}:{self.line} {self.name} " + METRICS[self.metric][3].format(score=self.score, was=was)


@dataclass(frozen=True)
class Copied:
    """A changed file over duplication.max_percent that is new, or has more duplicated lines and a larger share."""
    path: str
    percent: float
    base: float | None  # None: the file is new
    lines: int
    total: int
    blocks: list[dict]

    def text(self) -> str:
        was = "new" if self.base is None else f"was {self.base:.1f}%"
        first = f"; {self.blocks[0]['a']} = {self.blocks[0]['b']}" if self.blocks else ""
        return f"{self.path}: {self.percent:.1f}% duplicated, {self.lines} of {self.total} code lines ({was}){first}"


@dataclass
class Report:
    findings: list[Finding]
    unmeasured: list[str]  # "path: reason"
    unparsable: list[str]
    functions: int
    files: int
    duplicated: list[Copied] = field(default_factory=list)
    python_only: list[str] = field(default_factory=list)  # code files with no duplication or code-shape measure
    python_files: int = 0
    highest: dict[str, float] = field(default_factory=dict)  # per measure, the highest value in the change
    tools: list[dict] = field(default_factory=list)  # the `bugs` and `types` records (tool_measures.py)
    packs: list[str] = field(default_factory=list)  # gh-177: the errors of the review gates of the enabled packs


def _base_source(worktree: Path, fork: str | None, rel: str) -> str | None:
    if fork is None:
        return None  # --path: every file is new
    result = subprocess.run(["git", "show", f"{fork}:{rel}"], cwd=worktree, capture_output=True, text=True,
                            timeout=30, errors="replace")
    return result.stdout if result.returncode == 0 else None


def _by_name(scores: list[cc.FunctionScore], metric: str) -> dict[str, list[cc.FunctionScore]]:
    out: dict[str, list[cc.FunctionScore]] = {}
    for fn in scores:
        if getattr(fn, metric) is not None:
            out.setdefault(fn.name, []).append(fn)
    return {name: sorted(group, key=lambda f: (-getattr(f, metric), f.line)) for name, group in out.items()}


def _base_scores(rel: str, source: str | None) -> list[cc.FunctionScore] | None:
    """Scores at the merge base; [] for a new file.

    None when the base version exists but cannot be measured: then old and new debt cannot be told apart.
    """
    if source is None:
        return []
    try:
        return cc.analyze(rel, source)
    except (SyntaxError, cc.LostStructure, RecursionError):
        return None


def _worse(scores: list[cc.FunctionScore], base: list[cc.FunctionScore], rel: str, cfg: dict) -> list[Finding]:
    """Functions over a limit that are new or got worse, pairing same-named functions by rank (PR #101 MF-1)."""
    findings = []
    for metric, (attr, section, key, _text) in METRICS.items():
        was = {name: [getattr(f, attr) for f in group] for name, group in _by_name(base, attr).items()}
        for name, group in _by_name(scores, attr).items():
            for rank, fn in enumerate(group):
                value, before = getattr(fn, attr), (was.get(name, [])[rank:] or [None])[0]
                if value > cfg[section][key] and (before is None or value > before):
                    findings.append(Finding(rel, name, fn.line, value, before, len(group), metric))
    return findings


def _measure_file(rel: str, source: str, report: Report) -> list[cc.FunctionScore] | None:
    """The file's scores, or None after recording why it was not measured."""
    if cc.analyzer_for(rel) is None:
        report.unmeasured.append(f"{rel}: no analyzer")
        return None
    try:
        return cc.analyze(rel, source)
    except cc.LostStructure as exc:
        report.unmeasured.append(f"{rel}: scanner lost the structure ({exc})")
    except (SyntaxError, RecursionError):
        report.unparsable.append(rel)
    return None


def _judge_duplication(head: dict[str, str], base: dict[str, str], cfg: dict, report: Report) -> None:
    """Changed Python files whose duplicated share is over the limit and that are new or worse.

    Worse: more duplicated lines and a larger share than at the merge base. A file that only lost
    other lines has the same lines and a larger share, so it is not worse. A block that grew by
    one line because a neighbour changed has more lines and a smaller share, so it is not worse.
    """
    size = cfg["min_block_lines"]
    now, before = dup.measure(head, size), dup.measure(base, size)
    for rel, file in now.items():
        was = before.get(rel)
        report.highest["duplication"] = max(report.highest.get("duplication", 0.0), round(file.percent, 1))
        if file.percent > cfg["max_percent"] and (
                was is None or (file.duplicated_lines > was.duplicated_lines and file.percent > was.percent)):
            report.duplicated.append(Copied(rel, round(file.percent, 1), None if was is None else round(was.percent, 1),
                                            file.duplicated_lines, file.total_lines, file.blocks[:3]))


def _judge_file(worktree: Path, fork: str | None, rel: str, cfg: dict, report: Report) -> tuple[str, str | None] | None:
    """Judge one changed code file per function. For a measured Python file, its text now and at the base."""
    text, base_text = (worktree / rel).read_text(encoding="utf-8", errors="replace"), _base_source(worktree, fork, rel)
    scores = _measure_file(rel, text, report)
    base = None if scores is None else _base_scores(rel, base_text)
    if scores is not None and base is None:
        report.unmeasured.append(f"{rel}: the merge-base version cannot be measured, so old and new debt look alike")
    if scores is None or base is None:
        return None
    report.files += 1
    report.functions += len(scores)
    for metric, (attr, *_rest) in METRICS.items():
        values = [getattr(f, attr) for f in scores if getattr(f, attr) is not None]
        report.highest[metric] = max([report.highest.get(metric, 0), *values])
    report.findings += _worse(scores, base, rel, cfg)
    if rel.endswith(".py"):
        return text, base_text
    report.python_only.append(rel)
    return None


def measure_change(worktree: Path, fork: str | None, files: list[str], cfg: dict) -> Report:
    """Judge the changed files against the merge base `fork`; with no fork, every file is new."""
    report = Report([], [], [], 0, 0)
    head: dict[str, str] = {}
    base: dict[str, str] = {}
    code = [rel for rel in files if cc.is_code(rel) and not (worktree / rel).is_symlink() and (worktree / rel).is_file()]
    for rel in code:  # git stores a symlink as its target text, not as code
        texts = _judge_file(worktree, fork, rel, cfg, report)
        if texts is not None:
            head[rel] = texts[0]
            if texts[1] is not None:
                base[rel] = texts[1]
    report.python_files = len(head)
    _judge_duplication(head, base, cfg["duplication"], report)
    report.findings.sort(key=lambda f: (list(METRICS).index(f.metric), -f.score))
    report.tools = tm.measure(worktree, fork, code, cfg)
    return report


def records(report: Report, cfg: dict) -> list[dict]:
    """One record per measure, with its verdict. The exit code follows the verdicts."""
    out = []
    for metric, (_attr, section, key, _text) in METRICS.items():
        mine = [f for f in report.findings if f.metric == metric]
        out.append(measure_record(metric, max([f.score for f in mine], default=report.highest.get(metric, 0)),
                                  cfg[section][key], [f.text() for f in mine], failed=bool(mine),
                                  threshold_source=cfg[section]["threshold_source"][key],
                                  unmeasured=report.unmeasured if metric == "complexity" else report.python_only,
                                  unsupported=cfg[section]["unsupported"]))
    copies = report.duplicated
    out.append(measure_record("duplication", max([c.percent for c in copies], default=report.highest.get("duplication", 0.0)),
                              cfg["duplication"]["max_percent"], [c.text() for c in copies], failed=bool(copies),
                              threshold_source=cfg["duplication"]["threshold_source"]["max_percent"],
                              unmeasured=report.python_only, unsupported=cfg["duplication"]["unsupported"]))
    return out + report.tools


def _capped(items: list[str], what: str) -> list[str]:
    more = len(items) - MAX_LISTED
    return items[:MAX_LISTED] + ([f"and {more} more {what} in .context/gates/{SLUG}.json"] if more > 0 else [])


def _not_measured(report: Report, cfg: dict, failing: bool) -> list[str]:
    lines = []
    if report.unmeasured and (cfg["cognitive_complexity"]["unsupported"] == "fail" or failing):
        lines.append(f"{len(report.unmeasured)} changed code file(s) not measured: {'; '.join(report.unmeasured[:3])}")
    if report.python_only and any(cfg[s]["unsupported"] == "fail" for s in ("duplication", "code_shape")):
        lines.append(f"{len(report.python_only)} changed code file(s) measured for complexity only "
                     f"(duplication and code shape are Python only): {'; '.join(report.python_only[:3])}")
    return lines


def _problems(report: Report, cfg: dict) -> list[str]:
    lines = report.packs + _capped([f"cannot parse {rel}" for rel in report.unparsable], "unparsable files")
    lines += [f.text() for f in report.findings[:MAX_LISTED]]
    if len(report.findings) > MAX_LISTED:
        lines.append(f"and {len(report.findings) - MAX_LISTED} more in .context/gates/{SLUG}.json")
    lines += _capped([c.text() for c in report.duplicated], "duplicated files")
    lines += _not_measured(report, cfg, bool(lines))
    hints = {"complexity": f"limit is {cfg['cognitive_complexity']['max']}: split each function, or keep it at its "
                      "merge-base score",
             "statements": f"code_shape.max_function_statements is {cfg['code_shape']['max_function_statements']}",
             "branch_chain": f"code_shape.max_branch_chain is {cfg['code_shape']['max_branch_chain']}"}
    lines += [hint for metric, hint in hints.items() if any(f.metric == metric for f in report.findings)]
    if report.duplicated:
        lines.append(f"duplication.max_percent is {cfg['duplication']['max_percent']}: reuse the code that exists, "
                     "or move the copy into one shared helper")
    for rec in (r for r in report.tools if r["verdict"] == "fail"):
        lines += _capped([f"{rec['measure']}: {item}" for item in rec["offenders"] or rec["unmeasured"]],
                         f"{rec['measure']} findings")
        if rec["offenders"]:
            lines.append(f"{rec['measure']}.max is {rec['threshold']}: fix each {rec['tool']} finding on an added "
                         "line" + (" (a `# noqa` must name its rule)" if rec["tool"] == "ruff" else ""))
    return lines


def _fix_step(report: Report, check: bool) -> list[str]:
    """The named fix step of a failed R3 (gh-183), when the record lists offenders that a change of code removes."""
    if not (report.findings or report.duplicated or any(r["verdict"] == "fail" and r["offenders"] for r in report.tools)):
        return []
    write = "run R3 without --check to write the record, then " if check else ""
    return [f"fix step: {write}delegate $CADENCE_HOME/subagents/simplify-code.md with .context/gates/{SLUG}.json as "
            "its one input, then run R3 again (one cycle, then the review loop rule applies)"]


def _resolves(worktree: Path, ref: str) -> bool:
    result = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=worktree,
                            capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def _choose_base(context_dir: Path, worktree: Path, given: str | None) -> tuple[str | None, str | None]:
    """(base, None), or (None, the reason). A given base is used as given, or the gate fails closed."""
    if given is None:  # `--base ""` is given (it fails closed); an empty env var is not set
        given = os.environ.get(BASE_ENV) or None
    if given is None:
        found = resolve_base(worktree, run_for(context_dir) or {})
        return (found, None) if found else (None, "no base ref found (origin/HEAD, origin/main, ...); fetch the base branch")
    return (given, None) if _resolves(worktree, given) else (None, f"base ref {given} does not resolve")


def _config(workspace: Path | None = None) -> dict:
    return {section: load_config(_SCRIPTS.parent, section, workspace) for section in R3_SECTIONS}


def change_under_review(context_dir: Path, given: str | None = None) -> tuple[Path, str, str, list[str], list[str]] | str:
    """(worktree, base, merge base, changed files outside `measure_exclude`, the changed files under it),
    or why the change cannot be judged. The excluded files are named, never dropped in silence.

    R2 (test_shape.py) reads the change the same way.
    """
    worktree = run_worktree(context_dir)
    base, problem = _choose_base(context_dir, worktree, given)
    if base is None:
        return problem or "no base ref"
    try:
        exclude = cc.load_exclude(_SCRIPTS.parent, worktree)
        fork = merge_base(worktree, base)
        changed = changed_files(worktree, fork)
        excluded = [rel for rel in changed if rel.startswith(exclude)]
        return worktree, base, fork, [rel for rel in changed if rel not in excluded], excluded
    except ValueError as exc:
        return f"bad config: {exc}"
    except (MergeBaseError, GitError) as exc:
        return str(exc)


def _report(context_dir: Path, given: str | None = None) -> tuple[dict, str, Report, list[str]] | str:
    """(config, base, report, excluded changed files), or the reason the gate cannot judge the change."""
    change = change_under_review(context_dir, given)
    if isinstance(change, str):
        return change
    worktree, base, fork, files, excluded = change
    try:
        cfg = _config(worktree)
    except ValueError as exc:
        return f"bad config: {exc}"
    try:
        report = measure_change(worktree, fork, files, cfg)
    except GitError as exc:
        return str(exc)
    # gh-177: the review gate of each enabled pack that lists `review` judges every changed file.
    packs, report.packs = phase_packs(_SCRIPTS.parent, "review", worktree)
    report.packs += gate_errors(packs, "review", worktree, sorted([*files, *excluded]))
    return cfg, base, report, excluded


def measure_path(root: Path) -> tuple[list[dict], list[str]]:
    """Every code file under `root` judged as new: (the records, the unparsable files). For a corpus, not a diff."""
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                   if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts))
    cfg = _config(root)
    report = measure_change(root, None, files, cfg)
    return records(report, cfg), report.unparsable


def run(context_dir: Path, *, check: bool = False, base: str | None = None) -> int:
    judged = _report(context_dir, base)
    if isinstance(judged, str):
        return fail(SLUG, [judged])
    cfg, base, report, excluded = judged
    if not check:
        write_gate_breadcrumb(SLUG, {"base": base, "max": cfg["cognitive_complexity"]["max"], **cfg, **asdict(report),
                                     "measures": records(report, cfg), "excluded": excluded}, context_dir=context_dir)
    problems = _problems(report, cfg)
    if problems:
        return fail(SLUG, problems + _fix_step(report, check))
    note = (f"{report.functions} functions in {report.files} changed files, limit {cfg['cognitive_complexity']['max']}; "
            f"duplication and code shape in {report.python_files} Python files")
    if report.unmeasured:
        note += f"; {len(report.unmeasured)} unmeasured: {'; '.join(report.unmeasured[:3])}"
    if excluded:
        note += f"; {len(excluded)} changed file(s) under measure_exclude not measured: {'; '.join(excluded[:3])}"
    if report.python_only:
        note += (f"; {len(report.python_only)} changed code file(s) measured for complexity only "
                 f"(duplication and code shape are Python only): {'; '.join(report.python_only[:3])}")
    for rec in report.tools:
        note += f"; {rec['measure']} ({rec['tool']} {rec['version']}): " + (
            rec["unsupported"] or f"{rec['value']} findings on added lines of {rec['files']} Python files")
    return ok(SLUG, note)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R3 cognitive complexity, duplication and code shape gate")
    parser.add_argument("--check", action="store_true", help="judge only; write no breadcrumb")
    parser.add_argument("--base", default=None,
                        help=f"the base ref to judge against (default: ${BASE_ENV}, else the run's base chain). "
                             "On the base branch itself, only uncommitted changes are judged.")
    parser.add_argument("--path", default=None, help="judge every code file under this directory as new; print the "
                                                     "measure records as JSON (no git, no breadcrumb)")
    args = parser.parse_args(argv)
    if args.path is None:
        return run(ctx(), check=args.check, base=args.base)
    measures, unparsable = measure_path(Path(args.path))
    print(json.dumps({"path": args.path, "measures": measures, "unparsable": unparsable}, indent=2))
    return 1 if unparsable or any(m["verdict"] == "fail" for m in measures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
