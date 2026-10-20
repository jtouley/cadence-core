"""Planted fault: run has an if/elif chain of 7 links, over code_shape.max_branch_chain (6)."""


def run(kind, a, b):
    if kind == "add":
        return a + b
    elif kind == "sub":
        return a - b
    elif kind == "mul":
        return a * b
    elif kind == "div":
        return a / b
    elif kind == "mod":
        return a % b
    elif kind == "pow":
        return a**b
    elif kind == "max":
        return max(a, b)
    raise KeyError(kind)
