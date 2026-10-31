"""Side by side: the same notebook on this PC and in Fabric, compared by time and results."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import benchmark, lakehouse
from fabric_desktop import side_by_side as sbs
from fabric_desktop import side_by_side_service
from fabric_desktop.notebook import Kernel
from fabric_desktop.server import handle
from fabric_desktop.service import Service

LAKEHOUSE = {"workspace_id": "ws", "workspace_name": "Data", "lakehouse_id": "lh", "name": "LH_Bench"}


class LocalTables(sbs.Tables):
    """A lakehouse's Tables/ folder on disk: <root>/<schema>/<table>."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, table: str) -> Path:
        schema, _, name = table.rpartition(".")
        return self.root / (schema or "dbo") / name

    def versions(self):
        out = {}
        for d in self.root.glob("*/*"):
            if (d / "_delta_log").exists():
                out[f"{d.parent.name}.{d.name}"] = DeltaTable(str(d)).version()
        return out

    def history(self, table):
        return DeltaTable(str(self.path(table))).history()

    def dataset(self, table, version):
        return DeltaTable(str(self.path(table)), version=version).to_pyarrow_dataset()

    def write(self, data, table, mode="overwrite"):
        if not isinstance(data, pa.Table):
            data = lakehouse._to_arrow(data)
        write_deltalake(str(self.path(table)), data, mode=mode,
                        schema_mode="overwrite" if mode == "overwrite" else None)


@pytest.fixture
def tables(tmp_path):
    return LocalTables(tmp_path / "Tables")


# ---- the checksum

def test_checksum_ignores_row_order():
    a = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    b = pa.table({"id": [3, 1, 2], "name": ["c", "a", "b"]})
    assert sbs.fingerprint(a) == sbs.fingerprint(b)


def test_checksum_sees_one_changed_value():
    a = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    b = pa.table({"id": [1, 2, 3], "name": ["a", "b", "x"]})
    assert sbs.fingerprint(a)["checksum"] != sbs.fingerprint(b)["checksum"]


def test_checksum_ignores_column_order_and_integer_width():
    a = pa.table({"id": pa.array([1, 2], pa.int32()), "v": ["x", "y"]})
    b = pa.table({"v": ["x", "y"], "id": pa.array([1, 2], pa.int64())})
    assert sbs.fingerprint(a)["checksum"] == sbs.fingerprint(b)["checksum"]


def test_decimal_and_double_with_tiny_rounding_noise_match():
    # Spark's avg of a decimal is a decimal; DuckDB's is a double that can differ in the last bits.
    a = pa.table({"avg": pa.array([Decimal("25.5100000000"), Decimal("38273.1297346212")], pa.decimal128(20, 10))})
    b = pa.table({"avg": [25.510000000000002, 38273.12973462121]})
    assert sbs.fingerprint(a)["checksum"] == sbs.fingerprint(b)["checksum"]
    assert sbs.fingerprint(a)["columns"] == sbs.fingerprint(b)["columns"] == [["avg", "number"]]


def test_timestamps_with_and_without_time_zone_match():
    t = dt.datetime(2026, 10, 16, 12, 30)
    a = pa.table({"at": pa.array([t], pa.timestamp("us"))})
    b = pa.table({"at": pa.array([t.replace(tzinfo=dt.timezone.utc)], pa.timestamp("us", tz="UTC"))})
    assert sbs.fingerprint(a)["checksum"] == sbs.fingerprint(b)["checksum"]


# ---- what each run wrote, and whether it matches

def test_overwrite_in_both_runs_matches(tables):
    tables.write(pa.table({"id": [1]}), "dbo.old")  # there before, written by neither run
    before = tables.versions()
    tables.write(pa.table({"id": [1, 2]}), "bench.q01")
    after_local = tables.versions()
    tables.write(pa.table({"id": [2, 1]}), "bench.q01")
    after_fabric = tables.versions()
    rows = sbs.compare(tables, before, after_local, after_fabric)
    assert [r["table"] for r in rows] == ["bench.q01"]
    assert rows[0]["result"] == "match" and rows[0]["columns"] == "same"
    assert rows[0]["local"]["version"] == 0 and rows[0]["fabric"]["version"] == 1
    assert sbs.verdict(rows, "ok", "ok") == {"same": True, "text": "Same results in both places (1 table)."}


def test_different_answer_is_flagged(tables):
    before = tables.versions()
    tables.write(pa.table({"id": [1, 2]}), "bench.q01")
    after_local = tables.versions()
    tables.write(pa.table({"id": [1, 3]}), "bench.q01")
    rows = sbs.compare(tables, before, after_local, tables.versions())
    assert rows[0]["result"] == "differs"
    assert sbs.verdict(rows, "ok", "ok")["same"] is False


def test_appends_compare_the_rows_each_run_added(tables):
    tables.write(pa.table({"id": [0]}), "dbo.events")
    before = tables.versions()
    tables.write(pa.table({"id": [1, 2]}), "dbo.events", "append")
    after_local = tables.versions()
    tables.write(pa.table({"id": [2, 1]}), "dbo.events", "append")
    rows = sbs.compare(tables, before, after_local, tables.versions())
    assert rows[0]["result"] == "match"
    assert rows[0]["local"]["rows"] == rows[0]["fabric"]["rows"] == 2
    assert rows[0]["fabric"]["table_rows"] == 5
    assert "added" in rows[0]["note"]


def test_table_only_one_side_wrote(tables):
    before = tables.versions()
    tables.write(pa.table({"id": [1]}), "dbo.here")
    after_local = tables.versions()
    tables.write(pa.table({"id": [1]}), "dbo.there")
    rows = {r["table"]: r for r in sbs.compare(tables, before, after_local, tables.versions())}
    assert rows["dbo.here"]["result"] == "only_local"
    assert rows["dbo.there"]["result"] == "only_fabric"


def test_column_differences_are_named(tables):
    before = tables.versions()
    tables.write(pa.table({"id": [1], "a": ["x"]}), "dbo.t")
    after_local = tables.versions()
    tables.write(pa.table({"id": ["1"], "b": ["x"]}), "dbo.t")
    rows = sbs.compare(tables, before, after_local, tables.versions())
    assert rows[0]["result"] == "differs"
    assert "only on this PC: a" in rows[0]["columns"] and "only in Fabric: b" in rows[0]["columns"]
    assert "id (integer vs string)" in rows[0]["columns"]


def test_write_mode_from_commits():
    assert sbs.write_mode([{"operation": "WRITE", "operationParameters": {"mode": "Overwrite"}}]) == "overwrite"
    assert sbs.write_mode([{"operation": "CREATE OR REPLACE TABLE AS SELECT"}]) == "overwrite"
    assert sbs.write_mode([{"operation": "WRITE", "operationParameters": {"mode": "Append"}}] * 2) == "append"
    assert sbs.write_mode([{"operation": "MERGE"}]) == "in place"


def test_verdict_and_speed():
    assert "failed on this PC" in sbs.verdict([], "failed", "ok")["text"]
    assert sbs.verdict([], "ok", "ok")["same"] is None
    s = sbs.speed(10, 40, 20)
    assert s["end_to_end"] == "This PC was 4.0x faster end to end"
    assert s["run"].startswith("This PC was 2.0x faster on run time alone")
    assert sbs.speed(40, 10, None) == {"end_to_end": "Fabric was 4.0x faster end to end", "run": "", "processing": ""}


def test_timing_cells_write_where_the_app_reads():
    first, last = sbs.timing_cells("abc")
    ns = {}
    exec(first["source"], ns)
    assert "_fd_started" in ns
    assert f"/lakehouse/default/{sbs.TIMING_DIR}" in last["source"] and "abc.json" in last["source"]
    assert sbs.parse_timing(b'{"seconds": 12.5}') == 12.5 and sbs.parse_timing(None) is None


# ---- the whole run, called the way the app calls it

class FakeAuth:
    def storage_token(self):
        return "t"

    def fabric_token(self):
        return "t"


class FakeFabric:
    """Publishes, and 'runs' the notebook by doing what Fabric would: write the tables."""

    def __init__(self, tables, fabric_writes):
        self.tables, self.fabric_writes = tables, fabric_writes
        self.saved = []
        self.timing = None

    def save_notebook(self, ws, name, ipynb):
        self.saved.append(json.loads(ipynb))
        return {"id": "nb1", "displayName": name}

    def get_lakehouse(self, ws, lh):
        return {"properties": {"defaultSchema": "dbo"}}

    def run_item_job(self, ws, item, job, execution_data=None):
        self.execution_data = execution_data
        self.fabric_writes(self.tables)
        return "job1"

    def job_instance(self, ws, item, job):
        return {"status": "Completed", "startTimeUtc": "2026-10-16T10:00:00Z", "endTimeUtc": "2026-10-16T10:01:30Z"}

    def cancel_job(self, ws, item, job):
        pass


class FakeFiles:
    def __init__(self, token):
        pass

    def read_bytes(self, ws, lh, path):
        return b'{"seconds": 60}'

    def delete(self, ws, lh, path):
        pass


class SbsService(Service):
    fake = None
    fake_tables = None

    @property
    def auth(self):
        return FakeAuth()

    @property
    def fabric(self):
        return self.fake

    @property
    def fabric_jobs(self):
        return self.fake

    def _sbs_tables(self, lakehouse_ref):
        return self.fake_tables


@pytest.fixture
def svc(tmp_path, tables, monkeypatch):
    monkeypatch.setattr(side_by_side_service, "POLL_SECONDS", 0)
    monkeypatch.setattr(sbs, "DEFAULT_CORES", "all")  # in the engine, as the fakes need; test_side_by_side_cores.py caps
    monkeypatch.setattr(side_by_side_service, "FileStore", FakeFiles)
    monkeypatch.setattr(lakehouse, "write_table", tables.write)
    monkeypatch.setattr(lakehouse, "open_table", lambda t: DeltaTable(str(tables.path(t))))
    s = SbsService(root=tmp_path / "app")
    s.fake_tables = tables
    return s


def call(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def wait(svc, run_id):
    for _ in range(600):
        status = call(svc, "side_by_side_status", id=run_id)
        if status["state"] not in ("local", "fabric", "checking"):
            return status
        time.sleep(0.05)
    raise AssertionError("side by side didn't finish")


CELLS = [{"type": "markdown", "source": "# Test", "meta": {}},
         {"type": "code", "source": "from fabric_desktop import lakehouse\nimport pyarrow as pa\nx = 2", "meta": {}},
         {"type": "code", "source": "lakehouse.write_table(pa.table({'id': [1, x]}), 'bench.q01')\nprint('wrote')", "meta": {}}]


def payload(cells=CELLS):
    # Exactly what the notebook screen sends (notebookPayload in app.js).
    return {"name": "NB", "cells": cells, "lakehouse_ref": LAKEHOUSE, "compute": "default", "workspace": None,
            "confirm_writes": ["bench.q01"]}


def test_side_by_side_end_to_end_same_results(svc, tables):
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [2, 1]}), "bench.q01"))
    started = call(svc, "side_by_side_start", **payload())
    out = wait(svc, started["id"])
    assert out["state"] == "done", out["message"]
    assert out["local"]["status"] == "ok" and out["local"]["seconds"] is not None
    local = out["local"]
    assert local["processing_seconds"] + local["upload_wait_seconds"] == pytest.approx(local["seconds"], abs=0.02)
    assert out["local"]["cells"]["2"]["outputs"][0]["text"].startswith("wrote")
    f = out["fabric"]
    assert f["status"] == "ok" and f["job_seconds"] == 90 and f["run_seconds"] == 60 and f["start_seconds"] == 30
    assert f["cu_seconds"] == 90.0 and f["engine"] == "python"
    assert out["verdict"]["same"] is True
    assert [r["result"] for r in out["tables"]] == ["match"]
    # Fabric got the timing cells; the notebook on this PC didn't.
    published = ["".join(c["source"]) for c in svc.fake.saved[-1]["cells"]]
    assert any("start the clock" in s for s in published) and any(out["id"] in s for s in published)
    assert not any("start the clock" in c["source"] for c in svc.notebooks.load("NB")["cells"])
    # Kept for later, without the cell outputs; shows in Monitor's Fabric runs.
    listed = call(svc, "side_by_side_list", name="NB")
    assert listed[0]["id"] == out["id"] and "cells" not in listed[0]["local"]
    assert svc.fabric_runs(refresh=False)[0]["side_by_side"] == out["id"]


def test_side_by_side_uses_the_repository_location_without_saving_it_into_the_notebook(svc, tables):
    # A notebook in a notebooks repository with no lakehouse of its own (publishPayload's location_ref).
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [2, 1]}), "bench.q01"))
    started = call(svc, "side_by_side_start", **payload() | {"lakehouse_ref": None, "location_ref": LAKEHOUSE})
    out = wait(svc, started["id"])
    assert out["state"] == "done", out["message"]
    assert out["lakehouse"]["lakehouse_id"] == LAKEHOUSE["lakehouse_id"]
    assert svc.notebooks.load("NB").get("lakehouse") is None  # it keeps following the environment


def test_side_by_side_names_the_code_it_ran_as_a_receipt_does(svc, tables):
    from fabric_desktop import receipts
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [2, 1]}), "bench.q01"))
    cells = [dict(c) | {"cell_id": f"c{i}"} for i, c in enumerate(CELLS)]  # the app sends the ids it was given
    out = wait(svc, call(svc, "side_by_side_start", **payload(cells))["id"])
    assert call(svc, "side_by_side_list", name="NB")[0]["code_revision"] == receipts.code_revision(cells)["revision"]
    cells[1]["source"] += "\n# changed"  # so a comparison counts only while the notebook is unchanged
    assert out["code_revision"] != receipts.code_revision(cells)["revision"]


def test_side_by_side_reports_a_different_answer(svc, tables):
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [1, 3]}), "bench.q01"))
    out = wait(svc, call(svc, "side_by_side_start", **payload())["id"])
    assert out["verdict"] == {"same": False, "text": "Results differ in 1 of 1 table."}


def test_a_failure_on_this_pc_still_runs_in_fabric(svc, tables):
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [1, 2]}), "bench.q01"))
    cells = CELLS[:2] + [{"type": "code", "source": "raise ValueError('only on my laptop')", "meta": {}}]
    out = wait(svc, call(svc, "side_by_side_start", **payload(cells) | {"confirm_writes": []})["id"])
    assert out["local"]["status"] == "failed" and out["local"]["failed_cell"] == 3
    assert out["fabric"]["status"] == "ok"
    assert "failed on this PC" in out["verdict"]["text"]
    assert out["tables"][0]["result"] == "only_fabric"
    assert out["local"]["message"] == "ValueError: only on my laptop"
    assert out["speed"] == {}  # a run that stopped early isn't faster


def test_both_sides_failing_points_at_this_pcs_error():
    v = sbs.verdict([], "failed", "failed", 4)
    assert v["same"] is False
    assert "this PC (cell 4) and in Fabric" in v["text"] and "Fix this PC's error first" in v["text"]


def test_side_by_side_needs_a_notebook_that_runs_in_both_places(svc, tables):
    svc.fake = FakeFabric(tables, lambda t: None)
    out = handle(svc, json.dumps({"id": 1, "method": "side_by_side_start", "params": payload(
        [{"type": "code", "source": "x = open('C:\\\\data\\\\a.csv')", "meta": {}}])}))
    assert "can't run in Fabric" in out["error"]
    # The Fabric step runs only on Fabric Spark, so the run on this PC can't (fabric_step.py).
    step = CELLS[:2] + [dict(CELLS[2], meta={"fabric_step": True})]
    out = handle(svc, json.dumps({"id": 1, "method": "side_by_side_start", "params": payload(step)}))
    assert "can't run side by side: Cell 3 is marked Run on Fabric" in out["error"] and svc.fake.saved == []
    out = handle(svc, json.dumps({"id": 1, "method": "side_by_side_start", "params": payload() | {"lakehouse_ref": None}}))
    assert "Attach a lakehouse" in out["error"]


def test_side_by_side_runs_only_with_the_tables_it_overwrites_confirmed(svc, tables):
    """Both runs write the real tables, so the person confirms exactly which ones first."""
    svc.fake = FakeFabric(tables, lambda t: None)
    assert call(svc, "side_by_side_writes", cells=CELLS) == {"tables": ["bench.q01"], "unknown": False, "reasons": []}
    for confirm in (None, [], ["bench.other"], ["bench.q01", "bench.q02"]):
        out = handle(svc, json.dumps({"id": 1, "method": "side_by_side_start", "params": payload() | {"confirm_writes": confirm}}))
        assert "Confirm the tables it writes" in out["error"], confirm
    assert "bench.q01" not in tables.versions(), "nothing ran"
    dynamic = CELLS + [{"type": "code", "source": "exec(code)", "meta": {}}]
    assert call(svc, "side_by_side_writes", cells=dynamic)["unknown"] is True
    out = handle(svc, json.dumps({"id": 1, "method": "side_by_side_start", "params": payload(dynamic)}))
    assert "Confirm the tables" in out["error"]


# ---- the TPC-H benchmark notebooks

def test_examples_in_the_repository_are_the_generated_notebooks(tmp_path):
    repo = Path(__file__).resolve().parents[2] / "examples" / "benchmark"
    for path in benchmark.write(tmp_path):
        assert (repo / path.name).read_text(encoding="utf-8") == path.read_text(encoding="utf-8"), \
            "Run: python -m fabric_desktop.benchmark examples/benchmark (from sidecar/, path ../examples/benchmark)"


def test_benchmark_notebooks_are_added_and_kept(svc):
    out = call(svc, "benchmark_notebooks", lakehouse_ref=LAKEHOUSE)
    assert out["added"] == [benchmark.DATA_NOTEBOOK, benchmark.BENCHMARK_NOTEBOOK]
    nb = call(svc, "open_notebook", name=benchmark.BENCHMARK_NOTEBOOK)
    assert nb["lakehouse"] == LAKEHOUSE
    assert nb["check"]["local"]["ok"] and nb["check"]["fabric"]["ok"]
    assert call(svc, "benchmark_notebooks")["added"] == []
    assert len(benchmark.QUERIES) == 22


def _tpch_available() -> bool:
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("INSTALL tpch")
        con.execute("LOAD tpch")
        return True
    except Exception:
        try:
            import duckdb_extension_tpch  # noqa: F401
            return True
        except ImportError:
            return False


@pytest.mark.skipif(not _tpch_available(), reason="DuckDB's tpch extension can't be installed here")
@pytest.mark.parametrize("schemas", [True, False])
def test_benchmark_notebooks_run_end_to_end(svc, tables, schemas):
    """Generate scale factor 0.01, run the 22 queries, and check the answers against DuckDB's own."""
    import duckdb
    svc._schema_cache[LAKEHOUSE["lakehouse_id"]] = schemas
    tname = (lambda s, t: f"{s}.{t}") if schemas else (lambda s, t: f"{s}_{t}")  # noqa: E731
    for name, params in ((benchmark.DATA_NOTEBOOK, "scale_factor = 0.01"), (benchmark.BENCHMARK_NOTEBOOK, None)):
        cells = benchmark.notebooks()[name]
        svc._attach(LAKEHOUSE)
        svc.kernel.restart()
        for c in cells:
            if c["type"] != "code":
                continue
            out = svc.kernel.run(c["source"] + (f"\n{params}" if params and "parameters" in c.get("meta", {}).get("tags", []) else ""))
            assert out["status"] == "ok", out["outputs"]
    assert DeltaTable(str(tables.path(tname("tpch", "lineitem")))).to_pyarrow_table().num_rows > 50_000
    con = duckdb.connect()
    for t in benchmark.TABLES:
        con.register(t, DeltaTable(str(tables.path(tname("tpch", t)))).to_pyarrow_dataset())
    for n in (1, 6, 13, 18):
        expected = sbs.fingerprint(con.execute(benchmark.QUERIES[n]).pl().to_arrow())
        got = tables.fingerprint(tname("bench", f"q{n:02d}"), DeltaTable(str(tables.path(tname("bench", f"q{n:02d}")))).version())
        assert got["rows"] == expected["rows"] and got["checksum"] == expected["checksum"]


def test_benchmark_before_the_data_notebook_says_to_run_it_first(svc, tables):
    svc._schema_cache[LAKEHOUSE["lakehouse_id"]] = True  # Fabric's answer; a failed lookup stops the cell first
    svc._attach(LAKEHOUSE)
    svc.kernel.restart()
    cells = [c for c in benchmark.notebooks()[benchmark.BENCHMARK_NOTEBOOK] if c["type"] == "code"]
    for c in cells[:2]:
        assert svc.kernel.run(c["source"])["status"] == "ok"
    out = svc.kernel.run(cells[2]["source"])
    assert out["status"] == "error"
    assert "Run the TPC-H data notebook" in str(out["outputs"])


def test_side_by_side_runs_its_own_notebooks_kernel_not_the_open_one(svc, tables):
    # A kernel per notebook (as with notebooks in their own process), in the engine to keep it quick.
    svc.sessions.in_process, svc.sessions._make = True, lambda _process: Kernel()
    svc.sessions.active = "Other"
    other = svc.sessions.kernel("Other")
    other.run("y = 1")
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [2, 1]}), "bench.q01"))
    out = wait(svc, call(svc, "side_by_side_start", **payload())["id"])
    assert out["state"] == "done", out["message"]
    assert svc.sessions.kernel("NB").run("print(x)")["outputs"][0]["text"] == "2\n"
    assert other.run("print(y)")["outputs"][0]["text"] == "1\n"  # the open notebook wasn't restarted


def test_both_runs_use_the_parameters_bar_values(svc, tables):
    # Fabric's stand-in writes what Fabric would write with x = 5; this PC must use 5 too to match.
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [5, 1]}), "bench.q01"))
    cells = [dict(c) | {"cell_id": f"c{i}"} for i, c in enumerate(CELLS)]
    cells[1] = cells[1] | {"meta": {"tags": ["parameters"]}}
    call(svc, "save_notebook", name="NB", cells=cells, lakehouse_ref=LAKEHOUSE)
    call(svc, "notebook_inputs_set", name="NB", cells=cells, values={"parameters": {"x": "5"}})
    out = wait(svc, call(svc, "side_by_side_start", **payload(cells))["id"])
    assert out["state"] == "done", out["message"]
    assert svc.fake.execution_data == {"parameters": {"x": {"value": 5, "type": "int"}}}
    assert out["verdict"]["same"] is True
    # The code kept its own value; the bar's was added only to the run.
    assert svc.notebooks.load("NB")["cells"][1]["source"].endswith("x = 2")
    assert "_parameters" not in out and "_suffixes" not in out
    # A value the bar shows in red stops both runs before either starts.
    call(svc, "notebook_inputs_set", name="NB", cells=cells, values={"parameters": {"x": "five"}})
    refused = handle(svc, json.dumps({"id": 1, "method": "side_by_side_start", "params": payload(cells)}))
    assert "Type a whole number" in refused["error"]


def _tpch_available() -> bool:
    import duckdb
    try:
        duckdb.connect().execute("INSTALL tpch; LOAD tpch")
        return True
    except Exception:  # noqa: BLE001  # silent-ok: no extension site here, so the test is skipped
        return False


@pytest.mark.skipif(not _tpch_available(), reason="DuckDB's tpch extension can't be downloaded here")
def test_tpch_data_parts_are_exactly_one_dbgen_and_are_deleted_after(monkeypatch):
    """The TPC-H data notebook generates in parts on several processes: together they must be the
    rows one dbgen call makes, and the generated data must not stay on disk."""
    import duckdb
    monkeypatch.setattr(os, "cpu_count", lambda: 3)
    ns = {"scale_factor": 0.5, "TABLES": benchmark.TABLES, "schema": "tpch"}  # 3 parts
    exec(compile(benchmark._GENERATE, "generate", "exec"), ns)
    one = duckdb.connect()
    one.execute("LOAD tpch")
    one.execute("CALL dbgen(sf=0.5)")
    for table in benchmark.TABLES:
        query = f"SELECT count(*), sum(hash(t)) FROM {table} t"
        assert ns["con"].execute(query).fetchall() == one.execute(query).fetchall(), table
    work = ns["work"]
    ns["clean_up"]()
    assert not os.path.exists(work)


def test_this_pcs_time_splits_into_processing_and_upload_wait():
    from fabric_desktop.side_by_side_service import _split
    cells = [{"wait_seconds": 600, "upload_seconds": 640, "tables": 8, "rows": 86_000_000, "bytes": 3_000_000_000, "skipped": 0},
             {}, {"wait_seconds": 50, "upload_seconds": 60, "tables": 1, "rows": 10, "bytes": 290_000_000, "skipped": 1}]
    split = _split(780.0, cells)
    assert split["processing_seconds"] == 130.0 and split["upload_wait_seconds"] == 650.0
    assert split["upload"] == {"seconds": 700.0, "tables": 9, "rows": 86_000_010, "bytes": 3_290_000_000, "skipped": 1,
                               "mb_per_second": 4.7}
    assert _split(10.0, [])["upload"]["mb_per_second"] is None
    s = sbs.speed(780, 549, 488, 130)
    assert s["processing"].startswith("This PC was 3.8x faster on processing alone")
    assert sbs.speed(780, 549, 488, 780)["processing"] == ""  # no wait, nothing to add
