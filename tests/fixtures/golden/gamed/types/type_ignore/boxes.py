"""Gamed sample. Limit not caught: a `# type: ignore` hides the error on its line, and mypy does not
count an error that a comment hides. box_count still returns a str."""


def box_count(items: list[str], per_box: int) -> int:
    return str(-(-len(items) // per_box))  # type: ignore[return-value]
