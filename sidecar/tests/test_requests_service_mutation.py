"""Mutation tests for requests that make something in Fabric (central/requests_service.py): the role each
waiting access request's person has today, what a closed request says is left, and the steps of an
approval, with fakes for Fabric and the platform. Found by tools/mutate.py."""

import pytest

from fabric_desktop.central import core
from fabric_desktop.central import requests_service as rs

RAE = {"type": "User", "id": "", "email": "rae@example.com", "name": "Rae"}
GROUP = {"type": "Group", "id": "g-1", "email": "", "name": "Readers"}


def _assignment(role, upn="", oid="a-1"):
    return {"id": f"ra-{oid}", "role": role, "principal": {"id": oid, "type": "User" if upn else "Group",
                                                           "userDetails": {"userPrincipalName": upn} if upn else {}}}


class Base:
    def __init__(self, rows=(), domains=()):
        self.rows, self.domains = list(rows), list(domains)

    def platform_requests(self, status=""):
        return self.rows

    def platform_domains(self):
        return self.domains


class Probe(rs.RequestsMixin, Base):
    def __init__(self, fabric=None, **kw):
        super().__init__(**kw)
        self.fabric, self.progress = fabric, []

    def _rq_progress(self, req, fields):
        self.progress.append(fields)


class Fabric:
    def __init__(self, roles=None, workspaces=(), fail=()):
        self.roles, self.workspaces, self.fail, self.read, self.calls = roles or {}, list(workspaces), set(fail), [], []

    def list_role_assignments(self, wid):
        self.read.append(wid)
        if wid in self.fail:
            raise RuntimeError("no access")
        return self.roles.get(wid, [])

    def list_workspaces(self):
        return self.workspaces

    def create_workspace(self, name, capacity, description):
        self.calls.append(("create", name))
        return {"id": "w-new", "displayName": name}

    def assign_to_capacity(self, wid, capacity):
        self.calls.append(("assign", wid, capacity))

    def add_role_assignment(self, wid, oid, kind, role):
        if "add" in self.fail:
            raise RuntimeError("forbidden")
        self.calls.append(("add", wid, oid, kind, role))

    def update_role_assignment(self, wid, rid, role):
        self.calls.append(("update", wid, rid, role))


def _ask(id, wid, who=RAE, **kw):
    return {"id": id, "kind": "workspace_access", "status": "waiting", "can_decide": True,
            "detail": {"workspace_id": wid, "principal": who}} | kw


def test_each_waiting_access_request_says_the_role_its_person_has_today_read_once_per_workspace():
    rows = [_ask("r1", "w1"), _ask("r2", "w1", who=GROUP), _ask("r3", "w2"), _ask("r4", "w3"),
            _ask("r5", "w9", status="approved"), _ask("r6", "w9", can_decide=False), _ask("r7", "w9", kind="workspace")]
    fabric = Fabric(roles={"w1": [_assignment("Admin", upn="Rae@Example.com"), _assignment("Viewer", oid="g-1")],
                           "w2": [_assignment("Member", upn="dana@example.com")]}, fail={"w3"})
    out = Probe(fabric, rows=rows).platform_requests()
    assert [r.get("today") for r in out] == [{"role": "Admin"}, {"role": "Viewer"}, {"role": ""}, {"checked": False},
                                             None, None, None]
    assert fabric.read == ["w1", "w2", "w3"]


def test_a_principal_with_no_detail_has_no_role_of_its_own():
    out = Probe(Fabric(roles={"w1": [_assignment("Admin", upn="rae@example.com")]}),
                rows=[_ask("r1", "w1", who=None)]).platform_requests()
    assert out[0]["today"] == {"role": ""}


def test_an_older_platform_that_says_an_operation_isnt_there_says_to_repair():
    class Refuses:
        def __init__(self, text):
            self.text = text

        def call(self, op, **kw):
            raise core.PlatformError(self.text)

    with pytest.raises(LookupError, match="UDF_Platform is older"):
        rs.call_or_repair(Refuses("access_reviews not found"), "access_reviews", "review access")
    with pytest.raises(core.PlatformError, match="access_reviews was refused"):
        rs.call_or_repair(Refuses("access_reviews was refused"), "access_reviews", "review access")


def test_a_shortcut_request_says_which_shortcut_is_left_and_one_that_made_none_says_nothing():
    req = {"kind": "shortcut", "target": "orders", "detail": {"name": "Orders", "lakehouse_name": "LH_Sales",
                                                              "made": {"shortcut_id": "s1"}}}
    assert rs.left_in_fabric(req) == [("The shortcut Orders in LH_Sales, made by this request, is still in OneLake: "
                                       "remove it on OneLake > Shortcuts if it isn't wanted")]
    assert rs.left_in_fabric({"kind": "shortcut", "detail": {"made": {}}}) == []
    assert rs.left_in_fabric({"kind": "access", "detail": {"made": {"shortcut_id": "s1"}}}) == []


def test_a_closed_domain_request_names_the_domain_it_asked_for_among_others():
    probe = Probe(domains=[{"id": "hr", "name": "HR"}, {"id": "sales", "name": "Sales", "fabric_domain": "fd-1"}])
    out = probe._rq_closed({"id": "r1", "kind": "domain", "target": "sales", "detail": {"name": "Sales"}})
    assert out["next"] == [("The domain Sales is in the platform and Fabric: it was there before this request, or "
                            "approving it made it. Check which before deleting it")]


def test_a_new_workspace_isnt_moved_to_its_capacity_again_but_one_found_elsewhere_is():
    req = {"id": "r1", "reason": "Reports", "detail": {"name": "Sales", "capacity_id": "CAP-1"}}
    fabric = Fabric()
    assert Probe(fabric)._rq_make_workspace(req)["workspace_id"] == "w-new"
    assert fabric.calls == [("create", "Sales")]
    for found, moved in (({"id": "w1", "displayName": "sales", "capacityId": "cap-1"}, []),
                         ({"id": "w1", "displayName": "sales", "capacityId": "cap-2"}, [("assign", "w1", "CAP-1")])):
        fabric = Fabric(workspaces=[dict(found, description="(KratosForge request r1)")])
        Probe(fabric)._rq_make_workspace(req)
        assert fabric.calls == moved


def test_an_admin_that_cant_be_added_is_named_in_the_failed_step():
    req = {"id": "r1", "detail": {"name": "Sales", "capacity_id": "cap-1", "admins": [GROUP]}}
    with pytest.raises(rs.StepError, match="^Make Readers an admin failed"):
        Probe(Fabric(fail={"add"}))._rq_make_workspace(req)


def test_access_given_where_there_was_none_says_it_was_assigned():
    req = {"id": "r1", "detail": {"principal": GROUP, "role": "Viewer", "workspace_id": "w1"}}
    fabric = Fabric()
    probe = Probe(fabric)
    assert probe._rq_give_access(req) == {"assigned": True}
    assert fabric.calls == [("add", "w1", "g-1", "Group", "Viewer")]
    assert probe.progress == [{"assigned": True, "change": "gave", "from": ""}]
