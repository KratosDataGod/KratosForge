"""WR-258: Spark 3.5's timestamps below a second on this PC, value-checked against Spark 3.5.3 (see
spark_values.py).

Text with 1 to 9 digits of a second (Spark keeps microseconds and cuts the rest), casts to and from numbers,
date_trunc, timestampadd and timestampdiff on sub-second units, the parts of a second, and timestamps written as
text (Spark drops trailing zeros): the queries in tools/spark_value_cases/subseconds.txt, each answered by Spark
3.5.3 in tests/fixtures/spark_3_5_3_subseconds.json."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("interval text", r"CAST\(TIMESTAMP'[^']*' - TIMESTAMP'[^']*' AS STRING\)", r"interval cast to string",
            "`CAST(a_difference AS STRING)` of two timestamps"),
    Refused("fraction and offset", r"SSSSSSSXXX", r"more than six S letters with an offset",
            "more than six `S` letters and an offset"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_subsecond_timestamps_answer_as_spark_3_5(spark):
    check(spark, load("subseconds"), REFUSED)
