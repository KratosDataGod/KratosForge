"""Spark SQL built-ins on this PC where the arguments aren't the usual shape: a number format that
isn't written out, printf with a computed format, arrays_zip of nothing, parse_url without a part,
and date_part with a computed field. Each query runs in DuckDB in this process."""

from __future__ import annotations

import pytest

from fabric_desktop import sql


@pytest.mark.parametrize("fn", ["to_number", "try_to_number", "to_char"])
def test_a_number_format_must_be_written_out(fn):
    value = "'12.5'" if "number" in fn else "12.5"
    with pytest.raises(ValueError, match=f"{fn}\\(\\)'s format must be written out as text"):
        sql.run(f"SELECT {fn}({value}, f) FROM VALUES ('99.9') t(f)")


def test_printf_with_a_computed_format_is_left_to_duckdb():
    assert sql.run("SELECT printf(f, 1) AS s FROM VALUES ('%d') t(f)").to_pylist() == [{"s": "1"}]


def test_arrays_zip_of_nothing_is_an_empty_array():
    assert sql.run("SELECT arrays_zip() AS z").to_pylist() == [{"z": []}]


def test_parse_url_without_a_part_is_refused():
    with pytest.raises(ValueError, match="PARSE_URL"):
        sql.run("SELECT parse_url('http://a/b')")


def test_parse_url_reads_a_query_key():
    assert sql.run("SELECT parse_url('http://a/b?x=1', 'QUERY', 'x') AS v").to_pylist() == [{"v": "1"}]


def test_date_part_with_a_computed_field_is_left_to_duckdb():
    out = sql.run("SELECT date_part(f, DATE'2026-01-02') AS d FROM VALUES ('day') t(f)").to_pylist()
    assert out == [{"d": 2}]


def test_to_csv_of_a_struct():
    assert sql.run("SELECT to_csv(named_struct('a', 1, 'b', 'x')) AS c").to_pylist() == [{"c": "1,x"}]
