"""Copy jobs for source requests: what mutation testing found unchecked in central/landing_copy_service.py.
Which Fabric answers count as a workspace that's gone, a job found by its marker or its recorded id,
a schedule already there, what each request shows and holds, and the names a record falls back to.
Fabric is a fake; no tenant is called."""

from __future__ import annotations

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.requests_service import StepError
from fabric_desktop.fabric_api import FabricError
from tests.test_landing_copy_requests import (  # noqa: F401 - fixtures
    CJ1, COPY, WS, AdminApi, _corrupt, api, ask, svc, who)

THEIRS = "11111111-2222-4333-8444-555555555555"


def _gone_after(monkeypatch, svc, api, listing, admin):  # noqa: F811
    def fail(workspace_id):
        raise listing
    monkeypatch.setattr(api, "list_items", fail)
    monkeypatch.setattr(type(svc), "_fabric_admin", lambda self, write=False: AdminApi(admin))


@pytest.mark.parametrize("listing", [
    FabricError("gone", code="EntityNotFound", status=400),   # the code alone says not found
    FabricError("gone", code="ItemNotFound", status=404),     # the status alone says not found
])
def test_either_a_404_or_a_not_found_code_lets_the_admin_api_confirm_it(svc, who, api, monkeypatch, listing):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)
    _gone_after(monkeypatch, svc, api, listing, {"state": "Removing"})
    assert all(s["name"] != "erp" for s in svc.platform_delete_source("erp"))


@pytest.mark.parametrize("admin", [
    FabricError("gone", code="EntityNotFound", status=400),
    FabricError("gone", code="Other", status=404),
])
def test_the_admin_api_saying_not_found_either_way_is_gone(svc, admin):  # noqa: F811
    svc._fabric_admin = lambda write=False: AdminApi(admin)
    assert svc._lc_workspace_gone(WS) is True


@pytest.mark.parametrize("admin", [FabricError("Forbidden", code="Denied", status=403), TimeoutError("slow")])
def test_an_admin_api_that_cant_confirm_answers_false(svc, admin):  # noqa: F811
    svc._fabric_admin = lambda write=False: AdminApi(admin)
    assert svc._lc_workspace_gone(WS) is False


def test_without_one_landing_workspace_no_request_says_where_it_lands(svc, who):  # noqa: F811
    ask(svc, who)
    assert all("lands_in" not in r for r in svc.platform_requests("waiting"))


def test_only_a_waiting_source_request_with_a_copy_says_where_it_lands(svc, who, monkeypatch):  # noqa: F811
    central = {"workspace_id": "ws-landing", "workspace_name": "Platform - Landing", "lakehouse_id": "lh-central"}
    monkeypatch.setattr(type(svc), "_central_landing", lambda self: central)
    monkeypatch.setattr(type(svc), "_landing_zones", lambda self: [])
    done = ask(svc, who)
    svc.platform_decide(done["id"], True)
    waiting = ask(svc, who, target="crm")
    who["user"] = "dev@contoso.com"
    plain = svc.platform_request("source", "shopify", "Orders")
    who["user"] = "ana@contoso.com"
    rows = {r["id"]: r for r in svc.platform_requests()}
    assert "lands_in" in rows[waiting["id"]]
    assert "lands_in" not in rows[done["id"]] and "lands_in" not in rows[plain["id"]]


def test_a_platform_with_a_workspace_but_no_landing_lakehouse_isnt_landed_in(svc, who, api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_platform_config", lambda self: {"dev": {"workspace_id": WS}})
    req = ask(svc, who)
    with pytest.raises(StepError, match="Dev has no LH_Landing in Fabric yet"):
        svc.platform_decide(req["id"], True, env="dev")
    assert api.calls == []


def test_an_environments_landing_is_never_secure_and_keeps_its_workspace_name(svc, who):  # noqa: F811
    req = ask(svc, who)
    assert svc._lc_where(req, "prod") == {
        "workspace_id": WS, "lakehouse_id": svc._platform_config()["prod"]["landing_id"],
        "lakehouse_name": "LH_Landing", "workspace_name": "Platform - Prod", "zone": "", "zone_name": "",
        "secure": False}


def test_a_job_this_request_made_found_by_its_marker_is_used_and_recorded(svc, who, api):  # noqa: F811
    req = ask(svc, who)
    api.items.append({"id": THEIRS, "type": "CopyJob", "displayName": "cj_landing_ERP", "workspaceId": WS,
                      "description": f"Lands erp. (KratosForge request {req['id']})"})
    out = svc.platform_decide(req["id"], True)
    assert out["made"]["copy_job_id"] == THEIRS and out["made"]["schedule_id"] == f"s-{THEIRS}"
    assert [c[0] for c in api.calls] == ["create_schedule"]


def test_a_job_found_again_by_its_recorded_id_isnt_recorded_twice(svc, who, api, monkeypatch):  # noqa: F811
    req = ask(svc, who)
    api.fail["create_schedule"] = 1
    with pytest.raises(StepError):
        svc.platform_decide(req["id"], True)
    api.items[0]["description"] = "renamed in Fabric"   # no marker left: the recorded id finds it
    recorded, real = [], type(svc)._rq_progress
    monkeypatch.setattr(type(svc), "_rq_progress", lambda self, r, made: recorded.append(made) or real(self, r, made))
    assert svc.platform_decide(req["id"], True)["made"]["copy_job_id"] == CJ1
    assert recorded == [{"schedule_id": f"s-{CJ1}"}]
    assert len(api.items) == 1


def test_items_of_another_type_dont_count_as_a_copy_job_of_that_name(svc, who, api):  # noqa: F811
    api.items.append({"id": THEIRS, "type": "Lakehouse", "displayName": "LH_Landing", "workspaceId": WS})
    api.items.append({"id": "nb", "type": "Notebook", "displayName": "CJ_Landing_erp", "workspaceId": WS})
    assert svc.platform_decide(ask(svc, who)["id"], True)["status"] == "approved"


def test_the_copy_jobs_description_carries_the_reason(svc, who, api):  # noqa: F811
    req = ask(svc, who)
    svc.platform_decide(req["id"], True)
    assert api.items[0]["description"] == (f"Lands erp in the landing zone. Customer 360 needs ERP customers "
                                           f"(KratosForge request {req['id']})")


def test_without_times_no_schedule_is_made(svc, who, api):  # noqa: F811
    out = svc.platform_decide(ask(svc, who, COPY | {"times": []})["id"], True)
    assert "schedule_id" not in out["made"] and [c[0] for c in api.calls] == ["save_item"]


def test_a_schedule_already_on_the_job_is_kept(svc, who, api):  # noqa: F811
    req = ask(svc, who)
    api.fail["create_schedule"] = 1
    with pytest.raises(StepError):
        svc.platform_decide(req["id"], True)
    api.schedules[CJ1] = [{"id": "s-there"}, {"id": "s-later"}]
    assert svc.platform_decide(req["id"], True)["made"]["schedule_id"] == "s-there"
    assert [c[0] for c in api.calls] == ["save_item"]


def test_another_sources_copy_job_doesnt_hold_a_source(svc, who, api):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)
    svc.platform_save_source({"name": "crm"})
    assert all(s["name"] != "crm" for s in svc.platform_delete_source("crm"))


def test_a_recorded_job_name_is_the_one_named_and_a_missing_one_falls_back(svc, who, api):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)
    _corrupt(svc, "copy_job_name", "CJ_Renamed")
    erp = next(s for s in svc.platform_landing("dev")["sources"] if s["name"] == "erp")
    assert erp["copy_job"]["name"] == "CJ_Renamed"
    with pytest.raises(core.PlatformError, match="has the Copy job CJ_Renamed in"):
        svc.platform_delete_source("erp")
    _corrupt(svc, "copy_job_name", "")
    erp = next(s for s in svc.platform_landing("dev")["sources"] if s["name"] == "erp")
    assert erp["copy_job"]["name"] == "CJ_Landing_erp"
    with pytest.raises(core.PlatformError, match="has the Copy job CJ_Landing_erp in"):
        svc.platform_delete_source("erp")


def test_the_landing_zone_lists_a_source_approved_without_a_copy_job(svc, who):  # noqa: F811
    who["user"] = "dev@contoso.com"
    plain = svc.platform_request("source", "shopify", "Orders")
    who["user"] = "ana@contoso.com"
    svc.platform_decide(plain["id"], True)
    svc.platform_decide(ask(svc, who)["id"], True)
    sources = {s["name"]: s for s in svc.platform_landing("dev")["sources"]}
    assert "copy_job" not in sources["shopify"] and sources["erp"]["copy_job"]["id"] == CJ1
