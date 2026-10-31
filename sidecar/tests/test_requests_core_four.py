"""Governance plan step 10, requests: a new workspace, access to a workspace, a domain, and an
exception to a governance policy. Checked when sent, made as the approver when approved, with
fakes for Fabric (no tenant is called)."""

import datetime as dt

import pytest

from fabric_desktop import governance_notices as notices
from fabric_desktop.central import core, policy, requests_service
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service
from fabric_desktop.workloads.platform_notify import validate

ADMIN, DEV = "ana@contoso.com", "dev@contoso.com"
ROUTE = "https://contoso.webhook.office.com/hook"
CAP = "11111111-2222-3333-4444-555555555555"
GROUP = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
WS = "99999999-8888-7777-6666-555555555555"
TODAY = dt.datetime.now(dt.timezone.utc).date()  # the platform's date is UTC's (UDF_Platform runs in UTC)


@pytest.fixture(autouse=True)
def _same_day(monkeypatch):
    """The platform's clock stays on TODAY, so a run that crosses midnight UTC can't put setup and checks on
    different days. policy.py imports _today by name, so it is pinned there too."""
    for module in (core, policy):
        monkeypatch.setattr(module, "_today", lambda: TODAY)
NO_PII = {"id": "no-pii-in-dev", "name": "No PII in Dev", "category": "classification", "severity": "high",
          "enforcement": "block", "rule": "no_pii_dev"}


class FakeFabric:
    """Workspaces and their roles in memory. Records who did what, so a test can say it was the approver."""

    def __init__(self, me):
        self.me, self.workspaces, self.roles, self.calls = me, [], {}, []
        self.fail = {}

    def _maybe_fail(self, op):
        if self.fail.get(op):
            self.fail[op] -= 1
            raise RuntimeError(f"Fabric said no to {op}")

    def list_workspaces(self):
        return [dict(w) for w in self.workspaces]

    def create_workspace(self, name, capacity_id="", description=""):
        self._maybe_fail("create_workspace")
        ws = {"id": f"ws-{len(self.workspaces) + 1}", "displayName": name, "capacityId": capacity_id, "description": description}
        self.workspaces.append(ws)
        self.roles[ws["id"]] = [{"id": "r0", "role": "Admin", "principal": {"id": "me", "type": "User",
                                                                              "userDetails": {"userPrincipalName": self.me()}}}]
        self.calls.append(("create_workspace", self.me(), name, capacity_id))
        return dict(ws)

    def assign_to_capacity(self, workspace_id, capacity_id):
        self._maybe_fail("assign_to_capacity")
        next(w for w in self.workspaces if w["id"] == workspace_id)["capacityId"] = capacity_id
        self.calls.append(("assign_to_capacity", self.me(), workspace_id, capacity_id))

    def list_role_assignments(self, workspace_id):
        return [dict(r) for r in self.roles.get(workspace_id, [])]

    def add_role_assignment(self, workspace_id, principal_id, principal_type, role):
        self._maybe_fail("add_role_assignment")
        self.roles.setdefault(workspace_id, []).append(
            {"id": f"r{len(self.roles.get(workspace_id, [])) + 1}", "role": role,
             "principal": {"id": principal_id, "type": principal_type}})
        self.calls.append(("add_role_assignment", self.me(), workspace_id, principal_id, role))
        return {}

    def update_role_assignment(self, workspace_id, assignment_id, role):
        next(r for r in self.roles[workspace_id] if r["id"] == assignment_id)["role"] = role
        self.calls.append(("update_role_assignment", self.me(), workspace_id, assignment_id, role))
        return {}

    def add_by_email(self, workspace_id, email, role):
        self._maybe_fail("add_by_email")
        self.roles.setdefault(workspace_id, []).append(
            {"id": f"e{len(self.roles.get(workspace_id, [])) + 1}", "role": role,
             "principal": {"id": email, "type": "User", "userDetails": {"userPrincipalName": email}}})
        self.calls.append(("add_by_email", self.me(), workspace_id, email, role))


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
def svc(tmp_path, monkeypatch, who):
    s = Service(root=tmp_path / "app")
    s._auth = Auth(who)
    api = FakeFabric(lambda: who["user"])
    s.api = api
    monkeypatch.setattr(type(s), "_rq_fabric", lambda self: api)
    monkeypatch.setattr(type(s), "_add_user_by_email", lambda self, ws, email, role: api.add_by_email(ws, email, role))
    s.platform_whoami()  # the first signed-in person is the PC platform's admin
    s.platform_save_domain({"name": "Sales", "fabric_domain": "f-sales"})
    return s


def as_user(who, user):
    who["user"] = user


def ask_workspace(svc, **extra):
    detail = {"name": "Sales Forecasting - Dev", "capacity_id": CAP, "capacity_name": "Shared F64", "domain_id": "sales",
              "admins": [GROUP, "Lee@Contoso.com"]} | extra
    return svc.platform_request("workspace", detail["name"], "Forecast models for the Q1 plan", detail)


# --------------------------------------------------------------------------- checked when sent


def test_a_workspace_request_is_checked_when_it_is_sent(svc, who):
    as_user(who, DEV)
    for detail, message in [({"name": "Sales/Dev"}, "isn't a workspace name"), ({"name": "x" * 257}, "isn't a workspace name"),
                            ({"admins": []}, "Say who its admins are"), ({"admins": ["lee"]}, "isn't a person's email"),
                            ({"domain_id": "nope"}, "isn't one of the platform's domains"),
                            ({"capacity_id": "F64"}, "Pick the capacity")]:
        with pytest.raises(core.PlatformError, match=message):
            ask_workspace(svc, **detail)
    with pytest.raises(core.PlatformError, match="what the workspace is for"):
        svc.platform_request("workspace", "Sales", "", {"name": "Sales", "capacity_id": CAP, "admins": [GROUP]})
    out = ask_workspace(svc)
    assert out["status"] == "waiting" and out["target"] == "Sales Forecasting - Dev"
    assert out["detail"]["admins"] == [{"type": "Group", "id": GROUP, "email": "", "name": GROUP},
                                       {"type": "User", "id": "", "email": "lee@contoso.com", "name": "lee@contoso.com"}]
    assert out["detail"]["domain_name"] == "Sales" and out["detail"]["fabric_domain"] == "f-sales"
    with pytest.raises(core.PlatformError, match="already a request waiting"):
        ask_workspace(svc, name="sales forecasting - dev")
    svc.api.workspaces.append({"id": "taken", "displayName": "Taken"})
    with pytest.raises(core.PlatformError, match="already exists"):
        ask_workspace(svc, name="Taken")


def test_workspace_access_domain_and_exception_requests_are_checked_when_sent(svc, who):
    as_user(who, DEV)
    ask = {"workspace_id": WS, "workspace_name": "Sales Prod", "role": "Contributor", "who": "me"}
    with pytest.raises(core.PlatformError, match="role must be one of"):
        svc.platform_request("workspace_access", "", "", ask | {"role": "Owner"})
    with pytest.raises(core.PlatformError, match="Pick the workspace"):
        svc.platform_request("workspace_access", "", "", ask | {"workspace_id": "Sales Prod"})
    with pytest.raises(core.PlatformError, match="object ID"):
        svc.platform_request("workspace_access", "", "", ask | {"who": "group", "group_id": "sales@contoso.com"})
    mine = svc.platform_request("workspace_access", "", "Reports", ask)
    assert mine["target"] == "Sales Prod" and mine["detail"]["principal"]["email"] == DEV
    with pytest.raises(core.PlatformError, match="already a request waiting"):
        svc.platform_request("workspace_access", "", "", ask | {"role": "Viewer"})
    group = svc.platform_request("workspace_access", "", "", ask | {"who": "group", "group_id": GROUP, "group_name": "Sales Engineers"})
    assert group["detail"]["principal"] == {"type": "Group", "id": GROUP, "email": "", "name": "Sales Engineers"}
    as_user(who, "")
    with pytest.raises(core.PlatformError, match="Sign in"):
        svc._catalog().call("request", kind="workspace_access", target="", detail=ask | {"workspace_id": GROUP})
    as_user(who, DEV)

    with pytest.raises(core.PlatformError, match="already a domain"):
        svc.platform_request("domain", "Sales", "", {"name": "Sales"})
    with pytest.raises(core.PlatformError, match="doesn't exist"):
        svc.platform_request("domain", "EMEA", "", {"name": "EMEA", "parent_id": "europe"})
    sub = svc.platform_request("domain", "EMEA", "Regional teams", {"name": "EMEA", "parent_id": "sales", "owners": "ana@contoso.com"})
    assert sub["target"] == "emea" and sub["detail"]["parent_id"] == "sales"

    as_user(who, ADMIN)
    svc.governance_create(NO_PII)
    svc.governance_publish("no-pii-in-dev")
    svc.governance_create(NO_PII | {"id": "strict", "name": "Strict", "exception_eligible": False})
    svc.governance_publish("strict")
    as_user(who, DEV)
    later = (TODAY + dt.timedelta(days=30)).isoformat()
    good = {"policy": "no-pii-in-dev", "scope": "project", "scope_target": "hr-analytics", "until": later}
    for detail, reason, message in [(good | {"policy": "nope"}, "x", "no policy nope"),
                                    (good | {"policy": "strict"}, "x", "doesn't allow exceptions"),
                                    (good | {"scope": "entity"}, "x", "scope must be one of"),
                                    (good | {"scope_target": ""}, "x", "Say which project"),
                                    (good | {"scope": "domain", "scope_target": "hr"}, "x", "isn't one of the platform's domains"),
                                    (good | {"until": TODAY.isoformat()}, "x", "after today"),
                                    (good | {"until": (TODAY + dt.timedelta(days=400)).isoformat()}, "x", "a year at most"),
                                    (good | {"until": "next month"}, "x", "a date like"),
                                    (good, "", "Say why")]:
        with pytest.raises(core.PlatformError, match=message):
            svc.platform_request("exception", "", reason, detail)
    out = svc.platform_request("exception", "", "A masked copy isn't ready yet", good)
    assert out["target"] == "no-pii-in-dev" and out["detail"]["policy_name"] == "No PII in Dev"
    with pytest.raises(core.PlatformError, match="already a request waiting"):
        svc.platform_request("exception", "", "again", good)
    assert svc.platform_request("exception", "", "Another project", good | {"scope_target": "payroll"})["status"] == "waiting"


# --------------------------------------------------------------------------- approving makes it, as the approver


def test_approving_a_workspace_creates_it_as_the_approver_then_approves(svc, who):
    as_user(who, DEV)
    req = ask_workspace(svc)
    as_user(who, ADMIN)
    out = svc.platform_decide(req["id"], True, "ok")
    assert out["status"] == "approved" and out["decided_by"] == ADMIN
    ws = svc.api.workspaces[0]
    assert ws["displayName"] == "Sales Forecasting - Dev" and ws["capacityId"] == CAP and req["id"] in ws["description"]
    assert ("create_workspace", ADMIN, "Sales Forecasting - Dev", CAP) in svc.api.calls
    assert ("add_role_assignment", ADMIN, ws["id"], GROUP, "Admin") in svc.api.calls
    assert ("add_by_email", ADMIN, ws["id"], "lee@contoso.com", "Admin") in svc.api.calls
    assert out["next"] == ["Put it in the Sales domain in Fabric's admin portal"]
    assert out["detail"]["made"]["workspace_id"] == ws["id"]


def test_a_failed_step_keeps_the_request_waiting_names_the_step_and_a_retry_does_not_duplicate(svc, who):
    as_user(who, DEV)
    req = ask_workspace(svc)
    as_user(who, ADMIN)
    svc.api.fail["add_by_email"] = 1
    with pytest.raises(requests_service.StepError, match="Make lee@contoso.com an admin failed, so the request is still waiting"):
        svc.platform_decide(req["id"], True)
    waiting = svc.platform_requests("waiting")[0]
    assert waiting["id"] == req["id"] and waiting["detail"]["made"]["workspace_id"] == "ws-1"
    assert waiting["detail"]["made"]["admins"] == [GROUP]
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved"
    assert [c[0] for c in svc.api.calls].count("create_workspace") == 1  # found again, not made twice
    assert [c[0] for c in svc.api.calls].count("add_role_assignment") == 1


def test_a_retry_finds_the_workspace_by_name_and_puts_it_back_on_its_capacity(svc, who):
    as_user(who, DEV)
    req = ask_workspace(svc)
    as_user(who, ADMIN)
    svc.api.fail["add_role_assignment"] = 1
    with pytest.raises(requests_service.StepError, match="Make .* an admin failed"):
        svc.platform_decide(req["id"], True)
    svc.api.workspaces[0]["capacityId"] = "moved"
    svc.platform_decide(req["id"], True)
    assert svc.api.workspaces[0]["capacityId"] == CAP and len(svc.api.workspaces) == 1


def test_a_workspace_of_the_same_name_the_request_did_not_make_is_never_taken_over(svc, who):
    as_user(who, DEV)
    req = ask_workspace(svc)
    svc.api.workspaces.append({"id": "theirs", "displayName": "Sales Forecasting - Dev", "description": "someone else's"})
    as_user(who, ADMIN)
    with pytest.raises(requests_service.StepError, match="Create the workspace Sales Forecasting - Dev failed.*didn't make it"):
        svc.platform_decide(req["id"], True)
    assert svc.platform_requests("waiting")[0]["id"] == req["id"] and not svc.api.roles.get("theirs")


def test_a_failed_create_names_the_step_and_makes_nothing(svc, who):
    as_user(who, DEV)
    req = ask_workspace(svc)
    as_user(who, ADMIN)
    svc.api.fail["create_workspace"] = 1
    with pytest.raises(requests_service.StepError, match="Create the workspace"):
        svc.platform_decide(req["id"], True)
    assert svc.platform_requests("waiting")[0]["id"] == req["id"] and svc.api.workspaces == []


def test_a_workspace_request_without_a_capacity_takes_the_approvers(svc, who):
    as_user(who, DEV)
    req = ask_workspace(svc, capacity_id="", domain_id="")
    as_user(who, ADMIN)
    with pytest.raises(core.PlatformError, match="Pick a capacity"):
        svc.platform_decide(req["id"], True)
    out = svc.platform_decide(req["id"], True, capacity_id=CAP)
    assert out["status"] == "approved" and svc.api.workspaces[0]["capacityId"] == CAP and "next" not in out


def test_approving_workspace_access_adds_the_role_as_the_approver(svc, who):
    as_user(who, DEV)
    svc.api.roles[WS] = [{"id": "g1", "role": "Viewer", "principal": {"id": GROUP, "type": "Group"}}]
    mine = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "workspace_name": "Sales Prod", "role": "Contributor"})
    group = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Member", "who": "group", "group_id": GROUP})
    as_user(who, ADMIN)
    svc.platform_decide(mine["id"], True)
    svc.platform_decide(group["id"], True)
    assert ("add_by_email", ADMIN, WS, DEV, "Contributor") in svc.api.calls
    assert ("update_role_assignment", ADMIN, WS, "g1", "Member") in svc.api.calls
    assert {r["status"] for r in svc.platform_requests()} >= {"approved"}


@pytest.mark.parametrize("kind", ["user", "group"])
@pytest.mark.parametrize("have, asked, after, note", [
    ("Admin", "Viewer", "Admin", "already had Admin, so it was kept"),
    ("Member", "Contributor", "Member", "already had Member, so it was kept"),
    ("Contributor", "Contributor", "Contributor", "has Contributor there"),
    ("Viewer", "Member", "Member", None),
], ids=["admin kept", "member kept", "same role", "lower raised"])
def test_approving_workspace_access_never_lowers_a_role(svc, who, kind, have, asked, after, note):
    """A request asks for access, so approving one never takes any away: a higher role is kept."""
    principal = {"id": GROUP, "type": "Group"} if kind == "group" else {
        "id": DEV, "type": "User", "userDetails": {"userPrincipalName": DEV}}
    svc.api.roles[WS] = [{"id": "r1", "role": have, "principal": principal}]
    as_user(who, DEV)
    ask = {"workspace_id": WS, "role": asked} | (
        {"who": "group", "group_id": GROUP, "group_name": "Sales Engineers"} if kind == "group" else {})
    req = svc.platform_request("workspace_access", "", "", ask)
    as_user(who, ADMIN)
    out = svc.platform_decide(req["id"], True)
    assert [r["role"] for r in svc.api.roles[WS]] == [after]
    assert out["status"] == "approved"
    changed = [c for c in svc.api.calls if c[0] in ("update_role_assignment", "add_role_assignment", "add_by_email")]
    if note is None:
        assert changed == [("update_role_assignment", ADMIN, WS, "r1", asked)]
        assert "next" not in out and "kept" not in out["made"]
    else:
        assert changed == []
        whom = "the group Sales Engineers" if kind == "group" else DEV
        assert out["next"] == [f"{whom} {note}"] and out["made"]["kept"] == have
    change = "raised" if note is None else "same" if have == asked else "kept"
    assert out["detail"]["made"] == {"assigned": True, "change": change, "from": have}  # recorded for the audit trail (#1207)


class FabricDomains:
    """Fabric's admin domains API: refuses with ``status`` when set, as Fabric would."""

    def __init__(self, me):
        self.me, self.status, self.created = me, None, []

    def create_admin_domain(self, name, description="", parent_id=""):
        if self.status:
            raise FabricError(f"POST /admin/domains failed with {self.status}", status=self.status)
        self.created.append((self.me(), name, parent_id))
        return {"id": f"f-{name.lower()}", "displayName": name}

    def list_admin_domains(self):
        # what a 409 meets: a domain of that name someone made by hand, with no request's marker
        return [{"id": "f-hr", "displayName": "HR", "description": "", "parentDomainId": ""}]


@pytest.fixture
def fabric_domains(monkeypatch, who):
    fake = FabricDomains(lambda: who["user"])
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fake)
    return fake


def test_approving_a_domain_creates_it_in_fabric_as_the_approver(svc, who, fabric_domains):
    as_user(who, DEV)
    req = svc.platform_request("domain", "EMEA", "Regional", {"name": "EMEA", "parent_id": "sales", "owners": "omar@contoso.com"})
    top = svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and "next" not in out
    svc.platform_decide(top["id"], True)
    got = {d["id"]: d for d in svc.platform_domains()}
    emea = got["emea"]
    assert emea["parent_id"] == "sales" and emea["owners"] == "omar@contoso.com" and emea["fabric_domain"] == "f-emea"
    assert emea["updated_by"] == ADMIN and got["hr"]["fabric_domain"] == "f-hr"
    assert fabric_domains.created == [(ADMIN, "EMEA", "f-sales"), (ADMIN, "HR", "")]


def test_a_subdomain_of_a_platform_only_domain_is_saved_in_the_platform_only(svc, who, fabric_domains):
    svc.platform_save_domain({"name": "Ops"})
    as_user(who, DEV)
    req = svc.platform_request("domain", "Field", "", {"name": "Field", "parent_id": "ops"})
    as_user(who, ADMIN)
    out = svc.platform_decide(req["id"], True)
    field = next(d for d in svc.platform_domains() if d["id"] == "field")
    assert out["status"] == "approved" and not field.get("fabric_domain") and fabric_domains.created == []
    assert "Ops isn't in Fabric" in out["next"][0]


def test_fabric_refusing_the_approver_leaves_the_domain_request_waiting(svc, who, fabric_domains):
    as_user(who, DEV)
    req = svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    fabric_domains.status = 403
    with pytest.raises(requests_service.StepError) as err:
        svc.platform_decide(req["id"], True)
    text = str(err.value)
    assert text.startswith("Create the domain HR in Fabric failed, so the request is still waiting")
    assert "Only a Fabric administrator" in text and "A Fabric administrator can approve this request" in text
    assert "in the platform only" in text
    assert svc.platform_requests("waiting")[0]["id"] == req["id"] and "hr" not in {d["id"] for d in svc.platform_domains()}
    # Created in the platform only, then approved again: found, not made twice, and nothing asked of Fabric.
    svc.platform_save_domain({"name": "HR"})
    fabric_domains.status = None
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    assert fabric_domains.created == []


def test_a_name_fabric_already_has_says_to_import_it(svc, who, fabric_domains):
    as_user(who, DEV)
    req = svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    fabric_domains.status = 409
    with pytest.raises(requests_service.StepError, match="Fabric already has a domain called HR. Choose Import from Fabric"):
        svc.platform_decide(req["id"], True)
    assert [r["id"] for r in svc.platform_requests("waiting")] == [req["id"]]


def test_the_platform_refuses_an_approval_whose_thing_was_not_made_first(svc, who):
    as_user(who, DEV)
    ws = ask_workspace(svc)
    dom = svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    with pytest.raises(core.PlatformError, match="Create the workspace first"):
        svc._catalog().call("decide_request", id=ws["id"], approve=True)
    with pytest.raises(core.PlatformError, match="Save the domain hr first"):
        svc._catalog().call("decide_request", id=dom["id"], approve=True)
    assert svc.platform_decide(ws["id"], False, "Use Sales Dev")["status"] == "declined"  # declining needs nothing made


# --------------------------------------------------------------------------- who decides


def test_only_platform_admins_decide_the_new_kinds(svc, who):
    as_user(who, DEV)
    reqs = [ask_workspace(svc), svc.platform_request("domain", "HR", "", {"name": "HR"}),
            svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Viewer"})]
    assert not any(r["can_decide"] for r in svc.platform_requests("waiting"))
    for r in reqs:
        with pytest.raises((PermissionError, core.PlatformError), match="platform admin"):
            svc.platform_decide(r["id"], True)
    with pytest.raises(core.PlatformError, match="platform admin"):
        svc._catalog().call("record_request_progress", id=reqs[0]["id"], made={"workspace_id": "x"})
    assert svc.api.calls == []
    as_user(who, ADMIN)
    assert all(r["can_decide"] for r in svc.platform_requests("waiting"))


# --------------------------------------------------------------------------- exceptions


@pytest.fixture
def excepted(svc, who):
    svc.governance_create(NO_PII)
    svc.governance_publish("no-pii-in-dev")
    svc.governance_assign("no-pii-in-dev", "enterprise")
    svc._catalog().call("register_project", project={"name": "hr-analytics", "domain": "sales"})
    as_user(who, DEV)
    until = (TODAY + dt.timedelta(days=30)).isoformat()
    return svc.platform_request("exception", "", "A masked copy isn't ready yet",
                                {"policy": "no-pii-in-dev", "scope": "project", "scope_target": "hr-analytics", "until": until})


def test_an_exception_is_granted_only_by_a_platform_admin_and_audited(svc, who, excepted):
    with pytest.raises((PermissionError, core.PlatformError), match="platform admin"):
        svc.platform_decide(excepted["id"], True)
    assert svc.governance_overview()["exceptions"] == []
    as_user(who, ADMIN)
    svc.platform_decide(excepted["id"], True, "ok until the masked copy")
    [e] = svc.governance_overview()["exceptions"]
    assert e["policy_id"] == "no-pii-in-dev" and e["target"] == "hr-analytics" and e["approved_by"] == ADMIN
    assert e["requested_by"] == DEV and e["active"] and e["note"].startswith("Excepted until ")
    assert svc.governance_policy("no-pii-in-dev")["exceptions"][0]["id"] == e["id"]
    actions = [a["action"] for a in svc.platform_audit()]
    assert "policy exception granted" in actions and "approved exception" in actions
    assert svc.governance_policy("no-pii-in-dev")["history"][0]["action"] == "exception granted"


def test_an_exception_needs_admin_even_through_the_platform_directly(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    req = {"id": "r1", "reason": "x", "requested_by": DEV,
           "detail": {"policy": "p", "scope": "project", "scope_target": "crm", "until": "2999-01-01"}}
    p.call("add_admin", user=ADMIN)
    with p._db() as db, pytest.raises(core.PlatformError, match="Only platform admins"):
        policy._approve_exception(db, req, DEV)


def test_an_active_exception_turns_the_finding_into_a_note_and_ends_on_its_date(svc, who, excepted):
    as_user(who, ADMIN)
    before = svc.governance_resolve(project="hr-analytics")
    assert "exception" not in before["policies"][0]
    svc.platform_decide(excepted["id"], True)
    until = excepted["detail"]["until"]
    now = svc.governance_resolve(project="hr-analytics")["policies"][0]
    assert now["exception"]["note"] == f"Excepted until {until}: A masked copy isn't ready yet"
    assert "exception" not in svc.governance_resolve(project="other")["policies"][0]
    day_after = (dt.date.fromisoformat(until) + dt.timedelta(days=1)).isoformat()
    later = svc._catalog().call("resolve_policies", project="hr-analytics", today=day_after)["policies"][0]
    assert "exception" not in later  # ended on its date
    on_the_day = svc._catalog().call("resolve_policies", project="hr-analytics", today=until)["policies"][0]
    assert on_the_day["exception"]["until"] == until
    gone = svc._catalog().call("list_policy_exceptions", today=day_after)
    assert gone[0]["active"] is False and svc._catalog().call("list_policy_exceptions", active=True, today=day_after) == []


def test_a_workspace_or_domain_exception_covers_that_place_only():
    snap_exceptions = [{"id": "e1", "policy_id": "p", "scope": "workspace", "target": "Sales Dev", "until": "2999-01-01",
                        "reason": "r", "approved_by": ADMIN, "note": "Excepted until 2999-01-01: r"},
                       {"id": "e2", "policy_id": "q", "scope": "domain", "target": "sales", "until": "2999-01-01",
                        "reason": "r", "approved_by": ADMIN, "note": "n"}]
    chain = [("enterprise", "*"), ("domain", "sales"), ("project", "crm")]
    assert policy._excepted(snap_exceptions, "p", chain, {"workspace": "sales dev"}, "2026-01-01")["id"] == "e1"
    assert policy._excepted(snap_exceptions, "p", chain, {}, "2026-01-01") is None
    assert policy._excepted(snap_exceptions, "q", chain, {}, "2026-01-01")["id"] == "e2"
    assert policy._excepted(snap_exceptions, "q", [("enterprise", "*")], {}, "2026-01-01") is None


# --------------------------------------------------------------------------- notices


def test_the_new_kinds_have_their_own_notice_titles_and_no_email_in_them():
    base = {"id": "r1", "requested_by": DEV, "requested_at": "2026-10-21T10:00:00+00:00", "status": "waiting", "reason": "secret"}
    cases = [({"kind": "workspace", "target": "Sales Forecasting - Dev", "detail": {"domain_id": "sales"}}, "New workspace Sales Forecasting - Dev", "sales"),
             ({"kind": "workspace_access", "target": "Sales Prod", "detail": {"principal": {"email": DEV}}}, "Workspace access to Sales Prod", ""),
             ({"kind": "domain", "target": "emea", "detail": {"parent_id": "sales"}}, "New domain emea", "sales"),
             ({"kind": "exception", "target": "no-pii-in-dev", "detail": {"scope": "domain", "scope_target": "hr"}}, "Exception to no-pii-in-dev", "hr")]
    for extra, title, domain in cases:
        event = validate(notices.request_waiting(base | extra, []))
        assert event["title"] == title and event["domain"] == domain
        assert event["to"] == {"people": [], "roles": ["platform_admin"]}
        assert DEV not in event["title"] and "secret" not in str(event)
        decided = validate(notices.request_closed(base | extra | {"status": "approved"}, []))
        assert decided["title"] == f"{title} (approved)" and decided["to"]["people"] == [DEV]


# --------------------------------------------------------------------------- an older UDF_Platform


class OldPlatform:
    """A UDF_Platform from before this update: Fabric answers 404 for a function it doesn't have,
    and its request() doesn't know the new kinds."""

    def __init__(self, inner):
        self.inner = inner

    def call(self, op, **kwargs):
        if op in ("record_request_progress", "list_policy_exceptions"):
            raise LookupError("UDF_Platform wasn't found. Open Platform > Settings, then check the platform's workspace.")
        if op == "request" and kwargs.get("kind") in requests_service.NEW_KINDS:
            raise core.PlatformError("The platform couldn't request: kind must be one of key_table, source, access, shortcut")
        return self.inner.call(op, **kwargs)


def test_an_older_platform_says_to_run_finish_or_repair(svc, who, monkeypatch):
    as_user(who, DEV)
    req = ask_workspace(svc)
    inner = svc._catalog()
    monkeypatch.setattr(type(svc), "_catalog", lambda self: OldPlatform(inner))
    with pytest.raises(LookupError, match="Platform > Settings > Finish or repair once"):
        svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    with pytest.raises(requests_service.StepError, match="Finish or repair once"):
        svc.platform_decide(req["id"], True)
    out = svc.governance_overview()
    assert out["exceptions"] == [] and "Finish or repair once" in out["problems"][0]
    assert svc.platform_request("key_table", "customer", "", {})["status"] == "waiting"  # the old kinds still work


def test_call_or_repair_passes_other_errors_through():
    class Refuses:
        def call(self, op, **kwargs):
            raise core.PlatformError("Only platform admins can do that")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        requests_service.call_or_repair(Refuses(), "record_request_progress", "carry out this request")


# --------------------------------------------------------------------------- ending an exception early (PB-059 review #3)


def _granted(svc, who, excepted):
    as_user(who, ADMIN)
    svc.platform_decide(excepted["id"], True)
    [e] = svc.governance_overview()["exceptions"]
    return e


def test_only_a_platform_admin_ends_an_exception_early_with_a_reason_and_the_row_stays(svc, who, excepted):
    e = _granted(svc, who, excepted)
    until = excepted["detail"]["until"]
    as_user(who, DEV)
    with pytest.raises(core.PlatformError, match="Only platform admins can end policy exceptions early"):
        svc.governance_end_exception(e["id"], "The masked copy is ready")
    as_user(who, ADMIN)
    for reason, message in [("", "Say why"), ("   ", "Say why"), ("x" * (policy.EXCEPTION_END_REASON_MAX + 1), "500 characters")]:
        with pytest.raises(core.PlatformError, match=message):
            svc.governance_end_exception(e["id"], reason)
    assert "exception" in svc.governance_resolve(project="hr-analytics")["policies"][0]  # nothing changed yet

    out = svc.governance_end_exception(e["id"], "The masked copy is ready")
    assert out["active"] is False and out["ended_early"] and out["ended_by"] == ADMIN
    assert out["granted_until"] == until and out["until"] < TODAY.isoformat()
    # It stops applying now, and is no longer listed as in force...
    assert "exception" not in svc.governance_resolve(project="hr-analytics")["policies"][0]
    assert svc.governance_overview()["exceptions"] == [] and svc.governance_policy("no-pii-in-dev")["exceptions"] == []
    # ...but the row stays, and Include ended lists it with who ended it and why.
    [ended] = svc.governance_policy("no-pii-in-dev", include_ended=True)["exceptions"]
    assert ended["id"] == e["id"] and ended["end_reason"] == "The masked copy is ready" and ended["reason"] == e["reason"]
    assert ended["active"] is False and ended["ending"] is False and ended["approved_by"] == ADMIN
    actions = [a["action"] for a in svc.platform_audit()]
    assert "ended exception" in actions and "policy exception ended early" in actions
    history = svc.governance_policy("no-pii-in-dev")["history"][0]
    assert history["action"] == "exception ended early" and history["by_user"] == ADMIN
    with svc._catalog()._db() as db:
        assert len(db.rows(f"SELECT * FROM {db.t('catalog', 'policy_exception')}")) == 1  # never deleted

    with pytest.raises(core.PlatformError, match="already ended early"):
        svc.governance_end_exception(e["id"], "again")
    with pytest.raises(core.PlatformError, match="There's no policy exception 'nope'"):
        svc.governance_end_exception("nope", "typo")


def test_an_exception_that_ended_on_its_date_cannot_be_ended_again(svc, who, excepted):
    e = _granted(svc, who, excepted)
    with svc._catalog()._db() as db:
        db.run(f"UPDATE {db.t('catalog', 'policy_exception')} SET until_date = ? WHERE id = ?",
               ((TODAY - dt.timedelta(days=2)).isoformat(), e["id"]))
        db.commit()
    with pytest.raises(core.PlatformError, match="already ended on"):
        svc.governance_end_exception(e["id"], "Tidy up")


def test_ending_early_needs_admin_even_through_the_platform_directly(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    p.call("add_admin", user=ADMIN)
    with p._db() as db, pytest.raises(core.PlatformError, match="Only platform admins"):
        policy.end_policy_exception(db, "r1", "because", DEV)


def test_an_exception_table_from_before_gets_the_columns_that_record_an_early_end(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    added = ("ended_at", "ended_by", "end_reason", "granted_until", "warned_until")
    old = [c for c in policy._EXCEPTION_TABLE if c[0] not in added]
    with p._db() as db:
        db.run(f"DROP TABLE IF EXISTS {db.t('catalog', 'policy_exception')}")
        db.run(f"CREATE TABLE {db.t('catalog', 'policy_exception')} ({', '.join(core._column(db, n, k) for n, k in old)})")
        db.commit()
        assert sorted(policy.ensure_exception_schema(db)) == sorted(f"catalog.policy_exception.{c}" for c in added)
        assert policy.ensure_exception_schema(db) == []


def test_an_older_platform_says_to_run_finish_or_repair_to_end_an_exception(svc, who, monkeypatch):
    class Older(OldPlatform):
        def call(self, op, **kwargs):
            if op == "end_policy_exception":
                raise LookupError("UDF_Platform wasn't found. Open Platform > Settings, then check the platform's workspace.")
            return super().call(op, **kwargs)
    inner = svc._catalog()
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Older(inner))
    with pytest.raises(LookupError, match="can't end policy exceptions early yet.*Finish or repair once"):
        svc.governance_end_exception("r1", "because")


def test_the_function_app_ends_exceptions_as_the_caller():
    from fabric_desktop.central import udf
    app = udf.function_app()
    assert ("def end_policy_exception(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str, "
            "reason: str)") in app
    assert '_run(sqlDB, "end_policy_exception", ctx, id=id, reason=reason)' in app


# --------------------------------------------------------------------------- a warning before one ends (PB-059 review #2)


def test_an_exception_ending_within_14_days_warns_the_requester_and_the_project(svc, who, monkeypatch):
    svc.governance_create(NO_PII)
    svc.governance_publish("no-pii-in-dev")
    svc.governance_assign("no-pii-in-dev", "enterprise")
    as_user(who, DEV)
    soon = svc.platform_request("exception", "", "A masked copy isn't ready yet", {
        "policy": "no-pii-in-dev", "scope": "project", "scope_target": "hr-analytics",
        "until": (TODAY + dt.timedelta(days=10)).isoformat()})
    later = svc.platform_request("exception", "", "Payroll needs longer", {
        "policy": "no-pii-in-dev", "scope": "project", "scope_target": "payroll",
        "until": (TODAY + dt.timedelta(days=60)).isoformat()})
    as_user(who, ADMIN)
    svc.platform_decide(soon["id"], True)
    svc.platform_decide(later["id"], True)
    sent = []
    monkeypatch.setattr(type(svc), "_pn_raw", lambda self: {"routes": [{"name": "default", "url": ROUTE}]})
    monkeypatch.setattr(type(svc), "platform_notify", lambda self, events: sent.extend(events) or {})

    listed = {e["target"]: e for e in svc.governance_overview()["exceptions"]}
    assert listed["hr-analytics"]["ending"] and listed["hr-analytics"]["days_left"] == 10
    assert not listed["payroll"]["ending"]

    as_user(who, DEV)
    out = svc.governance_exceptions_ending()
    assert [e["target"] for e in out["ending"]] == ["hr-analytics"] and out["warn_days"] == 14
    assert out["ending"][0]["for_you"] is True  # DEV asked for it
    [event] = [validate(e) for e in sent]
    assert event["kind"] == "exception.ending" and event["severity"] == "warn"
    assert event["to"] == {"people": [DEV], "roles": ["project:hr-analytics"]}
    assert event["dedupe"] == f"exception.ending:{soon['id']}:{soon['detail']['until']}"
    assert event["open"] == {"view": "governance", "tab": "exceptions", "target": "no-pii-in-dev"}
    assert "masked copy" not in str(event) and "ends in 10 days" in event["title"]
    as_user(who, "someone@contoso.com")
    assert svc.governance_exceptions_ending()["ending"][0]["for_you"] is False

    as_user(who, ADMIN)
    svc.governance_end_exception(soon["id"], "Masked copy shipped early")
    sent.clear()
    assert svc.governance_exceptions_ending()["ending"] == [] and sent == []  # ended early: no warning


def test_an_ending_notice_goes_to_the_domain_owners_or_the_requester():
    domain = validate(notices.exception_ending({"id": "e1", "policy_id": "p", "scope": "domain", "target": "sales",
                                                "until": "2030-01-01", "requested_by": "", "reason": "secret"}, 1))
    assert domain["to"] == {"people": [], "roles": ["domain_owner:sales"]} and domain["domain"] == "sales"
    assert "tomorrow" in domain["title"] and "secret" not in str(domain)
    workspace = validate(notices.exception_ending({"id": "e2", "policy_id": "p", "scope": "workspace", "target": "Sales Dev",
                                                   "until": "2030-01-01", "requested_by": "not an email"}, 0))
    assert workspace["to"] == {"people": [], "roles": ["requester"]} and "today" in workspace["title"]
    assert notices.exception_ending({"id": "", "policy_id": "p", "until": "2030-01-01"}, 3) is None


def _ending_soon(svc, who):
    svc.governance_create(NO_PII)
    svc.governance_publish("no-pii-in-dev")
    as_user(who, DEV)
    soon = svc.platform_request("exception", "", "A masked copy isn't ready yet", {
        "policy": "no-pii-in-dev", "scope": "project", "scope_target": "hr-analytics",
        "until": (TODAY + dt.timedelta(days=5)).isoformat()})
    as_user(who, ADMIN)
    svc.platform_decide(soon["id"], True)
    return soon


def test_an_ending_notice_is_posted_once_across_pcs_that_share_the_platform(svc, who, tmp_path, monkeypatch):
    from fabric_desktop import app_settings
    from fabric_desktop import platform_notify_service as pns
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    posts = []
    monkeypatch.setattr(pns, "post_teams", lambda url, title, lines, **kw: posts.append(title))
    soon = _ending_soon(svc, who)
    # A second PC: its own app folder and outbox, the same platform.
    other = Service(root=tmp_path / "other-pc")
    other._auth = Auth(who)
    platform = svc._catalog()
    monkeypatch.setattr(svc, "_catalog", lambda: platform)
    monkeypatch.setattr(other, "_catalog", lambda: platform)
    for pc in (svc, other):
        pc.platform_notify_save([{"name": "default", "url": "https://contoso.webhook.office.com/hook"}])
    runs = [svc.governance_exceptions_ending(), other.governance_exceptions_ending(), svc.governance_exceptions_ending()]
    assert [len(x["ending"]) for x in runs] == [1, 1, 1]  # every PC still shows it on its bell and banner
    assert len([t for t in posts if "ends in 5 days" in t]) == 1, posts  # but Teams gets it once in total
    assert (svc.root / "platform_outbox.sqlite").exists() and (other.root / "platform_outbox.sqlite") != (svc.root / "platform_outbox.sqlite")
    with platform._db() as db:
        rows = db.rows(f"SELECT warned_until FROM {db.t('catalog', 'policy_exception')}")
    assert rows[0]["warned_until"] == soon["detail"]["until"]


def test_a_platform_that_cannot_claim_the_notice_posts_none_but_still_shows_it(svc, who, monkeypatch):
    _ending_soon(svc, who)
    sent = []
    monkeypatch.setattr(type(svc), "platform_notify", lambda self, events: sent.extend(events) or {})
    monkeypatch.setattr(type(svc), "_pn_raw", lambda self: {"routes": [{"name": "default", "url": ROUTE}]})

    class NoClaim:
        def __init__(self, inner):
            self.inner = inner

        def call(self, op, **kwargs):
            if op == "claim_exception_warning":
                raise LookupError("UDF_Platform wasn't found.")
            return self.inner.call(op, **kwargs)
    inner = svc._catalog()
    monkeypatch.setattr(type(svc), "_catalog", lambda self: NoClaim(inner))
    out = svc.governance_exceptions_ending()
    assert len(out["ending"]) == 1 and sent == []
    assert "Finish or repair once" in out["problems"][-1]


def test_the_claim_is_once_per_end_date_and_never_for_an_ended_exception(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    p.call("add_admin", user=ADMIN)
    until = (TODAY + dt.timedelta(days=5)).isoformat()
    with p._db() as db:
        policy.ensure_exception_schema(db)
        for eid in ("e1", "e2"):
            core._upsert(db, "catalog", "policy_exception", "id",
                         {"id": eid, "policy_id": "p", "scope": "project", "target": "crm", "until_date": until})
        db.commit()
        assert policy.claim_exception_warning(db, "e1", until) == {"claimed": True}
        assert policy.claim_exception_warning(db, "e1", until) == {"claimed": False}
        assert policy.claim_exception_warning(db, "e1", "2030-02-01") == {"claimed": False}  # not its end date
        policy.end_policy_exception(db, "e2", "done", ADMIN)
        assert policy.claim_exception_warning(db, "e2", until) == {"claimed": False}
        with pytest.raises(core.PlatformError, match="no policy exception"):
            policy.claim_exception_warning(db, "nope", until)


def test_a_pc_without_a_teams_route_never_claims_the_notice(svc, who, monkeypatch):
    _ending_soon(svc, who)
    calls = []
    inner = svc._catalog()

    class Counting:
        def call(self, op, **kwargs):
            calls.append(op)
            return inner.call(op, **kwargs)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Counting())
    out = svc.governance_exceptions_ending()
    assert len(out["ending"]) == 1 and "claim_exception_warning" not in calls  # still shown, not claimed
    # A route for another domain's notices only wouldn't take it either.
    monkeypatch.setattr(type(svc), "_pn_raw", lambda self: {"routes": [{"name": "finance", "url": ROUTE, "domains": ["finance"]}]})
    assert len(svc.governance_exceptions_ending()["ending"]) == 1 and "claim_exception_warning" not in calls
    [row] = inner.call("list_policy_exceptions", active=True)
    assert row["warned_until"] == ""  # left for a PC that can post


def test_a_pc_without_a_route_opening_first_leaves_the_post_to_one_with_a_route(svc, who, tmp_path, monkeypatch):
    from fabric_desktop import app_settings
    from fabric_desktop import platform_notify_service as pns
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    posts = []
    monkeypatch.setattr(pns, "post_teams", lambda url, title, lines, **kw: posts.append(url))
    _ending_soon(svc, who)
    b = Service(root=tmp_path / "pc-b")
    b._auth = Auth(who)
    platform = svc._catalog()
    monkeypatch.setattr(svc, "_catalog", lambda: platform)
    monkeypatch.setattr(b, "_catalog", lambda: platform)
    monkeypatch.setattr(svc, "_pn_raw", lambda: {})  # PC A: no Teams route
    monkeypatch.setattr(b, "_pn_raw", lambda: {"routes": [{"name": "default", "url": ROUTE}]})  # PC B: one
    assert len(svc.governance_exceptions_ending()["ending"]) == 1 and posts == []
    assert len(b.governance_exceptions_ending()["ending"]) == 1
    assert posts == [ROUTE]  # exactly one post, from B
    assert (svc.root / "platform_outbox.sqlite").exists() is False and (b.root / "platform_outbox.sqlite").exists()


def test_an_exception_already_warned_for_its_date_is_not_claimed_again(svc, who, monkeypatch):
    soon = _ending_soon(svc, who)
    monkeypatch.setattr(type(svc), "_pn_raw", lambda self: {"routes": [{"name": "default", "url": ROUTE}]})
    monkeypatch.setattr(type(svc), "platform_notify", lambda self, events: {})
    svc.governance_exceptions_ending()
    [row] = svc._catalog().call("list_policy_exceptions", active=True)
    assert row["warned_until"] == soon["detail"]["until"]  # the list says it was warned
    calls = []
    inner = svc._catalog()

    class Counting:
        def call(self, op, **kwargs):
            calls.append(op)
            return inner.call(op, **kwargs)
    monkeypatch.setattr(type(svc), "_catalog", lambda self: Counting())
    assert len(svc.governance_exceptions_ending()["ending"]) == 1
    assert "claim_exception_warning" not in calls  # no UDF call per load once it's warned
