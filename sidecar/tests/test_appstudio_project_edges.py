"""App Studio project folders: the refusals, links, history and import paths the tests in test_appstudio
don't reach. Everything is a temporary folder."""

import io
import json
import os
import zipfile

import pytest

from fabric_desktop.appstudio import project
from fabric_desktop.appstudio.project import ProjectError
from fabric_desktop.appstudio.spec import SpecError
from tests.test_appstudio import _zip, sales

no_links = pytest.mark.skipif(os.name == "nt", reason="making a symlink needs extra rights on Windows")
SET_TITLE = [{"op": "set", "id": "c_rev", "field": "title", "value": "Total revenue"}]


def test_a_specification_over_the_size_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "MAX_SPEC_BYTES", 100)
    with pytest.raises(SpecError, match="larger than"):
        project.create(tmp_path, "Sales", sales())
    assert list(tmp_path.iterdir()) == []


@no_links
def test_a_project_folder_that_is_a_link_is_not_used(tmp_path):
    real = project.create(tmp_path / "real", "Sales", sales())
    link = tmp_path / "Linked.KFApp"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ProjectError, match="folder is a link"):
        project.open_project(link)


@no_links
def test_a_project_file_that_is_a_link_is_not_used(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    outside = tmp_path / "outside.json"
    outside.write_text((folder / project.SPEC).read_text(encoding="utf-8"), encoding="utf-8")
    (folder / project.SPEC).unlink()
    (folder / project.SPEC).symlink_to(outside)
    with pytest.raises(ProjectError, match="app.json in the project is a link"):
        project.open_project(folder)


def test_a_history_over_the_size_limit_says_to_start_a_clone(tmp_path, monkeypatch):
    folder = project.create(tmp_path, "Sales", sales())
    project.change(folder, SET_TITLE)
    monkeypatch.setattr(project, "MAX_HISTORY_BYTES", 10)
    with pytest.raises(ProjectError, match="history is too large"):
        project.history(folder)


def test_a_change_from_an_unknown_source_is_refused_and_nothing_written(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    with pytest.raises(ProjectError, match="source must be one of"):
        project.change(folder, SET_TITLE, source="robot")
    assert not (folder / project.HISTORY).exists()


def test_undo_of_a_change_missing_from_history_is_refused(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    project.change(folder, SET_TITLE)
    (folder / project.HISTORY).write_text("", encoding="utf-8")
    with pytest.raises(ProjectError, match="missing from history.jsonl"):
        project.undo(folder)


def test_a_publish_entry_has_one_of_three_states(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    with pytest.raises(ProjectError, match="started, done or failed"):
        project.record_publish(folder, "queued", "ws", "rep", "Sales")


def test_a_project_with_the_most_versions_takes_no_more(tmp_path, monkeypatch):
    folder = project.create(tmp_path, "Sales", sales())
    project.save_version(folder, "v1")
    monkeypatch.setattr(project, "MAX_VERSIONS", 1)
    with pytest.raises(ProjectError, match="already has 1 versions"):
        project.save_version(folder, "v2")


@pytest.mark.parametrize("version", [0, True, "1"])
def test_restoring_needs_a_version_number(tmp_path, version):
    folder = project.create(tmp_path, "Sales", sales())
    with pytest.raises(ProjectError, match="Choose a version"):
        project.restore_version(folder, version)


def test_a_version_with_no_specification_is_not_restored(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    (folder / project.VERSIONS / "0001.json").write_text(json.dumps({"version": 1, "label": "v1"}), encoding="utf-8")
    with pytest.raises(ProjectError, match="Version 1 has no specification"):
        project.restore_version(folder, 1)


def test_an_import_larger_than_a_project_can_be_is_refused(tmp_path, monkeypatch):
    data = project.export(project.create(tmp_path / "a", "Sales", sales()))
    monkeypatch.setattr(project, "MAX_EXPORT_BYTES", 100)
    with pytest.raises(ProjectError, match="larger than an App Studio project"):
        project.import_(data, tmp_path / "b")


def test_an_import_keeps_history_and_leaves_damaged_versions_behind(tmp_path):
    folder = project.create(tmp_path / "a", "Sales", sales())
    project.change(folder, SET_TITLE, note="Rename the revenue card")
    project.save_version(folder, "good")
    data = project.export(folder)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {n: z.read(n).decode("utf-8") for n in z.namelist()}
    files["versions/0002.json"] = "not json"
    files["versions/0003.json"] = json.dumps({"version": 3, "label": "no spec"})
    dest = project.import_(_zip(files), tmp_path / "b")
    assert [h["note"] for h in project.history(dest)] == ["Rename the revenue card"]
    assert [v["label"] for v in project.versions(dest)] == ["good"]
