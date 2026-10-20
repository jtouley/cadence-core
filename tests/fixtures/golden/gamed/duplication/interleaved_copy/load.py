"""Gamed sample: copy.py copies this function. See copy.py for the limit."""


def load(path):
    text = path.read_text()
    rows = text.splitlines()
    kept = [r for r in rows if r]
    total = len(kept)
    print(total, "rows")
    return kept
