"""One-row reads from DuckDB that work on 1.x and 2.0 (#1341).

DuckDB 2.0 keeps a connection busy while an executed result has rows left, so a ``fetchone()`` left
the next query failing with "connection has an unfinished result"; its relations have no
``fetchone()`` at all. The engine reads single rows through duckdb_rows instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from fabric_desktop.duckdb_rows import first_row, one_row

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"


def test_a_one_row_read_leaves_the_connection_free():
    con = duckdb.connect()
    assert one_row(con.execute("SELECT 42")) == (42,)
    assert con.execute("SELECT 1").fetchall() == [(1,)]


def test_relations_and_empty_results():
    con = duckdb.connect()
    assert first_row(con.sql("SELECT range FROM range(10, 20)")) == (10,)
    assert first_row(con.execute("SELECT 1 WHERE false")) is None
    with pytest.raises(LookupError):
        one_row(con.execute("SELECT 1 WHERE false"))


def test_engine_code_that_uses_duckdb_reads_rows_through_duckdb_rows():
    found = []
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "duckdb_rows.py" and re.search(r"^\s*import duckdb|^\s*from duckdb", text, re.M):
            found += [f"{path.relative_to(PACKAGE)}:{n}" for n, line in enumerate(text.splitlines(), 1)
                      if ".fetchone()" in line]
    assert found == []
