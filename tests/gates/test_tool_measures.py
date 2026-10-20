"""R3 `bugs` (ruff) and `types` (mypy): findings of pinned tools on the added lines of a change (docs/tool-gates.md).

Most tests replace the tool process with a stub. The `pinned_tools` tests start the real ruff and mypy;
without them installed they are skipped, and with CADENCE_REQUIRE_TOOLS=1 (CI) they fail.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cadence_config  # noqa: E402
import cognitive_complexity as cc  # noqa: E402
import cognitive_complexity_gate as gate  # noqa: E402
import tool_measures as tm  # noqa: E402
from impl_artifact import merge_base  # noqa: E402

from conftest import commit_file, feature_repo, git, project_config, write_files  # noqa: E402

pytestmark = pytest.mark.integration

CLEAN = "def total(xs):\n    return sum(xs)\n"
SHELL = "import subprocess\n\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n"
WRONG_TYPE = "def box_count(n: int) -> int:\n    return str(n)\n"
RECORD_KEYS = {"measure", "value", "threshold", "threshold_source", "verdict", "offenders", "unmeasured", "tool",
               "version", "args_sha256", "rules_sha256", "base", "head", "timed_out", "unsupported", "files"}


@pytest.mark.parametrize(("diff", "want"), [
    pytest.param("+++ b/a.py\n@@ -0,0 +1,3 @@\n+x\n+y\n+z\n", {"a.py": {1, 2, 3}}, id="new-file"),
    pytest.param("+++ b/a.py\n@@ -4 +4 @@\n-x\n+y\n@@ -9,0 +10,2 @@\n+p\n+q\n", {"a.py": {4, 10, 11}}, id="two-hunks"),
    pytest.param("+++ b/a.py\n@@ -3,2 +2,0 @@\n-x\n-y\n", {"a.py": set()}, id="lines-removed-only"),
    pytest.param("--- a/a.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x\n-y\n", {}, id="file-removed"),
    pytest.param("rename from old.py\nrename to new.py\n+++ b/new.py\n@@ -2 +2 @@\n-x\n+y\n", {"new.py": {2}},
                 id="rename"),
    pytest.param("+++ b/a.py\n@@ -1 +1 @@\n-x\n\\ No newline at end of file\n+y\n\\ No newline at end of file\n",
                 {"a.py": {1}}, id="no-newline-at-end"),
    pytest.param('+++ "b/t\\303\\251st \\"q\\".py"\n@@ -0,0 +1 @@\n+x\n', {'tést "q".py': {1}}, id="quoted-path"),
    pytest.param("+++ b/my file.py\t\n@@ -0,0 +1 @@\n+x\n", {"my file.py": {1}}, id="path-with-a-space"),
])
def test_added_lines_come_from_the_hunk_headers(diff, want):
    assert tm.parse_added(diff) == want


def test_only_findings_on_added_lines_count():
    findings = [("a.py", 2, "S602"), ("a.py", 7, "B904"), ("b.py", 1, "E722"), ("c.py", 3, "E9")]
    assert tm.on_added(findings, {"a.py": {2, 3}, "b.py": None}) == [("a.py", 2, "S602"), ("b.py", 1, "E722")]


def _stub(monkeypatch, *, findings=None, version_rc=0, timeout=False, rc=None, stdout=None):
    """Replace the tool process. `findings` maps a tool to (path, line, code) rows; returns the calls."""
    calls = []

    def run(args, cwd, _timeout):
        calls.append(args)
        tool = args[4].split("==")[0]
        if timeout:
            return None
        if "--version" in args:
            return subprocess.CompletedProcess(args, version_rc, f"{tool} {tm.TOOLS[tool]}\n", "")
        rows = (findings or {}).get(tool, [])
        if tool == "ruff":
            text = json.dumps([{"filename": str(Path(cwd) / p), "location": {"row": n}, "code": c, "message": "m"}
                               for p, n, c in rows])
        else:
            text = "".join(json.dumps({"file": p, "line": n, "code": c, "message": "m", "severity": "error"}) + "\n"
                           for p, n, c in rows)
        code = rc if rc is not None else int(bool(rows))
        return subprocess.CompletedProcess(args, code, text if stdout is None else stdout, "boom")

    monkeypatch.setattr(tm, "_run", run)
    return calls


def _sections(workspace: Path, **raw: dict) -> dict:
    """The resolved bugs and types sections for `workspace`, whose project layer (.cadence.json) holds `raw`."""
    project_config(workspace, raw)
    return {section: cc.load_config(ROOT, section, workspace) for section in ("bugs", "types")}


def _judge(tmp_path, base: dict[str, str], head: dict[str, str], **raw) -> dict[str, dict]:
    tmp_path.mkdir(exist_ok=True)
    repo = feature_repo(tmp_path / "repo", {"README.md": "x\n", **base})
    write_files(repo, head)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    records = tm.measure(repo, merge_base(repo, "main"), sorted(head), _sections(repo, **raw))
    return {r["measure"]: r for r in records}


@pytest.mark.parametrize(("tool", "measure"), [("ruff", "bugs"), ("mypy", "types")])
def test_a_finding_on_an_added_line_fails_and_one_on_an_old_line_does_not(tmp_path, monkeypatch, tool, measure):
    _stub(monkeypatch, findings={tool: [("src/a.py", 2, "X1"), ("src/a.py", 5, "X2")]})
    base, head = {"src/a.py": CLEAN + "\n\n\nx = 1\n"}, {"src/a.py": CLEAN.replace("sum", "max") + "\n\n\nx = 1\n"}
    got = _judge(tmp_path / "new", base, head)
    assert (got[measure]["verdict"], got[measure]["value"], got[measure]["offenders"]) == ("fail", 1, ["src/a.py:2 X1 m"])
    _stub(monkeypatch, findings={tool: [("src/a.py", 5, "X2")]})
    got = _judge(tmp_path / "old", base, head)
    assert (got[measure]["verdict"], got[measure]["value"]) == ("pass", 0)


def test_each_record_names_the_tool_the_rules_and_the_change(tmp_path, monkeypatch):
    _stub(monkeypatch)
    got = _judge(tmp_path, {}, {"src/a.py": CLEAN})
    repo = tmp_path / "repo"
    for measure, (tool, rules) in {"bugs": ("ruff", tm.RUFF_RULES), "types": ("mypy", tm.MYPY_RULES)}.items():
        record = got[measure]
        assert set(record) == RECORD_KEYS and (record["tool"], record["version"]) == (tool, tm.TOOLS[tool])
        assert record["rules_sha256"] == hashlib.sha256(json.dumps(rules).encode()).hexdigest()
        assert (record["base"], record["head"]) == (merge_base(repo, "main"), git(repo, "rev-parse", "HEAD").strip())
        assert (record["threshold"], record["threshold_source"], record["timed_out"], record["files"]) == (0, "default", False, 1)


def test_the_tool_runs_offline_at_its_pin_with_a_temporary_cache(tmp_path, monkeypatch):
    calls = _stub(monkeypatch)
    _judge(tmp_path, {}, {"src/a.py": CLEAN})
    runs = [c for c in calls if "--version" not in c]
    assert [c[:5] for c in runs] == [["uv", "tool", "run", "--offline", "ruff==0.17.0"],
                                     ["uv", "tool", "run", "--offline", "mypy==2.4.0"]]
    assert {"--no-cache", "--isolated"} <= set(runs[0]) and runs[0][-1] == "src/a.py"
    cache = runs[1][runs[1].index("--cache-dir") + 1]
    assert "--no-incremental" in runs[1] and not Path(cache).exists() and not str(cache).startswith(str(tmp_path))


@pytest.mark.parametrize(("setting", "verdict"), [("warn", "warn"), ("fail", "fail")])
def test_a_missing_tool_is_unsupported_and_names_the_warm_command(tmp_path, monkeypatch, setting, verdict):
    calls = _stub(monkeypatch, version_rc=2)
    got = _judge(tmp_path, {}, {"src/a.py": SHELL}, bugs={"unsupported": setting}, types={"unsupported": setting})
    for measure, tool in (("bugs", "ruff"), ("types", "mypy")):
        assert got[measure]["verdict"] == verdict and got[measure]["value"] == 0
        assert f"run `uv tool run {tool}=={tm.TOOLS[tool]} --version` once" in got[measure]["unsupported"]
        assert got[measure]["unsupported"] in got[measure]["unmeasured"]
    assert all("--version" in c for c in calls)


def test_a_tool_timeout_is_unsupported_never_a_pass(tmp_path, monkeypatch):
    _stub(monkeypatch, timeout=True)
    got = _judge(tmp_path, {}, {"src/a.py": CLEAN}, bugs={"timeout_seconds": 5})
    assert (got["bugs"]["verdict"], got["bugs"]["timed_out"]) == ("warn", True)
    assert got["bugs"]["unsupported"] == "ruff 0.17.0 timed out after 5 s"


@pytest.mark.parametrize(("rc", "stdout"), [(2, "[]"), (1, "not json")], ids=["exit-code-not-on-the-list", "not-json"])
def test_a_report_that_cannot_be_read_fails(tmp_path, monkeypatch, rc, stdout):
    _stub(monkeypatch, rc=rc, stdout=stdout)
    got = _judge(tmp_path, {}, {"src/a.py": CLEAN})
    assert got["bugs"]["verdict"] == "fail" and got["bugs"]["offenders"][0].startswith("ruff ")


def test_two_files_of_one_name_go_to_mypy_in_separate_runs(tmp_path, monkeypatch):
    calls = _stub(monkeypatch)
    _judge(tmp_path, {}, {"a/conftest.py": CLEAN, "b/conftest.py": CLEAN, "b/x.py": CLEAN})
    runs = [c for c in calls if "mypy==2.4.0" in c and "--version" not in c]
    assert [c[c.index(tm.MYPY_RULES[-1]) + 1:] for c in runs] == [["a/conftest.py", "b/x.py"], ["b/conftest.py"]]


def test_a_non_python_file_follows_unsupported_and_no_change_runs_no_tool(tmp_path, monkeypatch):
    calls = _stub(monkeypatch)
    got = _judge(tmp_path, {}, {"src/a.ts": "export const a = 1;\n"}, types={"unsupported": "fail"})
    assert (got["bugs"]["verdict"], got["types"]["verdict"], calls) == ("warn", "fail", [])
    assert got["types"]["unmeasured"] == ["src/a.ts: not Python"]


def test_the_stop_guard_walk_runs_no_tool_and_says_so(tmp_path, monkeypatch):
    calls = _stub(monkeypatch)
    monkeypatch.setenv("CADENCE_GATES_READONLY", "1")
    got = _judge(tmp_path, {}, {"src/a.py": SHELL}, bugs={"unsupported": "fail"})
    assert calls == [] and got["bugs"]["verdict"] == "warn"
    assert "CADENCE_GATES_READONLY=1" in got["bugs"]["unsupported"]


@pytest.mark.parametrize(("raw", "want"), [
    ({"max": 3}, (0, "default (project value 3 ignored)")),
    ({"max": 0}, (0, "project")),
    ({"timeout_seconds": 600}, (60, "default (project value 600 ignored)")),
    ({"timeout_seconds": 10}, (10, "project")),
])
def test_config_can_only_tighten_bugs_and_types(tmp_path, raw, want):
    [key] = raw
    for cfg in _sections(tmp_path, bugs=raw, types=raw).values():
        assert (cfg[key], cfg["threshold_source"][key]) == want


def test_the_repo_config_names_bugs_and_types_at_their_defaults():
    # This repo's own layers (its .cadence.json) keep bugs and types at the shipped defaults.
    for section in tm.MEASURES:
        cfg = cc.load_config(ROOT, section, ROOT)
        assert set(cfg.pop("threshold_source").values()) == {"default"} and cfg == cc.SECTIONS[section]


@pytest.mark.parametrize("raw", [{"max": -1}, {"timeout_seconds": 0}, {"tool": "ruff"}, {"unsupported": "skip"}])
def test_a_bad_bugs_or_types_value_fails_closed(tmp_path, raw):
    with pytest.raises(cadence_config.ConfigError, match=r"\.cadence\.json\): types\."):
        _sections(tmp_path, types=raw)


def test_r3_fails_on_a_bugs_finding_and_lists_it(tmp_path, monkeypatch, capsys):
    _stub(monkeypatch, findings={"ruff": [("src/a.py", 5, "S602")]})
    repo = feature_repo(tmp_path / "repo", {"README.md": "x\n"})
    commit_file(repo, "head", "a.py", "x = 1\n")
    write_files(repo, {"src/a.py": SHELL})
    monkeypatch.chdir(repo)
    assert gate.run(repo / ".context", base="main") == 1
    err = capsys.readouterr().err
    assert "bugs: src/a.py:5 S602 m" in err and "bugs.max is 0" in err and "must name its rule" in err
    crumb = json.loads((repo / ".context" / "gates" / "cognitive-complexity.json").read_text())
    assert [(m["measure"], m["verdict"]) for m in crumb["measures"][-2:]] == [("bugs", "fail"), ("types", "pass")]


# --- The real tools. A missing tool skips these, or fails them under CADENCE_REQUIRE_TOOLS=1.


def test_real_ruff_counts_the_new_finding_and_not_the_old_one(tmp_path, pinned_tools):
    old = "def parse(text):\n    try:\n        return int(text)\n    except:  # noqa\n        return 0\n"
    got = _judge(tmp_path, {"src/a.py": old}, {"src/a.py": old + "\n\n" + SHELL})
    assert (got["bugs"]["verdict"], got["bugs"]["value"]) == ("fail", 1)
    assert got["bugs"]["offenders"][0].startswith("src/a.py:12 S602 ")


def test_real_mypy_counts_the_new_error_and_not_the_old_one(tmp_path, pinned_tools):
    got = _judge(tmp_path, {"src/a.py": WRONG_TYPE}, {"src/a.py": WRONG_TYPE + "\n\n" + WRONG_TYPE.replace("box", "bag")})
    assert (got["types"]["verdict"], got["types"]["value"]) == ("fail", 1)
    assert got["types"]["offenders"][0].startswith("src/a.py:6 return-value ")
    assert [p.name for p in (tmp_path / "repo").iterdir() if p.name.endswith("_cache")] == []


# PR #168 R1 N-5: B008, B904 and S108 are opinion rules, not bugs. S110 (try, except, pass) stays.
OPINION = ("def Depends(x=None):\n    return x\n\n\ndef handler(db=Depends(None)):\n"
           "    try:\n        open('/tmp/x').read()\n    except OSError:\n        pass\n"
           "    try:\n        return int(db)\n    except ValueError:\n        raise RuntimeError('bad')\n")


def test_the_bugs_rules_ignore_the_opinion_rules_and_keep_s110():
    ignored = tm.RUFF_RULES[tm.RUFF_RULES.index("--ignore") + 1].split(",")
    assert {"B008", "B904", "S108"} <= set(ignored) and "S110" not in ignored


def test_real_ruff_does_not_count_an_opinion_rule(tmp_path, pinned_tools):
    got = _judge(tmp_path, {}, {"src/a.py": OPINION})
    assert (got["bugs"]["verdict"], got["bugs"]["value"], got["bugs"]["offenders"]) == ("pass", 0, [])
