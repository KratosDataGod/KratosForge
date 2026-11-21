"""Entra ID groups per domain (central/entra.py), the paths the other tests leave: a plan for a domain that isn't
there, roles the naming turns off, and a roles script with nothing left to do by hand. Plain domain rows; no
platform, Entra or Fabric is called."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop.central import core, entra

SALES = {"id": "sales", "name": "Sales", "fabric_domain": "fd-1", "owners": "ana@contoso.com"}


def _naming(**changes):
    return copy.deepcopy(core.DEFAULT_GROUP_NAMING) | changes


def test_a_plan_for_a_domain_that_isnt_there_is_refused():
    with pytest.raises(ValueError, match="^marketing isn't a domain$"):
        entra.plan([SALES], "marketing", [], _naming())


def test_roles_the_naming_turns_off_get_no_group():
    off = [r.key for r in entra.ROLES if r.key != "domain_admins"]
    rows = entra.plan([SALES], "sales", [], _naming(roles_off=off))
    assert [(r["role"], r["env"]) for r in rows] == [("domain_admins", "")]


def test_a_roles_script_with_nothing_to_do_by_hand_says_nothing_about_it():
    rows = entra.plan([SALES], "sales", [], _naming(roles_off=[r.key for r in entra.ROLES if r.key != "domain_admins"]))
    script = entra.roles(rows, {})
    assert "/admin/domains/fd-1/roleAssignments/bulkAssign" in script
    assert "# Do these by hand:" not in script
