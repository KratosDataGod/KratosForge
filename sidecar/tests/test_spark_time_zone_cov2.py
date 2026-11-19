"""Spark's session time zone on this PC (spark_time_zone.py): the offsets written as zone ids, a zone this
PC's tz database doesn't know, intervals that are negative or not whole, and date_trunc rewritten once with
the type it had."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from fabric_desktop import spark_complex
from fabric_desktop import spark_time_zone as stz


@pytest.fixture
def shifting(monkeypatch):
    """A session in America/Chicago, whose clocks change, and no other test's sessions."""
    monkeypatch.setattr(stz, "_SHIFTING", {})
    session = object()
    stz.session_zone(session, "America/Chicago")
    yield session
    stz.session_zone(session, None)


@pytest.mark.parametrize("zone, duckdb_zone", [
    ("Z", "UTC"), (" ut ", "UTC"), ("z", "UTC"),
    ("+00", "UTC"), ("GMT-0", "UTC"), ("UTC+00:00", "UTC"),
    ("UTC+15", "UTC+15"), ("-13", "-13"),  # past Etc/GMT's offsets: DuckDB's to refuse
    ("+14", "Etc/GMT-14"), ("-12", "Etc/GMT+12"),
])
def test_offsets_as_zone_ids(zone, duckdb_zone):
    assert stz.zone_id(zone) == duckdb_zone


def test_an_offset_with_seconds_is_refused_naming_it():
    with pytest.raises(ValueError, match=r"^The time zone '\+01:00:30' can't be used on this PC"):
        stz.zone_id("+01:00:30")


@pytest.mark.parametrize("zone", ["No/Such_Zone", "../../etc/passwd", ""])
def test_a_zone_this_pc_does_not_know_counts_as_one_that_moves_by_an_hour_or_half(zone):
    stz.gaps.cache_clear()
    assert stz.gaps(zone) == (3600, 1800) and stz.shifts(zone)


def interval_in(sql):
    tree = sqlglot.parse_one(sql, read="duckdb")
    return tree, tree.find(exp.Interval)


def test_a_negative_interval_of_hours_is_split_into_days_and_the_rest_toward_zero(monkeypatch):
    monkeypatch.setattr(stz, "_SHIFTING", {})
    tree, interval = interval_in("SELECT TIMESTAMP '2026-03-10 12:00:00' + INTERVAL 1 HOUR AS t")
    interval.set("this", exp.Neg(this=exp.Literal.number(25)))  # INTERVAL -25 HOURS
    interval.set("unit", exp.Var(this="HOURS"))
    text = stz.fix(tree).sql("duckdb")
    assert "INTERVAL '-1' DAY" in text and "INTERVAL '-3600' SECOND" in text
    # Its own connection: DuckDB's default one is shared with every test in this process.
    con = duckdb.connect()
    try:
        assert con.execute(text).fetchone()[0].isoformat() == "2026-03-09T11:00:00"
    finally:
        con.close()


def test_an_interval_that_is_not_a_whole_number_is_left_to_duckdb(monkeypatch):
    monkeypatch.setattr(stz, "_SHIFTING", {})
    tree, _ = interval_in("SELECT TIMESTAMP '2026-03-10 12:00:00' + INTERVAL '36.5' HOUR AS t")
    assert stz.fix(tree).sql("duckdb") == "SELECT CAST('2026-03-10 12:00:00' AS TIMESTAMP) + INTERVAL '36.5' HOUR AS t"


def test_a_truncation_keeps_its_type_and_is_rewritten_only_once(shifting):
    tree = sqlglot.parse_one("SELECT date_trunc('hour', ts) AS h FROM t", read="duckdb")
    trunc = tree.find(exp.TimestampTrunc, exp.DateTrunc)
    trunc.type = exp.DataType.build("TIMESTAMPTZ")
    once = stz.fix(tree).sql("duckdb")
    wrapper = tree.selects[0].this
    assert wrapper.type.this == exp.DataType.Type.TIMESTAMPTZ and wrapper.meta.get(spark_complex.TYPED)
    assert stz.fix(tree).sql("duckdb") == once  # the truncation inside is marked done
    assert once.count("kf_d") >= 1 and once.count("DATE_TRUNC") == 1


def test_a_truncation_of_unknown_type_is_not_typed(shifting):
    tree = sqlglot.parse_one("SELECT date_trunc('hour', ts) AS h FROM t", read="duckdb")
    stz.fix(tree)
    assert not tree.selects[0].this.meta.get(spark_complex.TYPED)
