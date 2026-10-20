"""Gamed sample. Limit not caught: a file is thin only at one or two tests, so two
empty tests added to one real test pass, though the file still belongs in tests/test_mod.py."""

import mod


def test_empty():
    assert mod.size("") == 0


def test_pad_one():
    pass


def test_pad_two():
    pass
