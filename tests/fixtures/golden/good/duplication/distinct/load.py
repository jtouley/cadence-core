"""Clean sample: two functions with different shapes."""


def load(path):
    text = path.read_text()
    rows = text.splitlines()
    kept = [r for r in rows if r]
    total = len(kept)
    print(total, "rows")
    return kept
