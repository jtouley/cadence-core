"""R4 closes only after a review of the current head: review -> fix -> re-review.

The eval (cadence-workspace, iteration 1) found 2 of 3 cadence runs fixed R1
findings and closed with no review of the fixed head: R4 was a no-op that always
exited 0. ship-feature requires a final /pr-review-update consolidate. R4 now
fails while a must-fix is open, or while the newest PR review does not cite HEAD.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import code_paths, commit_file, context_ignored_repo, head_sha, make_session, pr_review, run_py

ROOT = Path(__file__).resolve().parents[2]


def _repo(tmp_path: Path) -> Path:
    repo = context_ignored_repo(tmp_path / "r", "feature/gh-1-x", "feat: phase 1")
    make_session(repo / ".context", workspace=str(repo))
    return repo


def _r4(repo: Path) -> subprocess.CompletedProcess[str]:
    return run_py("scripts/final_consolidate.py", "--check", cwd=repo)


def test_r4_passes_when_the_newest_review_cites_head(tmp_path):
    repo = _repo(tmp_path)
    pr_review(repo / ".context", head_sha(repo, 7))
    assert _r4(repo).returncode == 0


def test_r4_fails_after_a_fix_commit_until_the_head_is_reviewed_again(tmp_path):
    repo = _repo(tmp_path)
    pr_review(repo / ".context", head_sha(repo, 7))
    commit_file(repo, "fix: review finding", "fix.py", "X = 1\n")
    head = head_sha(repo, 7)
    proc = _r4(repo)
    assert proc.returncode == 1
    assert "/pr-review-update" in proc.stdout + proc.stderr and head in proc.stdout + proc.stderr
    pr_review(repo / ".context", head)
    assert _r4(repo).returncode == 0


def test_r4_fails_without_any_pr_review(tmp_path):
    assert _r4(_repo(tmp_path)).returncode == 1


def test_r4_fails_while_a_must_fix_is_open(tmp_path):
    repo = _repo(tmp_path)
    pr_review(repo / ".context", head_sha(repo, 7))
    row = {"id": "gh-1-F1", "stage": "pr-review-local", "rule_ref": "core:x", "class": "bug", "severity": "must-fix",
           "location": "a.py:1", "evidence": "repro", "status": "open", "check": "pytest"}
    (repo / ".context" / "findings.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    proc = _r4(repo)
    assert proc.returncode == 1 and "gh-1-F1" in proc.stdout + proc.stderr


def test_r4_reads_the_last_reviewed_commit_line_not_any_mention(tmp_path):
    """A HEAD sha quoted in an older section is not a review of HEAD."""
    repo = _repo(tmp_path)
    old = head_sha(repo, 7)
    commit_file(repo, "fix: finding", "fix.py", "X = 1\n")
    head = head_sha(repo, 7)
    path = repo / ".context" / "prs" / "reviews" / "PR_REVIEW_#1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"RUN_BY_COMMAND: pr-review-local\nnote: next head will be {head}\nLast reviewed commit: {old}\n", encoding="utf-8")
    assert _r4(repo).returncode == 1


def test_r4_on_claude_needs_an_attested_review(tmp_path):
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))
    import hashlib

    import peer_attest

    repo = _repo(tmp_path)
    session = repo / ".context" / "cadence-session.json"
    data = json.loads(session.read_text(encoding="utf-8")); data["host"] = "claude"
    session.write_text(json.dumps(data), encoding="utf-8")
    pr_review(repo / ".context", head_sha(repo, 7))
    assert _r4(repo).returncode == 1  # the orchestrator could have written it
    path = repo / ".context" / "prs" / "reviews" / "PR_REVIEW_#1.md"
    peer_attest.record(repo / ".context", "run-1", "prs/reviews/PR_REVIEW_#1.md", "peer-1",
                       hashlib.sha256(path.read_bytes()).hexdigest())
    assert _r4(repo).returncode == 0


def test_both_bundled_review_commands_record_the_reviewed_commit():
    for name in ("pr-review-local.md", "pr-review-update.md"):
        text = (ROOT / "commands" / name).read_text(encoding="utf-8")
        assert "Last reviewed commit:" in text, name
        assert "prs/reviews/PR_REVIEW_" in text, name


def _two_commits(tmp_path: Path) -> tuple[Path, str, str]:
    repo = _repo(tmp_path)
    old = head_sha(repo)
    return repo, old, commit_file(repo, "fix: finding", "fix.py", "X = 1\n")


def _write_review(repo: Path, body: str, name: str = "PR_REVIEW_#1.md") -> Path:
    path = repo / ".context" / "prs" / "reviews" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("RUN_BY_COMMAND: pr-review-local\n" + body + "\n", encoding="utf-8")
    return path


# Seam rows: R4 end to end on a real repo. The full form cross product is a unit
# test of labeled_lines (tests/gates/test_labeled_lines_reviewed_commit.py).
@pytest.mark.parametrize("line", [
    "**Last reviewed commit**: `{head}`",
    "> - last reviewed commit {head}",
    "## LAST REVIEWED COMMIT: {head}",
])
def test_r4_reads_the_markdown_forms_that_review_peers_write(tmp_path, line):
    # PR #38 R4: a peer wrote `**Last reviewed commit**: `sha`` for HEAD. The
    # pattern skipped it and read an older plain line, so R4 failed on a reviewed head.
    repo, old, head = _two_commits(tmp_path)
    _write_review(repo, f"Last reviewed commit: {old[:7]}\n" + line.format(head=head[:7]))
    assert _r4(repo).returncode == 0, _r4(repo).stderr


def test_r4_does_not_read_a_line_with_trailing_text(tmp_path):
    """cd-47 D3: the grammar anchors at the end of the line (an intended change)."""
    repo, _old, head = _two_commits(tmp_path)
    _write_review(repo, f"Last reviewed commit: {head[:7]} (after fixes)")
    proc = _r4(repo)
    assert proc.returncode == 1 and "no reviewed-commit line" in proc.stderr


def test_r4_names_the_newest_review_and_the_sha_it_read(tmp_path):
    repo, old, _head = _two_commits(tmp_path)
    import os
    import time

    older = _write_review(repo, f"Last reviewed commit: {old[:7]}", "PR_REVIEW_#1.md")
    newest = _write_review(repo, f"Last reviewed commit: {old[:9]}", "PR_REVIEW_#2.md")
    past = time.time() - 100
    os.utime(older, (past, past))
    proc = _r4(repo)
    assert proc.returncode == 1
    assert "prs/reviews/PR_REVIEW_#2.md" in proc.stderr and old[:9] in proc.stderr
    assert newest.is_file()


# --- gh-66: R4 takes a PR review only from a pr-review-local or pr-review-update peer --------

code_paths()

from verdict_join import peer_wrote, tamper, verdict_join_session  # noqa: E402

RUN = "11111111-1111-4111-8111-111111111111"
BAD_TEXT = ("rewrite", "Write or Edit", "no PR review written by a review peer")


def _joined_repo(tmp_path: Path, branch_kind: str) -> tuple[Path, Path, str]:
    """(repo, .context, review rel) for an R4 branch: `fallback` (no draft PR) or `own-pr`."""
    repo = _repo(tmp_path)
    ctx = repo / ".context"
    verdict_join_session(ctx, RUN, workspace=str(repo))
    if branch_kind == "own-pr":
        record = ctx / "prs" / f"draft-pr-{RUN}.md"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("PR: https://github.com/o/r/pull/7\n", encoding="utf-8")
        return repo, ctx, "prs/reviews/PR_REVIEW_#7.md"
    return repo, ctx, "reviews/pr_x.md"


def _problems(repo: Path, ctx: Path, monkeypatch) -> list[str]:
    import final_consolidate

    monkeypatch.chdir(repo)
    return final_consolidate.head_review_problems(ctx, repo)


def _body(repo: Path) -> str:
    return f"RUN_BY_COMMAND: pr-review-local\nLast reviewed commit: {head_sha(repo, 7)}\nVerdict: APPROVE\n"


@pytest.mark.parametrize("branch_kind", ["fallback", "own-pr"])
@pytest.mark.parametrize("stage", ["pr-review-local", "pr-review-update"])
def test_r4_takes_a_review_peer(tmp_path, monkeypatch, branch_kind, stage):
    repo, ctx, rel = _joined_repo(tmp_path, branch_kind)
    peer_wrote(ctx, RUN, rel, agent_id="a", stage=stage, body=_body(repo))
    assert _problems(repo, ctx, monkeypatch) == []


@pytest.mark.parametrize("branch_kind", ["fallback", "own-pr"])
def test_r4_names_a_wrong_stage_writer(tmp_path, monkeypatch, branch_kind):
    repo, ctx, rel = _joined_repo(tmp_path, branch_kind)
    peer_wrote(ctx, RUN, rel, agent_id="a", stage="spec-driven", body=_body(repo))
    [problem] = _problems(repo, ctx, monkeypatch)
    assert problem == (f"final-consolidate: {rel} written by a spec-driven peer; "
                       "this gate accepts pr-review-local, pr-review-update")
    assert not any(text in problem for text in BAD_TEXT)


@pytest.mark.parametrize("branch_kind", ["fallback", "own-pr"])
def test_r4_before_the_join_row_says_no_join_row(tmp_path, monkeypatch, branch_kind):
    """AC16: the R4 peer runs the check before it returns, so its join row is not there yet."""
    repo, ctx, rel = _joined_repo(tmp_path, branch_kind)
    peer_wrote(ctx, RUN, rel, agent_id="a", stage="pr-review-update", body=_body(repo), join=False)
    [problem] = _problems(repo, ctx, monkeypatch)
    assert "no join row" in problem
    assert not any(text in problem for text in BAD_TEXT)


def test_r4_own_pr_keeps_its_text_for_a_changed_review(tmp_path, monkeypatch):
    repo, ctx, rel = _joined_repo(tmp_path, "own-pr")
    tamper(peer_wrote(ctx, RUN, rel, agent_id="a", stage="pr-review-update", body=_body(repo)))
    [problem] = _problems(repo, ctx, monkeypatch)
    assert "not peer-written (ignored)" in problem


def test_the_r4_peer_command_says_no_join_row_is_information():
    text = (ROOT / "commands" / "pr-review-update.md").read_text(encoding="utf-8")
    assert "no join row" in text and "Do not change the review file for it." in text
