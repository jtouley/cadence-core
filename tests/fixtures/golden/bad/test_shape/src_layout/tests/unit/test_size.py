"""The module's own test file."""

from shapes import size


def test_size():
    assert size.size("ab") == 2


def test_one():
    assert size.size("a") == 1


def test_three():
    assert size.size("abc") == 3
