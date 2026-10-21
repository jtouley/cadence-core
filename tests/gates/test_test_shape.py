"""test_shape.py computes the R2 DRY lists of the changed test files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import feature_repo, project_config, write_files

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import test_shape  # noqa: E402

CONFTEST = """def make_thing(path):
    path.mkdir()
    (path / "x").write_text("1")
    return path


def root():
    return ROOT


def git(repo, *args):
    return run(repo, args)
"""
BASE = {"tests/conftest.py": CONFTEST, "scripts/mod.py": "X = 1\n", "scripts/other.py": "Y = 2\n",
        "tests/test_mod.py": "import mod\n\n\ndef test_a():\n    pass\n"}
THREE = "".join(f"\n\ndef test_{i}():\n    assert {i}\n" for i in "abc")
NONE: dict[str, list[str]] = {"helpers_reimplemented": [], "helpers_reused": [], "thin_files": []}

CASES = [
    ("same name", "def git(repo):\n    return 1\n" + THREE,
     {"helpers_reimplemented": ["tests/test_new.py: git (tests/conftest.py: git)"]}),
    ("renamed copy of the body", "def _mk(where):\n    '''Doc.'''\n    where.mkdir()\n    (where / 'y').write_text('2')\n"
     "    return where\n" + THREE, {"helpers_reimplemented": ["tests/test_new.py: _mk (tests/conftest.py: make_thing)"]}),
    ("a one-line body is not a copy", "def _here():\n    return HERE\n" + THREE, {}),
    ("a test is not a helper", "def test_root():\n    return ROOT\n" + THREE, {}),
    ("conftest names that a file imports", "from conftest import git, root\n" + THREE,
     {"helpers_reused": ["tests/test_new.py: git", "tests/test_new.py: root"]}),
    ("a thin file", "import mod\n\n\ndef test_x():\n    assert mod.X\n",
     {"thin_files": ["tests/test_new.py (1 tests of scripts/mod.py, also tested by tests/test_mod.py)"]}),
    ("three tests are not thin", "import mod\n" + THREE, {}),
    ("a module that no other file tests", "import other\n\n\ndef test_x():\n    assert other.Y\n", {}),
]


@pytest.mark.parametrize(("source", "want"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_the_lists_are_computed(tmp_path, source, want):
    write_files(tmp_path, {**BASE, "tests/test_new.py": source})
    result = test_shape.measure(tmp_path, ["tests/test_new.py", "scripts/mod.py"], test_shape.DEFAULTS)
    lists = {m["measure"]: m["offenders"] for m in result["measures"]} | {"helpers_reused": result["helpers_reused"]}
    assert lists == {**NONE, **want}
    verdicts = {m["measure"]: m["verdict"] for m in result["measures"]}
    assert verdicts == {k: "fail" if want.get(k) else "pass" for k in ("helpers_reimplemented", "thin_files")}


@pytest.mark.parametrize("key", ["max_helpers_reimplemented", "max_thin_files"])
def test_a_config_value_looser_than_the_default_is_ignored(tmp_path, key):
    project_config(tmp_path, {"test_shape": {key: 2}})
    cfg = test_shape.load_config(ROOT, "test_shape", tmp_path)
    assert cfg[key] == 0 and cfg["threshold_source"][key] == "default (project value 2 ignored)"
    write_files(tmp_path, {**BASE, "tests/test_new.py": "def git(repo):\n    return 1\n\n\ndef test_x():\n    import mod\n"})
    records = test_shape.measure(tmp_path, ["tests/test_new.py"], cfg)["measures"]
    assert [(r["verdict"], r["threshold_source"]) for r in records if r["measure"] == key[4:]] == [
        ("fail", "default (project value 2 ignored)")]


def test_the_cli_judges_every_test_file_under_a_directory(tmp_path, capsys):
    write_files(tmp_path, {**BASE, "tests/test_new.py": "import mod\n\n\ndef test_x():\n    assert mod.X\n"})
    assert test_shape.main([str(tmp_path)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [m["verdict"] for m in out["measures"]] == ["pass", "fail"]
    with pytest.raises(SyntaxError):
        write_files(tmp_path, {"tests/test_bad.py": "def broken(:\n"})
        test_shape.main([str(tmp_path)])


def test_the_cli_reads_the_change_with_the_base_that_r3_uses(tmp_path, monkeypatch, capsys):
    repo = feature_repo(tmp_path / "repo", BASE)
    write_files(repo, {"tests/test_new.py": "from conftest import git\n" + THREE})
    monkeypatch.chdir(repo)
    assert test_shape.main(["--change"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["base"] == "main" and out["helpers_reused"] == ["tests/test_new.py: git"]
    monkeypatch.setenv("CADENCE_BASE_REF", "nope")
    assert test_shape.main(["--change"]) == 1 and "base ref nope does not resolve" in capsys.readouterr().err


# --- the layout of the target repo (gh-180) ---------------------------------------------

SRC = {"pyproject.toml": '[project]\nname = "My-Pkg"\n', "src/my_pkg/__init__.py": "", "src/my_pkg/mod.py": "X = 1\n",
       "tests/unit/conftest.py": CONFTEST, "tests/unit/test_mod.py": "from my_pkg import mod\n" + THREE,
       "tools/run.py": ""}
LAYOUTS = [
    ("the cadence layout", BASE, ["tests"], ["tests/conftest.py"], ["scripts"]),
    ("a project name in a src layout", SRC, ["tests"], ["tests/unit/conftest.py"], ["src/my_pkg"]),
    ("setuptools packages", {"pyproject.toml": '[tool.setuptools]\npackages = ["core", "core.sub"]\n',
                             "core/__init__.py": "", "core/sub/__init__.py": "", "tests/test_a.py": ""},
     ["tests"], [], ["core", "core/sub"]),
    ("setuptools find", {"pyproject.toml": '[tool.setuptools.packages.find]\nwhere = ["lib"]\n',
                         "lib/alpha/__init__.py": "", "tests/conftest.py": ""}, ["tests"], ["tests/conftest.py"],
     ["lib/alpha"]),
    ("setup.cfg", {"setup.cfg": "[metadata]\nname = beta\n[options]\npackages = find:\n[options.packages.find]\n"
                                "where = src\n", "src/beta/__init__.py": "", "src/gamma/__init__.py": ""},
     [], [], ["src/beta", "src/gamma"]),
    ("no declaration", {"app/main.py": "", "app/sub/x.py": "", "docs/a.md": "", "test/test_a.py": "",
                        ".venv/lib/y.py": "", "setup.py": "", "app/sub/tests/test_b.py": ""},
     ["test"], [], ["app", "app/sub"]),
    ("a declared name with no directory", {"pyproject.toml": '[project]\nname = "cadence-harness"\n',
                                           "scripts/a.py": ""}, [], [], ["scripts"]),
]


@pytest.mark.parametrize(("files", "tests", "conftest", "runtime"), [c[1:] for c in LAYOUTS],
                         ids=[c[0] for c in LAYOUTS])
def test_the_layout_is_detected(tmp_path, files, tests, conftest, runtime):
    write_files(tmp_path, files)
    assert test_shape.layout(tmp_path, {}) == {"test_dirs": tests, "conftest": conftest, "runtime_dirs": runtime}


def test_a_src_layout_is_measured(tmp_path):
    write_files(tmp_path, {**SRC, "tests/unit/test_new.py": "from my_pkg import mod\n\n\ndef git(repo):\n"
                                                             "    return 1\n\n\ndef test_x():\n    assert mod.X\n"})
    result = test_shape.measure(tmp_path, ["tests/unit/test_new.py"], test_shape.DEFAULTS)
    assert {m["measure"]: m["offenders"] for m in result["measures"]} == {
        "helpers_reimplemented": ["tests/unit/test_new.py: git (tests/unit/conftest.py: git)"],
        "thin_files": ["tests/unit/test_new.py (1 tests of src/my_pkg/mod.py, also tested by tests/unit/test_mod.py)"]}
    assert result["layout"]["source"] == dict.fromkeys(test_shape.LAYOUT_KEYS, "detected")


@pytest.mark.parametrize(("files", "unsupported", "want"), [
    pytest.param({"README.md": "x\n"}, "warn", {"helpers_reimplemented": "warn", "thin_files": "warn"}, id="nothing"),
    pytest.param({"README.md": "x\n"}, "fail", {"helpers_reimplemented": "fail", "thin_files": "fail"}, id="fail"),
    pytest.param({"tests/test_a.py": "", "scripts/a.py": ""}, "fail", {"helpers_reimplemented": "fail",
                                                                     "thin_files": "pass"}, id="no-conftest"),
])
def test_a_layout_that_is_not_found_is_unsupported(tmp_path, files, unsupported, want):
    write_files(tmp_path, files)
    records = test_shape.measure(tmp_path, [], {**test_shape.DEFAULTS, "unsupported": unsupported})["measures"]
    assert {r["measure"]: r["verdict"] for r in records} == want
    unmeasured = [r["unmeasured"] for r in records if r["verdict"] != "pass"]
    assert unmeasured and all(why and all(": set test_shape.layout." in w for w in why) for why in unmeasured)


def test_the_config_layout_wins(tmp_path):
    write_files(tmp_path, {"checks/helpers.py": CONFTEST, "code/mod.py": "X = 1\n", "scripts/mod.py": "X = 1\n",
                           "checks/test_mod.py": "import mod\n" + THREE,
                           "checks/test_new.py": "import mod\n\n\ndef test_x():\n    assert mod.X\n"})
    given = {"test_dirs": ["checks"], "conftest": ["checks/helpers.py"], "runtime_dirs": ["code/"]}
    result = test_shape.measure(tmp_path, ["checks/test_new.py"], {**test_shape.DEFAULTS, "layout": given})
    assert result["measures"][1]["offenders"] == ["checks/test_new.py (1 tests of code/mod.py, also tested by "
                                                  "checks/test_mod.py)"]
    assert result["layout"]["source"] == dict.fromkeys(test_shape.LAYOUT_KEYS, "config")


@pytest.mark.parametrize("given", [
    pytest.param([], id="not-a-map"), pytest.param({"tests": ["t"]}, id="unknown-key"),
    pytest.param({"conftest": "tests/conftest.py"}, id="not-a-list"),
    pytest.param({"conftest": ["tests/nope.py"]}, id="a-path-that-is-not-there"),
    pytest.param({"runtime_dirs": ["tests/conftest.py"]}, id="a-file-for-a-directory"),
])
def test_a_bad_layout_fails_closed(tmp_path, given):
    write_files(tmp_path, BASE)
    with pytest.raises(ValueError, match="test_shape.layout"):
        test_shape.layout(tmp_path, given)


def test_the_config_section_keeps_the_layout_map(tmp_path):
    given = {"test_dirs": ["tests"]}
    project_config(tmp_path, {"test_shape": {"layout": given}})
    assert test_shape.load_config(ROOT, "test_shape", tmp_path)["layout"] == given


def test_a_measure_exclude_prefix_is_not_a_runtime_dir(tmp_path):
    write_files(tmp_path, {"app/main.py": "", "corpus/x/bad.py": ""})
    assert test_shape.layout(tmp_path, {}, ("corpus/",))["runtime_dirs"] == ["app"]


def test_this_repo_is_detected():
    found = test_shape.layout(ROOT, {}, test_shape.load_exclude(ROOT, ROOT))
    assert found["test_dirs"] == ["tests"] and found["conftest"] == ["tests/conftest.py"]
    assert {"scripts", "hooks", "shared", "shared/scripts"} <= set(found["runtime_dirs"])
