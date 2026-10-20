"""Clean sample: the dispatch is a table, the extension point. A new kind is a new row."""

HANDLERS = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mul": lambda a, b: a * b,
}


def run(kind, a, b):
    handler = HANDLERS.get(kind)
    if handler is None:
        raise KeyError(kind)
    return handler(a, b)
