"""The notebook explorer's calls (notebook_resources_service.py) that test_notebook_resources.py leaves out:
a file that arrives damaged, the folder a cell from elsewhere runs in, and Show in File Explorer on each
platform. Nothing is opened: the program that would show the file is a stand-in."""

from __future__ import annotations

import base64

import pytest

from fabric_desktop import notebook_resources_service as nrs
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.notebooks.save("Sales", [{"type": "code", "source": ""}])
    return s


@pytest.fixture
def shown(monkeypatch):
    calls = []

    def popen(args, **kw):
        calls.append((args, kw))
    monkeypatch.setattr(nrs.subprocess, "Popen", popen)
    return calls


# --------------------------------------------------------------------------- adding a file

@pytest.mark.parametrize("content", ["not base64!", "YWJj="])
def test_a_file_that_didnt_arrive_whole_is_refused_and_nothing_is_written(svc, content):
    with pytest.raises(ValueError, match="didn't arrive whole"):
        svc.notebook_resource_add("Sales", "a.csv", content)
    assert svc.notebook_resources("Sales")["files"] == []


def test_a_file_cant_be_added_to_a_notebook_that_isnt_saved(svc):
    with pytest.raises(ValueError, match="no notebook named 'Draft'"):
        svc.notebook_resource_add("Draft", "a.csv", base64.b64encode(b"a").decode())


# --------------------------------------------------------------------------- the folder builtin/ paths read

def test_the_folder_a_running_notebook_reads_is_beside_it(svc):
    assert svc._resources_dir("Sales") == str(svc.notebooks.folder / "Sales.resources")
    assert svc._resources_dir(None) == "" and svc._resources_dir("") == ""


def test_a_cell_run_from_somewhere_that_isnt_a_notebook_has_no_folder(svc):
    assert svc._resources_dir("../../outside") == ""


# --------------------------------------------------------------------------- Show in File Explorer

def test_showing_a_file_selects_it_and_doesnt_open_it(svc, shown, monkeypatch):
    monkeypatch.setattr(nrs.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    svc.notebook_resource_add("Sales", "run.cmd", base64.b64encode(b"echo hi").decode())
    out = svc.notebook_resource_reveal("Sales", "run.cmd")
    target = svc.notebooks.folder / "Sales.resources" / "builtin" / "run.cmd"
    assert out == {"shown": str(target)}
    (args, kw), = shown
    assert args == [r"C:\Windows\explorer.exe", f"/select,{target}"]
    assert kw["stdin"] == nrs.subprocess.DEVNULL and kw["stdout"] == nrs.subprocess.DEVNULL


def test_showing_the_folder_makes_it_when_the_notebook_has_none_yet(svc, shown, monkeypatch):
    monkeypatch.setattr(nrs.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    folder = svc.notebooks.folder / "Sales.resources" / "builtin"
    assert not folder.exists()
    assert svc.notebook_resource_reveal("Sales") == {"shown": str(folder)}
    assert folder.is_dir() and shown[0][0] == [r"C:\Windows\explorer.exe", str(folder)]


def test_a_file_that_isnt_there_is_not_shown(svc, shown):
    with pytest.raises(FileNotFoundError, match="no resource file named 'gone.csv'"):
        svc.notebook_resource_reveal("Sales", "gone.csv")
    assert shown == []


@pytest.mark.parametrize("platform, select, expected", [
    ("darwin", True, ["/usr/bin/open", "-R", "{path}"]),
    ("darwin", False, ["/usr/bin/open", "{path}"]),
    ("linux", True, ["xdg-open", "{parent}"]),
    ("linux", False, ["xdg-open", "{path}"]),
])
def test_other_platforms_show_it_with_their_own_program(tmp_path, shown, monkeypatch, platform, select, expected):
    monkeypatch.setattr(nrs.sys, "platform", platform)
    path = tmp_path / "builtin" / "a.csv"
    nrs._reveal(path, select=select)
    assert shown[0][0] == [a.format(path=path, parent=path.parent) for a in expected]
