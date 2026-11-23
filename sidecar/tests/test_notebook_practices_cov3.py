"""Notebook best-practice checks (notebook_practices): the remaining edges. A count() made twice isn't a
finding yet; the format given to load(); writes made safe by an option or partitioning; UDF and polars
read shapes that aren't what the rules look for."""

from __future__ import annotations

import ast

from fabric_desktop import notebook_practices as np_mod


def call(code: str) -> ast.Call:
    node = ast.parse(code, mode="eval").body
    assert isinstance(node, ast.Call)
    return node


def test_count_called_twice_on_one_frame_isnt_a_finding_yet():
    ids = [f["rule_id"] for f in np_mod.check(["df.count()\ndf.count()"])["findings"]]
    assert "NB011" not in ids
    ids = [f["rule_id"] for f in np_mod.check(["df.count()\ndf.count()", "df.count()"])["findings"]]
    assert ids.count("NB011") == 1


def test_the_format_named_in_load_is_read_and_one_that_isnt_text_is_not():
    assert np_mod._format(call('spark.read.load("Files/x", format="CSV")')) == "csv"
    assert np_mod._format(call('spark.read.load("Files/x", format=fmt)')) == ""
    assert np_mod._format(call('spark.read.load("Files/x")')) == ""
    assert np_mod._format(call('spark.read.option("header", "true").load("Files/x")')) == ""


def test_a_write_limited_by_options_or_partitioning_is_filtered():
    assert np_mod._filtered(call('df.write.options(replaceWhere="d > 1").mode("overwrite").save("t")')) is True
    assert np_mod._filtered(call('df.write.partitionBy("d").mode("overwrite").saveAsTable("t")')) is True
    assert np_mod._filtered(call('df.write.mode("overwrite").saveAsTable("t")')) is False


def test_only_udf_names_and_calls_are_udfs():
    assert np_mod._is_udf(ast.parse("lambda x: x", mode="eval").body) is False
    assert np_mod._is_udf(call("udf(f)")) is True


def test_a_filter_that_isnt_a_method_call_isnt_an_eager_read():
    assert np_mod._eager_polars_read(call("filter(rows)")) is False
    assert np_mod._eager_polars_read(call('pl.read_csv("a.csv").select("a").filter(x)')) is True
