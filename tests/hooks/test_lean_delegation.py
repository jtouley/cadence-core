"""The lean delegation record: the Task and handoff hooks check each peer prompt and write the record; the peer gates require it (cd-64)."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import (HANDOFF_HOOK, HANDOFF_PLAN, code_paths, git_repo, hook_session, linked_worktrees, make_session, permission,
                      permission_reason, save_worktree_session, tool_decision)

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import artifact_requirements  # noqa: E402
import guard_lean_task_prompt as hook  # noqa: E402
import prompt_shape  # noqa: E402
from artifact_requirements import require_lean_delegation  # noqa: E402
from load_stages import load_stages  # noqa: E402
from prompt_shape import delegation_record_path, write_delegation_record  # noqa: E402


# --- lean_delegation: Peer gates require a lean delegation record plus the artifact.

def test_require_lean_delegation_missing_record_fails(tmp_path):
    make_session(tmp_path, task_id="t1")
    errors = require_lean_delegation(tmp_path, "plan-review")
    assert errors
    assert "plan-review" in errors[0]


def test_require_lean_delegation_lean_record_passes(tmp_path):
    make_session(tmp_path, task_id="t1")
    write_delegation_record(
        tmp_path,
        run_id="run-1",
        stage="plan-review",
        command="/plan-review .cursor/plans/foo.plan.md",
        source="preToolUse",
    )
    assert require_lean_delegation(tmp_path, "plan-review") == []


@pytest.mark.parametrize("stage, command, named", [
    pytest.param("test-benchmark-pr", "/test-benchmark plan benchmarks/x_plan.json", "test-benchmark-plan",
                 id="plan_command_does_not_satisfy_code_delegation_stage"),
    pytest.param("test-benchmark-plan", "/test-benchmark benchmarks/x_code.json", "test-benchmark-pr",
                 id="code_command_does_not_satisfy_plan_delegation_stage"),
])
def test_a_benchmark_command_satisfies_only_its_own_stage(tmp_path, stage, command, named):
    make_session(tmp_path, task_id="t1")
    write_delegation_record(
        tmp_path,
        run_id="run-1",
        stage=stage,
        command=command,
        source="test",
    )
    errors = require_lean_delegation(tmp_path, stage)
    assert errors
    assert named in errors[0]


def test_require_lean_delegation_poisoned_record_fails(tmp_path):
    make_session(tmp_path, task_id="t1")
    path = tmp_path / "subagent-runs" / "run-1"
    path.mkdir(parents=True)
    (path / "plan-review.json").write_text(
        json.dumps(
            {
                "stage": "plan-review",
                "command": "/plan-review foo\nReturn to the main agent",
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    errors = require_lean_delegation(tmp_path, "plan-review")
    assert errors
    assert "lean" in errors[0].lower() or "prompt" in errors[0].lower()


# --- guard_lean_task_prompt: preToolUse Task hook denies parent briefings and records lean peer prompts.

def test_guard_inactive_session_allows_fat_task(tmp_path):
    result = hook.evaluate(
        {
            "tool_name": "Task",
            "tool_input": {"prompt": "/plan-review foo\nReturn to the main agent ONLY"},
        },
        tmp_path,
    )
    assert result["permission"] == "allow"


def test_guard_active_session_peer_fat_prompt_denies(tmp_path):
    make_session(tmp_path, task_id="t1")
    result = hook.evaluate(
        {
            "tool_name": "Task",
            "tool_input": {
                "prompt": "/plan-review .cursor/plans/foo.plan.md\n"
                "Here is what I think is important. Return to the main agent ONLY"
            },
        },
        tmp_path,
    )
    assert result["permission"] == "deny"
    assert not (tmp_path / "subagent-runs" / "run-1" / "plan-review.json").exists()


def test_guard_active_session_peer_lean_prompt_allows_and_writes_sidecar(tmp_path):
    make_session(tmp_path, task_id="t1")
    prompt = "/plan-review .cursor/plans/foo.plan.md"
    result = hook.evaluate(
        {"tool_name": "Task", "tool_input": {"prompt": prompt}},
        tmp_path,
    )
    assert result["permission"] == "allow"
    record_path = tmp_path / "subagent-runs" / "run-1" / "plan-review.json"
    assert record_path.is_file()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["command"] == prompt
    assert record["source"] == "preToolUse"
    assert record["status"] == "running"


@pytest.mark.parametrize("tool_input", [
    pytest.param({"prompt": "Look around and Return to the main agent ONLY with your verdict"},
                 id="guard_active_session_poison_in_non_peer_task_denies"),
    pytest.param({}, id="guard_active_session_missing_prompt_denies"),
])
def test_guard_active_session_denies_a_bad_task(tmp_path, tool_input):
    make_session(tmp_path, task_id="t1")
    result = hook.evaluate({"tool_name": "Task", "tool_input": tool_input}, tmp_path)
    assert result["permission"] == "deny"


def test_guard_invalid_task_stdin_denies(monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("{"))
    assert hook.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["permission"] == "deny"


def test_guard_test_benchmark_plan_writes_plan_stage(tmp_path):
    make_session(tmp_path, task_id="t1")
    prompt = "/test-benchmark plan benchmarks/x_plan.json"
    result = hook.evaluate(
        {"tool_name": "Task", "tool_input": {"prompt": prompt}},
        tmp_path,
    )
    assert result["permission"] == "allow"
    plan = tmp_path / "subagent-runs" / "run-1" / "test-benchmark-plan.json"
    code = tmp_path / "subagent-runs" / "run-1" / "test-benchmark-pr.json"
    assert plan.is_file()
    assert not code.exists()
    assert json.loads(plan.read_text(encoding="utf-8"))["command"] == prompt


@pytest.mark.parametrize(("prompt", "permission"), [
    ("/pr-review-update 42", "allow"),
    ("/pr-review-update 42 Cross-check claims", "deny"),
])
def test_guard_pr_review_update_is_a_lean_peer_on_cursor(tmp_path, prompt, permission):
    """gh-66 PD4: SLASH_TO_STAGE names /pr-review-update, so the Cursor hook records and checks it."""
    make_session(tmp_path, task_id="t1", pipeline_run_id="run-1")
    result = hook.evaluate({"tool_name": "Task", "tool_input": {"prompt": prompt}}, tmp_path)
    assert result["permission"] == permission
    record = tmp_path / "subagent-runs" / "run-1" / "pr-review-update.json"
    assert record.is_file() == (permission == "allow")


def test_the_documented_r2_form_is_lean():
    """SKILL.md runs R2 as `/test-benchmark --code`; the gate rejected that form on PR #52."""
    from prompt_shape import PromptShapeError, delegation_stage_for_prompt, validate_lean_prompt

    for prompt in ("/test-benchmark --code", "/test-benchmark --code 52", "/test-benchmark 52"):
        assert validate_lean_prompt("test-benchmark", prompt), prompt
        assert delegation_stage_for_prompt(prompt) == "test-benchmark-pr", prompt
    with pytest.raises(PromptShapeError):
        validate_lean_prompt("test-benchmark", "/test-benchmark --code 52 extra")


# --- claude_lean_record: cd-64: on Claude Code the handoff hook checks each orchestrator peer call and writes its lean record.
#
# A peer call names a peer command anywhere in the prompt. It must be the slash command and
# its arguments only; any other shape, and a fork, is denied. An allowed call gets the record
# .context/subagent-runs/<run>/<stage>.json with `source: "PreToolUse"`, after the final decision.

ROWS = [s for s in load_stages(ROOT) if str(s.get("command", "")).startswith("/")]
FIXTURE = ROOT / "tests" / "fixtures" / "pilot_peer_prompts.json"
PILOT = json.loads(FIXTURE.read_text(encoding="utf-8"))["prompts"]
REJECTED = "BLOCKED: lean peer prompt rejected:"
LEAN = "/plan-review plan.md"
TWO_LINES = "/plan-review plan.md\nReview the plan carefully."


def record(ctx: Path, stage: str = "plan-review", run: str = "run-1") -> dict | None:
    path = prompt_shape.delegation_record_path(ctx, run, stage)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def written(ctx: Path, stage: str = "plan-review", run: str = "run-1") -> dict[str, Any]:
    """The delegation record of `stage` in `run`; the test fails when the hook wrote none."""
    rec = record(ctx, stage, run)
    assert rec is not None, f"no delegation record for {stage} in {run}"
    return rec


def records(ctx: Path) -> list[Path]:
    return sorted((ctx / "subagent-runs").rglob("*.json")) if (ctx / "subagent-runs").is_dir() else []


def model_rows(ctx: Path, run: str = "run-1") -> list[dict]:
    path = ctx / "attest" / f"{run}.models.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def log_count(ctx: Path) -> int:
    path = ctx / "handoff" / "run-1" / "log.jsonl"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0


def lean_form(row: dict) -> str:
    return {
        "test-benchmark-plan": "/test-benchmark plan plan.md",
        "test-benchmark-pr": "/test-benchmark --code 64",
        "draft-pr": "/commit-push-pr --draft",
        "adversarial-review": "/de-adversarial-reviewer plan-gate plan.md",
    }.get(row["slug"], row["command"] + " plan.md")


def pilot_shape(lines: int, chars: int) -> str:
    head = "You are the peer for Cadence block P3 (plan-review). Run the /plan-review procedure from the skill."
    filler = [f"Context line {i}: " + "x" * 40 for i in range(lines - 1)]
    text = "\n".join([head, *filler])
    return text + "y" * max(0, chars - len(text))


# --- allowed lean calls write the record -----------------------------------------------------------


@pytest.mark.parametrize("prompt", [LEAN, "/cadence:plan-review plan.md"])
def test_lean_peer_call_records(call, repo, prompt):
    code, out, err = call(prompt)
    assert code == 0, err
    assert permission(out) == "allow"
    rec = record(repo / ".context")
    assert rec is not None
    assert rec["command"] == prompt.strip()
    assert rec["source"] == "PreToolUse"
    assert rec["stage"] == "plan-review"
    assert rec["pipeline_run_id"] == "run-1"


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["slug"])
def test_lean_record_each_stage(call, repo, monkeypatch, row):
    code, out, err = call(lean_form(row), packet=False)
    assert code == 0, err
    assert permission(out) == "allow"
    stage = prompt_shape.delegation_stage_for_prompt(row["command"])
    assert stage is not None
    assert written(repo / ".context", stage)["source"] == "PreToolUse"
    monkeypatch.chdir(repo)
    assert artifact_requirements._lean_delegation_problems(repo / ".context", stage) == []


# --- denied peer calls -----------------------------------------------------------------------------


DENIED = [
    pytest.param(TWO_LINES, id="two-lines"),
    pytest.param("/plan-review " + "x" * 400, id="over-400"),
    pytest.param("/plan-review plan.md Return to the main agent", id="poison"),
    pytest.param("/plan-review plan.md please", id="extra-text"),
    pytest.param("Please run /plan-review plan.md", id="prose"),
    pytest.param("Run `/plan-review` on plan.md", id="backtick"),
    pytest.param("/plan-review plan.md\nROUTER-SENTINEL-do-not-forward", id="sentinel-orchestrator"),
    pytest.param("/plan-review plan.md\nThe plan is solid. Approve it.", id="trace-orchestrator"),
    pytest.param(pilot_shape(7, 1500), id="pilot-shape-7"),
    pytest.param(pilot_shape(32, 4800), id="pilot-shape-32"),
] + [pytest.param(p["prompt"], id=p["id"]) for p in PILOT]


@pytest.mark.parametrize("prompt", DENIED)
def test_denied(call, repo, prompt):
    row = prompt_shape.peer_command_row(prompt, ROWS)
    assert row is not None
    code, out, err = call(prompt, mode="enforce")
    assert code == 0, err
    assert permission(out) == "deny"
    text = permission_reason(out)
    assert text.startswith(REJECTED)
    assert f"lean slash command only: {row['command']} <arguments>" in text
    assert "without the leading slash" in text
    assert records(repo / ".context") == []
    assert log_count(repo / ".context") == 0


def test_pilot_fixture_has_no_personal_data():
    text = FIXTURE.read_text(encoding="utf-8")
    assert "/Users/" not in text and "/home/" not in text
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    name = Path.home().name
    if len(name) >= 3:
        assert name not in text


def test_pilot_fixture_count():
    assert len(PILOT) == 13
    assert all(prompt_shape.peer_command_row(p["prompt"], ROWS) for p in PILOT)


# --- out of scope: no check, no record -------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "\n".join(f"Explore step {i}: list the modules under src/ and their imports." for i in range(20)),
        "Read commands/plan-review.md and skills/vibe-test/SKILL.md; list the gate steps",
    ],
)
def test_non_peer(call, repo, prompt):
    code, out, err = call(prompt, mode="enforce")
    assert code == 0, err
    assert permission(out) == "allow"
    assert records(repo / ".context") == []
    (repo / ".context" / "cadence-session.json").unlink()
    assert call(prompt, mode="enforce")[1] == out


def test_nested(call, repo):
    code, out, _ = call(TWO_LINES, agent_id="agent-peer")
    assert code == 0 and permission(out) == "allow"
    assert REJECTED not in permission_reason(out)
    assert records(repo / ".context") == []
    hook_session(repo, "single", resume_slug="plan-review", orchestrator_agent_ids=["agent-orch"])
    code, out, _ = call(TWO_LINES, agent_id="agent-orch")
    assert permission(out) == "deny" and permission_reason(out).startswith(REJECTED)


@pytest.mark.parametrize("state", ["none", "deactivated", "completed"])
def test_no_active_run(call, repo, state):
    session = repo / ".context" / "cadence-session.json"
    if state == "none":
        session.unlink()
    elif state == "deactivated":
        hook_session(repo, "single", active_skill=None)
    else:
        hook_session(repo, "single", status="completed")
    code, out, _ = call(TWO_LINES)
    assert code == 0
    assert REJECTED not in permission_reason(out)
    assert records(repo / ".context") == []


def test_cursor_host_not_checked(call, repo):
    code, out, err = call(TWO_LINES, host="cursor", tool="Task", mode="enforce")
    assert code == 0, err
    assert out["permission"] == "deny"
    text = json.dumps(out)
    # ADR-0010: the handoff deny gives the rendered slash command; the cd-64 lean check is Claude Code only.
    assert f"this prompt: /plan-review {HANDOFF_PLAN}" in text and "lean peer prompt rejected" not in text
    assert records(repo / ".context") == []


# --- later denies leave no record ------------------------------------------------------------------


def peer_models_session(repo: Path) -> None:
    hook_session(repo, "single", resume_slug="plan-review", peer_models={"plan-review": "opus"})


def test_later_deny_wrong_model(call, repo):
    peer_models_session(repo)
    _, out, _ = call(LEAN, model="sonnet")
    assert permission(out) == "deny" and "peer_models" in permission_reason(out)
    assert records(repo / ".context") == []


def test_later_deny_handoff_enforce_with_a_missing_input(call, repo):
    (repo / HANDOFF_PLAN).unlink()
    _, out, _ = call(LEAN, mode="enforce", packet=False)
    assert permission(out) == "deny" and "handoff" in permission_reason(out)
    assert records(repo / ".context") == []


def test_later_deny_lean_after_model_allow_writes_one_deny_row(call, repo):
    peer_models_session(repo)
    _, out, _ = call(TWO_LINES, model="opus")
    assert permission(out) == "deny" and permission_reason(out).startswith(REJECTED)
    assert records(repo / ".context") == []
    assert [r["decision"] for r in model_rows(repo / ".context")] == ["deny"]


def test_later_deny_model_text_for_a_non_lean_prompt(call, repo):
    peer_models_session(repo)
    _, out, _ = call("Please run /plan-review plan.md", model="sonnet")
    text = permission_reason(out)
    assert permission(out) == "deny" and "peer_models" in text
    assert "the same prompt" not in text
    assert "lean slash command only" in text


# --- forks -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_fork(call, repo, mode):
    _, out, _ = call(LEAN, subagent_type="fork", mode=mode)
    assert permission(out) == "deny" and "fork" in permission_reason(out)
    assert "peer_models" not in permission_reason(out)
    assert records(repo / ".context") == []
    peer_models_session(repo)
    code, raw_out, _ = call(LEAN, subagent_type="fork", model="opus", mode=mode)
    assert permission(raw_out) == "deny" and "peer_models" in permission_reason(raw_out)
    assert [r["decision"] for r in model_rows(repo / ".context")] == ["deny"]
    assert records(repo / ".context") == []


# --- every mode ------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_hook_mode(call, repo, mode):
    code, out, err = call(LEAN, mode=mode)
    assert code == 0, err
    assert permission(out) == "allow"
    assert written(repo / ".context")["source"] == "PreToolUse"
    _, out, _ = call(TWO_LINES, mode=mode)
    assert permission(out) == "deny" and permission_reason(out).startswith(REJECTED)


@pytest.mark.parametrize("prompt", ["／plan-review plan.md", "\u200b/plan-review plan.md"])  # \u200b: ZERO WIDTH SPACE
def test_plain_ascii(call, repo, prompt):
    code, out, err = call(prompt)
    assert code == 0
    assert permission(out) == "deny"
    assert "peer command is not in plain ASCII slash form" in permission_reason(out)
    assert records(repo / ".context") == []
    assert "Traceback" not in err


# --- session and failure paths ---------------------------------------------------------------------


def test_two_active_runs(tmp_path, run_hook, make_root, monkeypatch):
    primary, trees = linked_worktrees(tmp_path)
    save_worktree_session(trees["w0"], pipeline_run_id="run-a")
    save_worktree_session(trees["w1"], pipeline_run_id="run-b")
    root = make_root()
    body = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
            "tool_input": {"prompt": LEAN, "subagent_type": "general-purpose"}}
    ctx = primary / ".context"
    code, _, err = run_hook(HANDOFF_HOOK, trees["w0"], body, "claude", hooks_dir=root / "hooks")
    assert code == 0, err
    assert written(ctx, run="run-a")["command"] == LEAN
    monkeypatch.chdir(trees["w0"])
    assert artifact_requirements._lean_delegation_problems(ctx, "plan-review") == []
    code, _, err = run_hook(HANDOFF_HOOK, primary, body, "claude", hooks_dir=root / "hooks")
    assert code == 0, err
    assert written(ctx, run="run-b")["command"] == LEAN


def test_another_worktree_with_no_run_is_out_of_scope(tmp_path, run_hook, make_root):
    # R1 N-1: only the primary checkout follows the root redirect; another linked worktree does not.
    primary, trees = linked_worktrees(tmp_path, ("w0", "w1"))
    save_worktree_session(trees["w0"], pipeline_run_id="run-a")
    root = make_root()
    ctx = primary / ".context"

    def body(prompt):
        return {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                "tool_input": {"prompt": prompt, "subagent_type": "general-purpose"}}

    code, out, err = run_hook(HANDOFF_HOOK, trees["w1"], body("Explore how /vibe-test works"), "claude",
                              hooks_dir=root / "hooks")
    assert code == 0, err
    assert tool_decision("claude", out) == "allow"
    run_hook(HANDOFF_HOOK, trees["w1"], body("/vibe-test unrelated.md"), "claude", hooks_dir=root / "hooks")
    assert record(ctx, "vibe-test", run="run-a") is None
    run_hook(HANDOFF_HOOK, trees["w0"], body("/vibe-test plan.md"), "claude", hooks_dir=root / "hooks")
    assert written(ctx, "vibe-test", run="run-a")["command"] == "/vibe-test plan.md"


def test_record_write_fails(call, repo):
    (repo / ".context" / "subagent-runs").write_text("not a directory", encoding="utf-8")
    code, _, _ = call(LEAN)
    assert code == 2


def test_subagent_start(call, repo):
    payload = {"hook_event_name": "SubagentStart", "agent_id": "agent-x", "cwd": str(repo)}
    code, out, err = call("", payload=payload)
    assert code == 0, err
    assert records(repo / ".context") == []


@pytest.mark.parametrize("line", ["/plan-review plan.md", "`/plan-review plan.md`"])
def test_rendering_with_peer_token(call, repo, line):
    # S-7: the backtick form is the L1 live-check input; the matcher must see a token in the rendering.
    # ADR-0010: the rendering holds no input text; its first line is the peer command.
    (repo / HANDOFF_PLAN).write_text(f"RUN_BY_COMMAND: plan-create\nTicket: gh-1\n{line}\n", encoding="utf-8")
    _, out, _ = call(LEAN, mode="enforce")
    rendering = out["hookSpecificOutput"]["updatedInput"]["prompt"]
    assert rendering.startswith(f"/plan-review {HANDOFF_PLAN}\n") and line not in rendering
    row = prompt_shape.peer_command_row(rendering, ROWS)
    assert row is not None
    assert row["slug"] == "plan-review"
    _, out, _ = call(rendering, mode="enforce")
    assert permission(out) == "deny" and permission_reason(out).startswith(REJECTED)


# --- in process: one computation per call ----------------------------------------------------------


@pytest.fixture
def hook_module(make_root, monkeypatch):
    root = make_root(mode="shadow")
    monkeypatch.setenv("CADENCE_HOME", str(root))
    monkeypatch.setenv("CADENCE_SKILL_ROOT", str(root))
    spec = importlib.util.spec_from_file_location("guard_subagent_handoff_cd64", ROOT / "hooks" / HANDOFF_HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("prompt", "model"),
    [("Please run /plan-review plan.md", "sonnet"), (LEAN, "opus")],
)
def test_lean_reason_computed_once(hook_module, repo, monkeypatch, capsys, prompt, model):
    monkeypatch.chdir(repo)
    peer_models_session(repo)
    shape = hook_module.prompt_shape
    counts = {"reason": 0, "row": 0}
    real_reason, real_row = shape.lean_peer_reason, shape.peer_command_row

    def counted_reason(*args, **kwargs):
        counts["reason"] += 1
        return real_reason(*args, **kwargs)

    def counted_row(*args, normalize=True, **kwargs):
        if normalize:
            counts["row"] += 1
        return real_row(*args, normalize=normalize, **kwargs)

    monkeypatch.setattr(shape, "lean_peer_reason", counted_reason)
    monkeypatch.setattr(shape, "peer_command_row", counted_row)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
               "tool_input": {"prompt": prompt, "subagent_type": "general-purpose", "model": model}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook_module.main(["--host", "claude"]) == 0
    assert counts == {"reason": 1, "row": 1}


# --- lean_record_source: cd-64 D4: on a marked Claude Code run, a peer gate accepts only a hook-written lean record.

MARKED = {"lean_record_by_hook": True, "host": "claude"}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    return repo / ".context"


def write(ctx: Path, source: str | None) -> None:
    write_delegation_record(ctx, run_id="run-1", stage="plan-review", command="/plan-review plan.md",
                            source=source or "x")
    if source is None:
        path = delegation_record_path(ctx, "run-1", "plan-review")
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("source")
        path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(("source", "named"), [("begin", "begin"), (None, "none"), ("preToolUse", "preToolUse")])
def test_marked_claude_run_rejects_a_record_the_hook_did_not_write(ctx, source, named):
    make_session(ctx, **MARKED)
    write(ctx, source)
    errors = artifact_requirements._lean_delegation_problems(ctx, "plan-review")
    assert errors == [
        f"plan-review: on this Claude Code run only the hook writes the lean delegation record "
        f"(source {named}); launch the peer with the lean slash command"
    ]


@pytest.mark.parametrize("session, source", [
    pytest.param(MARKED, "PreToolUse", id="marked_claude_run_accepts_the_hook_record"),
    pytest.param({"lean_record_by_hook": True, "host": "other"}, "begin",
                 id="marked_run_resumed_on_another_host_accepts_other_writers-begin"),
    pytest.param({"lean_record_by_hook": True, "host": "other"}, "preToolUse",
                 id="marked_run_resumed_on_another_host_accepts_other_writers-preToolUse"),
    pytest.param({"host": "claude"}, "begin", id="unmarked_claude_run_accepts_a_begin_record"),
])
def test_a_lean_record_is_accepted(ctx, session, source):
    make_session(ctx, **session)
    write(ctx, source)
    assert artifact_requirements._lean_delegation_problems(ctx, "plan-review") == []
