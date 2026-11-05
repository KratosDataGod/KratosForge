"""Snowflake functions on this PC (SF-PARITY, #1199): each case runs again through the Snowflake rules and
DuckDB; a changed outcome fails here until the table and docs/engines.md are updated, and no case that
answers differently may be labelled "translated"."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf
from fabric_desktop import snowflake_sql_parity as parity

DOCS = Path(__file__).resolve().parents[2] / "docs" / "engines.md"


@pytest.mark.parametrize("case", parity.CASES, ids=lambda c: c.function)
def test_each_function_answers_as_recorded(case):
    outcome, value = parity.run(case, duckdb.connect())
    assert outcome == case.outcome, f"{case.function}: now {outcome} ({value!r}); update CASES and docs/engines.md"


@pytest.mark.parametrize("case", [c for c in parity.CASES if c.outcome == "differs"], ids=lambda c: c.function)
def test_a_function_that_answers_differently_is_never_labelled_translated(case):
    statement = sf.translate(case.sql).statements[0]
    ids = [n.id for n in statement.notes]
    assert statement.verdict == "difference" and any(i in case.note for i in ids), (case.function, ids)


def test_cases_are_unique_cite_snowflake_and_say_what_to_know():
    names = [c.function for c in parity.CASES]
    assert len(names) == len(set(names)) and len(names) >= 60
    for c in parity.CASES:
        assert c.outcome in ("same", "differs", "error", "untranslated")
        assert bool(c.note) == (c.outcome != "same"), c.function
        assert c.sql.startswith("SELECT ") and c.source.startswith("https://docs.snowflake.com/en/sql-reference/")


def test_the_docs_carry_the_table_and_say_no_account_was_used():
    page = DOCS.read_text(encoding="utf-8")
    assert parity.table_markdown() in page and "no Snowflake account was used" in parity.table_markdown()
