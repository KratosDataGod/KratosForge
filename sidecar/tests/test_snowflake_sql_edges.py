"""Snowflake SQL that is refused rather than rewritten: a computed or unknown date format, a
WIDTH_BUCKET or TO_OBJECT with the wrong arguments, FLATTEN's MODE, and what a refused
translation says when its SQL is asked for. Translation only; nothing runs."""

from __future__ import annotations

import pytest

from fabric_desktop import snowflake_sql as s


@pytest.mark.parametrize(("code", "says"), [
    ("SELECT TO_VARCHAR(d, f) FROM t", "TO_VARCHAR with a computed format"),
    ("SELECT TO_VARCHAR(d, 'YYYY-MM-DD hello') FROM t", "Date or time format"),
    ("SELECT WIDTH_BUCKET(x, 0, 10) FROM t", "WIDTH_BUCKET takes a value, min_value, max_value and num_buckets"),
    ("SELECT TO_OBJECT(a, b) FROM t", "TO_OBJECT takes one value"),
    ("SELECT * FROM TABLE(FLATTEN(input => x, mode => 'ARRAY'))", "FLATTEN's MODE argument has no DuckDB equivalent"),
])
def test_refused_snowflake_sql_says_why(code, says):
    t = s.translate(code)
    assert not t.runnable
    assert says in t.first_refusal()
    with pytest.raises(ValueError, match="Statement 1 runs only in Snowflake"):
        t.duckdb_sql  # noqa: B018


def test_a_refusal_without_a_note_still_says_it_runs_only_in_snowflake():
    t = s.Translation((s.StatementReport(1, "translated", "SELECT 1"), s.StatementReport(2, "not_translatable", None)))
    assert t.first_refusal() == "Statement 2 runs only in Snowflake: It can't be rewritten for DuckDB."


def test_a_runnable_translation_has_no_refusal():
    t = s.translate("SELECT 1")
    assert t.runnable and t.first_refusal() == ""


def test_object_agg_over_a_window_builds_the_object_outside_it():
    t = s.translate("SELECT OBJECT_AGG(k, v) OVER (PARTITION BY g) FROM t")
    assert t.runnable
    assert "OVER (PARTITION BY g)" in t.duckdb_sql
