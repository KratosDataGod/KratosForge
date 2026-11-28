"""spark_api paths a re-score (batch 508) showed no test checked: unsetting one key keeps the session's
time zone, the ANSI note only for true, a negative randomSplit weight, a plain name in colRegex, and
sampleBy with no fraction for any value."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("kernel")
    mp.setenv("LOCALAPPDATA", str(home))
    mp.setenv("HOME", str(home))
    yield Kernel().namespace["spark"]
    mp.undo()


def test_unsetting_another_key_keeps_the_time_zone(spark):
    spark.conf.set("spark.sql.session.timeZone", "America/New_York")
    try:
        spark.conf.set("kf.other", "1")
        spark.conf.unset("kf.other")
        assert spark.sql("SELECT current_timezone() AS z").collect()[0].z == "America/New_York"
        assert spark.conf.get("spark.sql.session.timeZone") == "America/New_York"
    finally:
        spark.conf.unset("spark.sql.session.timeZone")
    assert spark.sql("SELECT current_timezone() AS z").collect()[0].z == "UTC"


def test_the_ansi_note_is_printed_only_for_true(spark, capsys):
    from fabric_desktop.spark_api import ANSI_NOTE

    spark.conf.set("spark.sql.ansi.enabled", "false")
    assert ANSI_NOTE not in capsys.readouterr().out
    spark.conf.set("spark.sql.ansi.enabled", " TRUE ")
    assert ANSI_NOTE in capsys.readouterr().out
    spark.conf.set("spark.sql.ansi.enabled", "false")


def test_a_negative_weight_is_refused_even_when_the_sum_is_positive(spark):
    from duckdb.experimental.spark.errors import PySparkValueError

    df = spark.createDataFrame([(1,), (2,)], "k int")
    with pytest.raises(PySparkValueError, match="-1.0"):
        df.randomSplit([-1.0, 3.0])


def test_a_name_without_backquotes_is_that_column(spark):
    df = spark.createDataFrame([(1, "a")], "k int, name string")
    assert df.select(df.colRegex("name")).columns == ["name"]
    assert df.select(df.colRegex("`n.*`")).columns == ["name"]


def test_sampleby_with_no_fraction_for_any_value_is_an_empty_frame(spark):
    df = spark.createDataFrame([(1,), (2,)], "k int")
    for fractions in ({}, {None: 0.5}):
        sample = df.sampleBy("k", fractions)
        assert sample.columns == ["k"]
        assert sample.count() == 0
