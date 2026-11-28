"""access.py paths that mutation testing (batch 72, docs/quality/mutation-testing.md) showed no test
pinned: who a review flags and in what order, which rules count as a role's paths, how a read role
is built and refused, which paths reach a table, and who can read a table and why."""

from __future__ import annotations

import pytest

from fabric_desktop import access


def a(pid, role, kind="User", upn="", name=None, aid=None):
    p = {"id": pid, "type": kind, "displayName": name if name is not None else pid}
    if upn:
        p["userDetails"] = {"userPrincipalName": upn}
    return {"id": aid or f"as-{pid}", "role": role, "principal": p}


# ---- principal

def test_a_principal_is_named_by_display_name_then_upn_then_id():
    assert access.principal({"id": "1", "displayName": "Ann", "type": "User",
                             "userDetails": {"userPrincipalName": "ann@x.com"}}) == {
        "id": "1", "name": "Ann", "type": "User", "email": "ann@x.com", "guest": False}
    assert access.principal({"id": "1", "userDetails": {"userPrincipalName": "ann@x.com"}})["name"] == "ann@x.com"
    assert access.principal({"id": "1"}) == {"id": "1", "name": "1", "type": "", "email": "", "guest": False}


@pytest.mark.parametrize("upn, guest", [("bob_y.com#EXT#@x.onmicrosoft.com", True),
                                        ("bob_y.com#ext#@x.onmicrosoft.com", True), ("bob@x.com", False)])
def test_a_guest_is_an_ext_upn_in_any_case(upn, guest):
    assert access.principal({"id": "1", "userDetails": {"userPrincipalName": upn}})["guest"] is guest


# ---- review

def ws(key, env, *assignments, **extra):
    return {"key": key, "label": key.title(), "env": env, "workspace_id": f"id-{key}",
            "workspace_name": f"WS {key}", "assignments": list(assignments), **extra}


def test_one_row_per_person_with_their_role_in_each_workspace():
    out = access.review([ws("dev", "dev", a("u1", "Member")), ws("prod", "prod", a("u1", "Viewer", aid="x9"))])
    (row,) = out["people"]
    assert row["roles"] == {"dev": {"role": "Member", "assignment_id": "as-u1"},
                            "prod": {"role": "Viewer", "assignment_id": "x9"}}
    assert row["flags"] == [] and out["flagged"] == 0


@pytest.mark.parametrize("role", ["Admin", "Member", "Contributor"])
def test_a_user_who_can_change_prod_is_flagged(role):
    out = access.review([ws("dev", "dev", a("u1", "Viewer")), ws("prod", "prod", a("u1", role))])
    flags = out["people"][0]["flags"]
    assert flags[0] == f"Can change Prod directly ({role}); a group is easier to review"
    assert "Can change Prod but has no access to Dev" not in flags
    assert out["flagged"] == 1


def test_a_prod_viewer_and_a_prod_group_are_not_flagged():
    out = access.review([ws("prod", "prod", a("u1", "Viewer"), a("g1", "Admin", kind="Group"))])
    assert [r["flags"] for r in out["people"]] == [[], []] and out["flagged"] == 0


def test_a_prod_writer_with_no_dev_role_is_flagged_only_when_there_is_a_dev():
    both = access.review([ws("dev", "dev"), ws("prod", "prod", a("u1", "Member"))])
    assert both["people"][0]["flags"] == ["Can change Prod directly (Member); a group is easier to review",
                                         "Can change Prod but has no access to Dev"]
    no_dev = access.review([ws("test", "test"), ws("prod", "prod", a("u1", "Member"))])
    assert no_dev["people"][0]["flags"] == ["Can change Prod directly (Member); a group is easier to review"]


def test_a_dev_workspace_is_not_read_as_prod():
    out = access.review([ws("dev", "dev", a("u1", "Admin"))])
    assert out["people"][0]["flags"] == []


def test_a_guest_and_an_admin_app_are_flagged():
    out = access.review([ws("dev", "dev", a("g", "Viewer", upn="g_y.com#EXT#@x"), a("app", "Admin", kind="ServicePrincipal"),
                                a("app2", "Member", kind="ServicePrincipal"))])
    flags = {r["id"]: r["flags"] for r in out["people"]}
    assert flags == {"g": ["Guest account from another organization"], "app": ["An app with Admin rights"], "app2": []}
    assert out["flagged"] == 2


def test_flagged_first_then_users_then_by_name_ignoring_case():
    out = access.review([ws("prod", "prod",
                            a("s1", "Viewer", kind="ServicePrincipal", name="aaa"), a("u2", "Viewer", name="beth"),
                            a("u1", "Viewer", name="Adam"), a("u3", "Admin", name="zed"),
                            a("s2", "Admin", kind="ServicePrincipal", name="Bot"))])
    assert [r["id"] for r in out["people"]] == ["u3", "s2", "u1", "u2", "s1"]


def test_the_workspaces_come_back_with_their_error_and_nothing_else():
    out = access.review([ws("dev", "dev", error="no rights", secret="x")])
    assert out["workspaces"] == [{"key": "dev", "label": "Dev", "env": "dev", "workspace_id": "id-dev",
                                  "workspace_name": "WS dev", "error": "no rights"}]


# ---- data access roles

def rule(paths, effect=None, attr="Path"):
    r = {"permission": [{"attributeName": attr, "attributeValueIncludedIn": paths}]}
    if effect:
        r["effect"] = effect
    return r


def test_a_roles_paths_are_its_permit_rules_path_values():
    role = {"name": "R", "decisionRules": [rule(["/Tables/a"]), rule(["/Tables/b"], "Permit"),
                                           rule(["/Tables/c"], "Deny"), rule(["Read"], attr="Action")]}
    assert access.describe_role(role)["paths"] == ["/Tables/a", "/Tables/b"]


def test_a_role_is_described_with_its_entra_and_item_members():
    role = {"name": "R", "members": {"microsoftEntraMembers": [{"objectId": "o1"}, {"objectId": "o2"}],
                                     "fabricItemMembers": [{"itemAccess": ["ReadAll", "Write"]}, {}]}}
    assert access.describe_role(role) == {"name": "R", "paths": [], "entra": ["o1", "o2"],
                                          "item_members": ["ReadAll, Write", ""]}


def test_a_read_role_on_paths_for_people():
    role = access.make_role("Sales1", ["*", "Tables/dbo/", "/Files/raw", ""], ["o1", "o2", "o1"], "t")
    assert role == {"name": "Sales1", "decisionRules": [{"effect": "Permit", "permission": [
        {"attributeName": "Path", "attributeValueIncludedIn": ["*", "/Tables/dbo", "/Files/raw"]},
        {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}]}],
        "members": {"microsoftEntraMembers": [{"tenantId": "t", "objectId": "o1"}, {"tenantId": "t", "objectId": "o2"}]}}


def test_an_edited_role_keeps_its_item_members():
    keep = {"members": {"fabricItemMembers": [{"itemAccess": ["ReadAll"]}]}}
    role = access.make_role("DefaultReader", ["*"], [], "t", keep=keep)
    assert role["members"] == {"fabricItemMembers": [{"itemAccess": ["ReadAll"]}]}


@pytest.mark.parametrize("name", ["", "1abc", "a-b", "a b", "_a", "a" * 129, None])
def test_a_role_name_with_other_characters_or_too_long_is_refused(name):
    with pytest.raises(ValueError, match="letters and digits only"):
        access.make_role(name, ["*"], ["o"], "t")


@pytest.mark.parametrize("name", ["a", "A1", "a" * 128])
def test_a_role_name_of_letters_and_digits_up_to_128_is_taken(name):
    assert access.make_role(name, ["*"], ["o"], "t")["name"] == name


def test_a_role_name_ending_in_a_line_break_is_refused():
    with pytest.raises(ValueError):
        access.make_role("Sales\n", ["*"], ["o"], "t")


def test_a_role_with_no_paths_or_no_people_is_refused():
    with pytest.raises(ValueError, match="at least one table"):
        access.make_role("R", ["", ""], ["o"], "t")
    with pytest.raises(ValueError, match="at least one person"):
        access.make_role("R", ["*"], [], "t", keep={"members": {"fabricItemMembers": []}})
    with pytest.raises(ValueError, match="at least one person"):
        access.make_role("R", ["*"], [], "t")


@pytest.mark.parametrize("path, table, reaches", [
    ("*", "/Tables/dbo/orders", True), ("/Tables/dbo", "/Tables/dbo/orders", True),
    ("/Tables/dbo/", "/Tables/dbo/orders", True), ("/Tables/dbo/orders", "/Tables/dbo/orders", True),
    ("/Tables/db", "/Tables/dbo/orders", False), ("/Tables/dbo/orders", "/Tables/dbo", False),
    ("/Files", "/Tables/dbo/orders", False)])
def test_which_paths_reach_a_table(path, table, reaches):
    assert access.covers(path, table) is reaches


class Err(Exception):
    def __init__(self, text, code=None):
        super().__init__(text)
        self.code = code


@pytest.mark.parametrize("exc, off", [
    (Err("x", "OneLakeSecurityNotEnabled"), True), (Err("x", "DataAccessRolesNotEnabled"), True),
    (Err("x", "FeatureNotAvailable"), True), (Err("x", "UniversalSecurityFeatureNotEnabled"), True),
    (Err("OneLake security is NOT enabled here"), True), (Err("x", "Forbidden"), False), (Err("x"), False)])
def test_which_answers_mean_onelake_security_is_off(exc, off):
    assert access.security_off(exc) is off


def test_reading_roles_says_why_when_it_cant():
    assert access.read_roles(lambda: [{"name": "R"}]) == ([{"name": "R"}], None)

    def off():
        raise Err("x", "OneLakeSecurityNotEnabled")

    def denied():
        raise Err("no rights", "Forbidden")
    assert access.read_roles(off) == ([], None)
    assert access.read_roles(denied) == ([], "The lakehouse's data access roles couldn't be read: no rights")


def test_who_can_read_a_table_and_why():
    assignments = [a("w1", "Contributor", name="Wes"), a("v1", "Viewer", name="Vic"), a("v2", "Viewer", name="ann")]
    roles = [{"name": "Sales", "decisionRules": [rule(["/Tables/dbo"])],
              "members": {"microsoftEntraMembers": [{"objectId": "v1"}, {"objectId": "w1"}, {"objectId": "zz"}],
                          "fabricItemMembers": [{"itemAccess": ["ReadAll"]}, {}]}},
             {"name": "Other", "decisionRules": [rule(["/Tables/hr"])],
              "members": {"microsoftEntraMembers": [{"objectId": "v2"}]}}]
    out = access.who_can_read("/Tables/dbo/orders", assignments, roles)
    assert [(r["id"], r["name"], r["how"]) for r in out] == [
        ("item:Sales:", "Everyone with item permission", "Data access role Sales"),
        ("item:Sales:ReadAll", "Everyone with ReadAll permission", "Data access role Sales"),
        ("v1", "Vic", "Data access role Sales"),
        ("zz", "zz", "Data access role Sales"),
        ("w1", "Wes", "Workspace Contributor")]
    unknown = next(r for r in out if r["id"] == "zz")
    assert unknown == {"id": "zz", "name": "zz", "type": "", "email": "", "guest": False, "how": "Data access role Sales"}
    item = next(r for r in out if r["id"] == "item:Sales:ReadAll")
    assert item["type"] == "Item permission" and item["email"] == "" and item["guest"] is False


def test_with_no_roles_only_workspace_writers_can_read():
    out = access.who_can_read("/Tables/t", [a("a1", "Admin"), a("m1", "Member"), a("v1", "Viewer")], None)
    assert [(r["id"], r["how"]) for r in out] == [("a1", "Workspace Admin"), ("m1", "Workspace Member")]
