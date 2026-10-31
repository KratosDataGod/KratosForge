"""Scale plan stage 4.1 (decision D2): a team asks for a source with a Copy job, a platform admin
approves, and approving makes the Copy job in the landing workspace as the approver. Fabric is a fake;
no tenant is called."""

import base64
import json

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.requests_service import StepError
from fabric_desktop.orchestration import copyjob, copyjob_build
from fabric_desktop.service import Service

ADMIN, DEV = "ana@contoso.com", "dev@contoso.com"
CONN = "0f0e0d0c-0b0a-0908-0706-050403020100"
WS, LH = "1a2b3c4d-0000-4000-8000-00000000a001", "1a2b3c4d-0000-4000-8000-00000000a002"
CJ1 = "00000000-0000-4000-8000-000000000001"  # the first Copy job the fake makes
COPY = {"connection_id": CONN.upper(), "connection_name": "SQL Server: erp-sql01 / ERP",
        "tables": [{"table": "dbo.Customer", "mode": "incremental", "column": "ModifiedAt"},
                   {"table": "dbo.Region"}],
        "times": ["02:00"]}


class FakeFabric:
    """Items and schedules in memory; records who made what."""

    def __init__(self, me):
        self.me, self.items, self.schedules, self.calls, self.fail = me, [], {}, [], {}

    def _maybe_fail(self, op):
        if self.fail.get(op):
            self.fail[op] -= 1
            raise RuntimeError(f"Fabric said no to {op}")

    def list_items(self, workspace_id):
        return [dict(i) for i in self.items if i["workspaceId"] == workspace_id]

    def save_item(self, workspace_id, item_type, name, parts=None, description=""):
        self._maybe_fail("save_item")
        item = {"id": f"00000000-0000-4000-8000-{len(self.items) + 1:012d}", "type": item_type, "displayName": name, "description": description,
                "workspaceId": workspace_id, "parts": parts}
        self.items.append(item)
        self.calls.append(("save_item", self.me(), workspace_id, name))
        return dict(item, created=True)

    def list_schedules(self, workspace_id, item_id, job_type):
        return list(self.schedules.get(item_id, []))

    def create_schedule(self, workspace_id, item_id, job_type, configuration, enabled=True):
        self._maybe_fail("create_schedule")
        s = {"id": f"s-{item_id}", "jobType": job_type, "configuration": configuration, "enabled": enabled}
        self.schedules.setdefault(item_id, []).append(s)
        self.calls.append(("create_schedule", self.me(), item_id, job_type))
        return s


class Auth:
    def __init__(self, who):
        self.who = who

    def signed_in_user(self):
        return self.who["user"]

    def _token(self, scopes):
        return "tok"


@pytest.fixture
def who():
    return {"user": ADMIN}


@pytest.fixture
def api(who):
    return FakeFabric(lambda: who["user"])


@pytest.fixture
def svc(tmp_path, monkeypatch, who, api):
    s = Service(root=tmp_path / "app")
    s._auth = Auth(who)
    monkeypatch.setattr(type(s), "_rq_fabric", lambda self: api)
    monkeypatch.setattr(type(s), "fabric_jobs", property(lambda self: api))
    monkeypatch.setattr(type(s), "_central_landing", lambda self: {})
    platforms = {"dev": {}, "test": {}, "prod": {"workspace_id": WS, "landing_id": LH, "workspace_name": "Platform - Prod"}}
    monkeypatch.setattr(type(s), "_platform_config", lambda self: platforms)
    s.platform_whoami()  # the first signed-in person is the PC platform's admin
    return s


def ask(svc, who, copy=None, target="erp"):
    who["user"] = DEV
    out = svc.platform_request("source", target, "Customer 360 needs ERP customers",
                               {"owner": "sales-data@contoso.com", "expected_hours": "24", "copy": copy or COPY})
    who["user"] = ADMIN
    return out


# --------------------------------------------------------------------------- what a team may ask for


def test_check_normalizes_what_a_team_asks_for():
    spec = copyjob_build.check(COPY)
    assert spec["connection_id"] == CONN
    assert spec["tables"] == [
        {"table": "dbo.Customer", "mode": "incremental", "column": "ModifiedAt", "folder": "customer"},
        {"table": "dbo.Region", "mode": "full", "column": "", "folder": "region"}]
    assert spec["times"] == ["02:00"]


@pytest.mark.parametrize("change, says", [
    ({"connection_id": "erp"}, "Pick the Fabric connection"),
    ({"tables": []}, "at least one table"),
    ({"tables": [{"table": "Customer"}]}, "schema.table"),
    ({"tables": [{"table": "dbo.Customer; DROP TABLE x"}]}, "schema.table"),
    ({"tables": [{"table": "dbo.Customer", "mode": "incremental"}]}, "which column"),
    ({"tables": [{"table": "dbo.Customer", "mode": "cdc"}]}, "whole table"),
    ({"tables": [{"table": "dbo.Customer"}, {"table": "sales.customer"}]}, "same folder customer/"),
    ({"tables": [{"table": f"dbo.T{i}"} for i in range(51)]}, "50 tables at most"),
    ({"times": ["2am"]}, "HH:MM"),
])
def test_check_refuses_what_cant_become_a_copy_job(change, says):
    with pytest.raises(copyjob_build.CopyRequestError, match=says):
        copyjob_build.check(COPY | change)


def test_the_built_job_reads_back_through_the_data_factory_reader():
    job = copyjob_build.content(copyjob_build.check(COPY), "erp", WS, LH)
    payload = base64.b64encode(copyjob_build.parts(job)[copyjob.CONTENT].encode()).decode()
    out = copyjob.describe({"definition": {"parts": [{"path": copyjob.CONTENT, "payload": payload}]}})
    assert out["problems"] == [] and out["job_mode"] == "Batch"
    assert out["source"]["connection_id"] == CONN
    assert (out["destination"]["workspace_id"], out["destination"]["item_id"]) == (WS, LH)
    cust, region = out["tables"]
    assert (cust["source"]["schema"], cust["source"]["table"]) == ("dbo", "Customer")
    assert cust["destination"]["folder"] == "erp/customer"
    assert cust["incremental_columns"] == ["ModifiedAt"] and region["incremental_columns"] == []
    assert region["destination"]["folder"] == "erp/region" and region["write_behavior"] == "Append"


def test_sending_checks_the_copy_and_keeps_it_normalized(svc, who):
    req = ask(svc, who)
    assert req["status"] == "waiting" and req["detail"]["copy"]["connection_id"] == CONN
    with pytest.raises(core.PlatformError, match="which column"):
        ask(svc, who, COPY | {"tables": [{"table": "dbo.Customer", "mode": "incremental"}]}, target="crm")


# --------------------------------------------------------------------------- approving makes it


def test_approving_makes_the_copy_job_and_its_schedule_as_the_approver(svc, who, api):
    req = ask(svc, who)
    out = svc.platform_decide(req["id"], True, "ok")
    assert out["status"] == "approved"
    assert out["made"] == {"copy_job_id": CJ1, "workspace_id": WS, "copy_job_name": "CJ_Landing_erp",
                           "schedule_id": f"s-{CJ1}"}
    assert [c[:2] for c in api.calls] == [("save_item", ADMIN), ("create_schedule", ADMIN)]
    item = api.items[0]
    assert item["type"] == "CopyJob" and item["workspaceId"] == WS
    assert f"(KratosForge request {req['id']})" in item["description"]
    job = json.loads(item["parts"][copyjob.CONTENT])
    assert job["properties"]["destination"]["connectionSettings"]["typeProperties"]["artifactId"] == LH
    sched = api.schedules[CJ1][0]
    assert sched["jobType"] == "Execute" and sched["configuration"]["type"] == "Daily"
    assert sched["configuration"]["times"] == ["02:00"] and sched["configuration"]["localTimeZoneId"] == "UTC"
    src = next(s for s in svc.platform_sources() if s["name"] == "erp")
    assert src["owner"] == "sales-data@contoso.com" and src["expected_hours"] == "24"


def test_a_failed_step_leaves_it_waiting_and_approving_again_carries_on(svc, who, api):
    req = ask(svc, who)
    api.fail["create_schedule"] = 1
    with pytest.raises(StepError, match="Schedule CJ_Landing_erp daily at 02:00 UTC failed"):
        svc.platform_decide(req["id"], True)
    waiting = next(r for r in svc.platform_requests("waiting") if r["id"] == req["id"])
    assert waiting["detail"]["made"]["copy_job_id"] == CJ1
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    assert [c[0] for c in api.calls] == ["save_item", "create_schedule"]  # one Copy job, not two
    assert len(api.items) == 1


def test_a_copy_job_of_the_same_name_it_did_not_make_is_never_taken_over(svc, who, api):
    api.items.append({"id": "theirs", "type": "CopyJob", "displayName": "CJ_Landing_erp", "description": "",
                      "workspaceId": WS})
    req = ask(svc, who)
    with pytest.raises(StepError, match="this request didn't make it"):
        svc.platform_decide(req["id"], True)
    assert api.calls == [] and svc.platform_requests("waiting")[0]["id"] == req["id"]


def test_without_a_landing_lakehouse_approving_says_where_to_set_it_up(svc, who, api):
    req = ask(svc, who)
    with pytest.raises(StepError, match="Test has no LH_Landing in Fabric yet"):
        svc.platform_decide(req["id"], True, env="test")
    assert api.calls == []


def test_only_a_platform_admin_approves_and_declining_makes_nothing(svc, who, api):
    req = ask(svc, who)
    who["user"] = DEV
    with pytest.raises(PermissionError):
        svc.platform_decide(req["id"], True)
    who["user"] = ADMIN
    assert svc.platform_decide(req["id"], False, "Use the ERP export")["status"] == "declined"
    assert api.calls == []


def test_a_source_request_without_a_copy_job_is_approved_as_before(svc, who, api):
    who["user"] = DEV
    req = svc.platform_request("source", "shopify", "Orders from the shop")
    who["user"] = ADMIN
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    assert api.calls == [] and any(s["name"] == "shopify" for s in svc.platform_sources())


def test_the_landing_zone_names_the_copy_job_that_lands_a_source(svc, who):
    svc.platform_decide(ask(svc, who)["id"], True)
    erp = next(s for s in svc.platform_landing("dev")["sources"] if s["name"] == "erp")
    assert erp["copy_job"] | {"request_id": ""} == {"id": CJ1, "workspace_id": WS, "name": "CJ_Landing_erp",
                                                    "tables": 2, "times": ["02:00"], "request_id": ""}


def test_with_one_landing_workspace_the_request_says_which_zone_it_lands_in(svc, who, api, monkeypatch):
    central = {"workspace_id": "ws-landing", "workspace_name": "Platform - Landing", "lakehouse_id": "lh-shared"}
    zones = [{"id": "shared", "name": "Shared", "workspace_id": "ws-landing", "lakehouse_id": "lh-shared",
              "lakehouse_name": "LH_Landing", "workspace_name": "Platform - Landing"},
             {"id": "hr", "name": "HR", "secure": True, "workspace_id": "ws-landing", "lakehouse_id": "lh-hr",
              "lakehouse_name": "LH_Landing_HR", "workspace_name": "Platform - Landing"}]
    monkeypatch.setattr(type(svc), "_central_landing", lambda self: central)
    monkeypatch.setattr(type(svc), "_landing_zones", lambda self: zones)
    who["user"] = DEV
    shared = svc.platform_request("source", "erp", "", {"copy": COPY})
    secure = svc.platform_request("source", "payroll", "", {"copy": COPY, "zone": "hr"})
    who["user"] = ADMIN
    lands = {r["target"]: r["lands_in"] for r in svc.platform_requests("waiting")}
    assert lands["erp"] | {"workspace_id": "", "lakehouse_id": ""} == {
        "zone": "shared", "zone_name": "Shared", "secure": False, "workspace_name": "Platform - Landing",
        "lakehouse_name": "LH_Landing", "workspace_id": "", "lakehouse_id": ""}
    assert (lands["payroll"]["zone_name"], lands["payroll"]["secure"], lands["payroll"]["lakehouse_name"]) == ("HR", True, "LH_Landing_HR")
    assert secure["status"] == "waiting"
    svc.platform_decide(shared["id"], True)
    assert api.items[0]["workspaceId"] == "ws-landing"
    assert json.loads(api.items[0]["parts"][copyjob.CONTENT])["properties"]["destination"]["connectionSettings"][
        "typeProperties"]["artifactId"] == "lh-shared"


# --------------------------------------------------------------------------- the Copy job's description (#1069)


def test_a_long_reason_still_makes_the_copy_job_with_its_marker(svc, who, api):
    """Fabric's Create Item takes a description of at most 256 characters."""
    real = api.save_item

    def fabric_limit(workspace_id, item_type, name, parts=None, description=""):
        if len(description) > 256:
            raise RuntimeError("400 The description is longer than 256 characters")
        return real(workspace_id, item_type, name, parts, description)
    api.save_item = fabric_limit
    who["user"] = DEV
    other = svc.platform_request("source", "crm", "x", {"owner": "sales-data@contoso.com", "expected_hours": "24"})
    req = svc.platform_request("source", "erp", f"{'Customer 360 needs ERP customers. ' * 30} (KratosForge request "
                               f"{other['id']})", {"owner": "sales-data@contoso.com", "expected_hours": "24",
                                                   "copy": COPY})
    who["user"] = ADMIN
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    description = api.items[0]["description"]
    assert len(description) <= 256 and description.endswith(f" (KratosForge request {req['id']})")
    assert description.startswith("Lands erp in the landing zone.") and other["id"] not in description


# --------------------------------------------------------------------------- deleting a source with a Copy job (#1311)


def test_a_source_whose_copy_job_is_still_in_fabric_is_not_deleted(svc, who, api):
    svc.platform_decide(ask(svc, who)["id"], True)
    with pytest.raises(core.PlatformError) as err:
        svc.platform_delete_source("erp")
    assert str(err.value) == ("erp has the Copy job CJ_Landing_erp in the landing workspace, on its schedule: "
                              "delete it in Fabric first, then delete the source")
    assert any(s["name"] == "erp" for s in svc.platform_sources())
    assert [c[0] for c in api.calls] == ["save_item", "create_schedule"]  # nothing changed in Fabric
    assert CJ1 in {i["id"] for i in api.items}


def test_once_the_copy_job_is_deleted_in_fabric_the_source_deletes(svc, who, api):
    svc.platform_decide(ask(svc, who)["id"], True)
    api.items.clear()  # an admin deleted CJ_Landing_erp in Fabric
    assert all(s["name"] != "erp" for s in svc.platform_delete_source("erp"))


def test_a_source_without_a_copy_job_deletes_as_before(svc, who, api):
    who["user"] = DEV
    req = svc.platform_request("source", "shopify", "Orders from the shop")
    who["user"] = ADMIN
    svc.platform_decide(req["id"], True)
    api.list_items = None  # Fabric isn't read: there's no job to ask about
    assert all(s["name"] != "shopify" for s in svc.platform_delete_source("shopify"))


def test_a_copy_job_a_withdrawn_request_made_still_holds_the_source(svc, who, api):
    req = ask(svc, who)
    api.fail["create_schedule"] = 1
    with pytest.raises(StepError):
        svc.platform_decide(req["id"], True)  # the job is made, its schedule isn't
    svc.platform_save_source({"name": "erp"})
    svc.platform_decide(req["id"], False, "Not now")
    with pytest.raises(core.PlatformError, match="has the Copy job CJ_Landing_erp"):
        svc.platform_delete_source("erp")


def test_when_fabric_cant_be_read_the_source_is_kept(svc, who, api, monkeypatch):
    svc.platform_decide(ask(svc, who)["id"], True)

    def down(workspace_id):
        raise RuntimeError("Fabric is unreachable")
    monkeypatch.setattr(api, "list_items", down)
    with pytest.raises(core.PlatformError, match="Couldn't check whether erp's Copy job CJ_Landing_erp is still"):
        svc.platform_delete_source("erp")
    assert any(s["name"] == "erp" for s in svc.platform_sources())


def test_someone_who_isnt_an_admin_is_refused_as_before_without_hearing_of_the_job(svc, who, api):
    svc.platform_decide(ask(svc, who)["id"], True)
    who["user"] = DEV
    with pytest.raises(Exception) as err:
        svc.platform_delete_source("erp")
    assert "Copy job" not in str(err.value) and "admin" in str(err.value).lower()


def test_once_the_landing_workspace_is_deleted_the_source_deletes(svc, who, api, monkeypatch):
    from fabric_desktop.fabric_api import FabricError
    svc.platform_decide(ask(svc, who)["id"], True)

    def gone(workspace_id):
        raise FabricError("The requested workspace was not found", code="WorkspaceNotFound", status=404)
    monkeypatch.setattr(api, "list_items", gone)
    monkeypatch.setattr(type(svc), "_fabric_admin", lambda self, write=False: AdminApi({"state": "Deleted"}))
    assert all(s["name"] != "erp" for s in svc.platform_delete_source("erp"))


class AdminApi:
    def __init__(self, answer):
        self.answer = answer

    def admin_get_workspace(self, workspace_id):
        if isinstance(self.answer, Exception):
            raise self.answer
        return dict(self.answer, id=workspace_id)


@pytest.mark.parametrize("admin", [
    {"state": "Active"},  # there, only not for this admin: a non-member's 404
    "forbidden",          # not a Fabric admin, so it can't be confirmed
])
def test_a_not_found_the_admin_api_doesnt_confirm_keeps_the_source(svc, who, api, monkeypatch, admin):
    from fabric_desktop.fabric_api import FabricError
    svc.platform_decide(ask(svc, who)["id"], True)

    def not_found(workspace_id):
        raise FabricError("The requested workspace was not found", code="WorkspaceNotFound", status=404)
    monkeypatch.setattr(api, "list_items", not_found)
    answer = FabricError("Forbidden", code="InsufficientPrivileges", status=403) if admin == "forbidden" else admin
    monkeypatch.setattr(type(svc), "_fabric_admin", lambda self, write=False: AdminApi(answer))
    with pytest.raises(core.PlatformError, match="Couldn't check whether erp's Copy job"):
        svc.platform_delete_source("erp")
    assert any(s["name"] == "erp" for s in svc.platform_sources())


def test_the_admin_api_not_finding_the_workspace_either_lets_the_source_go(svc, who, api, monkeypatch):
    from fabric_desktop.fabric_api import FabricError
    svc.platform_decide(ask(svc, who)["id"], True)

    def not_found(workspace_id):
        raise FabricError("not found", code="WorkspaceNotFound", status=404)
    monkeypatch.setattr(api, "list_items", not_found)
    monkeypatch.setattr(type(svc), "_fabric_admin",
                        lambda self, write=False: AdminApi(FabricError("not found", code="WorkspaceNotFound", status=404)))
    assert all(s["name"] != "erp" for s in svc.platform_delete_source("erp"))


def test_no_access_to_the_landing_workspace_keeps_the_source(svc, who, api, monkeypatch):
    from fabric_desktop.fabric_api import FabricError
    svc.platform_decide(ask(svc, who)["id"], True)

    def denied(workspace_id):
        raise FabricError("Forbidden", code="InsufficientPrivileges", status=403)
    monkeypatch.setattr(api, "list_items", denied)
    with pytest.raises(core.PlatformError, match="Couldn't check whether erp's Copy job"):
        svc.platform_delete_source("erp")
    assert any(s["name"] == "erp" for s in svc.platform_sources())


def _corrupt(svc, field, value):
    req = svc.platform_requests("approved")[0]
    detail = dict(req["detail"], made=dict(req["detail"]["made"], **{field: value}))
    with svc._catalog()._db() as db:
        db.run(f"UPDATE {db.t('catalog', 'request')} SET detail = ? WHERE id = ?", (json.dumps(detail), req["id"]))
        db.conn.commit()


def test_a_real_copy_job_in_a_workspace_that_cant_be_read_keeps_the_source(svc, who, api, monkeypatch):
    svc.platform_decide(ask(svc, who)["id"], True)
    _corrupt(svc, "workspace_id", "../admin/workspaces")
    called = []
    monkeypatch.setattr(api, "list_items", lambda workspace_id: called.append(workspace_id) or [])
    with pytest.raises(core.PlatformError, match=f"recorded the Copy job {CJ1} in a landing workspace KratosForge"):
        svc.platform_delete_source("erp")
    assert called == [] and any(s["name"] == "erp" for s in svc.platform_sources())


@pytest.mark.parametrize("field, value", [("copy_job_id", "cj-1?x=1")])
def test_a_recorded_id_that_isnt_fabrics_never_reaches_fabric_and_doesnt_hold_the_source(svc, who, api, monkeypatch,
                                                                                          field, value):
    svc.platform_decide(ask(svc, who)["id"], True)
    _corrupt(svc, field, value)
    called = []
    monkeypatch.setattr(api, "list_items", lambda workspace_id: called.append(workspace_id) or [])
    assert all(s["name"] != "erp" for s in svc.platform_delete_source("erp"))
    assert called == []
