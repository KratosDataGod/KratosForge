"""warehouse_monitoring_service.py paths that mutation testing (batch 74,
docs/quality/mutation-testing.md) showed no test pinned: how many databases are tried and in which
order, the probe row read, the views listed only once monitoring is found, the problem cap, a view
whose query fails for another reason, the 100-warehouse limit, the queryset id kept, and a remembered
workspace list that grows."""

from __future__ import annotations

import pytest

from fabric_desktop import warehouse_monitoring_service as wms
from tests.test_warehouse_monitoring import DB, URI, WH, WS, Resp, _table, svc  # noqa: F401  # svc is a fixture

MISSING = Resp(400, {"error": {"message": "'WarehouseExecutions' failed to resolve table or column"}})


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _dbs(svc, names):  # noqa: F811
    dbs = [{"id": f"bbbbbbbb-0000-1111-2222-{i:012d}", "displayName": n, "properties": {"queryServiceUri": URI}}
           for i, n in enumerate(names)]
    svc.fake.dbs.update({d["id"]: d for d in dbs})
    svc.fake.list_kql_databases = lambda ws: list(dbs)
    return dbs


def test_a_queryset_is_ours_only_when_its_note_starts_and_ends_as_the_apps_does():
    assert wms._ours({"description": wms.QUERYSET_NOTE.format(name="Sales")})
    assert not wms._ours({"description": "Monitoring views for Sales, by hand."})
    assert not wms._ours({"description": "Mine" + wms.QUERYSET_NOTE.split("{name}")[1]})


def test_at_most_ten_databases_are_tried(svc):  # noqa: F811
    _dbs(svc, [f"Db{i:02d}" for i in range(12)])
    svc.post.answers = [MISSING] * 12
    out = svc.wh_monitoring_status(WS)
    assert wms.PROBES == 10 and len(svc.post.sent) == 10 and out["monitored"] is False


def test_a_database_named_like_a_monitoring_database_is_tried_first(svc):  # noqa: F811
    dbs = _dbs(svc, ["Alpha", "Sales_Database"])
    out = svc.wh_monitoring_status(WS)
    assert out["database"]["item_id"] == dbs[1]["id"] and len(svc.post.sent) == 1


def test_the_first_probe_row_gives_when_and_how_many(svc):  # noqa: F811
    svc.post.answers = [MISSING, Resp(200, _table(["LastSeen", "Items"], [["t1", 5], ["t2", 6]]))]
    found = svc.wh_monitoring_status(WS)["database"]
    assert (found["last_seen"], found["items"]) == ("t1", 5)


def test_a_probe_with_no_rows_is_monitored_with_nothing_seen(svc):  # noqa: F811
    svc.post.answers = [MISSING, Resp(200, _table(["LastSeen", "Items"], []))]
    found = svc.wh_monitoring_status(WS)["database"]
    assert (found["last_seen"], found["items"]) == (None, 0)


def test_the_views_are_listed_once_monitoring_is_found_and_not_before(svc):  # noqa: F811
    svc.fake.querysets = [{"displayName": "Sales_monitoring", "id": "q1",
                           "description": wms.QUERYSET_NOTE.format(name="Sales")}]
    assert svc.wh_monitoring_status(WS)["querysets"] == {"Sales_monitoring": "q1"}
    svc.post.answers = [MISSING, MISSING]
    assert svc.wh_monitoring_status(WS)["querysets"] == {}


def test_only_three_problems_are_reported(svc):  # noqa: F811
    _dbs(svc, [f"Db{i}" for i in range(5)])
    svc.post.answers = [Resp(403, {"error": {"message": f"no access {i}"}}) for i in range(5)]
    out = svc.wh_monitoring_status(WS)
    assert len(out["problems"]) == 3 and out["monitored"] is False


def test_a_view_with_nothing_recorded_yet_is_empty_and_not_cut_short(svc):  # noqa: F811
    svc.post.answers = [MISSING]
    out = svc.wh_monitoring_view(WS, DB, WH)
    assert out["truncated"] is False and out["rows"] == []


def test_a_view_whose_query_fails_for_another_reason_says_so(svc):  # noqa: F811
    svc.post.answers = [Resp(403, {"error": {"message": "Forbidden"}})]
    with pytest.raises(RuntimeError):
        svc.wh_monitoring_view(WS, DB, WH)


def _warehouses(n):
    return [{"id": f"aaaaaaaa-bbbb-cccc-dddd-{i:012d}", "name": f"W{i}"} for i in range(n)]


def test_a_hundred_warehouses_are_built_and_a_hundred_and_one_refused(svc):  # noqa: F811
    out = svc.wh_monitoring_build(WS, WS, DB, _warehouses(100), confirm=True)
    assert out["failed"] == 0 and len(out["results"]) == 100
    with pytest.raises(ValueError, match="between 1 and 100"):
        svc.wh_monitoring_build(WS, WS, DB, _warehouses(101), confirm=True)


def test_a_created_queryset_keeps_the_id_fabric_gave_it_or_none(svc):  # noqa: F811
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "Sales"}], confirm=True)
    assert out["results"][0]["item_id"] == "q-Sales_monitoring"
    svc.fake.create_kql_queryset = lambda *a, **k: {"name": "no id"}
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "Other"}], confirm=True)
    assert out["results"][0]["item_id"] == ""


def test_a_remembered_workspace_is_added_to_the_ones_already_there(svc):  # noqa: F811
    svc.settings["wh_monitoring_auto"] = ["99999999-2222-3333-4444-555555555555"]
    svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "Sales"}], confirm=True, auto=True)
    assert svc.settings["wh_monitoring_auto"] == ["99999999-2222-3333-4444-555555555555", WS]


def test_databases_of_the_same_kind_are_tried_in_name_order(svc):  # noqa: F811
    dbs = _dbs(svc, ["Zed", "Alpha"])
    out = svc.wh_monitoring_status(WS)
    assert out["database"]["item_id"] == dbs[1]["id"] and len(svc.post.sent) == 1
