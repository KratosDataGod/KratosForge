"""A waiting workspace access request shows the approver the role its person or group has today (#1108).

Read from Fabric as the viewer, once per workspace, only for rows they can decide, and never stored.
Fabric is faked as in test_requests_core_four; no tenant is called."""

from test_requests_core_four import ADMIN, DEV, GROUP, WS, _same_day, as_user, svc, who  # noqa: F401

OTHER_WS = "11111111-2222-3333-4444-555555555555"


def _count_reads(svc):
    real, seen = svc.api.list_role_assignments, []

    def read(workspace_id):
        seen.append(workspace_id)
        return real(workspace_id)

    svc.api.list_role_assignments = read
    return seen


def _ask(svc, who, ws, role, group=False):
    as_user(who, DEV)
    extra = {"who": "group", "group_id": GROUP, "group_name": "Sales Engineers"} if group else {}
    return svc.platform_request("workspace_access", "", "", {"workspace_id": ws, "role": role} | extra)


def _today(svc, rid):
    return next(r for r in svc.platform_requests() if r["id"] == rid).get("today")


def test_each_waiting_access_request_says_the_role_held_today(svc, who):
    svc.api.roles[WS] = [
        {"id": "g1", "role": "Admin", "principal": {"id": GROUP, "type": "Group"}},
        {"id": "u1", "role": "Viewer", "principal": {"id": "x", "type": "User", "userDetails": {"userPrincipalName": DEV}}}]
    group = _ask(svc, who, WS, "Viewer", group=True)
    mine = _ask(svc, who, WS, "Member")
    elsewhere = _ask(svc, who, OTHER_WS, "Viewer")
    as_user(who, ADMIN)
    seen = _count_reads(svc)
    rows = {r["id"]: r for r in svc.platform_requests()}
    assert rows[group["id"]]["today"] == {"role": "Admin"}
    assert rows[mine["id"]]["today"] == {"role": "Viewer"}
    assert rows[elsewhere["id"]]["today"] == {"role": ""}  # no role of its own there
    assert sorted(seen) == sorted([WS, OTHER_WS]), "one read per workspace, however many rows name it"


def test_nothing_is_read_for_someone_who_cant_decide_or_a_request_that_is_done(svc, who):
    req = _ask(svc, who, WS, "Viewer")
    seen = _count_reads(svc)
    assert "today" not in next(r for r in svc.platform_requests() if r["id"] == req["id"])  # DEV can't decide
    assert seen == []
    as_user(who, ADMIN)
    svc.platform_decide(req["id"], True)
    seen.clear()
    assert "today" not in next(r for r in svc.platform_requests() if r["id"] == req["id"])
    assert seen == []


def test_a_read_that_fails_says_so_and_approving_still_works(svc, who):
    svc.api.roles[WS] = [{"id": "g1", "role": "Admin", "principal": {"id": GROUP, "type": "Group"}}]
    req = _ask(svc, who, WS, "Viewer", group=True)
    as_user(who, ADMIN)
    real = svc.api.list_role_assignments

    def down(workspace_id):
        raise RuntimeError("Fabric answered 503")

    svc.api.list_role_assignments = down
    assert _today(svc, req["id"]) == {"checked": False}
    svc.api.list_role_assignments = real
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and svc.api.roles[WS][0]["role"] == "Admin"


def test_the_role_is_never_stored(svc, who):
    svc.api.roles[WS] = [{"id": "g1", "role": "Admin", "principal": {"id": GROUP, "type": "Group"}}]
    req = _ask(svc, who, WS, "Viewer", group=True)
    as_user(who, ADMIN)
    assert _today(svc, req["id"]) == {"role": "Admin"}
    stored = svc._catalog().call("list_requests", status="waiting")
    assert all("today" not in r and "today" not in (r.get("detail") or {}) for r in stored)
