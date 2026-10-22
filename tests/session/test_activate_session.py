"""activate_session.py: in-process main, peer models, the lean record marker, the verdict join switch, and the owner from the shell hook."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TypedDict, Unpack

import pytest

from conftest import (CLAUDE_CHAT, HostEnv, activate, activation_repo, branch_repo, code_paths, git_repo, project_config,
                      read_session, run_py, skill_gate_hook, worktree_session)

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import cadence_config  # noqa: E402
import peer_models  # noqa: E402
import session_store  # noqa: E402


# --- activate_session_main: In-process activate_session.main on an isolated temp workspace.

pytestmark = pytest.mark.integration


def _invoke(monkeypatch, tmp_path: Path, *flags: str) -> tuple[int, str, str]:
    import activate_session

    monkeypatch.setattr(
        sys,
        "argv",
        ["activate_session.py", "--workspace", str(tmp_path), *flags],
    )
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code = activate_session.main()
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize(
    "kind",
    ["fresh", "resume_empty", "resume_plan"],
    ids=["fresh", "resume_empty", "resume_plan"],
)
def test_activate_session_main_fresh_or_resume_writes_session(
    scripts_path, tmp_path, monkeypatch, kind
):
    flags = ["--task-id", "gh-1"]
    if kind == "fresh":
        flags.append("--fresh")
        expected_fresh = True
        expected_slug = "activate-session"
    elif kind == "resume_empty":
        # Nothing to resume, so --resume starts a fresh run and says so.
        flags.append("--resume")
        expected_fresh = True
        expected_slug = "activate-session"
    else:
        # A resume of an active run of the task finds the run's artifacts.
        assert _invoke(monkeypatch, tmp_path, "--task-id", "gh-1", "--fresh")[0] == 0
        flags.append("--resume")
        expected_fresh = False
        expected_slug = "plan-review"
        monkeypatch.chdir(tmp_path)
        plans = tmp_path / ".cursor" / "plans"
        plans.mkdir(parents=True)
        (plans / "gh-1-floor.plan.md").write_text("# plan\n", encoding="utf-8")

    code, _out, err = _invoke(monkeypatch, tmp_path, *flags)
    session = read_session(tmp_path)
    assert code == 0
    assert session["task_id"] == "gh-1"
    assert session["fresh_run"] is expected_fresh
    assert session["resume_slug"] == expected_slug
    assert session["pipeline_run_id"]
    assert "Cadence Session Activated" in err
    assert ("resume_fallback" in session) is (kind == "resume_empty")


def test_activate_session_main_deactivate_clears_active_skill(
    scripts_path, tmp_path, monkeypatch
):
    code, _out, _err = _invoke(monkeypatch, tmp_path, "--fresh", "--task-id", "gh-1")
    assert code == 0
    # An open run ends only after R6 or a recorded hard stop.
    code, _out, _err = _invoke(monkeypatch, tmp_path, "--hard-stop", "not-needed", "--reason", "test")
    assert code == 0
    code, out, _err = _invoke(monkeypatch, tmp_path, "--deactivate")
    session = read_session(tmp_path)
    assert code == 0
    assert session["active_skill"] is None
    assert session["completed_at"]
    assert '"deactivated": true' in out.lower() or "deactivated" in out


@pytest.mark.parametrize("flags,message", [
    pytest.param(("--fresh", "--resume", "--task-id", "gh-1"), "mutually exclusive",
                 id="fresh_and_resume_are_mutually_exclusive_fails"),
    pytest.param(("--supersede", "--task-id", "gh-1"), "--fresh", id="supersede_without_fresh_fails"),
    pytest.param(("--check",), "no active cadence session", id="check_without_session_fails"),
    pytest.param(("--fresh",), "Cannot determine task ID", id="missing_task_id_fails"),
])
def test_activate_session_main_bad_flags_fail_without_a_session(
    scripts_path, tmp_path, monkeypatch, flags, message
):
    code, _out, err = _invoke(monkeypatch, tmp_path, *flags)
    assert code == 1
    assert message in err
    assert not (tmp_path / ".context" / "cadence-session.json").is_file()


def test_activate_session_main_resume_slug_not_in_registry_fails(
    scripts_path, tmp_path, monkeypatch
):
    import activate_session

    monkeypatch.setattr(activate_session, "stage_slugs", lambda *_a, **_k: [])
    code, _out, err = _invoke(monkeypatch, tmp_path, "--fresh", "--task-id", "gh-1")
    assert code == 1
    assert "not in stages.json" in err
    assert not (tmp_path / ".context" / "cadence-session.json").is_file()


# --- activate_session_peer_models: A1 reads `peer_models` once, validates it, and snapshots it into the session.
#
# A1 resolves the config layers (scripts/cadence_config.py) for each fresh or resumed run.
# Only a fresh run takes `peer_models` from it: a resumed run keeps its own snapshot, and a
# bad `peer_models` value never fails a resume or an already active session. A bad layer
# fails the activation. A failure writes no session and keeps the orchestrator note for the next try.

SLUGS = ("validate", "plan-create", "plan-review", "adversarial-review", "test-benchmark-plan",
         "pr-review-local", "test-benchmark-pr")


class _UserEnv(TypedDict, total=False):
    """The sandbox user home of a run (`_activate_in`)."""

    HOME: str
    CADENCE_CLAUDE_SETTINGS_PATH: str


def _activate(repo: Path, root: Path, *flags: str, claude: bool = True,
              **env: Unpack[_UserEnv]) -> subprocess.CompletedProcess[str]:
    """activate_session.py --from-branch from `root`; the caller checks the exit code."""
    chat: HostEnv = CLAUDE_CHAT if claude else {}
    return run_py("scripts/activate_session.py", "--from-branch", *flags, cwd=repo, home=root, **chat, **env)


def _config(root: Path, repo: Path, peer_models_value) -> None:
    """Set `peer_models` in the project layer `.cadence.json` of `repo`, over the defaults of `root`."""
    project_config(repo, {"peer_models": peer_models_value})
    live = cadence_config.load(root, repo)
    assert live.get("peer_models") == peer_models_value and live.source("peer_models") == "project"


@pytest.fixture
def feature_branch_repo(tmp_path):
    return branch_repo(tmp_path / "repo")


# --- A1 (subprocess) ---------------------------------------------------------------------------


def test_no_peer_models_keeps_the_session_keys(feature_branch_repo, make_root, tmp_path):
    plain = make_root()
    proc = _activate(feature_branch_repo, plain, "--fresh")
    assert proc.returncode == 0, proc.stderr
    baseline_keys = set(session_store.load_worktree_session(feature_branch_repo))
    assert "peer_models" not in baseline_keys

    other = branch_repo(tmp_path / "other")
    proc = _activate(other, make_root(peer_models={}), "--fresh")
    assert proc.returncode == 0, proc.stderr
    assert set(session_store.load_worktree_session(other)) == baseline_keys


def test_default_fills_every_peer_slug(feature_branch_repo, make_root):
    root = make_root(peer_models={"default": "haiku"})
    proc = _activate(feature_branch_repo, root, "--fresh")
    assert proc.returncode == 0, proc.stderr
    assert session_store.load_worktree_session(feature_branch_repo)["peer_models"] == dict.fromkeys(SLUGS, "haiku")


def test_an_own_entry_wins_over_default(feature_branch_repo, make_root):
    root = make_root(peer_models={"default": "haiku", "plan-review": "opus"})
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    expected = dict.fromkeys(SLUGS, "haiku")
    expected["plan-review"] = "opus"
    assert session_store.load_worktree_session(feature_branch_repo)["peer_models"] == expected


def test_a_host_other_than_claude_fails_with_a_non_empty_map(feature_branch_repo, make_root):
    proc = _activate(feature_branch_repo, make_root(peer_models={"plan-review": "opus"}), "--fresh", claude=False)
    assert proc.returncode == 2
    assert "cannot pass a model to a peer" in proc.stdout + proc.stderr
    assert session_store.load_worktree_session(feature_branch_repo) is None


def test_an_empty_map_activates_on_any_host(feature_branch_repo, make_root):
    proc = _activate(feature_branch_repo, make_root(peer_models={}), "--fresh", claude=False)
    assert proc.returncode == 0, proc.stderr
    assert "peer_models" not in session_store.load_worktree_session(feature_branch_repo)


@pytest.mark.parametrize("edited", [
    pytest.param({"plan-review": "haiku"}, id="resume_keeps_the_snapshot_after_a_config_edit"),
    pytest.param({"plan-update": "opus"}, id="resume_bad_config_does_not_fail"),
])
def test_resume_keeps_the_snapshot_whatever_the_config_says(feature_branch_repo, make_root, edited):
    root = make_root(peer_models={"plan-review": "opus"})
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    record = session_store.load_worktree_session(feature_branch_repo)["config"]
    _config(root, feature_branch_repo, edited)
    proc = _activate(feature_branch_repo, root, "--resume")
    assert proc.returncode == 0, proc.stderr
    session = session_store.load_worktree_session(feature_branch_repo)
    assert session["peer_models"] == {"plan-review": "opus"}
    assert session["config"] == record  # the resumed run keeps the config record of its fresh start


def test_resume_pre_cd58_run_gets_no_peer_models(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    assert "peer_models" not in session_store.load_worktree_session(feature_branch_repo)
    _config(root, feature_branch_repo, {"default": "haiku"})
    proc = _activate(feature_branch_repo, root, "--resume")
    assert proc.returncode == 0, proc.stderr
    assert "peer_models" not in session_store.load_worktree_session(feature_branch_repo)


def test_resume_none_found_is_a_fresh_run_and_validates(feature_branch_repo, make_root):
    proc = _activate(feature_branch_repo, make_root(peer_models={"plan-update": "opus"}), "--resume")
    assert proc.returncode == 2
    assert "FAIL  peer_models:" in proc.stdout + proc.stderr


def test_already_active_bad_config_prints_the_record(feature_branch_repo, make_root):
    root = make_root(peer_models={"plan-review": "opus"})
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    _config(root, feature_branch_repo, "not a map")
    proc = _activate(feature_branch_repo, root)
    assert proc.returncode == 0, proc.stderr
    assert "already active" in proc.stderr


def test_plain_activation_does_not_resume(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    first = session_store.load_worktree_session(feature_branch_repo)["pipeline_run_id"]
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    assert session_store.load_worktree_session(feature_branch_repo)["pipeline_run_id"] != first


def test_note_kept_on_failure(feature_branch_repo, make_root):
    ctx = feature_branch_repo / ".context"
    ctx.mkdir(exist_ok=True)
    session_store.write_pending_orchestrator("agent-x", ctx)
    root = make_root(peer_models={"plan-update": "opus"})
    proc = _activate(feature_branch_repo, root, "--fresh")
    assert proc.returncode == 2
    out = proc.stdout + proc.stderr
    assert "FAIL  peer_models:" in out and "plan-update" in out
    assert session_store.load_worktree_session(feature_branch_repo) is None
    assert any((ctx / "sessions" / "pending-orchestrator").iterdir())

    _config(root, feature_branch_repo, {"plan-review": "opus"})
    proc = _activate(feature_branch_repo, root, "--fresh")
    assert proc.returncode == 0, proc.stderr
    assert session_store.load_worktree_session(feature_branch_repo)["orchestrator_agent_ids"] == ["agent-x"]


@pytest.mark.parametrize("layer", [
    pytest.param('{"peer_models": ', id="unparsable_layer"),
    pytest.param(json.dumps({"no_such_key": 1}), id="unknown_key"),
    pytest.param(json.dumps({"context_budget": {"mode": "loud"}}), id="bad_value"),
])
def test_a_bad_config_layer_fails_activation(feature_branch_repo, make_root, layer):
    ctx = feature_branch_repo / ".context"
    ctx.mkdir(exist_ok=True)
    session_store.write_pending_orchestrator("agent-x", ctx)
    path = feature_branch_repo / ".cadence.json"
    path.write_text(layer, encoding="utf-8")
    proc = _activate(feature_branch_repo, make_root(), "--fresh")
    assert proc.returncode == 2
    assert "FAIL  config:" in proc.stderr and str(path.resolve()) in proc.stderr
    assert session_store.load_worktree_session(feature_branch_repo) is None
    assert any((ctx / "sessions" / "pending-orchestrator").iterdir())


def test_a_fresh_run_records_the_config_layers_and_their_sha256(feature_branch_repo, make_root):
    root = make_root()
    layer = project_config(feature_branch_repo, {"context_budget": {"mode": "enforce"}})
    proc = _activate(feature_branch_repo, root, "--fresh")
    assert proc.returncode == 0, proc.stderr
    record = session_store.load_worktree_session(feature_branch_repo)["config"]
    layers = {entry["name"]: entry for entry in record["layers"]}
    assert list(layers) == ["default", "profile:standard", "project"]
    for name, path in (("default", root / "cadence.defaults.json"), ("project", layer.resolve())):
        assert layers[name]["path"] == str(path)
        assert layers[name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record["values"]["context_budget"]["mode"] == "enforce"
    assert record["values"] == cadence_config.load(root, feature_branch_repo).values


def _check(repo: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return _activate(repo, root, "--check")


def test_check_fails_on_peer_models_with_another_host(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    session = session_store.load_worktree_session(feature_branch_repo)
    session_store.save_session({**session, "peer_models": {"plan-review": "opus"}, "host": "other"}, feature_branch_repo)
    proc = _check(feature_branch_repo, root)
    assert proc.returncode == 1
    assert "peer_models" in proc.stderr


def test_check_positive_with_claude_host(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    session = session_store.load_worktree_session(feature_branch_repo)
    session_store.save_session({**session, "peer_models": {"plan-review": "opus"}, "host": "claude"}, feature_branch_repo)
    assert _check(feature_branch_repo, root).returncode == 0


# --- resolve (in process) ------------------------------------------------------------------------

MAP_ERROR = "peer_models must be a map of stage slug to model alias"


@pytest.mark.parametrize("raw", [["opus"], "opus", None])
def test_resolve_rejects_a_value_that_is_not_a_map(raw):
    assert peer_models.resolve(raw, "claude") == ({}, [MAP_ERROR])


@pytest.mark.parametrize("key", ["plan-update", "implement", "draft-pr", "Plan-Review"])
def test_resolve_rejects_an_unknown_key(key):
    resolved, errors = peer_models.resolve({key: "opus"}, "claude")
    assert resolved == {}
    assert len(errors) == 1
    assert repr(key) in errors[0] and "plan-review" in errors[0] and "default" in errors[0]


@pytest.mark.parametrize("key", ["plan-review", "default"])
@pytest.mark.parametrize("value", ["claude-opus-5-5", "Opus", 3, None, "", "gpt"])
def test_resolve_rejects_a_value_outside_the_aliases(key, value):
    resolved, errors = peer_models.resolve({key: value}, "claude")
    assert resolved == {}
    assert len(errors) == 1
    assert repr(key) in errors[0] and repr(value) in errors[0] and "haiku" in errors[0]


def test_the_aliases_are_the_host_agent_tool_aliases():
    assert peer_models.ALIASES == ("haiku", "sonnet", "opus", "fable")


@pytest.mark.parametrize("alias", ["haiku", "sonnet", "opus", "fable"])
def test_resolve_accepts_each_alias(alias):
    assert peer_models.resolve({"plan-review": alias}, "claude") == ({"plan-review": alias}, [])


def test_resolve_default_fills_the_seven_slugs_only():
    resolved, errors = peer_models.resolve({"default": "haiku"}, "claude")
    assert errors == []
    assert resolved == dict.fromkeys(SLUGS, "haiku")
    assert not {"plan-update", "implement", "draft-pr", "default"} & set(resolved)


def test_resolve_an_own_entry_wins():
    resolved, _ = peer_models.resolve({"default": "haiku", "pr-review-local": "opus"}, "claude")
    assert resolved["pr-review-local"] == "opus"
    assert resolved["plan-review"] == "haiku"


def test_resolve_host_rule():
    resolved, errors = peer_models.resolve({"plan-review": "opus"}, "other")
    assert resolved == {}
    assert errors == ["peer_models is set, but this host cannot pass a model to a peer. "
                      "Remove peer_models or run on Claude Code."]
    assert peer_models.resolve({}, "other") == ({}, [])


def test_resolve_reports_every_bad_key():
    _, errors = peer_models.resolve({"plan-update": "opus", "implement": "opus"}, "claude")
    assert len(errors) == 2


# --- activation_snapshot (in process) ------------------------------------------------------------


@pytest.mark.parametrize("value,resumed,expected", [
    pytest.param({"plan-update": "opus"}, {"peer_models": {"plan-review": "opus"}}, ({"plan-review": "opus"}, []),
                 id="snapshot_resumed_with_a_map_ignores_a_bad_config"),
    pytest.param({"default": "haiku"}, {"pipeline_run_id": "r"}, ({}, []),
                 id="snapshot_resumed_without_peer_models_gets_none"),
])
def test_snapshot_of_a_resumed_run_keeps_its_own_record(value, resumed, expected):
    assert peer_models.activation_snapshot(resumed, value, "claude") == expected


def test_snapshot_fresh_with_a_bad_config_returns_the_errors():
    resolved, errors = peer_models.activation_snapshot(None, {"plan-update": "opus"}, "claude")
    assert resolved == {} and len(errors) == 1 and "plan-update" in errors[0]


def test_snapshot_fresh_with_no_peer_models_is_empty():
    """An unset or empty `peer_models` is no snapshot. A broken layer fails at load (see above)."""
    assert peer_models.activation_snapshot(None, None, "claude") == ({}, [])
    assert peer_models.activation_snapshot(None, {}, "claude") == ({}, [])
    assert peer_models.activation_snapshot(None, {}, "other") == ({}, [])


def test_snapshot_fresh_with_a_value_that_is_not_a_map_returns_the_error():
    assert peer_models.activation_snapshot(None, [], "claude") == ({}, [MAP_ERROR])


def test_resumed_never_reads_config(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("a resumed run must not open the config")

    with monkeypatch.context() as patched:
        patched.setattr(Path, "read_text", boom)
        patched.setattr("builtins.open", boom)
        result = peer_models.activation_snapshot({"peer_models": {"plan-review": "opus"}}, {"plan-review": "haiku"},
                                                 "claude")
    assert result == ({"plan-review": "opus"}, [])


# --- activate_session_lean_marker: A1 marks a fresh Claude Code run: only the hook writes its lean delegation records.
#
# A fresh run on Claude Code gets `lean_record_by_hook: true`. `--resume` copies the marker
# only when the resumed record has it, so a run started before the lean check keeps its `begin` records.

def test_fresh_claude_run_is_marked(feature_branch_repo, make_root):
    proc = _activate(feature_branch_repo, make_root(), "--fresh")
    assert proc.returncode == 0, proc.stderr
    session = session_store.load_worktree_session(feature_branch_repo)
    assert session["lean_record_by_hook"] is True
    assert session["host"] == "claude"


def test_fresh_run_on_another_host_is_not_marked(feature_branch_repo, make_root):
    proc = _activate(feature_branch_repo, make_root(), "--fresh", claude=False)
    assert proc.returncode == 0, proc.stderr
    assert "lean_record_by_hook" not in session_store.load_worktree_session(feature_branch_repo)


def test_resume_keeps_the_marker(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    run = session_store.load_worktree_session(feature_branch_repo)["pipeline_run_id"]
    proc = _activate(feature_branch_repo, root, "--resume")
    assert proc.returncode == 0, proc.stderr
    assert session_store.load_worktree_session(feature_branch_repo)["pipeline_run_id"] == run
    assert session_store.load_worktree_session(feature_branch_repo)["lean_record_by_hook"] is True


def test_resume_of_a_pre_change_run_stays_unmarked(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    old = session_store.load_worktree_session(feature_branch_repo)
    old.pop("lean_record_by_hook")
    session_store.save_session(old, feature_branch_repo)
    proc = _activate(feature_branch_repo, root, "--resume")
    assert proc.returncode == 0, proc.stderr
    assert session_store.load_worktree_session(feature_branch_repo)["pipeline_run_id"] == old["pipeline_run_id"]
    assert "lean_record_by_hook" not in session_store.load_worktree_session(feature_branch_repo)


def test_resume_on_another_host_keeps_the_marker_and_sets_the_host(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh").returncode == 0
    proc = _activate(feature_branch_repo, root, "--resume", claude=False)
    assert proc.returncode == 0, proc.stderr
    session = session_store.load_worktree_session(feature_branch_repo)
    assert session["lean_record_by_hook"] is True
    assert session["host"] == "other"


def test_already_active_does_not_add_the_marker(feature_branch_repo, make_root):
    root = make_root()
    assert _activate(feature_branch_repo, root, "--fresh", claude=False).returncode == 0
    proc = _activate(feature_branch_repo, root)
    assert proc.returncode == 0, proc.stderr
    assert "already active" in proc.stderr
    assert "lean_record_by_hook" not in session_store.load_worktree_session(feature_branch_repo)


def test_resume_with_no_run_is_a_fresh_marked_run(feature_branch_repo, make_root):
    proc = _activate(feature_branch_repo, make_root(), "--resume")
    assert proc.returncode == 0, proc.stderr
    assert session_store.load_worktree_session(feature_branch_repo)["lean_record_by_hook"] is True


# --- activate_session_verdict_join: A1 turns on the verdict stage check only when the host loads the join hook.
#
# A fresh Claude Code run gets `verdict_join: true` only when a PostToolUse `Agent` group runs
# guard_subagent_handoff.py in one of four places: the user settings file, the project
# settings, the project local settings, or the plugin's own hooks/hooks.json. Without it the
# run keeps the old check and A1 warns with both actions. `--resume` never turns it on.

JOIN = {"matcher": "Agent|Task", "hooks": [{"type": "command",
        "command": 'python3 "/x/hooks/guard_subagent_handoff.py" --host claude'}]}
OTHER = {"matcher": "Agent|Task", "hooks": [{"type": "command", "command": "echo mine"}]}
WARNING = "PostToolUse Agent hook is not registered"


def _settings(path: Path, *groups: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {"PostToolUse": list(groups)}}), encoding="utf-8")
    return path


@pytest.fixture(name="toy")
def activation_toy(tmp_path):
    return activation_repo(tmp_path / "repo")


@pytest.fixture(name="home")
def sandbox_home(tmp_path):
    path = tmp_path / "home"
    (path / ".claude").mkdir(parents=True)
    return path


def _activate_in(repo, root, home, *flags, claude=True):
    env: _UserEnv = {"HOME": str(home), "CADENCE_CLAUDE_SETTINGS_PATH": str(home / ".claude" / "settings.json")}
    return _activate(repo, root, *flags, claude=claude, **env)


def test_user_settings_with_the_join_hook_mark_the_run(toy, make_root, home):
    _settings(home / ".claude" / "settings.json", JOIN)
    proc = _activate_in(toy, make_root(), home, "--fresh")
    assert proc.returncode == 0, proc.stderr
    assert worktree_session(toy)["verdict_join"] is True
    assert WARNING not in proc.stderr


def test_no_source_with_the_join_hook_keeps_the_old_check_and_warns(toy, make_root, home):
    proc = _activate_in(toy, make_root(), home, "--fresh")
    assert proc.returncode == 0, proc.stderr
    assert "verdict_join" not in worktree_session(toy)
    assert WARNING in proc.stderr
    assert "install.sh install: run install.sh" in proc.stderr
    assert "do not run install.sh" in proc.stderr


def test_a_post_tool_use_group_for_another_hook_does_not_count(toy, make_root, home):
    _settings(home / ".claude" / "settings.json", OTHER)
    assert _activate_in(toy, make_root(), home, "--fresh").returncode == 0
    assert "verdict_join" not in worktree_session(toy)


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
def test_project_settings_with_the_join_hook_mark_the_run(toy, make_root, home, name):
    _settings(toy / ".claude" / name, JOIN)
    assert _activate_in(toy, make_root(), home, "--fresh").returncode == 0
    assert worktree_session(toy)["verdict_join"] is True


def test_a_plugin_tree_counts_only_under_the_plugins_dir(toy, make_root, home, tmp_path):
    import shutil

    root = make_root()
    plugin = home / ".claude" / "plugins" / "cache" / "cadence"
    shutil.copytree(root, plugin)
    _settings(plugin / "hooks" / "hooks.json", JOIN)
    assert _activate_in(toy, plugin, home, "--fresh").returncode == 0
    assert worktree_session(toy)["verdict_join"] is True

    outside = tmp_path / "outside"
    shutil.copytree(plugin, outside)
    other = activation_repo(tmp_path / "repo2")
    assert _activate_in(other, outside, home, "--fresh").returncode == 0
    assert "verdict_join" not in worktree_session(other)


@pytest.mark.parametrize("text", ["{not json", '{"hooks": {"PostToolUse": 5}}', '{"hooks": {"PostToolUse": [{"matcher": "Agent", "hooks": 3}]}}'])
def test_unreadable_user_settings_keep_the_old_check(toy, make_root, home, text):
    (home / ".claude" / "settings.json").write_text(text, encoding="utf-8")
    proc = _activate_in(toy, make_root(), home, "--fresh")
    assert proc.returncode == 0, proc.stderr
    assert "verdict_join" not in worktree_session(toy)
    assert WARNING in proc.stderr


def test_a_run_on_another_host_is_not_marked(toy, make_root, home):
    _settings(home / ".claude" / "settings.json", JOIN)
    proc = _activate_in(toy, make_root(), home, "--fresh", claude=False)
    assert proc.returncode == 0, proc.stderr
    assert "verdict_join" not in worktree_session(toy)
    assert WARNING not in proc.stderr


def test_resume_keeps_the_mark_and_never_adds_it(toy, make_root, home):
    root = make_root()
    _settings(home / ".claude" / "settings.json", JOIN)
    assert _activate_in(toy, root, home, "--fresh").returncode == 0
    assert _activate_in(toy, root, home, "--resume").returncode == 0
    assert worktree_session(toy)["verdict_join"] is True

    old = worktree_session(toy)
    old.pop("verdict_join")
    session_store.save_session(old, toy)
    assert _activate_in(toy, root, home, "--resume").returncode == 0
    assert worktree_session(toy)["pipeline_run_id"] == old["pipeline_run_id"]
    assert "verdict_join" not in worktree_session(toy)


# --- owner_capture_from_shell_hook: Cursor has no chat id in the shell env. The shell hook passes it to activation.
#
# `guard_skill_gate.py` runs on Cursor `beforeShellExecution`, whose payload carries
# `conversation_id`. When the command runs `activate_session.py`, the hook writes a
# short-lived pending-owner note. Activation takes it when the host env has no id.

ACTIVATE = f"python3 {ROOT}/scripts/activate_session.py --task-id gh-5 --fresh"


def _shell_hook(repo: Path, command: str, conversation_id: str = "conv-C") -> subprocess.CompletedProcess[str]:
    payload = {
        "hook_event_name": "beforeShellExecution",
        "command": command,
        "cwd": str(repo),
        "conversation_id": conversation_id,
    }
    return skill_gate_hook(repo, payload, "cursor")


def test_activation_takes_the_owner_from_the_shell_hook(tmp_path):
    repo = git_repo(tmp_path / "r")
    proc = _shell_hook(repo, ACTIVATE)
    assert proc.returncode == 0 and "deny" not in proc.stdout
    activate(repo, "--task-id", "gh-5", "--fresh")
    assert session_store.load_worktree_session(repo)["owner_session_id"] == "conv-C"


def test_a_stale_note_is_ignored(tmp_path):
    repo = git_repo(tmp_path / "r")
    _shell_hook(repo, ACTIVATE)
    note = repo / ".context" / "sessions" / "pending-owner.json"
    data = json.loads(note.read_text(encoding="utf-8"))
    data["at"] = time.time() - 3600
    note.write_text(json.dumps(data), encoding="utf-8")
    activate(repo, "--task-id", "gh-5", "--fresh")
    assert session_store.load_worktree_session(repo)["owner_session_id"] == ""


def test_other_commands_leave_no_note(tmp_path):
    repo = git_repo(tmp_path / "r")
    _shell_hook(repo, "ls -la")
    assert not (repo / ".context").exists()


def test_the_note_is_used_once(tmp_path):
    repo = git_repo(tmp_path / "r")
    _shell_hook(repo, ACTIVATE)
    activate(repo, "--task-id", "gh-5", "--fresh")
    assert not (repo / ".context" / "sessions" / "pending-owner.json").exists()


def test_both_hooks_read_the_chat_id_in_one_order():
    sys.path.insert(0, str(ROOT / "hooks"))
    from hook_protocol import chat_id

    assert chat_id({"conversation_id": "cursor-chat", "session_id": "other"}) == "cursor-chat"
    assert chat_id({"session_id": "claude-chat"}) == "claude-chat"
    assert chat_id({}) == ""
