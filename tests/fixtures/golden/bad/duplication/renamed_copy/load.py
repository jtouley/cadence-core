"""Planted fault: read_all in copy.py is this function with new names and values."""


def load(path):
    text = path.read_text()
    rows = text.splitlines()
    kept = [r for r in rows if r]
    total = len(kept)
    print(total, "rows")
    return kept
