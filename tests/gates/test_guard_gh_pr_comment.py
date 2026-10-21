"""guard_gh_pr_comment.run blocks open must-fix findings, checks the two tables of the comment body, and fails closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import code_paths, ledger_row, write_ledger

code_paths()
from guard_gh_pr_comment import run  # noqa: E402


# --- guard_gh_pr_comment: guard_gh_pr_comment.run blocks open must-fix findings.

def _body(context: Path, *, fixes=(), deferrals=(), run_id: str | None = None, text: str | None = None) -> Path:
    """The R6 comment body: a `## Fixes` table (finding, commit) and a `## Deferrals` table (finding, ticket)."""
    def table(head: str, rows) -> str:
        return f"| Finding | {head} |\n|---|---|\n" + "".join(f"| {a} | {b} |\n" for a, b in rows)

    path = context / "prs" / "comments" / (f"pr-comment-{run_id}.md" if run_id else "pr-comment.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else
                    f"Closure: 0 open must-fix.\n\n## Fixes\n\n{table('Commit', fixes)}\n## Deferrals\n\n{table('Ticket', deferrals)}",
                    encoding="utf-8")
    return path


OPEN = ledger_row("gh-1-F01", rule_ref="core:x", location="src/x.py",
                  evidence="open must-fix still blocks the PR comment", check="uv run pytest tests/gates/test_guard_gh_pr_comment.py")


def test_guard_gh_pr_comment_open_must_fix_fails(scripts_path, tmp_path, capsys):
    context = tmp_path / ".context"
    write_ledger(context / "findings.jsonl", OPEN)
    code = run(context)
    err = capsys.readouterr().err
    assert code == 1
    assert "must-fix [gh-1-F01] at src/x.py" in err
    assert not (context / "gates" / "guard-gh-pr-comment.json").exists()


def test_guard_gh_pr_comment_no_open_must_fix_passes(scripts_path, tmp_path, capsys):
    context = tmp_path / ".context"
    verified = {**OPEN, "id": "gh-1-F02", "status": "verified"}
    write_ledger(context / "findings.jsonl", verified)
    _body(context)
    code = run(context)
    err = capsys.readouterr().err
    breadcrumb = context / "gates" / "guard-gh-pr-comment.json"
    assert code == 0, err
    assert breadcrumb.is_file()
    assert '"open_must_fix_count": 0' in breadcrumb.read_text(encoding="utf-8")


def _json_line(text: str) -> dict:
    import json

    return json.loads([line for line in text.splitlines() if line.startswith("{")][-1])


def _r6_ledgers(env, own_status: str = "open"):
    write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-902-F1"), ledger_row("zz-1-F1"),
                 ledger_row("zz-1-F2", severity="nit"))
    own = env.ctx / "findings" / "cd-901.jsonl"
    own.unlink(missing_ok=True)
    extra = {"ticket": "#1"} if own_status == "deferred" else {}
    write_ledger(own, ledger_row("cd-901-F1", status=own_status, **extra))


def test_r6_judges_only_the_runs_task(scripts_path, two_worktree_runs, capsys):
    """cd-49: R6 of run A reads both ledger layouts and judges only the rows of task cd-901."""
    env = two_worktree_runs
    _r6_ledgers(env)
    assert run(env.ctx, cwd=env.a) == 1
    err = capsys.readouterr().err
    assert "cd-901-F1" in err and "cd-902-F1" not in err
    summary = _json_line(err)
    assert summary["scope"] == "task" and summary["other_task_skipped"] == 1 and summary["unowned"] == ["zz-1-F1"]
    (env.ctx / "findings.jsonl").unlink()
    _r6_ledgers(env, own_status="deferred")
    _body(env.ctx, deferrals=[("cd-901-F1", "#1")], run_id=env.run_a)
    assert run(env.ctx, check=True, cwd=env.a) == 0
    summary = _json_line(capsys.readouterr().out)
    assert summary["scope"] == "task" and summary["other_task_skipped"] == 1 and summary["task_id"] == "cd-901"
    assert run(env.ctx, cwd=env.a) == 0
    breadcrumb = (env.ctx / "gates" / "guard-gh-pr-comment.json").read_text(encoding="utf-8")
    assert '"other_task_skipped": 1' in breadcrumb


def test_r6_with_no_run_judges_every_row(scripts_path, two_worktree_runs, capsys):
    env = two_worktree_runs
    _r6_ledgers(env, own_status="deferred")
    assert run(env.ctx, cwd=env.primary) == 1
    assert _json_line(capsys.readouterr().err)["scope"] == "all"


def test_r6_corrupt_row_of_another_task_fails(scripts_path, two_worktree_runs):
    import json

    env = two_worktree_runs
    ledger = write_ledger(env.ctx / "findings.jsonl", ledger_row("cd-901-F1", status="deferred", ticket="#1"))
    bad = {k: v for k, v in ledger_row("cd-902-F1").items() if k != "evidence"}
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(bad) + "\n")
    assert run(env.ctx, cwd=env.a) == 1


# --- guard_gh_pr_comment_corrupt_ledger_fails_closed: R6 guard fails closed when the findings ledger cannot be parsed.

def test_guard_gh_pr_comment_corrupt_ledger_fails_closed(tmp_path):
    findings = tmp_path / "findings"
    findings.mkdir()
    (findings / "t.jsonl").write_text(
        '{"id":"F1","severity":"must-fix","status":"op', encoding="utf-8"
    )
    assert run(tmp_path, check=True) == 1


# --- gh-178: the comment maps each fix to its commit and each deferral to its ticket, as the ledger has them.

SHA = "a" * 40
FIXED = {**OPEN, "id": "gh-1-F03", "status": "verified", "fixed_in": SHA}
DEFERRED = {**OPEN, "id": "gh-1-F04", "status": "deferred", "ticket": "#7"}


def _closed_ledger(tmp_path: Path) -> Path:
    context = tmp_path / ".context"
    write_ledger(context / "findings.jsonl", FIXED, DEFERRED, {**OPEN, "id": "gh-1-F05", "severity": "nit"})
    return context


@pytest.mark.parametrize("fixes", [[("gh-1-F03", SHA)], [("gh-1-F03", f"`{SHA[:7]}`")]])
def test_the_two_tables_match_the_ledger(scripts_path, tmp_path, fixes):
    context = _closed_ledger(tmp_path)
    _body(context, fixes=fixes, deferrals=[("gh-1-F04", "#7")])
    assert run(context, check=True) == 0


@pytest.mark.parametrize("fixes, deferrals, want", [
    ([], [("gh-1-F04", "#7")], "`## Fixes` has no row for gh-1-F03"),
    ([("gh-1-F03", "b" * 40)], [("gh-1-F04", "#7")], f"`## Fixes` row gh-1-F03 names {'b' * 40}, the ledger has {SHA}"),
    ([("gh-1-F03", SHA[:6])], [("gh-1-F04", "#7")], "`## Fixes` row gh-1-F03 names aaaaaa"),
    ([("gh-1-F03", SHA)], [], "`## Deferrals` has no row for gh-1-F04"),
    ([("gh-1-F03", SHA)], [("gh-1-F04", "#8")], "`## Deferrals` row gh-1-F04 names #8, the ledger has #7"),
    ([("gh-1-F03", SHA), ("gh-1-F09", SHA)], [("gh-1-F04", "#7")], "`## Fixes` row gh-1-F09 is not"),
    ([("gh-1-F03", SHA)], [("gh-1-F04", "#7"), ("gh-1-F03", "#7")], "`## Deferrals` row gh-1-F03 is not"),
])
def test_a_table_that_does_not_match_the_ledger_fails(scripts_path, tmp_path, capsys, fixes, deferrals, want):
    context = _closed_ledger(tmp_path)
    _body(context, fixes=fixes, deferrals=deferrals)
    assert run(context, check=True) == 1
    assert want in capsys.readouterr().err
    assert not (context / "gates" / "guard-gh-pr-comment.json").exists()


@pytest.mark.parametrize("text, want", [
    (None, "no comment body at"),
    ("## Fixes\n\n| Finding | Commit |\n|---|---|\n| gh-1-F03 | " + SHA + " |\n", "has no `## Deferrals` table"),
    ("Closure summary only.\n", "has no `## Fixes` table"),
])
def test_a_missing_body_or_table_fails_closed(scripts_path, tmp_path, capsys, text, want):
    context = _closed_ledger(tmp_path)
    if text is not None:
        _body(context, text=text)
    assert run(context, check=True) == 1
    assert want in capsys.readouterr().err


def test_the_body_file_flag_names_another_file(scripts_path, tmp_path):
    context = _closed_ledger(tmp_path)
    good = _body(context, fixes=[("gh-1-F03", SHA)], deferrals=[("gh-1-F04", "#7")])
    moved = good.rename(tmp_path / "body.md")
    assert run(context, check=True) == 1
    assert run(context, check=True, body=moved) == 0


# --- without --check, R6 writes the body from the ledger. No agent writes it.

def test_without_check_r6_writes_the_body_from_the_ledger_and_names_the_post(scripts_path, tmp_path, capsys):
    context = _closed_ledger(tmp_path)
    assert run(context, cwd=tmp_path) == 0
    body = (context / "prs" / "comments" / "pr-comment.md").read_text(encoding="utf-8")
    assert "| gh-1-F03 | aaaaaaaaaaaa |" in body and "| gh-1-F04 | #7 |" in body
    assert "- gh-1-F05" in body and body.startswith("## Closure summary")
    assert "NEXT: gh pr comment <N> --body-file " in capsys.readouterr().out
    assert run(context, check=True, cwd=tmp_path) == 0


def test_without_check_r6_replaces_a_hand_written_body(scripts_path, tmp_path):
    context = _closed_ledger(tmp_path)
    _body(context, fixes=[("gh-1-F03", "b" * 40)], deferrals=[])
    assert run(context, cwd=tmp_path) == 0
    assert "b" * 40 not in (context / "prs" / "comments" / "pr-comment.md").read_text(encoding="utf-8")


def test_with_check_r6_writes_nothing(scripts_path, tmp_path):
    context = _closed_ledger(tmp_path)
    assert run(context, check=True, cwd=tmp_path) == 1
    assert not (context / "prs" / "comments").exists()


def test_an_open_must_fix_writes_no_body(scripts_path, tmp_path):
    context = tmp_path / ".context"
    write_ledger(context / "findings.jsonl", OPEN)
    assert run(context, cwd=tmp_path) == 1
    assert not (context / "prs" / "comments").exists()


def test_the_body_lists_the_assumptions_that_the_register_logs(scripts_path, tmp_path):
    from guard_gh_pr_comment import render_body

    body = render_body([FIXED], {"G-1": ["G-1", "days 1 to 28", "phase 2"]})
    assert "## Assumptions" in body and "| G-1 | days 1 to 28 | phase 2 |" in body
    assert "## Assumptions" not in render_body([FIXED], {})
