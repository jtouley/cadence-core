#!/usr/bin/env python3
"""Pre-flight check and pipeline resume detection for /cadence.

Reads stages.json as the sole stage order source. Resume is a slug + block,
never a fractional legacy stage id.

Usage:
    python3 pipeline_status.py
    python3 pipeline_status.py --ticket TASK_ID
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from load_stages import SKILL_ROOT, load_stages  # noqa: E402

_SHARED = Path(__file__).resolve().parent.parent / "shared" / "scripts"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

try:
    from context_root import context_dir as canonical_context_dir  # type: ignore
except ImportError:  # pragma: no cover
    def canonical_context_dir(cwd=None):
        return Path.cwd() / ".context"


@dataclass
class PreflightResult:
    tool: str
    ok: bool
    detail: str


@dataclass
class BlockArtifact:
    block: str
    slug: str
    found: bool
    path: str | None = None
    detail: str = ""


@dataclass
class PipelineStatus:
    branch: str
    ticket_id: str | None
    preflight: list[PreflightResult]
    artifacts: list[BlockArtifact]
    resume_slug: str
    resume_block: str
    resume_reason: str
    registry_path: str
    warnings: list[str] = field(default_factory=list)


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


def get_branch() -> str:
    r = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def preflight_tools() -> list[PreflightResult]:
    results: list[PreflightResult] = []
    for tool in ("git", "gh", "python3", "uv"):
        path = shutil.which(tool)
        results.append(
            PreflightResult(tool=tool, ok=bool(path), detail=path or "not found")
        )
    return results


def artifact_hints_for(slug: str, context_dir: Path) -> tuple[bool, str | None, str]:
    """Best-effort presence checks keyed by slug (v1 heuristic)."""
    mapping: dict[str, list[Path]] = {
        "validate": [context_dir / "vibetest"],
        "plan-create": [Path.cwd() / ".cursor" / "plans"],
        "plan-review": [context_dir / "reviews"],
        "adversarial-review": [context_dir / "adversarial"],
        "test-benchmark-plan": [context_dir / "test-benchmarks"],
        "deferral-register": [context_dir / "deferrals"],
        "implement": [context_dir / "implementation"],
        "draft-pr": [],
        "pr-review-local": [context_dir / "reviews"],
        "closure": [context_dir / "findings"],
    }
    paths = mapping.get(slug, [])
    for p in paths:
        if p.is_dir() and any(p.iterdir()):
            return True, str(p), "non-empty directory"
        if p.is_file():
            return True, str(p), "file present"
    if slug == "activate-session":
        sess = context_dir / "cadence-session.json"
        if sess.is_file():
            return True, str(sess), "session file"
    return False, None, "not found"


def build_status(ticket_id: str | None) -> PipelineStatus:
    context_dir = canonical_context_dir()
    stages = load_stages(SKILL_ROOT)
    artifacts: list[BlockArtifact] = []
    resume_slug = stages[0]["slug"]
    resume_block = stages[0]["block"]
    resume_reason = "No completed blocks detected — start at first registry row"
    last_found_idx = -1

    for idx, row in enumerate(stages):
        found, path, detail = artifact_hints_for(row["slug"], context_dir)
        artifacts.append(
            BlockArtifact(
                block=row["block"],
                slug=row["slug"],
                found=found,
                path=path,
                detail=detail,
            )
        )
        if found:
            last_found_idx = idx

    if last_found_idx >= 0 and last_found_idx + 1 < len(stages):
        nxt = stages[last_found_idx + 1]
        resume_slug = nxt["slug"]
        resume_block = nxt["block"]
        resume_reason = (
            f"Last found artifact at {stages[last_found_idx]['block']} "
            f"({stages[last_found_idx]['slug']}); resume next"
        )
    elif last_found_idx == len(stages) - 1:
        resume_slug = stages[-1]["slug"]
        resume_block = stages[-1]["block"]
        resume_reason = "All registry artifact hints present — at R7 / session-close"

    return PipelineStatus(
        branch=get_branch(),
        ticket_id=ticket_id,
        preflight=preflight_tools(),
        artifacts=artifacts,
        resume_slug=resume_slug,
        resume_block=resume_block,
        resume_reason=resume_reason,
        registry_path=str(SKILL_ROOT / "stages.json"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cadence pipeline status (registry-driven)")
    parser.add_argument("--ticket", help="Ticket / task id hint")
    parser.add_argument("--branch", help="Unused compatibility flag")
    args = parser.parse_args()
    status = build_status(args.ticket)
    print(json.dumps(asdict(status), indent=2))
    print(
        f"Resume {status.resume_block} ({status.resume_slug}): {status.resume_reason}",
        file=sys.stderr,
    )
    print(f"Registry: {status.registry_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
