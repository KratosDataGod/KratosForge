"""central/requests_service.py paths that its first mutation run (batch 381,
docs/quality/mutation-testing.md) showed no test checked. Fabric is a fake; no tenant is called."""

from __future__ import annotations

import pytest

from fabric_desktop.central import core, requests_service
from fabric_desktop.service import Service
from tests.test_requests_core_four import (ADMIN, CAP, DEV, GROUP, WS, FabricDomains, _same_day, as_user,  # noqa: F401
                                           ask_workspace, svc, who)

LONG = "Regional reporting for the sales teams. " * 200


class Described(FabricDomains):
    def __init__(self, me):
        super().__init__(me)
        self.descriptions = []

    def create_admin_domain(self, name, description="", parent_id=""):
        self.descriptions.append(description)
        return super().create_admin_domain(name, description, parent_id)


@pytest.fixture
def domains(monkeypatch, who):  # noqa: F811
    fake = Described(lambda: who["user"])
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fake)
    return fake


def test_the_longest_descriptions_fabric_takes():
    assert (requests_service.WORKSPACE_DESCRIPTION, requests_service.DOMAIN_DESCRIPTION,
            requests_service.ITEM_DESCRIPTION) == (4000, 256, 256)


def test_a_long_domain_description_is_cut_to_what_fabric_takes_with_the_marker_kept(svc, who, domains):  # noqa: F811
    as_user(who, DEV)
    req = svc.platform_request("domain", "EMEA", LONG, {"name": "EMEA", "parent_id": "sales"})
    as_user(who, ADMIN)
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    [description] = domains.descriptions
    assert len(description) == 256 and description.endswith(f"(KratosForge request {req['id']})")


def test_a_long_workspace_reason_is_cut_to_what_fabric_takes_with_the_marker_kept(svc, who):  # noqa: F811
    as_user(who, DEV)
    req = svc.platform_request("workspace", "Sales Forecasting - Dev", LONG,
                               {"name": "Sales Forecasting - Dev", "capacity_id": CAP, "admins": [GROUP]})
    as_user(who, ADMIN)
    svc.platform_decide(req["id"], True)
    description = svc.api.workspaces[0]["description"]
    assert len(description) == 4000 and description.endswith(f"(KratosForge request {req['id']})")


def test_a_waiting_access_request_says_the_role_its_group_has_today(svc, who, monkeypatch):  # noqa: F811
    svc.api.roles[WS] = [{"id": "g1", "role": "Viewer", "principal": {"id": GROUP, "type": "Group"}}]
    as_user(who, DEV)
    group = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Member", "who": "group",
                                                              "group_id": GROUP})
    mine = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Contributor"})
    as_user(who, ADMIN)
    today = {r["id"]: r["today"] for r in svc.platform_requests("waiting")}
    assert today == {group["id"]: {"role": "Viewer"}, mine["id"]: {"role": ""}}

    def down(workspace_id):
        raise RuntimeError("Fabric is unreachable")

    monkeypatch.setattr(svc.api, "list_role_assignments", down)
    assert {r["id"]: r["today"] for r in svc.platform_requests("waiting")} == {
        group["id"]: {"checked": False}, mine["id"]: {"checked": False}}


def test_a_new_workspace_is_made_on_its_capacity_and_never_moved(svc, who, monkeypatch):  # noqa: F811
    real = svc.api.create_workspace
    # Fabric's answer may leave capacityId out: a workspace this approval made is on its capacity anyway.
    monkeypatch.setattr(svc.api, "create_workspace", lambda *a, **k: {k2: v for k2, v in real(*a, **k).items() if k2 != "capacityId"})
    as_user(who, DEV)
    req = ask_workspace(svc)
    as_user(who, ADMIN)
    svc.platform_decide(req["id"], True)
    assert [c[0] for c in svc.api.calls].count("assign_to_capacity") == 0


def test_a_retry_knows_its_own_domain_by_the_name_asked_for_not_its_id(svc, who, monkeypatch):  # noqa: F811
    from tests.test_request_retries import FabricDomains as Fabric, fail_platform_once

    fabric = Fabric()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fabric)
    as_user(who, DEV)
    req = svc.platform_request("domain", "Human Resources", "", {"name": "Human Resources"})
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "record_request_progress", "before")
    with pytest.raises(requests_service.StepError):
        svc.platform_decide(req["id"], True)
    out = svc.platform_decide(req["id"], True)
    assert out["detail"]["made"] == {"domain_id": "human-resources", "fabric_domain_id": "f-human resources"}


def test_approving_access_someone_already_has_still_says_it_is_assigned(svc, who):  # noqa: F811
    svc.api.roles[WS] = [{"id": "g1", "role": "Admin", "principal": {"id": GROUP, "type": "Group"}}]
    as_user(who, DEV)
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Member", "who": "group",
                                                            "group_id": GROUP})
    as_user(who, ADMIN)
    assert svc.platform_decide(req["id"], True)["made"] == {"assigned": True, "kept": "Admin"}


def test_a_not_found_from_another_operation_is_passed_through():
    class Missing:
        def call(self, op, **kwargs):
            raise core.PlatformError("Workspace not found")

    with pytest.raises(core.PlatformError, match="Workspace not found"):
        requests_service.call_or_repair(Missing(), "record_request_progress", "carry out this request")


def test_a_shortcut_request_that_made_nothing_leaves_nothing_to_say():
    assert requests_service.left_in_fabric({"kind": "shortcut", "detail": {"name": "supplier", "made": {}}}) == []


def test_a_shortcut_this_request_made_says_to_remove_it_if_unwanted():
    made = {"shortcut_id": "s1"}
    [line] = requests_service.left_in_fabric({"kind": "shortcut", "detail": {"name": "supplier", "lakehouse_name": "LH_Bronze",
                                                                              "made": made}})
    assert line == ("The shortcut supplier in LH_Bronze, made by this request, is still in OneLake: remove it on "
                    "OneLake > Shortcuts if it isn't wanted")


def test_only_waiting_access_requests_say_what_role_is_there_today(svc, who):  # noqa: F811
    as_user(who, DEV)
    ws = ask_workspace(svc)
    access = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Contributor"})
    as_user(who, ADMIN)
    svc.platform_decide(access["id"], True)
    rows = {r["id"]: r for r in svc.platform_requests()}
    assert "today" not in rows[ws["id"]] and "today" not in rows[access["id"]]


def test_approving_access_someone_did_not_have_says_it_is_assigned(svc, who):  # noqa: F811
    as_user(who, DEV)
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Contributor"})
    as_user(who, ADMIN)
    out = svc.platform_decide(req["id"], True)
    assert out["made"] == {"assigned": True} and "next" not in out


def test_a_workspace_name_already_taken_is_refused_by_the_name_asked_for(svc, who):  # noqa: F811
    svc.api.workspaces.append({"id": "theirs", "displayName": "Sales Forecasting - Dev"})
    as_user(who, DEV)
    with pytest.raises(core.PlatformError, match="A workspace named Sales Forecasting - Dev already exists"):
        svc.platform_request("workspace", "forecasting", "", {"name": "Sales Forecasting - Dev", "capacity_id": CAP,
                                                              "admins": [GROUP]})


def test_a_failed_access_step_names_the_workspace_by_its_name(svc, who, monkeypatch):  # noqa: F811
    as_user(who, DEV)
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "workspace_name": "Sales Prod",
                                                            "role": "Contributor"})
    as_user(who, ADMIN)
    svc.api.fail["add_by_email"] = 1
    with pytest.raises(requests_service.StepError, match=r"the Contributor role in Sales Prod failed"):
        svc.platform_decide(req["id"], True)


def test_a_marker_that_fills_the_limit_leaves_no_typed_text():
    mark = "(KratosForge request r-1)"
    assert requests_service.marked("Forecast models", mark, len(mark)) == mark
    assert requests_service.marked("Forecast models", mark, len(mark) + 1) == mark  # no room for a letter and a space
    assert requests_service.marked("Forecast models", mark, len(mark) + 2) == f"F {mark}"


def test_a_platform_that_has_no_such_operation_says_to_run_finish_or_repair():
    class Older:
        def call(self, op, **kwargs):
            raise core.PlatformError(f"Operation {op} not found")

    with pytest.raises(LookupError, match="can't carry out this request yet: UDF_Platform is older than this app"):
        requests_service.call_or_repair(Older(), "record_request_progress", "carry out this request")


def _workspace_half_made(svc, who):  # noqa: F811
    as_user(who, DEV)
    req = ask_workspace(svc, admins=[{"id": GROUP, "name": "Sales Engineers"}, "Lee@Contoso.com"])
    as_user(who, ADMIN)
    svc.api.fail["add_role_assignment"] = 1
    with pytest.raises(requests_service.StepError, match="Make Sales Engineers an admin failed"):
        svc.platform_decide(req["id"], True)
    return req


def test_a_retry_that_finds_the_workspace_on_its_capacity_leaves_it_there(svc, who):  # noqa: F811
    req = _workspace_half_made(svc, who)
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    assert [c[0] for c in svc.api.calls].count("assign_to_capacity") == 0


def test_a_failed_move_back_names_the_capacity(svc, who):  # noqa: F811
    req = _workspace_half_made(svc, who)
    svc.api.workspaces[0]["capacityId"] = "moved"
    svc.api.fail["assign_to_capacity"] = 1
    with pytest.raises(requests_service.StepError, match="Put it on Shared F64 failed"):
        svc.platform_decide(req["id"], True)


def test_a_retry_keeps_the_role_the_first_approval_raised_it_from(svc, who, monkeypatch):  # noqa: F811
    from tests.test_request_retries import fail_platform_once

    svc.api.roles[WS] = [{"id": "r1", "role": "Viewer", "principal": {"id": DEV, "type": "User",
                                                                       "userDetails": {"userPrincipalName": DEV}}}]
    as_user(who, DEV)
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Contributor"})
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "decide_request", "before")
    with pytest.raises(Exception, match="decide_request"):
        svc.platform_decide(req["id"], True)
    out = svc.platform_decide(req["id"], True)
    assert out["detail"]["made"] == {"assigned": True, "change": "raised", "from": "Viewer"}


def test_declining_a_domain_request_says_nothing_of_other_domains(svc, who):  # noqa: F811
    as_user(who, DEV)
    req = svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    out = svc.platform_decide(req["id"], False)
    assert out["status"] == "declined" and "next" not in out


def test_a_retry_knows_its_own_subdomain_under_its_fabric_parent(svc, who, monkeypatch):  # noqa: F811
    from tests.test_request_retries import FabricDomains as Fabric, fail_platform_once

    fabric = Fabric()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fabric)
    as_user(who, DEV)
    req = svc.platform_request("domain", "EMEA", "", {"name": "EMEA", "parent_id": "sales"})
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "record_request_progress", "before")
    with pytest.raises(requests_service.StepError):
        svc.platform_decide(req["id"], True)
    assert fabric.created == [("EMEA", "f-sales")]
    out = svc.platform_decide(req["id"], True)
    assert out["detail"]["made"] == {"domain_id": "emea", "fabric_domain_id": "f-emea"}
