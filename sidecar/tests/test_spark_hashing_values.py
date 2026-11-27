"""WR-119: Spark 3.5's hashing and encoding functions on this PC, value-checked against Spark 3.5.3 (see
spark_values.py).

hash (Murmur3, seed 42) and xxhash64 of each type, md5, sha1, sha2 at each bit length, crc32, base64 and
unbase64, hex and unhex, and conv: the queries in tools/spark_value_cases/hashing.txt, each answered by Spark
3.5.3 in tests/fixtures/spark_3_5_3_hashing.json. What this PC can't answer as Spark does is refused by name
below, and listed in docs/sql-compatibility.md."""

from __future__ import annotations

import pytest
from spark_values import Refused, check_part, check_still_needed, parts

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


@pytest.mark.parametrize("cases", parts("hashing"))
def test_spark_hashing_functions_answer_as_spark_3_5_or_are_refused_by_name(spark, cases):
    check_part(spark, cases, REFUSED)


def test_each_refusal_of_hashing_is_still_needed_and_listed(spark):
    check_still_needed(spark, "hashing", REFUSED)


def test_the_sha2_function_gives_null_for_a_length_spark_has_no_sha2_for():
    from fabric_desktop import spark_hash
    assert spark_hash.sha2(b"abc", 1) is None and spark_hash.sha2(b"abc", 999) is None


def test_a_hand_written_call_of_the_sha2_function_gives_null_where_spark_would():
    import duckdb

    from fabric_desktop import spark_hash
    con = duckdb.connect()
    spark_hash.register(con)
    assert con.sql("SELECT __kf_sha2('abc'::BLOB, 1), __kf_sha2(NULL, 512)").fetchall() == [(None, None)]


def test_sha2_at_512_bits_runs_over_a_200k_row_table_a_chunk_at_a_time(spark):
    import hashlib
    rows = spark.sql("SELECT id, sha2(CAST(id AS STRING), 512) s FROM range(200000) ORDER BY id").collect()
    assert len(rows) == 200000
    assert [r.s for r in rows[:3]] == [hashlib.sha512(str(i).encode()).hexdigest() for i in range(3)]
    assert rows[-1].s == hashlib.sha512(b"199999").hexdigest()


def test_the_chunked_sha2_function_gives_null_for_null_data_null_bits_or_another_length():
    import duckdb

    from fabric_desktop import spark_hash
    con = duckdb.connect()
    spark_hash.register(con)
    got = con.sql("SELECT __kf_sha2(d, b) FROM (VALUES ('abc'::BLOB, 224), (NULL, 384), ('abc'::BLOB, NULL), "
                  "('abc'::BLOB, 7), ('abc'::BLOB, 0), ('abc'::BLOB, 256)) t(d, b)").fetchall()
    import hashlib
    assert got == [(hashlib.sha224(b"abc").hexdigest(),), (None,), (None,), (None,),
                   (hashlib.sha256(b"abc").hexdigest(),), (hashlib.sha256(b"abc").hexdigest(),)]


def test_sha2_casts_its_length_to_an_int_first_as_spark_does(spark):
    # Spark casts a decimal or text length to an int, cutting a fraction, before choosing the digest
    import hashlib
    row = spark.sql("SELECT sha2('abc', 256.0) a, sha2('abc', 384.9) b, sha2('abc', '512') c, "
                    "sha2('abc', CAST(224.7 AS DECIMAL(4, 1))) d, sha2('abc', 255.9) e, "
                    "sha2('abc', x) f FROM VALUES (CAST(256.7 AS DECIMAL(4, 1))) t(x)").collect()[0]
    assert (row.a, row.b, row.c, row.d, row.e, row.f) == (
        hashlib.sha256(b"abc").hexdigest(), hashlib.sha384(b"abc").hexdigest(), hashlib.sha512(b"abc").hexdigest(),
        hashlib.sha224(b"abc").hexdigest(), None, hashlib.sha256(b"abc").hexdigest())


@pytest.mark.parametrize("length", ["1e30000000", "1e10", "NaN", "Infinity"])  # a minus sign is a Neg, not a literal
def test_a_huge_nan_or_infinite_length_literal_is_null_and_never_made_into_an_int(length):
    # int(Decimal('1e30000000')) would build a 30-million-digit int, stalling the engine before the query runs
    import time

    from sqlglot import exp

    from fabric_desktop.spark_builtins_sql import _sha2
    started = time.monotonic()
    out = _sha2(exp.Anonymous(this="sha2"), [exp.Literal.string("abc"), exp.Literal.number(length)])
    assert time.monotonic() - started < 1
    assert out is not None and "NULL" in out.sql("duckdb") and "SHA" not in out.sql("duckdb").upper()


def test_sha2_with_a_huge_exponent_length_is_null(spark):
    row = spark.sql("SELECT sha2('abc', 1e30000000) a, sha2('abc', -1e30000000) b, sha2('abc', 512e0) c").collect()[0]
    import hashlib
    assert (row.a, row.b, row.c) == (None, None, hashlib.sha512(b"abc").hexdigest())
