"""The layered config loader: layer order, merge rules, the closed key set and fail-closed errors."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cadence_config as cc  # noqa: E402


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.delenv("CADENCE_CONFIG_HOME", raising=False)
    return home / "cadence"


def write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def load(project: Path, **env: str):
    return cc.load(ROOT, project, env={**_base_env(), **env})


def _base_env() -> dict[str, str]:
    import os
    return {k: v for k, v in os.environ.items() if not k.startswith("CADENCE_") or k == "CADENCE_CONFIG_HOME"}


def test_the_defaults_alone_give_the_standard_profile(project, user_home):
    resolved = load(project)
    assert resolved.get("profile") == "standard"
    assert resolved.get("cognitive_complexity.max") == 15
    assert resolved.source("cognitive_complexity.max") == "default"
    assert [layer.name for layer in resolved.layers] == ["default", "profile:standard"]


def test_each_layer_wins_over_the_layer_below(project, user_home):
    write(user_home / "config.json", {"adapter": "none", "subagent_hygiene": {"mode": "shadow"}})
    write(project / ".cadence.json", {"subagent_hygiene": {"mode": "off"}})
    write(project / ".cadence.local.json", {"context_budget": {"mode": "off"}})
    resolved = load(project, CADENCE_CONTEXT_BUDGET_MODE="enforce")
    assert resolved.get("adapter") == "none" and resolved.source("adapter") == "user"
    assert resolved.get("subagent_hygiene.mode") == "off" and resolved.source("subagent_hygiene.mode") == "project"
    assert resolved.get("subagent_hygiene.classifier") == "none"
    assert resolved.get("context_budget.mode") == "enforce" and resolved.source("context_budget.mode") == "env"
    assert [layer.name for layer in resolved.layers] == ["default", "profile:standard", "user", "project", "local", "env"]


def test_the_project_is_the_nearest_folder_with_a_config_or_git(project, user_home):
    write(project / ".cadence.json", {"adapter": "none"})
    deep = project / "a" / "b"
    deep.mkdir(parents=True)
    assert load(deep).get("adapter") == "none"


def test_a_profile_applies_over_the_defaults_and_under_the_project(project, user_home):
    write(project / ".cadence.json", {"profile": "strict", "bugs": {"unsupported": "warn"}})
    resolved = load(project)
    assert "architecture" in resolved.get("packs")
    assert resolved.get("types.unsupported") == "fail" and resolved.source("types.unsupported") == "profile:strict"
    assert resolved.get("bugs.unsupported") == "warn" and resolved.source("bugs.unsupported") == "project"


@pytest.mark.parametrize("data, where", [
    ({"no_such_key": 1}, "no_such_key"),
    ({"context_budget": {"modee": "off"}}, "context_budget.modee"),
    ({"test_shape": {"layout": {"tests": ["t/"]}}}, "test_shape.layout.tests"),
])
def test_an_unknown_key_fails_with_the_file_and_the_key(project, user_home, data, where):
    path = write(project / ".cadence.json", data)
    with pytest.raises(cc.ConfigError) as err:
        load(project)
    assert str(path) in str(err.value) and where in str(err.value)


@pytest.mark.parametrize("data", [
    {"context_budget": {"mode": "loud"}},
    {"cognitive_complexity": {"max": "15"}},
    {"cognitive_complexity": {"max": True}},
    {"packs": "architecture"},
    {"profile": "huge"},
    {"benchmark_score_floor": 11},
])
def test_a_bad_value_fails_closed(project, user_home, data):
    write(project / ".cadence.json", data)
    with pytest.raises(cc.ConfigError):
        load(project)


def test_a_file_that_does_not_parse_fails_and_never_falls_back(project, user_home):
    (project / ".cadence.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(cc.ConfigError, match="not valid JSON"):
        load(project)


def test_a_limit_only_gets_stricter(project, user_home):
    write(user_home / "config.json", {"cognitive_complexity": {"max": 10}})
    write(project / ".cadence.json", {"cognitive_complexity": {"max": 12}, "benchmark_score_floor": 5})
    resolved = load(project)
    assert resolved.get("cognitive_complexity.max") == 10
    assert resolved.source("cognitive_complexity.max") == "user (project value 12 ignored)"
    assert resolved.get("benchmark_score_floor") == 7
    write(project / ".cadence.json", {"benchmark_score_floor": 9})
    assert load(project).get("benchmark_score_floor") == 9


def test_a_list_replaces_and_a_patch_changes_the_list_below(project, user_home):
    write(project / ".cadence.json", {"gated_prefixes": {"append": ["scripts/"], "remove": ["app/"]}})
    assert load(project).get("gated_prefixes") == ["src/", "lib/", "packages/", "scripts/"]
    write(project / ".cadence.json", {"gated_prefixes": ["only/"]})
    assert load(project).get("gated_prefixes") == ["only/"]


def test_a_grow_only_list_cannot_lose_an_item(project, user_home):
    write(user_home / "config.json", {"secret_paths": ["*.vault"]})
    write(project / ".cadence.json", {"secret_paths": ["*.token"]})
    assert load(project).get("secret_paths") == ["*.vault", "*.token"]
    write(project / ".cadence.json", {"secret_paths": {"remove": ["*.vault"]}})
    with pytest.raises(cc.ConfigError, match="only grows"):
        load(project)


def test_stage_overlays_join_in_layer_order(project, user_home):
    write(user_home / "config.json", {"stages": {"overlays": [{"op": "remove", "block": "A5"}]}})
    write(project / ".cadence.json", {"stages": {"overlays": [{"op": "remove", "block": "P6"}]}})
    assert [o["block"] for o in load(project).get("stages.overlays")] == ["A5", "P6"]


def test_an_env_value_is_json_when_it_parses(project, user_home):
    resolved = load(project, CADENCE_REVIEW_FIX_ROUNDS="1", CADENCE_PACKS='["tracker"]', CADENCE_ADAPTER="none")
    assert (resolved.get("review_fix_rounds"), resolved.get("packs"), resolved.get("adapter")) == (1, ["tracker"], "none")


def test_no_config_key_takes_the_name_of_a_runtime_env_var():
    taken = {"CADENCE_HOME", "CADENCE_SKILL_ROOT", "CADENCE_BASE_REF", "CADENCE_ALLOW_PR", "CADENCE_PYTHON",
             "CADENCE_GATES_READONLY", "CADENCE_OWNER_ID", "CADENCE_CONFIG_HOME"}
    assert not taken & {name for name, _ in cc.env_names(cc.load_schema(ROOT))}


def test_the_defaults_and_each_profile_follow_the_schema():
    schema = cc.load_schema(ROOT)
    defaults = json.loads((ROOT / "cadence.defaults.json").read_text(encoding="utf-8"))
    defaults.pop("$schema")
    cc.check_value(defaults, schema, "cadence.defaults.json")
    declared = {k for k in schema["properties"] if not k.startswith("$")}
    assert set(defaults) == declared, "every schema key has a default, and every default has a schema entry"
    for profile in schema["properties"]["profile"]["enum"]:
        data = json.loads((ROOT / "profiles" / f"{profile}.json").read_text(encoding="utf-8"))
        cc.check_value(data, schema, f"profiles/{profile}.json")


def test_each_measured_section_default_matches_the_schema():
    schema = cc.load_schema(ROOT)
    defaults = json.loads((ROOT / "cadence.defaults.json").read_text(encoding="utf-8"))
    for name, sub in schema["properties"].items():
        if sub.get("type") == "object" and sub.get("properties"):
            assert set(defaults[name]) == set(sub["properties"]), name


def test_resolve_names_each_layer_and_each_source(project, user_home):
    write(project / ".cadence.json", {"adapter": "none"})
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "cadence_config.py"), "resolve",
                          "--workspace", str(project)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert "# project:" in out.stdout and 'adapter' in out.stdout and "[project]" in out.stdout


def test_check_fails_with_the_file_on_a_bad_layer(project, user_home):
    write(project / ".cadence.json", {"adapterr": "none"})
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "cadence_config.py"), "check",
                          "--workspace", str(project)], capture_output=True, text=True, check=False)
    assert out.returncode == 1 and "adapterr" in out.stderr and ".cadence.json" in out.stderr


def test_the_record_holds_the_sha256_of_each_layer_file(project, user_home):
    path = write(project / ".cadence.json", {"adapter": "none"})
    record = load(project).record()
    import hashlib
    assert {"name": "project", "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} in record["layers"]
