"""The repo map: one file that each peer reads first, in place of 20 to 40 tool calls.

A peer starts with no context. The map gives the file tree, the build targets and the top-level
Python names of the tree at HEAD. The name holds the git tree sha, so a peer after E1 gets a new map.
"""

from __future__ import annotations

from pathlib import Path

from conftest import code_paths, git, git_repo, make_session

code_paths()
import handoff_inputs  # noqa: E402
import repo_map  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _repo(tmp_path: Path) -> Path:
    repo = git_repo(tmp_path / "repo")
    (repo / "src" / "marts").mkdir(parents=True)
    (repo / "src" / "marts" / "transform.py").write_text(
        '"""Transform."""\n\nclass Mart:\n    pass\n\n\ndef daily_revenue(rows, *, day=None):\n    return rows\n\n\n'
        "def _private():\n    pass\n", encoding="utf-8")
    (repo / "Makefile").write_text("test:\n\tpytest\n\nlint: test\n\truff .\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "marts"\n\n[project.scripts]\nmarts-build = "marts.build:main"\n',
                                         encoding="utf-8")
    (repo / "broken.py").write_text("def (:\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return repo


def test_the_map_names_files_targets_and_public_python_names(tmp_path):
    text = repo_map.build(_repo(tmp_path))
    assert "src/marts/transform.py" in text and "Makefile" in text
    assert "make test" in text and "make lint" in text and "marts-build" in text
    assert "class Mart" in text and "def daily_revenue(rows, *, day=None)" in text
    assert "_private" not in text
    assert "broken.py: not parsed" in text


def test_the_map_is_named_by_the_tree_and_written_once(tmp_path):
    repo = _repo(tmp_path)
    ctx = repo / ".context"
    first = repo_map.ensure(ctx, repo)
    assert first.parent == ctx / "repo-map" and first.name == f"{repo_map.tree_sha(repo)[:12]}.md"
    stamp = first.stat().st_mtime_ns
    assert repo_map.ensure(ctx, repo) == first and first.stat().st_mtime_ns == stamp
    (repo / "new.py").write_text("def added():\n    pass\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "more")
    assert repo_map.ensure(ctx, repo) != first


def test_a_big_tree_is_cut_and_says_so(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(repo_map, "MAX_BYTES", 200)
    text = repo_map.build(repo)
    assert len(text.encode()) < 600 and "cut at" in text


def test_the_peer_packets_pin_the_map(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    make_session(repo / ".context", task_id="gh-1", pipeline_run_id="run-1", workspace=str(repo))
    monkeypatch.chdir(repo)
    run = handoff_inputs.Run(root=ROOT, ctx=repo / ".context", workspace=repo,
                             session={"task_id": "gh-1", "pipeline_run_id": "run-1", "workspace": str(repo)})
    packet, missing = handoff_inputs.packet_for("A5", run)
    assert missing == [] and packet is not None
    assert any(p["path"].startswith(".context/repo-map/") for p in packet["inputs"])
