#!/usr/bin/env python3
"""The one reader of Cadence config: layers, a closed key set, and fail-closed errors.

Layers, lowest first. A higher layer wins, except where the schema says otherwise:

  1. default   <skill root>/cadence.defaults.json (core; an update overwrites it)
  2. profile   <skill root>/profiles/<profile>.json (`profile` from any layer)
  3. user      $CADENCE_CONFIG_HOME/config.json, else $XDG_CONFIG_HOME/cadence/config.json,
               else ~/.config/cadence/config.json
  4. project   <project>/.cadence.json (the team; git tracks it)
  5. local     <project>/.cadence.local.json (one checkout; git ignores it)
  6. env       CADENCE_<KEY>, with each `.` of a key path as `_`: CADENCE_CONTEXT_BUDGET_MODE=enforce.
               The value is JSON when it parses, else a string.

The project is the nearest directory at or above the workspace that holds `.cadence.json`,
`.cadence.local.json` or `.git`.

Merge rules (references/cadence.config.schema.json is the one source of keys and types):

- An object merges key by key.
- A list replaces the list below it. A patch {"append": [...], "remove": [...]} changes it.
- `x-cadence-merge: concat` lists join in layer order (stage overlays).
- `x-cadence-grow-only` lists only grow: a list or an append adds, a remove fails.
- `x-cadence-stricter: lower|higher` limits only get stricter. A looser value is ignored, and
  `resolve` and the measure records name it.

A key that the schema does not declare, a value of the wrong type and a file that does not
parse raise ConfigError with the file and the key. The loader never falls back to the defaults.

CLI: python3 scripts/cadence_config.py resolve [--workspace DIR] [--json]
     python3 scripts/cadence_config.py check [--workspace DIR]
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = Path("references") / "cadence.config.schema.json"
DEFAULTS = "cadence.defaults.json"
PROJECT = ".cadence.json"
LOCAL = ".cadence.local.json"
ENV_PREFIX = "CADENCE_"
PATCH_KEYS = frozenset({"append", "remove"})
_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


class ConfigError(ValueError):
    """A config layer is not valid. Every gate that reads config fails closed on it."""


@dataclass(frozen=True)
class Layer:
    name: str
    path: Path | None
    data: dict
    sha256: str | None = None


@dataclass
class Resolved:
    """The merged config, the layer that set each value, and each looser value that was ignored."""

    values: dict
    sources: dict[str, str]
    layers: list[Layer]
    ignored: dict[str, str] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.values
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict:
        return copy.deepcopy(self.values.get(name) or {})

    def source(self, dotted: str) -> str:
        """`default`, or the layer that set the value, with the looser value that it ignored."""
        base = self.sources.get(dotted, "default")
        note = self.ignored.get(dotted)
        return f"{base} ({note})" if note else base

    def record(self) -> dict:
        """The run record of the config: each layer file with its sha256, and the values."""
        return {
            "layers": [{"name": layer.name, "path": str(layer.path) if layer.path else None,
                        "sha256": layer.sha256} for layer in self.layers],
            "values": self.values,
        }


# --- schema -------------------------------------------------------------------------------------

def load_schema(skill_root: Path | None = None) -> dict:
    path = Path(skill_root or SKILL_ROOT) / SCHEMA
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: the config schema cannot be read: {exc}") from exc


def _child(schema: dict, key: str) -> dict | None:
    """The schema of `key` in an object schema, or None when the object does not declare it."""
    props = schema.get("properties") or {}
    if key in props:
        return props[key]
    extra = schema.get("additionalProperties", True)
    if extra is False:
        return None
    names = schema.get("propertyNames") or {}
    if "pattern" in names and not re.search(names["pattern"], key):
        return None
    return extra if isinstance(extra, dict) else {}


def _type_ok(value: Any, schema: dict) -> bool:
    want = schema.get("type")
    if want is None:
        return True
    for name in [want] if isinstance(want, str) else want:
        kinds = _TYPES[name]
        if isinstance(value, bool) and name in ("integer", "number"):
            continue
        if isinstance(value, kinds):
            return True
    return False


def check_value(value: Any, schema: dict, where: str) -> None:
    """Raise ConfigError for the first part of `value` that `schema` does not allow."""
    if not _type_ok(value, schema):
        raise ConfigError(f"{where} must be {schema.get('type')}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ConfigError(f"{where} must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ConfigError(f"{where} must be at least {schema['minimum']}, got {value!r}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ConfigError(f"{where} must be at most {schema['maximum']}, got {value!r}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ConfigError(f"{where} must not be empty")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ConfigError(f"{where} must match {schema['pattern']}, got {value!r}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            check_value(item, schema["items"], f"{where}[{i}]")
    if isinstance(value, dict) and schema.get("type") == "object" or (isinstance(value, dict) and "properties" in schema):
        for key, item in value.items():
            sub = _child(schema, key)
            if sub is None:
                raise ConfigError(f"{where}.{key} is not a Cadence config key")
            check_value(item, sub, f"{where}.{key}")


def env_names(schema: dict, prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, tuple[str, ...]]]:
    """(env var name, key path) for each declared key, objects too."""
    for key, sub in (schema.get("properties") or {}).items():
        if key.startswith("$"):
            continue
        path = (*prefix, key)
        yield ENV_PREFIX + "_".join(path).upper(), path
        if sub.get("properties"):
            yield from env_names(sub, path)


# --- layers -------------------------------------------------------------------------------------

def _read(path: Path, name: str) -> Layer | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: a config layer must be a JSON object")
    data.pop("$schema", None)
    return Layer(name, path, data, hashlib.sha256(raw).hexdigest())


def user_config_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("CADENCE_CONFIG_HOME"):
        return Path(env["CADENCE_CONFIG_HOME"]) / "config.json"
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or Path.home()) / ".config")
    return Path(base) / "cadence" / "config.json"


def project_root(workspace: Path | None) -> Path | None:
    """The nearest directory at or above `workspace` with a project config file or `.git`."""
    if workspace is None:
        return None
    start = Path(workspace).resolve()
    for folder in (start, *start.parents):
        if any((folder / name).exists() for name in (PROJECT, LOCAL, ".git")):
            return folder
    return None


def _env_layer(schema: dict, env: Mapping[str, str]) -> Layer | None:
    data: dict = {}
    for name, path in env_names(schema):
        if name not in env:
            continue
        text = env[name]
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = text
        node = data
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    return Layer("env", None, data) if data else None


def _profile_name(layers: list[Layer]) -> str:
    for layer in reversed(layers):
        if "profile" in layer.data:
            return layer.data["profile"]
    return "standard"


# --- merge --------------------------------------------------------------------------------------

def _is_patch(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and set(value) <= PATCH_KEYS


def _stricter(rule: str, new: Any, old: Any) -> bool:
    return new <= old if rule == "lower" else new >= old


def _merge(base: Any, value: Any, schema: dict, where: str, layer: Layer, out: Resolved) -> Any:
    """`value` from `layer` over `base`. Records the source of each leaf in `out`."""
    label = f"{layer.name} ({layer.path})" if layer.path else layer.name
    if schema.get("type") == "object" and isinstance(value, dict) and not _is_patch(value):
        merged = dict(base) if isinstance(base, dict) else {}
        for key, item in value.items():
            sub = _child(schema, key)
            if sub is None:
                raise ConfigError(f"{label}: {where}{key} is not a Cadence config key")
            merged[key] = _merge(merged.get(key), item, sub, f"{where}{key}.", layer, out)
        return merged
    key = where.rstrip(".")
    if schema.get("type") == "array":
        value = _merge_list(base, value, schema, key, label)
    else:
        check_value(value, schema, f"{label}: {key}")
        rule = schema.get("x-cadence-stricter")
        if rule and base is not None and not _stricter(rule, value, base):
            out.ignored[key] = f"{layer.name} value {value} ignored"
            return base
    check_value(value, schema, f"{label}: {key}")
    out.sources[key] = layer.name
    out.ignored.pop(key, None)
    return value


def _merge_list(base: Any, value: Any, schema: dict, key: str, label: str) -> list:
    base = list(base) if isinstance(base, list) else []
    grow_only = schema.get("x-cadence-grow-only")
    if schema.get("x-cadence-merge") == "concat":
        if not isinstance(value, list):
            raise ConfigError(f"{label}: {key} must be a list")
        return base + value
    if _is_patch(value):
        add, drop = value.get("append", []), value.get("remove", [])
        if not isinstance(add, list) or not isinstance(drop, list):
            raise ConfigError(f"{label}: {key} append and remove must be lists")
        if drop and grow_only:
            raise ConfigError(f"{label}: {key} only grows; a layer cannot remove an item")
        return [item for item in base if item not in drop] + [item for item in add if item not in base]
    if not isinstance(value, list):
        raise ConfigError(f"{label}: {key} must be a list or a patch {{\"append\": [...], \"remove\": [...]}}")
    if grow_only:
        return base + [item for item in value if item not in base]
    return value


# --- load ---------------------------------------------------------------------------------------

def load(skill_root: Path | None = None, workspace: Path | None = None,
         env: Mapping[str, str] | None = None, *, project_layers: bool = True) -> Resolved:
    """The resolved config for `workspace` (default: the current directory).

    `project_layers=False` leaves out the project and local layers: for a path anchor that is
    not the project root (the edit gate matches a prefix from its own anchor only).
    """
    root = Path(skill_root or SKILL_ROOT)
    env = os.environ if env is None else env
    schema = load_schema(root)
    defaults = _read(root / DEFAULTS, "default")
    if defaults is None:
        raise ConfigError(f"{root / DEFAULTS}: the core defaults are missing")
    upper: list[Layer] = []
    user = _read(user_config_path(env), "user")
    if user:
        upper.append(user)
    project = project_root(Path.cwd() if workspace is None else workspace) if project_layers else None
    if project is not None:
        for name, file in (("project", PROJECT), ("local", LOCAL)):
            layer = _read(project / file, name)
            if layer:
                upper.append(layer)
    env_layer = _env_layer(schema, env)
    if env_layer:
        upper.append(env_layer)
    profile = _profile_name([defaults, *upper])
    check_value(profile, schema["properties"]["profile"], "profile")
    profile_layer = _read(root / "profiles" / f"{profile}.json", f"profile:{profile}")
    if profile_layer is None:
        raise ConfigError(f"profile {profile!r} has no file profiles/{profile}.json")
    layers = [defaults, profile_layer, *upper]
    out = Resolved(values={}, sources={}, layers=layers)
    values: dict = {}
    for layer in layers:
        values = _merge(values, layer.data, schema, "", layer, out)
    out.values = values
    return out


def section(name: str, skill_root: Path | None = None, workspace: Path | None = None) -> dict:
    return load(skill_root, workspace).section(name)


def value(dotted: str, skill_root: Path | None = None, workspace: Path | None = None) -> Any:
    return load(skill_root, workspace).get(dotted)


# --- CLI ----------------------------------------------------------------------------------------

def _leaves(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict) and node:
        for key, item in node.items():
            yield from _leaves(item, f"{prefix}{key}.")
    else:
        yield prefix.rstrip("."), node


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("resolve", "check"))
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="resolve: print the run record as JSON")
    args = parser.parse_args(argv)
    try:
        resolved = load(workspace=args.workspace)
    except ConfigError as exc:
        print(f"cadence config: FAIL: {exc}", file=sys.stderr)
        return 1
    if args.command == "check":
        print("cadence config: PASS")
        return 0
    if args.json:
        print(json.dumps({**resolved.record(), "sources": resolved.sources, "ignored": resolved.ignored}, indent=2))
        return 0
    for layer in resolved.layers:
        print(f"# {layer.name}: {layer.path or 'environment'}")
    width = max(len(k) for k, _ in _leaves(resolved.values))
    for key, item in _leaves(resolved.values):
        print(f"{key:<{width}}  {json.dumps(item)}  [{resolved.source(key)}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
