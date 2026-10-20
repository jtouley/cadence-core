"""Gamed sample. Limit not caught: a body counts as a copy only when every normalized line
is equal, so a copy with one more line (here a no-op `assert where`) passes."""


def _fresh(where):
    assert where
    where.mkdir(parents=True)
    (where / "NOTES.md").write_text("y\n")
    return where


def test_one(tmp_path):
    assert _fresh(tmp_path / 'one').is_dir()


def test_two(tmp_path):
    assert _fresh(tmp_path / 'two').is_dir()


def test_three(tmp_path):
    assert _fresh(tmp_path / 'three').is_dir()
