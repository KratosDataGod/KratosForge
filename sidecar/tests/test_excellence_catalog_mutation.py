"""Mutation batch 362: excellence/catalog.py (docs/quality/mutation-testing.md). The survivors were the
wording of a control's result for exactly one person or app, the list of names cut at five, the
"size not given" of a capacity without a SKU, and which assignments count as a person's own write role."""

from __future__ import annotations

from fabric_desktop.excellence import catalog
from fabric_desktop.excellence import controls as ct


def person(name: str, role: str = "Member", kind: str = "User", guest: bool | None = False) -> dict:
    return {"name": name, "role": role, "type": kind, "guest": guest}


def roles(*people: dict) -> ct.Context:
    return ct.Context({"id": "ws"}, {"workspace.roles": [{"id": "r1", "payload": {"assignments": list(people)}}]})


def item(payload: dict) -> ct.Context:
    return ct.Context({"id": "nb"}, {"item.optimization": [{"id": "o1", "payload": payload}]})


def test_five_names_are_listed_whole_and_a_sixth_is_counted_as_one_more():
    five = [person(f"p{i}") for i in range(5)]
    assert catalog._names(five) == "p0, p1, p2, p3, p4"
    assert catalog._names([*five, person("p5")]) == "p0, p1, p2, p3, p4 and 1 more"
    assert catalog._names([person("a"), person("b")], limit=1) == "a and 1 more"


def test_one_admin_is_one_assignment_and_two_are_assignments():
    assert catalog._admins(roles(person("a", "Admin")))["text"] == "1 Admin assignment (at most 4)."
    assert catalog._admins(roles(person("a", "Admin"), person("b", "Admin")))["text"] == "2 Admin assignments (at most 4)."
    assert catalog._admins(roles())["text"] == "0 Admin assignments (at most 4)."


def test_one_guest_is_an_account_and_two_are_accounts():
    one = catalog._guests(roles(person("g", guest=True)))
    assert one["outcome"] == "fail"
    assert one["text"] == "1 guest account from another organization can change this workspace: g."
    two = catalog._guests(roles(person("g", guest=True), person("h", guest=True)))
    assert two["text"] == "2 guest accounts from another organization can change this workspace: g, h."


def test_a_writer_whose_guest_status_isnt_known_is_this_and_two_are_they():
    one = catalog._guests(roles(person("u", guest=None)))
    assert one["outcome"] == "not_evaluated"
    assert one["text"] == "Fabric didn't send the sign-in name of u, so whether this is a guest isn't known."
    two = catalog._guests(roles(person("u", guest=None), person("v", guest=None)))
    assert two["text"] == "Fabric didn't send the sign-in name of u, v, so whether they are guests isn't known."


def test_one_admin_app_is_and_two_are():
    app = dict(person("a1", "Admin", "ServicePrincipal"))
    assert catalog._app_admins(roles(app))["text"] == "1 app is Admin: a1."
    assert catalog._app_admins(roles(app, dict(app, name="a2")))["text"] == "2 apps are Admin: a1, a2."


def test_only_a_users_own_write_role_counts_as_given_person_by_person():
    # a group with a write role, and a user who can only view, are not people with their own write role
    got = catalog._direct_users(roles(person("u1"), person("grp", kind="Group"), person("v", role="Viewer")))
    assert got["text"] == ("1 directly assigned user with a write role (at most 5); group members aren't expanded.")
    two = catalog._direct_users(roles(person("u1"), person("u2", role="Contributor")))
    assert two["text"].startswith("2 directly assigned users with a write role")


def test_a_capacity_without_a_sku_says_its_size_isnt_given():
    ctx = ct.Context({"id": "cap"}, {"capacity.state": [{"id": "c1", "payload": {"state": "Active"}}]})
    assert catalog._capacity_active(ctx)["text"] == "The capacity (size not given) is Active."
    ctx = ct.Context({"id": "cap"}, {"capacity.state": [{"id": "c1", "payload": {"state": "Active", "sku": "F2"}}]})
    assert catalog._capacity_active(ctx)["text"] == "The capacity (F2) is Active."


def test_five_peers_not_read_are_all_named_and_a_sixth_is_counted():
    five = [f"n{i}" for i in range(5)]
    got = catalog._unseen({"peers_not_read": five}, "notebooks")
    assert got["text"].startswith("Nothing matched, but 5 other notebooks couldn't be read (n0, n1, n2, n3, n4), so")
    assert got["needs"] == "the definitions of n0, n1, n2, n3, n4"
    six = catalog._unseen({"peers_not_read": [*five, "n5"]}, "notebooks")
    assert "(n0, n1, n2, n3, n4 and 1 more)" in six["text"]
    assert catalog._duplicates(item({"matched": [], "peers_not_read": five}))["outcome"] == "not_evaluated"
