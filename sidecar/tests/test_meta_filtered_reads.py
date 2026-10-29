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
