"""One line grammar for `Label: value` lines in Cadence artifacts.

Peers write run ids and reviewed commits in many Markdown forms:
`Pipeline-Run: id`, `- Pipeline-Run: id`, `**Pipeline run:** \\`id\\``, `> Last reviewed
commit: sha`. Each reader used its own regex and missed some forms.
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


_FENCE = re.compile(r"^\s*(```|~~~)")
_MARKUP = re.compile(r"[*_`]")
_VERDICT_LABEL = re.compile(r"^\W*verdict\W*?:\s*(.*)$", re.IGNORECASE)
_VERDICT_HEADING = re.compile(r"^\s*#{1,6}\s*\W*verdict\W*\s*$", re.IGNORECASE)


def verdict(text: str, options: tuple[str, ...]) -> str | None:
    """The last verdict of ``text``, one of ``options`` as written there, else None.

    A verdict is a `Verdict: X` line in any markup, or the first line under a `Verdict` heading. X starts with
    an option (any case, `_` for a space), and prose may follow it: `## Verdict` then `NO MERGE. One must-fix`.
    The longest option wins. Fenced code is skipped. The corpus of real peer reports (tests/fixtures/peer_reports)
    holds each form.
    """
    for value in reversed(verdict_values(text)):
        found = _option(value, options)
        if found:
            return found
    return None


def unfenced_lines(text: str) -> list[str]:
    """The lines of ``text`` outside fenced code blocks; a fence line itself is left out too."""
    out, fenced = [], False
    for raw in text.splitlines():
        if _FENCE.match(raw):
            fenced = not fenced
        elif not fenced:
            out.append(raw)
    return out


def verdict_values(text: str) -> list[str]:
    """The text of each `Verdict:` line and of the first line under each `Verdict` heading, in file order."""
    values: list[str] = []
    heading = False
    for raw in unfenced_lines(text):
        if _VERDICT_HEADING.match(raw):
            heading = True
            continue
        label = _VERDICT_LABEL.match(_PREFIX.sub("", raw, count=1))
        if label:
            values.append(label.group(1))
        elif heading and raw.strip() and not raw.lstrip().startswith("#"):
            values.append(raw)
        heading = heading and not raw.strip()
    return values


def _option(value: str, options: tuple[str, ...]) -> str | None:
    said = " ".join(_MARKUP.sub(" ", value).replace("_", " ").split()).upper()
    for option in sorted(options, key=len, reverse=True):
        if said.startswith(option.upper()) and not said[len(option):len(option) + 1].isalnum():
            return option
    return None
