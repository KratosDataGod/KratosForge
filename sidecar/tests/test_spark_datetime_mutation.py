"""Spark's datetime patterns on this PC: what mutation testing (batch 83, docs/quality/mutation-testing.md)
showed no test checked. The most letters each pattern letter takes, the letters refused by name, the
padded day and day of the year, the localized and ISO offsets in every width, an empty pattern, the lenient
default, and which patterns can be read back. The SQL runs in an in-memory DuckDB; nothing else."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import spark_datetime as sd

# The most letters java.time takes in a run of each (O has its own rule, below).
MOST = {"d": 2, "D": 3, "a": 1, "h": 2, "K": 2, "k": 2, "H": 2, "m": 2, "s": 2, "S": 9, "X": 5, "x": 5, "Z": 5}
# Runs Spark stops on before java.time sees them: five of a text letter, seven or more y.
SPARK_MOST = {"G": 4, "M": 4, "L": 4, "Q": 4, "q": 4, "E": 4, "y": 6}


@pytest.mark.parametrize(("letter", "most"), sorted(MOST.items()))
def test_each_letter_takes_up_to_its_most_and_no_more(letter, most):
    assert sd.tokens(letter * most) == [("letter", letter * most)]
    with pytest.raises(ValueError, match=f"too many pattern letters: {letter}\\."):
        sd.tokens(letter * (most + 1))


@pytest.mark.parametrize(("letter", "most"), sorted(SPARK_MOST.items()))
def test_spark_stops_on_five_text_letters_and_seven_years(letter, most):
    assert sd.tokens(letter * most) == [("letter", letter * most)]
    for n in (most + 1, 99):
        with pytest.raises(ValueError, match=f'Spark stops on it too \\("Too many pattern letters: {letter}"\\)\\.'):
            sd.tokens(letter * n)


@pytest.mark.parametrize("run", ["OO", "OOO", "OOOOO"])
def test_the_localized_offset_is_o_or_oooo_only(run):
    with pytest.raises(ValueError, match="the offset is written O or OOOO\\."):
        sd.tokens(run)


@pytest.mark.parametrize("letter", ["A", "Z", "a", "z"])
def test_the_first_and_last_letters_are_read_as_letters(letter):
    if letter == "A":
        with pytest.raises(ValueError, match="Spark refuses this letter"):
            sd.tokens(letter)
    elif letter == "z":  # a zone's short name; its long name (zzzz) is refused
        assert sd.tokens(letter) == [("letter", letter)]
        with pytest.raises(ValueError, match="time zone names"):
            sd.tokens(letter * 4)
    else:
        assert sd.tokens(letter) == [("letter", letter)]


def fmt(pattern: str, zone: str = "UTC", at: str = "2026-01-05 13:04:05+00") -> str:
    con = duckdb.connect()
    try:
        con.execute(f"SET TimeZone = '{zone}'")
        return con.execute(f"SELECT {sd.format_sql(pattern)} FROM (SELECT TIMESTAMPTZ '{at}' AS fj_arg0)").fetchone()[0]
    finally:
        con.close()


def test_the_day_and_the_day_of_the_year_are_padded_to_the_letters_given():
    assert (fmt("d"), fmt("dd")) == ("5", "05")
    assert (fmt("D"), fmt("DD"), fmt("DDD")) == ("5", "05", "005")
    assert fmt("dd", at="2026-01-25 13:04:05+00") == "25"


def test_one_to_four_era_letters_give_the_short_or_full_era():
    assert (fmt("G"), fmt("GG"), fmt("GGGG")) == ("AD", "AD", "Anno Domini")


def test_localized_offsets_short_and_full():
    assert fmt("O", "Asia/Kolkata") == "GMT+5:30"
    assert fmt("OOOO", "Asia/Kolkata") == "GMT+05:30"
    assert fmt("OOOO") == "GMT"


def test_iso_offsets_in_every_width():
    got = {p: fmt(p, "Asia/Kolkata") for p in ("X", "XX", "XXX", "XXXX", "XXXXX", "x", "xxx", "xxxx")}
    assert got == {"X": "+0530", "XX": "+0530", "XXX": "+05:30", "XXXX": "+0530", "XXXXX": "+05:30",
                   "x": "+0530", "xxx": "+05:30", "xxxx": "+0530"}
    assert fmt("X") == "Z" and fmt("x") == "+00"


def test_five_zone_letters_give_the_iso_offset_with_z_for_zero():
    assert fmt("ZZZZZ", "Asia/Kolkata") == "+05:30"
    assert fmt("ZZZZZ") == "Z"
    assert fmt("ZZZZ", "Asia/Kolkata") == "GMT+05:30"
    assert fmt("Z", "Asia/Kolkata") == "+0530"


def test_an_empty_pattern_gives_empty_text():
    assert fmt("") == ""


def test_formatting_is_lenient_unless_asked_to_be_strict():
    assert "TRY_CAST(fj_arg0 AS TIMESTAMPTZ)" in sd.format_sql("yyyy")
    assert "TRY_CAST" not in sd.format_sql("yyyy", strict=True)


def test_reading_takes_a_long_standalone_month_and_a_long_year():
    assert sd.parse_format("LLLL", "to_date")[0] == "%B"
    assert sd.parse_format("yyyy", "to_date")[:2] == ("%Y", True)
    with pytest.raises(ValueError, match="to_date can't read 'LLLLL'"):
        sd.parse_format("LLLLL", "to_date")


def test_reading_takes_an_offset_of_up_to_three_letters():
    assert sd.parse_format("XXX", "to_timestamp")[:3] == ("%z", False, True)
    for run in ("XXXX", "xxxx", "ZZZZ"):
        with pytest.raises(ValueError, match=f"to_timestamp can't read '{run}'"):
            sd.parse_format(run, "to_timestamp")
