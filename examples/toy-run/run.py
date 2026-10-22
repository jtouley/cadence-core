#!/usr/bin/env python3
"""Run Cadence's real gates and hooks on a toy repository. No model, no network.

One change (add ``subtract()`` to a toy package) goes from A1 to E3 with the Cursor
hooks. The script plays each peer: it sends the peer's slash command through the host
hook, then writes the artifact that the peer's doc names. Every verdict comes from a gate
script in ``stages.json`` or a hook in ``hooks/``. E4 needs a draft PR on GitHub, so the
walk stops there. Then the script runs the findings ledger of the review phase on its
own, and P3 again with the Claude Code hooks.

Usage::

    python3 examples/toy-run/run.py              # print the transcript
    python3 examples/toy-run/run.py --keep DIR   # also keep the sandbox in DIR

The transcript replaces the sandbox path, the checkout path and the run id with
``<sandbox>``, ``<cadence>`` and ``<run>``, so two runs print the same text.
``transcript.txt`` next to this file is the committed output.

Stdlib only. Writes only in a temporary directory (or ``--keep DIR``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOY = ROOT / "tests" / "fixtures" / "toy_repo"
BRANCH = "feature/gh-12-add-subtract"
TASK = "cd-12"
PLAN = f"plans/{TASK}.plan.md"
CORE = "src/toy_pkg/core.py"


class Stopped(Exception):
    """A hook denied a call that the run needs. The script stops there, as an agent would."""


class Sandbox:
    """A toy repository with its own HOME, and the text that the transcript hides."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.repo = base / "toy"
        self.ctx = self.repo / ".context"
        self.run_id = ""
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(base / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "toy", "GIT_AUTHOR_EMAIL": "toy@example.com",
            "GIT_COMMITTER_NAME": "toy", "GIT_COMMITTER_EMAIL": "toy@example.com",
            "GIT_AUTHOR_DATE": "2026-10-19T12:00:00Z", "GIT_COMMITTER_DATE": "2026-10-19T12:00:00Z",
        }

    def sh(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(args, cwd=self.repo, env=self.env, input=stdin,
                              capture_output=True, text=True, timeout=300, check=False)

    def py(self, script: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return self.sh(sys.executable, str(ROOT / script), *args, stdin=stdin)

    def git(self, *args: str) -> None:
        done = self.sh("git", *args)
        if done.returncode != 0:
            raise SystemExit(f"git {' '.join(args)} failed: {done.stderr}")

    def head(self) -> str:
        return self.sh("git", "rev-parse", "HEAD").stdout.strip()

    def write(self, rel: str, body: str) -> None:
        path = self.ctx / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def hide(self, text: str) -> str:
        for real, mark in ((sys.executable, "python3"), (str(self.base), "<sandbox>"),
                           (str(ROOT), "<cadence>"), (self.run_id, "<run>")):
            if real:
                text = text.replace(real, mark)
        return text


def say(box: Sandbox, text: str = "") -> None:
    print(box.hide(text))


def heading(box: Sandbox, title: str) -> None:
    say(box)
    say(box, f"== {title}")


# --- the host side: hooks, as Cursor runs them (hooks/hooks-cursor.json) ---------------

def task(box: Sandbox, prompt: str) -> None:
    """A Task call: the preToolUse hook checks the lean prompt and records the delegation."""
    payload = {"tool_name": "Task", "tool_input": {"prompt": prompt},
               "cwd": str(box.repo), "workspace_roots": [str(box.repo)]}
    done = box.py("hooks/guard_lean_task_prompt.py", stdin=json.dumps(payload))
    verdict = hook_answer(done).get("permission", "?")
    say(box, f"  Task {prompt!r:39} {verdict}")
    expect(verdict, "allow", f"the Task hook on {prompt!r}")


def expect(got: str, want: str, what: str) -> None:
    """Every hook verdict and gate result the run depends on goes through here.

    The transcript claims what Cadence does. When a hook or a gate does something else,
    the run stops and exits 1 instead of printing a claim that is no longer true.
    """
    if got != want:
        raise Stopped(f"{what}: expected {want}, got {got}")


def edit(box: Sandbox, path: str, want: str) -> None:
    """A file write through the edit guard (Cursor: Write)."""
    payload = {"tool_name": "Write", "tool_input": {"path": path},
               "cwd": str(box.repo), "workspace_roots": [str(box.repo)]}
    expect(_guard(box, f"Edit {path}", payload), want, f"the edit guard on {path}")


def shell(box: Sandbox, command: str, want: str) -> None:
    payload = {"hook_event_name": "beforeShellExecution", "command": command,
               "cwd": str(box.repo), "workspace_roots": [str(box.repo)]}
    expect(_guard(box, f"Shell {command!r}", payload), want, f"the edit guard on {command!r}")


def read(box: Sandbox, path: str, want: str) -> None:
    """A file read through the secret-read guard (Cursor: Read)."""
    payload = {"tool_name": "Read", "tool_input": {"path": path},
               "cwd": str(box.repo), "workspace_roots": [str(box.repo)]}
    expect(_guard(box, f"Read {path}", payload, "hooks/guard_secret_read.py"), want, f"the secret-read guard on {path}")


def hook_answer(done: subprocess.CompletedProcess) -> dict:
    """The hook's JSON decision. A hook that fails closed (non-zero exit, no JSON) is a deny."""
    try:
        answer = json.loads(done.stdout) if done.returncode == 0 else None
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, dict):
        return {"permission": "deny", "agent_message": f"hook exit {done.returncode}: the host blocks"}
    return answer


def _guard(box: Sandbox, label: str, payload: dict, hook: str = "hooks/guard_skill_gate.py") -> str:
    done = box.py(hook, stdin=json.dumps(payload))
    answer = hook_answer(done)
    verdict = answer.get("permission", "?")
    say(box, f"  {label:44} {verdict.upper() if verdict == 'deny' else verdict}")
    reason = answer.get("agent_message") or answer.get("user_message") or ""
    if verdict == "deny" and reason:
        say(box, f"    {reason.splitlines()[0]}")
    return verdict


# --- the peers: each writes the artifact its doc names ----------------------------------

def run_line(box: Sandbox) -> str:
    return f"Pipeline-Run: {box.run_id}\n"


def play_plan_create(box: Sandbox) -> None:
    task(box, f"/plan-create {TASK}")
    box.write(PLAN, f"# Plan: {TASK}\n\n**RUN_BY_SKILL:** plan-create\nTicket: #12\n{run_line(box)}\n"
              "1. Add subtract(a, b) to src/toy_pkg/core.py.\n2. Add tests/test_core.py.\n")
    # The plan comes with a check of the request. P9 pins it, and R5 runs it.
    check = box.repo / "acceptance" / "check_subtract.py"
    check.parent.mkdir(exist_ok=True)
    check.write_text("import sys\nsys.path.insert(0, 'src')\nfrom toy_pkg.core import subtract\n"
                     "assert subtract(5, 3) == 2 and subtract(2, 5) == -3\n", encoding="utf-8")
    box.write(f"acceptance/{TASK}.json", json.dumps({"RUN_BY_COMMAND": "plan-create", "task_id": TASK, "checks": [
        {"id": "AC-1", "claim": "subtract(a, b) returns a - b", "run": "python3 acceptance/check_subtract.py"}]}))
    box.git("add", "acceptance/check_subtract.py")  # P1 commits its check scripts
    box.git("commit", "-qm", "test: acceptance checks of the request")


def play_plan_review(box: Sandbox) -> None:
    body = (f"# Plan review: {TASK}\n\n**RUN_BY_COMMAND:** plan-review\nTicket: #12\n{run_line(box)}"
            "**Verdict:** READY TO EXECUTE\n")
    say(box, "  The orchestrator writes the review itself, with no peer call:")
    box.write(f"reviews/plan_{TASK}.md", body)
    show_gate(box, "scripts/plan_review_gate.py", passes=False)
    say(box, "  Now the orchestrator calls the peer, and the peer writes nothing:")
    task(box, f"/plan-review {PLAN}")
    say(box, "  On Cursor, no hook records who wrote a verdict, so the gate takes this file (V4).")


def play_implement(box: Sandbox) -> None:
    task(box, f"/spec-driven {TASK}")
    edit(box, CORE, "allow")
    core = box.repo / CORE
    core.write_text(core.read_text(encoding="utf-8")
                    + "\n\ndef subtract(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    tests = box.repo / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_core.py").write_text(
        "from toy_pkg.core import add, subtract\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_subtract():\n    assert subtract(5, 3) == 2\n", encoding="utf-8")
    # A check runs a test or a script, not inline `python3 -c` code.
    (tests / "run_one.py").write_text(
        '"""Run one test of test_core.py: python3 tests/run_one.py <name>."""\nimport sys\n\nimport test_core\n\n'
        "getattr(test_core, sys.argv[1])()\n", encoding="utf-8")
    box.git("add", "-A")
    box.git("commit", "-qm", "feat: subtract()")
    say(box, "  committed: feat: subtract()")


# The default run visits the core blocks only. These are its peers.
PEERS = {"P1": play_plan_create, "P3": play_plan_review, "E1": play_implement}


# --- the gates ---------------------------------------------------------------------------

def run_script(box: Sandbox, script: str, *args: str) -> None:
    done = box.py(script, *args)
    say(box, f"  ran {Path(script).name} {' '.join(args)}".rstrip() + f": exit {done.returncode}")


def show_gate(box: Sandbox, script: str, passes: bool) -> None:
    done = box.py(script)
    say(box, f"  gate {Path(script).name}: exit {done.returncode}")
    for line in (done.stdout + done.stderr).strip().splitlines()[1:]:
        say(box, f"    {line.strip()}")
    expect("pass" if done.returncode == 0 else "fail", "pass" if passes else "fail", Path(script).name)


def next_block(box: Sandbox) -> dict:
    done = box.py("scripts/headless.py", "next", "--json")
    return json.loads(done.stdout)


def print_open(box: Sandbox, state: dict, seen: set[str]) -> None:
    for row in state["results"]:
        if row["ok"] and row["block"] not in seen:
            seen.add(row["block"])
            say(box, f"OK    {row['block']:3} {row['slug']}")
    failed = state["results"][-1]
    say(box, f"FAIL  {failed['block']:3} {failed['slug']}")
    for line in failed["output"].strip().splitlines()[1:]:
        say(box, f"        {line.strip()}")


def walk(box: Sandbox) -> dict:
    """Open block by open block: headless next names it, its peer or script clears it."""
    seen: set[str] = set()
    done: set[str] = set()
    while True:
        state = next_block(box)
        print_open(box, state, seen)
        block = state["next"]["block"]
        if block in done:
            return state
        done.add(block)
        if block in PEERS:
            PEERS[block](box)
        elif state["next"]["kind"] == "script":
            run_script(box, *state["next"]["run"].split()[1:])
        else:
            return state


# --- the run -----------------------------------------------------------------------------

def make_repo(box: Sandbox) -> None:
    shutil.copytree(TOY, box.repo)
    (box.base / "home").mkdir()
    box.git("init", "-q", "-b", "main")
    box.git("add", "-A")
    box.git("commit", "-qm", "toy package")
    box.git("checkout", "-qb", BRANCH)
    (box.repo / ".env").write_text("API_KEY=toy-not-a-real-key\n", encoding="utf-8")


def activate(box: Sandbox) -> None:
    make_repo(box)
    heading(box, f"A1  Activate on branch {BRANCH}")
    box.py("scripts/activate_session.py", "--from-branch", "--fresh")
    status = json.loads(box.py("scripts/activate_session.py", "--status").stdout)
    box.run_id = status["pipeline_run_id"]
    say(box, f"  task {status['task_id']}, run <run>, resume at {status['resume_block']}")


LEDGER = ".context/findings.jsonl"


def ledger(box: Sandbox) -> None:
    heading(box, "R4-R5  The findings ledger, on its own (no PR)")
    row = f"{TASK}-F1"
    finding = {"id": row, "stage": "pr-review-local", "task_id": TASK,
               "pipeline_run_id": box.run_id, "rule_ref": "core:test-coverage", "class": "bug",
               "severity": "must-fix", "location": "tests/test_core.py",
               "evidence": "no test for a result below zero",
               "check": "PYTHONPATH=src python3 tests/run_one.py test_subtract_below_zero",
               "status": "open"}
    step(box, "A reviewer files a finding, check `true`", 1, "scripts/findings_io.py", "append", "--json",
         json.dumps({**finding, "check": "true"}))
    step(box, "A reviewer files a must-fix finding", 0, "scripts/findings_io.py", "append", "--json", json.dumps(finding))
    step(box, "Closure", 1, "scripts/closure.py", "--check")
    step(box, "The agent sets it to verified", 1, "scripts/findings_io.py", "set-status", "--id", row, "--status", "verified")
    fixed = ("scripts/findings_io.py", "set-status", "--id", row, "--status", "fixed")
    step(box, "The agent sets it to fixed, with no commit", 1, *fixed)
    step(box, "The agent sets it to fixed, with no fix", 0, *fixed, "--fixed-in", box.head())
    step(box, "ledger_verify.py runs the check of the row", 1, "scripts/ledger_verify.py", "--ledger", LEDGER)
    step(box, "Closure", 1, "scripts/closure.py", "--check")
    with (box.repo / "tests" / "test_core.py").open("a", encoding="utf-8") as tests:
        tests.write("\n\ndef test_subtract_below_zero():\n    assert subtract(2, 5) == -3\n")
    box.git("commit", "-qam", "test: subtract() below zero")
    say(box, "  The agent adds the missing test and commits it.")
    step(box, "The agent sets fixed_in to the fix commit", 0, *fixed, "--fixed-in", box.head())
    step(box, "ledger_verify.py runs the check again", 0, "scripts/ledger_verify.py", "--ledger", LEDGER)
    step(box, "Closure", 0, "scripts/closure.py", "--check")


def step(box: Sandbox, label: str, want: int, script: str, *args: str) -> None:
    """Run one ledger script; print its exit code and the line that says why."""
    done = box.py(script, *args)
    lines = (done.stdout + done.stderr).strip().splitlines()
    reasons = [ln.strip() for ln in lines if ln.strip().startswith(("- ", "error:", "RuntimeError:"))]
    if reasons:
        why = reasons[0]
    elif lines and lines[-1].startswith("{"):
        record = json.loads(lines[-1])
        why = next((f"{key}: {record[key]}" for key in ("status", "verified", "ok") if key in record), lines[-1])
    else:
        why = lines[-1] if lines else ""
    say(box, f"  {label + ':':44} exit {done.returncode}  {why}")
    expect(f"exit {done.returncode}", f"exit {want}", f"{Path(script).name} ({label})")


# --- P3 with the Claude Code hooks (hooks/hooks.json) ----------------------------------

def claude_hook(box: Sandbox, hook: str, label: str, payload: dict, want: str) -> None:
    """One PreToolUse hook, as Claude Code runs it: no output allows, JSON can deny."""
    payload = {"hook_event_name": "PreToolUse", "session_id": "orchestrator", "cwd": str(box.repo), **payload}
    done = box.py(f"hooks/{hook}", "--host", "claude", stdin=json.dumps(payload))
    if done.returncode != 0:
        raise Stopped(f"{hook} exited {done.returncode}")
    answer = json.loads(done.stdout).get("hookSpecificOutput", {}) if done.stdout.strip() else {}
    verdict = answer.get("permissionDecision", "allow")
    say(box, f"  {label:44} {verdict.upper() if verdict == 'deny' else verdict}")
    if verdict == "deny":
        say(box, f"    {answer.get('permissionDecisionReason', '').splitlines()[0]}")
    expect(verdict, want, f"{hook} on {label.strip()}")


def claude_write(box: Sandbox, rel: str, body: str, agent: str | None) -> None:
    """A Write by the orchestrator (no agent_id) or by a peer. The host writes only on allow."""
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(box.ctx / rel), "content": body}}
    if agent:
        payload["agent_id"] = agent
    want = "allow" if agent else "deny"
    claude_hook(box, "guard_skill_gate.py", f"Write .context/{rel}", payload, want)
    if want == "allow":
        box.write(rel, body)


def claude_activate(box: Sandbox) -> None:
    """A1 through the Bash tool, so the hook notes the orchestrator before activation."""
    command = f"python3 {ROOT / 'scripts' / 'activate_session.py'} --from-branch --fresh"
    claude_hook(box, "guard_skill_gate.py", "Bash activate_session.py",
                {"tool_name": "Bash", "tool_input": {"command": command}}, "allow")
    box.py("scripts/activate_session.py", "--from-branch", "--fresh")
    status = json.loads(box.py("scripts/activate_session.py", "--status").stdout)
    box.run_id = status["pipeline_run_id"]
    say(box, f"  task {status['task_id']}, run <run>")


def claude_code(base: Path) -> None:
    """P3 again with the Claude Code hooks: the write hook records which agent wrote a verdict."""
    box = Sandbox(base)
    base.mkdir()
    box.env.update({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "orchestrator"})
    make_repo(box)
    heading(box, "P3 again, with the Claude Code hooks (hooks/hooks.json)")
    claude_activate(box)
    box.write(PLAN, f"# Plan: {TASK}\n\n**RUN_BY_SKILL:** plan-create\nTicket: #12\n{run_line(box)}")
    rel = f"reviews/plan_{TASK}.md"
    body = (f"# Plan review: {TASK}\n\n**RUN_BY_COMMAND:** plan-review\nTicket: #12\n{run_line(box)}"
            "**Verdict:** READY TO EXECUTE\n")
    say(box, "  The orchestrator writes the review:")
    claude_write(box, rel, body, None)
    say(box, "  The file appears anyway, for example from a shell write that the hook did not parse:")
    box.write(rel, body)
    prompt = f"/plan-review {PLAN}"
    claude_hook(box, "guard_subagent_handoff.py", f"Agent {prompt!r}",
                {"tool_name": "Agent", "tool_input": {"prompt": prompt, "description": "plan review"}}, "allow")
    say(box, "  The peer call is recorded, but no peer wrote the file:")
    show_gate(box, "scripts/plan_review_gate.py", passes=False)
    say(box, "  The peer writes the same text:")
    claude_write(box, rel, body, "plan-review-peer")
    show_gate(box, "scripts/plan_review_gate.py", passes=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", type=Path, help="keep the sandbox in this new directory")
    args = parser.parse_args(argv)
    if args.keep:
        args.keep.mkdir(parents=True)
        base = args.keep.resolve()
    else:
        base = Path(tempfile.mkdtemp(prefix="cadence-toy-")).resolve()
    box = Sandbox(base)
    try:
        return run(box)
    except Stopped as stop:
        say(box, f"  The toy run stops: {stop}.")
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)


def run(box: Sandbox) -> int:
    say(box, "Cadence toy run: add subtract() to a toy package. No model, no network.")
    activate(box)
    heading(box, "The edit guard, before the plan passed review")
    edit(box, CORE, "deny")
    shell(box, f"echo '# x' >> {CORE}", "deny")
    edit(box, "README.md", "allow")
    read(box, ".env", "deny")
    heading(box, "A1-E4  Gates in stages.json order (scripts/headless.py next)")
    stopped = walk(box)["next"]["block"]
    if stopped != "E4":
        raise Stopped(f"the walk ended at {stopped}, before E4. A gate or a peer changed")
    say(box, "  E4 needs a draft PR on GitHub. The toy run stops here.")
    heading(box, "The plan, after P9")
    edit(box, f".context/{PLAN}", "deny")
    ledger(box)
    claude_code(box.base / "claude")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
