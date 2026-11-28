"""WR-139: a seeded fuzz of how notebook cells' magics are read (magics.py, and the Fabric and
Databricks exports in notebook_formats.py). Cells are built from pieces whose meaning is known (a
line magic, a ``!`` line, plain Python, a triple-quoted string or brackets or a backslash
continuation holding lines that only look like magics, an ``if`` block with an indented magic), then
written with LF or CRLF, and checked:

- ``find`` reports exactly the magic lines, and ``rewrite`` and ``blank`` keep every other line byte
  for byte and the line count, and give Python that parses;
- ``cell`` reads each cell magic after blank lines and indentation, its header and body put back
  together are the cell as written (Python wrappers keep their line numbers instead), and a header
  with more letters or digits after it (``%%sqlx``, ``%%sql2``) is not that magic;
- ``fabric_cell`` and the exports read a CRLF cell as they read the same cell with LF;
- ``words`` reads back the arguments of a line that quotes them.

random.Random with fixed seeds, so a failure names a case that runs again; the smallest failing cell
(pieces dropped one at a time) is printed with it.
"""

from __future__ import annotations

import ast
import random
import re
from collections.abc import Callable

import pytest

from fabric_desktop import magics, notebook_formats

CASES = 400
SEEDS = range(3)

# (lines, which of them are magics). Every piece is valid Python once its magic lines are rewritten.
MAGIC = [
    ["%time f()"], ["%time  x = f()"], ['%run "path with spaces" $day="2026-01-01"'], ['%run Load sales {"a": "b c"}'],
    ['%pip install "pkg>=1,<2" -q'], ["%pip install -r builtin/requirements.txt"], ["!echo hi"], ["x = !ls -la 'a b'"],
    ["df = %sql SELECT 'a % b' AS c"], ["a, b = %foo bar=1"], ["%env A=1"], ["%matplotlib inline"], ["%who"],
    ["%fs put Files/a.txt \"say \\\"hi\\\"\""], ["%lsmagic"], ["%nope unknown"], ["%time"], ["%sql SELECT 1 -- é 日本"],
]
PLAIN = [
    ["x = 1"], ["print('a # b')"], ["y = 'it\\'s'"], ['z = "%time no"'], ["w = 5 % 3"], ["v = 1 != 2"], ["s = '!ls'"],
    ["u = 'é 日本 🚀'"], ["# %time in a comment"], ["# C:\\temp\\"], ["t = r'C:\\x' + '\\\\'"], ["q = \"a'b\" + 'c\"d'"],
    ["p = f'{x!r}'"], ["o = ''"], ["n = '''one line'''"],
]
HIDDEN = [  # lines that look like magics but aren't statements
    ['s = """', "%time f()", "!rm -rf x", '"""'], ["s = '''", "  %pip install x", "'''"], ["t = (5", "% 3)"],
    ["t = [5,", "  5", "!= 2]"], ["t = 5 \\", "% 3"], ["t = {'a': 5", "% 3}"], ["t = ('a#', 5 \\", "% 3)"],
    ["d = '''x''' + '''", "%time", "'''"], ['e = """a\\"""" + """', "!x", '"""'], ["g = 'abc\\", "%time f()'"],
]


def _piece(r: random.Random) -> tuple[list[str], set[int]]:
    roll = r.random()
    if roll < 0.35:
        return list(r.choice(MAGIC)), {0}
    if roll < 0.6:
        return list(r.choice(PLAIN)), set()
    if roll < 0.8:
        return list(r.choice(HIDDEN)), set()
    statements = [p for p in PLAIN if not p[0].startswith("#")]  # a block needs a statement, not only a comment
    body, magic = (list(r.choice(MAGIC)), {1}) if r.random() < 0.6 else (list(r.choice(statements)), set())
    indent = r.choice(["    ", "\t", "  "])
    head = r.choice(["if True:", "for _ in range(1):", "def g():", "with open(__file__):"])
    return [head] + [indent + ln for ln in body], magic


def _cell(pieces: list[tuple[list[str], set[int]]]) -> tuple[list[str], set[int]]:
    lines: list[str] = []
    expected: set[int] = set()
    for body, magic in pieces:
        expected |= {len(lines) + i + 1 for i in magic}
        lines += body
    return lines, expected


def _line_magics(pieces: list[tuple[list[str], set[int]]], crlf: bool) -> str | None:
    lines, expected = _cell(pieces)
    newline = "\r\n" if crlf else "\n"
    code = newline.join(lines)
    found = [m.line for m in magics.find(code)]
    if found != sorted(expected):
        return f"find: {found} != {sorted(expected)}"
    for name, made in (("rewrite", magics.rewrite(code)), ("blank", magics.blank(code))):
        out = made.split("\n")
        if len(out) != len(lines):
            return f"{name} changed the line count"
        kept = [i for i in range(len(lines)) if i + 1 not in expected]
        if [out[i] for i in kept] != [code.split("\n")[i] for i in kept]:
            return f"{name} changed a line that isn't a magic"
        try:
            ast.parse(made)
        except SyntaxError as exc:
            return f"{name}: {exc}"
    return None


HEADERS = sorted(set(magics.FABRIC_HEADERS) | set(magics.SHELL_HEADERS) | magics.PYTHON_WRAPPERS
                 | set(magics.OTHER_HEADERS) | set(magics.DATABRICKS_HEADERS))
BODIES = ["", "SELECT 1", "x = 1\nprint(x)", "# hi\n\nbody é", '{"driverMemory": "28g"}', "  indented", "a\tb"]


def _header_cell(r: random.Random) -> tuple[str, str, str, str]:
    header = r.choice(HEADERS)
    args = r.choice(["", "", " out", " -a helper.py"]) if header in magics.PYTHON_WRAPPERS | set(magics.OTHER_HEADERS) else ""
    lead = r.choice(["", "\n", "\n\n", "  ", "\t", "\n  "])
    body = r.choice(BODIES)
    nl = r.choice(["\n", "\r\n"])
    source = lead + header + args + (nl + body.replace("\n", nl) if body or r.random() < 0.5 else "")
    return source, header, args, nl


def _cell_magic(r: random.Random) -> str | None:
    source, header, args, nl = _header_cell(r)
    parsed = magics.cell(source)
    if parsed.header != header:
        return f"cell({source!r}).header == {parsed.header!r}"
    if parsed.language == "python":
        if parsed.body.count("\n") != source.count("\n"):
            return f"cell({source!r}) moved the lines of its Python"
        rest = source.lstrip().split("\n")[1:]  # the lines after the header, as written
        if rest and parsed.body.split("\n")[-len(rest):] != rest:
            return f"cell({source!r}) changed its Python"
    elif header not in magics.OTHER_HEADERS and not (header == "%sh" and parsed.args):
        if header + parsed.body != source.lstrip():
            return f"cell({source!r}): {header!r} + {parsed.body!r} isn't the cell"
    longer = source.replace(header, header + r.choice(["x", "2", "_", "X"]), 1)
    if magics.cell(longer).header:
        return f"cell({longer!r}) read {magics.cell(longer).header!r}"
    exported = notebook_formats._magic(notebook_formats._magic_first(longer))
    if exported is not None and f"%%{exported}" != longer.lstrip().split(None, 1)[0].lower():
        return f"the exports read {longer!r} as %%{exported}"
    if nl == "\r\n":
        lf = source.replace("\r\n", "\n")
        a, b = magics.fabric_cell({"type": "code", "source": source}), magics.fabric_cell({"type": "code", "source": lf})
        if {**a, "source": a["source"].replace("\r\n", "\n")} != b:
            return f"fabric_cell({source!r}) == {a!r}, but {b!r} with LF"
    return None


ALPHABET = ["a", "b", "1", "=", ",", "<", ">", " ", "\t", "'", '"', "\\", "é", "日", "-", ".", ":", "$", "{", "}"]


def _quote(arg: str) -> str:
    if arg and not any(c.isspace() or c in "'\"" for c in arg):
        return arg
    if "'" not in arg:
        return f"'{arg}'"
    return '"' + arg.replace('"', '\\"') + '"'


def _words(r: random.Random) -> str | None:
    args = ["".join(r.choice(ALPHABET) for _ in range(r.randint(0, 8))) for _ in range(r.randint(1, 4))]
    # An argument with ' is written in double quotes, where \" then a space can also end it ("C:\My Folder\"),
    # so one with both can read two ways (test_notebook_magics_wr139.py says which words takes).
    args = [a for a in args if not ("'" in a and re.search(r'"\s', a))]
    line = " ".join(_quote(a) for a in args)
    try:
        got = magics.words(line)
    except ValueError as exc:
        return f"words({line!r}): {exc}"
    return None if got == args else f"words({line!r}) == {got!r}, not {args!r}"


def _shrink(pieces: list[tuple[list[str], set[int]]], fails: Callable[[list], bool]) -> list:
    changed = True
    while changed:
        changed = False
        for i in range(len(pieces)):
            smaller = pieces[:i] + pieces[i + 1:]
            if smaller and fails(smaller):
                pieces, changed = smaller, True
                break
    return pieces


@pytest.mark.parametrize("seed", SEEDS)
def test_line_magics_are_found_where_python_starts_a_statement(seed):
    r = random.Random(seed)
    for case in range(CASES):
        pieces = [_piece(r) for _ in range(r.randint(1, 6))]
        crlf = r.random() < 0.3
        if problem := _line_magics(pieces, crlf):
            small = _shrink(pieces, lambda p, crlf=crlf: _line_magics(p, crlf) is not None)
            code = ("\r\n" if crlf else "\n").join(_cell(small)[0])
            pytest.fail(f"seed {seed} case {case}: {problem}\nsmallest failing cell: {code!r}: {_line_magics(small, crlf)}")


@pytest.mark.parametrize("seed", SEEDS)
def test_cell_magics_are_read_from_the_first_line_and_put_back_as_written(seed):
    r = random.Random(seed)
    for case in range(CASES):
        if problem := _cell_magic(r):
            pytest.fail(f"seed {seed} case {case}: {problem}")


@pytest.mark.parametrize("seed", SEEDS)
def test_quoted_magic_arguments_read_back(seed):
    r = random.Random(seed)
    for case in range(CASES):
        if problem := _words(r):
            pytest.fail(f"seed {seed} case {case}: {problem}")
