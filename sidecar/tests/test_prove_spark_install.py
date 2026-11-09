"""A proof's Spark side once it is bound (proof_spark.install): every way a notebook reaches a table
through Spark goes to the proof's pins and copies, and every way it could change the shared lakehouse
otherwise is refused. pyspark and delta are stand-in modules, so this runs without Spark; the run with
a real Spark is test_prove.py's (opt-in)."""

from __future__ import annotations

import sys
import types

import pytest

from fabric_desktop import proof_spark
from fabric_desktop.proof_runtime import ProofRuntime
from fabric_desktop.proof_spark import ProofRefused, ProofSpark
from test_prove_spark import ROOT, SANDBOX, SHARED

TABLE = f"{ROOT}/Tables/dbo/orders"


@pytest.fixture
def spark(monkeypatch, tmp_path):
    """Stand-in pyspark and delta modules with fresh classes, a bound proof with dbo.orders pinned at
    version 3 (and its lakehouse mounted at tmp_path), and ``calls``: what reached Spark itself."""
    calls = []

    class Frame:
        @property
        def write(self):
            return DataFrameWriter()

        def createOrReplaceTempView(self, name):  # noqa: N802 - Spark's names
            calls.append(("view", name))

    class DataFrameReader:
        def format(self, fmt):
            return self

        def option(self, key, value):
            calls.append(("reader.option", key, value))
            return self

        def options(self, **options):
            calls.append(("reader.options", options))
            return self

        def load(self, path=None, format=None, schema=None, **options):  # noqa: A002
            calls.append(("load", path, options))
            return Frame()

    class DataFrameWriter:
        def format(self, fmt):
            return self

        def mode(self, mode):
            calls.append(("mode", mode))
            return self

        def option(self, key, value):
            return self

        def options(self, **options):
            return self

        def save(self, path=None, format=None, mode=None, partitionBy=None, **options):  # noqa: A002, N803
            calls.append(("save", path, format, mode, options))
            return "saved"

    for fmt in ("parquet", "csv", "json", "orc", "text"):
        setattr(DataFrameReader, fmt, lambda self, *paths, _f=fmt, **o: calls.append((f"read.{_f}", paths)) or Frame())
        setattr(DataFrameWriter, fmt, lambda self, path, *a, _f=fmt, **k: calls.append((f"write.{_f}", path)))

    class Catalog:
        def tableExists(self, tableName, dbName=None):  # noqa: N802, N803
            calls.append(("tableExists", tableName, dbName))
            return "asked spark"

    class DataStreamWriter:
        pass

    class Session:
        @property
        def read(self):
            return DataFrameReader()

        @property
        def catalog(self):
            return Catalog()

        def sql(self, sqlQuery, args=None, **kwargs):  # noqa: N803
            calls.append(("sql", sqlQuery, args))
            return "result"

        def table(self, tableName):  # noqa: N803
            raise AssertionError("the proof's table() should answer")

    class DeltaTable:
        @classmethod
        def forPath(cls, sparkSession, path, *args, **kwargs):  # noqa: N802, N803
            calls.append(("forPath", path))
            return ("delta table", path)

        @classmethod
        def forName(cls, sparkSession, name):  # noqa: N802, N803
            raise AssertionError("the proof's forName should answer")

        @classmethod
        def createOrReplace(cls, sparkSession=None):  # noqa: N802, N803
            raise AssertionError("refused before this")

    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, m)

    module("pyspark")
    module("pyspark.sql", DataFrame=Frame)
    module("pyspark.sql.catalog", Catalog=Catalog)
    module("pyspark.sql.readwriter", DataFrameReader=DataFrameReader, DataFrameWriter=DataFrameWriter)
    module("pyspark.sql.streaming", DataStreamWriter=DataStreamWriter)
    module("delta")
    module("delta.tables", DeltaTable=DeltaTable)
    runtime = ProofRuntime({"id": "p1", "pins": {"dbo.orders": 3}, "flat": False}, "fabric")
    runtime.bind(ROOT, None, str(tmp_path))
    runtime.restore()
    session = Session()
    ps = proof_spark.bind(runtime, session, "LH_Proof", True)
    return types.SimpleNamespace(ps=ps, session=session, calls=calls, Frame=Frame, Reader=DataFrameReader,
                                 Writer=DataFrameWriter, Catalog=Catalog, Stream=DataStreamWriter,
                                 DeltaTable=DeltaTable, mount=tmp_path)


def test_spark_sql_runs_the_rewritten_statement_or_nothing(spark):
    assert spark.session.sql("SELECT * FROM orders") == "result"
    assert spark.calls[-1] == ("sql", f"SELECT * FROM delta.`{SHARED}/orders` VERSION AS OF 3", None)
    spark.session.sql("SELECT * FROM orders WHERE id = :id", args={"id": 1})
    assert spark.calls[-1][2] == {"id": 1}
    spark.session.sql("CREATE TABLE IF NOT EXISTS dbo.orders AS SELECT 1 AS a")  # it exists: nothing to run
    assert spark.calls[-1] == ("sql", "SELECT 1 AS skipped LIMIT 0", None)


def test_tables_read_by_name_or_path_read_the_pinned_version(spark):
    spark.session.table("orders")
    spark.session.read.table("LH_Proof.dbo.orders")
    spark.session.read.load(TABLE)
    spark.session.read.option("path", TABLE).load()
    spark.session.read.options(path=TABLE).load()
    spark.session.read.load(path=None, format="delta", **{"PATH": TABLE})
    loads = [c for c in spark.calls if c[0] == "load"]
    assert [c[1] for c in loads] == [f"{SHARED}/orders"] * 6


def test_files_read_through_spark_are_hashed_and_folders_of_tables_refused(spark):
    files = spark.mount / "Files" / "raw"
    files.mkdir(parents=True)
    (files / "rates.csv").write_text("region,rate\neast,1\n", encoding="utf-8")
    spark.session.read.csv("Files/raw/rates.csv")
    spark.session.read.load("/lakehouse/default/Files/raw/missing.csv")  # not on this mount: not hashed
    spark.session.read.parquet(["Files/raw/rates.csv"], "/tmp/scratch")
    spark.session.read.load(["Files/a", "Files/b"])  # a list goes straight to Spark
    assert set(spark.ps.proof.log["files"]) == {"Files/raw/rates.csv"}
    with pytest.raises(ProofRefused, match="read it with spark.read.table"):
        spark.session.read.parquet(TABLE)


def test_writes_by_name_or_path_go_to_the_copy_with_sparks_modes(spark):
    frame = spark.Frame()
    frame.write.mode("append").saveAsTable("orders")  # a table that exists: its pinned version is copied first
    assert [c for c in spark.calls if c[0] in ("load", "save")][-3:] == [
        ("load", f"{SHARED}/orders", {}), ("save", f"{SANDBOX}/orders", None, None, {}),  # the copy
        ("save", f"{SANDBOX}/orders", "delta", "append", {})]  # then the append to it
    with pytest.raises(ValueError, match="already exists"):
        frame.write.saveAsTable("orders")
    assert frame.write.mode("ignore").saveAsTable("orders") is None
    frame.write.save(f"{ROOT}/Tables/dbo/new_one", mode="append")  # appending to a new table makes it
    assert spark.calls[-1] == ("save", f"{SANDBOX}/new_one", "delta", "overwrite", {"overwriteSchema": "true"})
    with pytest.raises(ValueError, match="Delta tables"):
        frame.write.saveAsTable("orders", format="parquet", mode="overwrite")
    frame.write.save("/tmp/scratch/out", format="parquet")
    assert spark.calls[-1][1] == "/tmp/scratch/out"
    frame.write.parquet(f"{SANDBOX}/scratch")
    assert spark.ps.proof.log["writes"] == {"dbo.orders": ["append"], "dbo.new_one": ["overwrite"]}


@pytest.mark.parametrize("write, says", [
    (lambda w: w.save(), "pass the path to save()"),
    (lambda w: w.save("Files/out.csv"), "doesn't write Files/out.csv"),
    (lambda w: w.save("abfss://other@onelake.dfs.fabric.microsoft.com/lh2/Tables/x"), "in OneLake"),
    (lambda w: w.option("path", TABLE), "write's path as an option"),
    (lambda w: w.options(Path=TABLE), "write's path as an option"),
    (lambda w: w.saveAsTable("orders", path=TABLE), "write's path as an option"),
    (lambda w: w.csv("Files/out.csv"), "doesn't write Files/out.csv"),
    (lambda w: w.insertInto("orders"), "insertInto"),
])
def test_writes_that_would_escape_the_proof_are_refused(spark, write, says):
    with pytest.raises(ProofRefused, match=says):
        write(spark.Frame().write)
    assert spark.ps.proof.log["writes"] == {}


def test_other_ways_to_make_tables_or_streams_are_refused(spark):
    for refused in (lambda: spark.Frame().writeTo("orders"), lambda: spark.Catalog().createTable("t"),
                    lambda: spark.Catalog().createExternalTable("t"), lambda: spark.Stream().start(),
                    lambda: spark.Stream().toTable("t"), lambda: spark.DeltaTable.createOrReplace(spark.session)):
        with pytest.raises(ProofRefused):
            refused()


def test_table_exists_answers_from_the_proof_except_for_views_and_other_names(spark):
    catalog = spark.Catalog()
    assert catalog.tableExists("orders") is True and catalog.tableExists("`dbo`.`missing`") is False
    assert catalog.tableExists("orders", "dbo") is True
    spark.Frame().createOrReplaceTempView("Recent")
    assert "recent" in spark.ps.views and catalog.tableExists("recent") == "asked spark"
    assert catalog.tableExists("Other_LH.dbo.orders") == "asked spark"


def test_delta_tables_open_the_copy_and_other_paths_are_refused(spark):
    dt = spark.DeltaTable
    assert dt.forName(spark.session, "orders") == ("delta table", f"{SANDBOX}/orders")
    assert dt.forPath(spark.session, TABLE) == ("delta table", f"{SANDBOX}/orders")
    assert dt.forPath(spark.session, f"{SANDBOX}/orders") == ("delta table", f"{SANDBOX}/orders")
    assert dt.forPath(spark.session, "/tmp/elsewhere") == ("delta table", "/tmp/elsewhere")
    with pytest.raises(ProofRefused, match="isn't a table of the attached lakehouse"):
        dt.forPath(spark.session, "Files/raw")
    with pytest.raises(ValueError, match="doesn't exist"):
        dt.forName(spark.session, "missing")
    assert spark.ps.opened == {"dbo.orders"}


def test_a_table_only_opened_isnt_a_write_once_the_run_finishes(spark):
    spark.DeltaTable.forName(spark.session, "orders")
    spark.ps.orig["sql"] = lambda session, q: types.SimpleNamespace(collect=lambda: [{"version": 0}])
    spark.ps.finish()
    assert "dbo.orders" not in spark.ps.proof.log["writes"]


def test_a_copy_whose_history_cant_be_read_stays_a_write(spark):
    spark.DeltaTable.forName(spark.session, "orders")

    def unreadable(session, q):
        raise RuntimeError("DELTA_MISSING_TRANSACTION_LOG")
    spark.ps.orig["sql"] = unreadable
    spark.ps.finish()
    assert spark.ps.proof.log["writes"]["dbo.orders"] == ["open"]


def test_without_deltas_python_api_only_spark_is_bound(monkeypatch):
    for name in ("pyspark", "pyspark.sql", "pyspark.sql.catalog", "pyspark.sql.readwriter", "pyspark.sql.streaming"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["pyspark.sql"].DataFrame = type("DataFrame", (), {})
    sys.modules["pyspark.sql.catalog"].Catalog = type("Catalog", (), {"tableExists": lambda *a: None})
    names = {n: (lambda *a, **k: None) for n in ("load", "option", "options", "save", "mode", "parquet", "csv",
                                                  "json", "orc", "text")}
    sys.modules["pyspark.sql.readwriter"].DataFrameReader = type("DataFrameReader", (), dict(names))
    sys.modules["pyspark.sql.readwriter"].DataFrameWriter = type("DataFrameWriter", (), dict(names))
    sys.modules["pyspark.sql.streaming"].DataStreamWriter = type("DataStreamWriter", (), {})
    monkeypatch.setitem(sys.modules, "delta.tables", None)  # import fails
    session = type("Session", (), {"sql": lambda *a, **k: None, "table": lambda *a: None})()
    runtime = ProofRuntime({"id": "p1", "pins": {}, "flat": False}, "fabric")
    ps = ProofSpark(runtime, session, "LH", True).install()
    assert "forPath" not in ps.orig


# ---- what the rewrite refuses or leaves alone, past the main tests


def test_sql_paths_to_files_or_the_proofs_own_folder_are_left_and_other_reads_refused(spark):
    ps = spark.ps
    files = spark.mount / "Files"
    files.mkdir()
    (files / "r.parquet").write_bytes(b"PAR1")
    assert ps.rewrite("SELECT * FROM parquet.`Files/r.parquet`") == "SELECT * FROM parquet.`Files/r.parquet`"
    assert "Files/r.parquet" in ps.proof.log["files"]
    assert ps.rewrite(f"SELECT * FROM delta.`{SANDBOX}/orders`") == f"SELECT * FROM delta.`{SANDBOX}/orders`"
    assert ps.rewrite("SELECT * FROM parquet.`/tmp/elsewhere`") == "SELECT * FROM parquet.`/tmp/elsewhere`"
    with pytest.raises(ProofRefused, match="isn't a table of the attached lakehouse"):
        ps.rewrite("INSERT INTO delta.`Files/raw` SELECT 1")
    assert ps.rewrite(f"INSERT INTO delta.`{TABLE}` SELECT * FROM orders") == \
        f"INSERT INTO delta.`{SANDBOX}/orders` SELECT * FROM delta.`{SANDBOX}/orders`"


@pytest.mark.parametrize("sql, says", [
    ("COPY INTO dbo.orders FROM 'Files/x' FILEFORMAT = CSV", "COPY INTO"),
    ("CREATE SCHEMA sales", "doesn't create schema"),
    ("CREATE TABLE dbo.t (a INT) LOCATION 'Files/t'", "its own LOCATION"),
    ("REFRESH TABLE dbo.orders", "doesn't drop"),
])
def test_more_statements_that_would_escape_the_proof_are_refused(spark, sql, says):
    with pytest.raises(ProofRefused, match=says):
        spark.ps.rewrite(sql)


def test_a_name_with_too_many_parts_or_an_empty_one_isnt_a_table(spark):
    with pytest.raises(ProofRefused, match="pins only the attached lakehouse's tables"):
        spark.ps.parts(["a", "b", "c", "d"])
    with pytest.raises(ValueError, match="isn't a table name"):
        spark.ps.parts([])


def test_paths_need_a_bound_proof():
    ps = ProofSpark(ProofRuntime({"id": "p1", "pins": {}, "flat": False}, "fabric"), None, None, True)
    with pytest.raises(RuntimeError, match="isn't bound"):
        ps.lakehouse_path("Tables/dbo/orders")
