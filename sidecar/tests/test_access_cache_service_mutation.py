"""Mutation batch 69: access_cache_service.py, the edges its other tests leave open
(docs/quality/mutation-testing.md). The same fakes stand in for Fabric and OneLake."""

from __future__ import annotations

import pytest

from fabric_desktop.optimize import setup
from fabric_desktop.service import Service
from tests.test_access_cache import svc  # noqa: F401 - the fixture is used by name


def test_the_cache_names_each_workspace_and_lakehouse_as_fabric_does(svc):  # noqa: F811
    svc.access_cache_refresh(approve=True)
    got = {w["workspace_id"]: w for w in svc.access_cache()["workspaces"]}
    assert got["w1"]["workspace_name"] == "Sales" and got["w2"]["workspace_name"] == "HR"
    assert [lh["item_name"] for lh in got["w1"]["lakehouses"]] == ["LH_Sales"]


def test_a_lakehouse_with_no_name_is_kept_with_an_empty_one(svc):  # noqa: F811
    svc.fake.lakehouses["w1"] = [{"id": "lh1"}]
    svc.access_cache_refresh(approve=True)
    sales = next(w for w in svc.access_cache()["workspaces"] if w["workspace_id"] == "w1")
    assert [(lh["item_id"], lh["item_name"]) for lh in sales["lakehouses"]] == [("lh1", "")]


def test_a_connection_that_doesnt_say_reads_the_lakehouse_with_schemas(svc, monkeypatch):  # noqa: F811
    conn = {k: v for k, v in svc.central_lakehouse().items() if k != "schemas"}
    monkeypatch.setattr(Service, "central_lakehouse", lambda self: dict(conn))
    asked = []
    opened = Service._optimize_lake
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None: asked.append(schemas) or opened(self, ws, lh, schemas))
    svc.access_cache()
    assert asked == [True]


@pytest.mark.parametrize("access, can_write", [("write", True), ("read", False), (None, False)])
def test_before_the_tables_are_set_up_the_cache_isnt_ready_and_says_whether_this_pc_may_write(
        svc, monkeypatch, access, can_write):  # noqa: F811
    conn = svc.central_lakehouse() | {"access": access}
    monkeypatch.setattr(Service, "central_lakehouse", lambda self: dict(conn))
    out = svc.access_cache()
    assert out["ready"] is False and out["state"] not in (setup.READY, setup.NEWER)
    assert out["workspaces"] == [] and out["message"]
    assert out["can_write"] is can_write


@pytest.mark.parametrize("access, can_write", [("write", True), ("read", False), (None, False)])
def test_once_set_up_the_cache_is_ready_and_says_whether_this_pc_may_write(svc, monkeypatch, access, can_write):  # noqa: F811
    svc.access_cache_refresh(approve=True)
    conn = svc.central_lakehouse() | {"access": access}
    monkeypatch.setattr(Service, "central_lakehouse", lambda self: dict(conn))
    out = svc.access_cache()
    assert out["ready"] is True and out["state"] in (setup.READY, setup.NEWER) and out["message"] is None
    assert out["can_write"] is can_write


@pytest.mark.parametrize("members", [[], [" ", ""], None])
def test_choosing_no_one_is_refused_even_when_confirming(svc, members):  # noqa: F811
    svc.access_cache_refresh(approve=True)
    with pytest.raises(ValueError, match="Pick at least one person or group"):
        svc.access_cache_protect(members, approve=True)
    with pytest.raises(ValueError, match="Pick at least one person or group"):
        svc.access_cache_protect(members, approve=True, shown="anything")
    assert svc.onelake.puts == []
