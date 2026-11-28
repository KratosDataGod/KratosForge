"""central/landing_copy_service.py paths that its first mutation run (batch 380,
docs/quality/mutation-testing.md) showed no test checked. Fabric is a fake; no tenant is called."""

from __future__ import annotations

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.requests_service import StepError
from fabric_desktop.fabric_api import FabricError
from tests.test_landing_copy_requests import COPY, WS, AdminApi, api, ask, svc, who  # noqa: F401 - fixtures


@pytest.mark.parametrize("status, code", [(404, "SomethingElse"), (400, "EntityNotFound")])
def test_either_a_404_or_a_not_found_code_asks_the_admin_api(svc, who, api, monkeypatch, status, code):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)

    def not_found(workspace_id):
        raise FabricError("not found", code=code, status=status)

    monkeypatch.setattr(api, "list_items", not_found)
    monkeypatch.setattr(type(svc), "_fabric_admin", lambda self, write=False: AdminApi({"state": "Deleted"}))
    assert all(s["name"] != "erp" for s in svc.platform_delete_source("erp"))


def test_no_access_is_not_gone_even_when_the_admin_api_says_deleted(svc, who, api, monkeypatch):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)

    def denied(workspace_id):
        raise FabricError("Forbidden", code="InsufficientPrivileges", status=403)

    monkeypatch.setattr(api, "list_items", denied)
    monkeypatch.setattr(type(svc), "_fabric_admin", lambda self, write=False: AdminApi({"state": "Deleted"}))
    with pytest.raises(core.PlatformError, match="Couldn't check whether erp's Copy job"):
        svc.platform_delete_source("erp")
    assert any(s["name"] == "erp" for s in svc.platform_sources())


def test_without_one_landing_workspace_a_waiting_request_says_nothing_of_where_it_lands(svc, who):  # noqa: F811
    req = ask(svc, who)
    [waiting] = [r for r in svc.platform_requests("waiting") if r["id"] == req["id"]]
    assert "lands_in" not in waiting


def test_a_copy_job_this_request_made_but_never_recorded_is_recorded_on_approval(svc, who, api):  # noqa: F811
    req = ask(svc, who)
    api.items.append({"id": "00000000-0000-4000-8000-0000000000aa", "type": "CopyJob", "displayName": "CJ_Landing_erp",
                      "description": f"Lands erp. (KratosForge request {req['id']})", "workspaceId": WS})
    out = svc.platform_decide(req["id"], True)
    assert out["made"]["copy_job_id"] == "00000000-0000-4000-8000-0000000000aa"
    assert (out["made"]["workspace_id"], out["made"]["copy_job_name"]) == (WS, "CJ_Landing_erp")
    assert [c[0] for c in api.calls] == ["create_schedule"]


def test_a_copy_job_without_times_gets_no_schedule(svc, who, api):  # noqa: F811
    out = svc.platform_decide(ask(svc, who, COPY | {"times": []})["id"], True)
    assert "schedule_id" not in out["made"] and [c[0] for c in api.calls] == ["save_item"]
    assert api.schedules == {}


def test_an_environment_with_a_workspace_but_no_landing_lakehouse_says_where_to_set_it_up(svc, who, api, monkeypatch):  # noqa: F811
    platforms = {"test": {"workspace_id": WS, "workspace_name": "Platform - Test"}}
    monkeypatch.setattr(type(svc), "_platform_config", lambda self: platforms)
    req = ask(svc, who)
    with pytest.raises(StepError, match="Test has no LH_Landing in Fabric yet"):
        svc.platform_decide(req["id"], True, env="test")
    assert api.calls == []


def test_a_copy_job_for_another_source_never_holds_this_one(svc, who, api):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)
    svc.platform_decide(ask(svc, who, target="crm")["id"], True)
    api.items[:] = [i for i in api.items if i["displayName"] != "CJ_Landing_crm"]  # crm's job deleted in Fabric
    assert all(s["name"] != "crm" for s in svc.platform_delete_source("crm"))
    with pytest.raises(core.PlatformError, match="erp has the Copy job CJ_Landing_erp"):
        svc.platform_delete_source("erp")


def test_the_landing_page_lists_a_source_approved_without_a_copy_job(svc, who, api):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)
    who["user"] = "dev@contoso.com"
    req = svc.platform_request("source", "shopify", "Orders from the shop")
    who["user"] = "ana@contoso.com"
    svc.platform_decide(req["id"], True)
    sources = {s["name"]: s for s in svc.platform_landing("dev")["sources"]}
    assert "copy_job" not in sources["shopify"] and sources["erp"]["copy_job"]["name"] == "CJ_Landing_erp"


def test_the_landing_page_names_a_copy_job_whose_name_was_not_recorded(svc, who, api):  # noqa: F811
    from tests.test_landing_copy_requests import _corrupt

    svc.platform_decide(ask(svc, who)["id"], True)
    _corrupt(svc, "copy_job_name", "")
    erp = next(s for s in svc.platform_landing("dev")["sources"] if s["name"] == "erp")
    assert erp["copy_job"]["name"] == "CJ_Landing_erp"


def test_with_one_landing_workspace_only_waiting_copy_requests_say_where_they_land(svc, who, api, monkeypatch):  # noqa: F811
    central = {"workspace_id": "ws-landing", "workspace_name": "Platform - Landing", "lakehouse_id": "lh-central"}
    monkeypatch.setattr(type(svc), "_central_landing", lambda self: central)
    monkeypatch.setattr(type(svc), "_landing_zones", lambda self: [])
    copy = ask(svc, who)
    who["user"] = "dev@contoso.com"
    plain = svc.platform_request("source", "shopify", "Orders from the shop")
    who["user"] = "ana@contoso.com"
    waiting = {r["id"]: r for r in svc.platform_requests("waiting")}
    assert "lands_in" in waiting[copy["id"]] and "lands_in" not in waiting[plain["id"]]
    svc.platform_decide(copy["id"], True)
    [approved] = [r for r in svc.platform_requests("approved") if r["id"] == copy["id"]]
    assert "lands_in" not in approved


@pytest.mark.parametrize("status, code", [(404, "SomethingElse"), (400, "EntityNotFound")])
def test_the_admin_api_saying_not_found_either_way_lets_the_source_go(svc, who, api, monkeypatch, status, code):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)

    def not_found(workspace_id):
        raise FabricError("not found", code="WorkspaceNotFound", status=404)

    monkeypatch.setattr(api, "list_items", not_found)
    monkeypatch.setattr(type(svc), "_fabric_admin",
                        lambda self, write=False: AdminApi(FabricError("not found", code=code, status=status)))
    assert all(s["name"] != "erp" for s in svc.platform_delete_source("erp"))


def test_other_items_in_the_landing_workspace_never_block_the_copy_job(svc, who, api):  # noqa: F811
    api.items += [{"id": "theirs", "type": "CopyJob", "displayName": "CJ_Landing_crm", "description": "", "workspaceId": WS},
                  {"id": "nb", "type": "Notebook", "displayName": "CJ_Landing_erp", "description": "", "workspaceId": WS}]
    assert svc.platform_decide(ask(svc, who)["id"], True)["status"] == "approved"
    assert [c[0] for c in api.calls] == ["save_item", "create_schedule"]


def test_approving_again_reuses_the_recorded_job_and_does_not_record_it_twice(svc, who, api, monkeypatch):  # noqa: F811
    req = ask(svc, who)
    api.fail["create_schedule"] = 1
    with pytest.raises(StepError):
        svc.platform_decide(req["id"], True)
    api.items[0]["description"] = "Edited in Fabric"  # the marker is gone; the recorded id still names it
    recorded = []
    real = type(svc)._rq_progress
    monkeypatch.setattr(type(svc), "_rq_progress", lambda self, r, fields: recorded.append(sorted(fields)) or real(self, r, fields))
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    assert len(api.items) == 1 and recorded == [["schedule_id"]]


def test_the_copy_job_description_carries_the_reason(svc, who, api):  # noqa: F811
    svc.platform_decide(ask(svc, who)["id"], True)
    assert api.items[0]["description"].startswith("Lands erp in the landing zone. Customer 360 needs ERP customers")


def test_a_schedule_already_on_the_copy_job_is_reused(svc, who, api):  # noqa: F811
    req = ask(svc, who)
    jid = "00000000-0000-4000-8000-0000000000bb"
    api.items.append({"id": jid, "type": "CopyJob", "displayName": "CJ_Landing_erp",
                      "description": f"Lands erp. (KratosForge request {req['id']})", "workspaceId": WS})
    api.schedules[jid] = [{"id": "s-already", "jobType": "Execute"}]
    out = svc.platform_decide(req["id"], True)
    assert out["made"]["schedule_id"] == "s-already" and api.calls == []


def test_a_held_source_names_the_copy_job_even_when_its_name_was_not_recorded(svc, who, api):  # noqa: F811
    from tests.test_landing_copy_requests import _corrupt

    svc.platform_decide(ask(svc, who)["id"], True)
    _corrupt(svc, "copy_job_name", "")
    with pytest.raises(core.PlatformError, match="erp has the Copy job CJ_Landing_erp in the landing workspace"):
        svc.platform_delete_source("erp")
