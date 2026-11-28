"""Building warehouse monitoring views is a Fabric write a person may run again. A second build updates
the queryset the first one made rather than making another, and leaves one set of the app's tabs. With
"auto", a workspace already remembered is not added twice, and an organization that fixed the list
doesn't undo views that were built. Against fakes: nothing reaches Fabric."""

from __future__ import annotations


from fabric_desktop import app_settings
from fabric_desktop import warehouse_monitoring as wm

from test_warehouse_monitoring import DB, WH, WH2, WS, _decoded, svc  # noqa: F401 (svc is a fixture)


def _remembering(fake):
    """Make the fake list what it created, as Fabric does."""
    real = fake.create_kql_queryset

    def create(ws, name, definition, description=""):
        made = real(ws, name, definition, description)
        fake.querysets.append({"id": name, "displayName": name, "description": description})
        return made
    fake.create_kql_queryset = create


def test_building_twice_updates_the_queryset_it_made_and_keeps_one_set_of_tabs(svc):  # noqa: F811
    _remembering(svc.fake)
    first = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}], confirm=True)
    tabs = len(_decoded(svc.fake.definitions["WH_Gold_monitoring"])["tabs"])
    again = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}], confirm=True)
    assert [r["state"] for r in first["results"]] == ["created"]
    assert [r["state"] for r in again["results"]] == ["updated"] and again["failed"] == 0
    assert [c[0] for c in svc.fake.calls].count("create_kql_queryset") == 1
    assert len(svc.fake.querysets) == 1
    assert len(_decoded(svc.fake.definitions["WH_Gold_monitoring"])["tabs"]) == tabs == len(wm.VIEWS)


def test_auto_doesnt_add_a_workspace_it_already_remembers(svc):  # noqa: F811
    svc.settings["wh_monitoring_auto"] = [WS.upper()]
    svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}], confirm=True, auto=True)
    assert svc.settings["wh_monitoring_auto"] == [WS.upper()]


def test_views_built_stay_built_when_the_organization_fixed_the_auto_list(svc, monkeypatch):  # noqa: F811
    def fixed(changes):
        raise ValueError("wh_monitoring_auto is set by your organization")
    monkeypatch.setattr(app_settings, "save", fixed)
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}], confirm=True, auto=True)
    assert [r["state"] for r in out["results"]] == ["created"] and out["failed"] == 0
    assert svc.settings["wh_monitoring_auto"] == []


def test_a_build_that_fails_for_every_warehouse_still_reports_each_one(svc, monkeypatch):  # noqa: F811
    from fabric_desktop.fabric_api import FabricError

    def refused(ws, name, definition, description=""):
        raise FabricError("Fabric is limiting requests", code="TooManyRequests", status=429)
    monkeypatch.setattr(svc.fake, "create_kql_queryset", refused)
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "A"}, {"id": WH2, "name": "B"}],
                                  confirm=True)
    assert [r["state"] for r in out["results"]] == ["failed", "failed"] and out["failed"] == 2
    assert all("limiting" in r["error"] for r in out["results"])
