"""spark.sql MERGE INTO on this PC (PB-015, Preview): delta-rs changes the lakehouse table in one
commit, with Spark's clauses and results. Each test checks the real Delta table afterwards."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.fabric_run import compute_check
from fabric_desktop.notebook import Kernel


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: default)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    write_deltalake(str(root / "dbo" / "sales"), pa.table({"id": [1, 2, 3], "region": ["EU", "US", "EU"],
                                                           "amount": [10.0, 20.0, 30.0]}))
    yield root
    lakehouse.attach(None)


def rows(lake, table="sales"):
    return sorted(DeltaTable(str(lake / "dbo" / table)).to_pyarrow_table().to_pylist(), key=lambda r: r["id"])


def run(code):
    out = Kernel().run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out, text


UPDATES = ("spark.createDataFrame([(2, 'US', 25.0, False), (3, 'EU', 0.0, True), (4, 'APAC', 40.0, False)],"
           " ['ID', 'region', 'amount', 'gone']).createOrReplaceTempView('updates')\n")


def test_merge_updates_deletes_and_inserts_in_one_commit(lake):
    before = DeltaTable(str(lake / "dbo" / "sales")).version()
    out, text = run(UPDATES + "r = spark.sql('''MERGE INTO dbo.sales AS t USING updates AS s ON t.id = s.id\n"
                    "  WHEN MATCHED AND s.gone THEN DELETE\n"
                    "  WHEN MATCHED THEN UPDATE SET t.amount = s.amount\n"
                    "  WHEN NOT MATCHED THEN INSERT (id, region, amount) VALUES (s.id, s.region, s.amount)''')\n"
                    "print(r.collect()[0].asDict())")
    assert out["status"] == "ok", text
    assert "'num_affected_rows': 3, 'num_updated_rows': 1, 'num_deleted_rows': 1, 'num_inserted_rows': 1" in text
    assert rows(lake) == [{"id": 1, "region": "EU", "amount": 10.0}, {"id": 2, "region": "US", "amount": 25.0},
                          {"id": 4, "region": "APAC", "amount": 40.0}]
    table = DeltaTable(str(lake / "dbo" / "sales"))
    assert table.version() == before + 1 and table.history(1)[0]["operation"] == "MERGE"


def test_merge_star_clauses_and_not_matched_by_source(lake):
    out, text = run("spark.createDataFrame([(1, 'EU', 11.0), (5, 'US', 50.0)], ['id', 'region', 'amount'])"
                    ".createOrReplaceTempView('s')\n"
                    "spark.sql('MERGE INTO sales USING s ON sales.id = s.id WHEN MATCHED THEN UPDATE SET * "
                    "WHEN NOT MATCHED THEN INSERT * WHEN NOT MATCHED BY SOURCE AND sales.region = \\'US\\' THEN DELETE')")
    assert out["status"] == "ok", text
    assert rows(lake) == [{"id": 1, "region": "EU", "amount": 11.0}, {"id": 3, "region": "EU", "amount": 30.0},
                          {"id": 5, "region": "US", "amount": 50.0}]


def test_a_source_query_reads_lakehouse_tables(lake):
    write_deltalake(str(lake / "dbo" / "raise"), pa.table({"id": [1, 3], "pct": [0.5, 1.0]}))
    out, text = run("spark.sql('MERGE INTO dbo.sales t USING (SELECT id, pct FROM dbo.raise WHERE pct > 0.6) s "
                    "ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = t.amount * (1 + s.pct)')")
    assert out["status"] == "ok", text
    assert [r["amount"] for r in rows(lake)] == [10.0, 20.0, 60.0]


def test_two_source_rows_for_one_table_row_are_refused_and_nothing_changes(lake):
    version = DeltaTable(str(lake / "dbo" / "sales")).version()
    out, text = run("spark.createDataFrame([(1, 5.0), (1, 6.0)], ['id', 'amount']).createOrReplaceTempView('dup')\n"
                    "spark.sql('MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = s.amount')")
    assert out["status"] == "error" and "More than one source row matches" in text
    assert DeltaTable(str(lake / "dbo" / "sales")).version() == version


@pytest.mark.parametrize("statement, reason", [
    ("MERGE INTO nope t USING sales s ON t.id = s.id WHEN MATCHED THEN DELETE", "doesn't exist"),
    ("MERGE INTO sales t USING sales s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *", None),
    ("UPDATE nope SET amount = 0", "doesn't exist"),
    ("UPDATE sales SET amount = 0 WHERE id IN (SELECT id FROM sales)", "subquery"),
])
def test_what_isnt_supported_is_refused_by_name(lake, statement, reason):
    out, text = run(f"spark.sql({statement!r})")
    if reason is None:
        assert out["status"] == "ok", text  # a no-op MERGE of the table with itself runs
        assert [r["amount"] for r in rows(lake)] == [10.0, 20.0, 30.0]
    else:
        assert out["status"] == "error" and reason in text, text
        assert [r["amount"] for r in rows(lake)] == [10.0, 20.0, 30.0]


def test_insert_star_needs_every_column_in_the_source(lake):
    out, text = run("spark.createDataFrame([(9,)], ['id']).createOrReplaceTempView('s')\n"
                    "spark.sql('MERGE INTO sales t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *')")
    assert out["status"] == "error" and "the source has no region, amount" in text


def test_a_table_fabric_couldnt_read_after_the_change_is_left_alone(lake, monkeypatch):
    from fabric_desktop import delta_profile

    def refuse(table):
        raise delta_profile.ProfileViolation("it uses deletion vectors")
    monkeypatch.setattr(delta_profile, "check_table", refuse)
    out, text = run(UPDATES + "spark.sql('MERGE INTO sales t USING updates s ON t.id = s.id "
                              "WHEN MATCHED THEN UPDATE SET amount = s.amount')")
    assert out["status"] == "error" and "isn't changed on this PC" in text and "unreadable in Fabric" in text
    assert [r["amount"] for r in rows(lake)] == [10.0, 20.0, 30.0]


def test_with_preview_features_off_merge_runs_in_fabric_only(lake, monkeypatch):
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: False if key == "show_preview" else default)
    out, text = run(UPDATES + "spark.sql('MERGE INTO sales t USING updates s ON t.id = s.id WHEN MATCHED THEN DELETE')")
    assert out["status"] == "error" and "Preview features are off" in text
    assert len(rows(lake)) == 3


def test_a_managed_table_is_refused(lake, monkeypatch):
    monkeypatch.setattr(lakehouse, "write_guard", lambda lh, schema, table: f"{schema}.{table} is managed")
    out, text = run(UPDATES + "spark.sql('MERGE INTO sales t USING updates s ON t.id = s.id WHEN MATCHED THEN DELETE')")
    assert out["status"] == "error" and "dbo.sales is managed" in text
    assert len(rows(lake)) == 3


def test_the_compute_check_lets_merge_run_on_this_pc():
    def local(source):
        return compute_check([{"type": "code", "source": source}], None)["local"]
    assert local("spark.sql('MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE')")["ok"]
    assert local("%%sql\nMERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")["ok"]
    assert local("spark.sql('DELETE FROM t WHERE id = 1')")["ok"]
    assert local("%%sql\nUPDATE t SET a = 1 WHERE id = 2")["ok"]
    assert not local("%%sql\nUPDATE LH_Keys.keys.customer SET a = 1")["ok"]
    assert local("spark.sql('MERGE INTO t USING s ON t.r = \\'EU\\' WHEN MATCHED THEN DELETE')")["ok"]
    # A table named only at run time could be another lakehouse's: Unknown, not runs here (PB-014).
    dynamic = local('spark.sql(f"MERGE INTO {target} USING s ON t.id = s.id WHEN MATCHED THEN DELETE")')
    assert not dynamic["ok"] and dynamic["status"] == "unknown" and not dynamic["reasons"]


def test_the_compute_check_sends_a_python_cell_merge_it_cant_run_to_fabric(monkeypatch):
    def local(source):
        return compute_check([{"type": "code", "source": source}], None)["local"]
    other = local('spark.sql("""MERGE INTO LH_Keys.keys.customer k USING s ON k.bk = s.bk\n'
                  'WHEN NOT MATCHED THEN INSERT *""")')
    assert not other["ok"] and "of another lakehouse" in other["reasons"][0]["text"]
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: False if key == "show_preview" else default)
    assert not local("spark.sql('MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE')")["ok"]


def test_in_a_proof_merge_changes_the_proofs_copy_never_the_shared_table(lake, tmp_path, monkeypatch):
    from fabric_desktop.proof_runtime import ProofRuntime
    rt = ProofRuntime({"id": "m1", "pins": {"dbo.sales": 0}}, "pc").bind(tmp_path)
    monkeypatch.setattr(lakehouse, "proof", rt)
    monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)
    try:
        for _ in range(2):  # the second MERGE changes the proof's copy as the first left it
            out, text = run(UPDATES + "spark.sql('MERGE INTO sales t USING updates s ON t.id = s.id "
                                      "WHEN MATCHED THEN UPDATE SET amount = t.amount + 1')")
            assert out["status"] == "ok", text
    finally:
        rt.restore()
    assert [r["amount"] for r in rows(lake)] == [10.0, 20.0, 30.0]
    copy = DeltaTable(rt.sandbox_table("dbo.sales")).to_pyarrow_table().to_pylist()
    assert sorted(r["amount"] for r in copy) == [10.0, 22.0, 32.0]
    assert rt.log["reads"] == {"dbo.sales": 0}


def test_a_sql_cell_and_the_sql_line_magic_run_merge_too(lake):
    k = Kernel()
    k.run(UPDATES)
    out = k.run("%%sql\nMERGE INTO dbo.sales t USING updates s ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = s.amount")
    assert out["status"] == "ok", out
    assert out["outputs"][0]["rows"] == [[2, 2, 0, 0]]
    out = k.run("r = %sql MERGE INTO sales t USING updates s ON t.id = s.id WHEN MATCHED AND s.gone THEN DELETE\n"
                "r.collect()[0]['num_deleted_rows']")
    assert out["status"] == "ok" and out["outputs"][-1]["text"] == "1", out
    assert rows(lake) == [{"id": 1, "region": "EU", "amount": 10.0}, {"id": 2, "region": "US", "amount": 25.0}]


def test_the_delta_table_merge_builder(lake):
    out, text = run(UPDATES + "from delta.tables import DeltaTable\nfrom pyspark.sql import functions as F\n"
                    "updates = spark.table('updates')\n"
                    "r = (DeltaTable.forName(spark, 'dbo.sales').alias('t')\n"
                    "     .merge(updates.alias('s'), 't.id = s.id')\n"
                    "     .whenMatchedDelete(condition='s.gone')\n"
                    "     .whenMatchedUpdate(set={'amount': F.col('s.amount') * 2})\n"
                    "     .whenNotMatchedInsert(values={'id': 's.id', 'region': 's.region', 'amount': 's.amount'})\n"
                    "     .execute())\n"
                    "print(r.collect()[0].asDict())")
    assert out["status"] == "ok", text
    assert "'num_updated_rows': 1, 'num_deleted_rows': 1, 'num_inserted_rows': 1" in text
    assert rows(lake) == [{"id": 1, "region": "EU", "amount": 10.0}, {"id": 2, "region": "US", "amount": 50.0},
                          {"id": 4, "region": "APAC", "amount": 40.0}]
    # Without aliases the source is "source", and the table goes by its name, as in delta-spark.
    out, text = run("from delta.tables import DeltaTable\n"
                    "src = spark.createDataFrame([(1, 'EU', 1.0)], ['id', 'region', 'amount'])\n"
                    "DeltaTable.forName(spark, 'sales').merge(src, 'sales.id = source.id').whenMatchedUpdateAll().execute()")
    assert out["status"] == "ok", text
    assert rows(lake)[0]["amount"] == 1.0
    out, text = run("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').merge(spark.table('sales'), "
                    "'sales.id = source.id').execute()")
    assert out["status"] == "error" and "no whenMatched or whenNotMatched clause" in text
    out, text = run("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').restoreToVersion(0)")
    assert out["status"] == "error" and "runs in Fabric" in text


def test_auto_merge_on_is_refused_rather_than_dropping_new_columns(lake):
    out, text = run("spark.conf.set('spark.databricks.delta.schema.autoMerge.enabled', 'true')\n"
                    "spark.createDataFrame([(4, 'APAC', 40.0, 'new')], ['id', 'region', 'amount', 'extra'])"
                    ".createOrReplaceTempView('s')\n"
                    "spark.sql('MERGE INTO sales t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *')")
    assert out["status"] == "error" and "autoMerge.enabled is on" in text and "runs in Fabric" in text, text
    assert len(rows(lake)) == 3
    out, text = run("spark.conf.set('spark.databricks.delta.schema.autoMerge.enabled', 'true')\n"
                    "spark.createDataFrame([(4, 'APAC', 40.0, 'new')], ['id', 'region', 'amount', 'extra'])"
                    ".createOrReplaceTempView('s')\n"
                    "from delta.tables import DeltaTable\n"
                    "DeltaTable.forName(spark, 'sales').alias('t').merge(spark.table('s').alias('s'), 't.id = s.id')"
                    ".whenNotMatchedInsertAll().execute()")
    assert out["status"] == "error" and "autoMerge.enabled is on" in text, text
    assert len(rows(lake)) == 3


def test_merge_with_schema_evolution_says_it_runs_in_fabric(lake):
    out, text = run("spark.sql('MERGE WITH SCHEMA EVOLUTION INTO sales t USING sales s ON t.id = s.id "
                    "WHEN MATCHED THEN UPDATE SET *')")
    assert out["status"] == "error" and "WITH SCHEMA EVOLUTION" in text and "runs in Fabric" in text, text
    out, text = run("spark.sql('-- keep new columns\\nMERGE WITH SCHEMA EVOLUTION INTO sales t USING sales s "
                    "ON t.id = s.id WHEN MATCHED THEN UPDATE SET *')")
    assert out["status"] == "error" and "runs in Fabric" in text, text


def test_a_merge_must_be_the_only_statement_and_nothing_runs_otherwise(lake):
    version = DeltaTable(str(lake / "dbo" / "sales")).version()
    out, text = run("spark.sql('MERGE INTO sales t USING sales s ON t.id = s.id WHEN MATCHED THEN DELETE; "
                    "SELECT * FROM no_such_table')")
    assert out["status"] == "error" and "only statement" in text, text
    assert DeltaTable(str(lake / "dbo" / "sales")).version() == version


def test_the_attached_lakehouses_name_can_lead_the_target(lake):
    out, text = run("spark.sql('MERGE INTO Sales.dbo.sales t USING (SELECT 1 AS id, 9.0 AS amount) s ON t.id = s.id "
                    "WHEN MATCHED THEN UPDATE SET amount = s.amount')")
    assert out["status"] == "ok", text
    assert [r["amount"] for r in rows(lake)] == [9.0, 20.0, 30.0]
    from fabric_desktop.spark_local import change_runs_here
    assert change_runs_here("MERGE INTO Sales.dbo.sales t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")
    assert not change_runs_here("MERGE INTO LH_Keys.keys.customer t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")


def test_duplicate_source_rows_may_all_delete_the_same_row(lake):
    out, text = run("spark.createDataFrame([(1,), (1,)], ['id']).createOrReplaceTempView('dup')\n"
                    "spark.sql('MERGE INTO sales t USING dup s ON t.id = s.id WHEN MATCHED THEN DELETE')")
    assert out["status"] == "ok", text
    assert [r["id"] for r in rows(lake)] == [2, 3]
    out, text = run("spark.createDataFrame([(2,), (2,)], ['id']).createOrReplaceTempView('dup2')\n"
                    "spark.sql('MERGE INTO sales t USING dup2 s ON t.id = s.id WHEN MATCHED AND t.id > 0 THEN DELETE')")
    assert out["status"] == "error" and "More than one source row matches" in text


def test_update_and_delete_change_the_table_in_one_commit_each(lake):
    before = DeltaTable(str(lake / "dbo" / "sales")).version()
    out, text = run("r = spark.sql(\"UPDATE dbo.sales SET amount = amount * 2 WHERE region = 'EU'\")\n"
                    "print(r.collect()[0].asDict())")
    assert out["status"] == "ok" and "'num_affected_rows': 2" in text, text
    assert [r["amount"] for r in rows(lake)] == [20.0, 20.0, 60.0]
    out, text = run("print(spark.sql('DELETE FROM sales WHERE amount > 50').collect()[0].asDict())")
    assert out["status"] == "ok" and "'num_affected_rows': 1" in text, text
    assert [r["id"] for r in rows(lake)] == [1, 2]
    table = DeltaTable(str(lake / "dbo" / "sales"))
    assert table.version() == before + 2 and [h["operation"] for h in table.history(2)] == ["DELETE", "UPDATE"]


def test_a_sql_cell_and_the_delta_table_builder_update_and_delete(lake):
    out, text = run("%%sql\nUPDATE Sales.dbo.sales SET region = 'EMEA' WHERE region = 'EU'")
    assert out["status"] == "ok", text
    assert [r["region"] for r in rows(lake)] == ["EMEA", "US", "EMEA"]
    out, text = run("from delta.tables import DeltaTable\nimport pyspark.sql.functions as F\n"
                    "t = DeltaTable.forName(spark, 'dbo.sales')\n"
                    "t.update(F.col('id') == 2, {'amount': F.col('amount') + 1})\n"
                    "t.updateExpr('id = 3', {'amount': '0'})\n"
                    "t.delete(\"region = 'US' AND amount > 20\")")
    assert out["status"] == "ok", text
    assert rows(lake) == [{"id": 1, "region": "EMEA", "amount": 10.0}, {"id": 3, "region": "EMEA", "amount": 0.0}]
    out, text = run("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').delete()")
    assert out["status"] == "ok", text  # no condition: every row, as in Fabric
    assert rows(lake) == []


def test_update_and_delete_are_refused_where_merge_is(lake, monkeypatch):
    out, text = run("spark.createDataFrame([(1,)], ['id']).createOrReplaceTempView('v')\n"
                    "spark.sql('DELETE FROM v WHERE id = 1')")
    assert out["status"] == "error" and "temp view" in text, text
    out, text = run("spark.sql('DELETE FROM sales WHERE id = 1; SELECT 1')")
    assert out["status"] == "error" and "only statement" in text, text
    assert len(rows(lake)) == 3
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: False if key == "show_preview" else default)
    for statement in ("UPDATE sales SET amount = 0", "DELETE FROM sales"):
        out, text = run(f"spark.sql({statement!r})")
        assert out["status"] == "error" and "Preview features are off" in text, text
    assert [r["amount"] for r in rows(lake)] == [10.0, 20.0, 30.0]


def test_the_compute_check_uses_the_notebooks_own_lakehouse_with_none_attached():
    """#967: with no kernel attached, a change named with the notebook's lakehouse runs here."""
    lh = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1"}

    def local(source):
        return compute_check([{"type": "code", "source": source}], lh)["local"]
    assert local("spark.sql('MERGE INTO Sales.dbo.t USING s ON t.id = s.id WHEN MATCHED THEN DELETE')")["ok"]
    assert local("%%sql\nUPDATE Sales.dbo.t SET a = 1")["ok"]
    assert not local("%%sql\nUPDATE LH_Keys.dbo.t SET a = 1")["ok"]
    assert not local("spark.sql('DELETE FROM LH_Keys.dbo.t')")["ok"]
    assert local("# spark.sql('DELETE FROM LH_Keys.dbo.t')\nx = 1")["ok"]  # commented out
    assert not local("x = '#'; spark.sql('DELETE FROM LH_Keys.dbo.t')")["ok"]  # a # in a string isn't a comment


def test_update_and_delete_after_a_leading_comment_run_here(lake):
    out, text = run("spark.sql('-- fix one price\\nUPDATE sales SET amount = 11.0 WHERE id = 1')")
    assert out["status"] == "ok", text
    out, text = run("spark.sql('/* drop it */ DELETE FROM sales WHERE id = 3')")
    assert out["status"] == "ok", text
    assert [r["amount"] for r in rows(lake)] == [11.0, 20.0]
