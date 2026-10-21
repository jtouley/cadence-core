#!/usr/bin/env python3
"""Record and verify subagent delegation provenance for /cadence.

Cadence: cadence-session.json; stages.json slugs; --skill cadence on register.

Orchestrator: `begin` before Task(subagent), `complete` after artifact exists
and manifest register returns an entry id.

Sidecar layout:
  .context/subagent-runs/<pipeline_run_id>/<stage>.json

Usage:
  python3 subagent_provenance.py begin --stage plan-create --command "/plan-create TICKET"
  python3 subagent_provenance.py complete --stage plan-create \\
    --artifact .cursor/plans/foo.plan.md --manifest-id abc12345
  python3 subagent_provenance.py check [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
_LOCAL_SHARED = _ROOT / "shared" / "scripts"
if _LOCAL_SHARED.is_dir() and str(_LOCAL_SHARED) not in sys.path:
    sys.path.insert(0, str(_LOCAL_SHARED))

os.environ.setdefault("CADENCE_HOME", str(_ROOT))

try:
    from context_root import context_dir as canonical_context_dir  # type: ignore
except ImportError:  # pragma: no cover

    def canonical_context_dir(cwd: Path | None = None) -> Path:
        return (cwd or Path.cwd()).resolve() / ".context"


SESSION_NAME = "cadence-session.json"
SKILL_NAME = "cadence"

PLAN_DELEGATED = (
    "plan-create",
    "plan-review",
    "plan-update",
    "de-adversarial-reviewer",
)

REVIEW_DELEGATED = (
    "pr-review-local",
    "test-benchmark",
)

PROVENANCE_MARKERS = {
    "plan-create": ("RUN_BY_COMMAND: plan-create",),
    "plan-review": ("RUN_BY_COMMAND: plan-review",),
    "plan-update": ("RUN_BY_COMMAND: plan-update",),
    "de-adversarial-reviewer": ("RUN_BY_SKILL: de-adversarial-reviewer",),
    "pr-review-local": ("RUN_BY_COMMAND: pr-review-local", "RUN_BY_SKILL: pr-review"),
    "test-benchmark": ("RUN_BY_SKILL: test-benchmark",),
    "vibe-test": ("RUN_BY_SKILL: vibe-test",),
}


def ctx_root() -> Path:
    return canonical_context_dir(Path.cwd())


def load_session() -> dict:
    """The run the gates read from this working directory: the worktree's own run,
    else the run the root redirect names."""
    from pipeline_run_binding import current_session

    data = current_session(ctx_root())
    return data if isinstance(data, dict) else {}


def pipeline_run_id(session: dict | None = None) -> str | None:
    session = session if session is not None else load_session()
    run_id = session.get("pipeline_run_id")
    return str(run_id) if run_id else None


def run_record_path(run_id: str, stage: str) -> Path:
    from prompt_shape import delegation_record_path

    return delegation_record_path(ctx_root(), run_id, stage)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def has_marker(text: str, marker: str) -> bool:
    return marker in text


def _plans_dir() -> Path:
    cwd = Path.cwd()
    for candidate in (cwd / ".cursor" / "plans", cwd / "plans"):
        if candidate.is_dir():
            return candidate
    return cwd / ".cursor" / "plans"


def _any_plan_has_marker(marker: str) -> bool:
    plans = _plans_dir()
    if not plans.is_dir():
        return False
    for path in plans.glob("*.plan.md"):
        try:
            if marker in path.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _adversarial_artifact_present() -> bool:
    root = ctx_root()
    adv_dir = root / "adversarial"
    if adv_dir.is_dir() and any(adv_dir.glob("*.md")):
        return True
    reviews = root / "reviews"
    if reviews.is_dir():
        for pattern in ("adversarial_*.md", "*adversarial*.md", "*_plan-gate.md"):
            if any(reviews.glob(pattern)):
                return True
    return False


def required_stages() -> list[str]:
    stages: list[str] = ["plan-create", "plan-review"]
    if _any_plan_has_marker("RUN_BY_COMMAND: plan-update"):
        stages.append("plan-update")
    if _adversarial_artifact_present():
        stages.append("de-adversarial-reviewer")
    stages.extend(REVIEW_DELEGATED)
    return stages


def cmd_begin(stage: str, command: str, subagent_id: str | None) -> int:
    session = load_session()
    run_id = pipeline_run_id(session)
    if not run_id:
        print("FAIL  no pipeline_run_id — run activate_session.py first", file=sys.stderr)
        return 1
    from prompt_shape import hook_records_only

    if hook_records_only(session):
        print(
            "FAIL  on this Claude Code run the hook records the peer call; "
            "launch the peer with the lean slash command (do not run begin)",
            file=sys.stderr,
        )
        return 1

    # The gate reads the record under the stage the command maps to. A record
    # filed under another name can never clear it, so refuse it here.
    from prompt_shape import delegation_stage_for_prompt

    mapped = delegation_stage_for_prompt(command)
    if mapped and mapped != stage:
        print(
            f"FAIL  --stage {stage!r} does not match the command; `{command.split()[0]}` records as --stage {mapped!r}",
            file=sys.stderr,
        )
        return 1

    lean_covered = set(PLAN_DELEGATED) | set(REVIEW_DELEGATED) | {"vibe-test"}
    if stage in lean_covered:
        try:
            from prompt_shape import PromptShapeError, validate_lean_prompt

            validate_lean_prompt(stage, command)
        except PromptShapeError as exc:
            print(f"FAIL  lean prompt shape: {exc}", file=sys.stderr)
            return 1
        except ImportError:
            print("FAIL  prompt_shape module missing", file=sys.stderr)
            return 1

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "stage": stage,
        "command": command,
        "pipeline_run_id": run_id,
        "task_id": session.get("task_id", ""),
        "subagent_id": subagent_id or "",
        "status": "running",
        "started_at": now,
        "completed_at": "",
        "artifact_path": "",
        "manifest_entry_id": "",
    }
    path = run_record_path(run_id, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    # The record above is overwritten by each begin. The history keeps one line per
    # begin, so a gate can count cycles (the plan-review limit reads it).
    with (path.parent / "history.jsonl").open("a", encoding="utf-8") as history:
        # The command tells a plan-gate P6 round from a spec-pressure one.
        history.write(json.dumps({"stage": stage, "started_at": now, "command": command}) + "\n")
    print(f"OK  subagent run started: stage={stage} run_id={run_id}")
    return 0


def cmd_complete(
    stage: str,
    artifact: str,
    manifest_id: str,
    subagent_id: str | None,
    register: bool,
    artifact_type: str,
) -> int:
    from prompt_shape import hook_records_only

    session = load_session()
    run_id = pipeline_run_id(session)
    if not run_id:
        print("FAIL  no pipeline_run_id", file=sys.stderr)
        return 1
    hook_only = hook_records_only(session)
    path = run_record_path(run_id, stage)
    prior: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            prior = loaded
        elif hook_only:
            print(f"FAIL  unreadable hook record for {stage}: {path}", file=sys.stderr)
            return 1
    elif hook_only:
        print(
            f"FAIL  no hook record for {stage}: launch the peer with the lean slash command "
            "(the PreToolUse hook writes it; if no record appears, the plugin hooks did not run)",
            file=sys.stderr,
        )
        return 1
    artifact_path = Path(artifact)
    if not artifact_path.is_file():
        print(f"FAIL  artifact missing: {artifact}", file=sys.stderr)
        return 1
    text = read_text(artifact_path)
    markers = PROVENANCE_MARKERS.get(stage, ())
    if markers and not any(has_marker(text, m) for m in markers):
        print(
            f"FAIL  {artifact} missing provenance marker ({' or '.join(markers)})",
            file=sys.stderr,
        )
        return 1

    entry_id = manifest_id
    if register and not entry_id:
        manifest_script = Path.home() / ".cursor" / "scripts" / "manifest.py"
        if not manifest_script.is_file():
            print(f"FAIL  manifest.py not found at {manifest_script}", file=sys.stderr)
            return 1
        rel = str(artifact_path)
        try:
            rel = str(artifact_path.resolve().relative_to(Path.cwd().resolve()))
        except ValueError:
            pass
        proc = subprocess.run(
            [
                "python3",
                str(manifest_script),
                "register",
                "--skill",
                SKILL_NAME,
                "--path",
                rel,
                "--type",
                artifact_type or stage,
                "--task",
                str(session.get("task_id", "")),
                "--tags",
                f"subagent,{stage},pipeline_run:{run_id}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(f"FAIL  manifest register: {proc.stderr or proc.stdout}", file=sys.stderr)
            return 1
        try:
            payload = json.loads(proc.stdout)
            entry_id = str(payload.get("id") or payload.get("entry", {}).get("id") or "")
        except json.JSONDecodeError:
            entry_id = ""

    # Keep every field the earlier writer set (source, command, started_at, ...).
    record = {
        "stage": stage,
        "command": "",
        "pipeline_run_id": run_id,
        "started_at": "",
        **prior,
        "task_id": session.get("task_id", ""),
        "subagent_id": subagent_id or "",
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "artifact_path": str(artifact_path),
        "manifest_entry_id": entry_id or "",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"OK  subagent run complete: stage={stage} artifact={artifact}")
    return 0


def cmd_check(*, as_json: bool) -> int:
    needed = required_stages()
    session = load_session()
    run_id = pipeline_run_id(session)
    missing: list[str] = []
    if not run_id:
        missing = list(needed)
    else:
        for stage in needed:
            path = run_record_path(run_id, stage)
            if not path.is_file():
                missing.append(stage)
                continue
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                missing.append(stage)
                continue
            if rec.get("status") != "complete":
                missing.append(stage)
    payload = {"ok": not missing, "required": needed, "missing": missing, "run_id": run_id}
    if as_json:
        print(json.dumps(payload, indent=2))
    elif payload["ok"]:
        print(f"OK  provenance complete ({len(needed)} stages)")
    else:
        print(f"FAIL  missing provenance: {', '.join(missing)}", file=sys.stderr)
    return 0 if payload["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Cadence subagent provenance")
    sub = parser.add_subparsers(dest="cmd", required=True)

    begin = sub.add_parser("begin")
    begin.add_argument("--stage", required=True)
    begin.add_argument("--command", required=True)
    begin.add_argument("--subagent-id", default="")

    complete = sub.add_parser("complete")
    complete.add_argument("--stage", required=True)
    complete.add_argument("--artifact", required=True)
    complete.add_argument("--manifest-id", default="")
    complete.add_argument("--subagent-id", default="")
    complete.add_argument("--register", action="store_true")
    complete.add_argument("--type", dest="artifact_type", default="")

    check = sub.add_parser("check")
    check.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.cmd == "begin":
        return cmd_begin(args.stage, args.command, args.subagent_id or None)
    if args.cmd == "complete":
        return cmd_complete(
            args.stage,
            args.artifact,
            args.manifest_id,
            args.subagent_id or None,
            args.register,
            args.artifact_type,
        )
    if args.cmd == "check":
        return cmd_check(as_json=args.json)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
