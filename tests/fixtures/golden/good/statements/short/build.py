"""Clean sample: a short function that reads a table of field names."""

FIELDS = ("name", "size", "kind")


def build(row):
    return {field: row.get(field) for field in FIELDS}
