"""Every stages.json slash command is a bundled Cadence peer (not a host global)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGES = ROOT / "stages.json"
PEERS = ROOT / "references" / "bundled-peers.json"
sys.path.insert(0, str(ROOT / "scripts"))

HOST_LEAK = re.compile(
    r"(?i)~/?\.cursor/(?:commands|rules|skills)"
)


def _load_peers() -> dict:
    return json.loads(PEERS.read_text(encoding="utf-8"))


def _slash_commands(stages: dict) -> list[str]:
    found: list[str] = []
    for row in stages["stages"]:
        cmd = (row.get("command") or "").strip()
        if cmd.startswith("/"):
            name = cmd.split()[0]
            found.append(name)
    return found


def _all_peer_rows(data: dict) -> list[dict]:
    return list(data["peers"]) + list(data.get("supporting_commands") or [])


def test_bundled_peers_registry_lists_every_slash_stage():
    stages = json.loads(STAGES.read_text(encoding="utf-8"))
    peers = _load_peers()
    slashes = set(_slash_commands(stages))
    listed = {p["slash"] for p in peers["peers"]}
    missing = sorted(slashes - listed)
    extra = sorted(listed - slashes)
    assert not missing, f"stages.json slash commands missing from bundled-peers.json: {missing}"
    assert not extra, f"bundled-peers.json lists slashes not in stages.json: {extra}"


def test_every_peer_row_names_its_handoff_inputs():
    """The hook builds each peer packet from the `inputs` of the row."""
    from handoff_inputs import KINDS
    from prompt_shape import EXTRA_PEER_ROWS

    stages = json.loads(STAGES.read_text(encoding="utf-8"))
    rows = [r for r in stages["stages"] if str(r.get("command", "")).startswith("/")] + list(EXTRA_PEER_ROWS)
    bad = [r["block"] for r in rows if not isinstance(r.get("inputs"), list) or not set(r["inputs"]) <= set(KINDS)]
    assert not bad, f"peer rows with no inputs list or an unknown input kind: {bad}"

def test_bundled_peers_paths_are_under_cadence_home():
    peers = _load_peers()
    for peer in _all_peer_rows(peers):
        path = peer["path"]
        assert not path.startswith("~"), f"{peer['slash']} path leaks home: {path}"
        assert not path.startswith("/"), f"{peer['slash']} path is absolute: {path}"
        assert re.match(r"^(skills|commands|peers)/", path), (
            f"{peer['slash']} must live under skills/, commands/, or peers/: {path}"
        )


def test_bundled_peer_files_exist():
    peers = _load_peers()
    missing = [
        f"{row['slash']} -> {row['path']}"
        for row in _all_peer_rows(peers)
        if not (ROOT / row["path"]).is_file()
    ]
    assert not missing, "bundled peer files missing:\n" + "\n".join(missing)


def test_bundled_peer_files_have_no_host_leaks():
    peers = _load_peers()
    leaks: list[str] = []
    for row in _all_peer_rows(peers):
        path = ROOT / row["path"]
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for m in HOST_LEAK.finditer(text):
            leaks.append(f"{row['path']}: {m.group(0)!r}")
    assert not leaks, "peer files leak host globals:\n" + "\n".join(leaks)


def test_publish_peers_links_into_isolated_home(tmp_path):
    from publish_peers import publish_peers

    skills = tmp_path / "skills"
    commands = tmp_path / "commands"
    result = publish_peers(ROOT, skills_dir=skills, commands_dir=commands)
    assert result["ok"] is True
    peers = _load_peers()
    for row in peers["peers"]:
        rel = Path(row["path"])
        name = row["slash"].lstrip("/")
        if row["kind"] == "skill":
            dest = skills / name
            assert dest.is_symlink(), f"expected skill symlink {dest}"
            assert dest.resolve() == (ROOT / rel.parent).resolve()
        else:
            dest = commands / f"{name}.md"
            assert dest.is_symlink(), f"expected command symlink {dest}"
            assert dest.resolve() == (ROOT / rel).resolve()


def test_publish_peers_refuses_non_symlink_collision(tmp_path):
    from publish_peers import PublishError, publish_peers

    skills = tmp_path / "skills"
    commands = tmp_path / "commands"
    skills.mkdir()
    blocker = skills / "vibe-test"
    blocker.mkdir()
    (blocker / "SKILL.md").write_text("host copy\n", encoding="utf-8")
    try:
        publish_peers(ROOT, skills_dir=skills, commands_dir=commands)
    except PublishError as exc:
        assert "vibe-test" in str(exc)
    else:
        raise AssertionError("expected PublishError on non-symlink collision")
