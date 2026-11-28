"""Mutation-testing follow-up for the SQL portability report (sql_portability.py): the size limit at
its edge, findings that can't be changed, QUALIFY's ORDER BY when a key names an output column (plain,
qualified by the same table or another one, or an alias over another column), the hidden columns it
adds otherwise, and Snowflake SQL that can't be rewritten for DuckDB but still goes through sqlglot for
another platform. Everything here is static: SQL in, a report out."""

import dataclasses

import pytest

from fabric_desktop import sql_portability as sp
from fabric_desktop.sql_portability import portability


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_sql_up_to_200000_characters_is_checked_and_longer_isnt():
    head = "SELECT 1 -- "
    at_limit = portability(head + "x" * (sp.MAX_SQL - len(head)), "duckdb", ["postgres"])
    assert "problem" not in at_limit and at_limit["statements"] == 1
    over = portability(head + "x" * (sp.MAX_SQL - len(head) + 1), "duckdb", ["postgres"])
    assert over["problem"] == "The SQL is longer than 200,000 characters, so it wasn't checked."
    assert sp.MAX_SQL == 200_000


def test_a_finding_cant_be_changed():
    finding = sp.Finding(1, "compatible", None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.grade = "unsupported"  # type: ignore[misc]


def postgres(query):
    statement = portability(query, "duckdb", ["postgres"])["targets"][0]["statements"][0]
    assert statement["grade"] == "translated", statement
    return statement["rewritten"]


QUALIFY = "SELECT {cols} FROM t QUALIFY row_number() OVER (ORDER BY b) = 1 ORDER BY {key} LIMIT 5"


@pytest.mark.parametrize("cols, key", [("t.a, b", "t.a"), ("t.a, b", "a"), ("a, b", "a"), ("a, b", "A")])
def test_an_order_by_key_that_names_an_output_column_is_used_as_it_is(cols, key):
    rewritten = postgres(QUALIFY.format(cols=cols, key=key))
    assert "_o1" not in rewritten
    assert rewritten.endswith(f"WHERE _w = 1 ORDER BY {key[-1]} LIMIT 5")


@pytest.mark.parametrize("cols, key, hidden", [
    ("t.a, b", "u.a", "u.a AS _o1"),   # another table's column of the same name
    ("b AS a, b", "t.a", "t.a AS _o1"),  # the table's a, not the output alias a
])
def test_an_order_by_key_that_isnt_an_output_column_is_hidden_in_the_subquery(cols, key, hidden):
    rewritten = postgres(QUALIFY.format(cols=cols, key=key))
    assert hidden in rewritten and rewritten.endswith("ORDER BY _o1 LIMIT 5")
    assert rewritten.startswith("SELECT a, b FROM (")  # the hidden column isn't returned


def test_each_hidden_order_by_key_gets_its_own_name_in_order():
    rewritten = postgres(QUALIFY.format(cols="a, b", key="a + 1, b * 2 DESC"))
    assert "a + 1 AS _o1, b * 2 AS _o2" in rewritten
    assert rewritten.endswith("ORDER BY _o1, _o2 DESC NULLS LAST LIMIT 5")
    assert "_o0" not in rewritten and "_o3" not in rewritten


def test_snowflake_sql_that_duckdb_cant_take_is_still_rewritten_for_another_platform():
    report = portability("SELECT TO_VARCHAR(d, f) FROM t", "snowflake", ["duckdb", "spark", "postgres"])
    by_id = {t["id"]: t["statements"][0] for t in report["targets"]}
    assert by_id["duckdb"]["reasons"][0].startswith("SFX-002:")  # snowflake_sql's own label for DuckDB
    assert by_id["spark"]["grade"] == "unsupported"
    assert by_id["spark"]["reasons"] and not by_id["spark"]["reasons"][0].startswith("SF")  # sqlglot's reason
    assert by_id["postgres"]["reasons"] and not by_id["postgres"]["reasons"][0].startswith("SF")
