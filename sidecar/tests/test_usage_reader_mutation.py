"""Mutation-testing follow-ups for usage/reader.py (docs/quality/mutation-testing.md, batch 38).

These use the module's real limits (not monkeypatched ones), so a changed limit or an off-by-one at
the edge fails here.
"""

import datetime as dt

import pytest

from fabric_desktop.usage import reader


def test_a_file_of_exactly_the_size_limit_is_not_refused_for_its_size():
    assert reader.MAX_BYTES == 64 * 1024 * 1024 == 67_108_864
    text = "a\n" + "x" * (reader.MAX_BYTES - 2)
    assert len(text.encode("utf-8")) == reader.MAX_BYTES
    with pytest.raises(reader.FileProblem) as exc:  # one huge cell is bad CSV, but the size is fine
        reader.table(text)
    assert str(exc.value).startswith("The file isn't valid CSV")


def test_one_byte_over_the_limit_is_refused_and_named_in_megabytes():
    with pytest.raises(reader.FileProblem) as exc:
        reader.table("a\n" + "x" * (reader.MAX_BYTES - 1))
    assert str(exc.value) == "The file is larger than 64 MB. Export a shorter period."


def test_exactly_the_row_limit_is_read_and_one_more_is_refused():
    headers, rows = reader.table("v\n" + "1\n" * reader.MAX_ROWS)
    assert headers == ["v"] and len(rows) == reader.MAX_ROWS == 250_000
    with pytest.raises(reader.FileProblem) as exc:
        reader.table("v\n" + "1\n" * (reader.MAX_ROWS + 1))
    assert str(exc.value) == "The file has more than 250,000 rows. Export a shorter period."


def test_an_excel_workbook_is_named_as_one():
    with pytest.raises(reader.FileProblem) as exc:
        reader.table("PK\x03\x04rest,of,the,zip\n1,2,3,4\n")
    assert "Excel workbook" in str(exc.value)


def test_the_separator_comes_from_the_header_line_only():
    headers, rows = reader.table("a;b\n1,5;2,5,7,8,9\n")
    assert headers == ["a", "b"] and rows == [["1,5", "2,5,7,8,9"]]


def test_exponent_notation_is_not_a_number_here():
    assert reader.number("1e5", None) is None
    assert reader.number("12", None) == 12.0


def test_utc_is_the_utc_zone():
    assert reader.zone("UTC") is dt.timezone.utc


@pytest.mark.parametrize("offset, minutes", [("+14:00", 14 * 60), ("-14:00", -14 * 60), ("+02:30", 150),
                                             ("+05:59", 5 * 60 + 59)])
def test_offsets_up_to_fourteen_hours_and_59_minutes_are_read(offset, minutes):
    assert reader.zone(offset).utcoffset(None) == dt.timedelta(minutes=minutes)


@pytest.mark.parametrize("offset", ["+15:00", "+01:60", "-15"])
def test_offsets_past_fourteen_hours_or_59_minutes_are_refused(offset):
    with pytest.raises(reader.FileProblem):
        reader.zone(offset)


def test_twelve_as_the_second_number_still_reads_both_ways():
    with pytest.raises(reader.NeedsChoice):
        reader.date_order(["01/12/2026"])
    assert reader.date_order(["01/13/2026"]) == "mdy"
    assert reader.date_order(["13/01/2026"]) == "dmy"


def test_a_short_row_gives_empty_cells_for_the_missing_columns():
    assert reader.cells(["a", "b"], [["1"]], {"a": 0, "b": 1}) == [{"a": "1", "b": ""}]
