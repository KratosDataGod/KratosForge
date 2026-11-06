"""hash() and xxhash64() give Spark's numbers on this PC (spark_hash.py). Every expected value is what
Apache Spark 3.5.3 gave for the same query (tests/fixtures/spark_3_5_3_hash.json): each type in one
VALUES table (NULLs keep the seed, arrays hash their elements, text longer and shorter than the
32-byte stripes and 4- and 8-byte words), and calls with several arguments, each hash the next's seed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "spark_3_5_3_hash.json").read_text(encoding="utf-8"))


def rows(query: str) -> list[list]:
    return [list(r.values()) for r in sql.run(query).to_pylist()]


@pytest.mark.parametrize("group", FIXTURE["groups"], ids=lambda g: g["type"])
def test_each_type_hashes_as_spark_does(group):
    values = ", ".join(f"({i}, CAST({v} AS {group['type']}))" for i, v in enumerate(group["values"]))
    got = rows(f"SELECT hash(a) h, xxhash64(a) x FROM VALUES {values} AS t(i, a) ORDER BY i")
    assert got == group["want"], list(zip(group["values"], got, group["want"], strict=False))


def test_several_arguments_hash_as_spark_does():
    calls = FIXTURE["calls"]
    got = rows("SELECT " + ", ".join(f"hash({c['args']}), xxhash64({c['args']})" for c in calls))[0]
    assert [got[2 * i:2 * i + 2] for i in range(len(calls))] == [c["want"] for c in calls]


def test_the_dataframe_api_gives_the_same_numbers():
    out = Kernel().run("from pyspark.sql import functions as F\n"
                       "d = spark.createDataFrame([(1, 'a')], ['n', 's'])\n"
                       "print(d.select(F.hash('n', 's').alias('h'), F.xxhash64('n', 's').alias('x')).collect())\n"
                       "print(spark.sql('SELECT hash(1L, \\'a\\') h, xxhash64(1L, \\'a\\') x').collect())")
    text = "".join(o.get("text", "") for o in out["outputs"])
    want = next(c["want"] for c in FIXTURE["calls"] if c["args"] == "1L, 'a'")  # n is a long, as in Spark
    assert out["status"] == "ok", out
    assert text.count(f"h={want[0]}, x={want[1]}") == 2, text


def test_a_bucket_is_spark_s_bucket():
    """The usual use: hash(key) % n picks the same bucket as on Fabric."""
    want = rows("SELECT hash(CAST(id AS INT)) FROM range(5) AS t(id) ORDER BY id")
    got = rows("SELECT pmod(hash(CAST(id AS INT)), 7) FROM range(5) AS t(id) ORDER BY id")
    assert [g[0] for g in got] == [w[0] % 7 for w in want]


@pytest.mark.parametrize("value", ["map(1, 'a')", "named_struct('a', 1)", "array(array(1))", "INTERVAL '1' DAY"])
def test_types_not_written_here_stop_by_name(value):
    with pytest.raises(Exception, match=r"hash\(\) of a value of type .* can't run on this PC"):
        rows(f"SELECT hash({value})")


def test_no_arguments_is_an_error_as_in_spark():
    with pytest.raises(ValueError, match="at least one argument"):
        rows("SELECT xxhash64()")
