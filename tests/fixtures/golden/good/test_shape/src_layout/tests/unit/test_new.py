"""Clean sample in a src layout: a new module gets its own test file, and the helper comes from the conftest file."""

from conftest import make_repo
from shapes import twice


def test_a(tmp_path):
    assert make_repo(tmp_path / "r").is_dir() and twice.twice(0) == 0


def test_b():
    assert twice.twice(1) == 2


def test_c():
    assert twice.twice(2) == 4
