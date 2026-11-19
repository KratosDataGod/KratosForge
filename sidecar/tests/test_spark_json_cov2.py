"""Spark's to_json on this PC (spark_json.py): the types it refuses by name (a map with timestamp keys, a
struct field with no type, a type Spark writes its own way), an empty struct, options that aren't written
out, and typeof's name of a map of a type it doesn't name."""

import duckdb
import pytest
from sqlglot import exp

from fabric_desktop import spark_json as sj

T = exp.DataType


def test_a_map_with_timestamp_keys_is_refused():
    with pytest.raises(
        sj.Refused,
        match="because Spark writes a map with TIMESTAMP keys in its own text, so nothing ran",
    ):
        sj.to_json_sql("x", T.build("map<timestamp, int>", dialect="spark"), {})


def test_a_struct_field_with_no_type_is_refused():
    dtype = T(
        this=T.Type.STRUCT,
        expressions=[exp.ColumnDef(this=exp.to_identifier("a"))],
        nested=True,
    )
    with pytest.raises(sj.Refused) as raised:
        sj.to_json_sql("x", dtype, {})
    assert "a struct field's type couldn't be worked out here" in str(raised.value)
    assert str(raised.value).endswith(sj.UNTYPED_STEP)


def test_a_type_spark_writes_its_own_way_is_refused():
    with pytest.raises(
        sj.Refused, match="Spark writes a value of type INTERVAL in its own way"
    ):
        sj.to_json_sql(
            "x",
            T(this=T.Type.ARRAY, expressions=[T.build("interval")], nested=True),
            {},
        )


def test_an_empty_struct_is_written_as_empty_braces():
    sql = sj.to_json_sql("x", T(this=T.Type.STRUCT, expressions=[], nested=True), {})
    assert sql == "CASE WHEN x IS NULL THEN NULL ELSE '{}' END"
    con = duckdb.connect()
    try:
        assert con.sql("SELECT " + sql.replace("x", "NULL")).fetchone() == (None,)
        assert con.sql("SELECT " + sql.replace("x", "1")).fetchone() == ("{}",)
    finally:
        con.close()


def test_options_that_arent_written_out_are_refused():
    node = exp.JSONFormat(
        this=exp.column("x"),
        options=exp.VarMap(
            keys=exp.Array(expressions=[exp.Literal.string("dateFormat")]),
            values=exp.Array(expressions=[exp.column("fmt")]),
        ),
    )
    with pytest.raises(
        sj.Refused, match=r"must be written out as map\('name', 'value', \.\.\.\)"
    ):
        sj.options_of(node)
    node = exp.JSONFormat(this=exp.column("x"), options=exp.column("opts"))
    with pytest.raises(sj.Refused, match="must be written out"):
        sj.options_of(node)


def test_type_name_of_a_map_whose_value_type_it_doesnt_name_is_none():
    assert sj.type_name(T.build("map<string, interval>", dialect="spark")) is None
    assert (
        sj.type_name(T.build("map<string, int>", dialect="spark")) == "map<string,int>"
    )
