"""Mutation tests for App Studio project folders (appstudio/project.py): the limits at their edges, the
numbering of history entries, and what a project's files look like on disk. Found by tools/mutate.py."""

import io
import json
import re
import zipfile

import pytest

from fabric_desktop.appstudio import project
from fabric_desktop.appstudio.project import ProjectError
from fabric_desktop.appstudio.spec import SpecError
from tests.test_appstudio import sales

SET_TITLE = [{"op": "set", "id": "c_rev", "field": "title", "value": "Total revenue"}]
MB = 1024 * 1024


@pytest.fixture
def folder(tmp_path):
    return project.create(tmp_path, "Sales", sales())


def _entries(folder):
    return [json.loads(line) for line in (folder / project.HISTORY).read_text(encoding="utf-8").splitlines()]


def test_a_new_project_is_format_1_with_utc_times_and_a_two_space_specification(folder):
    meta = json.loads((folder / project.META).read_text(encoding="utf-8"))
    assert meta["format"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", meta["created"]) and meta["modified"] == meta["created"]
    assert (folder / project.SPEC).read_text(encoding="utf-8").splitlines()[1].startswith('  "')
    (folder / project.META).write_text(json.dumps(dict(meta, format=0)), encoding="utf-8")
    with pytest.raises(ProjectError, match="this version can read"):
        project.open_project(folder)


def test_a_folder_name_is_at_most_80_characters_and_never_a_device_name():
    assert project.folder_name("a" * 100) == "a" * 80 + project.SUFFIX
    for device in ("CON", "nul", "COM1", "LPT9"):
        with pytest.raises(ProjectError, match="Windows keeps for a device"):
            project.folder_name(device)


def test_a_secret_is_named_by_where_it_is_cut_to_40_characters_and_false_is_no_secret():
    key = "password_" + "x" * 50
    assert project.secret_problems({key: "v"}) == [f"{key[:40]} looks like it holds a secret. Projects never keep keys, tokens or passwords."]
    assert project.secret_problems({"a": {key: "v"}})[0].startswith(f"a.{key[:40]} looks")
    assert project.secret_problems({"password": False}) == [] and project.secret_problems({"password": 0}) == []
    assert len(project.secret_problems({"password": True})) == 1


def test_a_secret_in_text_that_isnt_json_is_found_by_either_check():
    assert project.text_problems("not json password=hunter2xyz", "h") == ["h holds something that looks like a key, token or password."]
    assert len(project.text_problems("not json sk-ant-" + "a" * 30, "h")) == 1
    assert project.text_problems("not json", "h") == []


def test_a_specification_of_2_mb_is_kept_and_one_byte_more_is_refused(monkeypatch):
    monkeypatch.setattr(project.specs, "check", lambda spec: None)
    pad = project.MAX_SPEC_BYTES - len(json.dumps({"pad": ""}))
    assert project._guard({"pad": " " * pad})
    with pytest.raises(SpecError) as e:
        project._guard({"pad": " " * (pad + 1)})
    assert str(e.value) == "The specification is larger than 2 MB."


def test_a_history_of_32_mb_is_read_and_one_byte_more_is_refused(folder):
    with open(folder / project.HISTORY, "wb") as f:
        f.truncate(32 * MB)
    assert project.history(folder) == []
    with open(folder / project.HISTORY, "ab") as f:
        f.write(b"\n")
    with pytest.raises(ProjectError, match="too large to read"):
        project.history(folder)


def test_changes_undos_redos_publishes_and_restores_are_numbered_from_1_in_order(folder):
    assert project.change(folder, SET_TITLE, note="n" * 300)["seq"] == 1
    project.undo(folder)
    project.redo(folder)
    project.record_publish(folder, "done", "w", "r", "Sales")
    project.save_version(folder)
    project.restore_version(folder, 1)
    entries = _entries(folder)
    assert [(e["seq"], e["kind"]) for e in entries] == [(1, "change"), (2, "undo"), (3, "redo"), (4, "publish"), (5, "restore")]
    assert len(entries[0]["note"]) == 200 and entries[0]["note"].endswith("…")
    assert entries[3]["replaced"] is False
    assert project.change(folder, SET_TITLE)["changed"]


def test_a_change_to_a_specification_that_no_longer_validates_is_refused_even_one_that_would_fix_it(folder):
    spec = json.loads((folder / project.SPEC).read_text(encoding="utf-8"))
    [rev] = [c for page in spec["pages"] for c in page["components"] if c["id"] == "c_rev"]
    rev["title"] = "password=hunter2xyz"
    (folder / project.SPEC).write_text(json.dumps(spec), encoding="utf-8")
    assert not project.open_project(folder)["valid"]
    with pytest.raises(SpecError):
        project.change(folder, SET_TITLE)
    assert not (folder / project.HISTORY).exists()


def test_history_shows_the_newest_100_by_default(folder):
    for n in range(101):
        project.record_publish(folder, "started", "w", "r", f"n{n}")
    shown = project.history(folder)
    assert len(shown) == 100 and shown[0]["seq"] == 101 and shown[-1]["seq"] == 2


def test_a_project_has_at_most_500_versions(folder):
    for n in range(1, project.MAX_VERSIONS):
        (folder / project.VERSIONS / f"{n:04d}.json").write_text(json.dumps({"version": n}), encoding="utf-8")
    assert project.save_version(folder)["version"] == 500
    with pytest.raises(ProjectError, match="already has 500 versions"):
        project.save_version(folder)


def test_save_as_cuts_the_name_to_120_and_takes_only_version_files(folder, tmp_path):
    project.save_version(folder)
    (folder / project.VERSIONS / "notes.json").write_text("{}", encoding="utf-8")
    dest = project.save_as(folder, tmp_path / "copies", "b" * 150)
    assert project.open_project(dest)["spec"]["name"] == "b" * 120
    assert sorted(f.name for f in (dest / project.VERSIONS).iterdir()) == ["0001.json"]


def test_an_export_takes_only_version_files(folder):
    project.save_version(folder)
    (folder / project.VERSIONS / "notes.json").write_text("{}", encoding="utf-8")
    names = zipfile.ZipFile(io.BytesIO(project.export(folder))).namelist()
    assert [n for n in names if n.startswith("versions/")] == ["versions/0001.json"]


def test_only_a_projects_own_paths_are_taken_from_a_zip():
    for name in ("../app.json", "/app.json", "versions\\0001.json", "versions/../app.json", "other.json"):
        assert project._member_ok(name) is False, name
    assert project._member_ok("versions/0001.json") is True


def test_a_zip_with_other_files_names_the_first_five_cut_to_60(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in range(6):
            z.writestr(f"{n}" + "x" * 70, "{}")
    with pytest.raises(ProjectError) as e:
        project.import_(buf.getvalue(), tmp_path)
    listed = str(e.value).split(": ", 1)[1].rstrip(".").split(", ")
    assert listed == [f"{n}" + "x" * 59 for n in range(5)]


def test_a_zip_of_64_mb_is_read_and_one_byte_more_is_refused(tmp_path):
    def zipped(size):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(project.HISTORY, b"\n" * size)
        return buf.getvalue()

    with pytest.raises(ProjectError, match="no app.json or kfapp.json"):
        project.import_(zipped(64 * MB), tmp_path)
    with pytest.raises(ProjectError, match="larger than an App Studio project"):
        project.import_(zipped(64 * MB + 1), tmp_path)
