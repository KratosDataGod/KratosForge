"""Views, filters, grouping and paging over workload records (workloads/query.py)."""

import pytest

from fabric_desktop.workloads import query
from fabric_desktop.workloads.model import HEALTH, HEALTH_LABELS


def _w(name, health="healthy", envs=(), where="this_pc", owners=None, kind="notebook", **extra):
    deps = [{"env": e, "env_label": e.title(), "location": {"kind": where, "label": "This PC"}} for e in envs] or \
        [{"location": {"kind": where, "label": "Fabric" if where == "fabric" else "This PC"}}]
    return {"id": name, "name": name, "kind": kind, "kind_label": kind.title(), "group": {"id": "p", "label": "p"},
            "health": health, "health_label": HEALTH_LABELS[health], "health_rank": HEALTH.index(health),
            "readiness": {"state": "ready"}, "unowned": not owners, "partial": False, "capabilities": [],
            "findings": [], "owners": owners or {}, "deployments": deps, **extra}


ROWS = [
    _w("a_prod", "failing", envs=("dev", "prod"), owners={"team": {"value": "Sales"}}),
    _w("b_test", "late", envs=("dev", "test"), owners={"technical_owner": {"value": "chris@contoso.com"}}),
    _w("c_dev", envs=("dev",), owners={"team": {"value": "me", "placeholder": True}}),
    _w("d_fabric", "unknown", where="fabric", kind="pipeline"),
    _w("e_pc", "idle"),
]


def _names(page):
    return [r["name"] for r in page["rows"]]


def test_each_view_and_its_count():
    page = query.select(ROWS, view="all", user="chris@contoso.com")
    counts = {v["id"]: v["count"] for v in page["views"]}
    assert counts == {"attention": 2, "mine": 1, "unowned": 2, "this_pc": 4, "fabric": 1, "production": 1, "all": 5}
    assert _names(query.select(ROWS, view="mine", user="CHRIS@Contoso.com")) == ["b_test"]  # any case
    team_chris = [_w("f", owners={"team": {"value": "Chris"}})]
    assert _names(query.select(team_chris, view="mine", user="chris@contoso.com")) == ["f"]  # the name before the @
    assert _names(query.select(ROWS, view="mine")) == []  # nobody signed in: nothing is theirs
    assert _names(query.select(ROWS, view="production")) == ["a_prod"]
    with pytest.raises(ValueError, match="view is one of"):
        query.select(ROWS, view="broken")


def test_filters_by_kind_environment_location_and_words():
    assert _names(query.select(ROWS, kind="pipeline")) == ["d_fabric"]
    assert _names(query.select(ROWS, env="test")) == ["b_test"]
    assert _names(query.select(ROWS, location="fabric")) == ["d_fabric"]
    assert _names(query.select(ROWS, query="SALES failing")) == ["a_prod"]  # every word, any case
    assert _names(query.select(ROWS, query="sales late")) == []


def test_grouping_by_environment_reaches_the_highest_one():
    page = query.select(ROWS, group="environment")
    assert [(g["id"], g["label"], g["count"]) for g in page["groups"]] == [
        ("env:dev", "Dev only", 1), ("env:fabric", "Fabric only", 1), ("env:prod", "Reaches Prod", 1),
        ("env:test", "Reaches Test", 1), ("env:pc", "This PC", 1)]


def test_grouping_by_owner_puts_placeholders_with_no_owner_last():
    page = query.select(ROWS, group="owner")
    assert [g["label"] for g in page["groups"]] == ["chris@contoso.com", "Sales", "No owner"]
    nobody = page["groups"][-1]
    assert nobody["count"] == 3 and nobody["health"]["unknown"] == 1 and nobody["health"]["idle"] == 1


def test_grouping_by_kind_and_none_and_a_bad_group():
    assert [g["label"] for g in query.select(ROWS, group="kind")["groups"]] == ["Notebook", "Pipeline"]
    assert [g["label"] for g in query.select(ROWS, group="none")["groups"]] == ["All workloads"]
    with pytest.raises(ValueError, match="group is one of"):
        query.select(ROWS, group="colour")


def test_paging_is_bounded_and_rows_keep_their_group():
    page = query.select(ROWS, group="none", offset=-5, limit=0)
    assert (page["offset"], page["limit"], page["total"]) == (0, 1, 5)
    assert _names(page) == ["a_prod"] and page["rows"][0]["group_key"] == "all"  # worst health first
    assert query.select(ROWS, limit=10_000)["limit"] == 500
    assert _names(query.select(ROWS, group="none", offset=4, limit=10)) == ["e_pc"]


def test_a_row_carries_its_newest_run_and_worst_finding():
    w = _w("x", "failing", envs=("dev", "prod"))
    w["deployments"][0]["latest_run"] = {"state": "ok", "started_at": "2026-11-06T08:00:00Z"}
    w["deployments"][1]["latest_run"] = {"state": "failed", "started_at": "2026-11-06T09:00:00Z"}
    w["findings"] = [{"level": "notice", "title": "n"}, {"level": "failing", "title": "f"}]
    r = query.row(w)
    assert r["last_run"]["state"] == "failed" and r["headline"]["title"] == "f"
    assert query.row(_w("y"))["last_run"] is None and query.row(_w("y"))["headline"] is None
