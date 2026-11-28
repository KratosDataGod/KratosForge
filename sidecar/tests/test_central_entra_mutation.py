"""Mutation batch 374: central/entra.py's suggested group names, plans and generated scripts."""

import csv
import dataclasses
import io
import json

import pytest

from fabric_desktop.central import core, entra


def naming(**kw):
    out = json.loads(json.dumps(core.DEFAULT_GROUP_NAMING))
    out.update(kw)
    return out


SALES = {"id": "sales", "name": "Sales EMEA", "owners": "omar@contoso.com"}


def test_a_role_cant_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        entra.ROLES[0].label = "Owners"


def test_the_domain_part_is_its_id_unless_the_naming_says_name():
    n = naming(pattern="{DOMAIN}-{ROLE}", case="as_typed", words={"creators": "Build"})
    assert entra.group_name(n, "creators", SALES, None) == "sales-Build"
    assert entra.group_name(dict(n, domain_part="name"), "creators", SALES, None) == "Sales-EMEA-Build"
    assert entra.group_name(dict(n, domain_part="other"), "creators", SALES, None) == "sales-Build"


def test_a_name_is_cut_to_256_characters_and_a_nickname_to_64():
    n = naming(pattern="{PREFIX}-{DOMAIN}-{ROLE}", prefix="p" * 300, case="lower")
    assert len(entra.group_name(n, "creators", SALES, None)) == 256
    assert entra.mail_nickname("A" * 100) == "a" * 64


def test_a_domain_that_isnt_there_is_refused():
    with pytest.raises(ValueError, match="nope isn't a domain"):
        entra.plan([SALES], "nope", [], naming())


def test_a_naming_without_roles_off_plans_every_role():
    n = naming()
    n.pop("roles_off", None)
    roles = {r["role"] for r in entra.plan([SALES], "sales", [], n)}
    assert roles == set(core.ENTRA_ROLES)


def test_a_domain_without_a_name_is_called_by_its_id_and_env_words_only_when_per_environment():
    rows = entra.plan([{"id": "hr"}], "hr", [], naming(), envs=["dev"])
    by = {(r["role"], r["env"]): r for r in rows}
    assert by[("creators", "dev")]["domain"] == "hr"
    assert by[("creators", "dev")]["description"] == "KratosForge: Creators of the hr domain (Dev)"
    assert by[("domain_admins", "")]["description"] == "KratosForge: Domain owners of the hr domain"
    assert by[("creators", "dev")]["who"] == "hr data engineers and analysts who build"
    assert by[("creators", "dev")]["fabric_domain"] == ""
    in_fabric = entra.plan([dict(SALES, fabric_domain="d1")], "sales", [], naming(), envs=["dev"])
    assert {r["fabric_domain"] for r in in_fabric} == {"d1"} and {r["domain"] for r in in_fabric} == {"Sales EMEA"}


def _row(label="Creators, Dev", suggested="SALES-CREATORS", linked=None, **kw):
    return {"domain": "Sales", "label": label, "suggested": suggested, "linked": linked, "env": "dev",
            "mail_nickname": "sales-creators", "description": "d", "who": "w", "gets": "g", "role": "creators",
            "fabric": None, "fabric_domain": "", **kw}


def test_only_new_groups_with_unsafe_names_are_left_for_by_hand():
    bad_linked = _row(label="Linked", suggested="a&b", linked={"group_id": "g1", "group_name": "Theirs"})
    bad_new = _row(label="New bad", suggested="a&b")
    good = _row(label="Good")
    lines = entra._unscriptable([bad_linked, bad_new, good])
    assert len(lines) == 1 and ": New bad. Check" in lines[0]
    assert entra._unscriptable([bad_linked, good]) == []


def test_the_header_names_the_first_rows_domain_who_made_it_and_counts_new_groups():
    one = entra._header([_row(domain="Sales"), _row(domain="Other", linked={"group_id": "g", "group_name": "G"})], "ana", "now")
    assert one[0] == "# KratosForge: Entra ID groups for the Sales domain. Generated now by ana."
    assert one[1].startswith("# 1 new group. Safe")
    two = entra._header([_row(), _row()], "", "now")
    assert two[0].endswith("Generated now.") and two[1].startswith("# 2 new groups. Safe")
    assert entra._header([], "", "now")[0] == "# KratosForge: Entra ID groups for the  domain. Generated now."
    assert entra._header([], "", "now")[1].startswith("# 0 new groups.")


def test_the_table_leaves_a_linked_groups_nickname_empty():
    linked = _row(linked={"group_id": "g1", "group_name": "Theirs"})
    out = list(csv.reader(io.StringIO(entra.table([_row(), linked], ["omar@contoso.com"]))))
    assert out[1][3:5] == ["SALES-CREATORS", "sales-creators"]
    assert out[2][3:5] == ["Theirs", ""]


def test_the_roles_script_names_each_fabric_role_and_the_readers_steps():
    rows = [_row(label="Domain owners", role="domain_admins", env="", fabric=["domain", "Admins"], fabric_domain=""),
            _row(label="Domain contributors", role="domain_contributors", env="", suggested="SALES-CONTRIB",
                 fabric=["domain", "Contributors"], fabric_domain="d1"),
            _row(label="Viewers, Prod", role="viewers", env="prod", suggested="SALES-VIEWERS", fabric=["workspace", "Viewer"]),
            _row(label="Readers, Prod", role="readers", env="prod", suggested="SALES-READERS"),
            _row(label="Stewards", role="stewards", env="", suggested="SALES-STEWARDS")]
    out = entra.roles(rows, {})
    assert "give SALES-CREATORS the Admins role there once it is." in out
    assert "Write-Host 'SALES-CONTRIB is Contributors of Sales'" in out
    assert "give SALES-VIEWERS Viewer in each one's Manage access." in out
    assert "# SALES-READERS: in each Prod lakehouse" in out
    assert "SALES-STEWARDS" not in out
