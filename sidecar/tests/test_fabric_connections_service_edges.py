"""Connections scan: the failure edges the main tests don't reach. The same fake Fabric stands in."""

from fabric_desktop import fabric_connections_service as fcs
from tests.test_fabric_connections import FakeFabric, svc  # noqa: F401 - the fixture is used by name


def test_a_refresh_while_a_scan_is_going_returns_the_pending_one(svc, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(fcs.threading, "Thread", lambda **kw: started.append(kw))
    svc._fabric_connections = {"pending": True, "connections": [], "missing": []}
    assert svc.fabric_connections(refresh=True) == {"pending": True, "connections": [], "missing": []}
    assert started == []


def test_a_connections_list_that_fails_ends_the_scan_with_its_error(svc, monkeypatch):  # noqa: F811
    def refuse(self):
        raise RuntimeError("503 busy")

    monkeypatch.setattr(FakeFabric, "list_connections", refuse)
    svc._scan_fabric_connections("tok", "conn-tok")
    out = svc._fabric_connections
    assert out["pending"] is False and out["connections"] == [] and "503 busy" in out["error"]


def test_a_gateway_list_that_fails_keeps_the_connections_and_says_why(svc, monkeypatch):  # noqa: F811
    def refuse(self):
        raise RuntimeError("429 Too many requests")

    monkeypatch.setattr(FakeFabric, "list_gateways", refuse)
    svc._scan_fabric_connections("tok", "conn-tok", "gw-tok")
    out = svc._fabric_connections
    assert "429" in out["gateways_error"]
    sql = next(c for c in out["connections"] if c["name"] == "sql-erp-prod")
    assert not sql.get("gateway_name")


def test_a_workspace_whose_items_cant_be_listed_is_counted_as_missed(svc, monkeypatch, caplog):  # noqa: F811
    real = FakeFabric.list_items

    def some(self, ws):
        if ws == "ws-test":
            raise RuntimeError("403 Forbidden")
        return real(self, ws)

    monkeypatch.setattr(FakeFabric, "list_items", some)
    warned = []
    monkeypatch.setattr(fcs._log, "warning", lambda *a, **k: warned.append(a))
    svc._scan_fabric_connections("tok", "conn-tok")
    out = svc._fabric_connections
    assert out["missed"] == 1 and out["pending"] is False
    assert warned and "items in workspace ws-test" in warned[0][2]
