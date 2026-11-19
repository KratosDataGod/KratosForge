"""Databricks' own built-ins (databricks_coverage.py): an example of several statements, a function with
no value to check, one whose value differs here, and probes that run or fail. Run on DuckDB on this PC."""

import duckdb
import pytest

from fabric_desktop import databricks_coverage as dc


@pytest.fixture
def con():
    c = duckdb.connect()
    yield c
    c.close()


def test_run_executes_every_statement_before_the_last(con):
    assert dc.run("CREATE TEMP VIEW v AS SELECT 7 AS x; SELECT x FROM v", con) == [[7]]


def test_check_without_a_value_is_not_checked(con):
    assert dc.check({"name": "zz_cov_fn", "example": "SELECT 1"}, con) == (
        "not checked",
        "",
    )
    assert dc.check({"name": "zz_cov_fn", "value": [[1]]}, con) == ("not checked", "")


def test_check_whose_value_differs_shows_both(con):
    assert dc.check(
        {"name": "zz_cov_fn", "example": "SELECT 1 AS x", "value": [[2]]}, con
    ) == ("differs", "[[1]] here, [[2]] in Databricks")


def test_probe_that_runs_with_null_arguments_is_not_checked(con):
    assert dc.probe({"name": "abs"}, con) == ("not checked", "")


def test_probe_of_an_unknown_function_says_duckdb_has_none(con):
    assert dc.probe({"name": "zz_cov_nope"}, con) == (
        "fails",
        "DuckDB has no zz_cov_nope, and the translation doesn't write it",
    )


def test_a_listed_probe_that_fails_gives_its_error(con):
    status, note = dc.probe(
        {"name": "zz", "probe": "SELECT * FROM zz_cov_missing_table"}, con
    )
    assert status == "fails" and "zz_cov_missing_table" in note
