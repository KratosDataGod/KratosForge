"""WR-119: Spark 3.5's hashing and encoding functions on this PC, value-checked against Spark 3.5.3 (see
spark_values.py).

hash (Murmur3, seed 42) and xxhash64 of each type, md5, sha1, sha2 at each bit length, crc32, base64 and
unbase64, hex and unhex, and conv: the queries in tools/spark_value_cases/hashing.txt, each answered by Spark
3.5.3 in tests/fixtures/spark_3_5_3_hashing.json. What this PC can't answer as Spark does is refused by name
below, and listed in docs/sql-compatibility.md."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("struct", r"hash\(named_struct", r"of a value of type STRUCT", "`hash` or `xxhash64` of a struct"),
    Refused("nested array", r"hash\(array\((named_struct|array)\(", r"of a value of type (STRUCT|\w+\[\])",
            "or of an array of structs or arrays"),
    Refused("not UTF-8", r"CAST\(X'[0-9A-F]*' AS STRING\)", r"aren't UTF-8 text",
            "A cast of binary to string where the bytes aren't UTF-8"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_hashing_functions_answer_as_spark_3_5_or_are_refused_by_name(spark):
    check(spark, load("hashing"), REFUSED)


def test_the_sha2_function_gives_null_for_a_length_spark_has_no_sha2_for():
    from fabric_desktop import spark_hash
    assert spark_hash.sha2(b"abc", 1) is None and spark_hash.sha2(b"abc", 999) is None


def test_a_hand_written_call_of_the_sha2_function_gives_null_where_spark_would():
    import duckdb

    from fabric_desktop import spark_hash
    con = duckdb.connect()
    spark_hash.register(con)
    assert con.sql("SELECT __kf_sha2('abc'::BLOB, 1), __kf_sha2(NULL, 512)").fetchall() == [(None, None)]
