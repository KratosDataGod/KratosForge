"""EA-017: Spark's hash() is a 32-bit Murmur3 and xxhash64() is XXH64, both seeded 42; DuckDB's hash is
neither. Every Spark path that goes through sql.spark_fixes now gives Spark's value (spark_hash.py);
the Local data engine's Spark SQL, which doesn't, still refuses them by name rather than answer with
another number. md5 and sha2(x, 256), the stable hashes the message points to, give
the text Spark gives."""

import hashlib

import pytest
from sqlglot import exp

from fabric_desktop import sql
from fabric_desktop.engines import local_sql
from fabric_desktop.notebook import Kernel


def run(code, k=None):
    out = (k or Kernel()).run("from pyspark.sql import functions as F\n" + code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


@pytest.mark.parametrize("code", [
    "print(spark.sql(\"SELECT hash('a') AS h\").collect())",
    "print(spark.sql(\"SELECT HASH(1, 'a') AS h\").collect())",
    "print(spark.range(3).where('hash(id) % 2 = 0').collect())",
    "print(spark.range(3).select(F.expr('hash(id)')).collect())",
    "print(spark.range(3).selectExpr('hash(id) AS h').collect())",
    "print(spark.range(3).select(F.xxhash64('id')).collect())",
    "print(spark.sql('SELECT xxhash64(1)').collect())",
])
def test_every_spark_path_runs_spark_s_hash(code):
    """Each path reaches spark_hash.py (its values: test_spark_hash.py), never DuckDB's own hash."""
    status, text = run(code)
    assert status == "ok", text
    assert "Row(" in text or "[]" in text


def test_the_characterization_holds():
    """The audit's check: Spark-sized (32-bit) and Spark's own number, never a 64-bit value."""
    status, text = run("print(spark.sql(\"SELECT hash('a') AS h\").collect()[0]['h'])")
    assert status == "ok", text
    assert int(text.strip()) == 1485273170  # Spark 3.5.3: SELECT hash('a')


def test_databricks_sql_on_the_duckdb_engine_gives_spark_s_hash_too():
    trees = local_sql._databricks("SELECT hash(1), xxhash64(1)")
    assert not [c for t in trees for c in t.find_all(exp.Anonymous) if c.name.upper() in ("HASH", "XXHASH64")]
    assert sql.run("SELECT hash(1) h").to_pylist() == [{"h": -559580957}]  # Spark 3.5.3


def test_duckdb_sql_keeps_duckdb_s_own_hash():
    """DuckDB SQL means DuckDB's hash, so it isn't refused there."""
    assert sql.parse("SELECT hash(1)", dialect="duckdb")


def test_names_like_hash_still_run():
    status, text = run("print(spark.sql('SELECT hash_value, 1 AS hash FROM (SELECT 1 AS hash_value)').collect())")
    assert status == "ok", text
    assert "hash_value=1" in text


def test_md5_and_sha2_give_spark_s_text():
    status, text = run("print(spark.sql(\"SELECT md5('a') m, sha2('a', 256) s\").collect())\n"
                       "print(spark.range(1).select(F.md5(F.lit('a')).alias('m'), F.sha2(F.lit('a'), 256).alias('s')).collect())")
    assert status == "ok", text
    want = f"m='{hashlib.md5(b'a').hexdigest()}', s='{hashlib.sha256(b'a').hexdigest()}'"
    assert text.count(want) == 2, text


def test_randomsplit_and_sample_keep_working():
    """sample(seed) and randomSplit use DuckDB's hash themselves, in DuckDB SQL: not refused."""
    status, text = run("d = spark.range(100)\nprint(d.sample(0.5, seed=1).count() > 0)\n"
                       "a, b = d.randomSplit([0.5, 0.5], seed=1)\nprint(a.count() + b.count())")
    assert status == "ok", text
    assert "True" in text and "100" in text


def test_local_data_projects_refuse_spark_hash_and_keep_duckdb_s(tmp_path):
    from fabric_desktop.localdata import LocalData
    local = LocalData(tmp_path / "ld")
    try:
        eng = local.engine(local.create("P")["id"])
        for query in ("SELECT hash(1)", "SELECT xxhash64('a') AS h", "CREATE TABLE t AS SELECT hash(1) AS h"):
            with pytest.raises(ValueError, match="can't run on this PC the way it runs in Fabric"):
                eng.execute(query, dialect="spark")
        assert eng.execute("SELECT hash(1) AS h", dialect="duckdb")["rows"]
    finally:
        local.close_all()
