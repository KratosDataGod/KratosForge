"""Shared JSON files (jsonfile): an unreadable file that can't be moved aside reads as empty with a
warning, and an old set-aside copy that can't be removed stays for the next set-aside to try again."""

from __future__ import annotations

import json
from pathlib import Path

from fabric_desktop import jsonfile


def refuse_replace(monkeypatch):
    def replace(src, dst):
        raise PermissionError("in use by another program")
    monkeypatch.setattr(jsonfile.os, "replace", replace)


def test_an_unreadable_file_that_cant_be_moved_aside_reads_as_the_default(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("{torn", encoding="utf-8")
    warned = []
    monkeypatch.setattr(jsonfile._log, "warning", lambda *a: warned.append(a))
    refuse_replace(monkeypatch)
    assert jsonfile.read_json(path, {"empty": True}) == {"empty": True}
    assert path.read_text(encoding="utf-8") == "{torn"  # left where it was
    assert warned[0][0] == "%s could not be read or moved aside (%s); it is being treated as empty."
    assert warned[0][1] == path and "in use by another program" in str(warned[0][2])


def test_a_file_that_isnt_one_object_and_cant_be_moved_aside_reads_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    warned = []
    monkeypatch.setattr(jsonfile._log, "warning", lambda *a: warned.append(a))
    refuse_replace(monkeypatch)
    assert jsonfile.read_object(path) == {}
    assert json.loads(path.read_text(encoding="utf-8")) == [1, 2]
    assert warned[0][0].startswith("%s isn't one JSON object and could not be moved aside")
    assert not list(tmp_path.glob("settings.json.corrupt-*"))


def test_an_old_set_aside_copy_that_cant_be_removed_stays_and_is_logged(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    old = [tmp_path / f"settings.json.corrupt-2020010{i}T000000000000Z" for i in range(1, 6)]
    for p in old:
        p.write_text("{}", encoding="utf-8")
    path.write_text("{torn", encoding="utf-8")
    real_unlink = Path.unlink
    stuck = old[0]

    def unlink(self, missing_ok=False):
        if self == stuck:
            raise PermissionError("read-only")
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    infos = []
    monkeypatch.setattr(jsonfile._log, "info", lambda *a: infos.append(a))
    dest = jsonfile.set_aside(path)
    assert dest.exists() and not path.exists()
    assert stuck.exists()  # it couldn't be removed: the next set-aside tries again
    assert [p.exists() for p in old[1:]] == [True] * 4  # the newest four besides the new copy are kept
    assert infos == [("Couldn't remove the old set-aside copy %s: %s", stuck, infos[0][2])]
    assert str(infos[0][2]) == "read-only"
