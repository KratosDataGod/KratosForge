"""WR-131: every way a notebook cell can delete or replace rows of a lakehouse table is refused in a Prod-marked
workspace until its name is typed (lakehouse.prod_refusal), shown by running each one in a cell.

Each path runs in a cell against a Prod-marked workspace and must be refused with nothing changed. This is
the runtime side of test_prod_write_paths.py (WR-248), which lists every place the engine writes a Delta table
with its gate; here the paths a cell's ordinary Spark and notebook code takes are run end to end.
"""

import json

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse
from tests.test_notebookutils_load_table import files  # noqa: F401 - a fixture
from tests.test_prod_writes import Svc
from tests.test_spark_writer import lake  # noqa: F401 - a fixture

# Each way a cell can delete or replace rows of a lakehouse table. Appends and OPTIMIZE keep every row, so they
# aren't asked (test_prod_writes covers that).
PATHS = {
    "saveAsTable overwrite": "df.write.mode('overwrite').saveAsTable('dbo.sales')",
    "saveAsTable mode argument": "df.write.saveAsTable('dbo.sales', mode='overwrite')",
    "saveAsTable replaceWhere": "df.write.mode('overwrite').option('replaceWhere', \"region = 'EU'\").saveAsTable('dbo.sales')",
    "saveAsTable overwriteSchema": "df.write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable('dbo.sales')",
    "save to a Tables path": "df.write.format('delta').mode('overwrite').save('Tables/dbo/sales')",
    "insertInto overwrite": "df.write.insertInto('dbo.sales', overwrite=True)",
    "writeTo createOrReplace": "df.writeTo('dbo.sales').createOrReplace()",
    "writeTo replace": "df.writeTo('dbo.sales').replace()",
    "INSERT OVERWRITE": "spark.sql(\"INSERT OVERWRITE dbo.sales SELECT 9 AS id, 'EU' AS region\")",
    "INSERT OVERWRITE TABLE": "spark.sql(\"INSERT OVERWRITE TABLE dbo.sales SELECT 9, 'EU'\")",
    "CREATE OR REPLACE TABLE AS": "spark.sql(\"CREATE OR REPLACE TABLE dbo.sales AS SELECT 9 AS id, 'EU' AS region\")",
    "MERGE INTO": "spark.sql(\"MERGE INTO dbo.sales t USING (SELECT 1 AS id, 'Z' AS region) s ON t.id = s.id "
                  "WHEN MATCHED THEN UPDATE SET *\")",
    "UPDATE": "spark.sql(\"UPDATE dbo.sales SET region = 'X'\")",
    "DELETE FROM": "spark.sql('DELETE FROM dbo.sales WHERE id = 1')",
    "VACUUM": "spark.sql('VACUUM dbo.sales')",
    "RESTORE": "spark.sql('RESTORE TABLE dbo.sales TO VERSION AS OF 0')",
    "DeltaTable merge": "from delta.tables import DeltaTable\n"
                        "DeltaTable.forName(spark, 'dbo.sales').alias('t').merge(df.alias('s'), 't.id = s.id')"
                        ".whenNotMatchedInsertAll().whenMatchedUpdateAll().execute()",
    "DeltaTable update": "from delta.tables import DeltaTable\n"
                         "DeltaTable.forName(spark, 'dbo.sales').update(\"id = 1\", {'region': \"'X'\"})",
    "DeltaTable updateExpr": "from delta.tables import DeltaTable\n"
                             "DeltaTable.forName(spark, 'dbo.sales').updateExpr(\"id = 1\", {'region': \"'X'\"})",
    "DeltaTable delete": "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').delete('id = 1')",
    "DeltaTable forPath delete": "from delta.tables import DeltaTable\n"
                                 "DeltaTable.forPath(spark, 'Tables/dbo/sales').delete('id = 1')",
    "DeltaTable vacuum": "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum()",
    "DeltaTable restoreToVersion": "from delta.tables import DeltaTable\n"
                                   "DeltaTable.forName(spark, 'dbo.sales').restoreToVersion(0)",
    "lakehouse.write_table": "import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
                             "lakehouse.write_table(pa.table({'id': [9], 'region': ['EU']}), 'dbo.sales')",
    "notebookutils loadTable Overwrite": "notebookutils.lakehouse.loadTable({'relativePath': 'Files/sales.csv', "
                                         "'pathType': 'File', 'mode': 'Overwrite', 'formatOptions': "
                                         "{'format': 'Csv', 'header': True, 'delimiter': ';'}}, 'sales')",
}


@pytest.fixture
def prod(files, monkeypatch, tmp_path):  # noqa: F811
    """ws1 (the attached lakehouse's workspace) marked Prod, with two commits so RESTORE has a version to go to."""
    (tmp_path / "prod_workspaces.json").write_text(json.dumps({"prod": ["ws1"]}))
    svc = Svc(tmp_path)
    monkeypatch.setattr(lakehouse, "prod_guard", svc._prod_write_refusal)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    write_deltalake(str(files / "dbo" / "sales"), pa.table({"id": [4], "region": ["APAC"]}), mode="append")
    return files


def _run(code):
    from fabric_desktop.notebook import Kernel
    out = Kernel().run("df = spark.createDataFrame([(9, 'EU')], ['id', 'region'])\n" + code)
    enames = [o.get("ename") for o in out["outputs"] if o.get("type") == "error"]
    return out["status"], enames, "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


@pytest.mark.parametrize("code", list(PATHS.values()), ids=list(PATHS))
def test_every_way_a_cell_replaces_or_deletes_rows_waits_for_the_name(prod, code):
    table = DeltaTable(str(prod / "dbo" / "sales"))
    before, rows = table.version(), table.to_pyarrow_table().to_pylist()
    status, enames, text = _run(code)
    assert status == "error" and enames == ["ProdConfirmNeeded"], text
    after = DeltaTable(str(prod / "dbo" / "sales"))
    assert after.version() == before and after.to_pyarrow_table().to_pylist() == rows


def test_without_a_prod_mark_the_same_paths_run(files):  # noqa: F811
    """The guard, not some other refusal, is what stops them above: unmarked, a sample of them runs."""
    for name in ("saveAsTable overwrite", "INSERT OVERWRITE", "DeltaTable delete", "notebookutils loadTable Overwrite"):
        status, _, text = _run(PATHS[name])
        assert status == "ok", f"{name}: {text}"
