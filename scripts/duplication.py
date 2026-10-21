#!/usr/bin/env python3
"""Duplicated blocks of Python code, measured with tokenize. Standard library only.

A unit is one logical line with its tokens normalized: a name that is not a keyword
becomes N, and a number or a string (an f-string too) becomes L. Thus a copy with new
names and new values is still a copy. Comments, blank lines, import lines and data (a
`parametrize` table, a field declaration, or a name bound to a table or a literal) are not units.
A block is `min_block` or more consecutive units that occur again, in another file or
in the same file without overlap.

The report gives, per file, the code lines that a duplicated block covers, the code
lines in all, and each block with its two locations.

CLI: python3 scripts/duplication.py FILE... prints the report as JSON.
"""

from __future__ import annotations

import io
import json
import keyword
import sys
import tokenize
from dataclasses import asdict, dataclass, field
from pathlib import Path

MIN_BLOCK = 6
_SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}
_FSTRING = (getattr(tokenize, "FSTRING_START", -1), getattr(tokenize, "FSTRING_END", -1))


@dataclass(frozen=True)
class Unit:
    text: str
    first: int  # first and last source line of the logical line
    last: int


@dataclass
class FileReport:
    duplicated_lines: int
    total_lines: int
    blocks: list[dict] = field(default_factory=list)  # {"units": n, "a": "path:3-9", "b": "path:20-26"}

    @property
    def percent(self) -> float:
        return 100.0 * self.duplicated_lines / self.total_lines if self.total_lines else 0.0


def _word(tok: tokenize.TokenInfo) -> str:
    if tok.type == tokenize.NAME:
        return tok.string if keyword.iskeyword(tok.string) else "N"
    return "L" if tok.type in (tokenize.NUMBER, tokenize.STRING) else tok.string


def _logical_lines(source: str) -> list[list[tokenize.TokenInfo]]:
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type not in _SKIP]
    except (tokenize.TokenError, IndentationError) as exc:
        raise SyntaxError(str(exc)) from exc
    lines: list[list[tokenize.TokenInfo]] = [[]]
    for tok in tokens:
        if tok.type == tokenize.NEWLINE:
            lines.append([])
        else:
            lines[-1].append(tok)
    return [line for line in lines if line]


def _normalized(line: list[tokenize.TokenInfo]) -> list[str]:
    words: list[str] = []
    depth = 0
    for tok in line:
        inside = depth > 0
        depth += (tok.type == _FSTRING[0]) - (tok.type == _FSTRING[1])
        if not inside:
            words.append("L" if depth else _word(tok))  # an f-string is one L
    return words


_DATA_WORDS = {"True", "False", "None"}
_CLOSE = {"(": ")", "[": "]", "{": "}"}


def _one_display(value: list[str]) -> bool:
    """`[...]`, `(...)` or `{...}` with no code keyword in it, or one literal."""
    if len(value) == 1:
        return value[0] in _DATA_WORDS or value[0][:1].isdigit() or value[0][:1] in "\"'"
    if not value or value[0] not in _CLOSE or any(keyword.iskeyword(w) and w not in _DATA_WORDS for w in value):
        return False
    depth = 0
    for i, word in enumerate(value):
        depth += (word in _CLOSE) - (word in _CLOSE.values())
        if depth == 0:
            return i == len(value) - 1  # the first bracket closes at the end
    return False


def _is_data(line: list[tokenize.TokenInfo]) -> bool:
    """Data, not code: a parametrize decorator, a field declaration (`name: type`), or a name bound to a table or a literal."""
    words = [t.string for t in line]
    if words[0] == "@":
        return "(" in words and "parametrize" in words[:words.index("(")]
    field = len(words) > 1 and words[1] == ":" and line[0].type == tokenize.NAME and not keyword.iskeyword(words[0])
    if "=" not in words:
        return field
    return _one_display(words[words.index("=") + 1:])


def units(source: str) -> list[Unit]:
    """The normalized logical lines of `source`. Raises SyntaxError when it does not tokenize."""
    out = []
    for line in _logical_lines(source):
        words = _normalized(line)
        if words[0] not in ("import", "from") and not _is_data(line):
            out.append(Unit(" ".join(words), line[0].start[0], line[-1].end[0]))
    return out


def _rows(seq: list[Unit]) -> set[int]:
    return {row for u in seq for row in range(u.first, u.last + 1)}


def _pairs(seqs: dict[str, list[Unit]], size: int) -> set[tuple[tuple[str, int], tuple[str, int]]]:
    """Each window of `size` units paired with one other window of the same text that it does not overlap."""
    seen: dict[tuple[str, ...], list[tuple[str, int]]] = {}
    for path, seq in seqs.items():
        for i in range(len(seq) - size + 1):
            seen.setdefault(tuple(u.text for u in seq[i:i + size]), []).append((path, i))
    pairs = set()
    for places in seen.values():
        for b in places:
            a = next((p for p in places if p != b and (p[0] != b[0] or abs(p[1] - b[1]) >= size)), None)
            if a is not None:
                pairs.add((min(a, b), max(a, b)))
    return pairs


def _where(seqs: dict[str, list[Unit]], path: str, start: int, length: int) -> str:
    return f"{path}:{seqs[path][start].first}-{seqs[path][start + length - 1].last}"


def measure(sources: dict[str, str], min_block: int = MIN_BLOCK) -> dict[str, FileReport]:
    """The report per path. Raises SyntaxError for a source that does not tokenize."""
    seqs = {path: units(text) for path, text in sources.items()}
    pairs = _pairs(seqs, min_block)
    duplicated: dict[str, set[int]] = {path: set() for path in seqs}
    reports = {path: FileReport(0, len(_rows(seq))) for path, seq in seqs.items()}
    for (pa, i), (pb, j) in pairs:
        duplicated[pa] |= _rows(seqs[pa][i:i + min_block])
        duplicated[pb] |= _rows(seqs[pb][j:j + min_block])
        if ((pa, i - 1), (pb, j - 1)) in pairs:
            continue  # not the start of a block
        length = min_block
        while ((pa, i + length - min_block + 1), (pb, j + length - min_block + 1)) in pairs:
            length += 1
        block = {"units": length, "a": _where(seqs, pa, i, length), "b": _where(seqs, pb, j, length)}
        reports[pa].blocks.append(block)
        if pb != pa:
            reports[pb].blocks.append(block)
    for path, report in reports.items():
        report.duplicated_lines = len(duplicated[path])
        report.blocks.sort(key=lambda b: -b["units"])
    return reports


def main(argv: list[str] | None = None) -> int:
    paths = sys.argv[1:] if argv is None else argv
    reports = measure({p: Path(p).read_text(encoding="utf-8", errors="replace") for p in paths})
    print(json.dumps({p: {**asdict(r), "percent": round(r.percent, 1)} for p, r in reports.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
