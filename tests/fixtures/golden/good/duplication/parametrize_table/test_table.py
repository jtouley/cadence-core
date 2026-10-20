"""Clean sample: a 10-row parametrize table is data, not duplication."""

import pytest


@pytest.mark.parametrize(("text", "want"), [
    ("row 0", 0),
    ("row 1", 1),
    ("row 2", 2),
    ("row 3", 3),
    ("row 4", 4),
    ("row 5", 5),
    ("row 6", 6),
    ("row 7", 7),
    ("row 8", 8),
    ("row 9", 9),
])
def test_rows(text, want):
    assert int(text.split()[1]) == want
