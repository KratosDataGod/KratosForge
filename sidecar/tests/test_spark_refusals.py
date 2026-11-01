"""#1286: the check behind Runs on knows everything this PC's Spark refuses, from spark_local's own
list, so a notebook it calls runs on this PC doesn't then fail on its first run here."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fabric_desktop import proof, spark_local, validate
from fabric_desktop.fabric_run import compute_check

LH = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}


def check(*sources):
    return compute_check([{"type": "code", "source": s, "meta": {}} for s in sources], LH)


def test_every_refusal_in_spark_local_is_listed_with_how_the_check_finds_it():
    """A new NotImplementedError in spark_local fails here until CHECKED_REFUSALS names it, and
    an entry whose refusal is gone fails too."""
    tree = ast.parse(Path(spark_local.__file__).read_text(encoding="utf-8"))
    raising = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                        and getattr(node.exc.func, "id", "") == "NotImplementedError":
                    raising.add(fn.name)
    assert raising == set(spark_local.CHECKED_REFUSALS)


# One notebook per refusal in CHECKED_REFUSALS: the check blocks it on this PC.
BLOCKED = {
    "option": ("df.write.option('replaceWhere', 'd = 1').mode('overwrite').saveAsTable('dbo.t')", "replaceWhere"),
    "_check_options": ("df.write.option('someOption', 'x').saveAsTable('dbo.t')", "saveAsTable doesn't take"),
    "_refuse_dynamic_overwrite": ("spark.conf.set('spark.sql.sources.partitionOverwriteMode', 'dynamic')",
                                  "dynamic partitionOverwriteMode"),
    "_refuse_auto_merge": ("spark.conf.set('spark.databricks.delta.schema.autoMerge.enabled', 'true')\n"
                           "spark.sql('MERGE INTO dbo.t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *')",
                           "autoMerge"),
    "schema": ("df = spark.read.schema('a INT, b STRING').csv('Files/x.csv')", "DDL string"),
    "_no_schema": ("df = spark.read.schema(my_schema).table('dbo.t')", "read.schema() to a read of a table"),
    "sql": ("spark.sql('MERGE WITH SCHEMA EVOLUTION INTO dbo.t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *')",
            "WITH SCHEMA EVOLUTION"),
    "_statement": ("spark.sql('INSERT INTO dbo.t PARTITION (d = 1) SELECT * FROM s')", "PARTITION or REPLACE WHERE"),
    "forPath": ("from delta.tables import DeltaTable\nt = DeltaTable.forPath(spark, 'Files/raw/t')", "forPath"),
    "_in_fabric": ("from delta.tables import DeltaTable\nt = DeltaTable.forName(spark, 'dbo.t')\nt.vacuum(168)",
                   "DeltaTable.vacuum"),
    "withSchemaEvolution": ("t.merge(s, 'a = b').withSchemaEvolution().whenMatchedUpdateAll().execute()",
                            "withSchemaEvolution"),
}


def test_every_listed_refusal_but_the_settings_one_has_a_notebook_here():
    assert set(BLOCKED) == set(spark_local.CHECKED_REFUSALS) - {"_preview_on"}  # covered by test_spark_merge


@pytest.mark.parametrize("site", sorted(BLOCKED))
def test_code_this_pcs_spark_refuses_is_blocked_on_this_pc(site):
    source, words = BLOCKED[site]
    local = check(source)["local"]
    assert local["status"] == "blocked" and not local["ok"], local
    assert words in local["reasons"][0]["text"], local["reasons"][0]["text"]


@pytest.mark.parametrize("source", [
    "df.write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable('dbo.t')",
    "df.write.option('mergeSchema', 'true').option('optimizeWrite', 'true').saveAsTable('dbo.t')",
    "df.write.format('delta').saveAsTable('dbo.t')",
    "df.write.insertInto('dbo.t')",
    "df = spark.read.format('csv').option('header', 'true').load('Files/x.csv')",
    "df = spark.read.load('Files/x.parquet')",
    "df = spark.read.format('delta').load('Tables/dbo/t')",
    "df = spark.read.schema(my_schema).csv('Files/x.csv')",
    "spark.conf.set('spark.sql.sources.partitionOverwriteMode', 'static')",
    "spark.conf.set('spark.databricks.delta.schema.autoMerge.enabled', 'true')",  # no MERGE in the notebook
    "from delta.tables import DeltaTable\nt = DeltaTable.forPath(spark, 'Tables/dbo/t')\nt.toDF().show()",
    "import scipy.optimize\nresult = model.optimize(x)",  # not a DeltaTable
    "spark.sql('INSERT INTO dbo.t SELECT * FROM s')",
    "spark.sql('DROP VIEW IF EXISTS v')",
])
def test_what_this_pcs_spark_takes_stays_ok(source):
    out = check(source)["local"]
    assert out["status"] == "ok", out


@pytest.mark.parametrize("source, words", [
    ("df.write.option(key, 'x').saveAsTable('dbo.t')", "write option named only at run time"),
    ("df = spark.read.format(fmt).load(path)", "format named only at run time"),
    ("df.write.format(fmt).saveAsTable('dbo.t')", "format named only at run time"),
    ("spark.sql('DROP TABLE IF EXISTS sales')", "runs here for a temp view and in Fabric for a lakehouse table"),
    ("spark.sql(f'DROP TABLE {name}')", "table named only at run time"),
])
def test_what_depends_on_a_name_known_at_run_time_is_unknown(source, words):
    local = check(source)["local"]
    assert local["status"] == "unknown", local
    assert words in local["unknown"][0]["text"], local["unknown"][0]["text"]


def test_a_read_of_a_format_this_pc_doesnt_have_is_blocked():
    for source in ("spark.read.format('avro').load('Files/x')", "spark.read.load('Files/x', format='orc')",
                   "spark.read.load('Files/x', 'xml')"):
        assert "which this PC's Spark doesn't have" in check(source)["local"]["reasons"][0]["text"], source


def test_saving_a_table_as_another_format_is_blocked():
    for source in ("df.write.format('parquet').saveAsTable('dbo.t')", "df.write.saveAsTable('dbo.t', format='csv')"):
        assert "lakehouse tables are Delta tables" in check(source)["local"]["reasons"][0]["text"], source


def test_sql_cells_and_the_sql_line_magic_read_the_same_refusals():
    cells = [{"type": "code", "source": "%%sql\nRESTORE TABLE dbo.t TO VERSION AS OF 3", "meta": {}},
             {"type": "code", "source": "%%sql\nTRUNCATE TABLE t", "meta": {}},
             {"type": "code", "source": "rows = %sql DROP TABLE dbo.old", "meta": {}}]
    out = compute_check(cells, LH)["local"]
    assert [(r["cell"], "RESTORE" in r["text"] or "DROP TABLE" in r["text"]) for r in out["reasons"]] == [(1, True), (3, True)]
    assert [u["cell"] for u in out["unknown"]] == [2]


def test_the_fabric_side_is_unchanged():
    for source, _ in BLOCKED.values():
        assert check(source)["fabric"]["status"] == "ok", source


def test_prove_and_validate_read_the_same_blocker():
    cells = [{"type": "code", "source": "from fabric_desktop import lakehouse\nlakehouse.write_table(df, 'dbo.out')", "meta": {}},
             {"type": "code", "source": BLOCKED["_statement"][0], "meta": {}}]
    text = compute_check(cells, LH)["local"]["reasons"][0]["text"]
    assert [b["text"] for b in proof.check(cells, LH)["blockers"] if b["cell"] == 2] == [f"can't run on this PC: {text}"]
    assert [f["severity"] for f in validate.check(cells, LH, "pc")["findings"] if f["code"] == "runs_on_pc"] == ["error"]


def test_a_clean_run_doesnt_ask_to_confirm_what_the_check_cant_read():
    """Unknown on this PC isn't something a clean run can't contain: running it is how to find out."""
    cells = [{"type": "code", "source": "df = spark.read.table('dbo.orders')", "meta": {}},
             {"type": "code", "source": "spark.sql('TRUNCATE TABLE orders')", "meta": {}}]
    assert validate.run_plan(cells, LH, spark=True)["uncontained"] == []


def test_a_refused_option_on_a_writer_held_in_a_variable_is_blocked():
    for source in ("w = df.write\nw.option('replaceWhere', 'd = 1').saveAsTable('dbo.t')",
                   "w = df.write.mode('overwrite')\nw = w.option('partitionOverwriteMode', 'dynamic')\nw.saveAsTable('dbo.t')"):
        local = check(source)["local"]
        assert local["status"] == "blocked" and "write option" in local["reasons"][0]["text"], source
