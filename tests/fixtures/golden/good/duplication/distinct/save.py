"""Clean sample: a different job, so a different shape."""


def save(path, rows):
    if not rows:
        raise ValueError("no rows")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as out:
        for row in rows:
            out.write(f"{row}\n")
    return len(rows)
