"""impl_artifact.run session and check contracts."""

from __future__ import annotations

import json

from conftest import make_session


def test_impl_artifact_missing_session_fails(scripts_path, tmp_path):
    from impl_artifact import run

    context = tmp_path / ".context"
    context.mkdir()
    code = run(context)
    assert code == 1
    assert not (context / "implementation").exists()
    assert not (context / "cadence-session.json").exists()


def test_impl_artifact_missing_task_id_fails(scripts_path, tmp_path):
    from impl_artifact import run

    context = tmp_path / ".context"
    make_session(context, task_id="")
    code = run(context)
    assert code == 1
    session = json.loads((context / "cadence-session.json").read_text(encoding="utf-8"))
    assert session["task_id"] == ""
    assert not list((context / "implementation").glob("*.json")) if (context / "implementation").exists() else True


def test_impl_artifact_check_missing_artifact_fails(scripts_path, tmp_path):
    from impl_artifact import run

    context = tmp_path / ".context"
    make_session(context)
    code = run(context, check=True)
    assert code == 1
    assert not (context / "implementation" / "gh-1.json").exists()
    assert (context / "cadence-session.json").is_file()


def test_impl_artifact_valid_session_writes_artifact(scripts_path, tmp_path, monkeypatch):
    import impl_artifact

    monkeypatch.setattr(impl_artifact, "_git_diff_stat", lambda *a, **k: "src/x.py | 2 ++")
    context = tmp_path / ".context"
    make_session(context)
    code = impl_artifact.run(context)
    artifact = context / "implementation" / "gh-1.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["RUN_BY_SCRIPT"] == "impl_artifact.py"
    assert payload["git_diff_stat"] == "src/x.py | 2 ++"
    assert payload["task_id"] == "gh-1"


def test_impl_artifact_empty_diff_fails_and_writes_nothing(scripts_path, tmp_path, monkeypatch):
    """An empty worktree is not an implementation snapshot."""
    import impl_artifact

    monkeypatch.setattr(impl_artifact, "_git_diff_stat", lambda *args: "")
    context = tmp_path / ".context"
    make_session(context)
    code = impl_artifact.run(context)
    assert code == 1
    assert not (context / "implementation" / "gh-1.json").exists()


def test_impl_artifact_check_rejects_empty_diff(scripts_path, tmp_path):
    import json as json_mod

    from impl_artifact import run

    context = tmp_path / ".context"
    make_session(context)
    artifact = context / "implementation" / "gh-1.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json_mod.dumps({"git_diff_stat": ""}) + "\n", encoding="utf-8")
    assert run(context, check=True) == 1


def test_an_unknown_base_names_the_failed_merge_base(scripts_path, tmp_path, monkeypatch):
    """A base ref that does not resolve gave "no change since the merge base", which hid the cause."""
    import subprocess

    import impl_artifact

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feature/x"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "a"], cwd=repo, check=True)
    context = repo / ".context"
    make_session(context, workspace=str(repo), base="no-such-ref")
    monkeypatch.setattr(impl_artifact, "run_worktree", lambda _ctx: repo)
    monkeypatch.setattr(impl_artifact, "resolve_base", lambda _wt, _s: "no-such-ref")
    errors = []
    monkeypatch.setattr(impl_artifact, "fail", lambda slug, errs, code=1: errors.extend(errs) or code)
    assert impl_artifact.run(context) == 1
    assert errors == [f"impl-artifact: git merge-base no-such-ref HEAD failed in {repo}"]
