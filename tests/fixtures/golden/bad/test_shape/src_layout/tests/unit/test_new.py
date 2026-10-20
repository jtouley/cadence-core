"""Planted fault in a src layout: tests/unit/conftest.py and src/shapes/ come from the layout, not from fixed paths.

The file copies the conftest helper under a new name, and it is a thin file: one test of
src/shapes/size.py, which tests/unit/test_size.py already tests.
"""

from shapes import size


def _repo(where):
    where.mkdir(parents=True)
    (where / "README.md").write_text("x\n")
    return where


def test_empty():
    assert size.size("") == 0
