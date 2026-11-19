"""WR-248: every way the engine writes a Delta table has a named gate, and the ones that can reach Prod need it.

The rule (Chris, 2026-11-17): a write to Prod waits for a typed name. A warehouse change needs the warehouse's
name (WR-209, test_warehouse_write_guard.py). A notebook cell's MERGE, UPDATE, DELETE, VACUUM, RESTORE or overwrite
needs the workspace's name (prod_writes_service.py, test_prod_writes.py). Test and Prod lakehouses otherwise change
only by promote.

This file has three parts:
- Every place in the engine that commits to a Delta table (write_deltalake, publish, write_table, change_table,
  maintain_table, vacuum, restore, optimize) is listed in WRITE_SITES with the gate it goes through. A new one fails
  here until someone says how it's gated.
- Each write statement a warehouse script can hold (CTAS, SELECT INTO, COPY INTO, MERGE, TRUNCATE, DROP and the
  rest, hidden after a comment, a CTE or another statement) is a change, never a read. A change can't start without
  the confirmed name.
- Two paths that skipped the typed name before WR-248:
  - The SQL page's Save as table replaced a table's rows whenever it was called with mode "overwrite". The page
    asked for the table's name, but the engine didn't check it.
  - A job ("Run on this PC") could write a workspace a project named Prod once no project was open.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fabric_desktop import publish, warehouse
from fabric_desktop.service import Service

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
WRITES = {"write_deltalake", "publish", "write_table", "change_table", "maintain_table", "vacuum", "restore",
          "z_order", "compact", "create_write_transaction", "convert_to_deltalake"}
NOT_DELTA = {("pq", "write_table"), ("pyarrow.parquet", "write_table"), ("papq", "write_table")}

CELL = "a notebook cell: lakehouse.prod_refusal asks for the Prod workspace's name (test_prod_writes.py)"
LOCAL = "this PC only: a local data project, a proof's or replay's own copy, or staging, never OneLake"
WRITE_SITES = {
    # notebook and Spark-on-this-PC writes, all through lakehouse.py's three entry points
    ("lakehouse.py", "write_table", "publish"): CELL + "; an overwrite asks, an append or a new table doesn't",
    ("lakehouse.py", "change_table", "write_deltalake"): CELL,
    ("lakehouse.py", "maintain_table", "t.optimize.compact"): "OPTIMIZE keeps the rows; Test and Prod refuse it from "
                                                             "code (meta_service._maintenance_refusal)",
    ("lakehouse.py", "maintain_table", "t.optimize.z_order"): "as compact",
    ("lakehouse.py", "maintain_table", "t.vacuum"): CELL,
    ("lakehouse.py", "maintain_table", "t.restore"): CELL,
    ("lakehouse.py", "_write_proof", "write_deltalake"): LOCAL,
    ("spark_local.py", "_write_table", "lakehouse.write_table"): CELL,
    ("spark_local.py", "LocalSparkSession._statement", "lakehouse.write_table"): CELL,
    ("spark_local.py", "LocalSparkSession._merge", "lakehouse.change_table"): CELL,
    ("spark_local.py", "LocalSparkSession._update_or_delete", "lakehouse.change_table"): CELL,
    ("spark_maint.py", "run", "lakehouse.maintain_table"): CELL,
    ("proof_spark.py", "ProofSpark.install", "me.write_table"): LOCAL,
    # the app's own pages
    ("service.py", "Service.save_sql", "lakehouse.write_table"): "Save as table: replacing a table needs its name "
                                                                "typed, checked by the engine (WR-248)",
    ("publish.py", "run_job", "publish"): "a job: meta_service._job_guard refuses Test and Prod, and with no project "
                                          "open a workspace named Prod before (WR-248)",
    ("publish.py", "publish", "write_deltalake"): "the write itself; its callers are listed here",
    ("publish.py", "stage_locally", "write_deltalake"): LOCAL,
    # the metadata framework: Test and Prod change only by promote, behind its gates and the audit line
    ("meta/store.py", "Store.write", "write_deltalake"): "the framework's run log and entity tables, written by a run "
                                                        "the promote and approval gates allow",
    ("meta/store.py", "Store.write_stream", "write_deltalake"): "as Store.write",
    ("meta/store.py", "LocalStore._tidy_meta", "dt_.optimize.compact"): LOCAL,
    ("meta/restore.py", "restore", "table.restore"): "an entity's restore: the duties gate in Test and Prod "
                                                     "(estate_service.meta_restore)",
    ("estate_service.py", "EstateMixin.meta_restore", "restore_mod.restore"): "the duties gate in Test and Prod",
    ("meta/promote.py", "rollback", "snapshot.restore"): "a release's files, not a table: the rollback gate",
    ("meta_service.py", "MetaMixin._released", "snapshot.restore"): "reads a release's files, writes no table",
    # this PC only, or not a Delta table at all
    ("localdata/engine.py", "LocalEngine._write", "write_deltalake"): LOCAL,
    ("maintenance.py", "vacuum_preview", "table.vacuum"): "dry_run=True: lists files, deletes none",
    ("maintenance.py", "_unnamed", "table.vacuum"): "dry_run=True",
    ("maintenance.py", "_named_anywhere", "table.vacuum"): "dry_run=True",
    ("maintenance.py", "compact_local", "DeltaTable(uri).optimize.compact"): LOCAL,
    ("maintenance.py", "compact_local", "table.vacuum"): LOCAL,
    ("prove_service.py", "ProveMixin._replay_seed", "write_deltalake"): LOCAL,
    ("livetest.py", "LiveTest._delta_write", "write_deltalake"): "the live test, run by hand in kf_tenant_test",
    ("livetest.py", "LiveTest._delta_append", "write_deltalake"): "the live test",
    ("livetest.py", "LiveTest._publish", "publish"): "the live test",
    ("livetest.py", "LiveTest._delta_concurrent", "self.write_table"): "the live test",
    ("livetest.py", "LiveTest._merge_profile", "self.write_table"): "the live test",
    ("academy/pyspark_lab.py", "prepare", "deltalake.write_deltalake"): "Academy: a sandbox folder on this PC",
    ("academy/pyspark_shim.py", "Writer.saveAsTable", "deltalake.write_deltalake"): "Academy: a sandbox folder",
    ("sql_worker.py", "run", "writer.write_table"): "a Parquet export of a result, not a table",
    ("cli.py", "_backup", "backup.restore"): "the app's settings backup",
    ("kernel_host.py", "Host.unbind_proof", "self.proof.restore"): "puts a proof's patches back, writes no table",
    ("notebook.py", "Kernel.unbind_proof", "proof.restore"): "as Host.unbind_proof",
    ("notebook_files_service.py", "NotebookFilesMixin.notebook_restore", "files.restore"): "a notebook file's version",
    ("agent.py", "Agent.publish_status", "agent_status.publish"): "the agent's status file",
    ("integrations/mcp_server.py", "Changes.publish", "self.bus.publish"): "an event to MCP clients",
    ("integrations/mcp_server.py", "Changes.check", "self.publish"): "an event to MCP clients",
}


def write_sites() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith("academy/packs/"):  # learners' exercises, run in the Academy's sandbox
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node: ast.AST, owner: str | None, rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, owner or child.name)
                elif isinstance(child, ast.ClassDef):
                    for item in child.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            visit(item, f"{child.name}.{item.name}")
                else:
                    if isinstance(child, ast.Call):
                        fn = child.func
                        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                        base = ast.unparse(fn.value) if isinstance(fn, ast.Attribute) else ""
                        if name in WRITES and (base, name) not in NOT_DELTA:
                            found.add((rel, owner or "<module>", f"{base}.{name}" if base else name))
                    visit(child, owner)
        visit(tree, None)
    return found


def test_every_place_that_writes_a_delta_table_is_listed_with_its_gate():
    found = write_sites()
    new = sorted(found - set(WRITE_SITES))
    assert not new, ("These write a Delta table but aren't in WRITE_SITES. Say which gate each one goes through "
                     f"(a typed name in Prod, promote only, or this PC only), and test that gate: {new}")
    gone = sorted(set(WRITE_SITES) - found)
    assert not gone, f"These are listed but no longer write: take them out of WRITE_SITES: {gone}"


def test_each_cell_write_in_lakehouse_asks_the_prod_guard():
    """lakehouse.py's entry points that a cell's write goes through call prod_refusal before anything commits."""
    tree = ast.parse((PACKAGE / "lakehouse.py").read_text(encoding="utf-8"))
    for name in ("write_table", "change_table", "maintain_table"):
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "prod_refusal" in calls, f"lakehouse.{name} no longer asks prod_refusal"


# ---- a warehouse script: every kind of write is a change, never a read

WAREHOUSE_WRITES = {
    "CTAS": "CREATE TABLE dbo.t AS SELECT 1 AS a",
    "SELECT INTO": "SELECT a INTO dbo.t2 FROM dbo.t",
    "SELECT INTO a temp table": "SELECT a INTO #t FROM dbo.t",
    "COPY INTO": "COPY INTO dbo.t FROM 'https://x.blob.core.windows.net/c/f.csv'",
    "MERGE": "MERGE dbo.t AS t USING dbo.s AS s ON t.a = s.a WHEN MATCHED THEN UPDATE SET t.b = s.b;",
    "TRUNCATE": "TRUNCATE TABLE dbo.t",
    "DROP": "DROP TABLE dbo.t",
    "DROP IF EXISTS": "DROP TABLE IF EXISTS dbo.t",
    "INSERT": "INSERT INTO dbo.t SELECT * FROM dbo.s",
    "UPDATE": "UPDATE dbo.t SET a = 1",
    "DELETE": "DELETE FROM dbo.t",
    "a write after a CTE": "WITH c AS (SELECT 1 AS a) DELETE FROM dbo.t WHERE a IN (SELECT a FROM c)",
    "ALTER": "ALTER TABLE dbo.t ADD c INT",
    "CREATE VIEW": "CREATE VIEW dbo.v AS SELECT 1 AS a",
    "CREATE PROCEDURE": "CREATE PROCEDURE dbo.p AS SELECT 1",
    "EXEC of a procedure": "EXEC dbo.p",
    "dynamic SQL": "EXEC('DROP TABLE dbo.t')",
    "sp_executesql": "EXEC sp_executesql N'DROP TABLE dbo.t'",
    "sp_rename": "EXEC sp_rename 'dbo.t', 't2'",
    "a write after a comment": "/* report */ DROP TABLE dbo.t",
    "a write after a line comment": "-- report\nDROP TABLE dbo.t",
    "a write after a read": "SELECT 1; DROP TABLE dbo.t",
    "a write in a later batch": "SELECT 1\nGO\nDROP TABLE dbo.t",
    "a write after a semicolon": ";DROP TABLE dbo.t",
    "a write in IF": "IF 1 = 1 DROP TABLE dbo.t",
    "a write in BEGIN ... END": "BEGIN DROP TABLE dbo.t END",
    "a write in TRY": "BEGIN TRY DROP TABLE dbo.t END TRY BEGIN CATCH END CATCH",
    "a write in WHILE": "WHILE 1 = 0 DELETE FROM dbo.t",
    "UPDATE STATISTICS": "UPDATE STATISTICS dbo.t",
    "a clone": "CREATE TABLE dbo.c AS CLONE OF dbo.t",
    "INSERT ... EXEC": "INSERT INTO dbo.t EXEC dbo.p",
}


@pytest.mark.parametrize("sql", WAREHOUSE_WRITES.values(), ids=WAREHOUSE_WRITES.keys())
def test_a_warehouse_write_is_never_run_as_a_read(sql):
    try:
        found = warehouse.verdicts(sql)
    except ValueError:
        return  # not readable, so it isn't shown to only read and doesn't run
    assert any(v.kind != "read_only" for v in found), found


@pytest.mark.parametrize("sql", WAREHOUSE_WRITES.values(), ids=WAREHOUSE_WRITES.keys())
def test_with_changes_allowed_a_warehouse_write_is_a_change_or_refused(sql):
    found = warehouse.verdicts(sql, writes=True)
    assert found[-1].kind in ("warehouse_write", "fabric_mutation"), found


# ---- Save as table: the engine checks the typed name before it replaces a table

@pytest.fixture
def saving(tmp_path, monkeypatch):
    from fabric_desktop import lakehouse, sql
    svc = Service(root=tmp_path / "app")
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(svc, "_attach", lambda ref: None)
    monkeypatch.setattr(sql, "run", lambda query, dialect: query)
    monkeypatch.setattr(lakehouse, "table_exists", lambda table: table == "dbo.sales")

    class Written:
        def __init__(self, table):
            self.table, self.rows = table, 1

        def __repr__(self):
            return f"wrote {self.table}"
    monkeypatch.setattr(lakehouse, "write_table", lambda data, table, mode: written.append((table, mode)) or Written(table))
    return svc, written


def test_replacing_a_table_without_its_typed_name_writes_nothing(saving):
    svc, written = saving
    with pytest.raises(PermissionError, match="type its name"):
        svc.save_sql("SELECT 1", {}, "dbo.sales", mode="overwrite")
    with pytest.raises(PermissionError, match="type its name"):
        svc.save_sql("SELECT 1", {}, "dbo.sales", mode="overwrite", confirm_name="dbo.Sales")  # exactly
    assert written == []


def test_replacing_a_table_with_its_typed_name_writes_it(saving):
    svc, written = saving
    assert svc.save_sql("SELECT 1", {}, "dbo.sales", mode="overwrite", confirm_name="dbo.sales")["table"] == "dbo.sales"
    assert written == [("dbo.sales", "overwrite")]


def test_a_new_table_and_an_append_need_no_name(saving):
    svc, written = saving
    assert svc.save_sql("SELECT 1", {}, "dbo.sales") == {"table": "dbo.sales", "exists": True}
    svc.save_sql("SELECT 1", {}, "dbo.fresh")
    svc.save_sql("SELECT 1", {}, "dbo.sales", mode="append")
    svc.save_sql("SELECT 1", {}, "dbo.other", mode="overwrite")  # not there yet, so nothing is replaced
    assert written == [("dbo.fresh", "overwrite"), ("dbo.sales", "append"), ("dbo.other", "overwrite")]


def test_an_unknown_mode_is_refused(saving):
    svc, written = saving
    with pytest.raises(ValueError, match="mode must be"):
        svc.save_sql("SELECT 1", {}, "dbo.sales", mode="replace")
    assert written == []


def test_the_page_sends_the_typed_name_with_a_replace():
    text = (PACKAGE.parents[1] / "app" / "ui" / "query.js").read_text(encoding="utf-8")
    assert 'mode: "overwrite", dialect: tab.dialect, confirm_name: name }' in text
    assert 'typeToConfirm: name' in text


# ---- a job never writes a workspace named Prod, with a project open or not

def test_a_job_never_writes_a_workspace_remembered_as_prod_with_no_project_open(tmp_path, monkeypatch):
    svc = Service(root=tmp_path / "app")
    svc.prod_workspaces_file.parent.mkdir(parents=True, exist_ok=True)
    svc.prod_workspaces_file.write_text('{"prod": ["ws-prod"]}', encoding="utf-8")
    ran: list[object] = []
    monkeypatch.setattr(publish, "run_local", lambda job: ran.append(job))
    from fabric_desktop.jobs import Job, Target
    job = Job("j", "sql", tmp_path, Target("WS-PROD", "LH_Gold", "t"), sql="SELECT 1")
    with pytest.raises(PermissionError, match="named Prod by a project opened on this PC"):
        publish.run_job(job, None, lambda: "tok", tmp_path, guard=svc._job_guard)
    assert not ran
    assert svc._job_guard("ws-scratch", "LH_Scratch", "dbo", "t") is None  # any other workspace, as before
