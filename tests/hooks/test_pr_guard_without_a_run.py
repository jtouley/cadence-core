"""`gh pr create` and `gh pr ready` need a Cadence run past E3, or a review of HEAD.

On run efcd8404 the orchestrator opened a PR with no session and no review: no hook
looked at `gh pr` commands. The guard in hooks/pr_guard.py now denies them in a repo
with `.context/` and commits ahead of the base, unless:
(a) this worktree's run has an E3 breadcrumb of its own run (and, for `ready`, of HEAD),
(b) a review of HEAD exists (attested by a peer on Claude Code), or
(c) the command starts with CADENCE_ALLOW_PR=1 (logged).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import (HOOKS_DIR, clean_env, code_paths, commit_file, context_ignored_repo, enable_all_packs, git,
                      head_sha, hook_denied, pr_review, run_py, save_worktree_session, shell_call, passing_acceptance)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import peer_attest  # noqa: E402
import pr_guard  # noqa: E402

code_paths()  # tests/fixtures on the path: the verdict-join tests import verdict_join

RUN = str(uuid.UUID(int=0x1111_2222_3333_4444_5555_6666_7777_8888))
OTHER = str(uuid.UUID(int=0x9999_aaaa_bbbb_cccc_dddd_eeee_ffff_0000))
HOSTS = ["claude", "cursor"]


def _repo(path: Path, ahead: int = 2, context: bool = True) -> Path:
    context_ignored_repo(path)
    git(path, "checkout", "-q", "-b", "feature/gh-47-x")
    for i in range(ahead):
        commit_file(path, f"work {i}")
    if context:
        (path / ".context").mkdir()
        (path / ".cadence.json").write_text("{}\n", encoding="utf-8")  # a Cadence footprint (R1 R-2)
        enable_all_packs(path)  # E3 is on (test_pr_guard_packs.py: the default config)
    return path


def _guard(cwd: Path, command: str, host: str) -> tuple[bool, str]:
    """(denied, output) of the real hook for a shell command."""
    proc = shell_call(cwd, command, host)
    return hook_denied(proc), proc.stdout + proc.stderr


def _session(repo: Path, run: str = RUN, **fields) -> None:
    activated = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    payload = {"pipeline_run_id": run, "activated_at": activated, "workspace": str(repo.resolve()),
               "orchestrator_agent_ids": [], "orchestrator_source": "toplevel"}
    payload.update(fields)
    save_worktree_session(repo, **payload)


def _breadcrumb(context: Path, run: str = RUN, *, names: str | None = None, head: str | None = None) -> Path:
    path = context / "gates" / f"architecture-review-impl-{run}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"slug": f"architecture-review-impl-{run}", "pipeline_run_id": names or run,
                                "head": head or ""}), encoding="utf-8")
    return path


# --- reach -------------------------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_a_repo_with_no_context_is_not_guarded(tmp_path, host):
    repo = _repo(tmp_path / "r", context=False)
    denied, _ = _guard(repo, "gh pr create --fill", host)
    assert not denied
    assert not (repo / ".context").exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", ["gh pr create --fill", "cd x && gh pr create"])
def test_no_run_and_no_review_is_denied(tmp_path, host, command):
    """SF-1: planned_checks finds no write in these commands, so the guard must run before that early allow."""
    repo = _repo(tmp_path / "r")
    denied, out = _guard(repo, command, host)
    assert denied
    assert "E3" in out and "review of HEAD" in out and "CADENCE_ALLOW_PR=1" in out
    assert "Cadence footprint" in out


@pytest.mark.parametrize("host", HOSTS)
def test_no_commits_ahead_is_allowed(tmp_path, host):
    repo = _repo(tmp_path / "r", ahead=0)
    assert not _guard(repo, "gh pr create --fill", host)[0]


@pytest.mark.parametrize("host", HOSTS)
def test_two_roots_and_no_base_is_denied(tmp_path, host):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feature/x")
    commit_file(repo, "a")
    git(repo, "checkout", "-q", "--orphan", "other")
    commit_file(repo, "b")
    git(repo, "checkout", "-q", "feature/x")
    git(repo, "merge", "-q", "--allow-unrelated-histories", "other", "-m", "m")
    (repo / ".context").mkdir()
    (repo / ".cadence.json").write_text("{}\n", encoding="utf-8")
    denied, out = _guard(repo, "gh pr create --fill", host)
    assert denied and "CADENCE_ALLOW_PR=1" in out


# --- escape (a): the E3 breadcrumb of this worktree's run ------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_a_breadcrumb_of_this_run_allows(tmp_path, host):
    repo = _repo(tmp_path / "r")
    _session(repo)
    _breadcrumb(repo / ".context", head=head_sha(repo))
    assert not _guard(repo, "gh pr create --draft", host)[0]


@pytest.mark.parametrize("host", HOSTS)
def test_a_hard_stopped_run_is_denied(tmp_path, host):
    repo = _repo(tmp_path / "r")
    _session(repo, hard_stop={"class": "retries", "reason": "x", "at": "2026-01-01T00:00:00+00:00"})
    _breadcrumb(repo / ".context", head=head_sha(repo))
    denied, out = _guard(repo, "gh pr create --draft", host)
    assert denied and "hard stop" in out


@pytest.mark.parametrize("host", HOSTS)
def test_no_breadcrumb_is_denied_with_the_run_id(tmp_path, host):
    repo = _repo(tmp_path / "r")
    _session(repo)
    denied, out = _guard(repo, "gh pr create --draft", host)
    assert denied and f"no E3 breadcrumb for run {RUN}" in out


@pytest.mark.parametrize("case", ["other_run", "before_activation", "names_other_run"])
def test_a_breadcrumb_that_is_not_this_runs_is_denied(tmp_path, case):
    repo = _repo(tmp_path / "r")
    context = repo / ".context"
    if case == "other_run":
        _session(repo)
        _breadcrumb(context, OTHER, head=head_sha(repo))
    elif case == "before_activation":
        _session(repo, activated_at=datetime.now(timezone.utc).isoformat())
        path = _breadcrumb(context, head=head_sha(repo))
        old = time.time() - 3600
        os.utime(path, (old, old))
    else:
        _session(repo)
        _breadcrumb(context, names=OTHER, head=head_sha(repo))
    assert _guard(repo, "gh pr create --draft", "claude")[0]


@pytest.mark.parametrize("host", HOSTS)
def test_stale_head_blocks_ready_but_not_create(tmp_path, host):
    repo = _repo(tmp_path / "r")
    _session(repo)
    old = head_sha(repo)
    _breadcrumb(repo / ".context", head=old)
    new = commit_file(repo, "one more")
    denied, out = _guard(repo, "gh pr ready 12", host)
    assert denied and f"E3 breadcrumb is for {old}, HEAD is {new}" in out
    assert not _guard(repo, "gh pr create --draft", host)[0]
    _breadcrumb(repo / ".context", head=new)
    assert not _guard(repo, "gh pr ready 12", host)[0]


def test_the_guard_does_not_import_headless(tmp_path):
    repo = _repo(tmp_path / "r")
    _session(repo)
    _breadcrumb(repo / ".context", head=head_sha(repo))
    code = (
        f"import sys; sys.path.insert(0, {str(HOOKS_DIR)!r}); import pr_guard; "
        f"print(pr_guard.pr_guard_decision('gh pr create', __import__('pathlib').Path({str(repo)!r}), 'claude')); "
        "print('headless' in sys.modules)"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=repo, env=clean_env(), capture_output=True, text=True)
    assert proc.stdout.split() == ["None", "False"], proc.stderr


# --- escape (b): a review of HEAD ----------------------------------------------------------------


def test_a_review_of_head_allows_on_cursor(tmp_path):
    repo = _repo(tmp_path / "r")
    pr_review(repo / ".context", head_sha(repo))
    assert not _guard(repo, "gh pr create --fill", "cursor")[0]


def test_on_claude_the_review_needs_a_peer_row(tmp_path):
    repo = _repo(tmp_path / "r")
    context = repo / ".context"
    review = pr_review(context, head_sha(repo))
    assert _guard(repo, "gh pr create --fill", "claude")[0]
    peer_attest.record(context, OTHER, "prs/reviews/PR_REVIEW_#1.md", "P", peer_attest.sha256(review.read_bytes()))
    assert not _guard(repo, "gh pr create --fill", "claude")[0]


def test_on_claude_an_orchestrator_row_does_not_count(tmp_path):
    repo = _repo(tmp_path / "r")
    context = repo / ".context"
    _session(repo, orchestrator_agent_ids=["O"], orchestrator_source="note")
    review = pr_review(context, head_sha(repo))
    peer_attest.record(context, RUN, "prs/reviews/PR_REVIEW_#1.md", "O", peer_attest.sha256(review.read_bytes()))
    assert _guard(repo, "gh pr create --fill", "claude")[0]


def test_on_claude_a_missing_orchestrator_blocks_the_review_escape(tmp_path):
    repo = _repo(tmp_path / "r")
    context = repo / ".context"
    _session(repo, orchestrator_source="missing")
    review = pr_review(context, head_sha(repo))
    peer_attest.record(context, RUN, "prs/reviews/PR_REVIEW_#1.md", "P", peer_attest.sha256(review.read_bytes()))
    denied, out = _guard(repo, "gh pr create --fill", "claude")
    assert denied and "orchestrator unknown" in out


# --- escape (c): the logged override ------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_the_override_is_allowed_and_logged(tmp_path, host):
    repo = _repo(tmp_path / "r")
    assert not _guard(repo, "CADENCE_ALLOW_PR=1 gh pr create --fill", host)[0]
    log = repo / ".context" / "issues" / "pr-guard-overrides.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and head_sha(repo) in lines[0] and "gh pr create" in lines[0]


# --- the command match --------------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "gh pr ready 12", "cd x && gh pr create", "gh --repo o/r pr create", "gh -R o/r pr ready 3",
    "gh --repo=o/r pr create", "FOO=1 gh pr create", "git push && gh pr create --draft",
    "gh pr comment 12 --body-file c.md", "gh -R o/r pr review 3 --approve",
])
def test_guarded_gh_pr_commands_are_matched(command):
    assert pr_guard.PR_COMMAND.search(command)


# Forms that once skipped the guard.
EVASIONS = [
    "git push -u origin HEAD\ngh pr create --fill",
    "{ gh pr create --fill; }",
    "env gh pr create --fill",
    "/opt/homebrew/bin/gh pr create --fill",
    "command gh pr create",
    "exec gh pr ready 3",
]


@pytest.mark.parametrize("command", EVASIONS)
def test_wrapped_forms_are_matched(command):
    assert pr_guard.PR_COMMAND.search(command)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", EVASIONS[:4])
def test_wrapped_forms_are_denied(tmp_path, host, command):
    repo = _repo(tmp_path / "r")
    assert _guard(repo, command, host)[0]


def test_every_segment_is_checked(tmp_path):
    """`create && ready`: the ready segment needs a breadcrumb of HEAD (R1 N-1)."""
    repo = _repo(tmp_path / "r")
    _session(repo)
    old = head_sha(repo)
    _breadcrumb(repo / ".context", head=old)
    commit_file(repo, "one more")
    assert not _guard(repo, "gh pr create --draft", "claude")[0]
    assert _guard(repo, "gh pr create --draft && gh pr ready 3", "claude")[0]


def test_an_override_on_one_segment_does_not_cover_another(tmp_path):
    repo = _repo(tmp_path / "r")
    assert _guard(repo, "CADENCE_ALLOW_PR=1 gh pr create; gh pr ready 3", "claude")[0]


@pytest.mark.parametrize("host", HOSTS)
def test_a_context_dir_without_a_cadence_footprint_is_not_guarded(tmp_path, host):
    """Other skills make .context/ too; their PRs are not Cadence's to hold."""
    repo = _repo(tmp_path / "r")
    (repo / ".cadence.json").unlink()
    assert not _guard(repo, "gh pr create --fill", host)[0]
    (repo / ".context" / "attest").mkdir()
    assert _guard(repo, "gh pr create --fill", host)[0]


@pytest.mark.parametrize("command", [
    "gh pr view", "gh --repo o/r pr view", 'echo "gh pr create"', "gh pr list", "gh issue create",
])
def test_other_commands_are_not_matched(command):
    assert not pr_guard.PR_COMMAND.search(command)


def test_no_work_on_other_commands(tmp_path, monkeypatch):
    calls = []
    real_run, real_exists = subprocess.run, Path.exists
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(("run", a)) or real_run(*a, **k))
    monkeypatch.setattr(Path, "exists", lambda self: calls.append(("exists", self)) or real_exists(self))
    assert pr_guard.pr_guard_decision("ls -la", tmp_path, "claude") is None
    assert calls == []


# --- rules that surviving mutants showed were not pinned (M1-M8) --------------------


def _decide(repo: Path, command: str = "gh pr create --fill", host: str = "claude") -> str | None:
    return pr_guard.pr_guard_decision(command, repo, host)


def test_a_review_of_an_older_commit_does_not_open_the_review_escape(tmp_path):
    """M1: on Cursor, any review file opened escape (b) if the sha check was `or`."""
    repo = _repo(tmp_path / "r")
    pr_review(repo / ".context", git(repo, "rev-parse", "HEAD~1").strip())
    assert _decide(repo, host="cursor") is not None


def test_a_breadcrumb_written_at_the_activation_instant_counts(tmp_path):
    """M2 (plan F5): the mtime rule is `before activation`, so equal is allowed."""
    repo = _repo(tmp_path / "r")
    instant = 1_900_000_000
    _session(repo, activated_at=datetime.fromtimestamp(instant, timezone.utc).isoformat())
    crumb = _breadcrumb(repo / ".context", head=head_sha(repo))
    os.utime(crumb, (instant, instant))
    assert _decide(repo) is None
    os.utime(crumb, (instant - 1, instant - 1))
    assert "before activation" in _decide(repo)


def test_no_override_writes_no_override_log(tmp_path):
    """M3."""
    repo = _repo(tmp_path / "r")
    assert _decide(repo) is not None
    assert not (repo / ".context" / "issues" / "pr-guard-overrides.log").exists()


def test_an_override_after_the_env_wrapper_counts(tmp_path):
    """M4."""
    repo = _repo(tmp_path / "r")
    assert _decide(repo, "env CADENCE_ALLOW_PR=1 gh pr create --fill") is None
    assert (repo / ".context" / "issues" / "pr-guard-overrides.log").read_text(encoding="utf-8").count("\n") == 1


def test_claudecode_in_the_env_needs_a_peer_row_on_any_host(tmp_path, monkeypatch):
    """M5: the review escape on Claude Code needs attestation, whatever --host says."""
    repo = _repo(tmp_path / "r")
    pr_review(repo / ".context", head_sha(repo))
    assert _decide(repo, host="cursor") is None
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _decide(repo, host="cursor") is not None


@pytest.mark.parametrize("footprint", ["cadence_json", "root_session", "by_worktree", "attest"])
def test_each_cadence_footprint_turns_the_guard_on(tmp_path, footprint):
    """M6: all four footprints, one at a time."""
    repo = _repo(tmp_path / "r")
    (repo / ".cadence.json").unlink()
    context = repo / ".context"
    assert _decide(repo) is None
    if footprint == "cadence_json":
        (repo / ".cadence.json").write_text("{}\n", encoding="utf-8")
    elif footprint == "root_session":
        (context / "cadence-session.json").write_text("{}\n", encoding="utf-8")
    elif footprint == "by_worktree":
        (context / "sessions" / "by-worktree").mkdir(parents=True)
        (context / "sessions" / "by-worktree" / "k.json").write_text("{}\n", encoding="utf-8")
    else:
        (context / "attest").mkdir()
    assert _decide(repo) is not None


def test_a_bad_first_review_does_not_hide_a_good_one(tmp_path):
    """M7: an unreadable review, then an unattested one, then an attested one."""
    repo = _repo(tmp_path / "r")
    context = repo / ".context"
    (context / "prs" / "reviews" / "a_unreadable.md").mkdir(parents=True)
    pr_review(context, head_sha(repo))  # prs/reviews/PR_REVIEW_#1.md: names HEAD, no peer row
    good = context / "reviews" / "pr_b.md"
    good.parent.mkdir(parents=True)
    good.write_text(f"RUN_BY_COMMAND: pr-review-update\nLast reviewed commit: {head_sha(repo)}\n", encoding="utf-8")
    assert _decide(repo) is not None
    peer_attest.record(context, OTHER, "reviews/pr_b.md", "P", peer_attest.sha256(good.read_bytes()))
    assert _decide(repo) is None


def test_a_stale_review_that_sorts_first_does_not_hide_a_good_one(tmp_path):
    """M7: the sha check skips a stale review and keeps looking in the same folder."""
    repo = _repo(tmp_path / "r")
    reviews = repo / ".context" / "prs" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "0_stale.md").write_text(f"Last reviewed commit: {git(repo, 'rev-parse', 'HEAD~1').strip()}\n", encoding="utf-8")
    (reviews / "z_good.md").write_text(f"Last reviewed commit: {head_sha(repo)}\n", encoding="utf-8")
    assert _decide(repo, host="cursor") is None


def test_the_deny_text_names_the_verb_the_stop_class_and_a_missing_base(tmp_path):
    """M8."""
    repo = _repo(tmp_path / "r")
    _session(repo, hard_stop={"class": "retries", "reason": "x", "at": "2026-01-01T00:00:00+00:00"})
    message = _decide(repo)
    assert "`gh pr create`" in message and "hard stop (retries)" in message
    assert "no base branch" not in message
    assert "`gh pr ready`" in _decide(repo, "gh pr create && gh pr ready 3")
    lone = tmp_path / "lone"
    lone.mkdir()
    git(lone, "init", "-q", "-b", "feature/x")
    commit_file(lone, "a")
    git(lone, "checkout", "-q", "--orphan", "other")
    commit_file(lone, "b")
    git(lone, "checkout", "-q", "feature/x")
    git(lone, "merge", "-q", "--allow-unrelated-histories", "other", "-m", "m")
    (lone / ".context").mkdir()
    (lone / ".cadence.json").write_text("{}\n", encoding="utf-8")
    assert "no base branch" in _decide(lone)


# --- on a verdict_join run, the review escape needs a review peer ---------------


@pytest.mark.parametrize(("stage", "denied"), [("spec-driven", True), ("pr-review-update", False)])
def test_on_a_verdict_join_run_the_review_escape_needs_a_review_peer(tmp_path, stage, denied):
    from verdict_join import peer_wrote

    repo = _repo(tmp_path / "r")
    _session(repo, verdict_join=True, host="claude")
    head = head_sha(repo)
    peer_wrote(repo / ".context", RUN, "prs/reviews/PR_REVIEW_#1.md", agent_id="a", stage=stage,
               body=f"RUN_BY_COMMAND: pr-review-local\nVerdict: APPROVE\nLast reviewed commit: {head}\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(repo),
               "tool_input": {"command": "gh pr ready"}}
    proc = run_py(HOOKS_DIR / "guard_skill_gate.py", "--host", "claude", cwd=elsewhere, stdin=payload)
    out = proc.stdout + proc.stderr
    got = hook_denied(proc)  # S-11: the hook process cwd is not the worktree
    assert got is denied, out
    if denied:
        assert "prs/reviews/PR_REVIEW_#1.md written by a spec-driven peer" in out


def test_a_wrong_stage_review_attested_in_another_run_log_does_not_open_the_escape(tmp_path):
    """The stage check reads the same newest attest row as the attest check."""
    from verdict_join import peer_wrote

    repo = _repo(tmp_path / "r")
    _session(repo, verdict_join=True, host="claude")
    head = head_sha(repo)
    peer_wrote(repo / ".context", RUN, "prs/reviews/PR_REVIEW_#1.md", agent_id="a", stage="spec-driven",
               body=f"RUN_BY_COMMAND: pr-review-local\nVerdict: APPROVE\nLast reviewed commit: {head}\n",
               attest_run=OTHER)
    denied, out = _guard(repo, "gh pr ready", "claude")
    assert denied, out
    assert "written by a spec-driven peer" in out


# --- `gh pr comment` and `gh pr review` wait for R5 (closure) of the worktree's run at HEAD ----------

TALK = ["gh pr comment 12 --body-file c.md", "gh pr review 12 --approve", "git push && gh -R o/r pr comment 3 -b x"]


def _closure(context: Path, run: str = RUN, head: str = "") -> Path:
    path = context / "gates" / f"closure-{run}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"slug": f"closure-{run}", "pipeline_run_id": run, "head": head}), encoding="utf-8")
    return path


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", TALK)
def test_a_pr_comment_waits_for_closure_of_the_run(tmp_path, host, command):
    """The E3 breadcrumb that opens `gh pr create` does not open a comment."""
    repo = _repo(tmp_path / "r")
    _session(repo)
    _breadcrumb(repo / ".context", head=head_sha(repo))
    denied, out = _guard(repo, command, host)
    assert denied and f"no R5 closure breadcrumb for run {RUN}" in out and "CADENCE_ALLOW_PR=1" in out
    _closure(repo / ".context", head=head_sha(repo))
    assert not _guard(repo, command, host)[0]


def test_a_closure_of_an_older_head_or_of_another_run_keeps_the_comment_denied(tmp_path):
    repo = _repo(tmp_path / "r")
    _session(repo)
    old = head_sha(repo)
    own = _closure(repo / ".context", head=old)
    new = commit_file(repo, "a fix after R5")
    assert f"R5 passed at {old}, HEAD is {new}" in str(_decide(repo, "gh pr comment 1 -b x"))
    own.unlink()
    _closure(repo / ".context", OTHER, head=new)
    assert f"no R5 closure breadcrumb for run {RUN}" in str(_decide(repo, "gh pr review 1 --approve"))


def test_a_review_of_head_does_not_open_the_comment(tmp_path):
    repo = _repo(tmp_path / "r")
    pr_review(repo / ".context", head_sha(repo))
    assert _decide(repo, "gh pr create --fill", host="cursor") is None
    assert "no Cadence run in this worktree" in str(_decide(repo, "gh pr comment 1 -b x", host="cursor"))


def test_the_comment_override_is_allowed_and_logged(tmp_path):
    repo = _repo(tmp_path / "r")
    assert _decide(repo, "CADENCE_ALLOW_PR=1 gh pr review 2 --approve") is None
    log = (repo / ".context" / "issues" / "pr-guard-overrides.log").read_text(encoding="utf-8")
    assert "gh pr review 2" in log


def test_a_review_from_a_base_branch_with_no_run_is_allowed(tmp_path):
    repo = _repo(tmp_path / "r", ahead=0)
    assert _decide(repo, "gh pr review 55 --approve") is None


def test_a_comment_and_a_create_segment_each_need_their_own_pass(tmp_path):
    repo = _repo(tmp_path / "r")
    _session(repo)
    _closure(repo / ".context", head=head_sha(repo))
    assert "E3" in str(_decide(repo, "gh pr comment 1 -b x && gh pr create --fill"))
    _breadcrumb(repo / ".context", head=head_sha(repo))
    assert _decide(repo, "gh pr comment 1 -b x && gh pr create --fill") is None


@pytest.mark.parametrize("flag", [(), ("--check",)])
def test_closure_writes_the_breadcrumb_that_the_guard_reads(tmp_path, flag):
    repo = _repo(tmp_path / "r")
    _session(repo)
    passing_acceptance(repo / ".context", repo)
    done = run_py(ROOT / "scripts" / "closure.py", *flag, cwd=repo)
    assert done.returncode == 0, done.stderr
    crumb = repo / ".context" / "gates" / f"closure-{RUN}.json"
    assert crumb.is_file() is not bool(flag)
    assert (_decide(repo, "gh pr comment 1 -b x") is None) is not bool(flag)
