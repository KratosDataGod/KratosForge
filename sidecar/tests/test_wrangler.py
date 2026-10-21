"""Data Wrangler: each step's Polars code, the preview it gives, and the cell it writes."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop import wrangler
from fabric_desktop.frames import Frames
from fabric_desktop.notebook import Kernel

TABLE = pa.table({
    "id": [1, 2, 2, 3],
    "name": ["  Ann ", "bob", "bob", None],
    "amount": [1.5, float("nan"), float("nan"), None],
    "day": ["2024-01-02", "bad", "bad", None],
    "tags": ["a,b", "c", "c", None],
})


def preview(steps, value=None, name="orders"):
    return wrangler.run(TABLE, value if value is not None else TABLE, steps, name)


def column(out, name):
    i = out["columns"].index(name)
    return [r[i] for r in out["rows"]]


def test_no_steps_writes_a_cell_that_returns_the_data_unchanged():
    out = preview([])
    assert out["rows_before"] == out["rows_after"] == 4
    assert out["error"] is None
    assert "def clean_data(df):\n    pass\n    return df" in out["code"]
    assert out["code"].endswith("orders_clean = clean_data(pl.from_arrow(orders))\ndisplay(orders_clean)\n")


def test_each_step_cleans_the_preview():
    out = preview([
        {"op": "dedupe"},
        {"op": "trim", "columns": ["name"]},
        {"op": "case", "columns": ["name"], "to": "title"},
        {"op": "fill", "columns": ["amount"], "value": "0"},
        {"op": "cast", "columns": ["day"], "to": "date"},
        {"op": "split", "column": "tags", "value": ",", "parts": 2},
        {"op": "rename", "column": "id", "to": "order_id"},
        {"op": "sort", "column": "order_id", "desc": True},
    ])
    assert out["error"] is None
    assert out["rows_before"] == 4 and out["rows_after"] == 3
    assert column(out, "order_id") == [3, 2, 1]
    assert column(out, "name") == [None, "Bob", "Ann"]
    assert column(out, "amount") == [0.0, 0.0, 1.5]
    assert column(out, "day") == [None, None, "2024-01-02"]
    assert column(out, "tags_2") == [None, None, "b"]
    assert out["types"][out["columns"].index("day")] == "date"
    assert set(out["changed"]) == {"name", "amount", "day", "tags_1", "tags_2", "order_id"}
    assert len(out["steps"]) == 8


def test_filter_drop_keep_and_replace():
    out = preview([
        {"op": "filter", "column": "id", "test": "ge", "value": "2"},
        {"op": "filter", "column": "name", "test": "present"},
        {"op": "replace", "columns": ["name"], "find": "b", "value": "B"},
        {"op": "drop_missing", "columns": ["name"]},
        {"op": "keep", "columns": ["id", "name"]},
    ])
    assert out["columns"] == ["id", "name"]
    assert out["rows"] == [[2, "BoB"], [2, "BoB"]]
    out = preview([{"op": "drop", "columns": ["tags", "day"]}])
    assert out["columns"] == ["id", "name", "amount"]


def test_the_generated_cell_does_what_the_preview_showed():
    steps = [{"op": "dedupe", "columns": ["id"]}, {"op": "fill", "columns": ["amount"], "value": "7"},
             {"op": "filter", "column": "name", "test": "contains", "value": "o"}]
    for value in (TABLE, pl.from_arrow(TABLE)):
        out = preview(steps, value)
        shown = []
        scope = {"orders": value, "display": shown.append}
        exec(out["code"], scope)  # noqa: S102 - the code under test
        assert scope["orders_clean"].to_dicts() == [dict(zip(out["columns"], r)) for r in out["rows"]]
    assert "clean_data(orders)\n" in preview(steps, pl.from_arrow(TABLE))["code"]


def test_typed_values_reach_the_code_only_as_literals():
    nasty = "x') or __import__('os').system('echo hi') or ('"
    out = preview([{"op": "filter", "column": "name", "test": "eq", "value": nasty},
                   {"op": "rename", "column": "id", "to": nasty}])
    assert out["error"] is None
    assert out["rows_after"] == 0
    assert nasty in out["columns"]
    assert f"pl.col('name') == {nasty!r}" in out["code"]


def test_a_step_that_cant_run_says_which_and_keeps_the_steps_before_it():
    out = preview([{"op": "dedupe"}, {"op": "fill", "columns": ["id"], "value": "lots"}, {"op": "drop", "columns": ["id"]}])
    assert out["error"] == {"step": 1, "message": "'lots' isn't a number."}
    assert out["rows_after"] == 3
    assert "unique" in out["code"] and "fill_null" not in out["code"]
    assert preview([{"op": "drop", "columns": ["nope"]}])["error"]["message"] == "There's no column 'nope' at this step."
    assert "isn't text" in preview([{"op": "trim", "columns": ["id"]}])["error"]["message"]
    assert "already a column" in preview([{"op": "rename", "column": "id", "to": "name"}])["error"]["message"]
    assert "Unknown step" in preview([{"op": "eval"}])["error"]["message"]


def test_a_name_must_be_a_variable():
    for bad in ("1x", "class", "a b", "x;y"):
        with pytest.raises(ValueError):
            preview([], name=bad)
    assert preview([], name="")["code"].endswith("df_clean = clean_data(pl.from_arrow(df))\ndisplay(df_clean)\n")


def test_dates_compare_and_fill_as_dates():
    table = pa.table({"d": [dt.date(2024, 1, 1), dt.date(2024, 3, 1), None]})
    out = wrangler.run(table, table, [{"op": "fill", "columns": ["d"], "value": "2020-01-01"},
                                      {"op": "filter", "column": "d", "test": "lt", "value": "2024-02-01"}], "t")
    assert out["error"] is None
    assert out["rows"] == [["2024-01-01"], ["2020-01-01"]]


def test_large_results_preview_the_first_million_rows(monkeypatch):
    monkeypatch.setattr(wrangler, "WRANGLE_ROWS", 3)
    out = preview([])
    assert out["sampled"] is True and out["rows_before"] == 3


def test_the_kernel_previews_a_displayed_result():
    k = Kernel()
    run = k.run("import polars as pl\norders = pl.DataFrame({'a': [1, 1, None]})\ndisplay(orders)")
    fid = next(o for o in run["outputs"] if o.get("type") == "table")["frame"]
    out = k.frame_wrangle(fid, [{"op": "dedupe"}, {"op": "drop_missing"}], "orders")
    assert out["rows_after"] == 1 and out["frame"] == fid
    assert "clean_data(orders)\n" in out["code"]


def test_each_source_is_read_with_code_that_also_runs_in_fabric_and_databricks():
    import duckdb
    from duckdb.experimental.spark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    assert wrangler.reader(spark.createDataFrame([(1,)], ["a"]), "x") == "pl.from_pandas(x.toPandas())"
    assert wrangler.reader(duckdb.sql("SELECT 1"), "x") == "x.pl()"
    assert wrangler.reader(pl.DataFrame().lazy(), "x") == "x.collect()"
    pd = pytest.importorskip("pandas")
    assert wrangler.reader(pd.DataFrame(), "x") == "pl.from_pandas(x)"


def test_frames_wrangle_raises_gone_for_a_dropped_result():
    from fabric_desktop.frames import FrameGone
    with pytest.raises(FrameGone):
        Frames().wrangle("missing", [])


def test_service_wrangle_after_restart_asks_to_run_again():
    from fabric_desktop.service import Service
    svc = Service.__new__(Service)
    svc.kernel = Kernel()
    run = svc.kernel.run("import polars as pl\npl.DataFrame({'a': [1, 1]})")
    fid = next(o for o in run["outputs"] if o.get("type") == "table")["frame"]
    assert svc.notebook_frame_wrangle(fid, [{"op": "dedupe"}])["rows_after"] == 1
    svc.kernel.restart()
    assert svc.notebook_frame_wrangle(fid, [])["gone"]


def test_a_column_name_with_a_newline_does_not_become_code(tmp_path):
    # Quality's #427 probe: a name from the data must never end the step's "# ..." comment.
    marker = tmp_path / "pwned"
    evil = f"x\n    open({str(marker)!r}, 'w').write('1')"
    table = pa.table({evil: [1, 2], "y": [3, 4]})
    for step in ({"op": "drop", "columns": [evil]}, {"op": "rename", "column": evil, "to": "z"},
                 {"op": "sort", "column": evil}, {"op": "filter", "column": evil, "test": "gt", "value": "1"}):
        out = wrangler.run(table, pl.from_arrow(table), [step], "df")
        code = out["code"]
        exec(compile(code, "<cell>", "exec"), {"df": pl.from_arrow(table), "display": lambda v: None})  # noqa: S102
        assert not marker.exists(), "column name ran as code when the generated cell ran:\n" + code
        assert all(not ln.startswith("    open(") for ln in code.splitlines())
    assert wrangler.describe_step({"op": "drop", "columns": ["a\nb"]}) == "Drop columns: 'a\\nb'"
