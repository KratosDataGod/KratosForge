"""central/requests_service.py, mutation batch 400: which platform errors mean an older platform, the
shortcut note on a closed request, the role each waiting access request's person has today, the domain note
after a domain request is closed, a new workspace's capacity, the admin step's name, and what giving access
returns. Fabric and the platform are fakes."""

import pytest

from fabric_desktop.central import core
from fabric_desktop.central import requests_service as rs


class Refuses:
    def __init__(self, exc):
        self.exc = exc

    def call(self, op, **kw):
        raise self.exc


def test_a_not_found_error_about_another_operation_isnt_an_older_platform():
    with pytest.raises(core.PlatformError, match="table products not found"):
        rs.call_or_repair(Refuses(core.PlatformError("table products not found")), "request", "take this request")
    with pytest.raises(LookupError):
        rs.call_or_repair(Refuses(core.PlatformError("request: not found")), "request", "take this request")


def test_a_shortcut_note_names_the_shortcut_and_its_lakehouse_or_says_its_lakehouse():
    made = {"shortcut_id": "s1"}
    assert rs.left_in_fabric({"kind": "shortcut", "target": "t", "detail": {"made": {}}}) == []
    named = rs.left_in_fabric({"kind": "shortcut", "target": "t",
                               "detail": {"name": "orders", "lakehouse_name": "LH_Sales", "made": made}})
    assert named[0].startswith("The shortcut orders in LH_Sales, made by this request")
    bare = rs.left_in_fabric({"kind": "shortcut", "target": "t", "detail": {"made": made}})
    assert bare[0].startswith("The shortcut t in its lakehouse, made by this request")


class Base:
    def __init__(self, rows):
        self.rows = rows

    def platform_requests(self, status=""):
        return [dict(r) for r in self.rows]


class Fabric:
    def __init__(self, roles=None, fail=()):
        self.roles, self.fail, self.read = roles or {}, set(fail), []

    def list_role_assignments(self, wid):
        self.read.append(wid)
        if wid in self.fail:
            raise RuntimeError("403")
        return self.roles.get(wid, [])


class Viewer(rs.RequestsMixin, Base):
    def __init__(self, rows, fabric):
        Base.__init__(self, rows)
        self.fabric = fabric


ANA = {"id": "a1", "email": "ana@contoso.com", "name": "Ana"}


def ask(wid, **kw):
    return {"kind": "workspace_access", "status": "waiting", "can_decide": True,
            "detail": {"workspace_id": wid, "principal": ANA}} | kw


def test_only_waiting_access_requests_the_viewer_can_decide_get_todays_role():
    fabric = Fabric(roles={"w1": [{"role": "Admin", "principal": {"id": "a1", "type": "User"}}]})
    rows = [ask("w1"), ask("w1", status="done"), ask("w1", can_decide=False), ask("w1", kind="workspace")]
    out = Viewer(rows, fabric).platform_requests()
    assert [r.get("today") for r in out] == [{"role": "Admin"}, None, None, None]


def test_each_workspaces_roles_are_read_once_and_an_unreadable_one_says_so():
    fabric = Fabric(roles={"w1": [{"role": "Member", "principal": {"id": "a1", "type": "User"}}]}, fail={"w2"})
    out = Viewer([ask("w1"), ask("w2"), ask("w1"), ask("w3")], fabric).platform_requests()
    assert fabric.read == ["w1", "w2", "w3"]
    assert [r["today"] for r in out] == [{"role": "Member"}, {"checked": False}, {"role": "Member"}, {"role": ""}]


class Closer(rs.RequestsMixin):
    def __init__(self, domains):
        self.domains = domains

    def platform_domains(self):
        return self.domains


def test_a_closed_domain_request_only_notes_its_own_domain():
    out = {"id": "r1", "kind": "domain", "target": "sales", "detail": {}}
    assert "next" not in Closer([{"id": "finance"}])._rq_closed(dict(out))
    noted = Closer([{"id": "finance"}, {"id": "sales"}])._rq_closed(dict(out))
    assert noted["next"][0].startswith("The domain sales is in the platform")


class Api:
    def __init__(self, workspaces=(), roles=(), fail_add=False):
        self.workspaces, self.roles, self.fail_add = list(workspaces), list(roles), fail_add
        self.assigned, self.added = [], []

    def list_workspaces(self):
        return self.workspaces

    def create_workspace(self, name, capacity, description):
        ws = {"id": "w-new", "displayName": name, "capacityId": "", "description": description}
        self.workspaces.append(ws)
        return ws

    def assign_to_capacity(self, wid, capacity):
        self.assigned.append((wid, capacity))

    def list_role_assignments(self, wid):
        return self.roles

    def add_role_assignment(self, wid, pid, kind, role):
        if self.fail_add:
            raise RuntimeError("403 Forbidden")
        self.added.append((pid, kind, role))


class Maker(rs.RequestsMixin):
    def __init__(self, api):
        self.fabric, self.progress = api, []

    def _rq_progress(self, req, fields):
        self.progress.append(fields)

    def _add_user_by_email(self, wid, email, role):
        self.fabric.add_role_assignment(wid, email, "User", role)


def workspace_request(**detail):
    return {"id": "r1", "target": "Sales", "reason": "",
            "detail": {"name": "Sales", "capacity_id": "cap-1", **detail}}


def test_a_new_workspace_isnt_put_on_its_capacity_a_second_time():
    api = Api()
    Maker(api)._rq_make_workspace(workspace_request())
    assert api.assigned == []


def test_a_failed_admin_step_names_the_admin():
    api = Api(fail_add=True)
    group = {"id": "g1", "type": "Group", "name": "Data team"}
    with pytest.raises(rs.StepError, match="Make Data team an admin failed"):
        Maker(api)._rq_make_workspace(workspace_request(admins=[group]))


def test_giving_a_role_someone_didnt_have_says_it_was_assigned():
    api = Api()
    req = {"id": "r1", "detail": {"principal": {"id": "g1", "type": "Group", "name": "Data team"},
                                  "role": "Viewer", "workspace_id": "w1"}}
    assert Maker(api)._rq_give_access(req) == {"assigned": True}
    assert api.added == [("g1", "Group", "Viewer")]
