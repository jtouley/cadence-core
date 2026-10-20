#!/usr/bin/env python3
"""Pack loader: find, check, lock and run packs. Standard library only.

A pack is a directory with a `pack.json` manifest (references/pack-standard.md). The loader
looks for a pack name in three places, and a name in two places fails:

  core     <skill root>/packs/<name>/                       (ships with Cadence)
  user     <user config dir>/packs/<name>/                   (lock: <user config dir>/cadence.lock.json)
  project  <project>/.cadence/packs/<name>/                  (lock: <project>/cadence.lock.json)

A user or project pack loads only when its lock file names it with the sha256 of its files, so a
pack cannot change between the review of a lock and a run. `packs` in the resolved config turns a
pack on (scripts/cadence_config.py).

CLI: python3 scripts/pack_loader.py list   [--workspace DIR]
     python3 scripts/pack_loader.py lock   [--workspace DIR] [--user]   (write the lock of the packs found)
     python3 scripts/pack_loader.py check  [--workspace DIR]            (each enabled pack loads)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cadence_config  # noqa: E402

PHASES = ("planning", "impl", "review")  # P9, E3, R3
V1_KEYS = frozenset({"name", "version", "description", "triggers", "phases", "rules", "gate_script",
                     "reviewer_command", "adr_scope"})
V2_KEYS = V1_KEYS | {"manifest_version", "requires", "config", "stages", "source", "gate_command"}
MANIFEST_KEYS = V2_KEYS
LOCK = "cadence.lock.json"
_PACK_NAME = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_RANGE = re.compile(r"^(>=|<=|>|<|==)?\s*(\d+(?:\.\d+){0,2})$")


@dataclass
class PackRule:
    id: str
    name: str
    severity: str


@dataclass
class Pack:
    name: str
    version: str
    description: str
    path: Path
    triggers: list[dict[str, Any]]
    phases: list[str]
    rules: list[PackRule]
    gate_script: str | None = None
    gate_command: list[str] | None = None
    reviewer_command: str | None = None
    adr_scope: str | None = None
    manifest_version: int = 1
    requires: dict[str, str] = field(default_factory=dict)
    config: dict[str, dict] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, str] = field(default_factory=dict)
    origin: str = "core"

    @property
    def has_gate(self) -> bool:
        return bool(self.gate_script or self.gate_command)


def is_must_fix(finding: dict[str, Any]) -> bool:
    """A finding blocks unless its severity is `nit`: no severity, or a misspelling, fails closed."""
    return finding.get("severity") != "nit"


# --- versions -----------------------------------------------------------------------------------

def core_version(skill_root: Path) -> str:
    return str(json.loads((Path(skill_root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"])


def _version_tuple(text: str) -> tuple[int, int, int]:
    parts = [int(p) for p in text.split(".")]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def version_in_range(version: str, spec: str) -> bool:
    """`spec` is comma-separated comparators: `>=0.1.0, <1.0.0`. A bare version means `==`."""
    match = _SEMVER.match(version)
    if not match:
        raise ValueError(f"version {version!r} is not semver")
    have = _version_tuple(".".join(match.groups()[:3]))
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        found = _RANGE.match(part)
        if not found:
            raise ValueError(f"version range {spec!r} does not parse at {part!r}")
        op, want = found.group(1) or "==", _version_tuple(found.group(2))
        ok = {">=": have >= want, "<=": have <= want, ">": have > want, "<": have < want, "==": have == want}[op]
        if not ok:
            return False
    return True


# --- manifests ----------------------------------------------------------------------------------

def read_pack(manifest_path: Path, origin: str = "core") -> Pack:
    """The pack of `manifest_path`. Every part that is not valid raises ValueError (fail closed)."""
    where = str(manifest_path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{where}: cannot read the manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{where}: the manifest must be an object")
    version = data.get("manifest_version", 1)
    if version not in (1, 2):
        raise ValueError(f"{where}: manifest_version must be 1 or 2, got {version!r}")
    allowed = V2_KEYS if version == 2 else V1_KEYS
    unknown = sorted(set(data) - allowed)
    if unknown:  # a misspelled key (`gate_scrpit`, `phase`) is an error, not a gate that never runs
        raise ValueError(f"{where}: unknown keys {unknown}; the keys of manifest_version {version} are {sorted(allowed)}")
    if data.get("name") != manifest_path.parent.name:
        raise ValueError(f"{where}: name must be the directory name {manifest_path.parent.name!r}, got {data.get('name')!r}")
    if version == 2 and not _SEMVER.match(str(data.get("version", ""))):
        raise ValueError(f"{where}: version must be a semver string such as 1.0.0, got {data.get('version')!r}")
    script, command = data.get("gate_script"), data.get("gate_command")
    if script is not None and command is not None:
        raise ValueError(f"{where}: a manifest holds gate_script or gate_command, not both")
    if script is not None and not (isinstance(script, str) and script):
        raise ValueError(f"{where}: gate_script must be a path in the pack, got {script!r}")
    if command is not None and not (isinstance(command, list) and command and all(isinstance(a, str) and a for a in command)):
        raise ValueError(f"{where}: gate_command must be a non-empty argv list of strings, got {command!r}")
    has_gate = script is not None or command is not None
    # A gate with no `phases` key keeps the P9 gate. A pack with no gate and no key has no phase.
    phases = data.get("phases", ["planning"] if has_gate else [])
    if not isinstance(phases, list) or not all(isinstance(p, str) and p in PHASES for p in phases):
        raise ValueError(f"{where}: phases must be a list of {list(PHASES)}, got {phases!r}")
    if "phases" in data and bool(phases) != has_gate:
        raise ValueError(f"{where}: a gate needs phases, and phases need a gate (phases={phases!r})")
    return Pack(
        name=str(data["name"]),
        version=str(data.get("version", "0.1.0")),
        description=str(data.get("description", "")),
        path=manifest_path.parent,
        triggers=list(data.get("triggers") or []),
        phases=[str(p) for p in phases],
        rules=[PackRule(id=str(r["id"]), name=str(r["name"]), severity=str(r.get("severity", "must-fix")))
               for r in data.get("rules", [])],
        gate_script=script,
        gate_command=command,
        reviewer_command=data.get("reviewer_command"),
        adr_scope=data.get("adr_scope"),
        manifest_version=version,
        requires=_requires(data.get("requires", {}), where),
        config=_declared_config(data.get("config", {}), where),
        stages=_stage_ops(data.get("stages", []), where),
        source=_source(data.get("source", {}), where),
        origin=origin,
    )


def _requires(raw: object, where: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) - {"cadence"} or not all(isinstance(v, str) for v in raw.values()):
        raise ValueError(f"{where}: requires must be {{\"cadence\": \"<version range>\"}}, got {raw!r}")
    return dict(raw)


def _declared_config(raw: object, where: str) -> dict[str, dict]:
    """The config keys of a pack: a key to a schema with a `default` that the schema allows."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: config must be a map of key to schema, got {raw!r}")
    for key, schema in raw.items():
        if not isinstance(schema, dict) or "default" not in schema:
            raise ValueError(f"{where}: config.{key} must be a schema with a default")
        cadence_config.check_value(schema["default"], schema, f"{where}: config.{key}.default")
    return dict(raw)


def _stage_ops(raw: object, where: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError(f"{where}: stages must be a list of overlay operations")
    schema = cadence_config.load_schema()["properties"]["stages"]["properties"]["overlays"]
    cadence_config.check_value(raw, schema, f"{where}: stages")
    return [dict(op) for op in raw]


def _source(raw: object, where: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) - {"git", "sha", "url"} or not all(isinstance(v, str) for v in raw.values()):
        raise ValueError(f"{where}: source must hold git, sha or url strings, got {raw!r}")
    return dict(raw)


# --- discovery and locks ------------------------------------------------------------------------

def pack_roots(skill_root: Path, workspace: Path | None) -> list[tuple[str, Path, Path | None]]:
    """(origin, packs dir, lock file) for core, user and project, in that order."""
    user = cadence_config.user_config_path().parent
    roots: list[tuple[str, Path, Path | None]] = [("core", Path(skill_root) / "packs", None),
                                                  ("user", user / "packs", user / LOCK)]
    project = cadence_config.project_root(workspace)
    if project is not None:
        roots.append(("project", project / ".cadence" / "packs", project / LOCK))
    return roots


def pack_digest(pack_dir: Path) -> str:
    """The sha256 of the pack's files: each relative path and the sha256 of its bytes, sorted."""
    lines = []
    for path in sorted(p for p in pack_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(pack_dir).as_posix()
        if "__pycache__" in rel.split("/") or rel.endswith(".pyc"):
            continue
        lines.append(f"{rel}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def read_lock(lock: Path) -> dict[str, dict]:
    if not lock.is_file():
        return {}
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{lock}: cannot read the lock: {exc}") from exc
    packs = data.get("packs") if isinstance(data, dict) else None
    if not isinstance(packs, dict):
        raise ValueError(f"{lock}: a lock is {{\"packs\": {{name: {{version, sha256, source}}}}}}")
    return packs


def find_pack(name: str, skill_root: Path, workspace: Path | None) -> Pack:
    """The one pack named `name`. A name that is not plain, missing, in two places or not locked raises."""
    if not _PACK_NAME.match(name):
        raise ValueError(f"pack name {name!r} is not a plain directory name")
    roots = pack_roots(skill_root, workspace)
    found = [(origin, root / name / "pack.json", lock) for origin, root, lock in roots
             if (root / name / "pack.json").is_file()]
    if not found:
        raise FileNotFoundError(f"pack {name!r} has no pack.json under " + ", ".join(str(r) for _o, r, _l in roots))
    if len(found) > 1:
        raise ValueError(f"pack {name!r} is in two places: {[str(m.parent) for _o, m, _l in found]}")
    origin, manifest, lock = found[0]
    if lock is not None:
        entry = read_lock(lock).get(name)
        if entry is None:
            raise ValueError(f"pack {name!r} ({manifest.parent}) is not in {lock}; check it, then run "
                             f"python3 scripts/pack_loader.py lock")
        digest = pack_digest(manifest.parent)
        if entry.get("sha256") != digest:
            raise ValueError(f"pack {name!r} changed since {lock} was written (sha256 {digest[:12]} is not "
                             f"{str(entry.get('sha256'))[:12]}); check the change, then lock again")
    pack = read_pack(manifest, origin)
    spec = pack.requires.get("cadence")
    if spec:
        have = core_version(skill_root)
        if not version_in_range(have, spec):
            raise ValueError(f"pack {name!r} requires cadence {spec}, and this is cadence {have}")
    return pack


def enabled_packs(skill_root: Path, workspace: Path | None = None,
                  resolved: cadence_config.Resolved | None = None) -> list[Pack]:
    """The packs that `packs` of the resolved config names, in its order."""
    resolved = resolved or cadence_config.load(skill_root, workspace)
    names = list(dict.fromkeys(resolved.get("packs") or []))
    packs = [find_pack(name, skill_root, workspace) for name in names]
    stray = sorted(set(resolved.get("pack_config") or {}) - set(names))
    if stray:
        raise cadence_config.ConfigError(f"pack_config names packs that are not on: {stray}")
    return packs


def pack_settings(pack: Pack, resolved: cadence_config.Resolved) -> dict[str, Any]:
    """The config of `pack`: its declared defaults, then `pack_config.<name>` of the layers."""
    given = resolved.get(f"pack_config.{pack.name}") or {}
    schema = {"type": "object", "additionalProperties": False, "properties": pack.config}
    cadence_config.check_value(given, schema, f"pack_config.{pack.name}")
    return {**{key: spec["default"] for key, spec in pack.config.items()}, **given}


def lock_packs(skill_root: Path, workspace: Path | None, *, user: bool = False) -> Path:
    """Write the lock of every pack in the project (or user) packs dir. Returns the lock path."""
    origin = "user" if user else "project"
    entry = next((r for r in pack_roots(skill_root, workspace) if r[0] == origin), None)
    if entry is None:
        raise ValueError("no project: run lock inside a git repo, or pass --user")
    _origin, root, lock = entry
    assert lock is not None
    packs: dict[str, dict] = {}
    for manifest in sorted(root.glob("*/pack.json")):
        pack = read_pack(manifest, origin)
        packs[pack.name] = {"version": pack.version, "sha256": pack_digest(manifest.parent),
                            **({"source": pack.source} if pack.source else {})}
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"packs": packs}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return lock


# --- gates --------------------------------------------------------------------------------------

def _argv(pack: Pack, phase: str, worktree: Path) -> list[str]:
    if pack.gate_script:
        script = pack.path / pack.gate_script
        if not script.is_file():
            raise FileNotFoundError(f"pack {pack.name} gate script missing: {script}")
        return [sys.executable, str(script), "--phase", phase, "--worktree", str(worktree)]
    assert pack.gate_command
    first = pack.path / pack.gate_command[0]
    return [str(first) if first.is_file() else pack.gate_command[0], *pack.gate_command[1:]]


def run_pack_gate(
    pack: Pack,
    phase: str,
    worktree: Path,
    files: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The findings of the pack's gate for ``phase``; [] when the pack does not list it.

    stdin is the JSON object {"phase", "worktree", "files", "config"}. `files` are the paths of the
    change (E3, R3), [] for P9. `config` is the pack's settings. stdout must be {"findings": [...]}.
    A gate that prints no JSON, or no `findings` list of objects, raises ValueError with the pack
    name. A non-zero exit with no must-fix finding adds one: a crash never reads as a pass.
    """
    if phase not in pack.phases or not pack.has_gate:
        return []
    request = {"phase": phase, "worktree": str(worktree), "files": list(files or []), "config": config or {}}
    try:
        proc = subprocess.run(_argv(pack, phase, worktree), cwd=str(pack.path), input=json.dumps(request),
                              capture_output=True, text=True, check=False)
    except OSError as exc:
        raise FileNotFoundError(f"pack {pack.name} gate cannot start: {exc}") from exc
    findings = _findings(pack, proc)
    if proc.returncode != 0 and not any(is_must_fix(f) for f in findings):
        findings.append({"rule_ref": f"pack:{pack.name}:exit", "severity": "must-fix",
                         "evidence": f"pack {pack.name} gate exited {proc.returncode}", "status": "open"})
    return findings


def _findings(pack: Pack, proc: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    """The `findings` list of the gate's stdout, or ValueError that names the pack and the exit."""
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    why = f"exit {proc.returncode}" + (f": {stderr[-300:]}" if stderr else "")
    if not stdout:
        raise ValueError(f"pack {pack.name} gate printed no JSON ({why})")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ValueError(f"pack {pack.name} gate stdout is not JSON ({why}): {e}") from e
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        raise ValueError(f"pack {pack.name} gate printed no `findings` list of objects ({why})")
    return findings


def phase_packs(skill_root: Path, phase: str, workspace: Path) -> tuple[list[tuple[Pack, dict]], list[str]]:
    """([(pack, settings)] of the enabled packs whose gate runs in `phase`, the load errors)."""
    try:
        resolved = cadence_config.load(skill_root, workspace)
        packs = enabled_packs(skill_root, workspace, resolved)
        settings = [(p, pack_settings(p, resolved)) for p in packs]  # every pack's settings, so a bad one fails early
        chosen = [(p, s) for p, s in settings if p.has_gate and phase in p.phases]
    except (OSError, ValueError, KeyError, TypeError) as exc:  # a bad manifest, lock or config layer
        return [], [f"packs: {exc}"]
    return chosen, []


def gate_errors(packs: list[tuple[Pack, dict]], phase: str, worktree: Path, files: list[str] | None = None) -> list[str]:
    """One line for each pack whose gate failed in `phase`: its first must-fix finding, or why it could not run."""
    errors: list[str] = []
    for pack, settings in packs:
        try:
            findings = run_pack_gate(pack, phase=phase, worktree=worktree, files=files, config=settings)
        except (FileNotFoundError, ValueError) as exc:
            errors.append(str(exc))
            continue
        must_fix = next((f for f in findings if is_must_fix(f)), None)
        if must_fix is not None:
            evidence = f": {must_fix['evidence']}" if must_fix.get("evidence") else ""
            errors.append(f"pack {pack.name} gate failed: {must_fix.get('rule_ref', pack.name)}{evidence}")
    return errors


def match_triggers(pack: Pack, changed_paths: list[str]) -> list[dict[str, Any]]:
    """Return trigger entries that match any changed path (simple glob suffix/prefix)."""
    import fnmatch

    hits: list[dict[str, Any]] = []
    for trigger in pack.triggers:
        pattern = str(trigger.get("pattern") or "")
        if not pattern:
            continue
        for path in changed_paths:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.lstrip("./")):
                hits.append({**trigger, "matched_path": path, "pack": pack.name})
                break
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cadence packs: list, lock, check")
    parser.add_argument("command", choices=("list", "lock", "check"))
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--user", action="store_true", help="lock: the user packs dir, not the project")
    args = parser.parse_args(argv)
    root = _SCRIPTS.parent
    workspace = args.workspace or Path.cwd()
    try:
        if args.command == "lock":
            print(f"wrote {lock_packs(root, workspace, user=args.user)}")
            return 0
        packs = enabled_packs(root, workspace)
    except (OSError, ValueError) as exc:
        print(f"packs: FAIL: {exc}", file=sys.stderr)
        return 1
    for pack in packs:
        gate = " ".join(pack.gate_command) if pack.gate_command else (pack.gate_script or "-")
        print(f"{pack.name} {pack.version} [{pack.origin}] phases={','.join(pack.phases) or '-'} gate={gate}")
    if args.command == "check":
        print("packs: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
