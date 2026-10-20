"""Published text must stay $HOME-relative (no absolute user-home paths)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
FORBIDDEN = ("Desktop/Cursor",)
_ABSOLUTE_USER_HOME = re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+")
_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf"}


def _published_text(path: Path) -> str | None:
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / raw.decode() for raw in result.stdout.split(b"\0") if raw]


def test_readme_home_only_paths():
    text = README.read_text(encoding="utf-8")
    hits = [f for f in FORBIDDEN if f in text]
    assert not hits, f"README contains forbidden absolute project paths: {hits}"
    assert "$HOME" in text or "~/" in text or "~/.cursor" in text


def test_published_tree_absolute_user_home_absent():
    """Tracked text must not embed /Users/<name> or /home/<name>."""
    sample = "/Users/" + "example/repo"
    assert _ABSOLUTE_USER_HOME.search(sample)

    offenders: list[str] = []
    for path in _tracked_files():
        text = _published_text(path)
        if text is None:
            continue
        for match in _ABSOLUTE_USER_HOME.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            rel = path.relative_to(ROOT)
            offenders.append(f"{rel}:{line}:{match.group(0)}")
    assert offenders == []
