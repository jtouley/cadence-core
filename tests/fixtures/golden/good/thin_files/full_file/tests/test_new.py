"""Clean sample: a new module gets its own test file with three tests."""

import other


def test_a():
    assert other.twice(0) == 0


def test_b():
    assert other.twice(1) == 2


def test_c():
    assert other.twice(2) == 4
