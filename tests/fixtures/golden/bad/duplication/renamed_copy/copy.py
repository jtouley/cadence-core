"""Planted fault: a copy of load in load.py. Only the names, the values and a comment are new."""


def read_all(where):
    body = where.read_text()  # read it
    lines = body.splitlines()
    good = [x for x in lines if x]
    n = len(good)
    print(n, "lines in all")
    return good
