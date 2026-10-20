"""Packs: discovery, manifest version 2, locks, gate_command and pack settings.

A pack is found in core, the user packs dir or the project packs dir. A user or project pack
loads only with a matching lock row. A manifest_version 2 pack can declare a Cadence version
range, config keys with defaults, stage rows, a source, and a gate in any language.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from conftest import code_paths, install_config, make_session, project_config, write_files

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import cadence_config  # noqa: E402
import pack_loader as pl  # noqa: E402
from domain_analysis import run_domain_analysis  # noqa: E402
from entry_invariant import check_execution_entry  # noqa: E402

ECHO_CONFIG = ("import json, sys\nreq = json.load(sys.stdin)\n"
               "print(json.dumps({'findings': [{'severity': 'nit', 'evidence': json.dumps(req['config'], sort_keys=True)}]}))\n")


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def project_pack(project: Path, name: str, manifest: dict, files: dict[str, str] | None = None) -> Path:
    root = project / ".cadence" / "packs" / name
    write_files(root, {"pack.json": json.dumps({"name": name, **manifest}), **(files or {})})
    return root


def lock(project: Path) -> Path:
    return pl.lock_packs(ROOT, project)


# --- discovery --------------------------------------------------------------------------------------


def test_no_pack_on_selects_nothing(project):
    assert pl.enabled_packs(ROOT, project) == []


def test_a_missing_pack_fails_loud(project):
    project_config(project, {"packs": ["no-such-pack"]})
    with pytest.raises(FileNotFoundError, match="no-such-pack"):
        pl.enabled_packs(ROOT, project)


def test_domain_analysis_with_no_pack_selects_nothing(tmp_path, project):
    context = tmp_path / "ctx"
    write_files(project, {"src/app.py": "x = 1\n"})
    result = run_domain_analysis(worktree=project, context_dir=context, skill_root=ROOT)
    data = json.loads((context / "domain-analysis.json").read_text(encoding="utf-8"))
    assert data["selected_packs"] == result["selected_packs"] == [] and data["matched_triggers"] == []


def test_the_sample_fail_pack_fails_p9(tmp_path, project):
    project_config(project, {"packs": ["_sample_fail_gate"]})
    context = tmp_path / ".context"
    make_session(context)
    [pack] = pl.enabled_packs(ROOT, project)
    assert any(f.get("severity") == "must-fix" for f in pl.run_pack_gate(pack, "planning", project))
    errors = check_execution_entry(context, skill_root=ROOT, worktree=project)
    assert any("_sample_fail_gate" in e for e in errors)


def test_a_project_pack_loads_only_with_a_matching_lock(project):
    project_pack(project, "house", {"rules": []})
    project_config(project, {"packs": ["house"]})
    with pytest.raises(ValueError, match="is not in .*cadence.lock.json"):
        pl.enabled_packs(ROOT, project)
    lock(project)
    [pack] = pl.enabled_packs(ROOT, project)
    assert (pack.name, pack.origin) == ("house", "project")
    (project / ".cadence" / "packs" / "house" / "extra.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since"):
        pl.enabled_packs(ROOT, project)


def test_a_user_pack_loads_from_the_user_dir_with_its_own_lock(project):
    user = cadence_config.user_config_path().parent
    write_files(user / "packs" / "mine", {"pack.json": json.dumps({"name": "mine"})})
    pl.lock_packs(ROOT, project, user=True)
    project_config(project, {"packs": ["mine"]})
    [pack] = pl.enabled_packs(ROOT, project)
    assert pack.origin == "user" and (user / "cadence.lock.json").is_file()


def test_a_pack_name_in_two_places_fails(project):
    project_pack(project, "_sample_fail_gate", {})
    project_config(project, {"packs": ["_sample_fail_gate"]})
    with pytest.raises(ValueError, match="in two places"):
        pl.enabled_packs(ROOT, project)


def test_the_manifest_name_is_the_directory_name(project):
    project_pack(project, "house", {})
    (project / ".cadence" / "packs" / "house" / "pack.json").write_text(json.dumps({"name": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="name must be the directory name"):
        lock(project)


# --- manifest version 2 -----------------------------------------------------------------------------


def test_v2_keys_need_manifest_version_2(project):
    project_pack(project, "house", {"requires": {"cadence": ">=0.1.0"}})
    with pytest.raises(ValueError, match="unknown keys"):
        lock(project)


@pytest.mark.parametrize("spec, ok", [(">=0.1.0", True), (">=0.1.0, <1.0.0", True), ("<0.1.0", False), ("==9.9.9", False)])
def test_a_pack_states_the_cadence_versions_it_works_with(project, spec, ok):
    project_pack(project, "house", {"manifest_version": 2, "version": "1.0.0", "requires": {"cadence": spec}})
    lock(project)
    project_config(project, {"packs": ["house"]})
    if ok:
        assert [p.name for p in pl.enabled_packs(ROOT, project)] == ["house"]
    else:
        with pytest.raises(ValueError, match="requires cadence"):
            pl.enabled_packs(ROOT, project)


def test_a_v2_pack_needs_a_semver_version(project):
    project_pack(project, "house", {"manifest_version": 2, "version": "one"})
    with pytest.raises(ValueError, match="semver"):
        lock(project)


def test_a_gate_command_runs_any_program_with_the_same_contract(project):
    gate = project_pack(project, "shell", {
        "manifest_version": 2, "version": "1.0.0", "phases": ["impl"], "gate_command": ["./gate.sh"]},
        {"gate.sh": "#!/bin/sh\ncat >/dev/null\necho '{\"findings\": [{\"severity\": \"must-fix\", \"evidence\": \"from sh\"}]}'\n"})
    path = gate / "gate.sh"
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    lock(project)
    project_config(project, {"packs": ["shell"]})
    packs, errors = pl.phase_packs(ROOT, "impl", project)
    assert errors == []
    assert pl.gate_errors(packs, "impl", project, ["a.go"]) == ["pack shell gate failed: shell: from sh"]


def test_a_gate_command_on_path_runs_too(project):
    project_pack(project, "py", {"manifest_version": 2, "version": "1.0.0", "phases": ["review"],
                                 "gate_command": [sys.executable, "gate.py"]}, {"gate.py": ECHO_CONFIG})
    lock(project)
    [pack] = [p for p in pl.enabled_packs(ROOT, project) if p.name == "py"] or [pl.find_pack("py", ROOT, project)]
    assert pl.run_pack_gate(pack, "review", project)[0]["evidence"] == "{}"


def test_a_gate_command_that_cannot_start_fails_with_the_pack_name(project):
    project_pack(project, "gone", {"manifest_version": 2, "version": "1.0.0", "phases": ["impl"],
                                   "gate_command": ["no-such-program-cadence"]})
    lock(project)
    pack = pl.find_pack("gone", ROOT, project)
    assert pl.gate_errors([(pack, {})], "impl", project)[0].startswith("pack gone gate cannot start")


def test_a_manifest_holds_a_gate_script_or_a_gate_command(project):
    project_pack(project, "both", {"manifest_version": 2, "version": "1.0.0", "phases": ["impl"],
                                   "gate_script": "g.py", "gate_command": ["g"]})
    with pytest.raises(ValueError, match="not both"):
        lock(project)


def test_pack_config_gives_the_gate_its_declared_settings(project):
    project_pack(project, "cfg", {
        "manifest_version": 2, "version": "1.0.0", "phases": ["planning"], "gate_script": "gate.py",
        "config": {"max_items": {"type": "integer", "minimum": 1, "default": 5},
                   "label": {"type": "string", "default": "x"}}}, {"gate.py": ECHO_CONFIG})
    lock(project)
    project_config(project, {"packs": ["cfg"], "pack_config": {"cfg": {"max_items": 2}}})
    [(pack, settings)], errors = pl.phase_packs(ROOT, "planning", project)
    assert errors == [] and settings == {"max_items": 2, "label": "x"}
    assert json.loads(pl.run_pack_gate(pack, "planning", project, config=settings)[0]["evidence"]) == settings


@pytest.mark.parametrize("given, needle", [({"cfg": {"max_items": 0}}, "at least 1"), ({"cfg": {"nope": 1}}, "not a Cadence config key"),
                                           ({"other": {}}, "not on")])
def test_bad_pack_config_fails_closed(project, given, needle):
    project_pack(project, "cfg", {"manifest_version": 2, "version": "1.0.0",
                                  "config": {"max_items": {"type": "integer", "minimum": 1, "default": 5}}})
    lock(project)
    project_config(project, {"packs": ["cfg"], "pack_config": given})
    packs, errors = pl.phase_packs(ROOT, "planning", project)
    assert packs == [] and needle in errors[0]


def test_a_declared_default_must_follow_its_own_schema(project):
    project_pack(project, "cfg", {"manifest_version": 2, "version": "1.0.0",
                                  "config": {"n": {"type": "integer", "default": "five"}}})
    with pytest.raises(ValueError, match="must be integer"):
        lock(project)


def test_the_lock_records_version_digest_and_source(project):
    project_pack(project, "house", {"manifest_version": 2, "version": "2.1.0",
                                    "source": {"git": "https://example.org/packs.git", "sha": "abc123"}})
    data = json.loads(lock(project).read_text(encoding="utf-8"))["packs"]["house"]
    assert data["version"] == "2.1.0" and len(data["sha256"]) == 64 and data["source"]["sha"] == "abc123"


def test_the_cli_lists_checks_and_locks(project):
    import subprocess

    project_pack(project, "house", {})
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "pack_loader.py"), "lock", "--workspace", str(project)],
                         capture_output=True, text=True, check=False, env={**os.environ})
    assert out.returncode == 0 and "cadence.lock.json" in out.stdout
    project_config(project, {"packs": ["house"]})
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "pack_loader.py"), "check", "--workspace", str(project)],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0 and "house" in out.stdout and "[project]" in out.stdout


@pytest.mark.parametrize("spec, version, ok", [
    (">=1.2", "1.2.0", True), (">1.2.0", "1.2.0", False), ("<=1.2.3", "1.2.3", True), ("1.2.3", "1.2.3", True),
    (">=0.1.0, <1.0.0", "1.0.0", False), ("<2", "1.9.9-rc1", True),
])
def test_version_ranges(spec, version, ok):
    assert pl.version_in_range(version, spec) is ok


def test_every_core_pack_loads_and_names_its_directory():
    for manifest in sorted((ROOT / "packs").glob("*/pack.json")):
        assert pl.read_pack(manifest).name == manifest.parent.name


def test_the_stage_packs_of_this_repo_are_on(tmp_path):
    shutil.copy(ROOT / ".cadence.json", tmp_path / ".cadence.json")
    (tmp_path / ".git").mkdir()
    names = [p.name for p in pl.enabled_packs(ROOT, tmp_path)]
    assert names == json.loads((ROOT / ".cadence.json").read_text(encoding="utf-8"))["packs"]
    install_config(tmp_path / "unused")  # the helper works on any directory
