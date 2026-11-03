"""PR-OPT-006: results wait on this PC when the central lakehouse can't be reached, and go later."""

from __future__ import annotations

import pytest

from fabric_desktop.optimize import outbox as ob
from fabric_desktop.optimize.shared import Shared
from test_optimize_service import svc, xray_result  # noqa: F401 - the fixture is used by name


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def payload(n=1):
    return {"run": {"run_id": f"r{n}"}, "artifacts": [], "findings": [{}] * n, "recommendations": []}


def test_items_wait_with_backoff_and_go_once_sent(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock)
    item = box.add("lh1", ob.ANALYSIS, payload(2), "ConnectionError: network down")
    assert box.status("lh1") | {"oldest": None} == {
        "pending": 1, "pending_rows": 3, "oldest": None, "last_sync": None,
        "last_error": "ConnectionError: network down", "last_error_at": box.status("lh1")["last_error_at"],
        "dropped": 0}
    assert box.due("lh1") == []  # just failed: it waits before the next try
    assert [i["id"] for i in box.due("lh1", include_waiting=True)] == [item]
    clock.now += ob.BACKOFF_SECONDS
    [due] = box.due("lh1")
    assert due["payload"] == payload(2) and due["attempts"] == 1
    box.failed(item, "still down")
    assert box.due("lh1") == []  # waits longer the second time
    clock.now += ob.BACKOFF_SECONDS * 2
    assert len(box.due("lh1")) == 1
    box.sent(item)
    status = box.status("lh1")
    assert status["pending"] == 0 and status["last_sync"] and status["last_error"] is None
    assert box.status("other")["pending"] == 0  # kept per lakehouse


def test_errors_are_stored_without_secrets(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    box.add("lh1", ob.ANALYSIS, payload(), "failed with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc")
    assert "eyJhbGci" not in box.status("lh1")["last_error"]


def test_the_outbox_is_bounded_and_says_what_it_dropped(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock, max_items=3, max_age_days=1)
    ids = []
    for n in range(5):
        clock.now += 1
        ids.append(box.add("lh1", ob.ANALYSIS, payload(n), "down"))
    left = [i["id"] for i in box.due("lh1", include_waiting=True)]
    assert left == ids[2:]  # the oldest went first
    assert box.status("lh1")["dropped"] == 2
    clock.now += 86400 + 10
    assert box.due("lh1", include_waiting=True) == []  # too old to send
    assert box.status("lh1")["dropped"] == 5


def test_discard_and_drop_count_too(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    a = box.add("lh1", ob.ANALYSIS, payload(), "down")
    box.add("lh1", ob.LOCAL_EXECUTIONS, {"rows": [{}, {}]}, "down")
    box.dropped(a, "refused")
    assert box.status("lh1")["dropped"] == 1 and box.status("lh1")["pending_rows"] == 2
    assert box.discard("lh1") == 1
    assert box.status("lh1") | {"last_error_at": None} == {
        "pending": 0, "pending_rows": 0, "oldest": None, "last_sync": None, "last_error": "refused",
        "last_error_at": None, "dropped": 2}


def test_the_same_thing_queued_again_waits_once(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite", max_items=2)
    box.add("lh1", ob.ANALYSIS, payload(1), "down")
    box.add("lh1", ob.ANALYSIS, payload(1), "down")  # the same scan shared again: replaces, not added
    runs = {"rows": [{"execution_id": "a"}, {"execution_id": "b"}]}
    box.add("lh1", ob.LOCAL_EXECUTIONS, runs, "down")
    assert box.add("lh1", ob.LOCAL_EXECUTIONS, runs, "down") == ""  # every run already waits
    box.add("lh1", ob.LOCAL_EXECUTIONS, {"rows": [{"execution_id": "b"}, {"execution_id": "c"}]}, "down")
    items = box.due("lh1", include_waiting=True)
    assert [i["kind"] for i in items] == [ob.LOCAL_EXECUTIONS, ob.LOCAL_EXECUTIONS]  # cap 2: the scan went
    assert [r["execution_id"] for r in items[1]["payload"]["rows"]] == ["c"]
    assert box.waiting_execution_ids("lh1") == {"a", "b", "c"}
    assert box.status("lh1")["dropped"] == 1  # only the cap dropped something; repeats aren't drops


def test_a_damaged_outbox_is_set_aside_and_starts_empty(tmp_path):
    path = tmp_path / "o.sqlite"
    path.write_bytes(b"this is not a SQLite database, just bytes " * 200)
    box = ob.Outbox(path)
    assert box.set_aside is not None and box.set_aside.exists()
    assert box.status("lh1")["pending"] == 0
    box.add("lh1", ob.ANALYSIS, payload(), "down")  # writing goes on as usual
    assert box.status("lh1")["pending"] == 1
    assert ob.Outbox(path).set_aside is None  # a healthy file opens as it is


def test_bad_items_are_refused(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    with pytest.raises(ValueError):
        box.add("lh1", "something", {}, "")
    with pytest.raises(ValueError):
        box.add("", ob.ANALYSIS, payload(), "")


# The app's side: X-ray and shared local runs keep what they couldn't send.

def _offline(monkeypatch, method, exc):
    real = getattr(Shared, method)
    state = {"down": True}

    def flaky(self, *a, **k):
        if state["down"]:
            raise exc
        return real(self, *a, **k)
    monkeypatch.setattr(Shared, method, flaky)
    return state


def _token():
    return "token"


def test_an_xray_scan_that_cant_reach_the_lakehouse_waits_and_goes_next_time(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    state = _offline(monkeypatch, "record_analysis", ConnectionError("network down"))
    out = svc._xray_share(xray_result(), _token)
    assert out["queued"] == 1 and "network down" in out["error"]
    assert svc.optimize_findings() == []
    status = svc.optimize_status()["outbox"]
    assert status["pending"] == 1 and "network down" in status["last_error"]
    # Back online: the next scan sends the waiting one first, whatever its wait (a person or a scan asked).
    state["down"] = False
    out = svc._xray_share(xray_result(), _token)
    assert out["findings"] == 1
    status = svc.optimize_status()["outbox"]
    assert status["pending"] == 0 and status["last_sync"] and status["last_error"] is None
    [found] = svc.optimize_findings()
    assert found["occurrences"] == 1  # sent twice, recorded once


def test_switching_or_connecting_sends_what_is_waiting(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    lakehouse = svc.optimize_status()["active"]["lakehouse_id"]
    state = _offline(monkeypatch, "record_analysis", TimeoutError("timed out"))
    assert svc._xray_share(xray_result(), _token)["queued"] == 1
    state["down"] = False
    out = svc.optimize_switch(lakehouse)
    assert out["sync"]["sent"] == 1 and out["sync"]["waiting"] == 0
    assert len(svc.optimize_findings()) == 1


def test_a_permission_error_isnt_queued(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    _offline(monkeypatch, "record_analysis", PermissionError("403 Forbidden"))
    out = svc._xray_share(xray_result(), _token)
    assert "queued" not in out and "403" in out["error"]
    assert svc.optimize_status()["outbox"]["pending"] == 0


def test_a_waiting_item_the_lakehouse_refuses_is_dropped_not_stuck(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    lakehouse = svc.optimize_status()["active"]["lakehouse_id"]
    svc.optimize_outbox.add(lakehouse, "analysis", {"run": {}}, "down")  # no run id: refused
    out = svc.optimize_switch(lakehouse)
    assert out["sync"] == {"sent": 0, "dropped": 1, "waiting": 0}
    assert svc.optimize_status()["outbox"]["dropped"] == 1


def test_disconnect_keeps_what_is_waiting(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    lakehouse = svc.optimize_status()["active"]["lakehouse_id"]
    _offline(monkeypatch, "record_analysis", ConnectionError("down"))
    svc._xray_share(xray_result(), _token)
    svc.optimize_disconnect(lakehouse)
    assert svc.optimize_status()["outbox"] is None
    assert svc.optimize_outbox.status(lakehouse)["pending"] == 1


def test_shared_local_runs_wait_too(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    write = {"job": "Notebook write", "finished_at": "2026-10-20T09:00:00Z", "seconds": 3.0, "rows": 10,
             "table_uri": "abfss://w1@onelake.dfs.fabric.microsoft.com/lh-sales/Tables/dbo/orders", "delta_version": 1}
    monkeypatch.setattr(type(svc), "history", lambda self, limit=0: [write])
    monkeypatch.setattr(type(svc.pc_schedules), "runs", lambda self, limit=0: [])
    with pytest.raises(PermissionError) as asked:
        svc.optimize_share_local_runs()
    preview = asked.value.would  # type: ignore[attr-defined]
    assert preview["new"] == 1
    _offline(monkeypatch, "record_local_executions", ConnectionError("down"))
    out = svc.optimize_share_local_runs(approve=True)
    assert out["shared"] == 0 and out["queued"] == preview["new"]
    assert svc.optimize_status()["outbox"]["pending_rows"] == preview["new"]


def test_runs_shared_with_no_network_at_all_wait_once(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    write = {"job": "Notebook write", "finished_at": "2026-10-20T09:00:00Z", "seconds": 3.0, "rows": 10,
             "table_uri": "abfss://w1@onelake.dfs.fabric.microsoft.com/lh-sales/Tables/dbo/orders", "delta_version": 1}
    monkeypatch.setattr(type(svc), "history", lambda self, limit=0: [write])
    monkeypatch.setattr(type(svc.pc_schedules), "runs", lambda self, limit=0: [])
    down = ConnectionError("network down")
    read_ids = Shared.local_execution_ids
    monkeypatch.setattr(Shared, "local_execution_ids", lambda self: (_ for _ in ()).throw(down))
    state = _offline(monkeypatch, "record_local_executions", down)
    out = svc.optimize_share_local_runs(approve=True)
    assert out["queued"] == 1 and "network down" in out["offline"]
    again = svc.optimize_share_local_runs(approve=True)  # sharing again while still offline
    assert again["new"] == 0 and again["waiting"] == 1
    assert svc.optimize_status()["outbox"]["pending"] == 1
    # Back online: the waiting run goes with the next connect or switch, once.
    state["down"] = False
    monkeypatch.setattr(Shared, "local_execution_ids", read_ids)
    lakehouse = svc.optimize_status()["active"]["lakehouse_id"]
    assert svc.optimize_switch(lakehouse)["sync"]["sent"] == 1
