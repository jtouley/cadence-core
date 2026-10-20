"""Clean sample: box_count returns the int that it declares."""


def box_count(items: list[str], per_box: int) -> int:
    return -(-len(items) // per_box)
