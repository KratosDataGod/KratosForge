"""Data Wrangler parity: the step choices and typed values test_wrangler.py doesn't reach. A step
the data can't take says why before any code is written, and a typed value becomes a literal of the
column's own type, so the preview and the generated cell agree. Test-only; nothing leaves this PC."""

from __future__ import annotations

import datetime as dt
import decimal

import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop import wrangler

DF = pl.DataFrame({
    "id": [1, 2, 3],
    "name": ["a b", "c d", None],
    "price": pl.Series([decimal.Decimal("1.10"), None, decimal.Decimal("3.30")], dtype=pl.Decimal(10, 2)),
    "ok": [True, None, False],
    "at": pl.Series([dt.datetime(2024, 1, 1), None, dt.datetime(2024, 1, 3)]).dt.replace_time_zone("UTC"),
    "wait": pl.Series([dt.timedelta(seconds=1), None, dt.timedelta(seconds=3)]),
    "when": [dt.time(9), None, dt.time(17)],
    "score": [0.5, None, 2.5],
    "tags": [[1], [2], None],
})


def code(step):
    return wrangler.code_for(DF, step)[0]


def run(step, df=DF):
    out = wrangler.run(df.to_arrow(), df, [step], "t")
    assert out["error"] is None, out["error"]
    return out


def column(out, name):
    i = out["columns"].index(name)
    return [r[i] for r in out["rows"]]


# ---------------------------------------------------------------- steps the data can't take


@pytest.mark.parametrize("step, words", [
    ({"op": "explode"}, "Unknown step 'explode'"),
    ({"op": "drop", "columns": []}, "Pick at least one column"),
    ({"op": "keep", "columns": ["nope"]}, "no column 'nope'"),
    ({"op": "rename", "column": "id", "to": "  "}, "Type the new name"),
    ({"op": "rename", "column": "id", "to": "name"}, "already a column 'name'"),
    ({"op": "cast", "columns": ["id"], "to": "varchar"}, "Pick a type"),
    ({"op": "filter", "column": "id", "test": "like"}, "Pick how to compare"),
    ({"op": "filter", "column": "id", "test": "contains", "value": "1"}, "'id' isn't text"),
    ({"op": "filter", "column": "id", "test": "eq", "value": "two"}, "'two' isn't a number"),
    ({"op": "filter", "column": "score", "test": "gt", "value": "inf"}, "not 'inf'"),
    ({"op": "filter", "column": "id", "test": "eq", "value": "2.5"}, "isn't a whole number"),
    ({"op": "filter", "column": "ok", "test": "eq", "value": "yes"}, "Type true or false"),
    ({"op": "filter", "column": "wait", "test": "eq", "value": "1s"}, "A duration can't be typed in"),
    ({"op": "trim", "columns": ["id"]}, "'id' isn't text"),
    ({"op": "case", "columns": ["name"], "to": "camel"}, "Pick upper, lower or title"),
    ({"op": "replace", "columns": ["name"], "find": ""}, "Type the text to find"),
    ({"op": "split", "column": "id", "value": ","}, "'id' isn't text"),
    ({"op": "split", "column": "name", "value": ""}, "Type the text to split on"),
    ({"op": "split", "column": "name", "value": " ", "parts": "many"}, "Type how many parts"),
    ({"op": "split", "column": "name", "value": " ", "parts": 1}, "Split into 2 to"),
    ({"op": "split", "column": "name", "value": " ", "parts": wrangler.MAX_SPLIT + 1}, "Split into 2 to"),
])
def test_a_step_the_data_cant_take_says_why(step, words):
    with pytest.raises(ValueError, match=words):
        code(step)


def test_a_split_never_overwrites_a_column():
    df = DF.with_columns(pl.lit("x").alias("name_2"))
    with pytest.raises(ValueError, match="already a column 'name_2'"):
        wrangler.code_for(df, {"op": "split", "column": "name", "value": " "})


def test_too_many_steps_are_refused():
    with pytest.raises(ValueError, match=f"At most {wrangler.MAX_STEPS} steps"):
        wrangler.run(DF.to_arrow(), DF, [{"op": "dedupe"}] * (wrangler.MAX_STEPS + 1))


def test_an_unknown_step_is_listed_as_unknown():
    assert wrangler.describe_step({"op": "explode", "columns": "id"}) == "Unknown step: 'id'"
    assert wrangler.describe_step({"op": "dedupe"}) == "Remove duplicate rows"


# ---------------------------------------------------------------- typed values keep the column's type


def test_a_decimal_fill_keeps_the_exact_digits_and_type():
    out = run({"op": "fill", "columns": ["price"], "value": "2.20"})
    assert column(out, "price")[1] in ("2.20", decimal.Decimal("2.20"))
    assert "pl.Decimal(10, 2)" in out["code"]


def test_a_float_column_compares_to_a_float_and_a_boolean_to_true_or_false():
    assert column(run({"op": "filter", "column": "score", "test": "ge", "value": "1"}), "score") == [2.5]
    assert column(run({"op": "filter", "column": "ok", "test": "eq", "value": " TRUE "}), "id") == [1]


def test_a_large_integer_is_compared_exactly():
    df = pl.DataFrame({"n": [9007199254740992, 9007199254740993]})
    assert column(run({"op": "filter", "column": "n", "test": "eq", "value": "9007199254740993"}, df), "n") == \
        [9007199254740993]


def test_a_typed_time_is_read_in_the_columns_zone_and_a_time_of_day_as_a_time():
    out = run({"op": "filter", "column": "at", "test": "ge", "value": "2024-01-02 00:00:00"})
    assert column(out, "id") == [3] and "time_zone='UTC'" in out["code"]
    assert column(run({"op": "filter", "column": "when", "test": "lt", "value": "12:00:00"}), "id") == [1]


def test_text_filters_and_null_tests():
    assert column(run({"op": "filter", "column": "name", "test": "starts", "value": "c"}), "id") == [2]
    assert column(run({"op": "filter", "column": "name", "test": "ends", "value": "b"}), "id") == [1]
    assert column(run({"op": "filter", "column": "name", "test": "missing"}), "id") == [3]
    assert column(run({"op": "filter", "column": "name", "test": "present"}), "id") == [1, 2]


def test_drop_missing_on_chosen_columns_and_dedupe_on_a_subset():
    assert column(run({"op": "drop_missing", "columns": ["ok"]}), "id") == [1, 3]
    df = pl.DataFrame({"k": [1, 1, 2], "v": ["a", "b", "c"]})
    assert column(run({"op": "dedupe", "columns": ["k"]}, df), "v") == ["a", "c"]


def test_a_value_of_another_kind_is_typed_in_as_text():
    assert wrangler._literal(DF.schema["tags"], 5) == "'5'"
    assert wrangler._kind(DF.schema["tags"]) == "other" and wrangler._kind(DF.schema["ok"]) == "boolean"


def test_a_single_column_result_is_still_a_table():
    out = wrangler.run(pa.table({"a": [2, 1]}), None, [{"op": "sort", "column": "a"}])
    assert out["error"] is None and column(out, "a") == [1, 2]
