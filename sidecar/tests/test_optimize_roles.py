"""Keeping one table family of the central lakehouse from DefaultReader (optimize/roles.py)."""

import polars as pl
import pytest

from fabric_desktop.optimize import roles, schema

FAM = schema.Family("fdtst_", "Access cache", [schema.Migration(1, "Create", {
    "who": {"name": pl.String}, "schema_version": schema.TABLES_V1["schema_version"]})], schema="access")


def reader(paths, **extra):
    return {"name": "DefaultReader", "decisionRules": [{"effect": "Permit", "permission": [
        {"attributeName": "Path", "attributeValueIncludedIn": paths},
        {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}]}],
        "members": {"fabricItemMembers": [{"itemAccess": ["ReadAll"], "sourcePath": "ws/lh"}]}} | extra


def plan(current, schemas=("dbo", "access", "sales"), members=("obj-1",)):
    return roles.restrict_plan(current, FAM, "AccessCacheReaders", list(members), "tenant", list(schemas))


def paths_of(role):
    return role["decisionRules"][0]["permission"][0]["attributeValueIncludedIn"]


def test_everything_becomes_every_other_schema_and_files():
    other = {"name": "SalesTeam", "decisionRules": [], "members": {}}
    out = plan([reader(["*"]), other])
    new_reader, kept, own = out["roles"]
    assert paths_of(new_reader) == ["/Tables/dbo", "/Tables/sales", "/Files"]
    assert new_reader["members"] == reader(["*"])["members"]  # everyone with ReadAll stays a member
    assert kept is other  # other roles are left as they are
    assert own["name"] == "AccessCacheReaders" and paths_of(own) == ["/Tables/access"]
    assert own["members"]["microsoftEntraMembers"] == [{"tenantId": "tenant", "objectId": "obj-1"}]
    assert len(out["changes"]) == 2


def test_an_admins_own_paths_are_kept_and_running_again_changes_nothing():
    out = plan([reader(["/Tables", "/Files/raw"])])
    assert paths_of(out["roles"][0]) == ["/Tables/dbo", "/Tables/sales", "/Files/raw"]
    assert plan(out["roles"])["changes"] == []
    assert plan(out["roles"], members=("obj-2",))["changes"] == ["AccessCacheReaders gets the members you chose"]


def test_it_refuses_rather_than_guess():
    with pytest.raises(PermissionError, match="no DefaultReader"):
        plan([])
    with pytest.raises(PermissionError, match="on purpose"):
        plan([reader(["/Tables/access/who"])])
    with pytest.raises(PermissionError, match="more than one rule"):
        plan([reader(["*"], decisionRules=[{"effect": "Permit"}, {"effect": "Permit"}])])
    with pytest.raises(ValueError, match="reads something else"):
        plan([reader(["*"]), dict(reader(["/Files"]), name="AccessCacheReaders")])
    with pytest.raises(ValueError, match="schema of its own"):
        roles.restrict_plan([reader(["*"])], schema.OPTIMIZE, "X", ["o"], "t", ["dbo"])


def test_a_second_family_can_be_restricted_after_the_first(monkeypatch):
    other = schema.Family("fdtwo_", "Second", FAM.migrations, schema="second")
    monkeypatch.setitem(schema.FAMILIES, FAM.prefix, FAM)
    monkeypatch.setitem(schema.FAMILIES, other.prefix, other)
    lake = ["dbo", "access", "second", "sales"]
    first = plan([reader(["*"])], schemas=lake)
    assert paths_of(first["roles"][0]) == ["/Tables/dbo", "/Tables/sales", "/Files"]  # no family schema
    second = roles.restrict_plan(first["roles"], other, "SecondReaders", ["obj-2"], "tenant", lake)
    assert paths_of(second["roles"][0]) == ["/Tables/dbo", "/Tables/sales", "/Files"]
    assert [r["name"] for r in second["roles"]] == ["DefaultReader", "AccessCacheReaders", "SecondReaders"]
    assert second["changes"] == ["New role SecondReaders reads /Tables/second"]


def test_fields_fabric_adds_dont_count_as_a_change():
    out = plan([reader(["*"])])
    listed = [dict(r, id=f"id-{i}", eTag=f'"e{i}"') for i, r in enumerate(out["roles"])]  # as List returns them
    again = plan(listed)
    assert again["changes"] == []
    assert again["roles"][1] is listed[1]  # the role is left exactly as Fabric returned it
