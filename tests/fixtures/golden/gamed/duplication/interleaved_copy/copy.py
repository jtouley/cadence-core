"""Gamed sample. Limit not caught: a block is six equal units in a row, so a copy with one
changed line in the middle (here a no-op `log` call) has no block and passes."""


def read_all(where):
    body = where.read_text()
    lines = body.splitlines()
    log("read")
    good = [x for x in lines if x]
    n = len(good)
    print(n, "lines in all")
    return good
