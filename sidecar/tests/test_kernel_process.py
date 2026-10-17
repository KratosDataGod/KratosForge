"""The notebook kernel in a process of its own (Prove stage 4): the same cells, but Stop and
crashes end only that process, and the engine keeps answering."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from test_prove import LAKEHOUSE, CELLS, FakeFabric, call, payload, shared_version, svc, wait  # noqa: F401

from fabric_desktop import app_settings, lakehouse
from fabric_desktop.kernel_proc import STOPPED_TEXT, ProcessKernel
from fabric_desktop.notebook import NotebookStore
from fabric_desktop.server import handle
from fabric_desktop.service import Service

BOOTSTRAP = str(Path(__file__).with_name("kernel_bootstrap.py"))


@pytest.fixture
def kernel():
    k = ProcessKernel(token=lambda audience: f"token-for-{audience}")
    yield k
    k.close()


def until_done(k, timeout=30):
    deadline = time.monotonic() + timeout
    snap = k.poll(-1, 0.5)
    while snap["status"] == "running" and time.monotonic() < deadline:
        snap = k.poll(-1, 0.5)
    return snap


def text(snap):
    return "".join(o.get("text", "") + o.get("evalue", "") for o in snap.get("outputs") or [])


def test_cells_run_in_another_process_with_the_same_kernel(kernel):
    out = kernel.run("import os, polars as pl\nx = 41\nprint(os.getpid())\npl.DataFrame({'a': [1, 2]})")
    assert out["status"] == "ok", out
    assert int(out["outputs"][0]["text"]) == kernel.pid != os.getpid()
    assert out["outputs"][1]["type"] == "table"
    assert kernel.run("x + 1")["outputs"] == [{"type": "text", "text": "42"}]
    assert any(v["name"] == "x" for v in kernel.variables())
    kernel.run("xylophone = 1")
    assert {"label": "xylophone", "type": "variable"} in kernel.complete("xylo", 4)["items"]
    # notebookutils asks the engine for tokens
    out = kernel.run("import notebookutils\nnotebookutils.credentials.getToken('storage')")
    assert out["outputs"] == [{"type": "text", "text": "'token-for-storage'"}]


def test_cancel_keeps_variables_and_stop_ends_only_the_process(kernel):
    kernel.run("kept = 1")
    kernel.start("import time\nwhile True:\n    time.sleep(0.05)")
    time.sleep(0.5)
    assert kernel.interrupt()["interrupted"] is True
    assert until_done(kernel)["status"] == "cancelled"
    assert kernel.run("kept")["outputs"] == [{"type": "text", "text": "1"}]
    # A cell stuck in one long call can't be interrupted; Stop ends its process, not the engine.
    pid = kernel.pid
    kernel.start("import time\ntime.sleep(600)")
    time.sleep(0.3)
    started = time.monotonic()
    assert kernel.stop() == {"stopped": True}
    assert time.monotonic() - started < 10
    snap = kernel.poll(-1, 0)
    assert snap["status"] == "cancelled" and STOPPED_TEXT in text(snap)
    out = kernel.run("'kept' in dir()")
    assert out["outputs"] == [{"type": "text", "text": "False"}] and kernel.pid != pid


def test_a_crash_ends_the_cell_with_a_reason_and_the_next_cell_gets_a_fresh_kernel(kernel):
    kernel.start("import os\nos._exit(3)")
    snap = until_done(kernel)
    assert snap["status"] == "error" and "exit code 3" in text(snap)
    assert kernel.run("1 + 1")["outputs"] == [{"type": "text", "text": "2"}]


def test_other_notebooks_and_the_managed_table_check_come_from_the_engine(tmp_path, monkeypatch):
    store = NotebookStore(tmp_path / "nb")
    store.save("helpers", [{"type": "code", "source": "def double(x):\n    return 2 * x"}])
    k = ProcessKernel(load_notebook=lambda name: store.load(name)["cells"])
    try:
        assert k.run("%run helpers\ndouble(21)")["outputs"][-1] == {"type": "text", "text": "42"}
        lakehouse.attach(lakehouse.Attached(workspace_id="ws", lakehouse_id="lh", name="LH", storage_token=lambda: "t",
                                            work_dir=tmp_path))
        monkeypatch.setattr(lakehouse, "write_guard", lambda name, schema, table: f"{name}.{schema}.{table} is managed"
                            if table == "managed" else None)
        out = k.run("import polars as pl\nfrom fabric_desktop import lakehouse\n"
                    "lakehouse.write_table(pl.DataFrame({'a': [1]}), 'dbo.managed')")
        assert out["status"] == "error" and "LH.dbo.managed is managed" in json.dumps(out["outputs"])
    finally:
        lakehouse.attach(None)
        k.close()


def test_the_setting_switches_the_kernel_and_stop_uses_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    assert call(s, "kernel_info") == {"process": False, "pid": os.getpid()}
    assert call(s, "stop_kernel_process") == {"stopped": False, "in_engine": True}
    call(s, "save_app_settings", changes={"kernel_process": True})
    assert app_settings.value("kernel_process") is True
    try:
        call(s, "start_cell", code="import time\ntime.sleep(600)")
        info = call(s, "kernel_info")
        assert info["process"] is True and info["pid"] != os.getpid()
        out = handle(s, json.dumps({"id": 1, "method": "save_app_settings", "params": {"changes": {"kernel_process": False}}}))
        assert "running" in out["error"]["message"] if isinstance(out.get("error"), dict) else "running" in str(out.get("error"))
        assert call(s, "stop_kernel_process") == {"stopped": True}
        assert call(s, "poll_cell", version=-1)["status"] == "cancelled"
        assert call(s, "run_cell", code="2 * 21")["outputs"] == [{"type": "text", "text": "42"}]
    finally:
        s.kernel.close()


def test_a_proof_runs_the_pc_side_in_the_kernel_process(svc):  # noqa: F811
    svc.fake = FakeFabric(svc)
    tz_before = os.environ.get("TZ")
    svc.kernel = ProcessKernel(token=svc._token_for, command=[sys.executable, BOOTSTRAP, str(svc.lh_root)])
    try:
        before = shared_version(svc)
        out = wait(svc, call(svc, "prove_start", **payload())["id"])
        assert out["state"] == "done", out["message"]
        assert out["verdict"]["verdict"] == "same", out["verdict"]
        assert shared_version(svc) == before
        # The PC side ran in the kernel's process, with the proof bound there.
        assert out["inputs"]["files"][0]["path"] == "Files/raw/rates.csv"
        assert out["pc"]["cells"]["4"]["outputs"][0]["text"].strip() == "5"
        # The time zone was fixed in the kernel's process only; the engine's is as it was.
        assert os.environ.get("TZ") == tz_before
    finally:
        svc.kernel.close()


@pytest.fixture
def quick_limits(monkeypatch):
    from fabric_desktop import kernel_proc
    monkeypatch.setattr(kernel_proc, "WATCH_SECONDS", 0.1)
    monkeypatch.setattr(kernel_proc, "TIME_GRACE_SECONDS", 1.0)


def test_a_cell_past_the_time_limit_is_interrupted_and_keeps_variables(kernel, quick_limits):
    kernel.run("kept = 1")
    kernel.time_limit = 1.0
    kernel.start("import time\nwhile True:\n    time.sleep(0.05)")
    snap = until_done(kernel)
    assert snap["status"] == "error" and "ran longer than the 1 second limit" in text(snap)
    assert "ran longer" in text(kernel.poll(-1, 0)), "every later look at the cell still says why it stopped"
    assert kernel.limit_hit == "time"
    assert kernel.run("kept")["outputs"] == [{"type": "text", "text": "1"}]
    # Run again without the limit: this one cell isn't stopped.
    kernel.start("import time\ntime.sleep(1.6)\n'done'", time_limit=False)
    snap = until_done(kernel)
    assert snap["status"] == "ok" and kernel.limit_hit == ""


def test_a_cell_that_ignores_the_interrupt_has_its_process_ended(kernel, quick_limits):
    kernel.run("kept = 1")
    kernel.time_limit = 0.5
    pid = kernel.pid
    kernel.start("import time\ntime.sleep(600)")  # one long call: the interrupt waits for it
    snap = until_done(kernel)
    assert snap["status"] == "error" and "didn't stop when interrupted" in text(snap)
    out = kernel.run("'kept' in dir()")
    assert out["outputs"] == [{"type": "text", "text": "False"}] and kernel.pid != pid


@pytest.mark.skipif(not sys.platform.startswith(("linux", "win")), reason="memory is read on Windows and Linux")
def test_a_notebook_over_the_memory_limit_has_its_process_ended(kernel, quick_limits):
    from fabric_desktop.kernel_proc import memory_used
    assert memory_used(os.getpid()) > 10 * 1024 ** 2
    kernel.run("kept = 1")
    kernel.memory_limit = (memory_used(kernel.pid) or 0) + 200 * 1024 ** 2
    kernel.start("import time\nblock = bytearray(400 * 1024 ** 2)\ntime.sleep(30)")
    snap = until_done(kernel)
    assert snap["status"] == "error" and "the memory limit" in text(snap) and kernel.limit_hit == "memory"
    assert kernel.run("'kept' in dir()")["outputs"] == [{"type": "text", "text": "False"}]


def test_the_limits_come_from_settings_or_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    call(s, "save_app_settings", changes={"kernel_process": True, "cell_time_limit_minutes": 60, "kernel_memory_limit_gb": 8})
    try:
        assert (s.kernel.time_limit, s.kernel.memory_limit) == (3600, 8 * 1024 ** 3)
        call(s, "save_app_settings", changes={"cell_time_limit_minutes": 0})
        assert s.kernel.time_limit is None and app_settings.load()["cell_time_limit_minutes"]["source"] == "default"
        for bad in (-1, "lots", 10 ** 9):
            with pytest.raises(ValueError):
                app_settings.save({"kernel_memory_limit_gb": bad})
        policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(json.dumps({"kernel_memory_limit_gb": 4}))
        with pytest.raises(ValueError, match="Your organization sets"):
            app_settings.save({"kernel_memory_limit_gb": 16})
        s.kernel.close()
        s.kernel = s._new_kernel()
        assert s.kernel.memory_limit == 4 * 1024 ** 3
        # A policy value out of range or of the wrong kind is ignored, and Help and about says why.
        for bad in (10 ** 9, True):
            policy.write_text(json.dumps({"kernel_memory_limit_gb": bad}))
            assert app_settings.load()["kernel_memory_limit_gb"]["source"] != "policy"
            assert any("kernel_memory_limit_gb" in p for p in app_settings.policy_problems())
    finally:
        s.kernel.close()


def test_a_proof_stopped_by_a_limit_says_so():
    from fabric_desktop.prove_service import _verdict
    record = {"pc": {"status": "failed", "stopped_by": "the time limit"}, "fabric": {"status": "ok"}}
    assert _verdict(record, [], [])["text"] == ("The run failed on this PC (stopped by the time limit), "
                                               "so the results can't be compared.")
