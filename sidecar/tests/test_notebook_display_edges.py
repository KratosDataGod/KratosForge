"""How a notebook cell's last value shows (notebook.py display and its rich views), the paths the main
tests don't reach: lazy and DuckDB frames, values that only look like frames, and rich views that are
broken, take no arguments, or carry something JSON can't hold. Each still shows something."""

from __future__ import annotations

import base64
import logging

import duckdb
import polars as pl

from fabric_desktop import notebook as nb


def _from(module):
    """A class whose values look as if they came from ``module``."""
    return type("Value", (), {"__module__": module})


def test_a_lazy_polars_frame_shows_as_a_table():
    out = nb.display(pl.LazyFrame({"a": [1, 2]}))
    assert out["type"] == "table" and out["rows"] == [[1], [2]] and out["total_rows"] == 2


def test_a_duckdb_relation_shows_as_a_table_without_running_it_again_to_count():
    # DuckDB's relations live in the _duckdb module: they showed as box-drawn text before.
    out = nb.display(duckdb.sql(f"SELECT range AS n FROM range({nb.PREVIEW_ROWS + 5})"))
    assert out["type"] == "table" and out["columns"] == ["n"]
    assert len(out["rows"]) == nb.PREVIEW_ROWS and out["total_rows"] == f"more than {nb.PREVIEW_ROWS}"
    assert nb.display(duckdb.sql("SELECT 1 AS n"))["total_rows"] == 1


def test_a_spark_value_that_isnt_a_frame_isnt_a_table():
    assert nb.as_table(_from("duckdb.experimental.spark.sql.column")()) is None
    assert nb.as_table(_from("polars.expr")()) is None


def test_a_frame_with_no_length_counts_what_the_preview_holds():
    table = pl.DataFrame({"a": [1]}).to_arrow()
    assert nb._total_rows(object(), table) == 1


class Bundle:
    def __init__(self, bundle=None, fails=False, plain=False):
        self.bundle, self.fails, self.plain = bundle, fails, plain

    def _repr_mimebundle_(self, *args, **kwargs):
        if self.fails:
            raise RuntimeError("the renderer broke")
        if self.plain and kwargs:
            raise TypeError("takes no arguments")
        return self.bundle, {}

    def _repr_markdown_(self):
        return ("**bold**", {"isolated": True})


def test_a_bundle_method_that_takes_no_arguments_is_called_without_them():
    assert nb._mime_bundle(Bundle({"text/html": "<b>x</b>"}, plain=True))["text/html"] == "<b>x</b>"


def test_a_broken_bundle_falls_back_to_the_other_views(caplog):
    with caplog.at_level(logging.INFO, logger=nb._log.name):
        bundle = nb._mime_bundle(Bundle(fails=True))
    assert bundle == {"text/markdown": "**bold**"}  # a (data, metadata) tuple gives its data
    assert "_repr_mimebundle_ failed" in caplog.text


def test_each_view_that_isnt_html_or_an_image_still_shows():
    png = b"\x89PNG not really"
    assert nb._from_bundle({"image/png": png}) == {"type": "image", "mime": "image/png",
                                                  "data": base64.b64encode(png).decode()}
    assert nb._from_bundle({"text/latex": r"$x^2$"}) == {"type": "text", "text": r"$x^2$"}
    assert nb._from_bundle({"application/json": {"when": object}})["type"] == "json"  # str() of what JSON lacks
    circular = []
    circular.append(circular)
    assert nb._from_bundle({"application/json": circular})["type"] == "text"
    assert nb._from_bundle({"text/plain": ""}) is None
