"""Executions a connected tool starts: the edges the main tests don't reach. Outputs of a kind no
one planned for, an interrupt on a closed connection, records only their own tool sees, records
that can't be read, old records pruned, a stop that fails, cancelling what already finished, and a
cancel that lands before the work starts. The work is a stand-in; no kernel or query runs."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import executions as ex
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError


@pytest.fixture
def mgr(tmp_path):
    return ex.ExecutionManager(SimpleNamespace(root=tmp_path, _attach=lambda ref: None), folder=tmp_path / "ex")


def run(client="cl_1", timeout=30.0):
    return ex.Execution("python", client, "a run", {"workspace_id": "ws"}, timeout)


def ctx(client="cl_1"):
    return CallContext(Caller(client, "test"), timeout=60)


def test_an_output_of_a_kind_no_one_planned_for_is_named_not_sent():
    out = ex.shape_outputs([{"type": "widget", "data": "x"}], [ex.MAX_OUTPUT_CHARS])
    assert out == [{"type": "omitted", "text": "[widget output not sent to external tools]"}]


def test_an_interrupt_on_a_connection_that_closed_stops_trying():
    calls = []

    class Closed:
        def interrupt(self):
            calls.append(1)
            raise RuntimeError("connection closed")

    ex.interrupt_until_stopped(Closed(), threading.Event(), seconds=5)
    assert calls == [1]


def test_a_running_execution_is_only_its_own_tool_s(mgr):
    r = run()
    with mgr._lock:
        mgr._runs[r.id] = r
    assert mgr.get(r.id, "cl_1") is r
    with pytest.raises(LookupError):
        mgr.get(r.id, "cl_2")
    rows = mgr.list("cl_1")
    assert [row["id"] for row in rows] == [r.id] and "result" not in rows[0]


@pytest.mark.parametrize("bad", ["../secrets", "ex_nothing_here"])
def test_an_id_that_isnt_one_or_isnt_on_disk_is_not_found(mgr, bad):
    with pytest.raises(LookupError, match="No execution"):
        mgr.get(bad, "cl_1")


def test_a_record_that_cant_be_read_is_left_out_of_the_list(mgr):
    mgr.folder.mkdir(parents=True)
    (mgr.folder / "ex_20260101000000aaaaaa.json").write_text("{torn", encoding="utf-8")
    good = {"id": "ex_20260101000001bbbbbb", "client_id": "cl_1", "created_at": 1, "result": "big", "logs": ["x"]}
    (mgr.folder / f"{good['id']}.json").write_text(json.dumps(good), encoding="utf-8")
    assert mgr.list() == [{"id": good["id"], "client_id": "cl_1", "created_at": 1}]


def test_only_the_newest_records_are_kept(mgr, monkeypatch):
    monkeypatch.setattr(ex, "KEEP_RECORDS", 2)
    mgr.folder.mkdir(parents=True)
    for name in ("ex_1", "ex_2", "ex_3"):
        (mgr.folder / f"{name}.json").write_text("{}", encoding="utf-8")
    r = run()
    r.id = "ex_9"
    mgr._save(r)
    assert sorted(p.name for p in mgr.folder.glob("ex_*.json")) == ["ex_3.json", "ex_9.json"]


def test_a_stop_that_fails_is_logged_and_the_execution_still_cancelled(mgr):
    r = run()

    def broken():
        raise RuntimeError("kernel already gone")

    r.on_cancel = broken
    mgr._stop(r)
    assert r.cancel_event.is_set() and r.logs[-1].endswith("stopping: kernel already gone")


def test_cancelling_a_finished_execution_says_it_wasn_t(mgr):
    def work(run_):
        return {"ok": True}

    view = mgr.start(run(), work, ctx(), wait=5)
    assert view["state"] == "succeeded"
    out = mgr.cancel(view["id"], "cl_1")
    assert out["cancelled"] is False and out["result"] == {"ok": True}


def test_a_cancel_before_the_query_starts_runs_nothing(mgr):
    r = run()
    r.cancel_event.set()
    attached = []
    mgr.service._attach = attached.append
    with pytest.raises(IntegrationError) as raised:
        mgr.run_sql(r, "SELECT 1", {"lakehouse_id": "lh"}, 10)
    assert raised.value.code == "cancelled" and attached == []
    r.state = "timed_out"
    assert mgr._stopped(r).code == "timeout"


class Kernel:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_cells_cancelled_before_they_start_run_none_and_end_the_kernel(mgr):
    kernel = Kernel()
    mgr._kernel_factory = lambda: kernel
    r = run()
    r.cancel_event.set()
    r.state = "cancelled"
    with pytest.raises(IntegrationError) as raised:
        mgr.run_cells(r, ["print(1)", "print(2)"], None, skipped="Run on Fabric cell left out")
    assert raised.value.code == "cancelled" and raised.value.details == {"cells": []}
    assert kernel.closed and r.on_cancel is None and r.logs[0].endswith("Run on Fabric cell left out")
