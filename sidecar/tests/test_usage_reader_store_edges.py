"""Usage imports: the reader's refusals and the local store's edges the main tests don't reach.
Files go to a temporary folder; nothing is sent anywhere."""

import csv
import datetime as dt

import pytest

from fabric_desktop.usage import reader
from fabric_desktop.usage.store import Store

NOW = dt.datetime(2026, 11, 11, 8, 0, tzinfo=dt.timezone.utc)


def test_a_file_over_the_size_limit_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(reader, "MAX_BYTES", 10)
    with pytest.raises(reader.FileProblem, match="larger than"):
        reader.table("a,b\n1,2\n3,4\n")


def test_csv_that_cant_be_parsed_or_has_only_blank_rows_is_refused(monkeypatch):
    class Broken:
        def __init__(self, *a, **k):
            pass

        def __iter__(self):
            raise csv.Error("new-line character seen in unquoted field")

    monkeypatch.setattr(reader.csv, "reader", Broken)
    with pytest.raises(reader.FileProblem, match="isn't valid CSV: new-line"):
        reader.table("a,b\n1,2\n")
    monkeypatch.undo()
    with pytest.raises(reader.FileProblem, match="has no rows"):
        reader.table(" , \n ,\n")


def test_a_file_with_too_many_rows_is_refused(monkeypatch):
    monkeypatch.setattr(reader, "MAX_ROWS", 2)
    with pytest.raises(reader.FileProblem, match="more than 2 rows"):
        reader.table("a\n1\n2\n3\n")


def test_dotted_thousands_and_short_decimals_settle_the_mark():
    assert reader.decimal_mark(["1.234.567"]) == ","
    assert reader.decimal_mark(["1.5", "2.25"]) == "."
    with pytest.raises(reader.NeedsChoice) as asked:
        reader.decimal_mark(["1.234"])  # one thousand, or one point two three four
    assert asked.value.option == "decimal"


def test_a_number_the_pattern_allows_but_float_cant_read_is_none():
    assert reader.number("1..2", ".") is None
    assert reader.number("1" * 400, ".") is None  # infinity is not an amount


def test_dates_that_mix_day_first_and_month_first_are_refused():
    with pytest.raises(reader.FileProblem, match="mix day-first and month-first"):
        reader.date_order(["13/01/2026", "01/13/2026"])


def test_an_am_pm_time_with_a_24_hour_clock_hour_is_not_a_time():
    assert reader.when("01/02/2026 13:00 PM", "mdy", dt.timezone.utc) is None
    assert reader.when("01/02/2026 01:00 PM", "mdy", dt.timezone.utc) == dt.datetime(2026, 1, 2, 13, tzinfo=dt.timezone.utc)


def _preview(sha="0123456789abcdef" * 4):
    return {"sha256": sha, "provider": {"id": "fabric", "label": "Fabric"}, "layout": {"id": "l", "label": "L"},
            "file_name": "usage.csv", "options": {}, "period": {}, "workspaces": [], "total": 1, "skipped": 0,
            "notes": [], "rows": [{"key": "k1", "slot": "s1"}, {"key": "k2"}]}


def test_the_store_refuses_bad_ids_and_a_second_import_of_the_same_file(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="isn't an import id"):
        store.delete("../evil")
    saved = store.save(_preview(), NOW)
    assert saved["id"] == "u-0123456789abcdef" and saved["rows"] == 2
    with pytest.raises(ValueError, match="already imported"):
        store.save(_preview(), NOW)
    assert store.keys() == {"k1", "k2"} and store.slots() == {"s1"}


def test_an_unreadable_import_is_skipped_and_a_missing_one_isnt_deleted(tmp_path):
    store = Store(tmp_path)
    store.save(_preview(), NOW)
    (store.folder / "u-ffffffffffffffff.json").write_text("{not json", encoding="utf-8")
    assert [s["id"] for s in store.summaries()] == ["u-0123456789abcdef"]
    assert store.delete("u-ffffffffffffffff") is True
    assert store.delete("u-ffffffffffffffff") is False
    assert store.delete("u-0123456789abcdef") is True and store.all() == []
