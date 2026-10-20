#!/usr/bin/env python3
"""R2 test shape: the DRY lists of the changed test files, computed by a script (ADR-0008).

- helpers_reimplemented: a function of a changed test file whose name, or whose body
  normalized as duplication.py does (so a renamed copy counts), is a function of a conftest file.
- helpers_reused: each conftest name that a changed test file imports.
- thin_files: a changed test file with one or two `def test_` functions that imports a module
  of a runtime directory that another test file also imports.

The layout of the target repo (ADR-0014) is `test_shape.layout` of the resolved config, else detected:
- test_dirs: the top-level `tests/` and `test/`;
- conftest: each conftest.py under the test dirs, outside `measure_exclude`;
- runtime_dirs: the package dirs that pyproject.toml (`[tool.setuptools] packages`, `[project] name`) or
  setup.cfg name, else each directory outside the test dirs that holds a .py file.
A measure whose part of the layout is not found is unsupported (`test_shape.unsupported`), never an empty pass.

The R2 gate fails when a list is longer than its `test_shape` limit in the resolved config.
CLI: python3 scripts/test_shape.py --change prints the records of the run's change.
python3 scripts/test_shape.py DIR judges every file under the test dirs of DIR as changed.
"""

from __future__ import annotations

import ast
import configparser
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import duplication as dup  # noqa: E402
from cognitive_complexity import SECTIONS, load_config, load_exclude  # noqa: E402
from gate_common import ctx, measure_record  # noqa: E402

DEFAULTS = SECTIONS["test_shape"]
LAYOUT_KEYS = ("test_dirs", "conftest", "runtime_dirs")
TEST_DIR_NAMES = ("tests", "test")
SKIP_DIRS = ("__pycache__", "node_modules", "venv", "build", "dist", "site-packages", *TEST_DIR_NAMES)
NOT_FOUND = {"test_dirs": "no test directory", "conftest": "no conftest.py under the test directories",
             "runtime_dirs": "no runtime directory"}
NEEDS = {"helpers_reimplemented": ("test_dirs", "conftest"), "thin_files": ("test_dirs", "runtime_dirs")}
MIN_BODY = 2  # a body of one line (`return ROOT`) is too common to call a copy
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _body(node: ast.FunctionDef | ast.AsyncFunctionDef, units: list[dup.Unit]) -> tuple[str, ...]:
    body = node.body
    if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]  # the docstring
    end = node.end_lineno
    if end is None:  # ast.parse sets it; without it the body cannot be found (fail closed)
        raise SyntaxError(f"{node.name}: no end line")
    start = body[0].lineno if body else end + 1
    return tuple(u.text for u in units if start <= u.first <= end)


def _functions(source: str) -> dict[str, tuple[str, ...]]:
    """Top-level functions and their normalized bodies. Raises SyntaxError."""
    tree, units = ast.parse(source), dup.units(source)
    return {node.name: _body(node, units) for node in tree.body if isinstance(node, _DEFS)}


def _imports(tree: ast.AST) -> set[str]:
    """The dotted names that a file imports: `import a.b` gives a.b, `from a import b` gives a and a.b."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out |= {node.module, *(f"{node.module}.{alias.name}" for alias in node.names)}
    return out


def _module_file(root: Path, runtime_dirs: list[str], module: str) -> str | None:
    """The file of a dotted module in a runtime dir: flat (scripts/mod.py) or under its package (src/pkg/mod.py)."""
    parts = module.split(".")
    for where in runtime_dirs:
        tails = [parts, parts[1:]] if parts[0] == Path(where).name and len(parts) > 1 else [parts]
        found = next((rel for rel in (f"{where}/{'/'.join(tail)}.py" for tail in tails) if (root / rel).is_file()), None)
        if found:
            return found
    return None


def _holds_python(path: Path) -> bool:
    return path.is_dir() and any(p.suffix == ".py" and p.is_file() for p in path.iterdir())


def _dist(name: object) -> list[str]:
    return [name.lower().replace("-", "_").replace(".", "_")] if isinstance(name, str) and name else []


def _pyproject(root: Path) -> tuple[list[str], list[str]]:
    """(package names, `find` source dirs) of pyproject.toml."""
    import tomllib

    path = root / "pyproject.toml"
    if not path.is_file():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"pyproject.toml does not parse, so test_shape.layout cannot be detected: {exc}") from exc
    packages = data.get("tool", {}).get("setuptools", {}).get("packages", [])
    wheres = packages.get("find", {}).get("where", ["."]) if isinstance(packages, dict) else []
    names = [p for p in packages if isinstance(p, str)] if isinstance(packages, list) else []
    return [*names, *_dist(data.get("project", {}).get("name"))], wheres


def _setup_cfg(root: Path) -> tuple[list[str], list[str]]:
    """(package names, `find:` source dirs) of setup.cfg."""
    path, cfg = root / "setup.cfg", configparser.ConfigParser(interpolation=None)
    if not path.is_file():
        return [], []
    try:
        cfg.read_string(path.read_text(encoding="utf-8"))
    except configparser.Error as exc:
        raise ValueError(f"setup.cfg does not parse, so test_shape.layout cannot be detected: {exc}") from exc
    packages = cfg.get("options", "packages", fallback="").split()
    find = packages[:1] in (["find:"], ["find_namespace:"])
    wheres = cfg.get("options.packages.find", "where", fallback=".").split() if find else []
    return [p for p in packages if not p.endswith(":")] + _dist(cfg.get("metadata", "name", fallback="")), wheres


def _packages(root: Path) -> list[str]:
    """The package dirs that pyproject.toml or setup.cfg name and that hold a .py file."""
    (names, wheres), (more, more_wheres) = _pyproject(root), _setup_cfg(root)
    wheres += more_wheres
    found = {Path(base, name.replace(".", "/")).as_posix() for base in ("", "src", *wheres) for name in names + more}
    found |= {p.parent.relative_to(root).as_posix() for where in wheres for p in (root / where).glob("*/__init__.py")}
    return sorted(rel for rel in found if _holds_python(root / rel))


def _python_dirs(root: Path, skip: tuple[str, ...]) -> list[str]:
    """Each directory that holds a .py file, outside `skip`, the test dirs, hidden dirs and tool dirs."""
    out = []
    for here, dirs, files in os.walk(root):
        rel = Path(here).relative_to(root).as_posix()
        dirs[:] = [d for d in dirs if not (d.startswith(".") or d in SKIP_DIRS
                                           or (f"{rel}/{d}/".removeprefix("./")).startswith(skip))]
        if rel != "." and any(name.endswith(".py") for name in files):
            out.append(rel)
    return sorted(out)


def _given(root: Path, raw: object) -> dict[str, list[str]]:
    """`test_shape.layout` checked: a map of lists of paths that the repo has (fail closed)."""
    if not isinstance(raw, dict) or set(raw) - set(LAYOUT_KEYS):
        raise ValueError(f"test_shape.layout must be a map with keys from {list(LAYOUT_KEYS)}, got {raw!r}")
    for key, paths in raw.items():
        if not (isinstance(paths, list) and all(isinstance(p, str) and p for p in paths)):
            raise ValueError(f"test_shape.layout.{key} must be a list of paths, got {paths!r}")
        missing = [p for p in paths if not ((root / p).is_file() if key == "conftest" else (root / p).is_dir())]
        if missing:
            raise ValueError(f"test_shape.layout.{key} names paths that the repo does not have: {missing}")
    return {key: [p.rstrip("/") for p in paths] for key, paths in raw.items()}


def layout(root: Path, raw: object, exclude: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """The test dirs, conftest files and runtime dirs of the repo at root: test_shape.layout, else detected."""
    given = _given(root, raw)
    tests = given["test_dirs"] if "test_dirs" in given else [d for d in TEST_DIR_NAMES if (root / d).is_dir()]
    conftest = given["conftest"] if "conftest" in given else sorted(
        rel for d in tests for rel in (p.relative_to(root).as_posix() for p in (root / d).rglob("conftest.py"))
        if not rel.startswith(exclude))
    runtime = given["runtime_dirs"] if "runtime_dirs" in given else (
        _packages(root) or _python_dirs(root, tuple(f"{d.rstrip('/')}/" for d in (*tests, *exclude))))
    return {"test_dirs": tests, "conftest": conftest, "runtime_dirs": runtime}


def _copies(rel: str, source: str, conftest: dict[str, tuple[str, tuple[str, ...]]]) -> list[str]:
    out = []
    for name, body in _functions(source).items():
        same = name if name in conftest else next(
            (c for c, (_where, theirs) in conftest.items() if len(body) >= MIN_BODY and theirs == body), None)
        if same and not name.startswith("test_"):
            out.append(f"{rel}: {name} ({conftest[same][0]}: {same})")
    return out


class _Tests:
    """The imports of every test file under the test dirs, read once and only when a thin file needs them."""

    def __init__(self, root: Path, test_dirs: list[str], exclude: tuple[str, ...]) -> None:
        self.root, self.test_dirs, self.exclude = root, test_dirs, exclude
        self._imports: dict[str, set[str]] | None = None

    def importers(self, module: str, but: str) -> list[str]:
        if self._imports is None:
            paths = sorted({p.relative_to(self.root).as_posix() for d in self.test_dirs
                            for p in (self.root / d).rglob("test_*.py")})
            self._imports = {rel: _imports(ast.parse((self.root / rel).read_text(encoding="utf-8", errors="replace")))
                             for rel in paths if not rel.startswith(self.exclude)}
        return [rel for rel, names in self._imports.items() if rel != but and module in names]


def _thin(root: Path, rel: str, tree: ast.AST, tests: _Tests, runtime_dirs: list[str]) -> list[str]:
    count = sum(isinstance(node, _DEFS) and node.name.startswith("test_") for node in ast.walk(tree))
    if not 1 <= count <= 2:
        return []
    for module in sorted(_imports(tree)):
        where = _module_file(root, runtime_dirs, module)
        others = tests.importers(module, rel) if where else []
        if others:
            return [f"{rel} ({count} tests of {where}, also tested by {others[0]})"]
    return []


def _record(measure: str, offenders: list[str], cfg: dict, found: dict[str, list[str]]) -> dict:
    key = f"max_{measure}"
    source = cfg.get("threshold_source", {}).get(key, "default")
    unmeasured = [f"{NOT_FOUND[part]}: set test_shape.layout.{part}" for part in NEEDS[measure] if not found[part]]
    return measure_record(measure, len(offenders), cfg[key], offenders, failed=len(offenders) > cfg[key],
                          threshold_source=source, unmeasured=unmeasured, unsupported=cfg["unsupported"])


def measure(root: Path, changed: list[str], cfg: dict, exclude: tuple[str, ...] = ()) -> dict:
    """The records of the changed test files under `root`, the conftest names they import, and the layout.

    Raises SyntaxError when a changed test file or a conftest file does not parse, ValueError on a bad layout.
    """
    found = layout(root, cfg.get("layout", {}), exclude)
    conftest: dict[str, tuple[str, tuple[str, ...]]] = {}
    for where in reversed(found["conftest"]):  # the first conftest file that defines a name wins
        source = (root / where).read_text(encoding="utf-8")
        conftest |= {name: (where, body) for name, body in _functions(source).items()}
    tests, prefixes = _Tests(root, found["test_dirs"], exclude), tuple(f"{d}/" for d in found["test_dirs"])
    copies: list[str] = []
    reused: list[str] = []
    thin: list[str] = []
    for rel in changed:
        path = root / rel
        if not (rel.startswith(prefixes) and rel.endswith(".py")) or path.name == "conftest.py" or not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        copies += _copies(rel, source, conftest)
        reused += sorted({f"{rel}: {a.name}" for node in ast.walk(tree)
                          if isinstance(node, ast.ImportFrom) and node.module == "conftest" for a in node.names})
        thin += _thin(root, rel, tree, tests, found["runtime_dirs"])
    origin = {key: "config" if cfg.get("layout", {}).get(key) is not None else "detected" for key in LAYOUT_KEYS}
    return {"measures": [_record("helpers_reimplemented", copies, cfg, found), _record("thin_files", thin, cfg, found)],
            "helpers_reused": reused, "layout": {**found, "source": origin}}


def for_change(context_dir: Path) -> dict | str:
    """The records of the run's change (the base that R3 uses), or why it cannot be measured."""
    import cognitive_complexity_gate as r3

    change = r3.change_under_review(context_dir)
    if isinstance(change, str):
        return change
    worktree, base, _fork, files, _excluded = change
    try:
        return {"base": base, **measure(worktree, files, load_config(_SCRIPTS.parent, "test_shape", worktree),
                                        load_exclude(_SCRIPTS.parent, worktree))}
    except SyntaxError as exc:
        return f"cannot parse a changed test file: {exc}"
    except ValueError as exc:
        return f"bad config or layout: {exc}"


def measure_path(root: Path) -> dict:
    """Every file under the test dirs of root judged as changed: for a corpus, not a diff."""
    cfg = load_config(_SCRIPTS.parent, "test_shape", root)
    files = sorted({p.relative_to(root).as_posix() for d in layout(root, cfg.get("layout", {}))["test_dirs"]
                    for p in (root / d).rglob("*.py")})
    return measure(root, files, cfg)


def main(argv: list[str] | None = None) -> int:
    [arg] = sys.argv[1:] if argv is None else argv
    result = for_change(ctx()) if arg == "--change" else measure_path(Path(arg))
    if isinstance(result, str):
        print(f"test shape cannot be measured: {result}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 1 if any(m["verdict"] == "fail" for m in result["measures"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
