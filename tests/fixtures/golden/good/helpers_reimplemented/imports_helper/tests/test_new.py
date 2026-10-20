"""Clean sample: the test file imports the conftest helper."""

from conftest import make_repo


def test_one(tmp_path):
    assert make_repo(tmp_path / 'one').is_dir()


def test_two(tmp_path):
    assert make_repo(tmp_path / 'two').is_dir()


def test_three(tmp_path):
    assert make_repo(tmp_path / 'three').is_dir()
