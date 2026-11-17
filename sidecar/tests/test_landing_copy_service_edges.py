"""Copy jobs for source requests: the edges the main tests don't reach. Where a request lands when
the shared zone isn't listed or a zone has no lakehouse, a stored copy that no longer checks, Fabric
answering without an id, an admin API that fails oddly, and a landing page whose requests can't be
read. Fabric is a fake; no tenant is called."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.requests_service import StepError
from fabric_desktop.fabric_api import FabricError
from tests.test_landing_copy_requests import COPY, AdminApi, api, ask, svc, who  # noqa: F401 - fixtures

CENTRAL = {"workspace_id": "ws-landing", "workspace_name": "Platform - Landing", "lakehouse_id": "lh-central"}


def landing(monkeypatch, svc, zones, central=CENTRAL):  # noqa: F811
    monkeypatch.setattr(type(svc), "_central_landing", lambda self: central)
    monkeypatch.setattr(type(svc), "_landing_zones", lambda self: zones)


def test_the_shared_zone_lands_in_the_central_lakehouse_when_no_zone_lists_it(svc, who, api, monkeypatch):  # noqa: F811
    landing(monkeypatch, svc, [])
    req = ask(svc, who)
    [waiting] = [r for r in svc.platform_requests("waiting") if r["id"] == req["id"]]
    assert waiting["lands_in"] == {"zone": "shared", "zone_name": "Shared", "secure": False,
                                   "workspace_name": "Platform - Landing", "workspace_id": "ws-landing",
                                   "lakehouse_id": "lh-central", "lakehouse_name": "LH_Landing"}


def test_a_zone_without_a_lakehouse_says_so_before_and_at_approval(svc, who, api, monkeypatch):  # noqa: F811
    landing(monkeypatch, svc, [{"id": "hr", "name": "HR", "secure": True, "workspace_id": "ws-landing"}])
    who["user"] = "dev@contoso.com"
    req = svc.platform_request("source", "payroll", "", {"copy": COPY, "zone": "hr"})
    who["user"] = "ana@contoso.com"
    [waiting] = svc.platform_requests("waiting")
    assert "landing zone hr has no lakehouse yet" in waiting["lands_in"]["problem"]
    with pytest.raises(StepError, match="Landing zone, then approve again"):
        svc.platform_decide(req["id"], True)
    assert api.calls == []


def test_a_stored_copy_that_no_longer_checks_asks_to_decline_it(svc, who, api):  # noqa: F811
    req = ask(svc, who)
    detail = dict(req["detail"], copy=dict(req["detail"]["copy"], tables=[]))
    with svc._catalog()._db() as db:
        db.run(f"UPDATE {db.t('catalog', 'request')} SET detail = ? WHERE id = ?", (json.dumps(detail), req["id"]))
        db.conn.commit()
    with pytest.raises(core.PlatformError, match="Decline the request and ask for it again"):
        svc.platform_decide(req["id"], True)
    assert api.calls == []


def test_fabric_answering_without_an_id_leaves_the_request_waiting(svc, who, api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api, "save_item", lambda *a, **k: {"displayName": "CJ_Landing_erp", "created": True})
    req = ask(svc, who)
    with pytest.raises(StepError, match="Fabric returned no id"):
        svc.platform_decide(req["id"], True)
    assert [r["id"] for r in svc.platform_requests("waiting")] == [req["id"]]


def test_an_admin_api_that_fails_oddly_never_counts_as_gone(svc, who, api, monkeypatch):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)

    def not_found(workspace_id):
        raise FabricError("not found", code="WorkspaceNotFound", status=404)

    monkeypatch.setattr(api, "list_items", not_found)
    monkeypatch.setattr(type(svc), "_fabric_admin", lambda self, write=False: AdminApi(TimeoutError("read timed out")))
    with pytest.raises(core.PlatformError, match="Couldn't check whether erp's Copy job"):
        svc.platform_delete_source("erp")
    assert any(s["name"] == "erp" for s in svc.platform_sources())


def test_the_landing_page_still_lists_sources_when_requests_cant_be_read(svc, who, api, monkeypatch):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)
    catalog = svc._catalog()
    real = catalog.call

    def call(op, **kw):
        if op == "list_requests" and kw.get("status") == "approved":
            raise core.PlatformError("catalog busy")
        return real(op, **kw)

    monkeypatch.setattr(catalog, "call", call)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: catalog)
    erp = next(s for s in svc.platform_landing("dev")["sources"] if s["name"] == "erp")
    assert "copy_job" not in erp
