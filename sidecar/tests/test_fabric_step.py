"""The Fabric step: a notebook's last cell, run on Fabric Spark for V-Order (fabric_step.py)."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import fabric_step, maintenance
from fabric_desktop.notebook import from_ipynb, to_ipynb
from fabric_desktop.pipeline_notebooks import code_cells
from fabric_desktop.server import handle
from fabric_desktop.service import Service

WS = "11111111-2222-3333-4444-555555555555"
LH = "66666666-7777-8888-9999-000000000000"
LAKEHOUSE = {"workspace_id": WS, "workspace_name": "Sales Dev", "lakehouse_id": LH, "name": "LH_Gold"}
STEP = {"fabric_step": True}


def cells(*sources, step=True):
    out = [{"type": "code", "source": s, "meta": {}} for s in sources]
    if step:
        out[-1]["meta"] = dict(STEP)
    return out


# ---- the mark


def test_the_mark_round_trips_through_ipynb_and_keeps_other_fabric_desktop_keys():
    c = cells("print(1)", 'spark.sql("OPTIMIZE gold.t VORDER")')
    c[-1]["meta"]["chart"] = {"kind": "bar"}
    nb = to_ipynb(c, None)
    assert nb["cells"][1]["metadata"]["fabric_desktop"] == {"chart": {"kind": "bar"}, "run_on": "fabric"}
    back = from_ipynb(nb)["cells"]
    assert back[1]["meta"]["fabric_step"] is True and not back[0].get("meta", {}).get("fabric_step")


def test_only_the_last_code_cell_can_carry_the_mark():
    assert fabric_step.step_index(cells("a = 1", "b = 2")) == 1
    assert fabric_step.step_index(cells("a = 1", step=False)) is None
    trailing = cells("a = 1", "b = 2") + [{"type": "markdown", "source": "notes"}, {"type": "code", "source": "  "}]
    assert fabric_step.step_index(trailing) == 1  # markdown and empty cells below don't count
    below = cells("a = 1", "b = 2") + [{"type": "code", "source": "c = 3", "meta": {}}]
    with pytest.raises(ValueError, match="Only the last code cell can run on Fabric, and 1 code cell is below"):
        fabric_step.step_index(below)
    two = cells("a = 1", "b = 2")
    two[0]["meta"] = dict(STEP)
    with pytest.raises(ValueError, match="Only one cell can run on Fabric; 2 are marked"):
        fabric_step.step_index(two)
    frozen = cells("a = 1", "b = 2")
    frozen[-1]["meta"]["frozen"] = True
    with pytest.raises(ValueError, match="frozen"):
        fabric_step.step_index(frozen)


def test_local_runs_leave_the_step_out():
    c = cells("a = 1", "b = 2")
    assert [x["source"] for x in code_cells(c)] == ["a = 1"]


# ---- the V-Order cell


def test_vorder_source_quotes_names_and_rewrite_all_puts_every_file_in_scope():
    src = fabric_step.vorder_source(["gold.fact_sales", "dim`x"])
    assert 'spark.sql("OPTIMIZE `gold`.`fact_sales` VORDER")' in src
    assert 'spark.sql("OPTIMIZE `dim``x` VORDER")' in src
    assert "minFileSize" not in src
    assert f'"{fabric_step.REWRITE_ALL_MIN_FILE_SIZE}"' in fabric_step.vorder_source(["t"], rewrite_all=True)
    # What the V-Order check later reads: the same tables, comments not mistaken for names.
    assert fabric_step.optimized_tables(fabric_step.vorder_source(["gold.fact_sales", "dim"], rewrite_all=True)) == [
        "gold.fact_sales", "dim"]
    with pytest.raises(ValueError):
        fabric_step.vorder_source([])
    with pytest.raises(ValueError):
        fabric_step.table_name("a.b.c")


def test_optimized_tables_reads_spark_sql_and_three_part_names():
    src = 'spark.sql("OPTIMIZE LH_Gold.gold.orders VORDER")\nspark.sql("optimize `gold`.`dim c` zorder by (id) vorder")'
    assert fabric_step.optimized_tables(src) == ["gold.orders", "gold.dim c"]


def test_candidates_are_the_tables_the_other_cells_name():
    c = cells('lakehouse.write_table(df, "gold.fact_sales")', 'x = spark.read.table("customers")',
              'spark.sql("OPTIMIZE gold.never VORDER")')
    tables = ["gold.fact_sales", "gold.customers", "gold.never", "gold.other"]
    assert fabric_step.vorder_candidates(c, tables) == ["gold.fact_sales", "gold.customers"]


# ---- the companion Spark notebook


def test_step_notebook_is_one_spark_cell_with_vorder_on_and_saves_its_output():
    nb = fabric_step.step_ipynb("Sales/Gold load", cells('spark.sql("OPTIMIZE gold.t VORDER")')[0], LAKEHOUSE, "fds-1")
    assert len(nb["cells"]) == 1 and nb["metadata"]["kernelspec"]["name"] == "python3"  # Spark, not a Python notebook
    assert nb["metadata"]["dependencies"]["lakehouse"]["default_lakehouse"] == LH
    body = "".join(nb["cells"][0]["source"])
    assert 'spark.conf.set("spark.sql.parquet.vorder.default", "true")' in body
    assert f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LH}/Files/_fabric_desktop/results/fds-1.json" in body
    compile(body, "step", "exec")  # the wrapper itself is valid Python


def test_step_runs_the_cell_and_records_print_and_errors(monkeypatch):
    put = {}

    class Fs:
        @staticmethod
        def put(path, content, overwrite):
            put[path] = json.loads(content)

    class Conf:
        @staticmethod
        def set(k, v):
            put["conf"] = (k, v)

    class Spark:
        conf = Conf()

    def run(source):
        nb = fabric_step.step_ipynb("NB", {"type": "code", "source": source}, LAKEHOUSE, "r1")
        g = {"spark": Spark(), "notebookutils": type("N", (), {"fs": Fs})}
        exec(compile("".join(nb["cells"][0]["source"]), "step", "exec"), g)
        return next(v for k, v in put.items() if k.endswith("r1.json"))

    assert run("print('done')") == {"request_id": "r1", "stdout": "done\n", "error": None}
    assert put["conf"] == ("spark.sql.parquet.vorder.default", "true")
    with pytest.raises(RuntimeError, match="ZeroDivisionError"):
        run("print('before')\n1/0")
    saved = next(v for k, v in put.items() if k.endswith("r1.json"))
    assert saved["stdout"] == "before\n" and "ZeroDivisionError" in saved["error"]


def test_sql_cells_become_spark_sql_and_other_magics_are_refused():
    nb = fabric_step.step_ipynb("NB", {"type": "code", "source": "%%sql\nOPTIMIZE gold.t VORDER"}, LAKEHOUSE, "r")
    assert 'OPTIMIZE gold.t VORDER' in "".join(nb["cells"][0]["source"])
    with pytest.raises(ValueError, match="Magics other than %%sql"):
        fabric_step.step_ipynb("NB", {"type": "code", "source": "%pip install x"}, LAKEHOUSE, "r")


# ---- the RPCs, called the way the app calls them


class FakeFabric:
    def __init__(self):
        self.saved, self.jobs, self.status = [], [], "InProgress"

    def save_notebook(self, ws, name, ipynb):
        self.saved.append((ws, name, json.loads(ipynb)))
        return {"id": "step-item", "displayName": name}

    def list_notebooks(self, ws):
        return []

    def run_item_job(self, ws, item, job):
        self.jobs.append((ws, item, job))
        return "job-1"

    def job_instance(self, ws, item, job):
        return {"status": self.status}

    def get_lakehouse(self, ws, lh):
        return {"properties": {"defaultSchema": "dbo"}}


class StepService(Service):
    fake = None

    @property
    def fabric(self):
        return self.fake

    @property
    def fabric_jobs(self):
        return self.fake

    def _quiet_fabric(self):
        return self.fake


@pytest.fixture
def svc(tmp_path):
    s = StepService(root=tmp_path)
    s.fake = FakeFabric()
    return s


def call(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def test_check_says_where_it_runs_and_why_not(svc):
    c = cells('lakehouse.write_table(df, "gold.t")', 'spark.sql("OPTIMIZE gold.t VORDER")')
    out = call(svc, "notebook_fabric_step_check", name="Sales/Gold load", cells=c, lakehouse_ref=LAKEHOUSE)
    assert out["ok"] and out["item_name"] == "Gold load (Fabric step)" and out["workspace_id"] == WS
    assert out["tables"] == ["gold.t"]
    local = dict(LAKEHOUSE, workspace_id="local", lakehouse_id="C:/data/lh")
    out = call(svc, "notebook_fabric_step_check", name="NB", cells=c, lakehouse_ref=local)
    assert not out["ok"] and "Attach a OneLake lakehouse" in out["problem"]
    assert "No cell" in call(svc, "notebook_fabric_step_check", name="NB", cells=cells("a=1", step=False),
                             lakehouse_ref=LAKEHOUSE)["problem"]
    assert svc.fake.saved == [] and svc.fake.jobs == []  # a check writes nothing


def test_run_publishes_the_step_starts_it_and_reads_back_output_and_vorder(svc, monkeypatch):
    c = cells('lakehouse.write_table(df, "gold.t")', 'spark.sql("OPTIMIZE gold.t VORDER")')
    run = call(svc, "run_notebook_fabric_step", name="NB", cells=c, lakehouse_ref=LAKEHOUSE)
    assert svc.fake.saved[0][:2] == (WS, "NB (Fabric step)") and svc.fake.jobs == [(WS, "step-item", "RunNotebook")]
    assert run["kind"] == "Fabric step" and run["tables"] == ["gold.t"] and run["request_id"].startswith("fds-")
    # Only the marked cell went to Fabric.
    body = "".join(svc.fake.saved[0][2]["cells"][0]["source"])
    assert "OPTIMIZE gold.t VORDER" in body and "write_table" not in body

    assert call(svc, "notebook_fabric_step_result", request_id=run["request_id"])["output"] is None  # still running
    svc.fake.status = "Completed"
    monkeypatch.setattr(type(svc.auth), "storage_token", lambda self: "tok")
    read = {}

    class Files:
        def __init__(self, token):
            pass

        def read_bytes(self, ws, lh, path):
            read["path"] = (ws, lh, path)
            return json.dumps({"stdout": "ok\n", "error": None}).encode()

    import fabric_desktop.cloud_service as cs
    monkeypatch.setattr(cs, "FileStore", Files)
    monkeypatch.setattr(maintenance, "health", lambda uri, options=None, local=False: {
        "exists": True, "v_ordered": uri.endswith("/Tables/gold/t"), "files": 3, "last_optimize": "now"})
    out = call(svc, "notebook_fabric_step_result", request_id=run["request_id"])
    assert out["status"] == "Completed" and out["output"]["stdout"] == "ok\n"
    assert read["path"] == (WS, LH, f"Files/_fabric_desktop/results/{run['request_id']}.json")
    assert out["tables"] == [{"table": "gold.t", "v_ordered": True, "files": 3, "last_optimize": "now"}]


def test_run_refuses_a_mark_that_is_not_last(svc):
    c = cells("a = 1", "b = 2") + [{"type": "code", "source": "c = 3", "meta": {}}]
    out = handle(svc, json.dumps({"id": 1, "method": "run_notebook_fabric_step",
                                  "params": {"name": "NB", "cells": c, "lakehouse_ref": LAKEHOUSE}}))
    assert "Only the last code cell" in out["error"] and svc.fake.jobs == []


def test_vorder_cell_rpc_ticks_named_tables_then_writes_the_code(svc):
    c = cells('lakehouse.write_table(df, "gold.t")', "# V-Order")
    out = call(svc, "notebook_vorder_cell", cells=c, tables=["gold.t", "gold.u"])
    assert out == {"candidates": ["gold.t"], "source": None}
    out = call(svc, "notebook_vorder_cell", cells=[], tables=[], picked=["gold.u"], rewrite_all=True)
    assert "OPTIMIZE `gold`.`u` VORDER" in out["source"] and "minFileSize" in out["source"]


def test_a_scheduled_run_skips_the_step_and_says_so(svc):
    ran = []
    svc.notebooks.save("NB", cells("a = 1", 'spark.sql("OPTIMIZE t VORDER")'), None)
    svc._attach = lambda ref: None
    svc._resources_dir = lambda name: None

    class Kernel:
        resources_dir = None

        def run(self, source):
            ran.append(source)
            return {"status": "ok", "outputs": []}

    type(svc).kernel = property(lambda self: Kernel._one)
    Kernel._one = Kernel()
    try:
        out = svc._run_notebook_here("NB")
    finally:
        del type(svc).kernel
    assert ran == ["a = 1"] and out == {"status": "ok", "message": "1 cell ran, the Fabric step skipped", "cells": 1}


def test_prove_refuses_a_notebook_with_the_step_and_says_how_to_clear_it():
    from fabric_desktop import proof
    c = cells("from fabric_desktop import lakehouse\nx = 1", "y = 2")
    out = proof.check(c, LAKEHOUSE)
    assert not out["ok"]
    assert out["blockers"][0]["cell"] == 2 and out["blockers"][0]["text"] == fabric_step.STEP_REFUSED
    assert proof.check(cells("from fabric_desktop import lakehouse\nx = 1", step=False), LAKEHOUSE)["ok"]
