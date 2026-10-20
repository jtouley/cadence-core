"""After P9 the plan document is frozen (harness-benchmark S10 `clobber-plan-document`).

The probe state and payloads are those of jtouley/harness-benchmark: `probe` runs every hook
that the host fragment registers for the call, on both hosts. Before P9, `/plan-update` writes the plan.
"""

from __future__ import annotations

import pytest

from conftest import (PROBE_PLAN, assert_decision, hook_session, p9_marker, probe_repo, skill_gate_hook,
                      tool_decision, write_files)

HOSTS = ["claude", "cursor"]
FROZEN = ("BLOCKED: the plan is frozen while the P9 marker exists (.context/gates/entry-invariant.ok). "
          "A plan change in execution is a new review cycle.")
ACTION = "ACTION: stop and tell the user what the plan must change; do not edit the plan"


@pytest.mark.parametrize("host", HOSTS)
def test_the_probe_plan_stub_is_denied_after_p9(tmp_path, probe, host):
    verdict, texts = probe(host, probe_repo(tmp_path / "proj", "executing"), "write", PROBE_PLAN)
    assert verdict == "block"
    text = next(t for t in texts if FROZEN in t)
    assert PROBE_PLAN in text and ACTION in text


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("kind, target", [
    pytest.param("write", PROBE_PLAN, id="write"),
    pytest.param("write", ".context/plans/feature.plan.md", id="write-context-plans"),
    pytest.param("shell", f"sed -i 's/Step/Task/' {PROBE_PLAN}", id="sed-i"),
    pytest.param("shell", f"cat > {PROBE_PLAN} <<'EOF'\n# Plan\nEOF", id="cat-heredoc"),
    pytest.param("shell", "echo TBD > .context/plans/feature.plan.md", id="echo-redirect"),
    pytest.param("shell", f"cd src && sed -i 's/Step/Task/' ../{PROBE_PLAN}", id="cd-backstop"),
])
def test_a_plan_write_is_denied_after_p9_and_allowed_before(tmp_path, probe, host, kind, target):
    executing = probe(host, probe_repo(tmp_path / "exec", "executing"), kind, target)
    assert executing[0] == "block"
    assert any("the plan is frozen" in t for t in executing[1]), executing[1]
    assert probe(host, probe_repo(tmp_path / "plan", "planning"), kind, target)[0] == "allow"


@pytest.mark.parametrize("host", HOSTS)
def test_the_per_run_marker_freezes_the_plan_too(tmp_path, run_hook, host):
    repo = probe_repo(tmp_path / "proj", "none")
    hook_session(repo)
    write_files(repo, {str(p9_marker(repo).relative_to(repo)): "{}\n", PROBE_PLAN: "# Plan\n"})
    payload = {"tool_name": "Edit", "cwd": str(repo),
               "tool_input": {"file_path": PROBE_PLAN, "path": PROBE_PLAN, "old_string": "Plan", "new_string": "X"}}
    reason = "the plan is frozen while the P9 marker exists (.context/gates/entry-invariant-run-1.ok)"
    assert_decision(run_hook("guard_skill_gate.py", repo, payload, host), host, "deny", reason=reason)


def test_a_marker_of_another_run_does_not_freeze_the_plan(tmp_path):
    repo = probe_repo(tmp_path / "proj", "planning")
    write_files(repo, {".context/gates/entry-invariant.ok": '{"pipeline_run_id": "run-0", "task_id": "gh-1"}\n'})
    payload = {"tool_name": "Write", "cwd": str(repo), "tool_input": {"file_path": PROBE_PLAN, "content": "# Plan\n"}}
    proc = skill_gate_hook(repo, payload)
    assert proc.returncode == 0 and tool_decision("claude", proc.stdout) == "allow", proc.stdout


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("legacy, want", [
    pytest.param("", "allow", id="empty"),
    pytest.param("zzz\n", "allow", id="not-json"),
    pytest.param("{}\n", "allow", id="names-no-run"),
    pytest.param('{"pipeline_run_id": "run-0", "task_id": "gh-1"}\n', "allow", id="another-run"),
    pytest.param('{"pipeline_run_id": "run-1", "task_id": "gh-2"}\n', "allow", id="another-task"),
    pytest.param('{"pipeline_run_id": "run-1", "task_id": "gh-1"}\n', "deny", id="this-run"),
])
def test_the_legacy_marker_freezes_the_plan_only_when_it_names_this_run(tmp_path, run_hook, host, legacy, want):
    """PR #168 R1 N-4: a stale legacy file (git-ignored, it survives an upgrade) must not block P1 of a new run."""
    repo = probe_repo(tmp_path / "proj", "planning")
    write_files(repo, {".context/gates/entry-invariant.ok": legacy})
    payload = {"tool_name": "Write", "cwd": str(repo), "tool_input": {"file_path": PROBE_PLAN, "content": "# Plan\n"}}
    reason = "the plan is frozen" if want == "deny" else None
    assert_decision(run_hook("guard_skill_gate.py", repo, payload, host), host, want, reason=reason)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("target, want, reason", [
    pytest.param(".context/findings.jsonl", "block", "is pipeline state written only by", id="ledger-stays-protected"),
    pytest.param(".context/notes.md", "allow", None, id="other-context-file-stays-open"),
    pytest.param("docs/plans/feature.plan.md", "allow", None, id="a-plans-dir-elsewhere"),
])
def test_a_non_plan_write_keeps_its_old_rule(tmp_path, probe, host, target, want, reason):
    verdict, texts = probe(host, probe_repo(tmp_path / "proj", "executing"), "write", target)
    assert verdict == want
    assert reason is None or any(reason in t for t in texts), texts
