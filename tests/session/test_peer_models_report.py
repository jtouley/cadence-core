"""`peer_models.py report` joins each recorded peer call to its subagent (cd-58 D6).

Claude Code writes <projects>/<project>/<chat>/subagents/agent-<id>.meta.json with the
call's toolUseId, and agent-<id>.jsonl with the served model. The report shows the
requested and served models side by side, so a benchmark can price each peer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import code_paths, git_repo, make_session, run_py

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import peer_models  # noqa: E402

code_paths()
from verdict_join import peer_join  # noqa: E402


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    ctx = repo / ".context"
    make_session(ctx, workspace=str(repo), host="claude", owner_session_id="chat-a",
                 peer_models={"plan-review": "opus", "validate": "haiku"})
    return ctx


@pytest.fixture
def projects(tmp_path):
    return tmp_path / "projects"


def row(ctx, tool_use_id, stage="plan-review", model="opus", decision="allow", session_id="chat-a", run="run-1"):
    peer_models.record_call(ctx, run, {"stage": stage, "block": "P3", "model": model, "subagent_type": "general-purpose",
                                       "decision": decision, "tool_use_id": tool_use_id, "agent_id": None,
                                       "session_id": session_id, "at": 1.0})


def agent(projects: Path, chat: str, agent_id: str, tool_use_id, served: str | None = "claude-opus-5-5") -> None:
    """The subagent of a call: its PostToolUse join row (gh-66) and its host transcript."""
    peer_join(projects.parent / "repo" / ".context", "run-1", tool_use_id=tool_use_id, agent_id=agent_id)
    folder = projects / "proj" / chat / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "x"}})]
    if served:
        lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "model": served}}))
    (folder / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def peer_attest_joins(ctx: Path) -> Path:
    return ctx / "attest" / "run-1.joins.jsonl"


def report(projects: Path, capsys, *extra: str) -> dict:
    assert peer_models.main(["report", "--json", "--projects-dir", str(projects), *extra]) == 0
    return json.loads(capsys.readouterr().out)


def calls(data: dict, slug: str) -> list[dict]:
    return next(s["calls"] for s in data["stages"] if s["slug"] == slug)


def test_lists_slug_configured_requested_agent_and_served(ctx, projects, capsys):
    row(ctx, "toolu_1")
    agent(projects, "chat-a", "a1", "toolu_1")
    data = report(projects, capsys)
    assert data["run_id"] == "run-1"
    [stage] = [s for s in data["stages"] if s["slug"] == "plan-review"]
    assert stage["configured"] == "opus"
    assert stage["calls"] == [{"model": "opus", "subagent_type": "general-purpose", "tool_use_id": "toolu_1",
                               "agent_id": "a1", "served_model": "claude-opus-5-5"}]
    assert peer_models.main(["report", "--projects-dir", str(projects)]) == 0
    text = capsys.readouterr().out
    assert "plan-review" in text and "opus" in text and "toolu_1" in text and "a1" in text and "claude-opus-5-5" in text


def test_a_row_with_no_meta_match_is_unjoined(ctx, projects, capsys):
    row(ctx, "toolu_x")
    assert calls(report(projects, capsys), "plan-review")[0]["agent_id"] == "unjoined"


def test_two_chats(ctx, projects, capsys):
    row(ctx, "toolu_1", session_id="chat-a")
    row(ctx, "toolu_2", session_id="chat-b")
    agent(projects, "chat-a", "a1", "toolu_1")
    agent(projects, "chat-b", "a2", "toolu_2")
    assert [c["agent_id"] for c in calls(report(projects, capsys), "plan-review")] == ["a1", "a2"]


def test_no_session_id_joins_through_the_owner(ctx, projects, capsys):
    row(ctx, "toolu_1", session_id=None)
    agent(projects, "chat-a", "a1", "toolu_1")
    assert calls(report(projects, capsys), "plan-review")[0]["agent_id"] == "a1"


def test_fallback_glob(ctx, projects, capsys):
    row(ctx, "toolu_1", session_id="chat-z")
    agent(projects, "chat-q", "a9", "toolu_1")
    assert calls(report(projects, capsys), "plan-review")[0]["agent_id"] == "a9"


def test_denied_rows_are_not_calls(ctx, projects, capsys):
    row(ctx, "toolu_1", model="sonnet", decision="deny")
    assert calls(report(projects, capsys), "plan-review") == []


def test_served_unknown(ctx, projects, capsys):
    row(ctx, "toolu_1")
    agent(projects, "chat-a", "a1", "toolu_1", served=None)
    assert calls(report(projects, capsys), "plan-review")[0]["served_model"] == "unknown"


def test_two_calls_one_slug_in_row_order(ctx, projects, capsys):
    row(ctx, "toolu_1")
    row(ctx, "toolu_2")
    assert [c["tool_use_id"] for c in calls(report(projects, capsys), "plan-review")] == ["toolu_1", "toolu_2"]


def test_unconfigured_slug_prints_a_dash(ctx, projects, capsys):
    data = report(projects, capsys)
    configured = {s["slug"]: s["configured"] for s in data["stages"]}
    assert list(configured) == list(peer_models.PEER_MODEL_SLUGS)
    assert configured["plan-review"] == "opus" and configured["pr-review-local"] == "-"


def test_corrupt_inputs_do_not_crash(ctx, projects, capsys):
    row(ctx, "toolu_1")
    row(ctx, "toolu_2")
    row(ctx, "toolu_3")
    folder = projects / "proj" / "chat-a" / "subagents"
    folder.mkdir(parents=True)
    # The bad files sort first, so a `break` on a bad file would lose the good ones (R2 F31).
    (folder / "agent-0bad.meta.json").write_text("not json", encoding="utf-8")
    (folder / "agent-0noid.meta.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
    peer_join(ctx, "run-1", tool_use_id="toolu_2", agent_id="a2")
    (folder / "agent-a2.jsonl").write_text("not json\n", encoding="utf-8")
    peer_join(ctx, "run-1", tool_use_id="toolu_3", agent_id="a3")
    with peer_attest_joins(ctx).open("a", encoding="utf-8") as handle:
        handle.write("garbage\n")
    with peer_models.rows_path(ctx, "run-1").open("a", encoding="utf-8") as handle:
        handle.write("garbage\n")
    found = calls(report(projects, capsys), "plan-review")
    assert [(c["agent_id"], c["served_model"]) for c in found] == [
        ("unjoined", "unknown"), ("a2", "unknown"), ("a3", "unknown")]


def test_no_rows_file(ctx, projects, capsys):
    assert peer_models.main(["report", "--projects-dir", str(projects)]) == 0
    assert "no peer model rows for run run-1" in capsys.readouterr().out


def test_run_selects_another_run(ctx, projects, capsys):
    row(ctx, "toolu_old", run="run-0")
    row(ctx, "toolu_new")
    data = report(projects, capsys, "--run", "run-0")
    assert data["run_id"] == "run-0"
    assert [c["tool_use_id"] for c in calls(data, "plan-review")] == ["toolu_old"]
    # run-0 has no session record, so the current run's map must not label it (R2 F31).
    assert {s["configured"] for s in data["stages"]} == {"-"}
    data = report(projects, capsys, "--run", "run-1")
    assert next(s["configured"] for s in data["stages"] if s["slug"] == "plan-review") == "opus"


def test_default_projects_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert peer_models._default_projects_dir() == tmp_path / "cfg" / "projects"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert peer_models._default_projects_dir() == tmp_path / "home" / ".claude" / "projects"


def test_cli_json_subprocess(ctx, projects):
    row(ctx, "toolu_1")
    agent(projects, "chat-a", "a1", "toolu_1")
    proc = run_py(ROOT / "scripts" / "peer_models.py", "report", "--json", "--projects-dir", str(projects),
                  cwd=ctx.parent, home=None)
    assert proc.returncode == 0, proc.stderr
    assert calls(json.loads(proc.stdout), "plan-review")[0]["agent_id"] == "a1"


def test_a_bad_transcript_line_before_the_assistant_row_is_skipped(ctx, projects, capsys):
    """Review N-4: `continue` -> `break` on a bad line must not hide the served model."""
    row(ctx, "toolu_1")
    agent(projects, "chat-a", "a1", "toolu_1")
    transcript = projects / "proj" / "chat-a" / "subagents" / "agent-a1.jsonl"
    transcript.write_text("garbage\n" + transcript.read_text(encoding="utf-8"), encoding="utf-8")
    assert calls(report(projects, capsys), "plan-review")[0]["served_model"] == "claude-opus-5-5"


def test_a_deny_row_before_an_allow_row_does_not_hide_it(ctx, projects, capsys):
    """Review N-4: `continue` -> `break` on a deny row must not drop the later allow row."""
    row(ctx, "toolu_0", model="sonnet", decision="deny")
    row(ctx, "toolu_1")
    assert [c["tool_use_id"] for c in calls(report(projects, capsys), "plan-review")] == ["toolu_1"]


def test_a_meta_file_with_no_join_row_is_unjoined(ctx, projects, capsys):
    """gh-66: the report joins through join rows, never through the undocumented host meta file."""
    row(ctx, "toolu_1")
    folder = projects / "proj" / "chat-a" / "subagents"
    folder.mkdir(parents=True)
    (folder / "agent-m1.meta.json").write_text(json.dumps({"toolUseId": "toolu_1"}), encoding="utf-8")
    assert calls(report(projects, capsys), "plan-review")[0]["agent_id"] == "unjoined"


def test_a_join_row_with_no_transcript_reports_its_resolved_model(ctx, projects, capsys):
    row(ctx, "toolu_1")
    peer_join(ctx, "run-1", tool_use_id="toolu_1", agent_id="a1", resolved_model="claude-opus-5-5")
    found = calls(report(projects, capsys), "plan-review")[0]
    assert (found["agent_id"], found["served_model"]) == ("a1", "claude-opus-5-5")
