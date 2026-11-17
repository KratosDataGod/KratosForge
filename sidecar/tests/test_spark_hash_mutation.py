"""Spark hash() and xxhash64() on this PC (spark_hash.py): the edges a mutation run showed no test
pinned (docs/quality/mutation-testing.md, batch 30). Nothing leaves this PC."""

from __future__ import annotations

import threading

import pyarrow as pa

from fabric_desktop import spark_hash, sql


def test_hash_is_an_int_and_xxhash64_a_long_as_in_spark():
    table = sql.run("SELECT hash(1) h, xxhash64(1) x")
    assert [f.type for f in table.schema] == [pa.int32(), pa.int64()]
    assert spark_hash.hash_sql("hash", 1).endswith("::INTEGER))[1]")
    assert spark_hash.hash_sql("xxhash64", 1).endswith("::BIGINT))[1]")


def test_the_same_call_always_builds_the_same_sql():
    once = spark_hash.hash_sql("hash", 2)
    spark_hash.hash_sql("xxhash64", 3)
    assert spark_hash.hash_sql("hash", 2) == once
    assert "fj_arg1" in once and "fj_arg2" not in once


def test_builds_at_the_same_time_each_get_their_own_names():
    want = {n: spark_hash.hash_sql("hash", n) for n in (1, 2, 3)}
    got: dict[int, list[str]] = {n: [] for n in want}
    start = threading.Barrier(6)

    def build(n):
        start.wait()
        for _ in range(5):
            got[n].append(spark_hash.hash_sql("hash", n))
    threads = [threading.Thread(target=build, args=(n,)) for n in (1, 2, 3, 1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(text == want[n] for n, texts in got.items() for text in texts)


def test_a_hash_of_null_alone_is_the_seed():
    assert sql.run("SELECT hash(NULL) h, xxhash64(NULL) x").to_pylist() == [{"h": 42, "x": 42}]
