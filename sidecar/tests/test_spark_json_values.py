"""WR-118: Spark 3.5's JSON functions on this PC, value-checked against Spark 3.5.3 (see spark_values.py).

get_json_object, json_tuple, from_json in its PERMISSIVE, FAILFAST and DROPMALFORMED modes (with the
corrupt-record column), to_json and schema_of_json: 232 queries in tools/spark_value_cases/json.txt, each
answered by Spark 3.5.3 in tests/fixtures/spark_3_5_3_json.json. What this PC can't answer as Spark does is
refused by name below, and listed in docs/sql-compatibility.md."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("reading options", r"(from_json|schema_of_json)\(.*'allow(Comments|UnquotedFieldNames|NumericLeadingZeros)', 'true'",
            r"option isn't read on this PC", "`allowComments`, `allowUnquotedFieldNames` and `allowNumericLeadingZeros`"),
    Refused("pretty", r"to_json\(.*'pretty'", r"'pretty' option isn't read here", "The `to_json` option `pretty`"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_json_functions_answer_as_spark_3_5_or_are_refused_by_name(spark):
    check(spark, load("json"), REFUSED)
