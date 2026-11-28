"""Storage levels, sameSemantics / semanticHash and Observation on this PC (spark_df_extras): the
answers Spark 3.5.3 gives where Spark's are fixed, and a refusal by name where they run in Fabric."""

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


def run(code, kernel=None):
    out = (kernel or Kernel()).run(code)
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


def error(code):
    out = Kernel().run(code)
    assert out["status"] == "error", out
    return " ".join(str(o.get("evalue", "")) for o in out["outputs"] if o.get("type") == "error")


def test_storage_levels_print_as_spark_prints_them():
    # Spark 3.5.3: str() and repr() of each level.
    text = run("from pyspark.storagelevel import StorageLevel\n"
               "for n in ('DISK_ONLY', 'DISK_ONLY_2', 'DISK_ONLY_3', 'MEMORY_ONLY', 'MEMORY_ONLY_2', 'MEMORY_AND_DISK',\n"
               "          'MEMORY_AND_DISK_2', 'OFF_HEAP', 'MEMORY_AND_DISK_DESER', 'NONE'):\n"
               "    print(n, '|', getattr(StorageLevel, n), '|', repr(getattr(StorageLevel, n)))")
    assert text.splitlines() == [
        "DISK_ONLY | Disk Serialized 1x Replicated | StorageLevel(True, False, False, False, 1)",
        "DISK_ONLY_2 | Disk Serialized 2x Replicated | StorageLevel(True, False, False, False, 2)",
        "DISK_ONLY_3 | Disk Serialized 3x Replicated | StorageLevel(True, False, False, False, 3)",
        "MEMORY_ONLY | Memory Serialized 1x Replicated | StorageLevel(False, True, False, False, 1)",
        "MEMORY_ONLY_2 | Memory Serialized 2x Replicated | StorageLevel(False, True, False, False, 2)",
        "MEMORY_AND_DISK | Disk Memory Serialized 1x Replicated | StorageLevel(True, True, False, False, 1)",
        "MEMORY_AND_DISK_2 | Disk Memory Serialized 2x Replicated | StorageLevel(True, True, False, False, 2)",
        "OFF_HEAP | Disk Memory OffHeap Serialized 1x Replicated | StorageLevel(True, True, True, False, 1)",
        "MEMORY_AND_DISK_DESER | Disk Memory Deserialized 1x Replicated | StorageLevel(True, True, False, True, 1)",
        "NONE | Serialized 1x Replicated | StorageLevel(False, False, False, False, 1)",
    ]
    assert run("from pyspark import StorageLevel as A\nfrom pyspark.storagelevel import StorageLevel as B\n"
               "print(A is B, A.DISK_ONLY == B(True, False, False, False), A.DISK_ONLY == A.DISK_ONLY_2)") == "True True False"


def test_persist_cache_and_unpersist_set_the_level_storage_level_reads():
    k = Kernel()
    run("from pyspark.storagelevel import StorageLevel\ndf = spark.range(3)", k)
    assert run("print(df.storageLevel)", k) == "Serialized 1x Replicated"
    assert run("print(df.persist().storageLevel)", k) == "Disk Memory Deserialized 1x Replicated"
    assert run("print(df.persist(StorageLevel.DISK_ONLY_2) is df, df.storageLevel)", k) == \
        "True Disk Serialized 2x Replicated"
    assert run("print(df.unpersist(True).storageLevel, df.count())", k) == "Serialized 1x Replicated 3"
    assert run("c = df.cache()\nprint(c.storageLevel, df.storageLevel, c.count())", k) == \
        "Disk Memory Deserialized 1x Replicated Disk Memory Deserialized 1x Replicated 3"
    assert "needs a StorageLevel" in error("spark.range(1).persist('MEMORY_ONLY')")


def test_same_semantics_ignores_made_up_and_output_names_only():
    setup = "df1 = spark.range(10)\ndf2 = spark.range(10)\n"
    # Spark 3.5.3's documented answers: True, False, True.
    assert run(setup + "print(df1.withColumn('c1', df1.id * 2).sameSemantics(df2.withColumn('c1', df2.id * 2)),"
                       " df1.withColumn('c1', df1.id * 2).sameSemantics(df2.withColumn('c1', df2.id + 2)),"
                       " df1.withColumn('c1', df1.id * 2).sameSemantics(df2.withColumn('c0', df2.id * 2)))") == \
        "True False True"
    assert run(setup + "a = df1.filter('id > 3').select('id')\nb = df2.filter('id > 3').select('id')\n"
                       "print(a.sameSemantics(b), a.semanticHash() == b.semanticHash(),"
                       " a.sameSemantics(df2.filter('id > 4').select('id')), a.sameSemantics(spark.range(11)))") == \
        "True True False False"
    # An inner name is how an outer column refers to it: swapped inner names are two different plans.
    assert run("t = spark.createDataFrame([(1, 2)], ['a', 'b'])\n"
               "from pyspark.sql.functions import col\n"
               "x = t.select(col('a').alias('x'), col('b').alias('y')).select('x')\n"
               "y = t.select(col('a').alias('y'), col('b').alias('x')).select('x')\n"
               "print(x.sameSemantics(y), x.collect()[0][0], y.collect()[0][0])") == "False 1 2"
    assert run("h = spark.range(3).semanticHash()\nprint(isinstance(h, int), -2**31 <= h < 2**31)") == "True True"
    assert "should be a DataFrame" in error("spark.range(1).sameSemantics(1)")


def test_observation_gives_spark_metrics_and_is_used_once():
    k = Kernel()
    run("from pyspark.sql.functions import col, count, lit, max\nfrom pyspark.sql import Observation\n"
        "df = spark.createDataFrame([(2, 'Alice'), (5, 'Bob')], schema=['age', 'name'])\n"
        "observation = Observation('my metrics')\n"
        "observed_df = df.observe(observation, count(lit(1)).alias('count'), max(col('age')))", k)
    assert run("observed_df.count()", k) == "2"
    assert run("observation.get", k) == "{'count': 2, 'max(age)': 5}"  # Spark 3.5.3's documented answer
    out = k.run("df.observe(observation, count(lit(1)))")
    assert out["status"] == "error" and "only once" in str(out["outputs"])
    assert "isn't on a DataFrame yet" in error("from pyspark.sql import Observation\nObservation().get")
    assert "at least one metric" in error("from pyspark.sql import Observation\nspark.range(1).observe(Observation())")
    assert "should be Columns" in error("from pyspark.sql import Observation\nspark.range(1).observe(Observation(), 'id')")


def test_listeners_and_named_observations_are_refused_by_name():
    text = error("from pyspark.sql.functions import count, lit\nspark.range(1).observe('m', count(lit(1)))")
    assert "listeners run in Fabric" in text
    text = error("from pyspark.sql.streaming import StreamingQueryListener\n"
                 "class L(StreamingQueryListener):\n    pass\n"
                 "spark.streams.addListener(L())")
    assert "spark.streams.addListener runs in Fabric" in text
    assert "runs in Fabric" in error("spark.streams.active")
