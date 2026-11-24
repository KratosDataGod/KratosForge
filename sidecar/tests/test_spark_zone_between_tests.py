"""A time zone a test sets on the process's one local Spark session doesn't reach the next test: conftest
puts the session back in UTC after each test (spark_session_in_utc). test_spark_timestamp_casts left it in
America/New_York, so later test files translated SQL for that zone."""

from __future__ import annotations

from conftest import spark_zone_back_to_utc

from fabric_desktop import spark_local, spark_time_zone
from fabric_desktop.notebook import Kernel


def test_a_zone_a_cell_set_is_back_to_utc_after_the_test():
    out = Kernel().run("spark.conf.set('spark.sql.session.timeZone', 'America/New_York')")
    assert out["status"] == "ok", out
    live = spark_local._session
    assert live.conf.get("spark.sql.session.timeZone") == "America/New_York" and spark_time_zone.current_gaps()
    spark_zone_back_to_utc()
    assert live.conf.get("spark.sql.session.timeZone") == "UTC"
    assert spark_time_zone.current_gaps() == ()
    assert live.sql("SELECT current_timezone() AS z").first().z == "UTC"


def test_with_no_zone_set_nothing_changes():
    live = spark_local.session()
    spark_zone_back_to_utc()
    assert live.conf.get("spark.sql.session.timeZone") == "UTC" and spark_time_zone.current_gaps() == ()
