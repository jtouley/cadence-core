"""A pack gate runs in P9, E3 and R3 for the phases its pack.json lists, and fails closed.

A gate that exits non-zero, prints no JSON or prints no `findings` list is a failure that names
the pack. `packs` in any config layer turns a pack's gate on, the project layer too.
packs/_sample_impl_gate flags a planted file name in the change (impl and review phases).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import feature_repo, install_config, make_session, project_config, run_py, write_files

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import architecture_review as e3  # noqa: E402
import cognitive_complexity_gate as r3  # noqa: E402
from entry_invariant import check_execution_entry  # noqa: E402
from pack_loader import enabled_packs, gate_errors, phase_packs, run_pack_gate  # noqa: E402

IMPL = "_sample_impl_gate"
PLANTED = "SAMPLE_IMPL_GATE_PLANTED.txt"
EXIT_1_SILENT = "import sys\nsys.exit(1)\n"


def _skill(tmp_path: Path) -> Path:
    root = tmp_path / "skill"
    if not (root / "cadence.defaults.json").is_file():
        install_config(root)
    return root


def _ws(tmp_path: Path, name: str = "ws") -> Path:
    """A project with no config of its own."""
    ws = tmp_path / name
    (ws / ".git").mkdir(parents=True, exist_ok=True)
    return ws


def _pack(tmp_path: Path, name: str, manifest: dict, gate: str | None = None) -> Path:
    """packs/<name>/pack.json in the skill root of `tmp_path` (gate.py when `gate` is given), turned on in its defaults."""
    root = _skill(tmp_path)
    files = {"pack.json": json.dumps({"name": name, **manifest, **({"gate_script": "gate.py"} if gate else {})})}
    write_files(root / "packs" / name, {**files, **({"gate.py": gate} if gate else {})})
    names = json.loads((root / "cadence.defaults.json").read_text(encoding="utf-8"))["packs"]
    install_config(root, packs=[*names, name])
    return root


def _load(root: Path, tmp_path: Path):
    return enabled_packs(root, _ws(tmp_path))


def _impl_pack(workspace: Path):
    packs, errors = phase_packs(ROOT, "impl", workspace)
    assert errors == []
    return next((p, s) for p, s in packs if p.name == IMPL)


@pytest.fixture
def change(tmp_path, monkeypatch):
    """A feature branch off `main` whose .cadence.json turns on _sample_impl_gate; the cwd is the repo."""
    repo = feature_repo(tmp_path / "repo", {"README.md": "x\n"})
    project_config(repo, {"packs": [IMPL]})
    monkeypatch.chdir(repo)
    return repo


# --- pack.json `phases` and the sample impl pack --------------------------------------------------------


def test_phases_default_to_planning_for_a_gate_and_an_explicit_list_is_kept(tmp_path):
    _pack(tmp_path, "old", {"rules": []}, gate=EXIT_1_SILENT)  # a gate with no phases key: P9 only
    _pack(tmp_path, "rows", {"rules": []})  # a stage pack: rows, no gate, no phase
    root = _pack(tmp_path, "new", {"phases": ["impl", "review"]}, gate=EXIT_1_SILENT)
    assert [p.phases for p in _load(root, tmp_path)] == [["planning"], [], ["impl", "review"]]


@pytest.mark.parametrize("phases", ["impl", ["implementation"], ["impl", 3], ["Planning"]])
def test_phases_outside_the_known_set_fail_closed(tmp_path, phases):
    """A typo in `phases` must not load as a pack whose gate never runs."""
    with pytest.raises(ValueError, match="phases must be a list of"):
        _load(_pack(tmp_path, "bad", {"phases": phases}), tmp_path)


@pytest.mark.parametrize("manifest", [
    {"gate_scrpit": "gate.py"}, {"phase": ["impl"], "gate_script": "gate.py"},  # a misspelled key
    {"phases": [], "gate_script": "gate.py"},  # a gate that no phase runs
    {"phases": ["impl"]}, {"phases": ["impl"], "gate_script": ""},  # phases with no gate
    {"gate_script": 123}, {"gate_script": ["gate.py"]},  # not a string
], ids=["key-typo", "singular-phase", "gate-no-phases", "phases-no-gate", "empty-gate", "int-gate", "list-gate"])
def test_a_manifest_that_names_a_gate_no_phase_runs_fails_the_load(tmp_path, manifest):
    """Each shape that would load as a pack whose gate never runs is an error."""
    with pytest.raises(ValueError, match="unknown keys|need a gate|needs phases|gate_script must be a path"):
        _load(_pack(tmp_path, "shape", manifest), tmp_path)


def test_the_stage_packs_with_no_gate_and_no_phases_still_load():
    packs = enabled_packs(ROOT, ROOT)
    assert sorted(p.name for p in packs) == sorted(json.loads((ROOT / ".cadence.json").read_text())["packs"])
    assert all(p.phases == [] and not p.has_gate for p in packs)  # the shape of a stage pack


@pytest.mark.parametrize("severity", ["must_fix", "MUST-FIX", "blocker", None])
def test_a_severity_that_is_not_nit_blocks(tmp_path, severity):
    """No severity, or a misspelling, is a must-fix, never a pass."""
    finding = {"rule_ref": "x", "evidence": "e", **({"severity": severity} if severity is not None else {})}
    gate = f"import json\nprint(json.dumps({{'findings': [{finding!r}]}}))\n"
    root = _pack(tmp_path, "sev", {"phases": ["planning"]}, gate=gate)
    [pack] = _load(root, tmp_path)
    assert gate_errors([(pack, {})], "planning", tmp_path) == ["pack sev gate failed: x: e"]
    _pack(tmp_path, "nit", {"phases": ["planning"]}, gate=gate.replace(repr(finding), repr({**finding, "severity": "nit"})))
    nit = [p for p in _load(root, tmp_path) if p.name == "nit"]
    assert gate_errors([(p, {}) for p in nit], "planning", tmp_path) == []


def test_the_sample_impl_gate_flags_only_the_planted_name(change):
    pack, _settings = _impl_pack(change)
    assert pack.phases == ["impl", "review"]
    assert run_pack_gate(pack, "impl", change, files=["src/a.py"]) == []
    [finding] = run_pack_gate(pack, "review", change, files=["src/a.py", f"docs/{PLANTED}"])
    assert finding["severity"] == "must-fix" and PLANTED in finding["evidence"]
    assert run_pack_gate(pack, "planning", change, files=[PLANTED]) == []


# --- a gate that crashes or prints no findings list fails, and names the pack ---------------------------


@pytest.mark.parametrize(("gate", "needle"), [
    (EXIT_1_SILENT, "printed no JSON"),
    ("pass\n", "printed no JSON"),
    ("print('not json')\n", "is not JSON"),
    ("print('{\"rules\": []}')\n", "no `findings` list"),
    ("print('[]')\n", "no `findings` list"),
], ids=["exit-1-no-stdout", "exit-0-no-stdout", "not-json", "no-findings-key", "bare-list"])
def test_a_gate_that_prints_no_findings_list_is_an_error_with_the_pack_name(tmp_path, gate, needle):
    [pack] = _load(_pack(tmp_path, "crashy", {"phases": ["impl"], "rules": [{"id": "r", "name": "r"}]}, gate), tmp_path)
    with pytest.raises(ValueError, match=needle) as err:
        run_pack_gate(pack, "impl", tmp_path, files=[])
    assert "crashy" in str(err.value)
    assert gate_errors([(pack, {})], "impl", tmp_path) == [str(err.value)]


def test_a_non_zero_exit_with_no_must_fix_finding_is_a_must_fix_finding(tmp_path):
    gate = "import sys\nprint('{\"findings\": [{\"severity\": \"nit\", \"rule_ref\": \"n\"}]}')\nsys.exit(3)\n"
    [pack] = _load(_pack(tmp_path, "nitpick", {"phases": ["review"]}, gate), tmp_path)
    findings = run_pack_gate(pack, "review", tmp_path, files=[])
    assert [f["severity"] for f in findings] == ["nit", "must-fix"]
    assert gate_errors([(pack, {})], "review", tmp_path) == ["pack nitpick gate failed: pack:nitpick:exit: "
                                                             "pack nitpick gate exited 3"]


def test_the_gate_reads_the_files_as_json_on_stdin(tmp_path):
    gate = ("import json, sys\nreq = json.load(sys.stdin)\n"
            "print(json.dumps({'findings': [{'severity': 'nit', 'evidence': ' '.join(req['files'])}]}))\n")
    [pack] = _load(_pack(tmp_path, "echo", {"phases": ["impl"]}, gate), tmp_path)
    assert run_pack_gate(pack, "impl", tmp_path, files=["a.py", "b.rs"])[0]["evidence"] == "a.py b.rs"


# --- `packs` of a config layer turns a pack's gate on ---------------------------------------------------


def test_a_project_layer_adds_a_pack_once(tmp_path):
    root = _pack(tmp_path, "both", {})
    _pack(tmp_path, "extra_only", {})
    install_config(root, packs=["both"])
    ws = _ws(tmp_path)
    project_config(ws, {"packs": {"append": ["both", "extra_only"]}})
    assert [p.name for p in enabled_packs(root, ws)] == ["both", "extra_only"]
    assert [p.name for p in enabled_packs(root, _ws(tmp_path, "plain"))] == ["both"]


def test_p9_runs_the_gate_of_a_pack_that_only_the_project_turns_on(tmp_path):
    context = tmp_path / ".context"
    make_session(context)
    plain, ws = _ws(tmp_path, "plain"), _ws(tmp_path)
    assert not any("_sample_fail_gate" in e for e in check_execution_entry(context, skill_root=ROOT, worktree=plain))
    project_config(ws, {"packs": ["_sample_fail_gate"]})
    assert any("pack _sample_fail_gate gate failed" in e
               for e in check_execution_entry(context, skill_root=ROOT, worktree=ws))


def test_an_unknown_pack_in_the_project_is_a_p9_error(tmp_path):
    context = tmp_path / ".context"
    make_session(context)
    ws = _ws(tmp_path)
    project_config(ws, {"packs": ["no-such-pack"]})
    assert any("no-such-pack" in e for e in check_execution_entry(context, skill_root=ROOT, worktree=ws))


# --- E3 and R3 run the impl and review gates on the files of the change ---------------------------------


def test_e3_fails_on_the_planted_file_and_names_the_pack(change):
    assert e3.pack_findings(change / ".context", change) == []
    write_files(change, {f"src/{PLANTED}": "x\n"})
    [finding] = e3.pack_findings(change / ".context", change)
    assert finding["severity"] == "must-fix" and f"pack {IMPL} gate failed" in finding["evidence"]
    proc = run_py("scripts/architecture_review.py", "--phase", "impl", "--check", cwd=change)
    assert proc.returncode == 1 and f"pack {IMPL} gate failed" in proc.stderr


def test_e3_record_mode_fails_on_a_pack_error(change):
    write_files(change, {f"src/{PLANTED}": "x\n"})
    proc = run_py("scripts/architecture_review.py", "--phase", "impl", cwd=change)
    assert proc.returncode == 1 and f"pack {IMPL} gate failed" in proc.stderr


def test_r3_fails_on_the_planted_file_and_passes_without_it(change, capsys):
    assert r3.run(change / ".context", check=True, base="main") == 0
    write_files(change, {f"lib/{PLANTED}": "x\n"})
    assert r3.run(change / ".context", check=True, base="main") == 1
    assert f"pack {IMPL} gate failed: pack:{IMPL}:planted-file" in capsys.readouterr().err


# --- a pack name is a plain directory name ---------------------------------------------------------------

@pytest.mark.parametrize("name", ["/tmp/evil-pack", "../evil", "evil/../../x", "./evil", "Evil"])
def test_a_pack_name_from_a_layer_cannot_leave_the_pack_dirs(tmp_path, name):
    """`packs` can come from the reviewed repo. A path there is an error, and no gate script runs."""
    evil = tmp_path / "evil"
    marker = tmp_path / "RAN"
    write_files(evil, {"pack.json": json.dumps({"name": "evil", "phases": ["planning"], "gate_script": "gate.py"}),
                       "gate.py": f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\nprint('{{\"findings\": []}}')\n"})
    root = _pack(tmp_path, "fine", {})
    ws = _ws(tmp_path)
    project_config(ws, {"packs": [str(evil) if name == "/tmp/evil-pack" else name]})
    with pytest.raises(ValueError):
        enabled_packs(root, ws)
    packs, errors = phase_packs(root, "planning", ws)
    assert packs == [] and len(errors) == 1
    assert not marker.exists()
