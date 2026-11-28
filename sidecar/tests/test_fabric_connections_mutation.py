"""Mutation batch 80: the Connections scan's edges. Every field of a connection row, the environment
order with prod and a custom environment, the cap on definitions read, the background scan's thread,
and how long ids are cut in what was missed."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop import fabric_connections_service as fcs
from fabric_desktop.redact import one_line
from tests.test_fabric_connections import FakeFabric, _definition, svc  # noqa: F401 - the fixture is used by name


def test_a_connection_row_keeps_each_field_fabric_gives():
    row = fcs.connection_row({"id": "ABC", "displayName": "erp", "connectivityType": "ShareableCloud",
                              "gatewayId": "GW", "privacyLevel": "Organizational",
                              "connectionDetails": {"type": "SQL", "path": "erp.contoso.local"},
                              "credentialDetails": {"credentialType": "OAuth2"}})
    assert row == {"id": "abc", "name": "erp", "type": "SQL", "path": "erp.contoso.local",
                   "connectivity": "ShareableCloud", "gateway_id": "gw", "gateway_name": "", "gateway_members": None,
                   "privacy": "Organizational", "credential_type": "OAuth2", "used_by": []}
    bare = fcs.connection_row({"id": "x"})
    assert (bare["name"], bare["type"], bare["path"], bare["privacy"], bare["credential_type"]) == ("x", "", "", "", "")


def test_environments_are_dev_test_prod_then_any_other_by_name(svc, monkeypatch):  # noqa: F811
    envs = {"uat": {"workspace_id": "ws-x"}, "prod": {"workspace_id": "ws-x"}, "test": {"workspace_id": "ws-test"},
            "dev": {"workspace_id": "ws-dev"}, "qa": {"workspace_id": "ws-dev"}, "stage": {}}
    monkeypatch.setattr(svc, "_project", lambda: SimpleNamespace(environments=envs))
    out = svc._connection_workspaces("tok")
    assert [(w["env"], w["id"], w["name"]) for w in out] == [
        ("dev", "ws-dev", "Sales Dev"), ("test", "ws-test", "Sales Test"), ("prod", "ws-x", "Other"),
        ("qa", "ws-dev", "Sales Dev"), ("uat", "ws-x", "Other")]


def test_at_most_500_definitions_are_read_and_the_rest_counted_as_skipped(svc, monkeypatch):  # noqa: F811
    many = [{"id": f"pl{i}", "type": "DataPipeline", "displayName": f"p{i}"} for i in range(501)]
    monkeypatch.setattr(FakeFabric, "list_items", lambda self, ws: many if ws == "ws-dev" else [])
    monkeypatch.setattr(FakeFabric, "get_item_definition", lambda self, ws, item, fmt=None: (
        FakeFabric.calls.append(item), _definition({"properties": {}}))[1])
    svc._scan_fabric_connections("tok", "conn-tok")
    out = svc._fabric_connections
    assert (out["items"], out["skipped"], out["missed"]) == (501, 1, 0)
    assert len(FakeFabric.calls) == 500 and "pl500" not in FakeFabric.calls


def test_the_scan_runs_on_a_daemon_thread_so_it_never_holds_the_app_open(svc, monkeypatch):  # noqa: F811
    started = []

    class Thread:
        def __init__(self, **kw):
            started.append(kw)

        def start(self):
            pass

    monkeypatch.setattr(fcs.threading, "Thread", Thread)
    assert svc.fabric_connections()["pending"] is True
    assert len(started) == 1 and started[0]["daemon"] is True
    assert started[0]["args"] == ("tok", "conn-tok", "gw-tok", "")


def test_long_ids_in_what_was_missed_are_cut_to_100_characters(svc, monkeypatch):  # noqa: F811
    long_ws, long_item = "w" * 150, "i" * 150
    monkeypatch.setattr(svc, "_project", lambda: SimpleNamespace(environments={}))
    monkeypatch.setattr(svc, "_schedule_workspaces", lambda all_ws, token: [
        {"id": long_ws, "name": "Long"}, {"id": "ws-dev", "name": "Sales Dev"}])

    def items(self, ws):
        if ws == long_ws:
            raise RuntimeError("403 Forbidden")
        return [{"id": long_item, "type": "DataPipeline", "displayName": "pl"}]

    def definition(self, ws, item, fmt=None):
        raise RuntimeError("404 Not found")

    monkeypatch.setattr(FakeFabric, "list_items", items)
    monkeypatch.setattr(FakeFabric, "get_item_definition", definition)
    warned = []
    monkeypatch.setattr(fcs._log, "warning", lambda *a, **k: warned.append(a))
    svc._scan_fabric_connections("tok", "conn-tok")
    assert svc._fabric_connections["missed"] == 2
    first = warned[0][2]
    assert first == f"items in workspace {one_line(long_ws, 100)}: RuntimeError: 403 Forbidden"
    assert len(one_line(long_ws, 100)) == 100 and one_line(long_ws, 100) != one_line(long_ws, 101)

    # The definition that can't be read is named the same way.
    monkeypatch.setattr(svc, "_schedule_workspaces", lambda all_ws, token: [{"id": "ws-dev", "name": "Sales Dev"}])
    warned.clear()
    svc._scan_fabric_connections("tok", "conn-tok")
    assert warned[0][2] == f"definition of {one_line(long_item, 100)}: RuntimeError: 404 Not found"
