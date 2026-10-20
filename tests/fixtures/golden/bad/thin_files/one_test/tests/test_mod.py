"""The module's own test file."""

import mod


def test_size():
    assert mod.size("ab") == 2


def test_one():
    assert mod.size("a") == 1


def test_three():
    assert mod.size("abc") == 3
