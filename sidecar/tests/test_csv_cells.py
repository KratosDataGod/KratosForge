"""WR-228: a CSV file the app writes for someone to open never holds a text cell a spreadsheet would run.

A text cell that starts with =, +, -, @, a tab or a carriage return gets an apostrophe in front. Number cells
never change. The export says how many cells it protected, and Keep raw values turns protection off for that
one export. The guard at the bottom fails when a module writes CSV without going through csv_cells.
"""

from __future__ import annotations

import ast
import csv
import io
import re
from pathlib import Path

import pyarrow as pa
import pytest

from fabric_desktop import csv_cells, model_dictionary, shortcut_center
from fabric_desktop.central import entra
from fabric_desktop.frames import Frames
from tests.test_sql_exec import local_runs, start_local, wait, wait_export  # noqa: F401 - local_runs is a fixture

RISKY = ["=HYPERLINK(\"http://x\",\"y\")", "+1", "-2", "@SUM(A1)", "\t=1", "\r=1"]
SAFE = ["plain", "", "a=b", "'=already text", " =1"]  # '= reads back as =: Import can't tell it was typed


@pytest.mark.parametrize("value", RISKY)
def test_a_text_cell_that_starts_like_a_formula_gets_an_apostrophe(value):
    assert csv_cells.protect(value) == "'" + value
    assert csv_cells.unprotect(csv_cells.protect(value)) == value


@pytest.mark.parametrize("value", SAFE)
def test_other_text_is_left_alone(value):
    assert csv_cells.protect(value) == value
    assert csv_cells.unprotect(value) == (value[1:] if value.startswith("'=") else value)


def test_rows_count_what_they_protected_and_raw_keeps_them():
    assert csv_cells.protect_row(["=1", -5, None, "x", "@a"]) == (["'=1", -5, None, "x", "'@a"], 2)
    assert csv_cells.protect_row(["=1", "@a"], raw=True) == (["=1", "@a"], 0)
    cells = csv_cells.Cells()
    assert [cells(v) for v in ("=1", None, 0, "a", "-b")] == ["'=1", "", "0", "a", "'-b"] and cells.protected == 2


def _batch():
    return pa.record_batch([
        pa.array(["=1+2", "ok", None, "-x"]),
        pa.array([-5, 1, 2, -3]),
        pa.array([-1.5, 0.0, None, 2.0]),
        pa.array(["@a", "b", "+c", None], pa.large_string()),
        pa.array(["=e", "f", "=e", None]).dictionary_encode(),
    ], names=["s", "n", "f", "l", "d"])


def test_a_batch_protects_text_columns_only_and_keeps_its_schema():
    batch = _batch()
    out, n = csv_cells.protect_batch(batch)
    assert n == 6 and out.schema == batch.schema
    assert out.column(0).to_pylist() == ["'=1+2", "ok", None, "'-x"]
    assert out.column(1).to_pylist() == [-5, 1, 2, -3]  # a number stays a number
    assert out.column(2).to_pylist() == [-1.5, 0.0, None, 2.0]
    assert out.column(3).to_pylist() == ["'@a", "b", "'+c", None]
    assert out.column(4).to_pylist() == ["'=e", "f", "'=e", None]
    assert csv_cells.protect_batch(batch, raw=True) == (batch, 0)
    numbers = pa.record_batch([pa.array([-1, -2])], names=["n"])
    assert csv_cells.protect_batch(numbers) == (numbers, 0)


def test_a_table_protects_like_a_batch():
    table = pa.Table.from_batches([_batch(), _batch()])
    out, n = csv_cells.protect_table(table)
    assert n == 12 and out.schema == table.schema and out.column("n").to_pylist() == [-5, 1, 2, -3] * 2
    assert csv_cells.protect_table(table, raw=True) == (table, 0)


def test_the_note_says_how_many_and_how_to_keep_raw_values():
    assert csv_cells.note(0) == ""
    assert csv_cells.note(1).startswith("1 text cell started with") and " it got an apostrophe" in csv_cells.note(1)
    many = csv_cells.note(1234)
    assert many.startswith("1,234 text cells") and many.endswith("To keep the values exactly as they are, pick CSV, Keep raw values.")
    assert "Keep raw values" not in csv_cells.note(3, can_keep_raw=False)


# ---- every path that writes a CSV for someone to open

def _rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


@pytest.mark.parametrize("dialect,query", [
    ("duckdb", "SELECT '=1+2' AS s, -5 AS n UNION ALL SELECT 'ok', 3 ORDER BY n"),
    ("tsql", "SELECT '=1+2' AS s, -5 AS n UNION ALL SELECT 'ok', 3 ORDER BY n"),
])
def test_the_sql_page_export_protects_and_keep_raw_values_does_not(local_runs, dialect, query):  # noqa: F811
    status = wait(local_runs, start_local(local_runs, query, dialect=dialect)["id"])
    assert status["state"] == "completed", status
    done = wait_export(local_runs, local_runs.export(status["id"], "csv")["id"])
    assert done["state"] == "completed" and done["protected"] == 1 and done["raw"] is False
    assert done["notes"] == [csv_cells.note(1)]
    assert _rows(done["path"])[1:] == [["'=1+2", "-5"], ["ok", "3"]]
    raw = wait_export(local_runs, local_runs.export(status["id"], "csv", raw=True)["id"])
    assert raw["protected"] == 0 and raw["raw"] is True and raw["notes"] == []
    assert _rows(raw["path"])[1:] == [["=1+2", "-5"], ["ok", "3"]]
    parquet = local_runs.export(status["id"], "parquet", raw=True)
    assert wait_export(local_runs, parquet["id"])["raw"] is False  # Keep raw values is for a CSV only


def test_the_notebook_download_protects_and_keep_raw_values_does_not(tmp_path):
    store = Frames()
    fid = store.keep(pa.table({"s": ["@cmd", "x"], "n": [-1, 2]}))
    target = tmp_path / "r.csv"
    target.touch()
    got = store.download(fid, "csv", str(target))
    assert got["protected"] == 1 and got["notes"] == [csv_cells.note(1)] and got["raw"] is False
    assert _rows(target)[1:] == [["'@cmd", "-1"], ["x", "2"]]
    target.unlink()
    target.touch()
    raw = store.download(fid, "csv", str(target), raw=True)
    assert raw["protected"] == 0 and raw["raw"] is True and _rows(target)[1:] == [["@cmd", "-1"], ["x", "2"]]


def test_the_listing_exports_protect_and_count():
    cells = csv_cells.Cells()
    text = shortcut_center.to_csv([{"name": "=HYPERLINK(1)", "path": "Tables/x", "workspace_name": "-ws"}], cells)
    row = list(csv.reader(io.StringIO(text)))[1]
    assert row[0] == "'=HYPERLINK(1)" and row[2] == "'-ws" and cells.protected == 2

    item = {"table": "t", "name": "c", "kind": "column", "type": "string", "description": "=1+2",
            "classification": "general", "hidden": False, "entity": "t.c"}
    cells = csv_cells.Cells()
    text = model_dictionary.to_csv([item], cells)
    assert cells.protected == 1 and "'=1+2" in text
    assert model_dictionary.read_csv(text)[0]["description"] == "=1+2"  # Import takes the apostrophe off

    group = {"domain": "@d", "label": "Admin", "env": "dev", "linked": None, "suggested": "g", "mail_nickname": "g",
             "description": "x", "who": "w", "gets": "+all"}
    cells = csv_cells.Cells()
    row = list(csv.reader(io.StringIO(entra.table([group], ["=owner"], cells))))[1]
    assert row[0] == "'@d" and row[6] == "'=owner" and row[8] == "'+all" and cells.protected == 3


# ---- the guard: no module writes a CSV without going through csv_cells

ENGINE = Path(__file__).resolve().parents[1] / "fabric_desktop"
WRITES_CSV = re.compile(r"csv\.writer\(|csv\.DictWriter\(|CSVWriter\(|\.write_csv\(|\.to_csv\(|\bsink_csv\(|"
                        r"FORMAT\s+'?csv", re.IGNORECASE)

CALLS_A_WRITER = re.compile(r"(\bcsv\.writer|\bcsv\.DictWriter|\bCSVWriter|\.write_csv|\.to_csv|\bsink_csv)$")

# Modules that write CSV data files rather than exports someone opens in a spreadsheet, and why.
DATA_FILES = {
    "spark_local.py": "DataFrame.write.csv on this PC: a Spark data file, written as Fabric's Spark writes it",
    "spark_stream.py": "a streaming query's CSV sink: a Spark data file, written as Fabric's Spark writes it",
}


# Functions that only open a CSV writer for a caller that protects every batch first.
WRITER_FOR_A_CALLER = {
    ("sql_exec.py", "_writer"): "SqlExecutions._write_batches protects the header and every batch it writes",
}


def _writers(path: Path) -> list[tuple[str, int]]:
    """(function, line) for each CSV writer call in the module, by the innermost function holding it."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    found = []

    def walk(node: ast.AST, inside: ast.AST | None) -> None:
        for child in ast.iter_child_nodes(node):
            here = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else inside
            if isinstance(child, ast.Call) and CALLS_A_WRITER.search(ast.get_source_segment(text, child.func) or ""):
                found.append((here, child.lineno))
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and WRITES_CSV.search(child.value):
                found.append((here, child.lineno))  # COPY ... (FORMAT csv) in SQL text
            walk(child, here)

    walk(tree, None)
    out = []
    for func, line in dict.fromkeys(found):
        body = ast.get_source_segment(text, func) if func is not None else text
        name = func.name if func is not None else "<module>"
        if "csv_cells" not in body and "cells(" not in body:
            out.append((name, line))
    return out


def test_every_csv_writer_protects_its_cells():
    """Each call that writes CSV, in the function that makes it: that function protects its cells, opens the writer
    for a caller that does (WRITER_FOR_A_CALLER), or its module writes Spark data files (DATA_FILES)."""
    unprotected = []
    for path in sorted(ENGINE.rglob("*.py")):
        rel = path.relative_to(ENGINE).as_posix()
        if rel == "csv_cells.py" or rel in DATA_FILES:
            continue
        unprotected += [f"{rel}:{line} {name}" for name, line in _writers(path) if (rel, name) not in WRITER_FOR_A_CALLER]
    assert not unprotected, (f"{unprotected} write CSV without csv_cells: protect the cells (WR-228), or list the "
                             "module in DATA_FILES with the reason it writes a data file and not an export")


def test_the_guard_catches_a_second_writer_in_a_module_that_protects_one(tmp_path):
    module = tmp_path / "two.py"
    module.write_text("import csv\nfrom . import csv_cells\n\ndef good(out, rows):\n    cells = csv_cells.Cells()\n"
                      "    csv.writer(out).writerows([[cells(v) for v in r] for r in rows])\n\n"
                      "def bad(out, rows):\n    csv.writer(out).writerows(rows)\n", encoding="utf-8")
    assert _writers(module) == [("bad", 9)]


def test_the_listed_writers_still_exist():
    for rel, name in WRITER_FOR_A_CALLER:
        tree = ast.parse((ENGINE / rel).read_text(encoding="utf-8"))
        assert any(isinstance(n, ast.FunctionDef) and n.name == name for n in ast.walk(tree)), f"{rel} {name}: unlist it"


def test_the_exempt_modules_still_exist_and_still_write_csv():
    for rel in DATA_FILES:
        assert WRITES_CSV.search((ENGINE / rel).read_text(encoding="utf-8")), f"{rel} no longer writes CSV: unlist it"


def test_a_column_name_that_starts_like_a_formula_is_protected_in_the_header_row(local_runs, tmp_path):  # noqa: F811
    status = wait(local_runs, start_local(local_runs, 'SELECT 1 AS "=HYPERLINK(1)", \'x\' AS s')["id"])
    done = wait_export(local_runs, local_runs.export(status["id"], "csv")["id"])
    assert _rows(done["path"]) == [["'=HYPERLINK(1)", "s"], ["1", "x"]] and done["protected"] == 1
    raw = wait_export(local_runs, local_runs.export(status["id"], "csv", raw=True)["id"])
    assert _rows(raw["path"])[0] == ["=HYPERLINK(1)", "s"] and raw["protected"] == 0
    store = Frames()
    target = tmp_path / "h.csv"
    target.touch()
    got = store.download(store.keep(pa.table({"@a": ["=b"], "n": [1]})), "csv", str(target))
    assert _rows(target) == [["'@a", "n"], ["'=b", "1"]] and got["protected"] == 2
    header, n = csv_cells.protect_header(pa.schema([("-x", pa.int64()), ("y", pa.string())]))
    assert header.names == ["'-x", "y"] and n == 1
    batch, n = csv_cells.protect_batch(pa.record_batch([pa.array([1])], names=["+n"]))
    assert batch.schema.names == ["'+n"] and n == 0  # a batch's names match the header's; counted once, there


def test_a_string_view_column_is_protected_and_written_as_string():
    batch = pa.record_batch([pa.array(["=a", "b"], pa.string_view()), pa.array([-1, 2])], names=["v", "n"])
    header, _ = csv_cells.protect_header(batch.schema)
    out, n = csv_cells.protect_batch(batch)
    assert n == 1 and out.schema == header and header.field("v").type == pa.string()
    assert out.column(0).to_pylist() == ["'=a", "b"] and out.column(1).to_pylist() == [-1, 2]
    table, n = csv_cells.protect_table(pa.Table.from_batches([batch]))
    assert n == 1 and table.schema.field("v").type == pa.string()
    import pyarrow.csv as pa_csv
    sink = io.BytesIO()
    with pa_csv.CSVWriter(sink, header) as w:
        w.write_batch(out)
    assert sink.getvalue().decode().splitlines() == ['"v","n"', '"\'=a",-1', '"b",2']
