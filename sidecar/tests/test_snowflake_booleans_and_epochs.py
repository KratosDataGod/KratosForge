"""Snowflake SQL on this PC, rules 2026.11.28: text cast to BOOLEAN (SFX-077) and the epoch date parts (SFX-078,
SFN-077). Every expected value is one docs.snowflake.com states; the page is named at each test."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf


def _rows(code: str) -> list[tuple[object, ...]]:
    translation = sf.translate(code)
    assert translation.runnable, translation.first_refusal()
    return duckdb.connect().sql(translation.duckdb_sql).fetchall()


def _error(code: str) -> str:
    translation = sf.translate(code)
    assert translation.runnable, translation.first_refusal()
    with pytest.raises(duckdb.Error) as caught:
        duckdb.connect().sql(translation.duckdb_sql).fetchall()
    return str(caught.value)


# ---------------------------------------------------------------- SFX-077: text to BOOLEAN

# data-types-logical, "Conversion to BOOLEAN": "Strings converted to TRUE: 'true', 't', 'yes', 'y', 'on', '1'."
# "Strings converted to FALSE: 'false', 'f', 'no', 'n', 'off', '0'." "Conversion is case-insensitive."
_TRUE = ["true", "t", "yes", "y", "on", "1", "TRUE", "On", "YeS"]
_FALSE = ["false", "f", "no", "n", "off", "0", "FALSE", "Off", "nO"]


@pytest.mark.parametrize("word, want", [(w, True) for w in _TRUE] + [(w, False) for w in _FALSE])
@pytest.mark.parametrize("form", ["SELECT '{w}'::BOOLEAN", "SELECT CAST('{w}' AS BOOLEAN)",
                                  "SELECT TRY_CAST('{w}' AS BOOLEAN)", "SELECT TO_BOOLEAN('{w}')",
                                  "SELECT x::BOOLEAN FROM (VALUES ('{w}')) AS v(x)",
                                  "SELECT TRY_CAST(x AS BOOLEAN) FROM (VALUES ('{w}')) AS v(x)",
                                  "SELECT TO_BOOLEAN(x) FROM (VALUES ('{w}')) AS v(x)"])
def test_text_to_boolean_reads_snowflakes_words(form, word, want):
    assert _rows(form.format(w=word)) == [(want,)]


def test_iff_of_a_cast_text_runs():
    assert _rows("SELECT IFF('off'::BOOLEAN, 1, 0), IFF(CAST('on' AS BOOLEAN), 1, 0)") == [(0, 1)]


@pytest.mark.parametrize("word", ["maybe", " yes", "yes ", "2", "", "tr"])
def test_other_text_stops_with_snowflakes_message_and_try_cast_is_null(word):
    # data-types-logical: "Other text strings can't be converted to BOOLEAN values."
    for code in (f"SELECT '{word}'::BOOLEAN", f"SELECT CAST(x AS BOOLEAN) FROM (VALUES ('{word}')) AS v(x)"):
        message = _error(code)
        assert f"Boolean value '{word}' is not recognized" in message and "Could not convert" not in message, code
    assert _rows(f"SELECT TRY_CAST('{word}' AS BOOLEAN)") == [(None,)]
    assert _rows(f"SELECT TRY_CAST(x AS BOOLEAN) FROM (VALUES ('{word}')) AS v(x)") == [(None,)]


def test_numbers_cast_to_boolean_as_before():
    # data-types-logical: "Zero (0) is converted to FALSE." "Any non-zero value is converted to TRUE."
    assert _rows("SELECT 0::BOOLEAN, 1::BOOLEAN, (-3)::BOOLEAN, 2.5::BOOLEAN, 0.0::BOOLEAN") == \
        [(False, True, True, True, False)]
    assert _rows("SELECT x::BOOLEAN FROM (VALUES (0), (7), (NULL)) AS v(x)") == [(False,), (True,), (None,)]
    assert _rows("SELECT NULL::BOOLEAN, TRUE::BOOLEAN") == [(None, True)]


def test_a_nested_cast_reads_its_value_once():
    sizes = []
    for depth in (1, 2, 4, 8):
        code = "x"
        for _ in range(depth):
            code = f"CAST(CAST({code} AS BOOLEAN) AS VARCHAR)"
        sizes.append(len(sf.translate(f"SELECT {code} FROM t").statements[0].duckdb_sql or ""))
    assert sizes[3] - sizes[2] <= 2.2 * (sizes[2] - sizes[1]), sizes  # it grows linearly


# ---------------------------------------------------------------- SFX-078, SFN-077: the epoch parts

# functions-date-time, "Supported date and time parts": epoch_second has the synonyms epoch and epoch_seconds.
# date_part: "Returns a value of NUMBER data type"; its example gives 1712619560 for 2024-04-08 23:39:20.123.
@pytest.mark.parametrize("part", ["epoch", "epoch_second", "epoch_seconds", "EPOCH"])
def test_epoch_seconds_are_whole_and_cut_off(part):
    assert _rows(f"SELECT DATE_PART({part}, TO_TIMESTAMP('2024-04-08T23:39:20.123-07:00'))") == [(1712619560,)]
    assert _rows(f"SELECT DATE_PART({part}, '2024-01-01 00:00:01.7'::TIMESTAMP)") == [(1704067201,)]
    assert _rows(f"SELECT EXTRACT({part} FROM '2024-01-01 00:00:01.7'::TIMESTAMP)") == [(1704067201,)]
    assert _rows(f"SELECT EXTRACT({part} FROM '2024-01-01'::DATE)") == [(1704067200,)]
    assert _rows(f"SELECT EXTRACT({part} FROM x) FROM (VALUES ('2024-01-01 00:00:01.999'::TIMESTAMP)) v(x)") == \
        [(1704067201,)]


def test_the_smaller_epoch_parts_are_whole_numbers():
    stamp = "'2024-01-01 00:00:01.7005'::TIMESTAMP"
    assert _rows(f"SELECT DATE_PART(epoch_millisecond, {stamp}), DATE_PART(epoch_microsecond, {stamp}), "
                 f"DATE_PART(epoch_nanosecond, {stamp})") == [(1704067201700, 1704067201700500, 1704067201700500000)]
    assert _rows("SELECT DATE_PART(epoch_milliseconds, '2024-01-01 00:00:01.7009'::TIMESTAMP)") == [(1704067201700,)]


def test_an_epoch_part_is_an_integer_type():
    translation = sf.translate("SELECT DATE_PART(epoch, '2024-01-01 00:00:01.7'::TIMESTAMP) AS e")
    types = duckdb.connect().sql(translation.duckdb_sql).types
    assert str(types[0]) == "BIGINT"


def test_a_whole_time_before_1970_runs():
    assert _rows("SELECT DATE_PART(epoch, '1969-12-31 23:59:59'::TIMESTAMP), "
                 "DATE_PART(epoch_millisecond, '1969-12-31 23:59:59.999'::TIMESTAMP), "
                 "DATE_PART(epoch_microsecond, '1969-12-31 23:59:59.999999'::TIMESTAMP), "
                 "EXTRACT(epoch FROM '1960-01-01'::DATE)") == [(-1, -1, -1, -315619200)]


@pytest.mark.parametrize("code", ["SELECT DATE_PART(epoch, '1969-12-31 23:59:59.5'::TIMESTAMP)",
                                  "SELECT EXTRACT(epoch_second FROM x) FROM (VALUES ('1900-01-01 00:00:00.1'::TIMESTAMP)) v(x)",
                                  "SELECT DATE_PART(epoch_millisecond, '1969-12-31 23:59:59.9995'::TIMESTAMP)"])
def test_a_time_before_1970_with_a_fraction_stops_by_name(code):
    # neither date_part nor functions-date-time says which way such a fraction goes
    translation = sf.translate(code)
    with pytest.raises(duckdb.Error) as caught:
        duckdb.connect().sql(translation.duckdb_sql).fetchall()
    assert "SFN-077" in str(caught.value) and sf.stopped_by_name(caught.value)


@pytest.mark.parametrize("part", ["epoch_millisecond", "epoch_microsecond", "epoch_nanosecond"])
def test_the_smaller_epoch_parts_of_a_date_stop_as_in_snowflake(part):
    # date_part usage notes: for a DATE value, epoch_millisecond, epoch_microsecond and epoch_nanosecond aren't
    # supported
    for code in (f"SELECT DATE_PART({part}, '2024-01-01'::DATE)",
                 f"SELECT DATE_PART({part}, x) FROM (VALUES ('2024-01-01'::DATE)) v(x)"):
        assert "isn't supported for a DATE value" in _error(code), code


def test_rules_are_in_the_catalog():
    ids = {id for id, _, _ in sf.CATALOG}
    assert {"SFX-077", "SFX-078", "SFN-077"} <= ids
    assert sf.RULES_VERSION >= "2026.11.28"
