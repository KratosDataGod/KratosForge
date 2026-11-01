"""spark_api on this PC: the session and DataFrame calls test_spark_api.py doesn't reach. Each works
as in PySpark 3.5 or raises with a pointer to Fabric. Nothing starts Spark or reaches OneLake."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture
def k():
    return Kernel()


def last(k, code):
    out = k.run(code)
    assert out["status"] == "ok", out
    return out["outputs"][-1]["text"]


def error(k, code):
    out = k.run(code)
    assert out["status"] == "error", out
    return out["outputs"][-1]


# ---------------------------------------------------------------- spark.conf and sparkContext


def test_an_unknown_time_zone_is_refused_and_unset_goes_back_to_utc(k):
    e = error(k, "spark.conf.set('spark.sql.session.timeZone', 'Mars/Olympus')")  # DuckDB's own reason
    assert e["ename"] == "NotImplementedException" and "Unknown TimeZone 'Mars/Olympus'" in e["evalue"]
    assert last(k, "spark.conf.get('spark.sql.session.timeZone')") == "'UTC'"
    k.run("spark.conf.set('spark.sql.session.timeZone', 'America/New_York')")
    k.run("spark.conf.unset('spark.sql.session.timeZone')")
    assert last(k, "spark.sql(\"SELECT current_setting('TimeZone') AS tz\").collect()[0]['tz']") == "'UTC'"


def test_conf_takes_booleans_as_spark_writes_them_and_an_explicit_none_default(k):
    assert last(k, "spark.conf.set('spark.sql.ansi.enabled', True)\nspark.conf.get('spark.sql.ansi.enabled')") == "'true'"
    assert last(k, "repr(spark.conf.get('spark.sql.shuffle.partitions', None))") == "'None'"
    k.run("spark.conf.unset('never.set')")
    assert last(k, "spark.conf.isModifiable('spark.executor.memory')") == "False"


def test_spark_context_conf_reads_and_writes_the_session_conf(k):
    assert last(k, "c = spark.sparkContext.getConf()\nc.set('spark.sql.caseSensitive', 'true') is c") == "True"
    assert last(k, "spark.conf.get('spark.sql.caseSensitive')") == "'true'"
    assert last(k, "(c.contains('spark.app.name'), c.get('nope', 'd'))") == "(True, 'd')"
    assert ("spark.app.name", "KratosForge") in eval(last(k, "c.getAll()"))  # noqa: S307 - our own repr


def test_spark_context_answers_without_a_cluster(k):
    out = last(k, "sc = spark.sparkContext\nsc.setLogLevel('WARN'); sc.setJobDescription('x'); sc.setJobGroup('g', 'd')\n"
                  "sc.setCheckpointDir('/tmp'); sc.setLocalProperty('a', '1')\n"
                  "(sc.getLocalProperty('a'), sc.getLocalProperty('b'), sc.uiWebUrl, sc.master, bool(sc.sparkUser()))")
    assert out == "('1', None, None, 'local[*]', True)"


@pytest.mark.parametrize("call", ["parallelize([1, 2])", "textFile('x')", "emptyRDD()", "broadcast(1)", "accumulator(0)"])
def test_rdds_run_in_fabric(k, call):
    e = error(k, f"spark.sparkContext.{call}")
    assert e["ename"] == "NotImplementedError" and "RDDs run in Fabric" in e["evalue"]


# ---------------------------------------------------------------- catalog


def test_table_exists_finds_a_temp_view_and_nothing_without_a_lakehouse(k):
    k.run("spark.range(3).createOrReplaceTempView('v')")
    assert last(k, "(spark.catalog.tableExists('v'), spark.catalog.tableExists('t', 'dbo'), "
                   "spark.catalog.tableExists('missing'))") == "(True, False, False)"


# ---------------------------------------------------------------- DataFrame


def test_create_temp_view_refuses_a_name_already_used(k):
    k.run("df = spark.range(2)\ndf.createTempView('once')")
    e = error(k, "df.createTempView('once')")
    assert e["ename"] == "ValueError" and "createOrReplaceTempView" in e["evalue"]


def test_empty_tail_offset_and_iteration(k):
    k.run("df = spark.createDataFrame([(1,), (2,), (3,)], ['n'])")
    assert last(k, "(df.isEmpty(), df.filter('n > 9').isEmpty())") == "(False, True)"
    assert last(k, "[r['n'] for r in df.orderBy('n').tail(2)], df.tail(0)") == "([2, 3], [])"
    assert last(k, "[r['n'] for r in df.orderBy('n').offset(1).collect()]") == "[2, 3]"
    assert last(k, "sum(r['n'] for r in df.toLocalIterator())") == "6"


def test_stats_answer_as_pyspark_does(k):
    k.run("df = spark.createDataFrame([(1, 2.0), (2, 4.0), (3, 6.0)], ['a', 'b'])")
    assert last(k, "round(df.stat.corr('a', 'b'), 6), round(df.stat.cov('a', 'b'), 6)") == "(1.0, 2.0)"
    assert last(k, "df.stat.approxQuantile('a', [0.0, 1.0], 0.01)") == "[1.0, 3.0]"
    assert last(k, "df.approxQuantile(['a', 'b'], [0.5], 0.01)") == "[[2.0], [4.0]]"


def test_random_split_parts_never_overlap_and_hold_every_row(k):
    k.run("df = spark.range(200)\nparts = df.randomSplit([0.5, 0.3, 0.2], seed=7)")
    counts = eval(last(k, "[p.count() for p in parts]"))  # noqa: S307 - our own repr
    assert sum(counts) == 200 and all(c > 0 for c in counts)
    assert last(k, "len({r['id'] for p in parts for r in p.collect()})") == "200"
    assert last(k, "[p.count() for p in df.randomSplit([0.5, 0.3, 0.2], seed=7)] == "
                   "[p.count() for p in parts]") == "True"


def test_a_dataframe_says_it_is_local_and_knows_its_session(k):
    assert last(k, "df = spark.range(1)\n(df.isLocal(), df.sparkSession is spark)") == "(True, True)"
    assert "".join(o.get("text", "") for o in k.run("df.explain()")["outputs"]).strip()  # DuckDB's plan, printed


@pytest.mark.parametrize("code", ["df.rdd", "df.foreach(print)", "df.foreachPartition(print)",
                                  "df.mapInPandas(lambda x: x, 'id long')"])
def test_row_by_row_python_runs_in_fabric(k, code):
    k.run("df = spark.range(1)")
    e = error(k, code)
    assert e["ename"] == "NotImplementedError" and "run in Fabric" in e["evalue"]


def test_register_temp_table_and_global_views_are_temp_views(k):
    k.run("spark.range(2).registerTempTable('old')\nspark.range(3).createOrReplaceGlobalTempView('g')")
    assert last(k, "(spark.table('old').count(), spark.table('g').count())") == "(2, 3)"
