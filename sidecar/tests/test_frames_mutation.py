"""Result viewer paths that mutation testing (batch 13, docs/quality/mutation-testing.md) showed no test
checked: page and profile sizes, the Excel row limit, chart aggregates and order, the Arrow table cache,
and the small Excel writer's dates, digits, columns, text and rows."""

from __future__ import annotations

import datetime as dt
import decimal
import io
import zipfile

import pyarrow as pa
import pytest

from fabric_desktop import frames
from fabric_desktop.frames import Frames


def held(table):
    f = Frames()
    return f, f.keep(table)


def test_a_page_holds_a_hundred_rows_unless_asked_for_another_size():
    f, fid = held(pa.table({"n": list(range(150))}))
    page = f.page(fid)
    assert len(page["rows"]) == frames.PAGE_ROWS == 100 and page["rows"][-1] == [99]
    assert len(f.page(fid, limit=5000)["rows"]) == 150


def test_a_frame_id_is_twelve_hex_characters():
    fid = Frames().keep(pa.table({"n": [1]}))
    assert len(fid) == 12 and int(fid, 16) >= 0


def test_the_arrow_table_is_made_once_and_reused(monkeypatch):
    made = []
    real = frames.full_table

    def counting(value):
        made.append(value)
        return real(value)
    monkeypatch.setattr(frames, "full_table", counting)
    f = Frames()
    fid = f.keep(pa.table({"n": [1, 2]}))
    assert f.table(fid) is f.table(fid)
    assert len(made) == 1


def test_an_excel_download_over_a_sheets_rows_is_refused_before_writing(tmp_path, monkeypatch):
    def written(*a, **k):
        raise AssertionError("the sheet was written")
    monkeypatch.setattr(frames, "write_xlsx", written)
    f, fid = held(pa.table({"n": pa.array(range(1_048_576), pa.int32())}))
    with pytest.raises(ValueError, match="at most 1,048,575 rows and this result has 1,048,576"):
        f.download(fid, "xlsx", str(tmp_path / "big.xlsx"))
    assert list(tmp_path.iterdir()) == []


def test_a_profile_covers_two_hundred_columns_and_says_how_many_more():
    f, fid = held(pa.table({f"c{i}": [i] for i in range(201)}))
    got = f.profile(fid)
    assert len(got["columns"]) == 200 and got["more_columns"] == 1


def test_a_profile_samples_only_above_a_quarter_million_rows():
    f, fid = held(pa.table({"n": pa.array(range(250_000), pa.int32())}))
    got = f.profile(fid)
    assert got["sampled"] is False and got["profiled_rows"] == 250_000
    f, fid = held(pa.table({"n": pa.array(range(250_001), pa.int32())}))
    got = f.profile(fid)
    assert got["sampled"] is True and got["profiled_rows"] == 250_000 and got["rows"] == 250_001


def test_a_number_columns_histogram_has_twelve_bins():
    f, fid = held(pa.table({"n": list(range(24))}))
    (col,) = f.profile(fid)["columns"]
    assert col["histogram"] == [2] * 12


@pytest.mark.parametrize("t, name", [(pa.bool_(), "boolean"), (pa.struct([("a", pa.int64())]), "struct")])
def test_type_names_for_true_false_and_struct_columns(t, name):
    assert frames.type_name(t) == name


def test_a_nan_category_is_its_own_chart_key():
    assert frames._chart_key(float("nan")) == ("nan",)
    assert frames._chart_key(float("nan")) != frames._chart_key(None)


def test_a_bar_chart_keeps_the_largest_first_and_a_line_chart_keeps_x_order():
    f, fid = held(pa.table({"x": ["a", "b", "c"], "y": [1.0, 5.0, 3.0]}))
    assert f.chart(fid, "bar", x=0, y=1)["categories"] == ["b", "c", "a"]
    assert f.chart(fid, "line", x=0, y=1)["categories"] == ["a", "b", "c"]


def test_a_line_chart_puts_a_missing_x_last():
    f, fid = held(pa.table({"x": [2, None, 1], "y": [1.0, 1.0, 1.0]}))
    assert f.chart(fid, "line", x=0, y=1)["categories"] == [1, 2, None]


def test_a_sum_of_a_decimal_number_column_keeps_its_fractions():
    f, fid = held(pa.table({"x": ["a", "a"], "y": [1.4, 1.4]}))
    (s,) = f.chart(fid, "bar", x=0, y=1, agg="sum")["series"]
    assert s["values"] == [pytest.approx(2.8)]


def test_count_counts_rows_even_where_the_value_is_missing_or_no_value_is_picked():
    f, fid = held(pa.table({"x": ["a", "a", "b"], "y": [1.0, None, 2.0]}))
    (s,) = f.chart(fid, "bar", x=0, y=1, agg="count")["series"]
    assert dict(zip(f.chart(fid, "bar", x=0, y=1, agg="count")["categories"], s["values"], strict=True)) == {"a": 2, "b": 1}
    (s,) = f.chart(fid, "bar", x=0, agg="sum")["series"]
    assert dict(zip(f.chart(fid, "bar", x=0, agg="sum")["categories"], s["values"], strict=True)) == {"a": 2, "b": 1}


def test_a_few_series_chart_without_an_other_slot():
    f, fid = held(pa.table({"x": ["a", "a", "b"], "y": [1.0, 2.0, 3.0], "s": ["p", "q", "p"]}))
    got = f.chart(fid, "bar", x=0, y=1, series=2)
    assert [s["name"] for s in got["series"]] == ["p", "q"]
    by = {s["name"]: dict(zip(got["categories"], s["values"], strict=True)) for s in got["series"]}
    assert by == {"p": {"a": 1.0, "b": 3.0}, "q": {"a": 2.0, "b": None}}


def test_a_histogram_bins_by_the_values_range():
    f, fid = held(pa.table({"y": [0.0, 2.0, 4.0, 10.0]}))
    got = f.chart(fid, "histogram", y=0, bins=2)
    assert got["counts"] == [3, 1] and (got["min"], got["max"]) == (0.0, 10.0)


@pytest.mark.parametrize("day, serial", [(dt.date(1900, 2, 28), 59), (dt.date(1900, 3, 1), 61),
                                         (dt.date(1900, 4, 1), 92), (dt.date(1901, 3, 1), 426)])
def test_excel_dates_count_the_1900_leap_day_that_never_was(day, serial):
    assert f"<v>{serial}</v>" in frames._xlsx_cell("A2", day)
    moment = dt.datetime(day.year, day.month, day.day)
    assert f"<v>{float(serial)}</v>" in frames._xlsx_cell("A2", moment)


def test_a_decimal_of_more_than_fifteen_digits_goes_to_excel_as_text():
    assert frames._xlsx_cell("A2", decimal.Decimal("123456789012345")) == '<c r="A2"><v>123456789012345</v></c>'
    assert 't="inlineStr"' in frames._xlsx_cell("A2", decimal.Decimal("1234567890123456"))


@pytest.mark.parametrize("n, letters", [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (701, "ZZ"), (702, "AAA")])
def test_excel_column_letters(n, letters):
    assert frames._col(n) == letters


def test_excel_text_keeps_tabs_and_line_breaks_and_drops_other_control_characters():
    cell = frames._text("A1", "a\tb\nc\rd\x0be\x0cf")
    assert "a\tb\nc\rdef" in cell


def test_each_excel_row_gets_its_own_number():
    sink = io.BytesIO()
    frames.write_xlsx(pa.table({"n": [1, 2, 3]}), sink)
    with zipfile.ZipFile(io.BytesIO(sink.getvalue())) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert [f'<row r="{r}">' in sheet for r in (1, 2, 3, 4)] == [True] * 4
    assert sheet.count("<row ") == 4
