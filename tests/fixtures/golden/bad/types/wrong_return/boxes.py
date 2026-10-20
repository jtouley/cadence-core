"""Planted fault: box_count returns a str, but it declares int (mypy return-value)."""


def box_count(items: list[str], per_box: int) -> int:
    return str(-(-len(items) // per_box))
