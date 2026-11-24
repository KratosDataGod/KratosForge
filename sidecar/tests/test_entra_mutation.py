"""central/entra.py: what mutation testing showed no test checked. Naming by a domain's name or id, the 256 and
64 character limits, roles switched off, a domain with no name or Fabric domain, the description's environment,
the scripts' header (domain, author, one group or several, the by-hand line), the CSV for a linked group, and
the by-hand steps of the roles script, each naming the role it gives."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.central import entra

NAMING = {"pattern": "{PREFIX}-{DOMAIN}-{SUBDOMAIN}-{ROLE}-{ENV}", "prefix": "SG", "case": "upper", "words": {}}
DOMAINS = [{"id": "sales", "name": "Sales Team", "fabric_domain": "d1"},
           {"id": "hr"}]  # no name and no Fabric domain


def rows(domain="sales", naming=NAMING, links=(), **kw):
    return entra.plan(DOMAINS, domain, list(links), naming, **kw)


def test_roles_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        entra.ROLES[0].label = "Owners"  # type: ignore[misc]


def test_a_group_is_named_by_the_domains_id_unless_the_naming_says_name():
    assert rows()[0]["suggested"] == "SG-SALES-DOMAIN_ADMINS"
    assert rows(naming={**NAMING, "domain_part": "name"})[0]["suggested"] == "SG-SALES-TEAM-DOMAIN_ADMINS"


def test_names_and_mail_nicknames_are_cut_to_entras_limits():
    long = {**NAMING, "prefix": "P" * 300}
    name = rows(naming=long)[0]["suggested"]
    assert len(name) == 256 and name == "P" * 256
    assert entra.mail_nickname("a" * 100) == "a" * 64


def test_an_unknown_domain_is_refused():
    with pytest.raises(ValueError, match="nope isn't a domain"):
        rows("nope")


def test_roles_switched_off_are_left_out():
    keys = {r["role"] for r in rows(naming={**NAMING, "roles_off": ["stewards", "readers"]})}
    assert "stewards" not in keys and "readers" not in keys and "viewers" in keys


def test_a_domain_with_no_name_or_fabric_domain():
    first = rows("hr")[0]
    assert first["domain"] == "hr" and first["fabric_domain"] == ""
    assert first["description"] == "KratosForge: Domain owners of the hr domain"
    assert first["who"] == "The people accountable for hr data"


def test_an_environments_description_names_it():
    dev = next(r for r in rows() if r["env"] == "dev")
    assert dev["description"] == "KratosForge: Workspace admins of the Sales Team domain (Dev)"
    assert rows()[0]["description"] == "KratosForge: Domain owners of the Sales Team domain"


def test_the_header_names_the_domain_the_author_and_how_many_groups():
    plan = rows()
    one = [plan[0]]
    assert entra._header(one, "Ana", "2026-11-23")[:2] == [
        "# KratosForge: Entra ID groups for the Sales Team domain. Generated 2026-11-23 by Ana.",
        "# 1 new group. Safe to run again: a group that already exists is left alone."]
    assert entra._header(plan[:2], "", "2026-11-23")[:2] == [
        "# KratosForge: Entra ID groups for the Sales Team domain. Generated 2026-11-23.",
        "# 2 new groups. Safe to run again: a group that already exists is left alone."]
    mixed = [{**plan[0], "domain": "First"}, {**plan[1], "domain": "Second"}]
    assert "for the First domain" in entra._header(mixed, "", "x")[0]
    assert entra._header([], "", "x")[0] == "# KratosForge: Entra ID groups for the  domain. Generated x."


def test_only_an_unlinked_name_that_cant_be_scripted_is_done_by_hand():
    plan = rows()[:2]
    linked = {**plan[0], "suggested": "has space", "linked": {"group_id": "g1", "group_name": "Owners"}}
    assert entra._unscriptable([linked, plan[1]]) == []
    bad = {**plan[1], "suggested": "has space"}
    assert entra._unscriptable([linked, bad]) == [
        ("# Do these by hand: these names have characters a script can't carry safely (only letters, digits, -, _ "
         "and .): Domain contributors. Check the platform's group naming.")]


def test_the_csv_leaves_a_linked_groups_mail_nickname_empty():
    plan = rows()[:2]
    linked = {**plan[0], "linked": {"group_id": "g1", "group_name": "Owners"}}
    lines = entra.table([linked, plan[1]], []).splitlines()
    assert lines[1].split(",")[3:5] == ["Owners", ""] and lines[1].endswith(",g1")
    assert lines[2].split(",")[4] == plan[1]["mail_nickname"]


def test_the_roles_script_names_each_role_it_gives_and_what_is_by_hand():
    script = entra.roles(rows(), {"dev": [{"id": "w1", "name": "Sales Dev"}]})
    assert "Write-Host 'SG-SALES-DOMAIN_ADMINS is Admins of Sales Team'" in script
    assert "Write-Host 'SG-SALES-DOMAIN_CONTRIBUTORS is Contributors of Sales Team'" in script
    assert ("# No Test workspace of Sales Team is known yet: give SG-SALES-WORKSPACE_ADMINS-TEST Admin in each "
            "one's Manage access.") in script
    assert ("# SG-SALES-READERS-PROD: in each Prod lakehouse that holds Sales Team data products, Manage OneLake data "
            "access > add it to a role that reads the product's tables.") in script
    assert "STEWARDS:" not in script
    hr = entra.roles(rows("hr"), {})
    assert "# hr isn't in Fabric yet: give SG-HR-DOMAIN_ADMINS the Admins role there once it is." in hr
    assert "# hr isn't in Fabric yet: give SG-HR-DOMAIN_CONTRIBUTORS the Contributors role there once it is." in hr
