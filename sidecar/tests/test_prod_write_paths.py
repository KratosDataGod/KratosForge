"""WR-131: every way a notebook cell can delete or replace rows of a lakehouse table goes through the Prod
typed-name guard (lakehouse.prod_refusal), and a new way to write a Delta table can't be added without a
decision about it.

The runtime half runs each path in a cell against a Prod-marked workspace and checks it is refused with nothing
changed. The static half lists every call in fabric_desktop that writes or changes a Delta table, by file, so a
new one fails here until it is routed through lakehouse's guarded writers or listed with its reason."""

import ast
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse
from tests.test_notebookutils_load_table import files  # noqa: F401 - a fixture
from tests.test_prod_writes import Svc
from tests.test_spark_writer import lake  # noqa: F401 - a fixture

PACKAGE = Path(lakehouse.__file__).parent

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


# --- static half: every call in the package that writes or changes a Delta table ---------------------------------

# A Delta table is written or changed only through these (deltalake's API).
_WRITERS = {"write_deltalake", "create_write_transaction", "convert_to_deltalake"}
_TABLE_CHANGES = {"merge", "delete", "update", "restore", "vacuum", "create_checkpoint", "cleanup_metadata",
                  "alter", "compact", "z_order", "set_table_properties", "add_constraint", "drop_constraint",
                  "add_columns", "add_feature"}

# Where each such call is, and why it's fine. A new one fails test_no_new_delta_write_skips_a_decision: route a
# cell-reachable write through lakehouse.write_table / change_table / maintain_table (which ask prod_refusal),
# or add it here with the reason it isn't a notebook cell's.
KNOWN = {
    "lakehouse.py": "guarded: prod_refusal runs first in write_table (overwrite), change_table and maintain_table; "
                    "a proof writes its own copy",
    "publish.py": "write_table's commit (after its prod_refusal), and the app's Publish page, which confirms its own way",
    "delta_dml.py": "the MERGE/UPDATE/DELETE engine, called by lakehouse.change_table (after prod_refusal) and Local data",
    "maintenance.py": "X-ray's Optimize/Vacuum: the app's own page, which shows the plan and confirms first",
    "localdata/engine.py": "Local data: this PC's own Delta folders, never a lakehouse in a workspace",
    "livetest.py": "the live test harness's own scratch tables",
    "meta/store.py": "a metadata project's run: Test and Prod change only by promote, which the app confirms",
    "meta/restore.py": "Undo a bad load, from the metadata project's page, which confirms first",
    "prove_service.py": "a proof's seed copies on this PC, never the shared table",
    "academy/pyspark_delta.py": "an Academy exercise's own tables, in its sandbox workspace",
    "academy/pyspark_lab.py": "an Academy exercise's own tables, in its sandbox workspace",
    "academy/pyspark_shim.py": "an Academy exercise's own tables, in its sandbox workspace",
}


def _delta_calls() -> Counter:
    found: Counter = Counter()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith("academy/packs/"):
            continue  # exercise files, run in the Academy's sandbox
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name in _WRITERS:
                found[rel] += 1
            elif name in _TABLE_CHANGES and isinstance(f, ast.Attribute) and _on_delta(f.value, node):
                found[rel] += 1
    return found


def _on_delta(value: ast.AST, call: ast.Call) -> bool:
    """Whether a method is called on a Delta table: one built or named as one (DeltaTable(...), self._table(),
    t.optimize), or deltalake's own keywords (update's updates=/predicate=). A dict's update and a file store's
    delete aren't."""
    text = ast.unparse(value)
    if "DeltaTable" in text or text.endswith((".optimize", "_table()")) or "delta" in text.lower():
        return True
    keywords = {k.arg for k in call.keywords}
    if keywords & {"updates", "predicate", "retention_hours", "source_alias", "target_alias", "ignore_missing_files"}:
        return True
    return call.func.attr in {"merge", "restore", "vacuum", "compact", "z_order"} and text.split(".")[0] in {  # type: ignore[attr-defined]
        "dt", "t", "table", "target", "committed", "tbl", "current"}


def test_no_new_delta_write_skips_a_decision():
    unknown = sorted(f for f in _delta_calls() if f not in KNOWN)
    assert not unknown, (
        f"{unknown} write or change a Delta table. If a notebook cell can reach it, route it through "
        "lakehouse.write_table / change_table / maintain_table, which ask the Prod guard; otherwise add it to "
        "KNOWN with the reason it isn't a cell's.")


def test_the_known_list_has_no_stale_entries():
    stale = sorted(f for f in KNOWN if f not in _delta_calls())
    assert not stale, f"{stale} no longer write Delta tables: take them out of KNOWN."


def test_the_guarded_writers_ask_before_they_write():
    """In lakehouse.py, write_table, change_table and maintain_table ask prod_refusal before their first Delta write."""
    tree = ast.parse((PACKAGE / "lakehouse.py").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ("write_table", "change_table", "maintain_table"):
        calls = [n for n in ast.walk(funcs[name]) if isinstance(n, ast.Call)]
        asked = min(n.lineno for n in calls if getattr(n.func, "id", getattr(n.func, "attr", "")) == "prod_refusal")
        writes = [n.lineno for n in calls if getattr(n.func, "id", getattr(n.func, "attr", "")) in
                  _WRITERS | {"restore", "vacuum", "compact", "z_order", "apply", "_write", "_publish", "write"}]
        assert all(asked < line for line in writes), name


def test_the_static_check_finds_a_new_delta_write(tmp_path, monkeypatch):
    """The check itself: a module that writes a Delta table directly is reported."""
    (tmp_path / "fabric_desktop").mkdir()
    (tmp_path / "fabric_desktop" / "sneaky.py").write_text(
        "from deltalake import DeltaTable, write_deltalake\n"
        "def f(uri, data):\n    write_deltalake(uri, data, mode='overwrite')\n"
        "def g(uri):\n    DeltaTable(uri).delete('id = 1')\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE", tmp_path / "fabric_desktop")
    assert _delta_calls() == Counter({"sneaky.py": 2})
