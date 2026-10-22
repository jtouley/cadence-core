"""The shell hook denies three routes past the evidence of a run.

1. The agent stamps a `manual` check itself (`findings_io.py set-status --human --by <name>`).
2. The agent gives a commit a forged author time (`git commit --date`, `GIT_AUTHOR_DATE`).
3. The agent writes two or more commits in one shell call (the phases of one shell).

The check rule (`python3 -c pass`) is in tests/gates/test_findings_ledger.py.
"""

from __future__ import annotations

import pytest

from conftest import SESSION_LAYOUTS, git_repo, hook_session, tool_decision

GATE = "guard_skill_gate.py"
HOSTS = ("cursor", "claude")


@pytest.fixture(params=SESSION_LAYOUTS)
def run_repo(request, tmp_path):
    repo = git_repo(tmp_path / "repo")
    hook_session(repo, request.param, resume_slug="implement")
    return repo


@pytest.fixture
def bare_repo(tmp_path):
    return git_repo(tmp_path / "bare")


@pytest.fixture
def decide(run_hook):
    def _decide(repo, command: str, host: str) -> tuple[str, str]:
        if host == "claude":
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        else:
            payload = {"hook_event_name": "beforeShellExecution", "command": command}
        code, out, err = run_hook(GATE, repo, payload, host)
        assert code == 0, err
        return tool_decision(host, out), out

    return _decide


HUMAN_STAMPS = [
    'python3 scripts/findings_io.py set-status --id gh-1-F03 --status fixed --fixed-in abc1234 --human --by "Jo"',
    'python3 "$C/findings_io.py" set-status --id gh-1-F03 --status fixed --human --by Jo',
    "uv run python scripts/findings_io.py --ledger x.jsonl set-status --id T-F1 --status fixed --human --by=Jo",
    "python3 -c \"import findings_io; findings_io.main(['set-status', '--id', 'T-F1', '--status', 'fixed', "
    "'--human', '--by', 'Jo'])\"",
    "cd scripts && python3 findings*.py set-status --id T-F1 --status fixed --human --by Jo",
    "python3 f.py set-status --id T-F1 --status fixed --human --by Jo",
]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", HUMAN_STAMPS)
def test_the_agent_cannot_stamp_a_manual_check(run_repo, bare_repo, decide, command, host):
    for repo in (run_repo, bare_repo):
        decision, out = decide(repo, command, host)
        assert decision == "deny", repo
        assert "own terminal" in out


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", [
    "python3 -c \"import findings_io as f; f.set_status(p, 'gh-1-F3', 'fixed', confirmed_by='Jo')\"",
    "python3 -c \"import json; p = '.context/findings.jsonl'; r = {'human_confirmed': True}\"",
    "jq '.confirmed_by = \"Jo\"' .context/findings.jsonl",
    "node -e \"row.human_confirmed = true\" .context/findings/gh-1.jsonl",
])
def test_the_agent_cannot_write_the_stamp_fields_in_a_run(run_repo, decide, command, host):
    assert decide(run_repo, command, host)[0] == "deny"


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", [
    "grep confirmed_by .context/findings.jsonl",
    "grep -n -- --human scripts/findings_io.py",
    "uv run python -m pytest -q tests/gates -k human_confirmed",
    "uv run python -m pytest tests/gates/test_findings_ledger.py -k human_confirmed",
    "python3 -c \"print('confirmed_by')\"",
    "python3 scripts/closure.py --check",
    "python3 scripts/findings_io.py set-status --id gh-1-F01 --status fixed --fixed-in abc1234",
    "python3 scripts/findings_io.py set-status --id gh-1-F01 --status fixed --note 'a human said so'",
])
def test_reads_and_other_ledger_commands_stay_allowed(run_repo, decide, command, host):
    assert decide(run_repo, command, host)[0] == "allow"


FORGED_TIMES = [
    'git commit --date="2026-10-20 10:00:01" -m "phase 2: b"',
    "git commit --amend --no-edit --date=now",
    "git -C . commit -qm 'phase 2: b' --date @1700000000",
    "GIT_AUTHOR_DATE=@1700000001 git commit -m 'phase 2: b'",
    "export GIT_COMMITTER_DATE='2026-10-20T10:00:00'; git commit -m x",
    "env GIT_AUTHOR_DATE=@1 git commit-tree HEAD^{tree} -m x",
    "git filter-branch --env-filter 'export GIT_AUTHOR_DATE=@1' HEAD",
    "faketime '2026-10-20 10:00:00' git commit -m 'phase 2: b'",
    "git commit --dat=2020-01-01 -m x",
    "git commit -m \"a; b\" --date=2020-01-01",
    "git commit -C HEAD~3",
    "git commit -qc HEAD~1",
    "git commit --reuse-message=HEAD~2",
    "bash -c \"git commit -m a --date=2020-01-01\"",
]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", FORGED_TIMES)
def test_a_forged_commit_time_is_denied_in_a_run(run_repo, bare_repo, decide, command, host):
    decision, out = decide(run_repo, command, host)
    assert decision == "deny"
    assert "time" in out
    assert decide(bare_repo, command, host)[0] == "allow"  # no run: not the hook's business


ONE_SHELL = [
    "git commit -m 'phase 1: a' && git commit -m 'phase 2: b'",
    "git add a && git commit -qm 'phase 1: a'; sleep 1; git add b && git commit -qm 'phase 2: b'",
    "for p in 1 2 3; do git commit --allow-empty -m \"phase $p: x\"; done",
    "while read p; do git commit -m \"$p\"; done < phases.txt",
    "seq 3 | xargs -I{} git commit --allow-empty -m 'phase {}: x'",
    "bash -c \"git commit -m a; git commit -m b\"",
    "eval \"git commit -m a\"; git commit -m b",
    "git -C \"my dir\" commit -m a && git -C \"my dir\" commit -m b",
    "sudo git commit -m a && sudo -u me git commit -m b",
    "git rebase -x 'git commit --allow-empty -m phase' HEAD~3",
    "git commit-tree t1 -m a && git commit-tree t2 -m b",
]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", ONE_SHELL)
def test_two_commits_in_one_shell_call_are_denied_in_a_run(run_repo, bare_repo, decide, command, host):
    decision, out = decide(run_repo, command, host)
    assert decision == "deny"
    assert "one shell call" in out
    assert decide(bare_repo, command, host)[0] == "allow"


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", [
    "git add -A && git commit -m 'phase 1: a' && git push -u origin HEAD",
    "git commit -qm 'phase 2: b' 2>&1 | tail -1",
    "git log --format='%at %s' main..HEAD",
    "git show --date=iso HEAD",
    "git log --date=relative -3",
    "git rebase --continue",
    "git cherry-pick a1b2c3d e4f5a6b",
    "grep -n 'git commit' docs/notes.md",
    "git commit -m \"fix --date parsing\"",
    "git commit -m 'phase 1: -C flag' -q",
    "git commit -F msg.txt --cleanup=strip",
])
def test_one_commit_and_date_reads_stay_allowed(run_repo, decide, command, host):
    assert decide(run_repo, command, host)[0] == "allow"
