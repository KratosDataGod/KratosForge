"""Seeded property tests: breakpoints land where they should (story S3).

Every case is made by ``random.Random(seed)`` from a fixed list of seeds, and every failure message
names its seed, so the generator (``cell_code(seed)``, ``notebook(seed)``) replays it. Nothing here
needs a package beyond the test suite's.

1. check_breakpoints (what the app sends to start a cell) and breakpoints_meta (what a saved cell
   keeps) on any input: sorted, unique, positive whole numbers within the cap; idempotent; a bad
   list to check_breakpoints is a ValueError and nothing else.
2. Save and load: the breakpoints of random cells come back from .ipynb and Fabric source (as
   breakpoints_meta normalises them), on the same line of text; Databricks source names them as
   dropped. A cell starting with blank lines in Fabric source is a known bug (xfail, at the end).
3. Placement: in random cell code (assignments, if/else, for, def, blank lines, comments), a
   breakpoint on a line with code is placed on that line, one on a line without code on the next
   line that has some, and one with no code after it is unplaced (Debugger.add). A few cells then
   run under the kernel: it pauses on exactly the line events of the placed lines, in order.

Edits in the browser (debugging.js dbgEdited) are covered by app/tests/ui.test.cjs.
"""

from __future__ import annotations

import ast
import json
import random
import sys
import time
from typing import Any

import pytest

from fabric_desktop import notebook_formats as nbf
from fabric_desktop.debugger import MAX_BREAKPOINTS, Debugger, check_breakpoints
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel, breakpoints_meta, from_ipynb, to_ipynb

SEEDS = list(range(3300, 3500))  # 200 cases for the pure properties
KERNEL_SEEDS = SEEDS[:60]  # cells run under the in-engine kernel
PROCESS_SEEDS = SEEDS[:10]  # and under the engine in its own process
CAP = 1_000_000  # breakpoints_meta's largest line number


# ---------- 1. normalising breakpoint lists ----------

def _junk(rng: random.Random) -> Any:
    return rng.choice([
        lambda: rng.randint(-5, 40),
        lambda: rng.randint(1, 3),  # duplicates
        lambda: rng.choice([0, -1, CAP, CAP + 1, 2**63, -(2**63)]),
        lambda: rng.choice([True, False]),
        lambda: rng.uniform(-3, 30),
        lambda: rng.choice([1.0, 2.0, float("nan"), float("inf")]),
        lambda: str(rng.randint(1, 9)),
        lambda: None,
        lambda: [rng.randint(1, 5)],
        lambda: {"line": rng.randint(1, 5)},
    ])()


def _value(rng: random.Random) -> Any:
    """A breakpoints value as it could arrive: a list (short, long, clean or not) or something else."""
    shape = rng.choice(["clean", "clean", "junk", "huge", "huge_clean", "other"])
    if shape == "clean":
        return [rng.randint(1, 60) for _ in range(rng.randint(0, 12))]
    if shape == "junk":
        return [_junk(rng) for _ in range(rng.randint(0, 20))]
    if shape == "huge":
        return [_junk(rng) for _ in range(rng.randint(MAX_BREAKPOINTS - 2, 3 * MAX_BREAKPOINTS))]
    if shape == "huge_clean":
        return [rng.randint(1, 10 * MAX_BREAKPOINTS) for _ in range(rng.randint(MAX_BREAKPOINTS - 2, 3 * MAX_BREAKPOINTS))]
    return rng.choice([None, 3, "1,2", {"a": 1}, (1, 2), 2.5, True, b"\x01"])


def _valid(n: Any) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n >= 1


def _assert_normal(out: Any, seed: int, cap: int | None) -> None:
    assert isinstance(out, list), f"seed {seed}: {out!r}"
    assert all(type(n) is int and n >= 1 for n in out), f"seed {seed}: {out!r}"
    assert out == sorted(set(out)), f"seed {seed}: not sorted and unique: {out!r}"
    assert len(out) <= MAX_BREAKPOINTS, f"seed {seed}: {len(out)} breakpoints"
    if cap is not None:
        assert all(n <= cap for n in out), f"seed {seed}: {out!r}"


@pytest.mark.parametrize("seed", SEEDS)
def test_breakpoints_meta_keeps_only_line_numbers_and_is_idempotent(seed):
    value = _value(random.Random(seed))
    out = breakpoints_meta(value)
    _assert_normal(out, seed, CAP)
    kept = sorted({n for n in value if _valid(n) and n <= CAP}) if isinstance(value, list) else []
    assert out == kept[:MAX_BREAKPOINTS], f"seed {seed}: the smallest {MAX_BREAKPOINTS} valid lines, got {out!r}"
    assert breakpoints_meta(out) == out, f"seed {seed}"
    # What a saved cell keeps is always something a run accepts as it is.
    assert check_breakpoints(out) == out, f"seed {seed}"


@pytest.mark.parametrize("seed", SEEDS)
def test_check_breakpoints_accepts_line_numbers_and_refuses_anything_else_with_a_value_error(seed):
    value = _value(random.Random(seed))
    ok = value is None or (isinstance(value, list) and len(value) <= MAX_BREAKPOINTS and all(_valid(n) for n in value))
    try:
        out = check_breakpoints(value)
    except ValueError:
        assert not ok, f"seed {seed}: refused {value!r}"
        return
    assert ok, f"seed {seed}: accepted {value!r} as {out!r}"
    _assert_normal(out, seed, None)
    assert out == sorted(set(value or [])), f"seed {seed}"
    assert check_breakpoints(out) == out, f"seed {seed}"


# ---------- random cell code, with what each line is ----------

class Cell:
    """Generated code: its lines, and for each whether it has code (a line event can happen there)."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.code: list[bool] = []
        self.names = 0

    def add(self, text: str, code: bool) -> None:
        self.lines.append(text)
        self.code.append(code)

    @property
    def source(self) -> str:
        return "\n".join(self.lines)


COMMENTS = ["# a note", "# CELL ********************", "# COMMAND ----------", "# %%", "# MAGIC %md", "#", "# é 東京"]


def _filler(rng: random.Random, cell: Cell, pad: str) -> None:
    """Lines without code: blank, spaces only, or a comment at any indent."""
    for _ in range(rng.choice([0, 0, 0, 1, 1, 2])):
        cell.add(rng.choice(["", "", "   ", pad + rng.choice(COMMENTS), rng.choice(COMMENTS)]), False)


def _expr(rng: random.Random, names: list[str]) -> str:
    v = rng.choice(names)
    return rng.choice([f"{v} + {rng.randint(0, 9)}", f"{v} * 2", f"len(str({v}))", f"{v} % 3",
                       f"sum([i * 2 for i in range({rng.randint(0, 3)})]) + {v}"])


def _block(rng: random.Random, cell: Cell, pad: str, names: list[str], depth: int, top: bool) -> None:
    """At least one statement with code, at indent ``pad``."""
    for _ in range(rng.randint(1, 4)):
        _filler(rng, cell, pad)
        kinds = ["assign", "assign", "aug"]
        if depth < 2:
            kinds += ["if", "for"]
        if top:
            kinds += ["def"]
        kind = rng.choice(kinds)
        if kind == "assign":
            cell.names += 1
            name = f"v{cell.names}"
            cell.add(f"{pad}{name} = {_expr(rng, names)}" + rng.choice(["", "", "  # why"]), True)
            names = names + [name]
        elif kind == "aug":
            cell.add(f"{pad}{names[0]} += {_expr(rng, names)}", True)  # total, or a in a function
        elif kind == "if":
            cell.add(f"{pad}if {rng.choice(names)} % {rng.randint(2, 3)} == {rng.randint(0, 1)}:", True)
            _block(rng, cell, pad + "    ", names, depth + 1, False)
            if rng.random() < 0.5:
                _filler(rng, cell, pad)
                cell.add(f"{pad}else:", False)  # no code: a breakpoint here goes to the next line
                _block(rng, cell, pad + "    ", names, depth + 1, False)
        elif kind == "for":
            cell.names += 1
            name = f"i{cell.names}"
            cell.add(f"{pad}for {name} in range({rng.randint(0, 3)}):", True)
            _block(rng, cell, pad + "    ", names + [name], depth + 1, False)
        else:
            cell.names += 1
            fn = f"f{cell.names}"
            cell.add(f"def {fn}(a):", True)
            _block(rng, cell, "    ", ["a"], 1, False)
            _filler(rng, cell, "    ")
            cell.add("    return a + 1", True)
            if rng.random() < 0.7:  # sometimes defined and never called: its breakpoints never pause
                _filler(rng, cell, "")
                cell.add(f"total += {fn}(total)", True)


def cell_code(seed: int) -> tuple[Cell, list[int]]:
    """Random cell code and random breakpoints on it (some past its end)."""
    rng = random.Random(seed)
    cell = Cell()
    _filler(rng, cell, "")
    cell.add("total = 0", True)
    _block(rng, cell, "", ["total"], 0, True)
    _filler(rng, cell, "")
    if rng.random() < 0.3:
        cell.add("", False)  # trailing blank lines: no code after them
        cell.add("# the end", False)
    n = len(cell.lines)
    lines = sorted({rng.randint(1, n + 2) for _ in range(rng.randint(1, min(8, n + 2)))})
    return cell, lines


def expected_placement(cell: Cell, lines: list[int]) -> tuple[set[int], list[int]]:
    """Where debugger.py promises each breakpoint goes: its own line when that has code, else the
    next line with code; none after it, unplaced."""
    placed, unplaced = set(), []
    for n in lines:
        at = next((m for m in range(n, len(cell.lines) + 1) if cell.code[m - 1]), None)
        if at is None:
            unplaced.append(n)
        else:
            placed.add(at)
    return placed, unplaced


def _describe(cell: Cell, lines: list[int], seed: int) -> str:
    numbered = "\n".join(f"{i:3} {'C' if c else ' '} {t}" for i, (t, c) in enumerate(zip(cell.lines, cell.code, strict=True), 1))
    return f"seed {seed}, breakpoints {lines}:\n{numbered}"


def test_the_generator_is_deterministic_and_makes_every_kind_of_line():
    made = [cell_code(s) for s in SEEDS]
    again = [cell_code(s) for s in SEEDS]
    assert [(c.source, b) for c, b in made] == [(c.source, b) for c, b in again]
    joined = "\n".join(c.source for c, _ in made)
    for needle in ("\nelse:", "\n    else:", "for i", "def f", "    return a + 1", "\n\n", "\n   \n", "# CELL", "if ", "+= f"):
        assert needle in joined, needle
    assert any(not c.code[0] for c, _ in made), "a cell that starts without code"
    assert any(b[-1] > len(c.lines) for c, b in made), "a breakpoint past the end"
    for c, _ in made:
        ast.parse(c.source)


# ---------- 3. placement, without running ----------

def _compiled(source: str) -> Any:
    """The cell as Kernel._exec_python compiles it (no last expression: the generator never ends on one)."""
    tree = ast.parse(source, "<cell>")
    assert not isinstance(tree.body[-1], ast.Expr)
    return compile(tree, "<cell>", "exec")


@pytest.mark.parametrize("seed", SEEDS)
def test_a_breakpoint_is_placed_on_its_line_or_the_next_line_with_code(seed):
    cell, lines = cell_code(seed)
    d = Debugger(check_breakpoints(lines), False, lambda name, value: {})
    d.add(_compiled(cell.source))
    placed, unplaced = expected_placement(cell, lines)
    assert (d.lines, sorted(d.unplaced)) == (placed, unplaced), _describe(cell, lines, seed)
    assert d.asked == lines, _describe(cell, lines, seed)


# ---------- 3. placement, running under the kernel ----------

def oracle(source: str) -> tuple[list[int], int]:
    """The cell's line events in the order they happen, and its total, from a plain run."""
    events: list[int] = []
    code = _compiled(source)

    def trace(frame, event, arg):
        if frame.f_code.co_filename != "<cell>":
            return None
        if event == "line":
            events.append(frame.f_lineno)
        return trace

    ns: dict[str, Any] = {}
    sys.settrace(trace)
    try:
        exec(code, ns)  # noqa: S102 - generated test code
    finally:
        sys.settrace(None)
    return events, ns["total"]


def _until_paused(k: Any, timeout: float = 20) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def run_pauses(k: Any, cell: Cell, lines: list[int], seed: int) -> None:
    placed, unplaced = expected_placement(cell, lines)
    events, total = oracle(cell.source)
    want = [n for n in events if n in placed]
    k.start(cell.source, breakpoints=lines)
    got = []
    while True:
        snap = _until_paused(k)
        p = snap.get("paused")
        if not p:
            break
        assert p["reason"] == "breakpoint", f"{_describe(cell, lines, seed)}\n{p}"
        got.append(p["line"])
        assert len(got) <= len(want), f"paused at {got}, expected {want}\n{_describe(cell, lines, seed)}"
        k.debug("continue")
    assert snap["status"] == "ok", f"{_describe(cell, lines, seed)}\n{snap}"
    assert got == want, f"paused at {got}, expected {want}\n{_describe(cell, lines, seed)}"
    said = "".join(o.get("text", "") for o in snap["outputs"] if o.get("type") == "text")
    for n in lines:
        assert (f"The breakpoint on line {n} can't pause" in said) == (n in unplaced), f"line {n}: {said!r}\n{_describe(cell, lines, seed)}"
    # Pausing changes nothing the cell computes.
    k.start("total")
    snap = _until_paused(k)
    assert snap["outputs"][-1]["text"] == str(total), f"{_describe(cell, lines, seed)}\n{snap}"


@pytest.fixture(scope="module")
def engine():
    return Kernel()


@pytest.mark.parametrize("seed", KERNEL_SEEDS)
def test_the_kernel_pauses_on_each_placed_line_as_it_runs(engine, seed):
    cell, lines = cell_code(seed)
    run_pauses(engine, cell, lines, seed)


@pytest.fixture(scope="module")
def process():
    k = ProcessKernel()
    yield k
    k.close()


@pytest.mark.parametrize("seed", PROCESS_SEEDS)
def test_the_process_kernel_pauses_on_each_placed_line_as_it_runs(process, seed):
    cell, lines = cell_code(seed)
    run_pauses(process, cell, lines, seed)


# ---------- 2. save and load ----------

def _code_source(rng: random.Random) -> str:
    kind = rng.choice(["python", "python", "python", "sql", "crlf"])
    source = cell_code(rng.randrange(10**9))[0].source
    if kind == "sql":
        source = "%%sql\nSELECT 1 AS v\n-- a note\n\nFROM t"
    if kind == "crlf":
        source = source.replace("\n", "\r\n")
    return source


def notebook(seed: int, leading_blanks: bool = False) -> list[dict[str, Any]]:
    """Random cells, each with random breakpoints (clean, junk or none). A code cell's text has no
    blank lines at its start unless ``leading_blanks``: .py source doesn't keep them."""
    rng = random.Random(seed)
    cells = []
    for _ in range(rng.randint(1, 6)):
        if rng.random() < 0.2:
            cell: dict[str, Any] = {"type": "markdown", "source": rng.choice(["# Title", "Some *text*", ""])}
        else:
            source = _code_source(rng)
            if not leading_blanks:
                source = source.lstrip("\r\n \t") or "x = 1"
            elif rng.random() < 0.5:
                source = rng.choice(["\n", "\n\n", "   \n"]) + source
            cell = {"type": "code", "source": source}
        meta: dict[str, Any] = {}
        if rng.random() < 0.8:
            n = len(cell["source"].splitlines()) or 1
            meta["breakpoints"] = rng.choice([
                [rng.randint(1, n) for _ in range(rng.randint(1, 5))],
                [rng.randint(1, n + 3) for _ in range(rng.randint(1, 5))],
                _value(rng),
            ])
        if rng.random() < 0.2:
            meta["tags"] = ["parameters"]
        if meta:
            cell["meta"] = meta
        cells.append(cell)
    return cells


def _bps(cell: dict[str, Any]) -> list[int]:
    return (cell.get("meta") or {}).get("breakpoints") or []


def _text_at(source: str, n: int) -> str | None:
    lines = source.replace("\r\n", "\n").split("\n")
    return lines[n - 1] if n <= len(lines) else None


def _fail(seed: int, cells: Any, back: Any) -> str:
    return f"seed {seed}\ncells: {json.dumps(cells, ensure_ascii=False, default=repr)}\nback: {json.dumps(back, ensure_ascii=False, default=repr)}"


def _check_round_trip(seed: int, cells: list[dict[str, Any]], back: list[dict[str, Any]], markdown_keeps: bool) -> None:
    assert len(back) == len(cells), _fail(seed, cells, back)
    for c, b in zip(cells, back, strict=True):
        want = breakpoints_meta((c.get("meta") or {}).get("breakpoints"))
        if c["type"] == "markdown" and not markdown_keeps:
            want = []
        assert _bps(b) == want, _fail(seed, cells, back)
        # Each breakpoint is still on the same line of text (or past the end, where it was on a
        # trailing blank line the format doesn't keep).
        for n in want:
            now = _text_at(b["source"], n)
            was = _text_at(c["source"], n)
            assert now == was or (now is None and not (was or "").strip()), f"line {n}: {was!r} -> {now!r}\n{_fail(seed, cells, back)}"


@pytest.mark.parametrize("seed", SEEDS)
def test_ipynb_round_trips_keep_breakpoints(seed):
    cells = notebook(seed, leading_blanks=True)
    for fmt in ("ipynb", "ipynb_outputs"):
        back = nbf.parse_ipynb(nbf.export_text(fmt, "N", cells))["cells"]
        _check_round_trip(seed, cells, back, markdown_keeps=True)
    back = from_ipynb(json.loads(json.dumps(to_ipynb(cells, None))))["cells"]  # the notebook's own save
    _check_round_trip(seed, cells, back, markdown_keeps=True)


@pytest.mark.parametrize("seed", SEEDS)
def test_fabric_source_round_trips_keep_breakpoints(seed):
    cells = notebook(seed)
    text = nbf.export_text("fabric_py", "N", cells)
    back = nbf.parse_source(text, "notebook-content.py")["cells"]
    _check_round_trip(seed, cells, back, markdown_keeps=False)
    # Read back and written again, then read back once more: nothing else moves.
    again = nbf.parse_source(nbf.export_text("fabric_py", "N", back), "notebook-content.py")["cells"]
    assert again == back, _fail(seed, back, again)


@pytest.mark.parametrize("seed", SEEDS)
def test_databricks_source_names_every_cell_whose_breakpoints_it_drops(seed):
    cells = notebook(seed)
    notes = nbf.databricks_export_notes(cells)
    named = sorted(n["cell"] for n in notes if "breakpoints" in n["what"])
    assert named == [i for i, c in enumerate(cells, 1) if c["type"] == "code" and breakpoints_meta(_bps(c))], f"seed {seed}"
    back = nbf.parse_source(nbf.export_text("databricks_py", "N", cells), "nb.py")["cells"]
    assert not any(_bps(c) for c in back), f"seed {seed}"


# A cell's leading blank lines aren't kept in .py source (test_notebook_formats_roundtrip), but its
# breakpoint numbers are written unchanged, so after the round trip each one is that many lines
# further down. Seed 3300 is the first in SEEDS where it happens: its third cell, "   \ntotal = 0\n
# v1 = total % 3\n...", has a breakpoint on line 2 ("total = 0") that comes back on "v1 = total % 3".
# Smallest: {"type": "code", "source": "\n\nx = 1\ny = 2\nz = 3", "meta": {"breakpoints": [3]}} comes back
# as "x = 1\ny = 2\nz = 3" with breakpoints [3], on "z = 3" instead of "x = 1".
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="bug: Fabric source drops a cell's leading blank lines without moving its breakpoints")
def test_fabric_source_keeps_breakpoints_on_their_lines_when_a_cell_starts_with_blank_lines():
    for seed in SEEDS:
        cells = notebook(seed, leading_blanks=True)
        back = nbf.parse_source(nbf.export_text("fabric_py", "N", cells), "notebook-content.py")["cells"]
        _check_round_trip(seed, cells, back, markdown_keeps=False)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="bug: Fabric source drops a cell's leading blank lines without moving its breakpoints")
def test_fabric_source_smallest_case_of_a_breakpoint_moved_by_leading_blank_lines():
    cells = [{"type": "code", "source": "\n\nx = 1\ny = 2\nz = 3", "meta": {"breakpoints": [3]}}]
    back = nbf.parse_source(nbf.export_text("fabric_py", "N", cells), "notebook-content.py")["cells"]
    assert [_text_at(back[0]["source"], n) for n in _bps(back[0])] == ["x = 1"]
