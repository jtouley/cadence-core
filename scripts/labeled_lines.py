"""One line grammar for `Label: value` lines in Cadence artifacts (cd-47 D8, D3).

Peers write run ids and reviewed commits in many Markdown forms:
`Pipeline-Run: id`, `- Pipeline-Run: id`, `**Pipeline run:** \\`id\\``, `> Last reviewed
commit: sha`. Each reader used its own regex and missed some forms (#41, MF-2).
This module is the one parser for all of them.
"""

from __future__ import annotations

import re

# Blockquote markers, heading markers, list markers and spaces at the start of a line.
_PREFIX = re.compile(r"^(?:\s|>|#+|[-*+](?=\s)|\d+\.(?=\s))*")


def labeled_values(text: str, labels: tuple[str, ...], value: str) -> list[str]:
    """Values of lines that hold one of `labels` and a `value` match, in file order, lower-cased.

    A line matches when, after its prefix, it is: optional `**`, a label, optional `**`,
    optional `:`, optional `**`, spaces, optional backtick, the value, optional backtick,
    optional spaces, end of line. The end anchor keeps prose out.
    """
    label_alt = "|".join(re.escape(label) for label in labels)
    line_re = re.compile(
        rf"^\**(?:{label_alt})\**:?\**\s+`?({value})`?\s*$",
        re.IGNORECASE,
    )
    out: list[str] = []
    for raw in text.splitlines():
        line = _PREFIX.sub("", raw, count=1)
        match = line_re.match(line)
        if match:
            out.append(match.group(1).lower())
    return out
