"""central/entra.py held to its exact words and limits: which part of a domain names its groups, the
roles a platform turned off, the 256 and 64 character cuts, the scripts' header lines, and the role
script's by-hand lines. Everything here is text: nothing calls Graph or Fabric."""

from __future__ import annotations

import copy
import csv
import dataclasses
import io

import pytest

from fabric_desktop.central import core, entra

DOMAINS = [
    {"id": "fin", "name": "Finance", "fabric_domain": "fd-1"},
    {"id": "ops", "name": "", "fabric_domain": ""},
]


def naming(**changes):
    out = copy.deepcopy(core.DEFAULT_GROUP_NAMING)
    out.update(changes)
    return out


def plan(domain_id="fin", links=(), envs=("prod",), **changes):
    return entra.plan(DOMAINS, domain_id, list(links), naming(**changes), envs=list(envs))


def row(rows, role):
    return next(r for r in rows if r["role"] == role)


def test_a_role_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        entra.ROLES[0].label = "x"


@pytest.mark.parametrize("part, name", [("id", "SG-FAB-FIN-READERS-PROD"), ("name", "SG-FAB-FINANCE-READERS-PROD"),
                                        (None, "SG-FAB-FIN-READERS-PROD")])
def test_groups_are_named_by_the_domains_id_unless_the_platform_says_name(part, name):
    assert entra.group_name(naming(domain_part=part), "readers", DOMAINS[0], None, "prod") == name


def test_a_domain_that_isnt_there_is_refused():
    with pytest.raises(ValueError, match="^nope isn't a domain$"):
        plan("nope")


def test_roles_the_platform_turned_off_get_no_groups():
    assert {r["role"] for r in plan(roles_off=["stewards", "readers"])} == {
        "domain_admins", "domain_contributors", "workspace_admins", "viewers"}
    assert "stewards" in {r["role"] for r in plan()}


def test_a_group_name_is_cut_at_256_characters_and_its_mail_nickname_at_64():
    name = entra.group_name(naming(prefix="P" * 300), "readers", DOMAINS[0], None, "prod")
    assert name == "P" * 256
    assert entra.mail_nickname("A" * 100) == "a" * 64


def test_a_row_names_its_domain_its_fabric_domain_and_says_its_environment_only_when_it_has_one():
    rows = plan()
    readers, stewards = row(rows, "readers"), row(rows, "stewards")
    assert readers["description"] == "KratosForge: Data readers of the Finance domain (Prod)"
    assert stewards["description"] == "KratosForge: Data stewards of the Finance domain"
    assert readers["fabric_domain"] == "fd-1" and stewards["fabric_domain"] == "fd-1"
    assert row(plan("ops"), "readers")["description"] == "KratosForge: Data readers of the ops domain (Prod)"


def test_the_header_names_the_domain_who_made_it_and_how_many_groups_are_new():
    rows = plan()
    one = entra.powershell(rows[:1], [], by="Ann", at="2026-11-24").splitlines()
    assert one[:2] == ["# KratosForge: Entra ID groups for the Finance domain. Generated 2026-11-24 by Ann.",
                       "# 1 new group. Safe to run again: a group that already exists is left alone."]
    two = entra.az(rows[:2], [], at="2026-11-24").splitlines()
    assert two[1:3] == ["# KratosForge: Entra ID groups for the Finance domain. Generated 2026-11-24.",
                        "# 2 new groups. Safe to run again: a group that already exists is left alone."]
    none = entra.powershell([], [], at="t").splitlines()
    assert none[:2] == ["# KratosForge: Entra ID groups for the  domain. Generated t.",
                        "# 0 new groups. Safe to run again: a group that already exists is left alone."]


def test_only_new_groups_whose_names_a_script_cant_carry_are_left_for_by_hand():
    rows = plan()
    assert not any("Do these by hand" in line for line in entra.powershell(rows, []).splitlines())
    bad = [dict(r, suggested="A&B") for r in rows[:2]]
    bad[1]["linked"] = {"group_id": "g1", "group_name": "A&B"}
    (line,) = [x for x in entra.powershell(bad, []).splitlines() if "Do these by hand" in x]
    assert line.endswith(": Domain owners. Check the platform's group naming.")


def test_the_csv_gives_a_mail_nickname_only_for_a_group_to_make():
    rows = plan(links=[{"domain_id": "fin", "role": "readers", "env": "prod", "group_id": "g1", "group_name": "Readers"}])
    table = list(csv.reader(io.StringIO(entra.table(rows, ["a@x.com"]))))
    by_role = {line[1]: line for line in table[1:]}
    assert by_role["Data readers, Prod"][3:5] == ["Readers", ""]
    assert by_role["Data readers, Prod"][9] == "g1"
    assert by_role["Domain owners"][3:5] == ["SG-FAB-FIN-DOMAIN-ADMINS", "sg-fab-fin-domain-admins"]


def test_the_role_script_names_each_role_it_gives_or_leaves_for_by_hand():
    script = entra.roles(plan("ops"), {}, at="t")
    manual = script.split("# Do these by hand:\n", 1)[1].splitlines()
    assert manual == [
        "# ops isn't in Fabric yet: give SG-FAB-OPS-DOMAIN-ADMINS the Admins role there once it is.",
        "# ops isn't in Fabric yet: give SG-FAB-OPS-DOMAIN-CONTRIBUTORS the Contributors role there once it is.",
        "# No Prod workspace of ops is known yet: give SG-FAB-OPS-WSADMINS-PROD Admin in each one's Manage access.",
        ("# SG-FAB-OPS-READERS-PROD: in each Prod lakehouse that holds ops data products, Manage OneLake data access "
         "> add it to a role that reads the product's tables."),
        "# No Prod workspace of ops is known yet: give SG-FAB-OPS-VIEWERS-PROD Viewer in each one's Manage access.",
    ]


def test_the_role_script_says_which_domain_role_each_group_got():
    script = entra.roles(plan(), {"prod": [{"id": "w1", "name": "Fin Prod"}]}, at="t")
    assert "  Write-Host 'SG-FAB-FIN-DOMAIN-ADMINS is Admins of Finance'" in script.splitlines()
    assert "  Write-Host 'SG-FAB-FIN-DOMAIN-CONTRIBUTORS is Contributors of Finance'" in script.splitlines()
    assert "# Do these by hand:" in script and "SG-FAB-FIN-STEWARDS" not in script.split("# Do these by hand:")[1]
