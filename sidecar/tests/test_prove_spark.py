"""A proof's Spark side (proof_spark.py): how Fabric's Spark SQL is pointed at the proof.

These read the rewriting and the path rules without Spark. The run end to end, with a real Spark
and Delta standing in for Fabric's, is in test_prove.py (opt-in: FD_SPARK_PYTHON).
"""

from __future__ import annotations

import os

import pytest

from fabric_desktop import proof
from fabric_desktop.proof_runtime import ProofRuntime
from fabric_desktop.proof_spark import ProofRefused, ProofSpark

ROOT = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh"
SHARED = f"{ROOT}/Tables/dbo"
SANDBOX = f"{ROOT}/Files/_fabric_desktop/proofs/p1/fabric/tables/dbo"


class _Reader:
    def __init__(self, calls):
        self.calls, self.opts = calls, {}

    def format(self, fmt):
        return self

    def option(self, key, value):
        self.opts[key] = value
        return self


class _Spark:
    """Only what seeding a copy asks of Spark: a reader to hand to the original load."""

    def __init__(self):
        self.calls = []

    @property
    def read(self):
        return _Reader(self.calls)


class _Frame:
    class write:  # noqa: N801 - Spark's attribute name
        @staticmethod
        def format(fmt):
            return _Frame.write

        @staticmethod
        def mode(m):
            return _Frame.write


def spark_proof(pins=None, flat=False, name="LH_Proof"):
    runtime = ProofRuntime({"id": "p1", "pins": pins if pins is not None else {"dbo.orders": 3, "dbo.Rates": 1},
                            "flat": flat}, "fabric")
    runtime.bind(ROOT, None, "/lakehouse/default")
    runtime.restore()
    fake = _Spark()
    ps = ProofSpark(runtime, fake, name, not flat)
    ps.orig["load"] = lambda reader, uri: fake.calls.append(("load", uri, dict(reader.opts))) or _Frame()
    ps.orig["save"] = lambda writer, uri, *a, **k: fake.calls.append(("save", uri))
    return ps, fake


def test_reads_point_at_the_pinned_version_and_keep_the_rest_of_the_text():
    ps, _ = spark_proof()
    sql = "SELECT o.region, 'dbo.orders' AS label -- dbo.orders\nFROM LH_Proof.dbo.orders AS o JOIN `dbo`.`rates` r USING (region)"
    assert ps.rewrite(sql) == (
        f"SELECT o.region, 'dbo.orders' AS label -- dbo.orders\nFROM delta.`{SHARED}/orders` VERSION AS OF 3 AS o "
        f"JOIN delta.`{SHARED}/Rates` VERSION AS OF 1 r USING (region)")
    assert ps.proof.log["reads"] == {"dbo.orders": 3, "dbo.Rates": 1}


def test_ctes_temp_views_and_table_functions_are_left_alone():
    ps, _ = spark_proof()
    ps.views.add("changes")
    sql = "WITH c AS (SELECT * FROM changes) SELECT * FROM c, range(3)"
    assert ps.rewrite(sql) == sql
    assert ps.rewrite("CREATE OR REPLACE TEMP VIEW recent AS SELECT * FROM orders") == \
        f"CREATE OR REPLACE TEMP VIEW recent AS SELECT * FROM delta.`{SHARED}/orders` VERSION AS OF 3"
    assert ps.rewrite("SELECT * FROM recent") == "SELECT * FROM recent"
    assert ps.rewrite("DROP VIEW recent") == "DROP VIEW recent" and "recent" not in ps.views


def test_a_merge_changes_a_copy_of_the_pinned_version():
    ps, fake = spark_proof()
    out = ps.rewrite("MERGE INTO dbo.orders AS t USING (SELECT * FROM orders WHERE region = 'east') AS s "
                     "ON t.order_id = s.order_id WHEN MATCHED THEN DELETE")
    # The copy is made first, so the source reads the same rows from it.
    assert fake.calls == [("load", f"{SHARED}/orders", {"versionAsOf": 3}), ("save", f"{SANDBOX}/orders")]
    assert out == (f"MERGE INTO delta.`{SANDBOX}/orders` AS t USING (SELECT * FROM delta.`{SANDBOX}/orders` "
                   "WHERE region = 'east') AS s ON t.order_id = s.order_id WHEN MATCHED THEN DELETE")
    assert ps.proof.log["writes"] == {"dbo.orders": ["merge"]}
    # Later changes and reads use the copy; nothing is copied again.
    assert ps.rewrite("UPDATE orders SET amount = 0") == f"UPDATE delta.`{SANDBOX}/orders` SET amount = 0"
    assert ps.rewrite("DELETE FROM LH_Proof.dbo.orders WHERE amount = 0") == \
        f"DELETE FROM delta.`{SANDBOX}/orders` WHERE amount = 0"
    assert len(fake.calls) == 2 and ps.proof.log["writes"]["dbo.orders"] == ["merge", "update", "delete"]


def test_inserts_and_creates_follow_spark_rules_in_the_copy():
    ps, fake = spark_proof()
    assert ps.rewrite("INSERT INTO dbo.orders (order_id) VALUES (9)") == \
        f"INSERT INTO delta.`{SANDBOX}/orders` (order_id) VALUES (9)"
    with pytest.raises(ValueError, match="doesn't exist"):
        ps.rewrite("INSERT INTO dbo.missing SELECT 1")
    with pytest.raises(ValueError, match="doesn't exist"):
        ps.rewrite("UPDATE dbo.missing SET a = 1")
    with pytest.raises(ValueError, match="already exists"):
        ps.rewrite("CREATE TABLE dbo.rates AS SELECT 1 AS a")
    assert ps.rewrite("CREATE TABLE IF NOT EXISTS dbo.rates AS SELECT 1 AS a") is None
    calls = len(fake.calls)
    assert ps.rewrite("CREATE OR REPLACE TABLE dbo.rates AS SELECT * FROM dbo.orders") == \
        f"CREATE OR REPLACE TABLE delta.`{SANDBOX}/rates` AS SELECT * FROM delta.`{SANDBOX}/orders`"
    assert len(fake.calls) == calls  # a replaced table isn't copied first
    assert ps.rewrite("CREATE TABLE dbo.new_one AS SELECT 1 AS a") == f"CREATE TABLE delta.`{SANDBOX}/new_one` AS SELECT 1 AS a"
    assert ps.rewrite("SELECT * FROM new_one") == f"SELECT * FROM delta.`{SANDBOX}/new_one`"


@pytest.mark.parametrize("sql, says", [
    ("DROP TABLE dbo.orders", "doesn't drop"),
    ("TRUNCATE TABLE dbo.orders", "doesn't drop"),
    ("ALTER TABLE dbo.orders ADD COLUMNS (x INT)", "doesn't drop"),
    ("USE other", "switch"),
    ("OPTIMIZE dbo.orders", "OPTIMIZE"),
    ("VACUUM dbo.orders", "VACUUM"),
    ("CREATE VIEW v AS SELECT 1", "TEMP VIEW"),
    ("SELECT * FROM Other_LH.dbo.orders", "isn't a table of the attached lakehouse"),
    ("CREATE TABLE dbo.`..` AS SELECT 1 AS a", "isn't a table name"),
    ("INSERT OVERWRITE DIRECTORY '/tmp/x' SELECT 1", "DIRECTORY"),
    (f"SELECT * FROM parquet.`{ROOT}/Tables/dbo/orders`", "read tables by name"),
])
def test_what_would_escape_the_proof_is_refused(sql, says):
    ps, _ = spark_proof()
    with pytest.raises((ProofRefused, ValueError), match=says):
        ps.rewrite(sql)
    assert ps.proof.log["writes"] == {}


def test_time_travel_reads_the_shared_table_unless_the_proof_changed_it():
    ps, _ = spark_proof()
    assert ps.rewrite("SELECT * FROM dbo.orders VERSION AS OF 1") == f"SELECT * FROM delta.`{SHARED}/orders` VERSION AS OF 1"
    ps.rewrite("DELETE FROM dbo.orders WHERE order_id = 1")
    with pytest.raises(ProofRefused, match="history"):
        ps.rewrite("SELECT * FROM dbo.orders VERSION AS OF 1")


def test_safe_commands_and_one_statement_at_a_time():
    ps, _ = spark_proof()
    assert ps.rewrite("SET spark.sql.shuffle.partitions = 4") == "SET spark.sql.shuffle.partitions = 4"
    assert ps.rewrite("") == ""
    with pytest.raises(ValueError, match="one statement"):
        ps.rewrite("SELECT 1; SELECT 2")
    with pytest.raises(ProofRefused, match="couldn't read this SQL"):
        ps.rewrite("SELECT FROM WHERE (")


def test_paths_into_the_lakehouse_are_tables_files_or_refused():
    ps, _ = spark_proof()
    assert ps.lakehouse_path(f"{ROOT}/Tables/dbo/orders") == ("table", "dbo", "orders")
    assert ps.lakehouse_path("Tables/dbo/orders/") == ("table", "dbo", "orders")
    assert ps.lakehouse_path("/lakehouse/default/Files/raw/rates.csv") == ("files", "Files/raw/rates.csv")
    assert ps.lakehouse_path(f"{SANDBOX}/orders") == ("sandbox", None)
    assert ps.lakehouse_path("abfss://other@onelake.dfs.fabric.microsoft.com/lh2/Tables/x") == ("onelake", None)
    assert ps.lakehouse_path("/tmp/scratch") is None
    assert ps.lakehouse_path("file:/lakehouse/default/Files/raw/rates.csv") == ("files", "Files/raw/rates.csv")
    assert ps.lakehouse_path("file:///lakehouse/default/Tables/dbo/orders") == ("table", "dbo", "orders")
    with pytest.raises(ProofRefused, match="isn't a table's folder"):
        ps.lakehouse_path("Tables/dbo/orders/part-0.parquet")
    assert ps.rewrite(f"SELECT * FROM delta.`{ROOT}/Tables/dbo/orders`") == \
        f"SELECT * FROM delta.`{SHARED}/orders` VERSION AS OF 3"


def test_a_lakehouse_without_schemas_names_tables_by_their_folder():
    ps, _ = spark_proof(pins={"dbo.orders": 0}, flat=True)
    assert ps.rewrite("SELECT * FROM LH_Proof.orders") == f"SELECT * FROM delta.`{ROOT}/Tables/orders` VERSION AS OF 0"
    with pytest.raises(ProofRefused, match="isn't a table of the attached lakehouse"):
        ps.rewrite("SELECT * FROM sales.orders")


def test_the_check_takes_spark_notebooks_and_stops_what_their_fabric_side_refuses():
    lh = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Proof"}
    ok = proof.check([{"type": "code", "source": "spark.sql('MERGE INTO dbo.t USING s ON t.id = s.id WHEN MATCHED THEN DELETE')"},
                      {"type": "code", "source": "%%sql\nUPDATE dbo.t SET a = 1"}], lh)
    assert ok["ok"] and ok["engine"] == "spark" and any("Files/" in t for t in ok["limits"])
    for source, says in [("df.write.insertInto('dbo.t')", "insertInto"),
                         ("df.writeTo('dbo.t').append()", "writeTo"),
                         ("spark.readStream.table('dbo.t')", "stream"),
                         ("spark.sql('DROP TABLE dbo.t')", "drops"),
                         ("%%sql\nTRUNCATE TABLE dbo.t", "TRUNCATE TABLE"),  # Runs on's reason comes first (#1286)
                         ("DeltaTable.createOrReplace(spark).tableName('t').execute()", "DeltaTable.create")]:
        out = proof.check([{"type": "code", "source": "df = spark.table('dbo.t')"}, {"type": "code", "source": source}], lh)
        assert not out["ok"] and says in out["blockers"][0]["text"], source
    # A Python notebook keeps the lakehouse module's rules.
    py = proof.check([{"type": "code", "source": "from fabric_desktop import lakehouse"},
                      {"type": "code", "source": "%%sql\nDELETE FROM t"}], lh)
    assert py["engine"] == "python" and "write with lakehouse.write_table" in py["blockers"][0]["text"]


def test_the_published_copy_binds_spark_first_and_makes_sql_cells_calls():
    from fabric_desktop.fabric_run import fabric_ipynb
    lh = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Proof"}
    cells = [{"type": "code", "source": "df = spark.table('dbo.orders')"},
             {"type": "code", "source": "%%sql\nUPDATE dbo.orders SET amount = 0"},
             {"type": "code", "source": "%%sql\nSELECT * FROM dbo.orders"}]
    first, last = proof.fabric_cells({"id": "p1", "pins": {"dbo.orders": 0}, "flat": False}, ["pyspark"],
                                     dict(lh, schemas=True))
    nb = fabric_ipynb([first] + cells + [last], lh, True, sql_calls=True)
    codes = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    for code in (codes[0], codes[-1]):
        compile(code, "cell", "exec")
    assert "_fd_spark_where = ('abfss://ws@onelake.dfs.fabric.microsoft.com/lh', '/lakehouse/default')" in codes[0]
    assert "_fd_smod.bind(_fd_proof, spark, 'LH_Proof', True)" in codes[0]
    assert codes[2] == 'spark.sql("UPDATE dbo.orders SET amount = 0")'
    assert codes[3] == '_sqldf = spark.sql("SELECT * FROM dbo.orders")\ndisplay(_sqldf)'
    assert codes[-1].splitlines()[1:] == ["_fd_spark.finish()", "_fd_proof.save(['pyspark'])"]
    assert nb["metadata"].get("microsoft", {}).get("language_group") != "jupyter_python"  # a Spark notebook


def test_sql_words_in_literals_and_comments_dont_stop_a_spark_proof():
    lh = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Proof"}
    check = lambda src: proof.check([{"type": "code", "source": "df = spark.table('dbo.t')"},  # noqa: E731
                                     {"type": "code", "source": src}], lh)
    assert check("%%sql\nSELECT 'use' AS x, `optimize` FROM t -- then DROP TABLE t")["ok"]
    assert check("spark.sql(\"SELECT 'truncate table' AS x\")")["ok"]
    for src in ("%%sql\nUSE other", "%%sql\nSELECT 1;\nUSE other", "spark.sql(\"COPY INTO dbo.t FROM 'Files/x'\")",
                "%%sql\nCREATE TABLE t LOCATION 'abfss://x' AS SELECT 1"):
        assert not check(src)["ok"], src


@pytest.mark.parametrize("sql, says", [
    ("COPY INTO dbo.orders FROM 'Files/raw' FILEFORMAT = CSV", "COPY INTO"),
    ("CREATE TABLE dbo.t2 LOCATION 'abfss://x@y.dfs.core.windows.net/t' AS SELECT 1 AS a", "LOCATION"),
])
def test_loads_and_locations_are_refused(sql, says):
    ps, _ = spark_proof()
    with pytest.raises(ProofRefused, match=says):
        ps.rewrite(sql)


ESCAPES = '''
import json, os, sys, types
from pathlib import Path
import pyspark
from delta import configure_spark_with_delta_pip
from pyspark.sql.utils import AnalysisException
root = Path(sys.argv[1])
b = (pyspark.sql.SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
     .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
     .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"))
spark = configure_spark_with_delta_pip(b).getOrCreate()
spark.range(3).write.format("delta").save(str(root / "Tables/dbo/orders"))
mods = {}
for name in ("proof_runtime", "proof_spark"):
    mods[name] = types.ModuleType(name)
    exec(compile(Path(sys.argv[2], name + ".py").read_text(), name, "exec"), mods[name].__dict__)
rt = mods["proof_runtime"].ProofRuntime({"id": "p1", "pins": {"dbo.orders": 0}}, "fabric").bind(str(root), None, str(root))
ps = mods["proof_spark"].bind(rt, spark, "LH", True)
df = spark.range(2)
shared = str(root / "Tables/dbo/orders")
attempts = {
    "save_option_path": lambda: df.write.format("delta").mode("overwrite").option("path", shared).save(),
    "save_options_path": lambda: df.write.format("delta").mode("overwrite").options(path=shared).save(),
    "save_no_path": lambda: df.write.format("delta").mode("overwrite").save(),
    "table_with_path_option": lambda: df.write.option("path", shared).mode("overwrite").saveAsTable("dbo.x"),
    "insert_into": lambda: df.write.insertInto("dbo.orders"),
    "stream_start": lambda: spark.readStream.format("rate").load().writeStream.format("delta").start(shared),
    "stream_table": lambda: spark.readStream.format("rate").load().writeStream.toTable("dbo.orders"),
    "files_write": lambda: df.write.parquet(str(root / "Files/out")),
    "drop": lambda: spark.sql("DROP TABLE dbo.orders"),
}
out = {}
for name, attempt in attempts.items():
    try:
        attempt()
        out[name] = "ran"
    except mods["proof_spark"].ProofRefused:
        out[name] = "refused"
    except Exception as exc:
        out[name] = type(exc).__name__
from delta.tables import DeltaTable
out["_shared_version"] = DeltaTable.forPath(spark, shared).history(1).collect()[0]["version"]
out["_files"] = (root / "Files/out").exists()
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not os.environ.get("FD_SPARK_PYTHON"), reason="set FD_SPARK_PYTHON to a Python with pyspark and delta-spark")
def test_on_a_real_spark_the_ways_around_the_proof_are_refused(tmp_path):
    import json
    import subprocess
    from pathlib import Path
    script = tmp_path / "escapes.py"
    script.write_text(ESCAPES, encoding="utf-8")
    here = Path(proof.__file__).parent
    python = os.environ["FD_SPARK_PYTHON"]
    ran = subprocess.run([python, str(script), str(tmp_path / "lh"), str(here)], capture_output=True, text=True,
                         timeout=900, env=dict(os.environ, PYSPARK_PYTHON=python), cwd=tmp_path)
    line = next((ln for ln in ran.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, ran.stderr[-3000:]
    out = json.loads(line[7:])
    assert out.pop("_shared_version") == 0 and out.pop("_files") is False
    assert out == {name: "refused" for name in out}, out


PINNED_READS = '''
import json, sys, types
from pathlib import Path
import pyspark
from delta import configure_spark_with_delta_pip
root = Path(sys.argv[1])
b = (pyspark.sql.SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
     .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
     .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"))
spark = configure_spark_with_delta_pip(b).getOrCreate()
shared = str(root / "Tables/dbo/orders")
spark.range(3).write.format("delta").save(shared)
spark.range(2).write.format("delta").mode("append").save(shared)  # version 1: 5 rows; the proof pins version 0
(root / "Files/raw").mkdir(parents=True)
(root / "Files/raw/rates.csv").write_text("region,rate\\neast,1.5\\n")
mods = {}
for name in ("proof_runtime", "proof_spark"):
    mods[name] = types.ModuleType(name)
    exec(compile(Path(sys.argv[2], name + ".py").read_text(), name, "exec"), mods[name].__dict__)
rt = mods["proof_runtime"].ProofRuntime({"id": "p1", "pins": {"dbo.orders": 0}}, "fabric").bind(str(root), None, str(root))
mods["proof_spark"].bind(rt, spark, "LH", True)
out = {
    "option_path": spark.read.format("delta").option("path", shared).load().count(),
    "options_path": spark.read.format("delta").options(path=shared).load().count(),
    "file_scheme": spark.read.format("delta").load("file:" + shared).count(),
    "by_name": spark.read.table("dbo.orders").count(),
}
spark.read.format("csv").option("header", "true").option("path", str(root / "Files/raw/rates.csv")).load().count()
out["files"] = sorted(rt.log["files"])
print("RESULT " + json.dumps(out))
'''


@pytest.mark.skipif(not os.environ.get("FD_SPARK_PYTHON"), reason="set FD_SPARK_PYTHON to a Python with pyspark and delta-spark")
def test_on_a_real_spark_a_path_given_as_an_option_or_a_file_url_reads_the_pinned_version(tmp_path):
    import json
    import subprocess
    from pathlib import Path
    script = tmp_path / "reads.py"
    script.write_text(PINNED_READS, encoding="utf-8")
    python = os.environ["FD_SPARK_PYTHON"]
    ran = subprocess.run([python, str(script), str(tmp_path / "lh"), str(Path(proof.__file__).parent)],
                         capture_output=True, text=True, timeout=900, env=dict(os.environ, PYSPARK_PYTHON=python), cwd=tmp_path)
    line = next((ln for ln in ran.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, ran.stderr[-3000:]
    out = json.loads(line[len("RESULT "):])
    assert out == {"option_path": 3, "options_path": 3, "file_scheme": 3, "by_name": 3, "files": ["Files/raw/rates.csv"]}
