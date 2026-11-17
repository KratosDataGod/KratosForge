"""App Studio service edges the main tests don't reach: a broken project in the list, an export too
large, a project that stopped validating, plan and change requests refused before any model is asked,
and the hint lookup after a failed publish. Nothing reaches a model or Fabric."""

from __future__ import annotations

import base64

import pytest

from fabric_desktop.appstudio import generate, project
from fabric_desktop.appstudio import spec as specs
from fabric_desktop.appstudio_service import MAX_DESCRIPTION, AppStudioMixin


class _Svc(AppStudioMixin):
    def __init__(self, root):
        self.root = root


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return _Svc(tmp_path / "data")


def _break(svc, name):
    (svc._appstudio_root() / name / project.SPEC).write_text('{"not": "an app"}', encoding="utf-8")


def test_a_project_that_cant_be_opened_is_listed_as_broken(svc):
    svc.appstudio_create("Good", "blank")
    svc.appstudio_create("Bad", "blank")
    (svc._appstudio_root() / "Bad.KFApp" / project.META).write_text("{not json", encoding="utf-8")
    rows = {r["project"]: r for r in svc.appstudio_home()["projects"]}
    assert rows["Bad.KFApp"] == {"project": "Bad.KFApp", "name": "Bad", "broken": True}
    assert "broken" not in rows["Good.KFApp"]


def test_an_export_larger_than_a_project_can_be_isnt_imported(svc, monkeypatch):
    monkeypatch.setattr(project, "MAX_EXPORT_BYTES", 4)
    with pytest.raises(ValueError, match="larger than an App Studio project can be"):
        svc.appstudio_import(base64.b64encode(b"12345").decode(), "x")


def test_a_project_that_stopped_validating_isnt_previewed_planned_or_published(svc):
    svc.appstudio_create("Sales", "sales")
    _break(svc, "Sales.KFApp")
    with pytest.raises(specs.SpecError):
        svc.appstudio_preview("Sales.KFApp")
    with pytest.raises(specs.SpecError):
        svc.appstudio_change_plan("Sales.KFApp", "Add a chart")
    with pytest.raises(specs.SpecError):
        svc.appstudio_publish_check("Sales.KFApp")


def test_a_plan_for_a_target_app_studio_doesnt_make_is_refused(svc):
    with pytest.raises(ValueError, match="target must be one of"):
        svc.appstudio_plan("A sales app", target="excel")


def test_a_change_request_too_long_is_refused_before_the_project_is_read(svc):
    with pytest.raises(ValueError, match=f"under {MAX_DESCRIPTION} characters"):
        svc.appstudio_change_plan("Missing.KFApp", "x" * (MAX_DESCRIPTION + 1))
    with pytest.raises(ValueError, match="Say what to change first"):
        svc.appstudio_change_plan("Missing.KFApp", "   ")


def test_an_app_without_a_semantic_model_uses_the_sample_fields(svc):
    assert svc._appstudio_project_metadata({"sources": [{"kind": "sample"}]}, False) == generate.sample_metadata()


def test_the_left_behind_lookup_after_a_failed_publish_never_hides_the_failure(svc):
    class Fabric:
        def find_item(self, ws, kind, name):
            raise ConnectionError("dropped")
    svc.fabric = Fabric()
    assert svc._appstudio_left_behind("ws", "Sales") == ""

    class Found:
        def find_item(self, ws, kind, name):
            return {"id": "rep-1"}
    svc.fabric = Found()
    assert svc._appstudio_left_behind("ws", "Sales") == "rep-1"
