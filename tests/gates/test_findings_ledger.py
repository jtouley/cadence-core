"""The findings ledger: findings_io validates and appends rows, set-status changes them, and only ledger_verify writes verified."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from findings_io import (FindingsValidationError, append_finding, load_findings, set_status,  # noqa: E402
                         validate_finding)
from ledger_verify import main, supersede_finding, verify_finding  # noqa: E402


# --- findings_io_cli_never_writes_verified: findings_io CLI: the sanctioned way to change the ledger; verified stays ledger_verify's alone.

ROW = {"id": "gh-1-F01", "stage": "pr-review-local", "rule_ref": "core:x", "class": "bug",
       "severity": "must-fix", "location": "src/a.py:1", "evidence": "off by one",
       "status": "open", "check": "python3 check.py 0"}


@pytest.fixture
def ledger(tmp_path, scripts_path):
    return tmp_path / "findings.jsonl"


@pytest.fixture
def fix_repo(tmp_path, monkeypatch):
    """A git repo with one commit as the cwd: `fixed_in` names a commit on its branch."""
    from conftest import commit_file, git_repo

    path = git_repo(tmp_path / "repo")
    commit_file(path, "base")
    (path / "check.py").write_text("import sys\nraise SystemExit(int(sys.argv[1]))\n", encoding="utf-8")
    monkeypatch.chdir(path)
    return path


def _cli(*args):
    from findings_io import main

    return main(list(args))


def test_append_then_fix(ledger, fix_repo):
    from conftest import head_sha

    assert _cli("--ledger", str(ledger), "append", "--json", json.dumps(ROW)) == 0
    assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "fixed",
                "--fixed-in", head_sha(fix_repo, short=7)) == 0
    row = json.loads(ledger.read_text())
    assert (row["status"], row["fixed_in"]) == ("fixed", head_sha(fix_repo))


def test_cannot_set_or_append_verified(ledger):
    assert _cli("--ledger", str(ledger), "append", "--json", json.dumps({**ROW, "status": "verified"})) == 1
    _cli("--ledger", str(ledger), "append", "--json", json.dumps(ROW))
    assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "verified") == 1
    assert json.loads(ledger.read_text())["status"] == "open"


def test_deferral_needs_a_ticket(ledger):
    _cli("--ledger", str(ledger), "append", "--json", json.dumps(ROW))
    assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "deferred") == 1
    assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "deferred",
                "--ticket", "#42") == 0
    assert json.loads(ledger.read_text())["ticket"] == "#42"


def test_verified_rows_are_frozen(ledger, fix_repo):
    from conftest import head_sha
    from ledger_verify import verify_finding

    ledger.write_text(json.dumps(verify_finding({**ROW, "status": "fixed", "fixed_in": head_sha(fix_repo)})) + "\n")
    assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "open") == 1
    assert json.loads(ledger.read_text())["status"] == "verified"


def test_append_rejects_duplicate_id(ledger):
    # cd-10-F8: a second row with the same id made set-status unusable.
    assert _cli("--ledger", str(ledger), "append", "--json", json.dumps(ROW)) == 0
    assert _cli("--ledger", str(ledger), "append", "--json", json.dumps(ROW)) == 1
    assert len(ledger.read_text().splitlines()) == 1


def _canonical_rows(context):
    from findings_io import load_findings

    return [r["id"] for r in load_findings(context / "findings.jsonl")]


def test_append_rejects_an_id_of_another_task(scripts_path, tmp_path, monkeypatch):
    """In a run's worktree, the canonical ledger takes only ids of the run's task."""
    from conftest import ledger_row, linked_worktrees, save_worktree_session

    primary, trees = linked_worktrees(tmp_path, ("w0",))
    save_worktree_session(trees["w0"], task_id="cd-49")
    monkeypatch.chdir(trees["w0"])
    assert _cli("append", "--json", json.dumps(ledger_row("gh-49-F1"))) == 1
    assert _canonical_rows(primary / ".context") == []
    assert _cli("append", "--json", json.dumps(ledger_row("cd-49-F1"))) == 0
    other = tmp_path / "x.jsonl"
    assert _cli("--ledger", str(other), "append", "--json", json.dumps(ledger_row("gh-49-F1"))) == 0


def test_append_from_a_runless_cwd_rejects_an_unknown_prefix(scripts_path, two_worktree_runs, monkeypatch):
    """With no run for the cwd, the id must be of a known task."""
    from conftest import ledger_row
    from context_root import context_dir

    env = two_worktree_runs
    monkeypatch.chdir(env.primary)
    assert context_dir().resolve() == env.ctx.resolve()
    assert _cli("append", "--json", json.dumps(ledger_row("gh-49-F1"))) == 1
    assert _canonical_rows(env.ctx) == []
    assert _cli("append", "--json", json.dumps(ledger_row("cd-901-F1"))) == 0


@pytest.mark.parametrize("spelling", ["relative", "symlink"])
def test_append_checks_the_canonical_ledger_by_resolved_path(scripts_path, tmp_path, monkeypatch, spelling):
    """P7 cd-49-F02: another spelling of the canonical ledger is still checked."""
    import os

    from conftest import ledger_row, linked_worktrees, save_worktree_session

    primary, trees = linked_worktrees(tmp_path, ("w0",))
    save_worktree_session(trees["w0"], task_id="cd-49")
    monkeypatch.chdir(trees["w0"])
    canonical = primary / ".context" / "findings.jsonl"
    canonical.touch()
    path = Path(os.path.relpath(canonical, trees["w0"]))
    if spelling == "symlink":
        path = tmp_path / "link.jsonl"
        path.symlink_to(canonical)
    assert _cli("--ledger", str(path), "append", "--json", json.dumps(ledger_row("gh-49-F1"))) == 1
    assert _canonical_rows(primary / ".context") == []


def test_append_validates_the_record_before_the_owner_check(scripts_path, tmp_path, monkeypatch, capsys):
    """R1 N-5: a record with no id fails as invalid, not as an id of another task."""
    from conftest import ledger_row, linked_worktrees, save_worktree_session

    _primary, trees = linked_worktrees(tmp_path, ("w0",))
    save_worktree_session(trees["w0"], task_id="cd-49")
    monkeypatch.chdir(trees["w0"])
    record = {k: v for k, v in ledger_row("cd-49-F1").items() if k != "id"}
    assert _cli("append", "--json", json.dumps(record)) == 1
    assert "missing required fields" in capsys.readouterr().err


# --- findings_io_rejects_invalid_and_verified_status: Phase 4: findings_io validates schema; forbids status=verified writes.

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "finding_open_must_fix.json").read_text()
)


def test_findings_io_rejects_invalid_finding():
    bad = dict(FIXTURE)
    del bad["evidence"]
    with pytest.raises(FindingsValidationError, match="missing required"):
        validate_finding(bad)


def test_findings_io_rejects_verified_status_on_append(tmp_path):
    row = dict(FIXTURE)
    row["status"] = "verified"
    with pytest.raises(FindingsValidationError, match="ledger_verify"):
        append_finding(tmp_path / "f.jsonl", row)


def test_findings_io_appends_open_finding(tmp_path):
    path = tmp_path / "f.jsonl"
    append_finding(path, dict(FIXTURE))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "open"


# --- findings_set_status_on_duplicate_rows: `set-status` can close a row that the ledger holds twice, byte for byte.
#
# Two early P5 runs appended the same `CADENCE-F1` row. `set-status` refused any id
# with more than one match, so nobody could close the row and R5 failed for every
# later run. Identical rows are one finding: the command now changes all of them.
# Different rows with one id are still ambiguous and still refused.

DUP_ROW = {"id": "CADENCE-F1", "stage": "architecture-review-plan", "rule_ref": "core:decision-coverage", "class": "spec-gap",
       "severity": "must-fix", "location": "architecture", "evidence": "e", "check": "manual", "status": "open"}


def test_identical_duplicate_rows_change_together(tmp_path):
    ledger = tmp_path / "findings.jsonl"
    append_finding(ledger, DUP_ROW)
    append_finding(ledger, DUP_ROW)
    set_status(ledger, "CADENCE-F1", "deferred", ticket="#49", note="row of another task")
    assert [(r["status"], r["ticket"]) for r in load_findings(ledger)] == [("deferred", "#49")] * 2


def test_different_rows_with_one_id_are_still_refused(tmp_path):
    ledger = tmp_path / "findings.jsonl"
    append_finding(ledger, DUP_ROW)
    append_finding(ledger, {**DUP_ROW, "evidence": "other"})
    with pytest.raises(FindingsValidationError, match="2 matching findings"):
        set_status(ledger, "CADENCE-F1", "deferred", ticket="#49")


# --- ledger_verify_promotes_fixed_to_verified: Phase 4: only ledger_verify may set status=verified after check passes (a dry run writes nothing).


def _fixed_row(path: Path, check: str) -> dict:
    from conftest import head_sha

    row = dict(FIXTURE)
    row["status"] = "fixed"
    row["check"] = check
    row["fixed_in"] = head_sha(Path.cwd())
    append_finding(path, row)
    return load_findings(path)[0]


def test_ledger_verify_promotes_fixed_to_verified(tmp_path, fix_repo):
    from conftest import head_sha

    row = _fixed_row(tmp_path / "f.jsonl", "python3 check.py 0")
    verified = verify_finding(row)
    assert verified["status"] == "verified"
    assert verified["verification_exit_code"] == 0
    assert "verified_at" in verified
    assert verified["verified_at_sha"] == head_sha(fix_repo)


def test_dry_run_returns_the_row_unverified(tmp_path, fix_repo):
    row = _fixed_row(tmp_path / "f.jsonl", "python3 check.py 0")
    assert verify_finding(row, dry_run=True)["status"] == "fixed"


def test_dry_run_cli_leaves_the_ledger_bytes_unchanged(tmp_path, fix_repo, capsys):
    path = tmp_path / "f.jsonl"
    _fixed_row(path, "python3 check.py 2")
    before = path.read_bytes()
    assert main(["--ledger", str(path), "--dry-run"]) == 0
    assert path.read_bytes() == before
    assert json.loads(capsys.readouterr().out) == {"would_verify": 1, "verified": 0, "ledger": str(path)}


def test_supersede_promotes_an_open_row_of_a_passed_gate(tmp_path):
    row = dict(FIXTURE)
    row["check"] = "python3 $CADENCE_HOME/scripts/architecture_review.py --phase impl --check"
    out = supersede_finding(row, "E3 pass at abc123")
    assert out["status"] == "verified"
    assert out["note"] == "superseded by E3 pass at abc123"
    assert out["verification_exit_code"] == 0


def test_supersede_refuses_a_manual_check():
    with pytest.raises(ValueError, match="manual"):
        supersede_finding({**FIXTURE, "check": "manual"}, "E3 pass")


def test_a_check_that_is_not_valid_shell_stays_fixed(tmp_path, fix_repo):
    """A row written before the append rule: verify refuses its check, and the row stays fixed."""
    from conftest import head_sha

    path = tmp_path / "f.jsonl"
    row = {**FIXTURE, "status": "fixed", "check": "pytest assert_fn(", "fixed_in": head_sha(Path.cwd())}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid shell"):
        verify_finding(load_findings(path)[0])
    assert load_findings(path)[0]["status"] == "fixed"


# --- a finding closes only on a real check, a fix names its commit, and a person confirms the manual path.

NO_OP_CHECKS = ["true", ":", "echo ok", "exit 0", "", "   ", "uv run --", "/bin/true", "FOO=1 true", "uv run true", "npx -c true",
                "pytest -q || true", "pytest -q; echo done"]
NOT_A_RUNNER = ["ls -la", "bash -c ok", "cat README.md",
                # inline interpreter code is not a test
                "python3 -c pass", "python -c 1", "uv run python -c pass", "PYTHONPATH=src python3 -u -c 'import a'",
                "node -e 1", "perl -e 1", "ruby -e 1", "sh -c 'pytest -q'", "bash -c 'npm test'",
                "python3 -cpass", "python3 -Ic pass", "python3 -X dev -c pass", "node --eval 1",
                "/usr/bin/python3.12 -c pass"]
REAL_CHECKS = ["pytest -q tests/test_a.py", "python3 -m pytest", "uv run pytest", "npm test", "cargo test",
               "go test ./...", "make test", "./run-tests.sh", "bash scripts/check.sh", "cd sub && pytest", "manual",
               "pnpm test", "yarn test -- a.spec.ts", "bun run check", "npx vitest run", "cargo clippy -- -D warnings",
               "go vet ./...", "ruff check src", "mypy src", "tsc --noEmit", "tox -e py",
               "python3 -m pytest -c pytest.ini tests", "python3 tests/check.py test_a", "python3 -c pass && pytest -q",
               "python3 -W error -m pytest -q", "python3 -Wignore -m pytest", "npx -p vitest vitest run"]


@pytest.mark.parametrize("check", NO_OP_CHECKS)
def test_append_rejects_a_check_that_is_a_shell_no_op(tmp_path, check):
    with pytest.raises(FindingsValidationError, match="no-op|is empty|require check"):
        append_finding(tmp_path / "f.jsonl", {**ROW, "check": check})
    assert not (tmp_path / "f.jsonl").exists()


@pytest.mark.parametrize("check", NOT_A_RUNNER)
def test_append_rejects_a_check_that_names_no_runner(tmp_path, check):
    with pytest.raises(FindingsValidationError, match="names no test runner"):
        append_finding(tmp_path / "f.jsonl", {**ROW, "check": check})


@pytest.mark.parametrize("check", REAL_CHECKS)
def test_append_accepts_a_test_runner_or_a_script_path(tmp_path, check):
    append_finding(tmp_path / "f.jsonl", {**ROW, "check": check})
    assert load_findings(tmp_path / "f.jsonl")[0]["check"] == check


def test_ledger_verify_refuses_a_no_op_check_written_before_the_rule(tmp_path, fix_repo):
    from conftest import head_sha

    with pytest.raises(ValueError, match="no-op"):
        verify_finding({**ROW, "check": "true", "status": "fixed", "fixed_in": head_sha(fix_repo)})


def test_fixed_needs_a_commit_on_the_branch(ledger, fix_repo, capsys):
    from conftest import commit_file, git, head_sha

    _cli("--ledger", str(ledger), "append", "--json", json.dumps(ROW))
    base = head_sha(fix_repo)
    git(fix_repo, "checkout", "-q", "-b", "other")
    elsewhere = commit_file(fix_repo, "on another branch")
    git(fix_repo, "checkout", "-q", "-")
    for extra, why in (([], "requires fixed_in"), (["--fixed-in", "0" * 40], "not a commit"),
                       (["--fixed-in", elsewhere], "not on the branch")):
        assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "fixed", *extra) == 1
        assert why in capsys.readouterr().err
    assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "fixed", "--fixed-in", base) == 0
    with pytest.raises(FindingsValidationError, match="fixed_in"):
        append_finding(ledger.with_name("g.jsonl"), {**ROW, "id": "gh-1-F02", "status": "fixed"})


def test_ledger_verify_runs_at_head_and_refuses_a_fix_that_head_does_not_hold(tmp_path, fix_repo):
    from conftest import commit_file, git

    git(fix_repo, "checkout", "-q", "-b", "other")
    elsewhere = commit_file(fix_repo, "on another branch")
    git(fix_repo, "checkout", "-q", "-")
    with pytest.raises(ValueError, match="not HEAD or an ancestor of HEAD"):
        verify_finding({**ROW, "status": "fixed", "fixed_in": elsewhere})
    with pytest.raises(ValueError, match="fixed_in"):
        verify_finding({**ROW, "status": "fixed"})


MANUAL = {**ROW, "check": "manual"}


def test_only_set_status_human_confirms_a_manual_check(ledger, fix_repo, capsys):
    from conftest import head_sha

    sha = head_sha(fix_repo)
    assert _cli("--ledger", str(ledger), "append", "--json", json.dumps({**MANUAL, "human_confirmed": True})) == 1
    assert "set-status --human" in capsys.readouterr().err
    assert _cli("--ledger", str(ledger), "append", "--json", json.dumps(MANUAL)) == 0
    fix = ("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "fixed", "--fixed-in", sha)
    assert _cli(*fix) == 0
    with pytest.raises(ValueError, match="set-status --human"):
        verify_finding(load_findings(ledger)[0])
    assert _cli(*fix, "--human") == 1
    assert "--by" in capsys.readouterr().err
    assert _cli(*fix, "--human", "--by", "Jane Doe") == 0
    row = load_findings(ledger)[0]
    assert (row["human_confirmed"], row["confirmed_by"]) == (True, "Jane Doe") and row["confirmed_at"]
    assert main(["--ledger", str(ledger)]) == 0
    assert load_findings(ledger)[0]["status"] == "verified"


def test_human_confirms_only_a_manual_check(ledger, fix_repo, capsys):
    from conftest import head_sha

    _cli("--ledger", str(ledger), "append", "--json", json.dumps(ROW))
    assert _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "fixed",
                "--fixed-in", head_sha(fix_repo), "--human", "--by", "Jane Doe") == 1
    assert "manual" in capsys.readouterr().err


def test_ledger_verify_refuses_a_human_flag_without_its_stamp(fix_repo):
    from conftest import head_sha

    with pytest.raises(ValueError, match="set-status --human"):
        verify_finding({**MANUAL, "status": "fixed", "fixed_in": head_sha(fix_repo), "human_confirmed": True})


def test_closure_names_each_manually_confirmed_row(ledger, fix_repo, monkeypatch, capsys):
    import closure
    from conftest import head_sha

    _cli("--ledger", str(ledger), "append", "--json", json.dumps(MANUAL))
    _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "fixed", "--fixed-in",
         head_sha(fix_repo), "--human", "--by", "Jane Doe")
    assert main(["--ledger", str(ledger)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["closure.py", "--check", "--ledger", str(ledger)])
    assert closure.main() == 0
    out = capsys.readouterr().out
    assert "MANUAL  gh-1-F01 confirmed by Jane Doe at " in out
    summary = json.loads([line for line in out.splitlines() if line.startswith("{")][-1])
    assert [r["id"] for r in summary["manually_confirmed"]] == ["gh-1-F01"]


# --- during review, a deferral names a ticket of the adapter pattern and a row of the deferral register.

REGISTER = """# Deferral register: cd-49

## Approved deferrals

Zero approved deferrals.

## Review deferrals

| # | Item | Why deferred | Request trace | Ripeness | Evidence | Follow-up ticket |
|---|---|---|---|---|---|---|
| 1 | cd-49-F1: Retry the upload | Needs the cache layer first | none | blocked-by | #41 adds the cache layer | #42 |
"""


def _ruling(register: Path, verdict: str = "GREEN") -> None:
    """The /deferral-review peer's ruling of the register as it is now, attested as the hook does."""
    import hashlib

    import peer_attest

    sha = hashlib.sha256(register.read_bytes()).hexdigest()[:12]
    body = (f"RUN_BY_COMMAND: deferral-review\nRegister sha256: {sha}\n\n| Row | Ruling | Reason | Condition |\n"
            f"|---|---|---|---|\n| review 1 | {verdict} | #41 is open | none |\n").encode()
    review = register.with_name("cd-49_deferral-review.md")
    review.write_bytes(body)
    peer_attest.record(register.parent.parent, "run-49", f"deferrals/{review.name}", "deferral-peer",
                       peer_attest.sha256(body))


def test_a_review_deferral_needs_the_adapter_pattern_and_a_register_row(scripts_path, tmp_path, monkeypatch, capsys):
    from conftest import ledger_row, linked_worktrees, save_worktree_session

    primary, trees = linked_worktrees(tmp_path, ("w0",))
    save_worktree_session(trees["w0"], task_id="cd-49", pipeline_run_id="run-49")
    monkeypatch.chdir(trees["w0"])
    context = primary / ".context"
    defer = ("set-status", "--id", "cd-49-F1", "--status", "deferred", "--ticket")
    assert _cli("append", "--json", json.dumps(ledger_row("cd-49-F1"))) == 0
    assert _cli(*defer, "whatever") == 0  # before E4, P8 owns the register
    (context / "prs").mkdir(parents=True, exist_ok=True)
    (context / "prs" / "draft-pr-run-49.md").write_text("PR: https://github.com/o/r/pull/9\n", encoding="utf-8")
    assert _cli(*defer, "whatever") == 1
    assert "does not match the adapter pattern" in capsys.readouterr().err
    assert _cli(*defer, "#42") == 1
    assert "no row of an adjudicated deferral register names #42" in capsys.readouterr().err
    (context / "deferrals").mkdir()
    register = context / "deferrals" / "cd-49_deferral-register.md"
    register.write_text(REGISTER, encoding="utf-8")
    assert _cli(*defer, "#42") == 1  # no /deferral-review ruling yet
    _ruling(register, "RED")
    assert _cli(*defer, "#42") == 1
    _ruling(register, "AMBER")  # a review row is GREEN or RED
    assert _cli(*defer, "#42") == 1
    _ruling(register)
    assert _cli(*defer, "#43") == 1
    assert _cli(*defer, "#42") == 0
    assert _cli("set-status", "--id", "cd-49-F1", "--status", "spec_gap", "--ticket", "#43") == 1
    assert _canonical_rows(context) == ["cd-49-F1"]
    assert load_findings(context / "findings.jsonl")[0]["ticket"] == "#42"
    # With no --ticket, the check reads the ticket the row already holds
    assert _cli("set-status", "--id", "cd-49-F1", "--status", "deferred", "--note", "again") == 0
    assert load_findings(context / "findings.jsonl")[0]["note"] == "again"


def test_a_bare_mf_id_becomes_the_row_id_of_the_worktrees_run(scripts_path, tmp_path, monkeypatch, capsys):
    """The peer writes `MF-1`; findings_io builds `<task>-<run tag>-MF-1` from the real run."""
    from conftest import ledger_row, linked_worktrees, save_worktree_session

    primary, trees = linked_worktrees(tmp_path, ("w0",))
    save_worktree_session(trees["w0"], task_id="cd-49", pipeline_run_id="3f2a9c1d-0000-4000-8000-000000000001")
    monkeypatch.chdir(trees["w0"])
    assert _cli("append", "--json", json.dumps({**ledger_row("MF-1"), "stage": "pr-review-local"})) == 0
    assert _canonical_rows(primary / ".context") == ["cd-49-3f2a9c1d-MF-1"]
    assert _cli("append", "--json", json.dumps({**ledger_row("MF-1"), "stage": "pr-review-local"})) == 1
    assert "already exists" in capsys.readouterr().err
    assert _cli("append", "--json", json.dumps({**ledger_row("MF-3"), "stage": "test-benchmark"})) == 1
    assert "is for the R1 stages" in capsys.readouterr().err
    monkeypatch.chdir(primary)  # the primary checkout has no run: the id cannot be built
    assert _cli("append", "--json", json.dumps(ledger_row("MF-2"))) == 1
    assert "cannot be built" in capsys.readouterr().err


def test_ledger_verify_reports_each_row_it_cannot_verify_and_verifies_the_rest(ledger, fix_repo, capsys):
    """One legacy fixed row with no fixed_in does not stop the others."""
    from conftest import head_sha

    legacy = {**ROW, "id": "gh-1-F02", "status": "fixed"}
    good = {**ROW, "id": "gh-1-F03", "status": "fixed", "fixed_in": head_sha(fix_repo)}
    ledger.write_text("".join(json.dumps(r) + "\n" for r in (ROW, legacy, good)), encoding="utf-8")
    assert main(["--ledger", str(ledger)]) == 1
    err = capsys.readouterr().err
    assert "gh-1-F02" in err and "fixed_in" in err
    by_id = {r["id"]: r["status"] for r in load_findings(ledger)}
    assert by_id == {"gh-1-F01": "open", "gh-1-F02": "fixed", "gh-1-F03": "verified"}


# --- A check that names a Cadence script runs from the skill: it names it under $CADENCE_HOME.

def test_append_rejects_a_cadence_script_named_as_a_file_of_the_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FindingsValidationError, match=r"write \$CADENCE_HOME/scripts/test_shape.py"):
        append_finding(tmp_path / "f.jsonl", {**ROW, "check": "python3 scripts/test_shape.py --change"})
    append_finding(tmp_path / "f.jsonl", {**ROW, "check": "python3 $CADENCE_HOME/scripts/test_shape.py --change"})
    assert load_findings(tmp_path / "f.jsonl")[0]["check"].startswith("python3 $CADENCE_HOME/")


def test_a_script_of_the_repository_itself_is_not_a_cadence_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "test_shape.py").write_text("", encoding="utf-8")
    append_finding(tmp_path / "f.jsonl", {**ROW, "check": "python3 scripts/test_shape.py --change"})


def test_ledger_verify_runs_a_check_under_cadence_home(tmp_path, fix_repo, monkeypatch):
    monkeypatch.delenv("CADENCE_HOME", raising=False)
    row = _fixed_row(tmp_path / "f.jsonl", "python3 $CADENCE_HOME/scripts/ledger_verify.py --help")
    assert verify_finding(row)["status"] == "verified"


@pytest.mark.parametrize("check", [
    "python3 -m pytest tests/test_recurring.py -q (cases are parametrize rows; one shared helper)",
    "pytest -q 'tests/a.py",
])
def test_append_rejects_a_check_that_is_not_valid_shell(tmp_path, check):
    with pytest.raises(FindingsValidationError, match="not valid shell"):
        append_finding(tmp_path / "f.jsonl", {**ROW, "check": check})


def test_set_status_takes_no_abbreviation_of_human(ledger, fix_repo, capsys):
    """The shell hook matches `--human`, so argparse takes no `--hum` or `--hu`."""
    from conftest import head_sha

    assert _cli("--ledger", str(ledger), "append", "--json", json.dumps(MANUAL)) == 0
    for flag in ("--hum", "--hu"):
        with pytest.raises(SystemExit):
            _cli("--ledger", str(ledger), "set-status", "--id", "gh-1-F01", "--status", "fixed",
                 "--fixed-in", head_sha(fix_repo), flag, "--by", "Jo")
    assert "human_confirmed" not in load_findings(ledger)[0]


# --- a check that names a placeholder is refused, and a check that cannot run can be replaced.

@pytest.mark.parametrize("check", ["python3 SCRATCH/r2_f04_check.py", "python3 -m pytest <test_file>",
                                   "python3 TMP_DIR/check.py"])
def test_append_rejects_a_check_that_names_a_placeholder(tmp_path, check):
    with pytest.raises(FindingsValidationError, match="placeholder"):
        append_finding(tmp_path / "f.jsonl", {**ROW, "check": check})


def test_set_check_replaces_only_a_check_that_cannot_run(ledger, fix_repo):
    """A run stopped at R5 on a row whose check named a script that was never written; append was the only writer
    of a check. set-check replaces a check that cannot run, keeps the old one, and never touches one that runs."""
    from findings_io import missing_script, set_check

    append_finding(ledger, {**ROW, "check": "python3 checks/f04.py"})
    assert missing_script("python3 checks/f04.py", fix_repo) == "checks/f04.py"
    assert _cli("--ledger", str(ledger), "set-check", "--id", "gh-1-F01", "--check", "python3 check.py 0") == 0
    [row] = load_findings(ledger)
    assert (row["check"], row["check_was"]) == ("python3 check.py 0", "python3 checks/f04.py")
    with pytest.raises(FindingsValidationError, match="can run, so it stays"):
        set_check(ledger, "gh-1-F01", "python3 check.py 1", cwd=fix_repo)  # a weaker check never replaces one that runs
    with pytest.raises(FindingsValidationError, match="no-op"):
        set_check(ledger, "gh-1-F01", "true", cwd=fix_repo)


def test_ledger_verify_names_set_check_for_a_check_whose_script_is_missing(ledger, fix_repo, capsys):
    from conftest import head_sha

    append_finding(ledger, {**ROW, "check": "python3 checks/f04.py"})
    set_status(ledger, "gh-1-F01", "fixed", fixed_in=head_sha(fix_repo), cwd=fix_repo)
    assert main(["--ledger", str(ledger)]) == 1
    err = capsys.readouterr()
    assert "findings_io.py set-check --id gh-1-F01" in err.out + err.err
