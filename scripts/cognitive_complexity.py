#!/usr/bin/env python3
"""Cognitive complexity per function, for every language with an analyzer.

The metric is SonarSource cognitive complexity:
  - +1 for each break in linear flow: if, else if, else, loops, catch/except,
    switch/match, ternary, and each sequence of like boolean operators;
  - +nesting for a structure inside another structure;
  - a nested function adds to the function that holds it.
Comprehensions add nothing (only the boolean operators inside them count), as
in the `cognitive_complexity` package that flake8-cognitive-complexity uses.
Not counted, as in that package: recursion, and the order of mixed boolean
operators that `ast` does not keep (`a or b and c or d` scores 2, not 3).

ANALYZERS maps a file extension to an analyzer. To add a language, add an
analyzer and one row. Python uses `ast` and follows the rules above. C-family
languages use a token scanner (comments and strings removed, braces and keywords
tracked); it is an approximation. When the scanner loses the structure (an
unbalanced brace at the end), it raises LostStructure: the caller reports the
file as unmeasured, never as clean. Standard library only.

CLI: python3 scripts/cognitive_complexity.py [--max N] [PATH ...]
prints the functions over N for files or dirs, worst first. The default N is
cognitive_complexity.max of the resolved config (scripts/cadence_config.py).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import cadence_config  # noqa: E402

# The measured sections. Each limit is a floor: a config layer can make it stricter,
# never looser (x-cadence-stricter in references/cadence.config.schema.json).
# `bugs` and `types` count tool findings on added lines (tool_measures.py, docs/tool-gates.md).
MEASURED = ("cognitive_complexity", "duplication", "code_shape", "test_shape", "bugs", "types")
SECTIONS: dict[str, dict] = {
    name: json.loads((Path(__file__).resolve().parent.parent / cadence_config.DEFAULTS).read_text(encoding="utf-8"))[name]
    for name in MEASURED
}
DEFAULTS = SECTIONS["cognitive_complexity"]
DEFAULT_MAX = DEFAULTS["max"]
NOT_LIMITS = ("unsupported", "layout")


def load_config(root: Path, section: str = "cognitive_complexity", workspace: Path | None = None) -> dict:
    """One measured section of the resolved config, with `threshold_source` for each limit.

    The source is the layer that set the limit, and names a looser value that a layer gave and the
    floor ignored. A bad value or an unknown key raises (fail closed).
    """
    resolved = cadence_config.load(root, workspace)
    cfg = resolved.section(section)
    cfg["threshold_source"] = {key: resolved.source(f"{section}.{key}") for key in cfg if key not in NOT_LIMITS}
    return cfg


def load_exclude(root: Path, workspace: Path | None = None) -> tuple[str, ...]:
    """`measure_exclude`: path prefixes that R2 and R3 do not measure, such as a corpus of planted faults."""
    return tuple(cadence_config.load(root, workspace).get("measure_exclude") or ())


class LostStructure(ValueError):
    """The scanner lost the brace structure of a file, so its scores cannot be trusted."""


@dataclass(frozen=True)
class FunctionScore:
    name: str
    line: int
    score: int
    statements: int | None = None  # code shape, Python only: statements in the body, nested ones too
    chain: int | None = None  # the longest if/elif or guard chain, or the most match cases


# --- Python (ast) -------------------------------------------------------------------------------

_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class _PythonScorer(ast.NodeVisitor):
    """Score one function body. Nested functions and lambdas add with nesting."""

    def __init__(self) -> None:
        self.score = 0
        self.nesting = 0

    def _structure(self, node: ast.AST, *children: Iterable[ast.AST]) -> None:
        self.score += 1 + self.nesting
        self._nested(*children)

    def _nested(self, *children: Iterable[ast.AST]) -> None:
        self.nesting += 1
        for group in children:
            for child in group:
                self.visit(child)
        self.nesting -= 1

    def _visit_all(self, nodes: Iterable[ast.AST]) -> None:
        for child in nodes:
            self.visit(child)

    def visit_If(self, node: ast.If, *, is_elif: bool = False) -> None:
        self.visit(node.test)
        if is_elif:
            self.score += 1  # hybrid: no nesting increment, but its body nests
            self._nested(node.body)
        else:
            self._structure(node, node.body)
        self._visit_else(node, node.orelse)

    def _visit_else(self, node: ast.AST, orelse: list[ast.stmt]) -> None:
        if not orelse:
            return
        elif_node = orelse[0] if len(orelse) == 1 and isinstance(orelse[0], ast.If) else None
        if elif_node is not None and elif_node.col_offset == getattr(node, "col_offset", -1):
            self.visit_If(elif_node, is_elif=True)
            return
        self.score += 1
        self._nested(orelse)

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        self.visit(node.iter if not isinstance(node, ast.While) else node.test)
        self._structure(node, node.body)
        self._visit_else(node, node.orelse)

    visit_For = visit_AsyncFor = visit_While = _visit_loop

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_all(node.body)
        for handler in node.handlers:
            self._structure(handler, handler.body)
        self._visit_all(node.orelse)  # try-else is not a flow break in the spec
        self._visit_all(node.finalbody)

    visit_TryStar = visit_Try

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self._structure(node, [node.body, node.orelse])

    def visit_Match(self, node: ast.AST) -> None:
        self.visit(node.subject)  # type: ignore[attr-defined]
        self._structure(node, [c for case in node.cases for c in case.body])  # type: ignore[attr-defined]

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.score += 1
        for value in node.values:
            # A nested BoolOp of the same operator is the same sequence: `(a and b) and c`.
            if isinstance(value, ast.BoolOp) and type(value.op) is type(node.op):
                self.score -= 1
            self.visit(value)

    def _visit_nested_function(self, node: ast.AST) -> None:
        body = node.body if isinstance(node.body, list) else [node.body]  # type: ignore[attr-defined]
        self._nested(body)

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _visit_nested_function


def _python_functions(tree: ast.AST, prefix: str = "") -> Iterable[tuple[str, ast.AST]]:
    """Top-level functions and methods (with their class), not nested functions."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            yield from _python_functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield f"{prefix}{node.name}", node
        elif isinstance(node, ast.stmt) or isinstance(node, ast.excepthandler):
            # `if TYPE_CHECKING:`, `try: ... except ImportError: def f()`, loops: any block can hold a def
            yield from _python_functions(node, prefix)


def _links(node: ast.If) -> list[ast.If]:
    """An if and its elif links (an else that holds only an if is a new chain, as in _visit_else)."""
    links = [node]
    while len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) and node.orelse[0].col_offset == node.col_offset:
        node = node.orelse[0]
        links.append(node)
    return links


def _block_chain(block: list[ast.stmt]) -> int:
    """The longest chain in one block. An if after an if whose last link ends in return or raise continues it."""
    best = run = 0
    exits = False
    for stmt in block:
        if not isinstance(stmt, ast.If):
            run, exits = 0, False
            continue
        links = _links(stmt)
        run = run + len(links) if exits else len(links)
        best = max(best, run)
        exits = not links[-1].orelse and isinstance(links[-1].body[-1], (ast.Return, ast.Raise))
    return best


def _chain(node: ast.AST) -> int:
    """The open/closed proxy: the longest dispatch chain in the function."""
    best = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Match):
            best = max(best, len(child.cases))
        for name in ("body", "orelse", "finalbody"):
            block = getattr(child, name, None)
            if isinstance(block, list):
                best = max(best, _block_chain(block))
    return best


def analyze_python(source: str) -> list[FunctionScore]:
    tree = ast.parse(source)
    out = []
    for name, node in _python_functions(tree):
        scorer = _PythonScorer()
        for stmt in node.body:  # type: ignore[attr-defined]
            scorer.visit(stmt)
        statements = sum(isinstance(n, ast.stmt) for n in ast.walk(node)) - 1  # not the def itself
        out.append(FunctionScore(name, node.lineno, scorer.score, statements, _chain(node)))  # type: ignore[attr-defined]
    return out


# --- C-family (token scanner) -------------------------------------------------------------------

@dataclass(frozen=True)
class CFamily:
    """What differs between C-family languages for the scanner."""

    function_keywords: frozenset[str]
    control: frozenset[str]
    ternary: bool
    rust: bool = False  # lifetimes, raw strings, `|x|` closures
    block_expressions: bool = False  # `f(if a { 1 } else { 2 })`, `Ok(match x { .. })`: a block can sit in parentheses
    arrows: bool = False
    closures_use_pipes: bool = False
    methods_without_keyword: bool = False
    receivers: bool = False  # Go `func (r *T) Name(`: the name follows the receiver
    headers_without_parens: bool = False  # Go `if v, ok := m[k]; ok {`: a `;` does not end a control header
    line_semicolons: bool = False  # Go: a line end can end a statement (the Go spec semicolon rule)
    colon_return_types: bool = True  # TypeScript `): { ... }` is a return type, not a body
    angle_type_literals: bool = True  # TypeScript `<{ ... }>` and `extends { ... }` are types
    type_body_keywords: frozenset[str] = frozenset()  # a `{` right after one of these opens a type body
    raw_strings: str = ""  # the key of the raw string rule in _RAW_ENDS
    arrow_token: str = "=>"  # Java lambdas use `->`
    switch_rules: bool = False  # Java `case 1 -> {`: a switch rule block, not a lambda
    not_method_words: frozenset[str] = frozenset()  # not a method when before `(` or before the name
    annotations: bool = False  # Java `@Name(...)` before a method header
    generic_wildcards: bool = False  # Java `<?>`, `<? extends T>`: a `?` after `<` or `,` is not a ternary
    preprocessor: bool = False  # C `#if`, `#define`: directive lines (and their `\` lines) add nothing
    initializers: bool = False  # C `... = { f, g };`: a brace after `=` is not a function
    bracket_lambdas: bool = False  # C++ `[&](int x) {`: a `]` before the `(` is a lambda


TYPESCRIPT = CFamily(
    function_keywords=frozenset({"function"}),
    control=frozenset({"if", "for", "while", "switch", "catch", "do"}),
    ternary=True,
    arrows=True,
    methods_without_keyword=True,
)
# Go, Java and C/C++ are approximations, as TypeScript is. Known gaps, not fixed:
# - Java: an anonymous class in a field initializer or in a call nests as a function (G4, R3.3);
#   a lambda without braces does not nest (CH5); enum constants with a body are reported as
#   functions (SF-3); a compact record constructor `P { ... }` is not scored (R5.4); a guarded
#   switch rule with a ternary (`when (a ? b : c) ->`) nests one level more (R4.1).
# - Go: `range` over a composite literal (`range []int{1, 2} {`) does not nest (SF-1); the name of a
#   closure that returns a func or a generic type can be wrong, never its score (R2 nit).
# - Go, C and Java: `goto` and labeled jumps (`continue label`) add 0, not +1, as in TypeScript and
#   Rust.
# - C/C++: `operator[]` is scored under the name `<lambda>`; `operator()` is scored under the name
#   `operator`; the other operator overloads are not scored (CH6, R7.1). A constructor with a member
#   brace initializer (`K() : a{1} { ... }`, `: m_{std::move(m)}`): the `{1}` opens and closes the
#   function `K`, so the constructor body is not scored, and a constructor of any complexity in this
#   form passes R3 (R5.2). A lambda with no parameter list (`[] { ... }`) is a plain block (R5.1). A
#   lambda with a trailing return type in a call (`[](int x) -> bool { ... }`) does not nest (R3.3;
#   scanner 1, hand 2).
#   A function-like macro loop in a body (`list_for_each(p, head) { ... }`) nests but adds no +1, and
#   prefixes such as `__attribute__((...))` or `TEST(a, b)` give the macro name (SF-6, R5.4). Two
#   headers for one function under `#ifdef` make the file unmeasured (SF-2). C++14 digit separators
#   (`1'000`) read as a char literal to the end of the line. Some C++ headers give a wrong name,
#   never a wrong score: `std::function<void(int)> make() {` is named `void`, and
#   `int (*getf(void))(int) {` is named `int`.
# - All: code outside a function is not scored (Java `static {}`, C K&R definitions; G5), and a
#   braceless nested control statement does not nest (VT#37). A text block or raw string with no
#   close makes the file unmeasured.
# Equivalent mutants kept for clarity (F27): `angle_type_literals=False` on GO, JAVA and C_CPP, and
# `constexpr` in the C_CPP `not_method_words` (`if constexpr (x) {` already scores right, CH3).
JAVA = CFamily(
    function_keywords=frozenset(),
    control=frozenset({"if", "for", "while", "switch", "catch", "do"}),
    ternary=True,
    arrows=True,
    methods_without_keyword=True,
    colon_return_types=False,
    angle_type_literals=False,
    raw_strings="java",
    arrow_token="->",
    switch_rules=True,
    not_method_words=frozenset({"try", "synchronized", "record", "case"}),
    annotations=True,
    generic_wildcards=True,
)
C_CPP = CFamily(
    function_keywords=frozenset(),
    control=frozenset({"if", "for", "while", "switch", "do", "catch"}),
    ternary=True,
    methods_without_keyword=True,
    colon_return_types=False,  # `case F(1): {` is a case block
    angle_type_literals=False,
    raw_strings="cpp",
    not_method_words=frozenset({"constexpr", "case"}),
    preprocessor=True,
    initializers=True,
    bracket_lambdas=True,
)
RUST = CFamily(
    function_keywords=frozenset({"fn"}),
    control=frozenset({"if", "for", "while", "loop", "match"}),
    ternary=False,
    rust=True,
    block_expressions=True,
    closures_use_pipes=True,
    colon_return_types=False,
    angle_type_literals=False,
    raw_strings="rust",
)
GO = CFamily(
    function_keywords=frozenset({"func"}),
    control=frozenset({"if", "for", "switch", "select"}),
    ternary=False,
    receivers=True,
    headers_without_parens=True,
    line_semicolons=True,
    colon_return_types=False,  # `case f(): {` is a case block
    angle_type_literals=False,
    type_body_keywords=frozenset({"interface", "struct"}),
    raw_strings="go",
)

_TOKEN = re.compile(r"\"\"|[A-Za-z_$][\w$]*|=>|&&|\|\||\?\?|\?\.|::|[{}()\[\];,?:=|&!<>.+\-*/%^~#@]|\d[\w.]*")
_TOKEN_THIN_ARROW = re.compile(r"->|" + _TOKEN.pattern)  # only for a language whose arrow is `->`
_NOT_METHOD = frozenset({"if", "for", "while", "switch", "catch", "return", "new", "typeof", "await", "function"})
# A `/` starts a regex after one of these tokens; after anything else it divides.
# `<` and `>` are not here: `</li>` is a JSX closing tag.
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%~^") | {"", "return", "typeof", "case", "do", "else", "in", "of", "yield",
                                              "await", "void", "delete", "throw", "new"}
_IDENT = re.compile(r"[A-Za-z_$]")
# With block expressions, a `{` in parentheses is inline only after one of these (a struct or object literal).
_INLINE_AFTER = ("(", ",", ":", "=", "?", "||", "&&")


def _blank(text: str) -> str:
    """The same text as spaces, with its newlines kept, so line numbers do not move."""
    return re.sub(r"[^\n]", " ", text)


def _quoted_end(source: str, i: int, quote: str, *, one_line: bool) -> int:
    """The index after a string, char or regex literal that starts at `i`.

    A one-line literal (a JS/TS quote string, a regex) also ends at a newline, so a
    stray quote (an apostrophe in JSX text) cannot swallow the rest of the file.
    """
    j = i + 1
    while j < len(source) and source[j] != quote and not (one_line and source[j] == "\n"):
        j += 2 if source[j] == "\\" else 1
    return min(j + 1, len(source))


_LIFETIME = re.compile(r"'[A-Za-z_]\w*")
_RAW_STRING = re.compile(r'(?<![\w])b?r(#*)"')


def _is_lifetime(source: str, i: int) -> bool:
    match = _LIFETIME.match(source, i)
    return bool(match) and source[match.end() : match.end() + 1] != "'"


def _raw_string_end(source: str, i: int) -> int | None:
    """Rust `r"..."` / `r#"..."#`: no escapes, ends at a quote with the same number of `#`."""
    match = _RAW_STRING.match(source, i)
    if match is None:
        return None
    close = '"' + match.group(1)
    j = source.find(close, match.end())
    return len(source) if j < 0 else j + len(close)


def _go_raw_end(source: str, i: int) -> int | None:
    """Go `` `...` ``: no escapes, ends at the next backtick."""
    if source[i] != "`":
        return None
    j = source.find("`", i + 1)
    return len(source) if j < 0 else j + 1


def _text_block_end(source: str, i: int) -> int | None:
    """Java `\"\"\"...\"\"\"`: a `\\` escapes one character; ends after the next three quotes."""
    if not source.startswith('"""', i):
        return None
    j = i + 3
    while j < len(source) and not source.startswith('"""', j):
        j += 2 if source[j] == "\\" else 1
    return min(j + 3, len(source))


_CPP_RAW = re.compile(r'(?<![\w])(?:u8|[uUL])?R"([^()\\\s]{0,16})\(')


def _cpp_raw_end(source: str, i: int) -> int | None:
    """C++ `R"d(...)d"`: no escapes, ends at `)` + the delimiter + a quote."""
    match = _CPP_RAW.match(source, i)
    if match is None:
        return None
    close = ")" + match.group(1) + '"'
    j = source.find(close, match.end())
    return len(source) if j < 0 else j + len(close)


_RAW_ENDS: dict[str, Callable[[str, int], int | None]] = {
    "rust": _raw_string_end, "go": _go_raw_end, "java": _text_block_end, "cpp": _cpp_raw_end,
}


def _raw_end(source: str, i: int, raw: str) -> int | None:
    """The end of a raw string of the language `raw` that starts at `i`, else None."""
    end_of = _RAW_ENDS.get(raw)
    return end_of(source, i) if end_of else None


def _comment_end(source: str, i: int) -> int | None:
    if source.startswith("//", i):
        j = source.find("\n", i)
        return len(source) if j < 0 else j
    if source.startswith("/*", i):
        j = source.find("*/", i + 2)
        return len(source) if j < 0 else j + 2
    return None


def _literal_end(source: str, i: int, rust: bool, before: str, raw: str = "") -> int | None:
    """The end of a comment or literal that starts at `i`, else None."""
    c = source[i]
    end = _comment_end(source, i) if c == "/" else _raw_end(source, i, raw)
    if end is not None:
        return end
    if c in "\"`" or (c == "'" and not (rust and _is_lifetime(source, i))):
        return _quoted_end(source, i, c, one_line=not rust and c != "`")
    if c == "/" and not rust and before in _REGEX_BEFORE:
        return _quoted_end(source, i, "/", one_line=True)  # a regex literal, not a division
    return None


def _next_before(before: str, out: list[str], c: str) -> str:
    """The last token before the next character: a whole word, or one punctuation character."""
    if c.isspace():
        return before
    if (c.isalnum() or c in "_$") and out and (out[-1].isalnum() or out[-1] in "_$"):
        return before + c
    return c


def _blank_literal(text: str) -> str:
    """A literal as one `""` token plus spaces, with every newline kept (so later line numbers do not move)."""
    out = list(_blank(text))
    marks = [i for i, ch in enumerate(text) if ch != "\n"][:2]
    if len(marks) < 2:
        return "".join(out)
    out[marks[0]] = '"'
    out[marks[0] + 1 if marks[0] + 1 == marks[1] else marks[0]] = '"' if marks[0] + 1 == marks[1] else '""'
    return "".join(out)


def _strip(source: str, rust: bool, raw: str = "") -> str:
    """Source with comments, strings, chars and regex literals blanked (lines kept)."""
    out: list[str] = []
    i, before = 0, ""
    while i < len(source):
        end = _literal_end(source, i, rust, before, raw)
        if end is None:
            before = _next_before(before, out, source[i])
            out.append(source[i])
            i += 1
        else:
            out.append(_blank_literal(source[i:end]))
            before, i = '"', end
    return "".join(out)


def _blank_directives(text: str) -> str:
    """C: blank each `#` line and its `\\` continuation lines, on text with no comments or strings left."""
    lines = text.split("\n")
    joined = False
    for n, line in enumerate(lines):
        if joined or line.lstrip().startswith("#"):
            joined = line.endswith("\\")
            lines[n] = _blank(line)
    return "\n".join(lines)


@dataclass
class _Frame:
    nests: bool
    function: str | None = None  # the name when this brace opens a top-level function
    inline: bool = False  # a destructuring, object argument or type literal inside parentheses
    parens: int = 0  # the paren depth of the enclosing block, restored when this brace closes
    do_body: bool = False


def _tokens(source: str, lang: CFamily) -> list[tuple[str, int]]:
    pattern = _TOKEN_THIN_ARROW if lang.arrow_token == "->" else _TOKEN
    tokens = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        tokens += [(m.group(0), line_no) for m in pattern.finditer(line)]
    return tokens


# Go: a line that ends with one of these (or a word, a number, `++`, `--`) ends a statement.
_GO_LINE_END = frozenset({")", "]", "}", '""'})


def _ends_go_line(tokens: list[tuple[str, int]], i: int) -> bool:
    tok = tokens[i][0]
    if tok in ("+", "-"):  # `n++`, `n--`: the tokenizer gives two tokens
        return i > 0 and tokens[i - 1] == tokens[i]
    return tok in _GO_LINE_END or bool(re.match(r"[\w$]", tok))


def _insert_line_semicolons(tokens: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Go: add the `;` that the Go spec adds at a line end, so `type H func(int) int` ends there.

    A line that ends with `{`, `(`, `,` or an operator gets no `;`, so a header on two lines goes on.
    A multi-line raw string is one `""` token on its first line, so the `;` goes after that line,
    not after the last line of the string; inside parentheses a `;` does not end the header.
    Equivalent mutant: dropping `++`/`--` from the line ends changes no score of
    valid Go, because such a line is a statement in a body, where the next header decides
    only control and nesting, not a function name.
    """
    out: list[tuple[str, int]] = []
    for i, (tok, line) in enumerate(tokens):
        out.append((tok, line))
        if (i + 1 == len(tokens) or tokens[i + 1][1] != line) and _ends_go_line(tokens, i):
            out.append((";", line))
    return out


def _type_brace(tokens: list[tuple[str, int]], i: int, out: list[tuple[str, int]], lang: CFamily) -> bool:
    """A `{` that opens a type literal, by the rules that `lang` turns on."""
    prev = [t for t, _ in out[-2:]]
    return tokens[i][0] == "{" and (
        (lang.colon_return_types and prev == [")", ":"])
        or (lang.angle_type_literals and prev[-1:] in (["<"], ["extends"]))
        or (i > 0 and tokens[i - 1][0] in lang.type_body_keywords)
    )


def _drop_type_braces(tokens: list[tuple[str, int]], lang: CFamily) -> list[tuple[str, int]]:
    """Drop `{...}` type literals (TypeScript `):` and `<`, Go `interface`), so they never look like a body."""
    out: list[tuple[str, int]] = []
    i = 0
    while i < len(tokens):
        if _type_brace(tokens, i, out, lang):
            if out[-1][0] == ":":
                out.pop()  # `): {...}` is a return type; the next `{` follows `)` like any body
            i = _matching_brace(tokens, i) + 1
            continue
        out.append(tokens[i])
        i += 1
    return out


def _matching_brace(tokens: list[tuple[str, int]], i: int) -> int:
    depth = 0
    for j in range(i, len(tokens)):
        depth += {"{": 1, "}": -1}.get(tokens[j][0], 0)
        if depth == 0:
            return j
    return len(tokens) - 1


def _keyword_name(header: list[str], lang: CFamily) -> str | None:
    for i, tok in enumerate(header):
        if tok in lang.function_keywords:
            return _name_after_keyword(header, i + 1, lang)
    return None


def _name_after_keyword(header: list[str], i: int, lang: CFamily) -> str:
    nxt = header[i] if i < len(header) else ""
    if lang.receivers and nxt == "(":
        return _receiver_method(header, i)
    return nxt if _IDENT.match(nxt) else "<anonymous>"


def _receiver_method(header: list[str], i: int) -> str:
    """Go: the name after the receiver `(...)` at `i`, when `(` follows it; else a closure."""
    j = _matching_paren(header, i)
    name, follow = (header[j + 1 : j + 3] + ["", ""])[:2]
    return name if _IDENT.match(name) and follow == "(" else "<anonymous>"


def _arrow_name(header: list[str]) -> str:
    names = [t for t, after in zip(header, header[1:]) if after == "=" and _IDENT.match(t)]
    return names[0] if names else "<arrow>"


def _drop_annotations(header: list[str]) -> list[str]:
    """Java: the header without each `@a.b.Name` and its `(...)` arguments."""
    out: list[str] = []
    i = 0
    while i < len(header):
        if header[i] != "@":
            out.append(header[i])
            i += 1
            continue
        i += 2  # `@` and the first name
        while header[i : i + 1] == ["."]:
            i += 2
        if header[i : i + 1] == ["("]:
            i = _matching_paren(header, i) + 1
    return out


def _matching_paren(header: list[str], i: int) -> int:
    depth = 0
    for j in range(i, len(header)):
        depth += {"(": 1, ")": -1}.get(header[j], 0)
        if depth == 0:
            return j
    return len(header)


def _method_name(header: list[str], lang: CFamily) -> str | None:
    header = _drop_annotations(header) if lang.annotations else header
    if "(" not in header or ")" not in header or header.index("(") == 0 or (lang.initializers and header[-1] == "="):
        return None  # `static int (*h[])(int) = {`: an initializer, not a body
    at = header.index("(")
    name, before = header[at - 1], header[at - 2] if at > 1 else ""
    if lang.bracket_lambdas and name == "]":
        return "<lambda>"  # `[&](int x) {`; the control check first: `if (t[i](x)) {` has `if` before `(`
    words = {name, before} & lang.not_method_words  # `try (`, `synchronized (`, `record P(`, `case F(1):`
    return name if _IDENT.match(name) and name not in _NOT_METHOD and not words else None


def _switch_rule(header: list[str], lang: CFamily) -> bool:
    """Java `case 1 -> {`, `case P(int x) when x > 0 -> {`: one arrow at the end, balanced parens, no `:`.

    Equivalent mutant: without the paren test the rows still pass, because a lambda in
    a call after a label has a second arrow (a rule) or a `:` (a label). The test stays (R4.1).
    """
    arrow = lang.arrow_token
    return (lang.switch_rules and header[:1] in (["case"], ["default"]) and header[-1:] == [arrow]
            and header.count("(") == header.count(")") and header.count(arrow) == 1 and ":" not in header)


def _header_function(header: list[str], lang: CFamily) -> str | None:
    """The function name when the tokens before a `{` open a function, else None."""
    if not header or _switch_rule(header, lang):
        return None
    name = _keyword_name(header, lang)
    if name is None and lang.arrows and header[-1] == lang.arrow_token:
        name = _arrow_name(header)
    if name is None and lang.methods_without_keyword:
        name = _method_name(header, lang)
    return name


class _CScorer:
    """One pass over the tokens. Scores add to the top-level function that holds them."""

    def __init__(self, lang: CFamily) -> None:
        self.lang = lang
        self.stack: list[_Frame] = []
        self.header: list[str] = []
        self.pending_nest = False
        self.last_bool: str | None = None
        self.prev = ""
        self.parens = 0  # paren depth inside the current block
        self.after_do = False  # the `}` of a `do` body was the last token: its `while` is the same loop
        self.current: dict | None = None
        self.results: list[FunctionScore] = []

    def nesting(self) -> int:
        return sum(1 for f in self.stack if f.nests) - 1  # the function's own body is level 0

    def add(self, points: int) -> None:
        if self.current is not None:
            self.current["score"] += points

    def run(self, tokens: list[tuple[str, int]]) -> list[FunctionScore]:
        for index, (tok, line) in enumerate(tokens):
            nxt = tokens[index + 1][0] if index + 1 < len(tokens) else ""
            self.step(tok, line, nxt)
            self.prev = tok
        if self.stack or self.current is not None:
            raise LostStructure(f"{len(self.stack)} brace(s) still open at the end of the file")
        return self.results

    def step(self, tok: str, line: int, nxt: str) -> None:
        self.after_do = self.after_do and tok == "while"
        if tok == "{":
            self.open_brace(line)
        elif tok == "}":
            self.close_brace()
        elif tok == ";" and not self.in_inline():
            self.end_statement()
        else:
            self.parens += {"(": 1, ")": -1}.get(tok, 0)
            self.last_bool = None if tok == "," else self.last_bool
            self.header.append(tok)
            self.score_token(tok, nxt)

    def inline_brace(self) -> bool:
        """A `{` inside parentheses that is not a block."""
        if self.parens <= 0:
            return False
        if self.lang.block_expressions:
            return self.prev in _INLINE_AFTER  # `Ok(match x {` and `f(if a {` are blocks
        # TypeScript: only `=> {`, `) {`, a class expression body (`new (class {`) and a function literal
        # with a result type (`g(func() bool {`, `g(function (): T {`, cd-100-F29) are blocks.
        return (self.prev not in (self.lang.arrow_token, ")") and not self._in_class_expression()
                and not self._function_literal())

    def _function_literal(self) -> bool:
        """A function keyword at depth 0 after the last open `(` of the header: the `{` opens its body.

        Equivalent mutants: without the stop at the open `(`, or with the keyword at any
        depth, no row changes. A keyword in a closed group (`g((func())(nil), T{...})`) comes before
        a composite literal, whose braces hold no scored structure. The two guards stay.
        """
        depth = 0
        for tok in reversed(self.header):
            depth += {")": 1, "(": -1}.get(tok, 0)
            if depth < 0:
                return False
            if depth == 0 and tok in self.lang.function_keywords:
                return True
        return False

    def _in_class_expression(self) -> bool:
        opened = len(self.header) - self.header[::-1].index("(") if "(" in self.header else 0
        return "class" in self.header[opened:]

    def in_inline(self) -> bool:
        return bool(self.stack) and self.stack[-1].inline

    def end_statement(self) -> None:
        """A `;` ends a statement. Inside `for (;;)` it does not end the header."""
        self.last_bool = None
        if self.parens <= 0 and not self._header_goes_on():
            self.header, self.pending_nest = [], False

    def _header_goes_on(self) -> bool:
        """Go `for i := 0; i < n; i++ {`: a `;` after a control keyword is part of its header."""
        return self.lang.headers_without_parens and self.pending_nest

    def open_brace(self, line: int) -> None:
        if self.inline_brace():
            # The header goes on through a destructuring, an object argument or a type.
            self.stack.append(_Frame(nests=False, inline=True, parens=self.parens))
            self.parens, self.last_bool = 0, None  # `{a && x}{b && y}`: a brace ends a boolean sequence
            return
        name = _header_function(self.header, self.lang)
        if name is not None and self.current is None:
            self.current = {"name": name, "line": line, "score": 0}
            self.stack.append(_Frame(nests=True, function=name, parens=self.parens))
        else:
            # A nested function or a control structure nests; a plain block, object or impl does not.
            nests = self.pending_nest or (name is not None and self.current is not None)
            self.stack.append(_Frame(nests=nests, parens=self.parens, do_body=self.prev == "do"))
        self.header, self.pending_nest, self.last_bool, self.parens = [], False, None, 0

    def close_brace(self) -> None:
        if not self.stack:
            raise LostStructure("a `}` with no open brace")
        frame = self.stack.pop()
        self.parens = frame.parens
        self.after_do = frame.do_body
        if frame.inline:
            self.last_bool = None
            return
        if frame.function is not None and self.current is not None:
            self.results.append(FunctionScore(self.current["name"], self.current["line"], self.current["score"]))
            self.current = None
        self.header, self.last_bool = [], None

    def score_token(self, tok: str, nxt: str) -> None:
        if self.current is None:
            return
        if self.prev in (".", "?."):
            return  # `p.catch(e)`, `gen.if(...)`, `gen.else()`: a property, not control flow
        if tok in self.lang.control:
            self.score_control()
        elif tok == "else":
            self.add(1)  # `else` and `else if`: hybrid, no nesting increment
            self.pending_nest = True
        elif _is_ternary(tok, nxt, self.prev, self.lang):
            self.add(1 + self.nesting())
        elif tok in ("&&", "||"):
            self.score_boolean(tok)

    def score_control(self) -> None:
        if self.after_do:  # `do { } while (x)`: one loop, scored at `do`
            return
        if self.prev != "else":
            self.add(1 + self.nesting())
        self.pending_nest = True

    def score_boolean(self, tok: str) -> None:
        if tok == "||" and self.lang.closures_use_pipes and not re.match(r"[\w)\]\"]", self.prev or "("):
            return  # a Rust closure with no arguments, not a logical or
        if tok != self.last_bool:
            self.add(1)
        self.last_bool = tok


def _is_ternary(tok: str, nxt: str, prev: str, lang: CFamily) -> bool:
    """A `?` that is a ternary: not `?:`, and not a Java wildcard (a `?` right after `<` or `,`)."""
    return tok == "?" and lang.ternary and nxt != ":" and not (lang.generic_wildcards and prev in ("<", ","))


def _c_family(lang: CFamily) -> Callable[[str], list[FunctionScore]]:
    def analyze(source: str) -> list[FunctionScore]:
        text = _strip(source, lang.rust, lang.raw_strings)
        tokens = _tokens(_blank_directives(text) if lang.preprocessor else text, lang)
        if lang.line_semicolons:
            tokens = _insert_line_semicolons(tokens)
        return _CScorer(lang).run(_drop_type_braces(tokens, lang))

    return analyze


# --- the analyzer table -------------------------------------------------------------------------

ANALYZERS: dict[str, Callable[[str], list[FunctionScore]]] = {
    ".py": analyze_python,
    ".ts": _c_family(TYPESCRIPT),
    ".tsx": _c_family(TYPESCRIPT),
    ".js": _c_family(TYPESCRIPT),
    ".mjs": _c_family(TYPESCRIPT),
    ".cjs": _c_family(TYPESCRIPT),
    ".jsx": _c_family(TYPESCRIPT),
    ".mts": _c_family(TYPESCRIPT),
    ".cts": _c_family(TYPESCRIPT),
    ".rs": _c_family(RUST),
    ".go": _c_family(GO),
    ".java": _c_family(JAVA),
    ".c": _c_family(C_CPP),
    ".h": _c_family(C_CPP),
    ".cc": _c_family(C_CPP),
    ".cpp": _c_family(C_CPP),
    ".hpp": _c_family(C_CPP),
}

# Files that hold code but have no analyzer yet. Listed as unmeasured, never passed silently.
CODE_EXTENSIONS = frozenset(ANALYZERS) | frozenset(
    {".kt", ".cs", ".swift", ".rb", ".php",
     ".scala", ".vue", ".svelte", ".sh", ".bash", ".zsh", ".lua", ".dart", ".m", ".sql"}
)


def analyzer_for(path: str | Path) -> Callable[[str], list[FunctionScore]] | None:
    return ANALYZERS.get(Path(path).suffix.lower())


def is_code(path: str | Path) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS


def analyze(path: str | Path, source: str) -> list[FunctionScore]:
    """Scores for one file.

    Raises KeyError for a language with no analyzer, SyntaxError for bad Python, and
    LostStructure when the C-family scanner loses the braces.
    """
    analyzer = analyzer_for(path)
    if analyzer is None:
        raise KeyError(Path(path).suffix)
    return analyzer(source)


def _files(paths: list[str]) -> Iterable[Path]:
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink() and analyzer_for(p)
                              and not any(part.startswith(".") or part == "node_modules" for part in p.parts))
        elif path.is_file():
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cognitive complexity per function.")
    parser.add_argument("paths", nargs="*", default=["."])
    parser.add_argument("--max", type=int, default=None, help="default: cognitive_complexity.max of the resolved config")
    args = parser.parse_args(argv)
    limit = args.max if args.max is not None else load_config(Path(__file__).resolve().parent.parent)["max"]
    rows: list[tuple[int, str]] = []
    unmeasured: list[str] = []
    measured = 0
    for path in _files(args.paths):
        try:
            scores = analyze(path, path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, LostStructure) as exc:
            unmeasured.append(f"{path} ({exc.__class__.__name__})")
            continue
        measured += len(scores)
        rows += [(s.score, f"{path}:{s.line} {s.name}") for s in scores if s.score > limit]
    for score, where in sorted(rows, reverse=True):
        print(f"{score:4d}  {where}")
    for where in unmeasured:
        print(f"   ?  {where}")
    print(f"{len(rows)} of {measured} functions are over {limit}; {len(unmeasured)} files unmeasured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
