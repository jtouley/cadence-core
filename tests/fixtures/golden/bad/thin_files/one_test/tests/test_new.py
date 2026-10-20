"""Planted fault: one test of scripts/mod.py, which tests/test_mod.py already tests."""

import mod


def test_empty():
    assert mod.size("") == 0
