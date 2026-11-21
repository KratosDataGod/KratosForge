"""App Studio's service (appstudio_service.py), the paths the other tests leave: a home folder holding things that
aren't projects, plans and changes that expired or were pushed out by newer ones, a change whose model hasn't
been given consent, a change that fails to save and can be approved again, and publish checks stopped by a model
not named by its ids, a workspace not chosen, and a workspace Fabric doesn't name. The model and Fabric are
stand-ins, as in the main tests."""

from __future__ import annotations

import time

import pytest

from fabric_desktop import appstudio_service
from fabric_desktop.appstudio import discovery, generate, project
from fabric_desktop.appstudio import providers as prov
from test_appstudio_edit import svc  # noqa: F401 - svc is a fixture
from test_appstudio_generate import BIM, ITEM, PLAN, WS
from test_appstudio_publish import _Svc as _PublishSvc

SOURCE = {"workspace_id": WS, "item_id": ITEM, "name": "Sales model"}
RENAME = {"summary": "s", "changes": [{"action": "set_title", "id": "c_1", "title": "Total"}]}


def test_home_lists_only_project_folders(svc, tmp_path):  # noqa: F811
    root = svc._appstudio_root()
    (root / "notes.txt").write_text("x")
    (root / "Loose folder").mkdir()
    (root / "Lookalike.KFApp").write_text("a file, not a folder")
    assert [r["project"] for r in svc.appstudio_home()["projects"]] == [svc.project]


def test_an_expired_plan_is_dropped_and_the_oldest_makes_room_for_a_new_one(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(appstudio_service, "MAX_PLANS", 2)
    plans = svc._appstudio_plan_store()
    now = time.monotonic()
    plans.update({"stale": {"at": now - appstudio_service.PLAN_MINUTES * 60 - 1},
                  "older": {"at": now - 10}, "newer": {"at": now - 5}})
    svc.reply = PLAN
    got = svc.appstudio_plan("Sales", source=SOURCE)
    assert set(plans) == {"newer", got["plan_id"]}


def test_an_expired_change_is_dropped_and_the_oldest_makes_room_for_a_new_one(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(appstudio_service, "MAX_PLANS", 2)
    plans = svc._appstudio_plan_store()
    now = time.monotonic()
    plans.update({"stale": {"at": now - appstudio_service.PLAN_MINUTES * 60 - 1},
                  "older": {"at": now - 10}, "newer": {"at": now - 5}})
    svc.reply = RENAME
    got = svc.appstudio_change_plan(svc.project, "rename")
    assert set(plans) == {"newer", got["change_id"]}
    assert plans[got["change_id"]]["kind"] == "change"


def test_a_change_needs_consent_to_send_the_apps_context(svc):  # noqa: F811
    conn = svc._appstudio_model("")
    svc.appstudio_connection_save({"id": conn["id"], "name": "Contoso", "provider": "openai-compatible",
                                   "model": "m1", "endpoint": "https://llm.contoso.com/v1",
                                   "key_ref": "credman:KratosForge/c", "consent": {"context": False, "sample_rows": False}})
    with pytest.raises(prov.ConsentError, match="^Agree to send application context to this model first"):
        svc.appstudio_change_plan(svc.project, "rename")
    assert svc.sent == [], "nothing went to the model"


def test_a_change_that_fails_to_save_can_be_approved_again(svc, monkeypatch):  # noqa: F811
    svc.reply = RENAME
    got = svc.appstudio_change_plan(svc.project, "rename")
    real = project.change

    def full(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(project, "change", full)
    with pytest.raises(OSError, match="disk full"):
        svc.appstudio_apply_change(svc.project, got["change_id"])
    monkeypatch.setattr(project, "change", real)
    opened = svc.appstudio_apply_change(svc.project, got["change_id"])
    assert opened["spec"]["pages"][0]["components"][0]["title"] == "Total"
    with pytest.raises(ValueError, match="expired"):
        svc.appstudio_apply_change(svc.project, got["change_id"])


def _publisher(tmp_path, source):
    s = _PublishSvc(tmp_path / "data")
    meta = discovery.from_bim(BIM)
    checked = generate.check_plan(PLAN, meta, "powerbi-report")
    spec = generate.to_spec(checked["plan"], meta, "powerbi-report", {"kind": "semantic-model", **source})
    s.project = project.create(s._appstudio_root(), "Sales overview", spec).name
    return s


def test_a_model_not_named_by_its_ids_isnt_published(tmp_path):
    s = _publisher(tmp_path, {"name": "Sales model", "workspace_id": WS, "item_id": "Sales model"})
    with pytest.raises(ValueError, match="^The app's semantic model isn't named by its ids. Connect it again.$"):
        s.appstudio_publish_check(s.project)
    assert s.fabric.writes == []


def test_a_workspace_that_isnt_chosen_or_isnt_named_stops_the_check(tmp_path):
    s = _publisher(tmp_path, SOURCE)
    with pytest.raises(ValueError, match="^Choose the workspace to publish to.$"):
        s.appstudio_publish_check(s.project, workspace_id="Sales dev")
    s.fabric.get_workspace = lambda ws: {"id": ws}
    with pytest.raises(RuntimeError, match="^Fabric didn't say what the workspace is called. Try again.$"):
        s.appstudio_publish_check(s.project)
    s.fabric.get_workspace = lambda ws: None
    with pytest.raises(RuntimeError, match="didn't say what the workspace is called"):
        s.appstudio_publish_check(s.project)
    assert s.fabric.writes == []
