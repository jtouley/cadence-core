"""R3 cognitive complexity, every language with an analyzer (gh-100).

The analyzer tables give a hand-scored example per rule (SonarSource cognitive
complexity). The gate tests use a real repo: `main` holds the base, a feature
branch holds the change, and the gate judges only what got worse.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import git, project_config, write_files

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cadence_config  # noqa: E402
import cognitive_complexity as cc  # noqa: E402
import cognitive_complexity_gate as gate  # noqa: E402


def scores(ext: str, source: str) -> dict[str, int]:
    return {s.name: s.score for s in cc.analyze(f"x{ext}", source)}


# --- analyzers ------------------------------------------------------------------------------------

PYTHON = [
    ("straight", "def f():\n    return 1\n", {"f": 0}),
    ("if elif else", "def f(a):\n    if a:\n        pass\n    elif b:\n        pass\n    else:\n        pass\n", {"f": 3}),
    ("else-if is nested, not elif", "def f(a):\n    if a:\n        pass\n    else:\n        if b:\n            pass\n", {"f": 4}),
    ("nesting", "def f(x):\n    for i in x:\n        while i:\n            if i:\n                pass\n", {"f": 6}),
    ("boolean sequences", "def f(a, b, c):\n    return a and b or c\n", {"f": 2}),
    ("same operator is one sequence", "def f(a, b, c):\n    return (a and b) and c\n", {"f": 1}),
    ("except and ternary", "def f():\n    try:\n        x = 1 if a else 2\n    except E:\n        pass\n", {"f": 2}),
    ("try-else is not counted", "def f():\n    try:\n        pass\n    except E:\n        pass\n    else:\n        pass\n", {"f": 1}),
    ("loop else", "def f(x):\n    for i in x:\n        pass\n    else:\n        pass\n", {"f": 2}),
    ("a comprehension adds nothing", "def f(x):\n    return [i for i in x if i]\n", {"f": 0}),
    ("but its boolean operators count", "def f(x):\n    return [i for i in x if i and j]\n", {"f": 1}),
    ("nested function adds with nesting",
     "def f():\n    def g():\n        if a:\n            pass\n    return g\n", {"f": 2}),
    ("N3 a def under a module-level if", "if TYPE_CHECKING:\n    def f(a):\n        if a:\n            pass\n", {"f": 1}),
    ("methods are named by class",
     "class K:\n    def m(self):\n        if a:\n            pass\n", {"K.m": 1}),
    ("worked example", "def f(x):\n    if x:\n        for i in x:\n            if i and j or k:\n                pass\n"
                       "    elif y:\n        pass\n    else:\n        pass\n", {"f": 10}),
]


TYPESCRIPT = [
    ("function and method",
     "function a(x: number) { if (x) { return 1; } }\nclass K { m(q?: number) { return q ? 1 : 2; } }",
     {"a": 1, "m": 1}),
    ("else if and else are hybrid",
     "function a() { if (x) { } else if (y) { } else { } }", {"a": 3}),
    ("nesting and boolean sequences",
     "function a() { for (const x of xs) { if (x && y || z) { } } }", {"a": 5}),
    ("a nested arrow nests", "function a() { xs.map((v) => { if (v) { } }); }", {"a": 2}),
    ("a top-level arrow is named by its const", "export const go = async (a) => { while (a) { } };", {"go": 1}),
    ("optional chaining, nullish and optional params are not ternaries",
     "function a(p?: T) { return p?.x ?? 1; }", {"a": 0}),
    ("strings and comments hide code",
     'function a() { const s = "if (x) { }"; // if (y) {\n /* while (z) { */ return `${"for"}`; }', {"a": 0}),
    ("catch and switch", "function a() { try { } catch (e) { switch (e) { } } }", {"a": 3}),
    ("more than one parameter", "function a(x: number, y: string, z) { if (x) { } }", {"a": 1}),
    ("a regex with quotes is not a string", "function a(s) { const r = /^[\"']/; if (s) { } }\nfunction b() { if (c) { } }",
     {"a": 1, "b": 1}),
    ("an object return type is not the body",
     'function a(h: string, r: any): { kind: "x" | "y"; why?: string } { if (r) { return 1; } }', {"a": 1}),
    ("a type literal in a generic is not the body",
     "function a(): Promise<{ ok: boolean }> { if (x) { } }", {"a": 1}),
    ("T1 a destructured parameter is not the body",
     "export function Card({ title, items }: Props) { if (title) { for (const i of items) { } } }", {"Card": 3}),
    ("T1 an object-typed parameter", "function f(o: { a: number; b: string }) { if (o.a && o.b) { } }", {"f": 2}),
    ("T1 an object default", "function f(opts = { x: 1 }) { if (opts) { } }", {"f": 1}),
    ("T2 an apostrophe in JSX text", "function A() {\n  return <p>Don't stop</p>;\n}\nfunction B() { if (x) { } }",
     {"A": 0, "B": 1}),
    ("T2 a JSX closing tag", "function A() {\n  return (<ul>{xs.map((x) => <li>{x}</li>)}</ul>);\n}\nfunction B() { if (x) { } }",
     {"A": 0, "B": 1}),
    ("T3 a regex after return", 'function f(s) { return /{"/.test(s); }\nfunction g() { if (y) { } }', {"f": 0, "g": 1}),
    ("division is not a regex", "function f(a, b, c) { const x = a / b / c; if (x) { } }", {"f": 1}),
    ("for (;;) keeps its nesting", "function f() { for (let i = 0; i < n; i++) { if (a) { } } }", {"f": 3}),
    ("N5 a statement ends a pending nest", "function f(x) { if (!x) return; try { if (y) { } } catch (e) { } }", {"f": 3}),
    ("MF-4 a type literal after extends", "function f<T extends { id: string }>(x: T) { if (x) {} }", {"f": 1}),
    ("MF-4 an intersection type", "function f(o: Base & { x: number }) { if (o) { if (o.x) {} } }", {"f": 3}),
    ("MF-4 a union of object types", "function f(o: { a: 1 } | { b: 2 }) { if (o) {} }", {"f": 1}),
    ("MF-4 props with an intersection",
     "export function Card(props: Props & { extra?: string }) { if (props.extra) { } }", {"Card": 1}),
    ("MF-4 an array default", "function f(a = [{ x: 1 }]) { if (a) {} }", {"f": 1}),
    ("a for-of destructuring still nests", "function f() { for (const { a } of xs) { if (a) {} } }", {"f": 3}),
    ("an IIFE body is a nested function", "function f() { (function () { if (a) {} })(); }", {"f": 2}),
    ("an object method inside a call", "function f() { g({ m() { if (a) {} } }); }", {"f": 2}),
    ("N15 a ternary with string branches", 'function f(on) { return on ? "a" : "b"; }', {"f": 1}),
    ("N5 do-while is one loop", "function f() { do { if (a) {} } while (x); }", {"f": 3}),
    ("MF-5 JSX containers end a boolean sequence", "function C() { return (<div>{a && x}{b && y}</div>); }", {"C": 2}),
    ("MF-5 a class expression body holds methods", "const R = new (class { m() { if (a) { } } })();", {"m": 1}),
    ("N21 a control word after a dot is a property",
     "function f(p, gen) { p.catch((e) => {}); gen.if(a); gen.else(); x.do(1); }", {"f": 0}),
    ("N15 a literal that starts with a newline", "function f(a) { return a ? `\n color: red;` : ''; }", {"f": 1}),
    ("F29 a function expression with a return type in a call",
     "function f() { g(function (): boolean { if (a) { if (b) { } } }); }", {"f": 5}),
]


RUST = [
    ("match and loop", "fn a(v: &[i32]) { for x in v { match x { _ => {} } } loop { break; } }", {"a": 4}),
    ("a match arm is not a closure or function", "fn a() { match x { A => { if b {} } } }", {"a": 3}),
    ("closures, lifetimes and chars are not logic",
     "fn a<'b>(s: &'b str) -> bool { let f = || true; let c = '}'; s.is_empty() || f() }", {"a": 1}),
    ("impl methods", "impl K { fn new() -> Self { if a && b { } Self {} } }", {"new": 2}),
    ("MF-5 a match as an argument is a block", "fn f(k: u8) -> R { Ok(match k { 0 => { if a { 1 } else { 2 } } _ => 3 }) }",
     {"f": 4}),
    ("MF-5 an if as an argument is a block", "fn f(m: bool) { h(if m { if a { 1 } else { 2 } } else { 3 }); }", {"f": 5}),
    ("a struct literal argument is inline", "fn f() { g(S { a: 1 }); if b { } }", {"f": 1}),
    ("N20 || after a byte literal", "fn f(d: u8) -> bool { if d < b'0' || d > b'9' { } true }", {"f": 2}),
    ("N20 || after a string", 'fn f(s: &str) -> bool { s == "x" || s == "y" }', {"f": 1}),
    ("T4 a raw string with quotes and braces", 'fn a() -> &str { r#"he said "}" \\"#;\n if b { } "" }\nfn c() { if d { } }',
     {"a": 1, "c": 1}),
]


GO = [
    ("VT#1 if and else if", "func F(a int) { if a > 0 { } else if a < 0 { } }", {"F": 2}),
    ("VT#2 receiver", "func (r *T) Name() { if x { } }", {"Name": 1}),
    ("VT#3 receiver with no name", "func (T) Name() { if x { } }", {"Name": 1}),
    ("a generic receiver", "func (r *T[K]) Get(k K) { if x { } }", {"Get": 1}),
    ("VT#4 a closure nests", "func F() { go func() { if x { } }() }", {"F": 2}),
    ("VT#5 a struct literal and a raw string with a brace",
     "func F() {\n\tx := T{A: 1}\n\ts := `{`\n\tif x.A > 0 { }\n\tfor { }\n}", {"F": 2}),
    ("VT#6 for, select and case", "func F() { for { select { case <-c: if x { } } } }", {"F": 6}),
    ("VT#7 a for header with semicolons", "func F(n int) { for i := 0; i < n; i++ { if a { } } }", {"F": 3}),
    ("VT#8 an if header with a statement", "func F(m map[string]int, k string) { if v, ok := m[k]; ok { if v > 0 { } } }",
     {"F": 3}),
    ("VT#9 range", "func F(xs []int) { for _, i := range xs { if i > 0 { } } }", {"F": 3}),
    ("VT#10 type parameters", "func Map[T any](xs []T) { if x { } }", {"Map": 1}),
    ("a statement ; still ends a header", "func F() { x := 1; if a { } }", {"F": 1}),
    ("MF-1 a func type before a function", "type H func(int) int\n\nfunc F() { if x { } }", {"F": 1}),
    ("MF-1 a method of a func type", "type HandlerFunc func(int)\n\nfunc (f HandlerFunc) Serve() { if x { } }", {"Serve": 1}),
    ("MF-1 a func variable", "var hook func()\nfunc F() { if x { } }", {"F": 1}),
    ("MF-1 a func result type", "func F() func() int { if x { } ; return nil }", {"F": 1}),
    ("MF-1 a header on two lines", "func F(\n\ta int,\n) { if a > 0 { } }", {"F": 1}),
    ("MF-2 an interface result", "func F() interface{} { if x { } }", {"F": 1}),
    ("MF-2 a struct result", "func F() struct{ a int } { if x { } }", {"F": 1}),
    ("MF-2 a map of interface result", "func F() map[string]interface{} { if x { } }", {"F": 1}),
    ("MF-3 a case block", "func F(k int) { switch k { case f(): { if a { } } } }", {"F": 3}),
    ("SF-1 approximation: range over a composite literal does not nest",
     "func F() { for _, v := range []int{1, 2} { if v > 0 { } } }", {"F": 2}),
    ("SF-7 a raw string that ends with a backslash",
     'func F(s string) string { s = strings.TrimSuffix(s, `\\`); if s == "" { }; return s }', {"F": 1}),
    ("SF-7 a raw path", 'func F() { p := `C:\\`; if p != "" { } }', {"F": 1}),
    ("SF-7 two raw strings that end with a backslash", "func F() { a, b := `x\\`, `y\\`; if x { } }", {"F": 1}),
    ("SF-8 a multi-line raw string", 'func F() { x := `a\nb`\n if x != "" { if y { } } }', {"F": 3}),
    ("F28 a line that ends with }", "var hook func() interface{}\nfunc F() { if x { } }", {"F": 1}),
    ("F28 a line that ends with ]", "var hook func() = hs[0]\nfunc F() { if x { } }", {"F": 1}),
    ("F28 a line that ends with ++", "func F() {\n\tn++\n\tgo func() { if x { } }()\n}", {"F": 2}),
    ("F28 a line that ends with --", "func F() {\n\tn--\n\tgo func() { if x { } }()\n}", {"F": 2}),
    ("F28 a line that ends with a string", 'func F() {\n\ts := "a"\n\tgo func() { if x { } }()\n}', {"F": 2}),
    ("F28 a line that ends with a number", "func F() {\n\tn := 1\n\tgo func() { if x { } }()\n}", {"F": 2}),
    ("F28 a string ends the line before a func", 'var hook func() = "a"\nfunc F() { if x { } }', {"F": 1}),
    ("F28 a number ends the line before a func", "var hook func() = 1\nfunc F() { if x { } }", {"F": 1}),
    ("F26 a closure with a result type is anonymous", "var f = func() int { if x { }; return 1 }", {"<anonymous>": 1}),
    ("F29 a closure with a result type in a call nests", "func F() { sort.Slice(xs, func(i, j int) bool { if a { if b { } } }) }",
     {"F": 5}),
    ("F29 a closure that returns an error in a call", "func F() { g(func() error { if a { if b { } } }) }", {"F": 5}),
    ("F29 a closure that returns a pointer in a call", "func F() { g(xs, func(i int) *T { if a { if b { } } }) }", {"F": 5}),
    ("F29 a closure in a call inside an if", "func F() { if a { g(func() bool { if b { if c { } } }) } }", {"F": 8}),
]


def _in_switch(body: str) -> str:
    """A Java method `f` whose body is one `switch (k)` with these cases."""
    return "class A { void f(int k) { switch (k) { " + body + " } } }"


JAVA = [
    ("VT#11 a method of a subclass", "class A extends B { int f() { if (a) { } } }", {"f": 1}),
    ("VT#12 an annotation", 'class A { @SuppressWarnings("x") int f() { if (a) { } } }', {"f": 1}),
    ("SF-5 a qualified annotation", 'class A { @javax.annotation.Generated("x") int f() { if (a) { } } }', {"f": 1}),
    ("VT#13 a lambda body nests", "class A { void f() { xs.forEach(x -> { if (x) { } }); } }", {"f": 2}),
    ("VT#14 approximation: a lambda without braces does not nest",
     "class A { int f() { return xs.map(x -> x > 0 ? 1 : 2); } }", {"f": 1}),
    ("VT#15 a switch rule does not nest", _in_switch("case 1 -> { if (a) { } } default -> { }"), {"f": 3}),
    ("a record pattern rule", _in_switch("case Point(int x) -> { if (a) { } } default -> { }"), {"f": 3}),
    ("R4.1 a rule with two labels", _in_switch("case 1, 2 -> { if (a) { } } default -> { }"), {"f": 3}),
    ("R4.1 a guarded rule", _in_switch("case Point(int x) when x > 0 -> { if (a) { } } default -> { }"), {"f": 3}),
    ("R4.1 a lambda in a call after a rule", _in_switch("case 1 -> xs.forEach(x -> { if (a) { } }); default -> { }"),
     {"f": 4}),
    ("R4.1 a lambda in a call after a label", _in_switch("case 1: xs.forEach(x -> { if (a) { } });"), {"f": 4}),
    ("R4.1 a lambda assigned after a label", _in_switch("case 1: Runnable r = () -> { if (a) { } }; break;"), {"f": 4}),
    ("R4.1 a one-parameter lambda after a label", _in_switch("case 1: F g = x -> { if (a) { } }; break;"), {"f": 4}),
    ("R4.1 a rule that yields a lambda", _in_switch("case 1 -> () -> { if (a) { } }; default -> null;"), {"f": 4}),
    ("R3.3 approximation: an anonymous class in a rule nests as a function",
     _in_switch("case 1 -> new R() { public void run() { if (a) { } } }; default -> { }"), {"f": 5}),
    ("MF-3 a colon pattern label is a block",
     "class A { void f(Object o) { switch (o) { case Point(int x): { if (a) { } } } } }", {"f": 3}),
    ("B-1 a default method", "interface I { default int f(int a) { if (a > 0) { } return 0; } int g(); }", {"f": 1}),
    ("B-1 a default method with nesting",
     "interface I { default int f(int a) { if (a > 0) { if (a > 1) { } } return 0; } }", {"f": 3}),
    ("VT#16 wildcards are not ternaries", "class A { void f() { List<? extends T> xs = g(); Map<?, ?> m = h(); } }", {"f": 0}),
    ("C-2 a ternary before super", "class A { int f(boolean c) { return c ? super.f() : g(); } }", {"f": 1}),
    ("C-2 a wildcard and a ternary", "class A { void f(boolean c) { Class<?> k = c ? A.class : B.class; } }", {"f": 1}),
    ("a ternary still scores", "class A { int f() { return a ? 1 : 2; } }", {"f": 1}),
    ("VT#17 try with resources is not a function",
     "class A { void f() { try (R r = open()) { if (a) { } } catch (E e) { } } }", {"f": 2}),
    ("VT#18 synchronized is not a function", "class A { void f() { synchronized (this) { if (a) { } } } }", {"f": 1}),
    ("CH4 a synchronized method", "class A { synchronized void f() { if (a) { } } }", {"f": 1}),
    ("VT#19 a record is not a function",
     "record P(int x) { int a() { if (x > 0) { } } int b() { if (x < 0) { } } }", {"a": 1, "b": 1}),
    ("B-2 a text block with ? marks",
     'class A { void f() { String q = """\n  SELECT * FROM t WHERE a = ? AND b = ?\n  """; if (x) { } } }', {"f": 1}),
    ("B-2 a text block that closes before )",
     'class A { void g() { run("""\n  x""");\n if (a) { } else { if (b) { } } } }', {"g": 4}),
    ("B-2 an escaped quote run in a text block",
     'class A { void f() { String s = """\n  a \\""" { b\n  """; if (x) { } } }', {"f": 1}),
    ("VT#22 a text block with a brace", 'class A { void f() { String s = """\n{\n"""; if (x) { } } }', {"f": 1}),
    ("F25 an empty string and a quote char are not text blocks",
     "class A { void f() { String s = \"\"; char c = '\"'; if (a) { } } }", {"f": 1}),
]

C_CPP_ROWS = [
    ("VT#23 if and else", "static int f(int a) { if (a) { } else { } }", {"f": 2}),
    ("VT#24 array initializers are not functions", "int xs[] = {1, 2, 3};\nint a[] = { f(1) };\nint g() { if (x) { } }",
     {"g": 1}),
    ("VT#25 initializers in a body", "int f() { int xs[] = {1, 2}; struct P p = {.x = g(1)}; if (a) { } }", {"f": 1}),
    ("VT#26 a function-pointer table is not a function",
     "static int (*handlers[])(int) = { f, g };\nint h() { if (x) { } }", {"h": 1}),
    ("VT#27 directives add nothing, the code between them counts",
     "int f() {\n#if DEBUG\n if (a) { }\n#else\n if (b) { }\n#endif\n return 0; }", {"f": 2}),
    ("a directive with a continuation line", "int f() {\n#define M(a) \\\n  if (a) { }\n return 0; }", {"f": 0}),
    ("VT#29 a macro body is not a function", "#define SWAP(a, b) { int t = a; a = b; b = t; }\nint f() { if (x) { } }",
     {"f": 1}),
    ("VT#30 extern C", 'extern "C" { int f() { if (a) { } } }', {"f": 1}),
    ("VT#31 a lambda in a call nests", "int f() { std::sort(a, b, [](int x, int y) { return x < y ? 1 : 0; }); }",
     {"f": 2}),
    ("VT#32 a constructor and a const method", "class K { K() : x(1) { if (a) { } } int m() const { if (a) { } } };",
     {"K": 1, "m": 1}),
    ("VT#34 a qualified method name", "namespace n { int K::m(int a) { if (a) { } } }", {"m": 1}),
    ("VT#35 -> is member access", "int f(P* p) { return p->x && p->y ? 1 : 0; }", {"f": 2}),
    ("CH1 a default template argument", "template <typename T = int> T f() { if (x) { } }", {"f": 1}),
    ("CH3 if constexpr", "int f() { if constexpr (x) { if (y) { } } }", {"f": 3}),
    ("try and catch", "int f() { try { } catch (E& e) { if (a) { } } }", {"f": 3}),
    ("MF-3 a macro in a case label is a block", "int f(int k) { switch (k) { case F(1): { if (a) { } } } }", {"f": 3}),
    ("F24 a continuation inside a comment", "/*\n#define X \\\n*/\nint g() { if (y) { } }", {"g": 1}),
    ("F24 a # line inside a joined string", 'int g() { const char *s = "a\\\n#if {"; if (y) { } }', {"g": 1}),
]

# C++ only (R7.2: 9 rows).
CPP_ONLY_ROWS = [
    ("B-2 a raw string with a ?", 'int f() { const char *s = R"(\nx ? y\n)"; if (a) { } }', {"f": 1}),
    ("B-2 a raw string with a delimiter", 'int f() { const char *s = R"d(\n{ )"\n)d"; if (a) { } }', {"f": 1}),
    ("VT#33 a raw string with a brace", 'int f() { const char *s = R"(\n{\n)"; if (a) { } }', {"f": 1}),
    ("B-2 a u8 raw string", 'int f() { auto s = u8R"(\nx ? y\n)"; if (a) { } }', {"f": 1}),
    ("C-1 an assigned lambda nests", "int f() { auto g = [&](int x) { if (x) { if (y) { } } }; return g(1); }", {"f": 5}),
    ("C-1 a lambda at namespace scope is a function",
     "auto g = [](int x) { if (x) { if (y) { } } return 0; };\nint h() { if (a) { } }", {"<lambda>": 3, "h": 1}),
    ("C-2 approximation: a member brace initializer ends the constructor",
     "struct K { K() : a{1} { if (x) { } } };", {"K": 0}),
    ("F25 R and a quote with no ( is not a raw string", 'int f() { auto R = 1; g(R"x"); if (a) { } }', {"f": 1}),
    ("R7.1 approximation: operator[] is named <lambda>",
     "struct V { T& operator[](int i) { if (i) { } return d; } };", {"<lambda>": 1}),
]

TABLES = [
    ((".py",), PYTHON),
    ((".ts", ".js"), TYPESCRIPT),
    ((".rs",), RUST),
    ((".go",), GO),
    ((".java",), JAVA),
    ((".c", ".cpp"), C_CPP_ROWS),
    ((".cpp",), CPP_ONLY_ROWS),  # R3.2, R5.1, R5.2, R6.5, R7.1
]


def _analyzer_cases():
    for exts, table in TABLES:
        for ext in exts:
            for name, source, want in table:
                yield pytest.param(ext, source, want, id=f"{ext} {name}")


@pytest.mark.parametrize("ext, source, want", list(_analyzer_cases()))
def test_analyzer(ext, source, want):
    assert scores(ext, source) == want


@pytest.mark.parametrize("path, measured, code", [
    ("a.py", True, True), ("a.ts", True, True), ("a.rs", True, True), ("a.mjs", True, True),
    ("a.go", True, True), ("a.java", True, True), ("a.c", True, True), ("a.h", True, True),
    ("a.cpp", True, True), ("a.hpp", True, True), ("a.cc", True, True), ("a.sh", False, True), ("a.md", False, False), ("a.json", False, False),
    ("a.jsx", True, True), ("a.mts", True, True), ("a.cts", True, True), ("a.vue", False, True), ("a.svelte", False, True),
])
def test_the_table_names_what_is_measured_and_what_is_code(path, measured, code):
    assert (cc.analyzer_for(path) is not None) == measured
    assert cc.is_code(path) == code


def test_the_repo_cli_lists_functions_over_the_limit(tmp_path, capsys, monkeypatch):
    (tmp_path / "a.py").write_text(PYTHON[-1][1], encoding="utf-8")
    assert cc.main([str(tmp_path), "--max", "5"]) == 0
    out = capsys.readouterr().out
    assert "  10  " in out and "a.py:1 f" in out and "1 of 1 functions are over 5" in out
    (tmp_path / ".git").mkdir()  # the CLI reads the config of the current directory
    project_config(tmp_path, {"cognitive_complexity": {"max": 5}})
    monkeypatch.chdir(tmp_path)
    assert cc.main([str(tmp_path)]) == 0
    assert "1 of 1 functions are over 5" in capsys.readouterr().out  # D12: the limit comes from the config


# --- code shape: statements per function and the longest branch chain, Python only -----

GUARDS = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(3)) + "    raise E\n"
SHAPE = [
    ("nested statements count", "def f(x):\n    if x:\n        y = 1\n        return y\n    return 0\n", (4, 1)),
    ("a nested function counts", "def f():\n    def g():\n        pass\n    return g\n", (3, 0)),
    ("elif links one chain", "def f(x):\n    if a:\n        pass\n    elif b:\n        pass\n    elif c:\n        pass\n"
     "    else:\n        pass\n", (7, 3)),
    ("else-if starts a new chain", "def f(x):\n    if a:\n        pass\n    else:\n        if b:\n            pass\n", (4, 1)),
    ("an if after a return or raise links the chain", GUARDS, (7, 3)),
    ("an if after an if with no exit does not", "def f(x):\n    if a:\n        pass\n    if b:\n        pass\n", (4, 1)),
    ("each match case is a link", "def f(x):\n    match x:\n        case 1:\n            pass\n        case 2:\n"
     "            pass\n        case _:\n            pass\n", (4, 3)),
]


@pytest.mark.parametrize(("source", "want"), [c[1:] for c in SHAPE], ids=[c[0] for c in SHAPE])
def test_python_code_shape(source, want):
    [fn] = cc.analyze("x.py", source)
    assert (fn.statements, fn.chain) == want


def test_a_c_family_function_has_no_code_shape():
    [fn] = cc.analyze("x.ts", "function a() { if (x) { } }")
    assert (fn.statements, fn.chain) == (None, None)


# --- the gate -------------------------------------------------------------------------------------

def _branching(n: int) -> str:
    """A Python function with cognitive complexity n (n flat ifs).

    Each test has its own shape (a tuple of i values), so the function is not a duplicate of itself.
    """
    return "def busy(x):\n" + "".join(f"    if x == {tuple(range(i))}:\n        pass\n" for i in range(n))


def _make_repo(repo: Path, base: dict[str, str], via: dict[str, str] | None) -> None:
    """`main` holds `base`; with `via`, a `release` branch from `main` holds it, and the feature starts there."""
    import shutil

    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for branch, files in (("main", {"README.md": "x\n", **base}), ("release", via)):
        if files is None:
            continue
        if branch != "main":
            git(repo, "checkout", "-qb", branch)
        write_files(repo, files)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", branch)
    git(repo, "checkout", "-qb", "feature/gh-7-x")


@pytest.fixture
def change(tmp_path, monkeypatch):
    """change(base_files, head_files) -> the gate's exit code, stdout+stderr and breadcrumb."""
    repo = tmp_path / "repo"

    def _change(base: dict[str, str], head: dict[str, str], *, commit_head: bool = True, cfg: dict | None = None,
                check: bool = False, links: dict[str, Path] | None = None, via: dict[str, str] | None = None,
                base_ref: str | None = None, dup: dict | None = None, shape: dict | None = None,
                layer: dict | None = None):
        # The settings go in the project layer (.cadence.json) of the repo the gate measures. `main` holds it,
        # so it is not a changed file.
        given = {**{section: value for section, value in (("cognitive_complexity", cfg), ("duplication", dup),
                                                          ("code_shape", shape)) if value is not None}, **(layer or {})}
        if given:
            base = {".cadence.json": json.dumps(given), **base}
        _make_repo(repo, base, via)
        write_files(repo, head)
        for rel, target in (links or {}).items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).symlink_to(target)
        if commit_head:
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "--allow-empty", "-m", "head")
        monkeypatch.chdir(repo)
        context = repo / ".context"
        code = gate.run(context, check=check, base=base_ref)
        crumb = context / "gates" / "cognitive-complexity.json"
        return code, json.loads(crumb.read_text(encoding="utf-8")) if crumb.is_file() else None

    return _change


def test_a_new_function_over_the_limit_fails(change, capsys):
    code, crumb = change({}, {"src/a.py": _branching(16)})
    assert code == 1
    err = capsys.readouterr().err
    assert "src/a.py:1 busy = 16 (new)" in err and "limit is 15" in err
    assert crumb["findings"][0]["score"] == 16


def test_a_function_at_the_limit_passes(change, capsys):
    code, _ = change({}, {"src/a.py": _branching(15)})
    assert code == 0
    assert capsys.readouterr().out.startswith("OK  cognitive-complexity  1 functions in 1 changed files, limit 15")


def test_old_debt_that_did_not_grow_passes(change):
    code, crumb = change({"src/a.py": _branching(30)}, {"src/a.py": _branching(30) + "\n\ndef small():\n    pass\n"})
    assert code == 0 and crumb["findings"] == []


@pytest.mark.parametrize("base, head, kwargs, needle", [
    pytest.param({"src/a.py": _branching(30)}, {"src/a.py": _branching(31)}, {}, "busy = 31 (was 30)",
                 id="old_debt_that_grew_fails"),
    pytest.param({}, {"run.sh": "echo\n"}, {"cfg": {"unsupported": "fail"}},
                 "1 changed code file(s) not measured: run.sh: no analyzer",
                 id="a_language_with_no_analyzer_fails_when_configured"),
    pytest.param({}, {"src/a.py": "def broken(:\n"}, {}, "cannot parse src/a.py",
                 id="a_changed_file_that_does_not_parse_fails_closed"),
    pytest.param({}, {"src/a.py": _branching(16), "run.sh": "echo\n"}, {},
                 "1 changed code file(s) not measured: run.sh: no analyzer",
                 id="a_fail_also_names_the_unmeasured_files"),
])
def test_a_failing_change_names_the_cause(change, capsys, base, head, kwargs, needle):
    code, _ = change(base, head, **kwargs)
    assert code == 1
    assert needle in capsys.readouterr().err


@pytest.mark.parametrize("check, first", [
    pytest.param(False, "fix step: delegate", id="the-record-is-written"),
    pytest.param(True, "fix step: run R3 without --check to write the record, then delegate", id="check-writes-none"),
])
def test_a_failed_r3_ends_with_the_simplify_fix_step(change, capsys, check, first):
    code, _ = change({}, {"src/a.py": _branching(16)}, check=check)
    last = capsys.readouterr().err.rstrip().splitlines()[-1]
    assert code == 1 and last.startswith(f"  - {first}"), last
    assert "subagents/simplify-code.md with .context/gates/cognitive-complexity.json as its one input" in last
    assert last.endswith("then run R3 again (one cycle, then the review loop rule applies)")


def test_a_fail_that_simplify_cannot_fix_names_no_fix_step(change, capsys):
    code, _ = change({}, {"src/a.py": "def broken(:\n"})
    assert code == 1 and "simplify-code.md" not in capsys.readouterr().err


def test_uncommitted_and_untracked_files_count(change):
    code, crumb = change({}, {"src/a.ts": "function busy() {" + "if (a) {}" * 16 + "}"}, commit_head=False)
    assert code == 1 and crumb["findings"][0]["path"] == "src/a.ts"


def test_unchanged_files_are_not_judged(change):
    code, crumb = change({"src/old.py": _branching(40)}, {"src/new.py": "def f():\n    pass\n"})
    assert code == 0 and crumb["files"] == 1


def test_a_language_with_no_analyzer_warns_by_default(change, capsys):
    code, crumb = change({}, {"run.sh": "if true; then echo; fi\n"})
    assert code == 0 and crumb["unmeasured"] == ["run.sh: no analyzer"]
    assert "1 unmeasured: run.sh: no analyzer" in capsys.readouterr().out


def test_the_list_is_capped(change, capsys):
    head = {f"src/m{i}.py": _branching(16 + i) for i in range(8)}
    code, crumb = change({}, head)
    err = capsys.readouterr().err
    assert code == 1 and len(crumb["findings"]) == 8
    assert err.count(" busy = ") == gate.MAX_LISTED and "and 3 more in .context/gates/cognitive-complexity.json" in err


def test_check_writes_no_breadcrumb(change):
    code, crumb = change({}, {"src/a.py": "def f():\n    pass\n"}, check=True)
    assert code == 0 and crumb is None


@pytest.mark.parametrize(("section", "value"), [
    ("cognitive_complexity", {"max": 0}), ("cognitive_complexity", {"max": "15"}), ("cognitive_complexity", {"max": True}),
    ("cognitive_complexity", {"unsupported": "skip"}), ("cognitive_complexity", []),
    ("duplication", {"max_percent": -1}), ("duplication", {"min_block_lines": 0}), ("duplication", {"unsupported": "off"}),
    ("code_shape", {"max_function_statements": 1.5}), ("code_shape", {"max_branch_chain": None}), ("code_shape", "x"),
])
def test_a_bad_config_raises(tmp_path, section, value):
    body = {section: {**cc.SECTIONS[section], **value} if isinstance(value, dict) else value}
    project_config(tmp_path, body)
    with pytest.raises(cadence_config.ConfigError, match=rf"\.cadence\.json\): {section}"):
        gate.load_config(ROOT, section, tmp_path)


def test_the_shipped_config_and_stage_row(tmp_path):
    (tmp_path / ".git").mkdir()  # a project with no project layer: the shipped defaults only
    for section in ("cognitive_complexity", "duplication", "code_shape", "test_shape"):
        cfg = gate.load_config(ROOT, section, tmp_path)
        assert set(cfg.pop("threshold_source").values()) == {"default"} and cfg == cc.SECTIONS[section]
    rows = json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows["stages"]
    row = next(r for r in rows if r["block"] == "R3")
    assert (row["slug"], row["trigger"], row["gate_script"]) == (
        "cognitive-complexity", "always", "scripts/cognitive_complexity_gate.py")


def test_the_gate_runs_as_registered(tmp_path):
    from conftest import activated_repo

    repo = activated_repo(tmp_path / "ws")
    (repo / "big.py").write_text(_branching(16), encoding="utf-8")

    def gate_cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "cognitive_complexity_gate.py"), *args],
                              cwd=repo, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})

    proc = gate_cli()
    assert proc.returncode == 1 and "big.py:1 busy = 16 (new)" in proc.stderr
    git(repo, "branch", "main")
    proc = gate_cli("--check", "--base", "main")  # MF-4: the flag is parsed and the given base is used
    assert proc.returncode == 1 and "big.py:1 busy = 16 (new)" in proc.stderr
    proc = gate_cli("--check", "--base", "nope")
    assert proc.returncode == 1 and "base ref nope does not resolve" in proc.stderr


@pytest.mark.parametrize("given", ["arg", "env", "both"])
def test_the_gate_judges_against_the_given_base(change, monkeypatch, given):
    """D11: `--base`, then CADENCE_BASE_REF, then the run's base chain. `release` already holds busy = 30."""
    if given in ("env", "both"):
        monkeypatch.setenv("CADENCE_BASE_REF", "release" if given == "env" else "origin/nope")
    head = {"src/a.py": _branching(30) + "\n\ndef small():\n    pass\n"}
    code, crumb = change({}, head, via={"src/a.py": _branching(30)}, base_ref=None if given == "env" else "release")
    assert code == 0 and crumb["base"] == "release"


def test_with_no_given_base_the_gate_falls_back_to_main(change):
    head = {"src/a.py": _branching(30) + "\n\ndef small():\n    pass\n"}
    code, crumb = change({}, head, via={"src/a.py": _branching(30)})
    assert code == 1 and crumb["base"] == "main"


def test_an_empty_given_base_fails_closed(change, capsys):
    """R1 N2: `--base ""` is a given base that does not resolve; it does not fall back to the chain."""
    code, crumb = change({}, {"src/a.py": "def f():\n    pass\n"}, base_ref="")
    assert code == 1 and crumb is None and "does not resolve" in capsys.readouterr().err


@pytest.mark.parametrize("given", ["arg", "env"])
def test_a_given_base_that_does_not_resolve_fails_closed(change, monkeypatch, capsys, given):
    if given == "env":
        monkeypatch.setenv("CADENCE_BASE_REF", "origin/nope")
    code, crumb = change({}, {"src/a.py": "def f():\n    pass\n"}, base_ref="origin/nope" if given == "arg" else None)
    assert code == 1 and crumb is None
    assert "base ref origin/nope does not resolve" in capsys.readouterr().err


# --- PR #101 R1 ------------------------------------------------------------------------------------

LOST = [
    pytest.param("x.ts", "function f() { if (x) { ", id="ts an unclosed brace"),
    pytest.param("x.rs", "fn f() { } }", id="rs an extra close"),
    pytest.param("x.java", 'class A { void f() { String s = """\n{ if (x) { } } }', id="java a text block with no close"),
    pytest.param("x.c", "#ifdef A\nint f(int a) {\n#else\nint f(int a, int b) {\n#endif\n  if (a) { }\n}\n",
                 id="SF-2 c two headers for one function"),
    pytest.param("x.cpp", 'int f() { auto s = R"(\n{ if (a) { } }', id="cpp a raw string with no close"),
]


@pytest.mark.parametrize("path, source", LOST)
def test_lost_structure_is_raised_not_scored(path, source):
    with pytest.raises(cc.LostStructure):
        cc.analyze(path, source)


def test_t6_the_repos_own_register_is_measured():
    """T6: `register` in pi/cadence-gates.ts takes a destructured parameter; it scored 0 before the fix."""
    scores = {s.name: s.score for s in cc.analyze("x.ts", (ROOT / "pi" / "cadence-gates.ts").read_text(encoding="utf-8"))}
    assert scores.get("register", 0) > 15


def _impls(*scores: int) -> str:
    """A Rust file with one `impl` per score, each with `fn new` of that cognitive complexity."""
    return "".join(f"impl T{i} {{ fn new(x: i32) {{ " + "if x {} " * n + "} }\n" for i, n in enumerate(scores))


@pytest.mark.parametrize("base, head, want", [
    ((30,), (30, 20), 1),         # S5 a new same-named function over the limit fails
    ((20, 1), (20, 1), 0),        # S6 an unchanged legacy `new` does not fail
    ((30, 20), (31, 20), 1),      # S9 a legacy `new` that got worse fails
    ((30,), (1, 30), 0),          # S11 a small `new` inserted before a legacy one does not fail
    ((30, 2), (10, 25), 0),       # S10 accepted miss: the total in that name did not grow (option A)
], ids=["S5", "S6", "S9", "S11", "S10-accepted-miss"])
def test_same_named_functions_pair_by_rank(change, base, head, want):
    code, crumb = change({"src/a.rs": _impls(*base)}, {"src/a.rs": _impls(*head)})
    assert len(crumb["findings"]) == want and code == (1 if want else 0)


def test_s7_two_anonymous_arrows_pair_by_rank(change):
    arrow = lambda n: "export const x = [() => { " + "if (a) { } " * n + "}];\n"  # noqa: E731
    code, crumb = change({"src/a.ts": arrow(30)}, {"src/a.ts": arrow(30) + arrow(20)})
    assert code == 1 and [f["base"] for f in crumb["findings"]] == [None]


def test_s8_a_property_getter_and_setter(change):
    prop = "class K:\n    @property\n    def x(self):\n" + "".join(f"        if a == {i}:\n            pass\n" for i in range(20))
    prop += "    @x.setter\n    def x(self, v):\n        pass\n"
    code, crumb = change({"src/k.py": prop}, {"src/k.py": prop + "\n# a comment\n"})
    assert code == 0 and crumb["findings"] == []


def test_a_non_ascii_path_is_judged(change):
    code, crumb = change({}, {"src/café.py": _branching(16)})
    assert code == 1 and crumb["findings"][0]["path"] == "src/café.py"


def test_a_git_error_fails_closed(change, monkeypatch, capsys):
    import impl_artifact

    def broken(*_a, **_k):
        raise impl_artifact.GitError("git diff failed: boom")

    monkeypatch.setattr(gate, "changed_files", broken)
    code, _ = change({}, {"src/a.py": "def f():\n    pass\n"})
    assert code == 1 and "git diff failed: boom" in capsys.readouterr().err


def test_a_lost_file_is_unmeasured_with_its_reason(change, capsys):
    code, crumb = change({}, {"src/a.ts": "function f() { if (x) { "})
    assert code == 0 and crumb["unmeasured"][0].startswith("src/a.ts: scanner lost the structure")
    code, _ = change({}, {"src/b.ts": "function f() { if (x) { "}, cfg={"unsupported": "fail"})
    assert code == 1 and "scanner lost the structure" in capsys.readouterr().err  # N19


def test_a_bad_config_is_a_fail_line_not_a_traceback(change, monkeypatch, capsys):
    def bad(*_args):
        raise ValueError("cognitive_complexity.max must be a positive integer, got 0")

    monkeypatch.setattr(gate, "load_config", bad)
    code, _ = change({}, {"src/a.py": "def f():\n    pass\n"})
    assert code == 1 and "FAIL  cognitive-complexity" in capsys.readouterr().err


def test_a_symlink_is_not_read(change, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text(_branching(40), encoding="utf-8")
    code, crumb = change({}, {"src/a.py": "def f():\n    pass\n"}, links={"src/link.py": outside})
    assert code == 0 and crumb["files"] == 1


def test_unparsable_files_are_capped(change, capsys):
    code, _ = change({}, {f"src/b{i}.py": "def broken(:\n" for i in range(8)})
    err = capsys.readouterr().err
    assert code == 1 and err.count("cannot parse") == gate.MAX_LISTED and "and 3 more unparsable files" in err


LINES = [
    pytest.param("x.ts", "function a() {\n  const css = `\n.x {}\n`;\n  const s = 'it\n}\nfunction b() { if (x) {} }",
                 [("a", 1), ("b", 7)], id="N18 ts multi-line literals"),
    pytest.param("x.c", "#define M(a) \\\n  (a)\nint f() { if (x) { } }\n", [("f", 3)], id="F24 c directive lines are kept"),
]


@pytest.mark.parametrize("path, source, want", LINES)
def test_multi_line_text_keeps_line_numbers(path, source, want):
    assert [(f.name, f.line) for f in cc.analyze(path, source)] == want


def test_n3_a_def_in_an_except_handler_or_a_loop_is_scored():
    source = "try:\n    from m import f\nexcept ImportError:\n    def f(a):\n        if a:\n            pass\n"
    assert scores(".py", source) == {"f": 1}


def test_n17_a_base_that_cannot_be_measured_makes_the_file_unmeasured(change):
    base = "function B() {\n" + "  if (a) {}\n" * 20 + "}\nfunction C() { if (x) {\n"  # unbalanced: the scanner is lost
    head = "function B() {\n" + "  if (a) {}\n" * 20 + "}\n"
    code, crumb = change({"src/a.tsx": base}, {"src/a.tsx": head})
    assert code == 0 and crumb["findings"] == []
    assert crumb["unmeasured"] == ["src/a.tsx: the merge-base version cannot be measured, so old and new debt look alike"]


def test_n16_same_named_functions_keep_their_lines_and_say_so(change, capsys):
    code, crumb = change({"src/a.rs": _impls(30)}, {"src/a.rs": _impls(30, 20)})
    err = capsys.readouterr().err
    assert code == 1 and "src/a.rs:2 new = 20 (new; 2 functions named new, paired by score)" in err
    assert [f["line"] for f in crumb["findings"]] == [2]


# --- R3 duplication and code shape -----------------------------------------------------

COPY = """def load(path):
    text = path.read_text()
    rows = text.splitlines()
    kept = [r for r in rows if r]
    total = len(kept)
    print(total, "rows")
    return kept
"""
RENAMED = COPY.replace("load", "read_all").replace("rows", "lines").replace('"lines"', '"in all"')
def _filler(n: int) -> str:
    """n small functions, each of its own shape."""
    return "".join(f"\n\ndef unique_{i}(x):\n    return x == {tuple(range(i))}\n" for i in range(n))


FILLER = _filler(4)


def test_new_duplication_over_the_limit_fails(change, capsys):
    code, crumb = change({}, {"src/a.py": COPY, "src/b.py": "# a copy\n" + RENAMED})
    err = capsys.readouterr().err
    assert code == 1 and "src/a.py: 100.0% duplicated, 7 of 7 code lines (new); src/a.py:1-7 = src/b.py:2-8" in err
    assert "duplication.max_percent is 3" in err and [d["path"] for d in crumb["duplicated"]] == ["src/a.py", "src/b.py"]


@pytest.mark.parametrize(("base", "head", "dup", "want"), [
    pytest.param(COPY + RENAMED, COPY + RENAMED + FILLER, None, "", id="old-duplication-that-did-not-grow"),
    pytest.param(COPY + RENAMED + FILLER, COPY + RENAMED + FILLER + COPY.replace("load", "x"), None,
                 "src/a.py: 75.9% duplicated, 22 of 29 code lines (was 68.2%)", id="old-duplication-that-grew"),
    pytest.param("", COPY + RENAMED + _filler(250), None, "", id="under-the-limit"),
    # The share rose from 68.2% to 87.5% because other lines left, and the copy is the same 14 lines.
    pytest.param(COPY + RENAMED + FILLER, COPY + RENAMED + _filler(1), None, "",
                 id="old-duplication-in-a-file-that-lost-other-lines"),
])
def test_duplication_is_judged_against_the_base(change, capsys, base, head, dup, want):
    code, _ = change({"src/a.py": base} if base else {}, {"src/a.py": head}, dup=dup)
    err = capsys.readouterr().err
    assert code == (1 if want else 0) and want in err, err


def _shape(kind: str, n: int) -> str:
    """A function of n unique statements, or a chain of n if/elif links."""
    if kind == "statements":
        return "def long(x):\n" + "".join(f"    x = {tuple(range(i))}\n" for i in range(n))
    return "def chain(x):\n" + "".join(f"    {'el' if i else ''}if x == {tuple(range(i))}:\n        pass\n" for i in range(n))


@pytest.mark.parametrize(("kind", "cap", "text"), [
    ("statements", 50, "src/a.py:1 long has 51 statements (new): split it"),
    ("chain", 6, "src/a.py:1 chain has a branch chain of 7 (new): a new branch on a long dispatch chain belongs "
                 "in an extension point"),
])
def test_code_shape_is_judged_against_the_base(change, capsys, kind, cap, text):
    assert change({}, {"src/a.py": _shape(kind, cap)})[0] == 0
    code, crumb = change({}, {"src/a.py": _shape(kind, cap + 1)})
    assert code == 1 and text in capsys.readouterr().err and crumb["findings"][0]["metric"] == {"chain": "branch_chain"}.get(kind, kind)
    code, _ = change({"src/a.py": _shape(kind, cap + 1)}, {"src/a.py": _shape(kind, cap + 1) + FILLER})
    assert code == 0  # old debt that did not grow


@pytest.mark.parametrize(("dup", "shape", "want"), [
    (None, None, 0), ({"unsupported": "fail"}, None, 1), (None, {"unsupported": "fail"}, 1),
], ids=["warn", "duplication-fails", "code-shape-fails"])
def test_a_non_python_file_follows_unsupported(change, capsys, dup, shape, want):
    code, crumb = change({}, {"src/a.ts": "function a() { if (x) { } }"}, dup=dup, shape=shape)
    out = capsys.readouterr()
    assert code == want and crumb["python_only"] == ["src/a.ts"]
    assert "1 changed code file(s) measured for complexity only (duplication and code shape are Python only): src/a.ts" \
        in (out.err if want else out.out)


@pytest.mark.parametrize(("base", "head", "want"), [
    pytest.param({}, {"src/a.py": _shape("statements", 51), "src/b.py": COPY, "src/c.py": RENAMED},
                 {"complexity": ("pass", 0), "statements": ("fail", 51), "branch_chain": ("pass", 0),
                  "duplication": ("fail", 100.0)}, id="new-offenders-fail"),
    pytest.param({"src/a.py": _shape("statements", 51)}, {"src/a.py": _shape("statements", 51) + FILLER},
                 {"complexity": ("pass", 0), "statements": ("warn", 51), "branch_chain": ("pass", 0),
                  "duplication": ("pass", 0.0)}, id="old-debt-warns"),
])
def test_each_measure_has_one_verdict_and_the_exit_follows_it(change, base, head, want):
    code, crumb = change(base, head)  # the tests hide the pinned tools: bugs and types are unsupported (warn)
    got = {m["measure"]: (m["verdict"], m["value"]) for m in crumb["measures"]}
    assert got == {**want, "bugs": ("warn", 0), "types": ("warn", 0)}
    assert code == (1 if any(v == "fail" for v, _ in want.values()) else 0)
    assert {m["measure"]: m["threshold"] for m in crumb["measures"]} == {
        "complexity": 15, "statements": 50, "branch_chain": 6, "duplication": 3, "bugs": 0, "types": 0}


def test_measure_exclude_skips_a_corpus_of_planted_faults_and_names_them(change, monkeypatch, capsys):
    # measure_exclude is the one setting that bypasses the floors, so the gate names what it skipped.
    code, crumb = change({}, {"tests/fixtures/golden/bad/a.py": _branching(16) + COPY + RENAMED},
                         layer={"measure_exclude": ["tests/fixtures/golden/"]})
    assert code == 0 and crumb["files"] == 0
    assert crumb["excluded"] == ["tests/fixtures/golden/bad/a.py"]
    assert "1 changed file(s) under measure_exclude not measured: tests/fixtures/golden/bad/a.py" in capsys.readouterr().out


@pytest.mark.parametrize(("arg", "key", "measure", "default"), [
    ("cfg", "max", "complexity", 15), ("shape", "max_function_statements", "statements", 50),
    ("shape", "max_branch_chain", "branch_chain", 6), ("dup", "max_percent", "duplication", 3),
])
def test_a_config_value_looser_than_the_default_is_ignored(change, arg, key, measure, default):
    code, crumb = change({}, {"src/a.py": "def f():\n    pass\n"}, **{arg: {key: default * 10}})
    [record] = [m for m in crumb["measures"] if m["measure"] == measure]
    assert code == 0 and record["threshold"] == default
    assert record["threshold_source"] == f"default (project value {default * 10} ignored)"
    code, crumb = change({}, {"src/a.py": "def f():\n    pass\n"}, **{arg: {key: 1}})
    assert [(m["threshold"], m["threshold_source"]) for m in crumb["measures"] if m["measure"] == measure] == [(1, "project")]
