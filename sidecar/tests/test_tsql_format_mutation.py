"""Mutation-testing follow-up for T-SQL's FORMAT on the local engine (localdata/tsql_format.py):
backslashes inside and outside quoted text, a '%' that isn't before a letter, and the precision a
number format may have."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    return local.engine(local.create("TSQL")["id"])


DAY = "CAST('2024-03-05 14:07:09' AS DATETIME2)"


def fmt(engine, pattern: str):
    quoted = pattern.replace("'", "''")
    return engine.execute(f"SELECT FORMAT({DAY}, '{quoted}') AS f", dialect="tsql")["rows"][0][0]


def refused(engine, sql: str) -> str:
    with pytest.raises(ValueError) as e:
        engine.execute(sql, dialect="tsql")
    assert "This T-SQL can't be run on the local engine" in str(e.value)
    return str(e.value)


def test_quoted_text_with_an_escaped_quote(engine):
    assert fmt(engine, "'a\\'b' d") == "a'b 5"


def test_quoted_text_that_ends_in_a_backslash_is_not_closed(engine):
    assert "quote that isn't closed" in refused(engine, f"SELECT FORMAT({DAY}, '''ab\\')")


def test_a_backslash_outside_quotes_keeps_one_character(engine):
    assert fmt(engine, "\\d d") == "d 5"
    assert fmt(engine, "\\dd") == "d5"


def test_a_backslash_at_the_end_is_refused(engine):
    assert "ends with a backslash" in refused(engine, f"SELECT FORMAT({DAY}, 'd\\')")


def test_a_percent_has_to_come_before_a_letter(engine):
    assert "'%' that isn't before a letter" in refused(engine, f"SELECT FORMAT({DAY}, 'd%')")
    assert "'%' that isn't before a letter" in refused(engine, f"SELECT FORMAT({DAY}, '%%d')")
    assert fmt(engine, "%d") == "5"


@pytest.mark.parametrize("pattern", ["N123", "Nx", "N2x", "F100"])
def test_a_number_format_takes_at_most_two_digits(engine, pattern):
    assert "isn't carried yet" in refused(engine, f"SELECT FORMAT(5, '{pattern}')")


def test_a_number_format_takes_at_most_38_places(engine):
    assert "at most 38 decimal places" in refused(engine, "SELECT FORMAT(5, 'N39')")
    assert "at most 38 decimal places" in refused(engine, "SELECT FORMAT(5, 'F99')")
    assert engine.execute("SELECT FORMAT(0.5, 'F37') AS f", dialect="tsql")["rows"] == [["0." + "5" + "0" * 36]]
