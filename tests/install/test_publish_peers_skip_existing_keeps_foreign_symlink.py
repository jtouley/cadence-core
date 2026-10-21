"""--skip-existing must not replace a host's own symlinked skill of the same name."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from publish_peers import peer_kind, publish_peers  # noqa: E402


def test_publish_peers_skip_existing_keeps_foreign_symlink(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    mine = tmp_path / "mine" / "vibe-test"
    mine.mkdir(parents=True)
    (skills / "vibe-test").symlink_to(mine)

    result = publish_peers(ROOT, skills_dir=skills, commands_dir=tmp_path / "cmds", skip_existing=True)

    assert (skills / "vibe-test").readlink() == mine
    assert f"skip (foreign symlink): {skills / 'vibe-test'} -> {mine}" in result["messages"]


def test_publish_peers_reports_a_link_to_the_bundled_peer_as_linked(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "vibe-test").symlink_to(ROOT / "skills" / "vibe-test")

    result = publish_peers(ROOT, skills_dir=skills, commands_dir=tmp_path / "cmds", skip_existing=True)

    assert f"ok (already linked): {skills / 'vibe-test'}" in result["messages"]


@pytest.mark.parametrize(
    ("row", "kind"),
    [
        ({"kind": "skill", "path": "commands/x.md"}, "skill"),
        ({"kind": "command", "path": "skills/x/SKILL.md"}, "command"),
        ({"path": "skills/x/SKILL.md"}, "skill"),
        ({"path": "commands/x.md"}, "command"),
    ],
)
def test_peer_kind_uses_kind_then_the_path(row, kind):
    assert peer_kind(row) == kind


def test_publish_peers_skip_existing_survives_a_resolve_error(tmp_path, resolve_loop):
    # Python 3.9 to 3.12 raise RuntimeError on a symlink loop.
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "vibe-test").symlink_to(tmp_path / "loop")
    resolve_loop(skills)
    result = publish_peers(ROOT, skills_dir=skills, commands_dir=tmp_path / "cmds", skip_existing=True)

    assert f"skip (foreign symlink): {skills / 'vibe-test'} -> {tmp_path / 'loop'}" in result["messages"]
