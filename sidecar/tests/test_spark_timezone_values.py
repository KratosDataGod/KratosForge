"""WR-120: Spark 3.5's session time zone on this PC, value-checked against Spark 3.5.3 (see spark_values.py).

timestamp and timestamp_ntz, casts between them and to and from dates and text, unix_timestamp and
from_unixtime, from_utc_timestamp and to_utc_timestamp, and the hours a daylight-saving change skips or
repeats, each in the session time zone a case names: the queries in tools/spark_value_cases/timezone.txt,
each answered by Spark 3.5.3 in tests/fixtures/spark_3_5_3_timezone.json. What this PC can't answer as Spark
does is refused by name below, and listed in docs/sql-compatibility.md."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("offset with minutes", r"\+05:30", r"whole hours only", "written as an offset with minutes, such as `+05:30`"),
    Refused("zone name", r"XXX z'", r"can't use 'z' in a pattern", "Zone names (`z`, such as `EST`)"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_time_zone_answers_as_spark_3_5_or_is_refused_by_name(spark):
    check(spark, load("timezone"), REFUSED)
