"""PB-015 criterion 3: what this PC doesn't run of MERGE, UPDATE and DELETE is refused with a named reason.

Each test names the exact reason and checks the Delta table afterwards: same version, same rows. The
refusals Spark makes too (duplicate matches, subqueries, clauses that can't be) are compared with real
Spark in test_spark_dml_parity.py; these are the ones only this PC makes: another lakehouse, Preview
off, a temp view, RESTORE outside the app, a managed table, schema evolution Fabric couldn't read, more
than one statement in a call, and shapes the engine reads but can't run.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel

ROWS = [{"id": 1, "name": "a", "amount": 10.0}, {"id": 2, "name": "b", "amount": 20.0},
        {"id": 3, "name": "c", "amount": 30.0}]
UPD = ("spark.createDataFrame([(2, 'B', 25.0), (4, 'd', 40.0)], 'id INT, name STRING, amount DOUBLE')"
       ".createOrReplaceTempView('upd')\n")
DUP = "spark.createDataFrame([(1, 5.0), (1, 6.0)], 'id INT, amount DOUBLE').createOrReplaceTempView('dup')\n"
MULTIPLE = ("[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE] More than one source row matches the same row "
            "of sales ({}), so which one changes it is undefined. Delta refuses this MERGE in Fabric and Databricks "
            "too, and nothing was changed.")
PREVIEW_OFF = ("{} on this PC is a Preview feature, and Preview features are off in Settings or by your "
               "organization's policy. Run the cell in Fabric.")


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: default)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    write_deltalake(str(root / "dbo" / "sales"), pa.table({"id": pa.array([1, 2, 3], pa.int32()),
                                                           "name": ["a", "b", "c"], "amount": [10.0, 20.0, 30.0]}))
    yield root
    lakehouse.attach(None)


def state(lake):
    table = DeltaTable(str(lake / "dbo" / "sales"))
    return table.version(), sorted(table.to_pyarrow_table().to_pylist(), key=lambda r: r["id"])


def refused(lake, code, reason, kernel=None):
    """Run ``code``: it must fail with ``reason`` in its message and leave dbo.sales as it was."""
    before = state(lake)
    assert before[1] == ROWS
    out = (kernel or Kernel()).run(code)
    errors = [o for o in out["outputs"] if o.get("type") == "error"]
    assert out["status"] == "error" and errors, out
    assert reason in errors[-1]["evalue"], errors[-1]["evalue"]
    assert state(lake) == before
    return errors[-1]


def preview_off(monkeypatch):
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: False if key == "show_preview" else default)


# ---- more than one source row for one table row (Spark raises there too)

@pytest.mark.parametrize("statement, detail", [
    ("MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = s.amount", "source key 1"),
    ("MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED AND s.amount > 100 THEN UPDATE SET amount = 0",
     "source key 1"),  # Delta counts what ON matches, whatever the clause's own condition says
    ("MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED AND t.id > 0 THEN DELETE", "source key 1"),
    ("MERGE INTO sales t USING dup s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN DELETE", "source key 1"),
    ("MERGE INTO sales t USING dup s ON t.id = s.id + 0 WHEN MATCHED THEN UPDATE SET amount = 1", "2 source rows"),
])
def test_two_source_rows_for_one_table_row(lake, statement, detail):
    error = refused(lake, DUP + f"spark.sql({statement!r})", MULTIPLE.format(detail))
    assert error["ename"] == "ValueError"


def test_two_source_rows_for_one_table_row_through_the_merge_builder(lake):
    refused(lake, DUP + "from delta.tables import DeltaTable\n"
            "DeltaTable.forName(spark, 'sales').alias('t').merge(spark.table('dup').alias('s'), 't.id = s.id')"
            ".whenMatchedUpdate(set={'amount': 's.amount'}).execute()", MULTIPLE.format("source key 1"))


# ---- a table outside the attached lakehouse

@pytest.mark.parametrize("statement, name", [
    ("MERGE INTO LH_Keys.dbo.sales t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE", "LH_Keys.dbo.sales AS t"),
    ("UPDATE LH_Keys.dbo.sales SET amount = 0", "LH_Keys.dbo.sales"),
    ("DELETE FROM LH_Keys.dbo.sales", "LH_Keys.dbo.sales"),
    ("MERGE INTO cat.LH.dbo.sales t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE", "cat.LH.dbo.sales AS t"),
])
def test_another_lakehouses_table(lake, statement, name):
    refused(lake, UPD + f"spark.sql({statement!r})", f"{name}: name the table as schema.table or table.")


@pytest.mark.parametrize("statement, what", [
    ("MERGE INTO other.sales t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE", "MERGE has nothing to change"),
    ("UPDATE other.sales SET amount = 0", "UPDATE has nothing to change"),
    ("DELETE FROM other.sales", "DELETE has nothing to change"),
])
def test_a_table_the_lakehouse_hasnt_got(lake, statement, what):
    refused(lake, UPD + f"spark.sql({statement!r})", f"Table other.sales doesn't exist, so {what}")


@pytest.mark.parametrize("statement, what", [
    ("MERGE INTO upd t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE", ("MERGE INTO upd: upd is a temp view on "
     "this PC. MERGE changes")),
    ("UPDATE upd SET amount = 0", "UPDATE upd: upd is a temp view on this PC. UPDATE changes"),
    ("DELETE FROM upd", "DELETE upd: upd is a temp view on this PC. DELETE changes"),
])
def test_a_temp_view(lake, statement, what):
    refused(lake, UPD + f"spark.sql({statement!r})", what + " a Delta table of the attached lakehouse.")


def test_a_managed_table(lake, monkeypatch):
    monkeypatch.setattr(lakehouse, "write_guard", lambda lh, schema, table: f"{schema}.{table} is managed")
    for statement in ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE",
                      "UPDATE sales SET amount = 0", "DELETE FROM sales"):
        refused(lake, UPD + f"spark.sql({statement!r})", "dbo.sales is managed")


# ---- Preview off

@pytest.mark.parametrize("code, what", [
    ("spark.sql('MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE')", "MERGE INTO"),
    ("spark.sql('UPDATE sales SET amount = 0')", "UPDATE"),
    ("spark.sql('DELETE FROM sales')", "DELETE"),
    (("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').alias('t')"
     ".merge(spark.table('upd').alias('s'), 't.id = s.id').whenMatchedDelete().execute()"), "DeltaTable.merge"),
    ("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').update(set={'amount': '0'})",
     "DeltaTable.update"),
    ("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').delete()", "DeltaTable.delete"),
    ("spark.sql('RESTORE TABLE sales TO VERSION AS OF 0')", "RESTORE"),
])
def test_with_preview_off(lake, monkeypatch, code, what):
    preview_off(monkeypatch)
    error = refused(lake, UPD + code, PREVIEW_OFF.format(what))
    assert error["ename"] == "NotImplementedError"


def test_the_sql_line_magic_with_preview_off(lake, monkeypatch):
    preview_off(monkeypatch)
    refused(lake, "%sql DELETE FROM sales", "This MERGE, UPDATE or DELETE runs in Fabric only: on this PC they change "
            "tables of the attached lakehouse, with Preview features on.")


@pytest.mark.parametrize("statement", [
    "DELETE FROM sales", "UPDATE sales SET amount = 0",
    "MERGE INTO sales t USING (SELECT 1 AS id) s ON t.id = s.id WHEN MATCHED THEN DELETE",
])
def test_a_sql_cell_with_preview_off_is_left_for_fabric(lake, monkeypatch, statement):
    """A %%sql cell isn't an error: it says it runs in Fabric, and runs nothing here."""
    preview_off(monkeypatch)
    before = state(lake)
    out = Kernel().run(f"%%sql\n{statement}")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].startswith("This cell changes tables, so it runs in Fabric once the notebook "
                                                "is published.")
    assert state(lake) == before


# ---- RESTORE

@pytest.mark.parametrize("code", [
    "spark.sql('RESTORE TABLE sales TO VERSION AS OF 0')",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').restoreToVersion(0)",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').restoreToTimestamp('2020-01-01')",
])
def test_restore_outside_the_app(lake, code):
    error = refused(lake, code, "OPTIMIZE, VACUUM and RESTORE from notebook code run on this PC only inside "
                    "KratosForge, which checks the workspace first. Nothing was changed.")
    assert error["ename"] == "PermissionError"


# ---- schema evolution

BAD_NEW = ("spark.createDataFrame([(4, 'd', 40.0, 'b')], ['id', 'name', 'amount', 'Region Name'])"
           ".createOrReplaceTempView('s')\n")


@pytest.mark.parametrize("code", [
    "spark.sql('MERGE WITH SCHEMA EVOLUTION INTO sales t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *')",
    ("spark.conf.set('spark.databricks.delta.schema.autoMerge.enabled', 'true')\n"
     "spark.sql('MERGE INTO sales t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *')"),
    ("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').alias('t')"
     ".merge(spark.table('s').alias('s'), 't.id = s.id').withSchemaEvolution().whenNotMatchedInsertAll().execute()"),
], ids=["sql", "autoMerge", "builder"])
def test_schema_evolution_that_fabric_couldnt_read(lake, code):
    refused(lake, BAD_NEW + code, "Column 'Region Name' has a space or one of , ; { } ( ) =, which Fabric only "
            "reads with column mapping. Rename it in the query (AS a_name).")


# ---- shapes this PC reads but doesn't run

@pytest.mark.parametrize("statement, reason", [
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED AND s.id IN (SELECT id FROM upd) THEN DELETE",
     ("s.id IN (SELECT id FROM upd) has a subquery, which delta-rs can't run inside a condition or value. Join the "
     "rows in with MERGE INTO ... USING (SELECT ...) instead.")),
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = (SELECT max(amount) FROM upd)",
     "has a subquery, which delta-rs can't run inside a condition or value."),
    ("UPDATE sales SET amount = 0 WHERE id IN (SELECT id FROM upd)",
     "has a subquery, which delta-rs can't run inside a condition or value."),
    ("DELETE FROM sales WHERE id IN (SELECT id FROM upd)",
     "has a subquery, which delta-rs can't run inside a condition or value."),
    ("DELETE FROM sales WHERE EXISTS (SELECT 1 FROM upd WHERE upd.id = sales.id)",
     "has a subquery, which delta-rs can't run inside a condition or value."),
    ("UPDATE sales SET amount = u.amount FROM upd u WHERE sales.id = u.id",
     ("UPDATE ... FROM isn't supported on a Delta table. Use MERGE INTO sales USING ... ON ... WHEN MATCHED THEN "
     "UPDATE SET ...")),
    ("DELETE FROM sales USING upd WHERE sales.id = upd.id",
     "DELETE ... USING isn't supported on a Delta table. Use MERGE INTO sales USING ... ON ... WHEN MATCHED THEN DELETE"),
    ("MERGE INTO sales t USING (SELECT 1 AS id) ON t.id = id WHEN MATCHED THEN DELETE",
     "Give the MERGE source an alias: USING (SELECT ...) AS s."),
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED THEN DELETE",
     "WHEN NOT MATCHED can only INSERT: there is no row in the table to delete."),
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED THEN UPDATE SET amount = 0",
     "WHEN NOT MATCHED can only INSERT: there is no row in the table to update."),
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN UPDATE SET *",
     "UPDATE SET * goes with WHEN MATCHED, where there is a source row to copy."),
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN INSERT *",
     "INSERT goes with WHEN NOT MATCHED, where the source row has no row in the table."),
    ("MERGE INTO sales t USING (SELECT 4 AS id) s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *",
     ("UPDATE SET * and INSERT * take every column of sales from the source, and the source has no name, amount. "
     "Name the columns instead.")),
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id)",
     "INSERT VALUES without a column list gives a value for each column of sales in order, and it has 3"),
    ("MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.price = s.amount",
     "sales has no column price."),
    ("UPDATE sales SET price = 0", "sales has no column price."),
    ("UPDATE sales SET *", "* isn't column = value."),
])
def test_a_shape_this_pc_doesnt_run(lake, statement, reason):
    refused(lake, UPD + f"spark.sql({statement!r})", reason)


def test_a_value_the_column_would_change(lake):
    refused(lake, "spark.createDataFrame([(1, 2.75)], 'id INT, n DOUBLE').createOrReplaceTempView('v')\n"
            "spark.sql('MERGE INTO sales t USING v s ON t.id = s.id WHEN MATCHED THEN UPDATE SET id = s.n')",
            "This MERGE would change values in id to fit its type in sales (int32)")


@pytest.mark.parametrize("code", [
    "spark.sql('DELETE FROM sales WHERE id = 1; SELECT 1')",
    "spark.sql('UPDATE sales SET amount = 0; DELETE FROM sales')",
    "spark.sql('MERGE INTO sales t USING upd s ON t.id = s.id WHEN MATCHED THEN DELETE; SELECT 1')",
])
def test_more_than_one_statement_in_the_call(lake, code):
    refused(lake, UPD + code, "spark.sql runs MERGE INTO, UPDATE and DELETE only as the only statement in the call. "
            "Nothing ran. Call spark.sql once for each statement.")


def test_the_merge_builder_without_a_clause(lake):
    refused(lake, "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').alias('t')"
            ".merge(spark.table('sales').alias('s'), 't.id = s.id').execute()",
            "The merge has no whenMatched or whenNotMatched clause, so there is nothing to do.")

