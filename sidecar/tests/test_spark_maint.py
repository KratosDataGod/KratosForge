"""OPTIMIZE, VACUUM and RESTORE from a notebook on this PC (Fabric notebook parity F6, Preview): each runs with
delta-rs on a table of the attached lakehouse behind the app's guards, and each refusal changes nothing."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse
from fabric_desktop.notebook import Kernel
from tests.test_spark_writer import lake  # noqa: F401 - a fixture


@pytest.fixture
def table(lake, monkeypatch):  # noqa: F811
    """dbo.sales in three small commits (versions 0 to 2), and the app's guard allowing the workspace."""
    uri = str(lake / "dbo" / "sales")
    write_deltalake(uri, pa.table({"id": [4], "region": ["APAC"]}), mode="append")
    write_deltalake(uri, pa.table({"id": [5], "region": ["US"]}), mode="append")
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    return lake


def run(code):
    out = Kernel().run(code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


def version(lake):
    return DeltaTable(str(lake / "dbo" / "sales")).version()


def ids(lake, at=None):
    return sorted(r["id"] for r in DeltaTable(str(lake / "dbo" / "sales"), version=at).to_pyarrow_table().to_pylist())


@pytest.mark.parametrize("code", [
    "r = spark.sql('OPTIMIZE dbo.sales')",
    "r = spark.sql('-- compact\\nOPTIMIZE `dbo`.`sales`')",
    "from delta.tables import DeltaTable\nr = DeltaTable.forName(spark, 'dbo.sales').optimize().executeCompaction()",
])
def test_optimize_compacts_into_one_file_and_keeps_every_row(table, code):
    before = ids(table)
    status, text = run(code + "\nprint(r.collect()[0]['metrics']['numFilesRemoved'])")
    assert status == "ok", text
    assert "3" in text and "without V-Order" in text
    t = DeltaTable(str(table / "dbo" / "sales"))
    assert len(t.file_uris()) == 1 and ids(table) == before


def test_optimize_zorder_and_a_where_on_a_column_that_isnt_a_partition(table):
    status, text = run("from delta.tables import DeltaTable\n"
                       "DeltaTable.forName(spark, 'sales').optimize().executeZOrderBy('id')")
    assert status == "ok", text
    v = version(table)
    status, text = run("spark.sql(\"OPTIMIZE sales WHERE region = 'EU'\")")
    assert status == "error" and "only on partition columns" in text, text
    assert version(table) == v


def test_restore_to_a_version_brings_its_rows_back_in_a_new_commit(table):
    status, text = run("r = spark.sql('RESTORE TABLE dbo.sales TO VERSION AS OF 1')\nprint(r.collect()[0].asDict())")
    assert status == "ok", text
    assert ids(table) == [1, 2, 3, 4] and version(table) == 3
    status, text = run("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').restoreToVersion(0)")
    assert status == "ok", text
    assert ids(table) == [1, 2, 3] and version(table) == 4


@pytest.mark.parametrize("sql, says", [
    ("RESTORE TABLE dbo.sales TO VERSION AS OF 2", "earlier one"),  # the current version
    ("RESTORE TABLE dbo.sales TO VERSION AS OF 9", "earlier one"),
    ("RESTORE TABLE dbo.sales", "takes the form"),
    ("VACUUM dbo.sales RETAIN 1 HOURS", "at least 7 days"),
    ("VACUUM dbo.sales RETAIN 0 HOURS DRY RUN", "at least 7 days"),
    ("OPTIMIZE dbo.sales ZORDER BY (nope)", "hasn't got: nope"),
    ("OPTIMIZE dbo.sales; DROP TABLE dbo.sales", "takes the form"),
    ("OPTIMIZE delta.`Tables/dbo/sales`", "not its path"),
    ("VACUUM Other.dbo.sales", "isn't the attached lakehouse"),
])
def test_what_is_refused_changes_nothing(table, sql, says):
    before, rows = version(table), ids(table)
    status, text = run(f"spark.sql({sql!r})")
    assert status == "error" and says in text, text
    assert version(table) == before and ids(table) == rows


def test_vacuum_keeps_seven_days_and_a_dry_run_lists_without_deleting(table):
    run("spark.sql('OPTIMIZE dbo.sales')")  # three files are now unreferenced, but newer than 7 days
    files = sorted(p.name for p in (table / "dbo" / "sales").glob("*.parquet"))
    status, text = run("print(spark.sql('VACUUM dbo.sales DRY RUN').count())\n"
                       "print(spark.sql('VACUUM dbo.sales RETAIN 200 HOURS').collect()[0]['path'])")
    assert status == "ok", text
    assert text.split()[:2] == ["0", "dbo.sales"]
    assert sorted(p.name for p in (table / "dbo" / "sales").glob("*.parquet")) == files
    assert ids(table, at=0) == [1, 2, 3]  # time travel still works


@pytest.mark.parametrize("guard, says", [
    (None, "only inside KratosForge"),  # no app to ask: fail closed
    (lambda op, ws, t: f"{op} on {t}: this workspace is Prod's", "workspace is Prod's"),
])
def test_without_the_apps_go_ahead_nothing_changes(table, monkeypatch, guard, says):
    monkeypatch.setattr(lakehouse, "maintenance_guard", guard)
    before = version(table)
    for sql in ("OPTIMIZE dbo.sales", "VACUUM dbo.sales", "RESTORE TABLE dbo.sales TO VERSION AS OF 0"):
        status, text = run(f"spark.sql({sql!r})")
        assert status == "error" and says in text, (sql, text)
    assert version(table) == before


def test_a_table_a_metadata_project_manages_is_refused(table, monkeypatch):
    monkeypatch.setattr(lakehouse, "write_guard", lambda name, schema, t: "managed by the metadata project")
    status, text = run("spark.sql('OPTIMIZE dbo.sales')")
    assert status == "error" and "managed by the metadata project" in text


def test_with_preview_off_it_runs_in_fabric(table, monkeypatch):
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: False if key == "show_preview" else default)
    status, text = run("spark.sql('OPTIMIZE dbo.sales')")
    assert status == "error" and "Preview feature" in text
    from fabric_desktop import spark_local
    assert spark_local.sql_refusal("VACUUM dbo.sales")[0] == "blocked"


def test_in_a_proof_optimize_and_vacuum_do_nothing_and_restore_is_refused(table, tmp_path, monkeypatch):
    from fabric_desktop.proof_runtime import ProofRuntime
    rt = ProofRuntime({"id": "m6", "pins": {"dbo.sales": 2}}, "pc").bind(tmp_path)
    monkeypatch.setattr(lakehouse, "proof", rt)
    monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)
    before = version(table)
    try:
        status, text = run("spark.sql('OPTIMIZE dbo.sales')\nspark.sql('VACUUM dbo.sales')")
        assert status == "ok" and "in a proof it does nothing" in text, text
        status, text = run("spark.sql('RESTORE TABLE dbo.sales TO VERSION AS OF 0')")
        assert status == "error" and "isn't run in a proof" in text
    finally:
        rt.restore()
    assert version(table) == before


def test_the_engine_refuses_test_and_prod_workspaces_of_the_metadata_project():
    from types import SimpleNamespace

    from fabric_desktop.meta_service import MetaMixin

    envs = {"dev": {"workspace_id": "w-dev"}, "test": {"workspace_id": "w-test"},
            "prod": {"workspace_id": "w-prod", "workspaces": {"gold": {"id": "w-gold"}}}}
    mixin = SimpleNamespace(_project=lambda: SimpleNamespace(environments=envs),
                            _maintenance_capacity_refusal=lambda *a: None, _maintenance_audit=lambda *a: a[-1])
    refuse = MetaMixin._maintenance_refusal
    assert refuse(mixin, "vacuum", "w-dev", "dbo.t") is None
    assert "Test's" in refuse(mixin, "optimize", "w-test", "dbo.t")
    assert "Prod's" in refuse(mixin, "restore", "w-gold", "dbo.t")

    def none():
        raise LookupError("no project")
    assert refuse(SimpleNamespace(_project=none, _maintenance_capacity_refusal=lambda *a: None,
                                  _maintenance_audit=lambda *a: a[-1]),
                  "vacuum", "w-prod", "dbo.t") is None


def test_vacuum_keeps_the_tables_own_longer_retention(table):
    t = DeltaTable(str(table / "dbo" / "sales"))
    t.alter.set_table_properties({"delta.deletedFileRetentionDuration": "interval 30 days"})
    before = version(table)
    status, text = run("spark.sql('VACUUM dbo.sales RETAIN 200 HOURS')")
    assert status == "error" and "keeps 30 days (720 hours)" in text, text
    assert version(table) == before
    status, text = run("print(spark.sql('VACUUM dbo.sales RETAIN 720 HOURS DRY RUN').count())")
    assert status == "ok", text


def test_the_engine_refuses_when_the_capacity_check_is_blocked():
    from types import SimpleNamespace

    from fabric_desktop.meta_service import MetaMixin

    def mixin(cap):
        def read(ws, name, refresh, reads):
            if isinstance(cap, Exception):
                raise cap
            return cap
        m = SimpleNamespace(_project=lambda: SimpleNamespace(environments={}), _risk_capacity=read)
        m._maintenance_capacity_refusal = lambda *a: MetaMixin._maintenance_capacity_refusal(m, *a)
        m._maintenance_audit = lambda *a: MetaMixin._maintenance_audit(m, *a)  # no audit log here: the refusal as is
        return m

    refuse = MetaMixin._maintenance_refusal
    paused = {"access": "ok", "status": "not_running", "capacity": {"name": "F64"}, "age_seconds": 30}
    assert "Blocked (F64 is paused)" in refuse(mixin(paused), "optimize", "w-dev", "dbo.t")
    running = {"access": "ok", "status": "running", "capacity": {"name": "F64"}, "age_seconds": 30}
    assert refuse(mixin(running), "vacuum", "w-dev", "dbo.t") is None
    assert refuse(mixin(None), "vacuum", "w-dev", "dbo.t") is None  # not read: unknown, allowed
    assert refuse(mixin(RuntimeError("offline")), "vacuum", "w-dev", "dbo.t") is None
    assert refuse(mixin(paused), "vacuum", "", "dbo.t") is None  # a lakehouse on this PC only
