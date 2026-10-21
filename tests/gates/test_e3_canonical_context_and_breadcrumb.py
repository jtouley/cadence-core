"""E3 uses the canonical .context, writes a per-run breadcrumb, and supersedes its own rows (cd-47 D4).

In a linked worktree, E3 read `<wt>/.context` by default. The plan of the run is in
the primary checkout's `.context`, so E3 raised `core:plan-conformance` and wrote the
row to a ledger that R4 and R5 never read. E3 now resolves `.context` the way activation
does. With no must-fix finding it writes `gates/architecture-review-impl-<run>.json`,
which the PR guard reads (escape (a)). A pass closes this stage's own earlier must-fix rows.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import (clean_env, enable_all_packs, git, head_sha, hook_denied, ledger_row, linked_worktrees, save_worktree_session, shell_call, write_ledger)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from final_consolidate import consolidate  # noqa: E402
from findings_io import append_finding, load_findings  # noqa: E402

RUN = str(uuid.UUID(int=0x1111_2222_3333_4444_5555_6666_7777_8888))
RUN_1 = str(uuid.UUID(int=0x9999_aaaa_bbbb_cccc_dddd_eeee_ffff_0000))
GATE = "python3 $CADENCE_HOME/scripts/architecture_review.py --phase impl --check"
STUB = "---\ndecision_id: D-47\nstatus: proposed\nquestion: q\nchoice: c\nrationale: r\n{links}---\n"
LINKS = "links:\n  - rel: covers\n    target_type: task\n    target_id: cd-47\n"


def _stage_command(slug: str) -> list[str]:
    rows = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))
    rows = rows["stages"] if isinstance(rows, dict) else rows
    command = next(r["command"] for r in rows if r["slug"] == slug)
    assert "--check" not in command
    argv = shlex.split(command)
    return [sys.executable, str(ROOT / argv[1]), *argv[2:]]


def _run(argv: list[str], cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=clean_env(**env), capture_output=True, text=True)


def _e3(wt: Path, *flags: str, **env: str) -> subprocess.CompletedProcess[str]:
    return _run(_stage_command("architecture-review-impl") + list(flags), wt, **env)


class Fixture:
    def __init__(self, root: Path, stub_links_task: bool, name: str = "wt", run: str = RUN, task: str = "cd-47"):
        if (root / "primary").exists():
            self.primary = root / "primary"
            self.wt = root / name
            git(self.primary, "worktree", "add", "-q", "-b", f"feature/{name}", str(self.wt))
        else:
            self.primary, trees = linked_worktrees(root, (name,))
            self.wt = trees[name]
        self.context = self.primary / ".context"
        self.run = run
        (self.wt / ".context").mkdir()
        (self.wt / ".context" / "manifest.json").write_text("{}\n", encoding="utf-8")
        self.plan = self.context / "plans" / f"x_{task}.plan.md"
        self.plan.parent.mkdir(parents=True, exist_ok=True)
        self.plan_text = f"RUN_BY_COMMAND: plan-create\nPipeline-Run: {run}\n# Plan\n### Phase 1: x\n"
        self.plan.write_text(self.plan_text, encoding="utf-8")
        (self.context / "proposals").mkdir(exist_ok=True)
        (self.context / "proposals" / "D-47.md").write_text(STUB.format(links=LINKS if stub_links_task else ""), encoding="utf-8")
        activated = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        save_worktree_session(self.wt, pipeline_run_id=run, task_id=task, ticket_tokens=["47", task],
                              activated_at=activated, workspace=str(self.wt.resolve()),
                              orchestrator_agent_ids=[], orchestrator_source="toplevel")
        enable_all_packs(self.wt)  # ADR-0007: E3 is in the architecture pack
        (self.wt / "work.txt").write_text("w\n", encoding="utf-8")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "feat: phase 1 x")
        for absent in (self.wt / ".context" / "plans", self.wt / ".context" / "plan.md", self.wt / ".cursor" / "plans"):
            assert not absent.exists()

    @property
    def crumb(self) -> Path:
        return self.context / "gates" / f"architecture-review-impl-{self.run}.json"

    @property
    def head(self) -> str:
        return head_sha(self.wt)

    def rows(self) -> list[dict]:
        return load_findings(self.context / "findings.jsonl")


def _guard_allows(cwd: Path, command: str, host: str) -> bool:
    return not hook_denied(shell_call(cwd, command, host))


def test_canonical_plan(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=False)
    proc = _e3(fx.wt)
    assert proc.returncode == 0, proc.stderr
    rows = fx.rows()
    assert not any(r["rule_ref"] == "core:plan-conformance" for r in rows)
    assert [(r["id"], r["rule_ref"]) for r in rows] == [("cd-47-F1", "core:decision-coverage-scope")]
    assert rows[0]["severity"] != "must-fix"
    assert not (fx.wt / ".context" / "findings.jsonl").exists()
    assert not (fx.wt / ".context" / "architecture-review-notice.json").exists()
    fx.plan.unlink()
    _e3(fx.wt)
    assert any(r["rule_ref"] == "core:plan-conformance" and r["severity"] == "must-fix" for r in fx.rows())


def test_linked_worktree(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    proc = _e3(fx.wt, "--check")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(fx.crumb.read_text(encoding="utf-8"))
    assert data["pipeline_run_id"] == RUN and data["head"] == fx.head
    assert not list((fx.wt / ".context").glob("gates/architecture-review-impl*"))
    assert _guard_allows(fx.wt, "gh pr create --draft", "claude")

    import headless

    fx.crumb.unlink()
    row = next(r for r in headless.load_stages(ROOT) if r["slug"] == "architecture-review-impl")
    assert headless.run_gate(row, ROOT, fx.wt, timeout=60).ok
    assert fx.crumb.is_file()


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_e3_command(tmp_path, host):
    fx = Fixture(tmp_path, stub_links_task=True)
    assert _e3(fx.wt).returncode == 0
    assert fx.crumb.is_file()
    assert _guard_allows(fx.wt, "gh pr create --draft", host)


def test_record_mode_with_a_must_fix_finding_deletes_its_breadcrumb(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    _e3(fx.wt)
    assert fx.crumb.is_file()
    fx.plan.unlink()
    assert _e3(fx.wt).returncode == 0
    assert not fx.crumb.exists()
    proc = shell_call(fx.wt, "gh pr create")
    assert f"no E3 breadcrumb for run {RUN}" in proc.stdout


def test_readonly_writes_nothing(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    _e3(fx.wt, CADENCE_GATES_READONLY="1")
    _e3(fx.wt, "--check", CADENCE_GATES_READONLY="1")
    assert not fx.crumb.exists()


def test_no_worktree_session_writes_no_breadcrumb(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    for record in (fx.context / "sessions" / "by-worktree").glob("*.json"):
        record.unlink()
    assert _e3(fx.wt, "--check").returncode == 0
    assert not fx.crumb.exists()


def test_a_failed_check_deletes_only_its_own_breadcrumb(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    _e3(fx.wt)
    other = fx.context / "gates" / f"architecture-review-impl-{RUN_1}.json"
    other.write_text("{}", encoding="utf-8")
    fx.plan.unlink()
    assert _e3(fx.wt, "--check").returncode == 1
    assert not fx.crumb.exists() and other.is_file()


def test_two_active_runs_keep_their_own_breadcrumbs(tmp_path):
    """SF-1, cycle 2: E3 of W1 cannot remove or overwrite the breadcrumb of W0."""
    w0 = Fixture(tmp_path, stub_links_task=True, name="w0", run=RUN)
    w1 = Fixture(tmp_path, stub_links_task=True, name="w1", run=RUN_1, task="cd-48")
    (w1.context / "proposals" / "D-48.md").write_text(STUB.format(links=LINKS.replace("cd-47", "cd-48")), encoding="utf-8")
    _e3(w0.wt)
    _e3(w1.wt)
    assert w0.crumb.is_file() and w1.crumb.is_file()
    assert _guard_allows(w0.wt, "gh pr create", "claude") and _guard_allows(w1.wt, "gh pr create", "claude")
    w0.plan.unlink()  # both plans share the canonical plans/ dir; with none, E3 in W1 has a must-fix
    w1.plan.unlink()
    _e3(w1.wt)
    assert not w1.crumb.exists() and w0.crumb.is_file()
    assert _guard_allows(w0.wt, "gh pr create", "claude")
    assert not _guard_allows(w1.wt, "gh pr create", "claude")


def test_an_explicit_context_keeps_its_meaning(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    other = tmp_path / "other" / ".context"
    (other / "plans").mkdir(parents=True)
    (other / "plans" / "y.plan.md").write_text(fx.plan_text, encoding="utf-8")
    fx.plan.unlink()
    assert _e3(fx.wt, "--context", str(other)).returncode == 0
    assert (other / "findings.jsonl").is_file()
    assert not any(r["rule_ref"] == "core:plan-conformance" for r in load_findings(other / "findings.jsonl"))


def test_check_creates_no_context_directory(tmp_path):
    primary, trees = linked_worktrees(tmp_path, ("wt",))
    _e3(trees["wt"], "--check", CADENCE_GATES_READONLY="1")
    assert not (primary / ".context").exists() and not (trees["wt"] / ".context").exists()


def test_p5_shares_the_ledger_and_leaves_the_breadcrumb(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=False)
    fx.crumb.parent.mkdir(parents=True, exist_ok=True)
    fx.crumb.write_text("{}", encoding="utf-8")
    before = fx.crumb.stat().st_mtime_ns
    assert _run(_stage_command("architecture-review-plan"), fx.wt).returncode == 0
    assert [r["stage"] for r in fx.rows()] == ["architecture-review-plan"]
    assert not (fx.wt / ".context" / "findings.jsonl").exists()
    assert fx.crumb.stat().st_mtime_ns == before


# --- supersede (P6 condition 1) --------------------------------------------------------------------


def test_supersede_closes_this_stages_own_must_fix(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    fx.plan.unlink()
    _e3(fx.wt)
    [row] = [r for r in fx.rows() if r["rule_ref"] == "core:plan-conformance"]
    assert row["status"] == "open" and row["check"] == GATE
    fx.plan.write_text(fx.plan_text, encoding="utf-8")
    assert _e3(fx.wt).returncode == 0
    [row] = [r for r in fx.rows() if r["rule_ref"] == "core:plan-conformance"]
    assert row["status"] == "verified" and row["verified_at"] and row["verification_exit_code"] == 0
    assert row["note"].startswith(f"superseded by E3 pass at {fx.head}")
    closure = _run([sys.executable, str(ROOT / "scripts" / "closure.py"), "--check"], fx.wt)
    assert closure.returncode == 0, closure.stderr
    assert consolidate(fx.context / "findings.jsonl")["close_pass"]["open_must_fix"] == []
    assert fx.crumb.is_file()


def _foreign_rows(fx: Fixture) -> None:
    ledger = fx.context / "findings.jsonl"
    base = {"class": "spec-gap", "severity": "must-fix", "location": "architecture", "evidence": "e",
            "check": "manual", "status": "open"}
    append_finding(ledger, {**base, "id": "cd-32-F1", "stage": "architecture-review-impl", "rule_ref": "core:plan-conformance"})
    append_finding(ledger, {**base, "id": "cd-47-F9", "stage": "architecture-review-plan", "rule_ref": "core:decision-coverage",
                            "check": "python3 $CADENCE_HOME/scripts/architecture_review.py --phase plan --check"})


def _status(fx: Fixture, finding: str) -> str:
    return next(r["status"] for r in fx.rows() if r["id"] == finding)


def test_supersede_scope(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    _foreign_rows(fx)
    assert _e3(fx.wt).returncode == 0
    assert _status(fx, "cd-32-F1") == "open" and _status(fx, "cd-47-F9") == "open"
    assert _run(_stage_command("architecture-review-plan"), fx.wt).returncode == 0
    assert _status(fx, "cd-47-F9") == "verified" and _status(fx, "cd-32-F1") == "open"


def test_supersede_needs_record_mode(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=True)
    fx.plan.unlink()
    _e3(fx.wt)
    [failed] = [r["id"] for r in fx.rows() if r["rule_ref"] == "core:plan-conformance"]
    fx.plan.write_text(fx.plan_text, encoding="utf-8")
    assert _e3(fx.wt, "--check").returncode == 0
    assert _status(fx, failed) == "open"
    _e3(fx.wt, CADENCE_GATES_READONLY="1")
    assert _status(fx, failed) == "open"


def test_no_duplicate_nits(tmp_path):
    fx = Fixture(tmp_path, stub_links_task=False)
    first = json.loads(_e3(fx.wt).stdout)
    second = json.loads(_e3(fx.wt).stdout)
    assert [r["rule_ref"] for r in fx.rows()] == ["core:decision-coverage-scope"]
    assert first["findings"] == second["findings"]


def test_check_and_record_report_the_same_findings(tmp_path):
    """With an empty layer_map, --check emitted two "layer_map present" nits that record mode did not."""
    fx = Fixture(tmp_path, stub_links_task=True)
    checked = json.loads(_e3(fx.wt, "--check").stdout)
    recorded = json.loads(_e3(fx.wt).stdout)
    assert checked["findings"] == recorded["findings"] == 0


# --- R2 on PR #52: E3 survivors (M13-M16), in-process ----------------------------------------------

import architecture_review as ar  # noqa: E402


def _review(phase: str, worktree: Path, context: Path, **kw) -> list[dict]:
    return ar.run_architecture_review(phase=phase, worktree=worktree, context_dir=context, skill_root=ROOT, **kw)


E3_DEFAULTS = {"stage": "architecture-review-impl", "rule_ref": "core:plan-conformance", "class": "spec-gap",
               "location": "architecture", "evidence": "e", "check": GATE}


def _plain_repo(tmp_path: Path, linked: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    context = repo / ".context"
    (context / "proposals").mkdir(parents=True)
    (context / "proposals" / "D-47.md").write_text(STUB.format(links=LINKS if linked else ""), encoding="utf-8")
    return repo, context


def test_the_skip_notice_names_the_stage_and_the_skipped_rules(tmp_path):
    """M13."""
    repo, context = _plain_repo(tmp_path)
    _review("plan", repo, context, task_id="cd-47")
    notice = json.loads((context / "architecture-review-notice.json").read_text(encoding="utf-8"))
    assert notice == {"stage": "architecture-review-plan",
                      "skipped_rules": ["core:dependency-direction", "core:boundary-integrity"],
                      "reason": "layer_map is empty in the config"}


def test_a_repeat_is_skipped_only_for_an_open_non_blocking_row_of_this_stage(tmp_path):
    """M14: same stage, rule and evidence; never a must-fix; never another stage's row."""
    repo, context = _plain_repo(tmp_path, linked=False)
    (context / "plans").mkdir()
    (context / "plans" / "x.plan.md").write_text("plan\n", encoding="utf-8")
    _review("plan", repo, context, task_id="cd-47")
    _review("impl", repo, context, task_id="cd-47")  # same rule and evidence, other stage: a new row
    _review("plan", repo, context, task_id="cd-47")  # repeat of the open plan nit: no new row
    stages = sorted(r["stage"] for r in load_findings(context / "findings.jsonl"))
    assert stages == ["architecture-review-impl", "architecture-review-plan"]
    (context / "proposals" / "D-47.md").unlink()  # decision coverage is now a must-fix
    _review("plan", repo, context, task_id="cd-47")
    _review("plan", repo, context, task_id="cd-47")
    musts = [r for r in load_findings(context / "findings.jsonl") if r["severity"] == "must-fix"]
    assert len(musts) == 2


def test_supersede_closes_fixed_rows_and_skips_manual_ones(tmp_path):
    """M15."""
    repo, context = _plain_repo(tmp_path)
    (context / "plans").mkdir()
    (context / "plans" / "x.plan.md").write_text("plan\n", encoding="utf-8")
    write_ledger(context / "findings.jsonl", ledger_row("cd-47-F1", **{**E3_DEFAULTS, "status": "fixed", "fixed_in": "0" * 40}))
    write_ledger(context / "findings.jsonl", ledger_row("cd-47-F2", **{**E3_DEFAULTS, "check": "manual"}))
    _review("impl", repo, context, task_id="cd-47")
    status = {r["id"]: r["status"] for r in load_findings(context / "findings.jsonl")}
    assert status == {"cd-47-F1": "verified", "cd-47-F2": "open"}


def test_supersede_leaves_an_unchanged_ledger_unwritten(tmp_path):
    """M15: nothing to close, no rewrite."""
    repo, context = _plain_repo(tmp_path)
    (context / "plans").mkdir()
    (context / "plans" / "x.plan.md").write_text("plan\n", encoding="utf-8")
    write_ledger(context / "findings.jsonl", ledger_row("cd-32-F1", **E3_DEFAULTS))
    ledger = context / "findings.jsonl"
    os.utime(ledger, (1_000_000_000, 1_000_000_000))
    _review("impl", repo, context, task_id="cd-47")
    assert ledger.stat().st_mtime == 1_000_000_000


def test_a_p5_pass_names_p5_in_the_supersede_note(tmp_path):
    """M16."""
    repo, context = _plain_repo(tmp_path)
    write_ledger(context / "findings.jsonl", ledger_row("cd-47-F1", **{
        **E3_DEFAULTS, "stage": "architecture-review-plan", "rule_ref": "core:decision-coverage",
        "check": "python3 $CADENCE_HOME/scripts/architecture_review.py --phase plan --check"}))
    _review("plan", repo, context, task_id="cd-47")
    [row] = load_findings(context / "findings.jsonl")
    assert row["note"].startswith("superseded by P5 pass at ")


def test_update_breadcrumb_honors_readonly_and_a_missing_file(tmp_path, monkeypatch):
    """M16: the env check, and a must-fix with no breadcrumb to delete."""
    fx = Fixture(tmp_path, stub_links_task=True)
    must = [{"severity": "must-fix"}]
    ar.update_breadcrumb(fx.context, fx.wt, must)  # no file yet: no error
    monkeypatch.setenv("CADENCE_GATES_READONLY", "1")
    ar.update_breadcrumb(fx.context, fx.wt, [])
    assert not fx.crumb.exists()
    monkeypatch.delenv("CADENCE_GATES_READONLY")
    ar.update_breadcrumb(fx.context, fx.wt, [])
    assert fx.crumb.exists()
    monkeypatch.setenv("CADENCE_GATES_READONLY", "1")
    ar.update_breadcrumb(fx.context, fx.wt, must)
    assert fx.crumb.exists()


def test_with_no_session_the_ids_use_the_cadence_prefix(tmp_path):
    """M16: the default task id."""
    repo, context = _plain_repo(tmp_path, linked=False)
    assert _run([sys.executable, str(ROOT / "scripts" / "architecture_review.py"), "--phase", "plan"], repo).returncode == 0
    assert [r["id"] for r in load_findings(context / "findings.jsonl")] == ["CADENCE-F1"]
