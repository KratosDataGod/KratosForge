"""Databricks SQL built-in functions on this PC: each case runs again through the DuckDB engine's own
translation, and a changed outcome (a sqlglot or DuckDB upgrade) fails here until the table and
docs/engines.md are updated."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from fabric_desktop.engines import databricks_sql_parity as parity

DOCS = Path(__file__).resolve().parents[2] / "docs" / "engines.md"


@pytest.mark.parametrize("case", parity.CASES, ids=lambda c: c.function)
def test_each_function_answers_as_recorded(case):
    outcome, value = parity.run(case, duckdb.connect())
    recorded = ("same", "differs") if case.outcome == "varies" else (case.outcome,)
    assert outcome in recorded, f"{case.function}: now {outcome} ({value!r}); update CASES and docs/engines.md"


def test_cases_are_unique_and_every_difference_says_what_to_do():
    names = [c.function for c in parity.CASES]
    assert len(names) == len(set(names))
    for c in parity.CASES:
        assert c.outcome in ("same", "differs", "varies", "error", "untranslated")
        assert bool(c.note) == (c.outcome != "same"), c.function
        assert c.sql.startswith("SELECT ")


def test_same_compares_as_databricks_shows_values():
    assert parity.same(3.0, 3) and parity.same([1, None], [1, None])
    assert not parity.same(1, True) and not parity.same("1", 1) and not parity.same([1, 2], [2, 1])


def test_the_docs_carry_the_table():
    assert parity.table_markdown() in DOCS.read_text(encoding="utf-8")
