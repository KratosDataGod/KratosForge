"""App Studio's engine methods (appstudio_service): the remaining edges. The project list skips what isn't a
project, waiting plans are pruned by age and count, a change refused on save stays usable, and a publish check
stops on a model or workspace it can't name. The model and Fabric are fakes; nothing leaves this PC."""

from __future__ import annotations

import os

import pytest

from fabric_desktop import appstudio_service as svc_mod
from fabric_desktop.appstudio import discovery, generate, project
from fabric_desktop.appstudio import providers as prov

from test_appstudio_edit import svc as edit_svc  # noqa: F401 - the fixture, used by name below
from test_appstudio_generate import BIM, ITEM, PLAN, WS
from test_appstudio_generate import svc as plan_svc  # noqa: F401 - the fixture, used by name below
from test_appstudio_publish import _Svc as _PublishSvc


def test_the_project_list_skips_files_links_and_folders_that_arent_projects(tmp_path):
    s = _PublishSvc(tmp_path / "data")
    made = s.appstudio_create("Real one", "blank")["project"]
    root = s._appstudio_root()
    (root / "notes.txt").write_text("x", encoding="utf-8")
    (root / "stray folder").mkdir()
    os.symlink(root / made, root / "Linked.KFApp", target_is_directory=True)
    rows = s.appstudio_home()["projects"]
    assert [r["project"] for r in rows] == [made]


def age(plans, minutes):
    for v in plans.values():
        v["at"] -= minutes * 60


def test_plans_older_than_an_hour_are_dropped_and_only_the_newest_are_kept(plan_svc, monkeypatch):  # noqa: F811
    source = {"workspace_id": WS, "item_id": ITEM, "name": "Sales model"}
    old = plan_svc.appstudio_plan("Sales", source=source)["plan_id"]
    plans = plan_svc._appstudio_plan_store()
    age(plans, svc_mod.PLAN_MINUTES + 1)
    monkeypatch.setattr(svc_mod, "MAX_PLANS", 2)
    first = plan_svc.appstudio_plan("Sales", source=source)["plan_id"]
    assert list(plans) == [first]  # the expired one is gone
    second = plan_svc.appstudio_plan("Sales", source=source)["plan_id"]
    third = plan_svc.appstudio_plan("Sales", source=source)["plan_id"]
    assert set(plans) == {second, third}  # the oldest made room
    with pytest.raises(ValueError, match="expired"):
        plan_svc.appstudio_create_from_plan(old, PLAN)


def test_changes_waiting_are_pruned_by_age_and_count_too(edit_svc, monkeypatch):  # noqa: F811
    edit_svc.reply = {"summary": "s", "changes": [{"action": "set_title", "id": "c_1", "title": "Total"}]}
    plans = edit_svc._appstudio_plan_store()
    plans.clear()
    edit_svc.appstudio_change_plan(edit_svc.project, "rename")
    age(plans, svc_mod.PLAN_MINUTES + 1)
    monkeypatch.setattr(svc_mod, "MAX_PLANS", 1)
    a = edit_svc.appstudio_change_plan(edit_svc.project, "rename")["change_id"]
    assert list(plans) == [a]
    b = edit_svc.appstudio_change_plan(edit_svc.project, "rename")["change_id"]
    assert list(plans) == [b]


def test_a_change_needs_consent_to_send_the_app_to_the_model(edit_svc, monkeypatch):  # noqa: F811
    real = edit_svc._appstudio_model

    def no_consent(conn_id):
        conn = real(conn_id)
        return {**conn, "consent": {**conn["consent"], "context": False}}
    monkeypatch.setattr(edit_svc, "_appstudio_model", no_consent)
    with pytest.raises(prov.ConsentError, match="Agree to send application context"):
        edit_svc.appstudio_change_plan(edit_svc.project, "rename")
    assert edit_svc.sent == []


def test_a_change_refused_when_saved_stays_usable(edit_svc, monkeypatch):  # noqa: F811
    edit_svc.reply = {"summary": "s", "changes": [{"action": "set_title", "id": "c_1", "title": "Total"}]}
    got = edit_svc.appstudio_change_plan(edit_svc.project, "rename")
    real = project.change

    def disk_full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(project, "change", disk_full)
    with pytest.raises(OSError, match="disk full"):
        edit_svc.appstudio_apply_change(edit_svc.project, got["change_id"])
    monkeypatch.setattr(project, "change", real)
    after = edit_svc.appstudio_apply_change(edit_svc.project, got["change_id"])
    assert after["spec"]["pages"][0]["components"][0]["title"] == "Total"


def publish_svc(tmp_path, workspace_id=WS, item_id=ITEM):
    s = _PublishSvc(tmp_path / "data")
    meta = discovery.from_bim(BIM)
    spec = generate.to_spec(generate.check_plan(PLAN, meta, "powerbi-report")["plan"], meta, "powerbi-report",
                            {"kind": "semantic-model", "name": "Sales model", "workspace_id": workspace_id,
                             "item_id": item_id})
    s.project = project.create(s._appstudio_root(), "Sales overview", spec).name
    return s


def test_a_publish_check_stops_when_the_model_isnt_named_by_its_ids(tmp_path):
    s = publish_svc(tmp_path, workspace_id="Sales workspace")
    with pytest.raises(ValueError, match="^The app's semantic model isn't named by its ids. Connect it again.$"):
        s.appstudio_publish_check(s.project)


def test_a_publish_check_needs_a_workspace_id(tmp_path):
    s = publish_svc(tmp_path)
    with pytest.raises(ValueError, match="^Choose the workspace to publish to.$"):
        s.appstudio_publish_check(s.project, workspace_id="not-a-workspace")
    assert s.fabric.writes == []


def test_a_workspace_fabric_doesnt_name_stops_the_check(tmp_path):
    s = publish_svc(tmp_path)
    s.fabric.get_workspace = lambda ws: {"id": ws}
    with pytest.raises(RuntimeError, match="^Fabric didn't say what the workspace is called. Try again.$"):
        s.appstudio_publish_check(s.project)
