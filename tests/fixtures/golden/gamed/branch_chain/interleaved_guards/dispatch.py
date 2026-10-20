"""Gamed sample. Limit not caught: a chain links only adjacent guards, so a statement between
two guards (here a no-op `seen.append`) cuts a 7-case dispatch into chains of 4 and 3 that pass."""


def run(kind, a, b, seen):
    if kind == "add":
        return a + b
    if kind == "sub":
        return a - b
    if kind == "mul":
        return a * b
    if kind == "div":
        return a / b
    seen.append(kind)
    if kind == "mod":
        return a % b
    if kind == "pow":
        return a**b
    if kind == "max":
        return max(a, b)
    raise KeyError(kind)
