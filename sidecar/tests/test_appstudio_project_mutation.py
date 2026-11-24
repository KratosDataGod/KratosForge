"""Mutation batch 364: appstudio/project.py (docs/quality/mutation-testing.md). The survivors were the
limits (2 MB specification, 32 MB history, 64 MB export, 500 versions) checked only against the
module's own constants or not at all, the sequence numbers and kinds written to history.jsonl, the
cuts of names, notes and messages, and files in versions/ that aren't versions."""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest

from fabric_desktop.appstudio import project
from fabric_desktop.appstudio.project import ProjectError
from fabric_desktop.appstudio.spec import SpecError
from tests.test_appstudio import _zip, sales

SET_TITLE = [{"op": "set", "id": "c_rev", "field": "title", "value": "Total revenue"}]
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def entries(folder) -> list[dict]:
    return [json.loads(x) for x in (folder / project.HISTORY).read_text(encoding="utf-8").splitlines()]


# ---- the project's own files


def test_a_new_project_is_format_1_stamped_in_utc_and_app_json_is_indented_by_two(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    meta = json.loads((folder / project.META).read_text(encoding="utf-8"))
    assert meta["format"] == 1
    assert STAMP.fullmatch(meta["created"]) and STAMP.fullmatch(meta["modified"])
    assert (folder / project.SPEC).read_text(encoding="utf-8").startswith('{\n  "')


def test_kfapp_json_of_another_format_or_without_a_text_app_id_isnt_read(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    meta = json.loads((folder / project.META).read_text(encoding="utf-8"))
    for bad in ({**meta, "format": 2}, {**meta, "app_id": 7}):
        (folder / project.META).write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ProjectError, match="kfapp.json"):
            project.open_project(folder)


# ---- folder names


def test_a_long_name_is_cut_to_80_characters_and_a_device_name_is_refused():
    assert project.folder_name("a" * 100) == "a" * 80 + ".KFApp"
    with pytest.raises(ProjectError, match="device"):
        project.folder_name("con")


# ---- secrets


def test_a_secret_under_a_long_key_is_reported_with_the_key_cut_to_40():
    key = "x" * 50
    got = project.secret_problems({key: {"password": "hunter2-hunter2"}})
    assert got == [f"{'x' * 40}.password looks like it holds a secret. Projects never keep keys, tokens or passwords."]
    got = project.secret_problems({"password" + "y" * 50: "s3cret-value"})
    assert got[0].startswith("password" + "y" * 32 + " looks like")


def test_a_secret_named_key_set_to_false_isnt_a_secret_but_true_is():
    assert project.secret_problems({"password": False}) == []
    assert project.secret_problems({"password": True}) != []


def test_a_secret_on_a_json_line_is_reported_once():
    line = json.dumps({"password": "hunter2-hunter2"})
    assert project.text_problems(line, "history.jsonl") == [
        "history.jsonl line 1.password looks like it holds a secret. Projects never keep keys, tokens or passwords."]


# ---- the limits


def test_a_specification_over_2_mb_is_refused_saying_2_mb(tmp_path, monkeypatch):
    monkeypatch.setattr(project.specs, "check", lambda spec: None)
    with pytest.raises(SpecError, match=r"larger than 2 MB\."):
        project._guard({"name": "x", "blob": "a" * (2 * 1024 * 1024)})
    project._guard({"name": "x", "blob": "a" * (2 * 1024 * 1024 - 100)})


def test_a_history_over_32_mb_is_refused(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    with open(folder / project.HISTORY, "wb") as f:
        f.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ProjectError, match="too large"):
        project.history(folder)


def test_a_zip_over_64_mb_is_refused(tmp_path):
    folder = project.create(tmp_path / "a", "Sales", sales())
    files = {name: (folder / name).read_bytes() for name in (project.META, project.SPEC)}
    files[project.VALIDATION] = b" " * (64 * 1024 * 1024 + 1 - sum(len(v) for v in files.values()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    with pytest.raises(ProjectError, match="larger than"):
        project.import_(buf.getvalue(), tmp_path / "b")


def test_the_five_hundredth_version_is_the_last(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    for n in range(1, 500):
        (folder / project.VERSIONS / f"{n:04d}.json").write_text(json.dumps({"version": n}), encoding="utf-8")
    assert project.save_version(folder)["version"] == 500
    with pytest.raises(ProjectError, match="already has 500 versions"):
        project.save_version(folder)


# ---- history


def test_changes_undo_redo_publish_and_restore_number_history_from_one(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    project.save_version(folder)
    got = project.change(folder, SET_TITLE, note="n" * 300)
    assert got["seq"] == 1 and got["changed"]
    assert project.change(folder, [{"op": "set", "id": "c_rev", "field": "title", "value": "Rev"}])["seq"] == 2
    project.undo(folder)
    project.redo(folder)
    project.record_publish(folder, "started", "w1", "r1", "Sales")
    project.restore_version(folder, 1)
    got = entries(folder)
    assert [(e["seq"], e["kind"]) for e in got] == [(1, "change"), (2, "change"), (3, "undo"), (4, "redo"),
                                                    (5, "publish"), (6, "restore")]
    assert got[0]["note"] == "n" * 199 + "…"
    assert got[2]["of"] == 2 and got[3]["of"] == 2
    assert got[4]["replaced"] is False


def test_history_shows_the_newest_100_by_default(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    lines = [json.dumps({"seq": n, "kind": "change"}) for n in range(1, 102)]
    (folder / project.HISTORY).write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = project.history(folder)
    assert len(got) == 100 and got[0]["seq"] == 101 and got[-1]["seq"] == 2


def test_publish_history_is_only_the_publishes(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    project.change(folder, SET_TITLE)
    project.record_publish(folder, "done", "w1", "r1", "Sales", replaced=True)
    assert [(e["kind"], e["state"], e["replaced"]) for e in project.publish_history(folder)] == [("publish", "done", True)]


def test_a_change_to_a_specification_that_no_longer_validates_is_refused(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    (folder / project.SPEC).write_text(json.dumps({"name": ""}), encoding="utf-8")
    with pytest.raises(SpecError):
        project.change(folder, SET_TITLE)
    assert not (folder / project.HISTORY).exists()


# ---- save as, export and import


def test_save_as_cuts_the_name_to_120_and_copies_only_version_files(tmp_path):
    folder = project.create(tmp_path / "a", "Sales", sales())
    project.save_version(folder)
    (folder / project.VERSIONS / "notes.json").write_text("{}", encoding="utf-8")
    dest = project.save_as(folder, tmp_path / "b", "N" * 150)
    assert project.open_project(dest)["spec"]["name"] == "N" * 120
    assert sorted(p.name for p in (dest / project.VERSIONS).iterdir()) == ["0001.json"]


def test_export_takes_only_version_files_from_versions(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    project.save_version(folder)
    (folder / project.VERSIONS / "notes.json").write_text("{}", encoding="utf-8")
    names = zipfile.ZipFile(io.BytesIO(project.export(folder))).namelist()
    assert "versions/0001.json" in names and "versions/notes.json" not in names


def test_a_zip_with_strangers_names_five_of_them_cut_to_60():
    files = {("x" * 70) + f"{i}.txt": "x" for i in range(6)}
    with pytest.raises(ProjectError) as got:
        project.import_(_zip(files), "unused")
    named = str(got.value).split(": ", 1)[1].rstrip(".").split(", ")
    assert named == ["x" * 60] * 5


def test_a_secret_in_a_torn_line_is_found_in_the_raw_text():
    assert project.text_problems("not json password=hunter2hunter2", "history.jsonl") == [
        "history.jsonl holds something that looks like a key, token or password."]


def test_a_nested_long_key_is_cut_to_40_too():
    got = project.secret_problems({"a": {"password" + "y" * 50: "s3cret-value"}})
    assert got[0].startswith("a.password" + "y" * 32 + " looks like")


def test_a_project_that_no_longer_validates_gets_no_version(tmp_path):
    folder = project.create(tmp_path, "Sales", sales())
    (folder / project.SPEC).write_text(json.dumps({"name": ""}), encoding="utf-8")
    with pytest.raises(SpecError):
        project.save_version(folder)
    assert list((folder / project.VERSIONS).iterdir()) == []
