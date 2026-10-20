"""Planted fault: _fresh is make_repo of tests/conftest.py with new names."""


def _fresh(where):
    where.mkdir(parents=True)
    (where / "NOTES.md").write_text("y\n")
    return where


def test_one(tmp_path):
    assert _fresh(tmp_path / 'one').is_dir()


def test_two(tmp_path):
    assert _fresh(tmp_path / 'two').is_dir()


def test_three(tmp_path):
    assert _fresh(tmp_path / 'three').is_dir()
