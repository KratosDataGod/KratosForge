"""Notebook import and export: the refusals and failed writes the main tests don't reach. Files go
to a temporary app folder and a temporary Downloads; nothing reaches a tenant."""

import builtins
import json
from pathlib import Path

import pytest

from fabric_desktop import notebook_formats as nbf
from fabric_desktop import notebook_formats_service as nfs
from tests.test_notebook_formats import svc  # noqa: F401  (fixture)

NB = json.dumps({"cells": [{"cell_type": "code", "source": "x = 1", "outputs": []}]})
CELLS = [{"type": "code", "source": "x = 1"}]


def test_an_archive_too_large_or_a_notebook_sent_as_base64_is_refused(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_DBC", 3)
    with pytest.raises(ValueError, match="larger than 64 MB"):
        nfs._read("w.dbc", "A" * 20, "base64")
    with pytest.raises(ValueError, match="sent as text"):
        nfs._read("x.ipynb", "e30=", "base64")


def test_no_free_name_left_is_said_plainly(svc, monkeypatch):
    monkeypatch.setattr(type(svc.notebooks), "_taken", lambda self, name: True)
    with pytest.raises(ValueError, match="free notebook name for Sales"):
        svc.notebook_formats_import_check("Sales.ipynb", NB)
    with pytest.raises(ValueError, match="free notebook name for Sales"):
        svc.notebook_formats_import("Sales.ipynb", NB)


def test_a_file_that_appears_under_the_name_is_skipped_for_the_next_one(svc, monkeypatch):
    real_open = builtins.open
    seen = []

    def racing(path, mode="r", *a, **k):
        if mode == "xb" and not seen:
            seen.append(Path(path).name)
            raise FileExistsError(path)  # another window wrote "Load.ipynb" a moment ago
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", racing)
    assert svc.notebook_formats_import("Load.ipynb", NB)["created"] == ["Load (2)"]
    assert seen == ["Load.ipynb"]


def test_a_write_that_fails_removes_the_half_written_notebook_and_says_how_many_were_made(svc, monkeypatch):
    class Broken:
        def __init__(self, path):
            self.path = path
            Path(path).write_bytes(b"{")  # what a half-finished write leaves

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, data):
            raise OSError(28, "No space left on device")

    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda p, mode="r", *a, **k: Broken(p) if mode == "xb" else real_open(p, mode, *a, **k))
    with pytest.raises(OSError, match=r"0 of 1 were created: none") as raised:
        svc.notebook_formats_import("Load.ipynb", NB)
    assert "No space left" in str(raised.value)
    assert not (svc.notebooks.folder / "Load.ipynb").exists()


def test_export_refuses_cells_that_arent_a_list_of_cells(svc):
    with pytest.raises(ValueError, match="list of cells"):
        svc.notebook_export("n", {"type": "code"}, "fabric_py")
    with pytest.raises(ValueError, match="list of cells"):
        svc.notebook_export("n", ["x = 1"], "fabric_py")


def test_export_with_no_free_file_name_left_says_so(svc, monkeypatch):
    real_open = builtins.open

    def taken(path, mode="r", *a, **k):
        if mode == "x":
            raise FileExistsError(path)
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", taken)
    with pytest.raises(ValueError, match="free file name in Downloads"):
        svc.notebook_export("Daily", CELLS, "fabric_py")


def test_the_page_to_print_is_the_web_page_export_and_writes_nothing(svc, tmp_path):
    from fabric_desktop import audit
    from fabric_desktop import known_folders

    cells = [{"type": "code", "source": "print('<b>')", "outputs": [{"type": "stream", "text": "<i>x</i>"}], "count": 1}]
    before = sorted(p.name for p in known_folders.downloads().glob("*")) if known_folders.downloads().exists() else []
    page = svc.notebook_print_page("Sales", cells)["html"]
    assert page == nbf.to_html("Sales", cells)
    assert "&lt;i&gt;x&lt;/i&gt;" in page and "<i>" not in page
    assert "@media print" in page
    after = sorted(p.name for p in known_folders.downloads().glob("*")) if known_folders.downloads().exists() else []
    assert after == before
    assert "notebook_print_page" in audit.READ_ONLY
    with pytest.raises(ValueError, match="list of cells"):
        svc.notebook_print_page("Sales", "x")
