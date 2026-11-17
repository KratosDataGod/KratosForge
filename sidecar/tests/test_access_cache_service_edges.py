"""Access cache: the edges the main tests don't reach. The same fakes stand in for Fabric and OneLake."""

from fabric_desktop.service import Service
from tests.test_access_cache import lake, svc  # noqa: F401 - the fixture is used by name


def test_with_no_central_lakehouse_the_cache_says_to_connect_one(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(Service, "central_lakehouse", lambda self: None)
    out = svc.access_cache()
    assert out["ready"] is False and out["state"] is None and out["workspaces"] == []
    assert "central lakehouse first" in out["message"]


def test_a_refresh_without_lakehouses_reads_only_workspace_roles(svc):  # noqa: F811
    asked = []
    svc.onelake.list_data_access_roles = lambda ws, item: asked.append(item) or []
    out = svc.access_cache_refresh(approve=True, lakehouses=False)
    assert out["problems"] == 0 and asked == []
    assert not lake(svc).has("fdacc_lakehouse_roles") or lake(svc).rows("fdacc_lakehouse_roles").height == 0


def test_a_workspace_whose_lakehouses_cant_be_listed_counts_as_a_problem(svc):  # noqa: F811
    def refuse(ws):
        if ws == "w1":
            raise RuntimeError("503 busy")
        return []

    svc.fake.list_lakehouses = refuse
    out = svc.access_cache_refresh(approve=True)
    assert out["problems"] == 1
    sales = next(w for w in svc.access_cache()["workspaces"] if w["workspace_id"] == "w1")
    assert [p["name"] for p in sales["people"]] == ["Ann", "Sales readers"]  # the roles were still read
