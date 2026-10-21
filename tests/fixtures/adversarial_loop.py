"""One builder for the P3/P6 review-loop tests (session, plan, attested reports, history)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "scripts", ROOT / "shared" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import peer_attest  # noqa: E402

RUN = "11111111-1111-4111-8111-111111111111"
OTHER_RUN = "22222222-2222-4222-8222-222222222222"
SLUG = "gh-1_x"


class Loop:
    """A repo with an active run and a plan; helpers write attested P6 reports."""

    def __init__(self, repo: Path, *, host: str = "claude", run: str = RUN, slug: str = SLUG, **session):
        self.repo, self.run, self.slug, self.host = repo, run, slug, host
        self.ctx = repo / ".context"
        self.ctx.mkdir(parents=True, exist_ok=True)
        data = {"active_skill": "cadence", "task_id": "gh-1", "pipeline_run_id": run, "workspace": str(repo),
                "host": host, "orchestrator_source": "toplevel", **session}
        (self.ctx / "cadence-session.json").write_text(json.dumps(data), encoding="utf-8")
        self.session = data
        self.plan_path = self.ctx / "plans" / f"{slug}.plan.md"
        self.write_plan("")

    def write_plan(self, extra: str) -> Path:
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(
            f"RUN_BY_COMMAND: plan-create\nPipeline-Run: {self.run}\n**Ticket:** gh-1\n\n### Phase 1: x\n### Phase 2: y\n{extra}",
            encoding="utf-8",
        )
        return self.plan_path

    def report(self, verdict: str | None = "REQUEST CHANGES", *, round_n: int = 1, agent: str = "peer-1",
               mode: str | None = "plan-gate", run: str | None = None, body: str = "", attest: bool = True,
               name: str | None = None) -> Path:
        """Write one P6 report and, on Claude Code, attest it as written by `agent`."""
        name = name or (f"adversarial_{self.slug}.md" if round_n == 1 else f"adversarial_{self.slug}_r{round_n}.md")
        path = self.ctx / "adversarial" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Adversarial review", "", "RUN_BY_SKILL: de-adversarial-reviewer"]
        if mode:
            lines.append(f"Mode: {mode}")
        lines.append(f"Pipeline-Run: {run or self.run}")
        lines += ["", body.rstrip("\n")] if body else []
        if verdict is not None:
            lines += ["", f"Verdict: {verdict}"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if attest and self.host == "claude":
            self.attest(path, agent)
        return path

    def attest(self, path: Path, agent: str, run: str | None = None, *, stage: str | None = None) -> None:
        """Attest `path` as written by `agent`. With `stage`, first join the agent to a call of that stage."""
        if stage is not None:
            from verdict_join import peer_call, peer_join

            tool_use_id = f"toolu_{agent}_{stage}"
            peer_call(self.ctx, run or self.run, stage=stage, tool_use_id=tool_use_id)
            peer_join(self.ctx, run or self.run, tool_use_id=tool_use_id, agent_id=agent)
        rel = path.relative_to(self.ctx).as_posix()
        peer_attest.record(self.ctx, run or self.run, rel, agent, hashlib.sha256(path.read_bytes()).hexdigest())

    def history(self, *commands: str | None, stage: str = "de-adversarial-reviewer") -> None:
        path = self.ctx / "subagent-runs" / self.run / "history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            for command in commands:
                entry = {"stage": stage, "started_at": "t"}
                if command is not None:
                    entry["command"] = command
                log.write(json.dumps(entry) + "\n")
