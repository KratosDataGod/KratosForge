"""#1016: a metadata run reads only its own entity's rows from meta.run_log, meta.watermarks and
meta.loaded_files, in the order Store.meta gives, instead of each whole table."""

import polars as pl
from test_large_tables import DAY1, DAY2, DAY3, Setup, rows
from test_meta import Repo, edit_entity

WATCHED = ("run_log", "watermarks", "loaded_files")


def watch(store, monkeypatch) -> list[str]:
    whole = []
    meta = store.meta

    def meta_(table):
        if table in WATCHED:
            whole.append(table)
        return meta(table)
    monkeypatch.setattr(store, "meta", meta_)
    return whole


def test_an_incremental_run_reads_no_meta_table_whole(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1))
    s.run()
    s.source(rows([10], DAY2))
    whole = watch(s.store, monkeypatch)
    res = s.run(day=DAY2)
    assert whole == []
    assert res.rows_read == 1  # the watermark still stops the rows already loaded


def test_a_landing_run_reads_no_meta_table_whole_and_skips_files_already_loaded(tmp_path, monkeypatch):
    r = Repo(tmp_path / "project")
    edit_entity(r.root, "bronze.orders", 'mode = "full"', 'mode = "incremental"')
    r.reload()
    first = r.engine().run("bronze.orders")
    assert first.status == "ok" and first.rows_read, first.message
    whole = watch(r.dev, monkeypatch)
    res = r.engine(DAY2).run("bronze.orders")
    assert res.status == "ok", res.message
    assert whole == []
    assert res.rows_read == 0  # the files loaded the first time are skipped


def test_meta_where_gives_meta_s_rows_in_meta_s_order(tmp_path):
    s = Setup(tmp_path, True)
    for day, ids in ((DAY1, range(3)), (DAY2, [10]), (DAY3, [11])):
        s.source(rows(ids, day))
        s.run(day=day)
    for table, where in (("run_log", (pl.col("entity") == "bronze.events") & (pl.col("status") == "ok")),
                         ("watermarks", pl.col("entity") == "bronze.events"),
                         ("loaded_files", pl.col("entity") == "bronze.events")):
        assert s.store.meta_where(table, where).equals(s.store.meta(table).filter(where)), table
    assert s.store.meta_where("watermarks", pl.col("entity") == "bronze.events").height == 3
    assert s.store.meta_where("watermarks", pl.col("entity") == "nobody").height == 0


# ----------------------------------------------------------------------------- #1020: run history screens


def test_an_entity_s_page_and_a_run_s_detail_read_no_meta_table_whole(tmp_path, monkeypatch):
    from fabric_desktop.meta.store import Store
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "proj"), "3")
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    svc.meta_run()  # a second run of every entity, so the log holds other runs too
    store = svc._store(svc._project(), "dev")
    every = store.meta("run_log")
    want_runs = every.filter(pl.col("entity") == "silver.orders").reverse().drop("steps")
    run_id = want_runs["run_id"][0]
    want_dq = store.meta("dq_results").filter(pl.col("run_id") == run_id)

    whole, meta = [], Store.meta
    monkeypatch.setattr(Store, "meta", lambda self, table: whole.append(table) or meta(self, table))
    page = svc.meta_entity("silver.orders")
    detail = svc.meta_run_detail(run_id)
    assert whole == []
    assert [r["run_id"] for r in page["runs"]] == want_runs["run_id"].head(10).to_list()
    assert detail["run_id"] == run_id and detail["entity"] == "silver.orders"
    assert len(detail["dq"]) == min(want_dq.height, 100)


# ----------------------------------------------------------------------------- deployments and the other meta tables


def test_the_promote_gate_reads_only_its_own_environments_deployments(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fabric_desktop.meta.engine import Engine, GateError
    from fabric_desktop.meta.store import LocalStore, Store
    store = LocalStore(tmp_path, "test")
    for env, version in (("test", "1.0.0"), ("prod", "0.9.0"), ("test", "1.1.0")):
        store.meta_append("deployments", [{"env": env, "version": version}])
    whole, meta = [], Store.meta
    monkeypatch.setattr(Store, "meta", lambda self, table: whole.append(table) or meta(self, table))
    engine = Engine.__new__(Engine)
    engine.store, engine.project = store, SimpleNamespace(version="1.1.0")
    engine.check_deployed()  # test is on 1.1.0, its latest deployment
    engine.project = SimpleNamespace(version="1.0.0")
    try:
        engine.check_deployed()
        raise AssertionError("an older project passed the gate")
    except GateError as exc:
        assert "test is on metadata 1.1.0" in str(exc)
    assert whole == []


def test_meta_where_matches_meta_then_filter_even_when_rows_share_a_time(tmp_path):
    """Several rows written with the same time keep meta()'s order, on this PC's read and on the
    dataset read OneLake uses, so the latest deployment is the same either way."""
    import datetime as dt

    from deltalake import DeltaTable

    from fabric_desktop.meta.store import META_LAKEHOUSE, LocalStore, lazy
    store = LocalStore(tmp_path, "dev")
    at = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).isoformat()
    for i in range(12):
        store.meta_append("deployments", [{"env": ("prod", "test")[i % 2], "version": f"v{i}", "at": at}])
        store.meta_append("schema_history", [{"entity": f"e{i % 3}", "run_id": f"r{i}", "change": "added", "at": at}])
        store.meta_append("restores", [{"entity": f"e{i % 3}", "at": at}])
    for table, where in (("deployments", pl.col("env") == "prod"), ("schema_history", pl.col("entity") == "e1"),
                         ("restores", pl.col("entity") == "e2"), ("entities", pl.col("version") == "v1"),
                         ("release_files", pl.col("version") == "v1")):
        assert store.meta_where(table, where).equals(store.meta(table).filter(where)), table
    uri = store.uri(META_LAKEHOUSE, "meta", "deployments")
    on_onelake = lazy(DeltaTable(uri), uri, {}).filter(pl.col("env") == "prod").collect().sort("at", maintain_order=True)
    assert on_onelake.equals(store.meta("deployments").filter(pl.col("env") == "prod"))


def test_runs_started_in_the_same_second_never_share_an_id(monkeypatch):
    """Each entity run gets its own id, and a run of every entity starts several in one second. With
    16 random bits two of them shared an id, and a run's detail then showed the other entity's run."""
    import datetime as dt

    from fabric_desktop.meta import engine

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 11, 6, 14, 57, 54)
    monkeypatch.setattr(engine.dt, "datetime", Frozen)
    ids = [engine.new_run_id() for _ in range(5000)]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("r-20261106-145754-") for i in ids)
