"""Result frame edges (frames.py) the main tests don't reach: the less common table kinds, charts
missing a column they need, a download format that isn't offered, and Excel cells for the values a
spreadsheet can't hold as written."""

import datetime as dt
import decimal

import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop import frames
from fabric_desktop.frames import Frames


def test_a_lazy_polars_frame_becomes_arrow():
    assert frames.full_table(pl.LazyFrame({"a": [1, 2]})).column("a").to_pylist() == [1, 2]


def test_a_duckdb_spark_frame_becomes_arrow():
    from duckdb.experimental.spark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    df = spark.createDataFrame([(1,), (2,)], ["a"])
    assert frames.full_table(df).column("a").to_pylist() == [1, 2]


def test_a_value_that_isnt_a_table_is_refused():
    with pytest.raises(TypeError, match="dict isn't a table"):
        frames.full_table({"a": [1]})


@pytest.mark.parametrize("t, name", [(pa.binary(), "binary"), (pa.large_binary(), "binary"),
                                     (pa.map_(pa.string(), pa.int64()), "map"), (pa.null(), "null"),
                                     (pa.time64("us"), "time"), (pa.large_list(pa.int64()), "array")])
def test_type_labels_for_the_less_common_types(t, name):
    assert frames.type_name(t) == name


@pytest.mark.parametrize("t, kind", [(pa.large_string(), "text"), (pa.bool_(), "boolean"), (pa.binary(), "other"),
                                     (pa.decimal128(10, 2), "number"), (pa.date32(), "date")])
def test_column_kinds_for_charts_and_profiles(t, kind):
    assert frames._kind(t) == kind


@pytest.fixture
def held():
    f = Frames()
    fid = f.keep(pa.table({"name": ["a", "b"], "n": [1, 2], "flag": [True, False]}))
    return f, fid


@pytest.mark.parametrize("kind, kw, message", [
    ("histogram", {}, "Pick a number column for Values"),
    ("scatter", {"x": 1}, "Pick a number column for Values"),
    ("box", {"x": 0}, "Pick a number column for Values"),
    ("bar", {"y": 1}, "Pick a column for the X axis"),
    ("scatter", {"x": 0, "y": 1}, "'name' isn't a number column. Pick a number column for the X axis."),
    ("bar", {"x": 0, "y": 0, "agg": "sum"}, "'name' isn't a number column. Pick a number column for Values, or count rows."),
])
def test_a_chart_missing_a_column_it_needs_says_which(held, kind, kw, message):
    f, fid = held
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
        f.chart(fid, kind, **kw)


def test_a_true_false_column_can_be_summed_in_a_bar_chart(held):
    f, fid = held
    out = f.chart(fid, "bar", x=0, y=2, agg="sum")
    assert out["categories"] == ["a", "b"] and out["series"][0]["values"] == [1, 0]


def test_a_download_format_that_isnt_offered_is_refused(held, tmp_path):
    f, fid = held
    with pytest.raises(ValueError, match="Download as"):
        f.download(fid, "pdf", str(tmp_path / "x.pdf"))


def test_removing_a_file_never_written_is_quiet(tmp_path):
    frames.with_suppressed_unlink(tmp_path / "never-written.csv")


def test_a_repeated_name_skips_a_number_already_used():
    assert frames._unique(["a", "a_2", "A", "a"]) == ["a", "a_2", "A_3", "a_4"]


def test_excel_keeps_fifteen_digits():
    assert frames._excel_exact(0.1) is True and frames._excel_exact(123) is True
    assert frames._excel_exact(10 ** 15) is False
    assert frames._excel_exact(decimal.Decimal("1.50")) is True
    assert frames._excel_exact(decimal.Decimal("1234567890.1234567")) is False


@pytest.mark.parametrize("value, expected", [
    (None, ""),
    (True, '<c r="A1" t="b"><v>1</v></c>'),
    (float("inf"), "inf"),
    (float("nan"), "nan"),
    (1234567890123456789, "1234567890123456789"),
    (dt.datetime(1899, 1, 1), "1899-01-01 00:00:00"),
    (dt.date(1899, 1, 1), "1899-01-01"),
])
def test_excel_cells_for_values_a_spreadsheet_cant_hold_as_numbers_or_dates(value, expected):
    cell = frames._xlsx_cell("A1", value)
    assert (cell == expected) if not expected or expected.startswith("<") else (expected in cell and "inlineStr" in cell)


def test_an_aware_datetime_goes_to_excel_as_its_wall_clock_time():
    aware = dt.datetime(2026, 11, 11, 6, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    naive = dt.datetime(2026, 11, 11, 6, 0)
    assert frames._xlsx_cell("A1", aware) == frames._xlsx_cell("A1", naive)
    assert 's="2"' in frames._xlsx_cell("A1", naive)
    early = frames._xlsx_cell("A1", dt.datetime(1900, 2, 1))
    assert early == f'<c r="A1" s="2"><v>{(dt.datetime(1900, 2, 1) - frames._EPOCH).total_seconds() / 86400 - 1}</v></c>'
