"""Side by side's "Cores on this PC": the run on this PC held to as many cores as Fabric's session."""

from __future__ import annotations

import os
import subprocess
import sys

import pyarrow as pa
import pytest

from fabric_desktop import cpu_cap
from fabric_desktop import side_by_side as sbs
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

from test_side_by_side import CELLS, FakeFabric, call, payload, svc, tables, wait  # noqa: F401 - fixtures

AFFINITY = sys.platform == "win32" or hasattr(os, "sched_setaffinity")


def code(source):
    return {"type": "code", "source": source, "meta": {}}


# ---- what each choice comes to

def test_fabric_vcores_are_a_python_notebooks_two_or_its_configure_cell():
    assert sbs.fabric_vcores([code("x = 1")], "python") == 2
    assert sbs.fabric_vcores([code('%%configure -f\n{"vCores": 8}'), code("x = 1")], "python") == 8
    assert sbs.fabric_vcores([code("%%configure\nnot json")], "python") == 2
    assert sbs.fabric_vcores([code("x = 1")], "spark") is None  # its pool decides


def test_cores_for_each_choice():
    assert sbs.cores_for("all", 2, 24) is None
    assert sbs.cores_for("match", 2, 24) == 2
    assert sbs.cores_for("match", None, 24) is None  # Spark: every core, and the card says so
    assert sbs.cores_for("match", 32, 24) is None  # Fabric has more than this PC
    assert sbs.cores_for(8, 2, 24) == 8
    assert sbs.cores_for(24, 2, 24) is None
    assert sbs.cores_for(True, 2, 24) is None


def test_cores_line_says_whether_both_sides_had_the_same():
    assert sbs.cores_line({"cores": 24, "cores_used": 2}, 2).startswith("Same cores on both sides: 2")
    assert "compares hardware" in sbs.cores_line({"cores": 24, "cores_used": None}, 2)
    assert sbs.cores_line({"cores": 24}, None) == ""


# ---- the cap itself

def test_env_names_the_cap_for_every_pool_that_reads_a_count():
    assert cpu_cap.env(None) == {}
    assert cpu_cap.env(2) == {"FABRIC_DESKTOP_CORES": "2", "POLARS_MAX_THREADS": "2", "PYTHON_CPU_COUNT": "2"}


@pytest.mark.skipif(not AFFINITY, reason="no process affinity on this OS")
def test_a_capped_process_and_the_processes_it_starts_get_only_those_cores():
    script = ("import os, subprocess, sys\n"
              "from fabric_desktop import cpu_cap\n"
              "print(cpu_cap.from_env(), cpu_cap.allowed(), os.cpu_count())\n"
              "print(subprocess.run([sys.executable, '-c', 'from fabric_desktop import cpu_cap; print(cpu_cap.allowed())'],\n"
              "                     capture_output=True, text=True).stdout.strip())\n")
    env = dict(os.environ, **cpu_cap.env(1), PYTHONPATH=os.pathsep.join(sys.path))
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["1", "1", "1", "1"]


@pytest.mark.skipif(not AFFINITY, reason="no process affinity on this OS")
def test_a_process_kernel_with_cores_runs_its_cells_on_them():
    k = ProcessKernel()
    k.cores = 1
    try:
        res = k.run("import os, duckdb, polars\nfrom fabric_desktop import cpu_cap\n"
                    "threads = lambda con: con.execute(\"select current_setting('threads')\").fetchall()[0][0]\n"
                    "print(cpu_cap.allowed(), os.cpu_count(), polars.thread_pool_size(),\n"
                    "      threads(duckdb.connect()), threads(duckdb.connect(':memory:', config={'threads': 3})),\n"
                    "      duckdb.sql(\"select current_setting('threads')\").fetchall()[0][0])")
        assert res["status"] == "ok", res
        # DuckDB too, with a connection's own number kept.
        assert res["outputs"][0]["text"].split() == ["1", "1", "1", "1", "3", "1"]
    finally:
        k.close()


def test_no_cap_asked_changes_nothing(monkeypatch):
    monkeypatch.delenv(cpu_cap.ENV, raising=False)
    before = os.cpu_count
    assert cpu_cap.from_env() is None
    assert os.cpu_count is before


# ---- the choice, kept with the notebook

def test_the_choice_is_kept_with_the_notebook(svc, monkeypatch):
    monkeypatch.setattr(sbs, "DEFAULT_CORES", "match")
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    svc.notebooks.save("NB", CELLS, None)
    first = call(svc, "side_by_side_cores", name="NB", cells=CELLS)
    assert first == {"choice": "match", "pc_cores": 24, "engine": "python", "fabric_vcores": 2, "cores": 2}
    assert call(svc, "side_by_side_cores_set", name="NB", cells=CELLS, choice=6)["cores"] == 6
    assert call(svc, "side_by_side_cores", name="NB", cells=CELLS)["choice"] == 6
    assert call(svc, "side_by_side_cores_set", name="NB", cells=CELLS, choice="all")["cores"] == 24
    for bad in (0, 25, "half", True):
        with pytest.raises(ValueError):
            svc.side_by_side_cores_set("NB", CELLS, bad)


# ---- a run held to fewer cores

class CappedKernel(Kernel):
    """Stands in for the run's own process kernel, in the engine so the fakes reach it."""
    made: list[CappedKernel] = []

    def __init__(self):
        super().__init__()
        self.cores = None
        self.closed = False
        CappedKernel.made.append(self)

    def close(self):
        self.closed = True


def test_a_matched_run_uses_its_own_kernel_and_says_so(svc, tables, monkeypatch):
    monkeypatch.setattr(sbs, "DEFAULT_CORES", "match")
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    CappedKernel.made = []
    monkeypatch.setattr(svc, "_new_kernel", lambda process=None: CappedKernel())
    svc.sessions.in_process, svc.sessions._make = True, lambda _process: Kernel()
    svc.sessions.kernel("NB").run("y = 7")
    svc.fake = FakeFabric(tables, lambda t: t.write(pa.table({"id": [2, 1]}), "bench.q01"))
    out = wait(svc, call(svc, "side_by_side_start", **payload())["id"])
    assert out["state"] == "done", out["message"]
    [kernel] = CappedKernel.made
    assert kernel.cores == 2 and kernel.closed
    assert out["machine"]["cores_used"] == 2 and out["fabric"]["vcores"] == 2
    assert out["speed"]["cores"].startswith("Same cores on both sides")
    # The notebook's own session kept its variables: the run didn't restart it.
    assert svc.sessions.kernel("NB").run("print(y)")["outputs"][0]["text"] == "7\n"


def test_cancel_ends_a_capped_runs_own_process(svc):
    class Proc:
        stopped = interrupted = False

        def stop(self):
            self.stopped = True

        def interrupt(self):
            self.interrupted = True

    proc = Proc()
    svc._sbs_state()["r1"] = {"id": "r1", "name": "NB", "state": "local", "_cancel": False, "_kernel": proc}
    assert svc.side_by_side_cancel("r1") == {"cancelled": True}
    assert proc.stopped and not proc.interrupted
