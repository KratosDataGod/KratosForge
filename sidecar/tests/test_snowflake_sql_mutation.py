"""Snowflake SQL on this PC (snowflake_sql.py): behaviour that mutation testing (batch 11,
docs/quality/mutation-testing.md) showed no test checked: DIV0 and DIV0NULL with a zero divisor, a
divisor written as text, OCTET_LENGTH of BINARY, OBJECT_INSERT with a FALSE update flag, positional
FLATTEN, the ALTER statements refused as Snowflake settings, and the report a translation gives."""

from __future__ import annotations

import dataclasses

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf


def _run(code, setup=()):
    t = sf.translate(code)
    assert t.runnable, t.first_refusal()
    con = duckdb.connect()
    try:
        for s in setup:
            con.execute(s)
        return con.execute(t.duckdb_sql).fetchall()
    finally:
        con.close()


def _verdicts(code):
    return [(s.verdict, [n.id for n in s.notes]) for s in sf.translate(code).statements]


# ---- division

def test_div0_and_div0null_give_zero_for_a_zero_divisor_instead_of_stopping():
    assert _run("select div0(10, 0), div0null(10, 0), div0null(10, null), div0(7, 2)") == [(0.0, 0.0, 0.0, 3.5)]


def test_a_divisor_written_as_text_still_gets_snowflakes_division_by_zero():
    with pytest.raises(duckdb.Error, match="Division by zero"):
        _run("select 10 / '0'")
    with pytest.raises(duckdb.Error, match="Division by zero"):
        _run("select 10 / 0")
    assert _run("select 10 / 4, 10 / 0.5") == [(2.5, 20.0)]


# ---- strings, objects and FLATTEN

def test_octet_length_counts_the_bytes_of_binary_and_of_text():
    assert _run("select octet_length(hex_decode_binary('00FF')), octet_length(to_binary('00FF00', 'HEX')), "
                "octet_length('abc')") == [(2, 3, 3)]


def test_object_insert_with_a_false_update_flag_refuses_a_key_that_is_there():
    with pytest.raises(duckdb.Error, match="Duplicate field key a"):
        _run("select object_insert(object_construct('a', 1), 'a', 2, false)")
    assert _run("select object_insert(object_construct('a', 1), 'a', 2, true)") == [('{"a":2}',)]
    assert _run("select object_insert(object_construct('a', 1), 'b', 2, false)") == [('{"a":1,"b":2}',)]


def test_flatten_reads_a_positional_input():
    setup = ["CREATE TABLE t(id INT, v JSON)", """INSERT INTO t VALUES (1, '{"arr":[1,2]}')"""]
    assert _run("select f.value from t, lateral flatten(v:arr) f order by 1", setup) == [("1",), ("2",)]


# ---- what runs only in Snowflake

def test_alter_that_sets_a_tag_is_refused_and_a_rename_runs():
    assert _verdicts("alter table t set tag a = 'x'") == [("not_translatable", ["SFN-010"])]
    assert _verdicts("alter table t rename to u") == [("translated", [])]  # stored as U (SFX-072)


# ---- the report

def test_the_translation_label_carries_each_note_in_full():
    out = sf.translate("alter table t set tag a = 'x'").as_output()
    (statement,) = out["statements"]
    assert statement["verdict"] == "not_translatable" and out["runnable"] is False
    (note,) = statement["notes"]
    assert set(note) == {"id", "title", "reason", "action", "source"}
    assert note["id"] == "SFN-010" and note["title"] == "Snowflake settings"
    assert note["reason"] == "This changes something that exists only in Snowflake."


def test_a_translation_and_its_parts_cant_be_changed_after_the_fact():
    t = sf.translate("select 1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.statements = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.statements[0].verdict = "translated"


def test_flatten_outer_false_drops_an_empty_array_and_outer_true_keeps_one_row():
    setup = ["CREATE TABLE t(id INT, v JSON)", """INSERT INTO t VALUES (1, '{"arr":[]}')"""]
    assert _run("select f.value from t, lateral flatten(input => v:arr, outer => false) f", setup) == []
    assert _run("select f.value from t, lateral flatten(input => v:arr, outer => true) f", setup) == [(None,)]


def test_a_divisor_that_is_a_column_named_like_a_number_is_still_checked():
    setup = ['CREATE TABLE z("2" INT)', "INSERT INTO z VALUES (0)"]
    with pytest.raises(duckdb.Error, match="Division by zero"):
        _run('select 10 / "2" from z', setup)


def _refusal(code):
    (statement,) = sf.translate(code).statements
    assert statement.verdict == "not_translatable"
    (note,) = statement.notes
    return note.id, note.reason


def test_as_decimal_takes_a_precision_of_at_most_38():
    assert _refusal("select as_decimal(v, 39) from t") == (
        "SFX-011", "Precision is 1 to 38 and scale 0 to the precision.")
    assert sf.translate("select as_decimal(v, 38, 2) from t").runnable


@pytest.mark.parametrize("code, reason", [
    ("select as_decimal(v, 1, 2, 3) from t", "AS_DECIMAL takes one VARIANT and an optional precision and scale."),
    ("select as_number(v, 1, 2, 3) from t", "AS_NUMBER takes one VARIANT and an optional precision and scale."),
    ("select as_integer(v, 1) from t", "AS_INTEGER takes one VARIANT."),
])
def test_as_and_is_functions_called_with_too_many_arguments_say_what_they_take(code, reason):
    assert _refusal(code) == ("SFX-011", reason)
