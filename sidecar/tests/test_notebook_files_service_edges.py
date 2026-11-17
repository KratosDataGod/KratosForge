"""Notebook files on this PC (notebook_files_service.py), the paths the main tests don't reach: a delete
that fails puts the notebook's schedules back on, a restore whose schedule was deleted meanwhile, a
Fabric notebook saved here whose link can't be recorded, a link whose file can't be resolved, and
Windows paths read as text. Against a fake Fabric; nothing is sent."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_files
from fabric_desktop import notebook_files_service as N

from test_notebook_files import FakeFabric, FilesService, call, ipynb, save  # noqa: F401


@pytest.fixture
def svc(tmp_path):
    s = FilesService(root=tmp_path)
    s.fake = FakeFabric()
    return s


def _scheduled(svc, name):
    save(svc, name, "x = 1")
    return call(svc, "add_pc_schedule", target={"kind": "notebook", "name": name},
                schedule={"kind": "daily", "times": ["06:00"]})["schedule"]["id"]


def _enabled(svc, name):
    return [s["enabled"] for s in call(svc, "pc_schedules_for", kind="notebook", name=name)]


def test_a_delete_that_fails_puts_the_notebooks_schedules_back_on(svc, monkeypatch):
    _scheduled(svc, "Daily")

    def refuse(*args):
        raise PermissionError(13, "the file is open in another program")

    monkeypatch.setattr(notebook_files, "delete", refuse)
    with pytest.raises(PermissionError):
        svc.notebook_delete("Daily")
    assert _enabled(svc, "Daily") == [True]
    assert "Daily" in [n["name"] for n in svc.notebooks.list()]


def test_a_restore_whose_schedule_was_deleted_meanwhile_restores_the_notebook(svc):
    sid = _scheduled(svc, "Daily")
    gone = svc.notebook_delete("Daily")["id"]
    call(svc, "delete_pc_schedule", schedule_id=sid)
    out = svc.notebook_restore(gone)
    assert out["schedules_on"] == [] and out["schedules_left_off"] == []
    assert "Daily" in [n["name"] for n in svc.notebooks.list()]


def test_a_fabric_notebook_saved_here_whose_link_cant_be_recorded_is_still_saved(svc, monkeypatch):
    svc.fake.add("item-1", "ws1", "Margin", ipynb([("code", "x = 1")]))
    said = []
    monkeypatch.setattr(N._log, "warning", lambda msg, *args: said.append(msg % args))

    def refuse(*args, **kwargs):
        raise OSError("links file is read only")

    monkeypatch.setattr(svc.workload_store, "record_link", refuse)
    out = svc.notebook_from_fabric("ws1", "item-1", "Margin", "Sales WS")
    assert out["name"] == "Margin" and svc.notebooks.load("Margin")
    assert said == ["Couldn't record notebook 'Margin''s Fabric item: OSError: links file is read only"]


def test_a_notebook_file_whose_path_cant_be_resolved_has_no_name(svc, monkeypatch):
    save(svc, "Daily", "x = 1")
    path = str(svc.notebooks.path("Daily"))
    assert svc.notebook_name_for_path(path) == {"name": "Daily"}

    def broken(self, strict=False):
        raise OSError("the drive went away")

    monkeypatch.setattr(Path, "resolve", broken)
    assert svc.notebook_name_for_path(path) == {"name": None}


@pytest.mark.parametrize("path, local", [
    ("C:\\Users\\me\\nb.ipynb", True),
    ("d:/notebooks/nb.ipynb", True),
    ("\\\\host\\share\\nb.ipynb", False),
    ("//host/share/nb.ipynb", False),
    ("nb.ipynb", False),
])
def test_on_windows_only_a_path_on_a_drive_is_local(monkeypatch, path, local):
    monkeypatch.setattr(N, "os", SimpleNamespace(name="nt"))
    assert N._local_path(path) is local
