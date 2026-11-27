"""The first excellence controls: what the first mutation run of excellence/catalog.py left alive (batch 344).

The controls' checks are run on a stand-in context that returns one evidence record, so each message can be
read exactly."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop.excellence import catalog


def ctx(payload: dict, kind_id: str = "r1"):
    return SimpleNamespace(one=lambda kind: {"id": kind_id, "payload": payload})


def people(n: int, role: str = "Admin", kind: str = "User", guest: bool | None = False) -> list[dict]:
    return [{"name": f"p{i}", "role": role, "type": kind, "guest": guest} for i in range(n)]


def roles(assignments: list[dict]):
    return ctx({"assignments": assignments})


def test_names_list_five_then_count_the_rest():
    assert catalog._names(people(5)) == "p0, p1, p2, p3, p4"
    assert catalog._names(people(6)) == "p0, p1, p2, p3, p4 and 1 more"


def test_admin_counts_read_as_words():
    assert catalog._admins(roles([]))["text"] == "0 Admin assignments (at most 4)."
    assert catalog._admins(roles(people(1)))["text"] == "1 Admin assignment (at most 4)."
    assert catalog._admins(roles(people(2)))["text"] == "2 Admin assignments (at most 4)."


def test_guest_counts_read_as_words():
    one = catalog._guests(roles(people(1, "Member", guest=True)))
    assert one["text"] == "1 guest account from another organization can change this workspace: p0."
    two = catalog._guests(roles(people(2, "Member", guest=True)))
    assert two["text"].startswith("2 guest accounts from another organization")
    unknown_one = catalog._guests(roles(people(1, "Member", guest=None)))
    assert unknown_one["text"] == "Fabric didn't send the sign-in name of p0, so whether this is a guest isn't known."
    unknown_two = catalog._guests(roles(people(2, "Member", guest=None)))
    assert "so whether they are guests isn't known" in unknown_two["text"]


def test_app_admin_counts_read_as_words():
    assert catalog._app_admins(roles(people(1, kind="ServicePrincipal")))["text"] == "1 app is Admin: p0."
    assert catalog._app_admins(roles(people(2, kind="ServicePrincipal")))["text"] == "2 apps are Admin: p0, p1."


def test_only_users_with_a_write_role_count_as_direct_writers():
    groups = people(6, "Member", kind="Group")
    viewers = people(6, "Viewer", kind="User")
    out = catalog._direct_users(roles(groups + viewers))
    assert out["outcome"] == "pass" and out["text"].startswith("0 directly assigned users with a write role")
    assert catalog._direct_users(roles(people(1, "Member")))["text"].startswith(
        "1 directly assigned user with a write role (at most 5)")
    assert catalog._direct_users(roles(people(2, "Member")))["text"].startswith("2 directly assigned users")


def test_an_active_capacity_names_its_size():
    assert catalog._capacity_active(ctx({"state": "Active", "sku": "F64"}))["text"] == "The capacity (F64) is Active."
    assert catalog._capacity_active(ctx({"state": "Active"}))["text"] == "The capacity (size not given) is Active."


def test_peers_not_read_list_five_then_count_the_rest():
    five = catalog._unseen({"peers_not_read": [f"n{i}" for i in range(5)]}, "notebooks")
    assert "couldn't be read (n0, n1, n2, n3, n4), so" in five["text"]
    six = catalog._unseen({"peers_not_read": [f"n{i}" for i in range(6)]}, "notebooks")
    assert "couldn't be read (n0, n1, n2, n3, n4 and 1 more), so" in six["text"]
    assert six["needs"] == "the definitions of n0, n1, n2, n3, n4 and 1 more"
