"""The Snowflake functions the coverage page listed as Not checked (2026.11.21): what each now does here.

ARRAY_INSERT, ARRAY_PREPEND and ARRAY_CONSTRUCT_COMPACT run their pages' examples (SFX-040, checked in
snowflake_sql_parity). CURRENT_TIME(p) and LOCALTIME(p) run (SFX-041). CURRENT_VERSION, CURRENT_SCHEMAS and
RANDOM with a seed stop by name (SFN-040, SFN-041, SFN-042). The rest keep a reason on the page."""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from fabric_desktop import snowflake_coverage
from fabric_desktop import snowflake_sql as sf
from fabric_desktop import snowflake_sql_parity as parity


def _run(sql):
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    con = duckdb.connect()
    return con.execute(translation.duckdb_sql).fetchone(), [d[1] for d in con.description]


@pytest.mark.parametrize("name", ["ARRAY_INSERT (docs example)", "ARRAY_INSERT (negative position, docs example)",
                                  "ARRAY_PREPEND (docs example)", "ARRAY_CONSTRUCT_COMPACT (docs example)"])
def test_the_array_pages_examples_answer_as_documented(name):
    [case] = [c for c in parity.CASES if c.function == name]
    assert parity.run(case) == ("same", case.expected)


def test_an_array_of_text_and_numbers_keeps_each_elements_type():
    row, _ = _run("SELECT TO_JSON(ARRAY_CONSTRUCT_COMPACT(NULL, 'hello', 3::double, 4, 5)), "
                  "TO_JSON(ARRAY_APPEND(ARRAY_CONSTRUCT(1, 2), 'a')), TO_JSON(ARRAY_CONSTRUCT(1, 'a')), "
                  "ARRAY_SIZE(ARRAY_CONSTRUCT(1, 'a')), ARRAY_CONSTRUCT(1, 'a')[1]::VARCHAR")
    assert row == ('["hello",3.0,4,5]', '[1,2,"a"]', '[1,"a"]', 2, "a")


def test_an_array_of_one_kind_stays_a_plain_list():
    sql = sf.translate("SELECT ARRAY_INSERT(ARRAY_CONSTRUCT(0, 1), 1, 9), ARRAY_CONSTRUCT('a', NULL)").duckdb_sql
    assert "VARIANT" not in sql


@pytest.mark.parametrize(("precision", "digits"), [(0, 0), (2, 2), (4, 4), (6, 6), (9, 6)])
def test_current_time_with_a_precision_is_cut_to_it(precision, digits):
    for name in ("CURRENT_TIME", "LOCALTIME"):
        (value,), types = _run(f"SELECT {name}({precision})")
        assert types == ["TIME"] and isinstance(value, dt.time)
        assert value.microsecond % 10 ** (6 - digits) == 0


def test_current_time_without_a_precision_still_runs():
    (a, b), types = _run("SELECT CURRENT_TIME, LOCALTIME()")
    assert types == ["TIME", "TIME"]


def test_current_time_with_a_precision_past_9_is_refused():
    assert not sf.translate("SELECT CURRENT_TIME(10)").runnable


@pytest.mark.parametrize(("sql", "rule"), [("SELECT CURRENT_VERSION()", "SFN-040"),
                                           ("SELECT CURRENT_SCHEMAS()", "SFN-041"),
                                           ("SELECT RANDOM(4711) FROM TABLE(GENERATOR(ROWCOUNT => 3))", "SFN-042"),
                                           ("SELECT RANDOM(4711), RANDOM(4711)", "SFN-042")])
def test_values_that_would_be_wrong_here_stop_by_name(sql, rule):
    translation = sf.translate(sql)
    assert not translation.runnable
    [note] = translation.statements[0].notes
    assert note.id == rule


def test_random_without_a_seed_and_uniform_still_run():
    (value,), types = _run("SELECT RANDOM()")
    assert types == ["BIGINT"] and -2 ** 63 <= value < 2 ** 63
    assert sf.translate("SELECT UNIFORM(1, 10, RANDOM(5))").statements[0].notes[0].id == "SFN-021"


def test_the_new_rules_are_in_the_catalog():
    ids = {row[0] for row in sf.CATALOG}
    assert {"SFX-040", "SFX-041", "SFN-040", "SFN-041", "SFN-042"} <= ids
    assert "SFN-025" not in ids and sf.RULES_VERSION == "2026.11.21"


def test_the_functions_left_not_checked_each_say_why():
    rows = {r["name"]: r for r in snowflake_coverage.coverage()}
    assert {n for n, r in rows.items() if r["status"] == "not checked"} == set(snowflake_coverage.NOT_CHECKED)
    assert all(r["note"] for r in rows.values() if r["status"] == "not checked")
    for name in ("ARRAY_INSERT", "ARRAY_PREPEND", "ARRAY_CONSTRUCT_COMPACT"):
        assert rows[name]["status"] == "runs"
    assert rows["CURRENT_VERSION"]["status"] == rows["CURRENT_SCHEMAS"]["status"] == "refused"
