"""Edges of notebook import, export and the report that the other tests leave open (mutation
batch 75). Files go to a temporary app folder and a temporary Downloads; nothing reaches a tenant."""

import builtins
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_formats as nbf
from fabric_desktop import notebook_formats_service as nfs
from fabric_desktop.notebook import to_ipynb
from tests.test_notebook_formats import svc  # noqa: F401  (fixture)

NB = json.dumps({"cells": [{"cell_type": "code", "source": "x = 1", "outputs": []}]})
CELLS = [{"type": "code", "source": "x = 1"}]
REAL_OPEN = builtins.open  # taken once, so a second patch doesn't wrap the first


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


def _only_free(svc, monkeypatch, free):
    monkeypatch.setattr(type(svc.notebooks), "_taken", lambda self, name: name != free)


def test_a_report_is_at_most_64_mb():
    assert nfs.REPORT_MAX_BYTES == 64 * 1024 * 1024 == 67_108_864


def test_an_archive_at_the_size_limit_is_read_and_one_character_more_is_refused(monkeypatch):
    monkeypatch.setattr(nbf, "MAX_DBC", 30)  # base64 of 30 bytes, with slack: at most 30 * 4 // 3 + 4 = 44
    monkeypatch.setattr(nfs, "b64", SimpleNamespace(decode_exact=lambda c: c.encode()))
    monkeypatch.setattr(nbf, "parse_dbc", lambda data: [{"len": len(data)}])
    assert nfs._read("w.dbc", "A" * 44, "base64")["notebooks"] == [{"len": 44}]
    with pytest.raises(ValueError, match="larger than 64 MB"):
        nfs._read("w.dbc", "A" * 45, "base64")


def test_a_free_name_is_looked_for_up_to_number_9999_when_checking(svc, monkeypatch):
    _only_free(svc, monkeypatch, "Sales (9999)")
    assert [n["name"] for n in svc.notebook_formats_import_check("Sales.ipynb", NB)["notebooks"]] == ["Sales (9999)"]
    _only_free(svc, monkeypatch, "Sales (10000)")
    with pytest.raises(ValueError, match="free notebook name for Sales"):
        svc.notebook_formats_import_check("Sales.ipynb", NB)


def test_a_free_name_is_looked_for_up_to_number_9999_when_importing(svc, monkeypatch):
    _only_free(svc, monkeypatch, "Sales (9999)")
    assert svc.notebook_formats_import("Sales.ipynb", NB)["created"] == ["Sales (9999)"]
    _only_free(svc, monkeypatch, "Sales (10000)")
    with pytest.raises(ValueError, match="free notebook name for Sales"):
        svc.notebook_formats_import("Sales.ipynb", NB)
    assert not (svc.notebooks.folder / "Sales (10000).ipynb").exists()


def test_an_imported_notebook_is_written_as_ipynb_json_indented_by_one(svc):
    created = svc.notebook_formats_import("Load.ipynb", NB)["created"]
    data = (svc.notebooks.folder / f"{created[0]}.ipynb").read_bytes()
    cells = nbf.parse_ipynb(NB)["cells"]
    assert data == json.dumps(to_ipynb(cells, None), indent=1).encode("utf-8")
    assert data.startswith(b'{\n "')  # one space per level, and no "\r" on any platform


def test_notes_are_kept_under_the_name_of_the_notebook_they_belong_to(svc, monkeypatch):
    note = [{"cell": 1, "note": "changed"}]
    monkeypatch.setattr(nfs, "_read", lambda *a: {"format": "F", "notebooks": [
        {"name": "First", "folder": "", "cells": CELLS, "notes": []},
        {"name": "Second", "folder": "", "cells": CELLS, "notes": note}]})
    out = svc.notebook_formats_import("x.dbc", "", "base64")
    assert out["created"] == ["First", "Second"]
    assert out["notes"] == {"Second": note}


def test_a_failed_write_whose_file_is_already_gone_keeps_the_writes_own_error(svc, monkeypatch):
    class Gone:
        def __init__(self, path):
            self.path = Path(path)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, data):
            raise OSError(28, "No space left on device")  # and nothing was left on disk

    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda p, mode="r", *a, **k: Gone(p) if mode == "xb" else real_open(p, mode, *a, **k))
    with pytest.raises(OSError, match="No space left on device") as raised:
        svc.notebook_formats_import("Load.ipynb", NB)
    assert "0 of 1 were created: none" in str(raised.value)


@pytest.mark.parametrize("cells", [tuple(CELLS), ["x = 1"], [CELLS[0], "x"]])
def test_the_page_to_print_and_the_report_need_a_list_of_cells(svc, cells):
    with pytest.raises(ValueError, match="list of cells"):
        svc.notebook_print_page("n", cells)
    with pytest.raises(ValueError, match="list of cells"):
        svc.notebook_report_open("n", cells, "Ran today.")
    assert svc.notebook_report_page() == {}


def test_the_run_line_is_at_most_200_characters(svc):
    assert svc.notebook_report_open("n", CELLS, "r" * 200)["name"] == "n"
    with pytest.raises(ValueError, match="short line"):
        svc.notebook_report_open("n", CELLS, "r" * 201)


def test_a_report_page_at_the_size_limit_is_kept_and_one_byte_more_is_refused(svc, monkeypatch):
    monkeypatch.setattr(nfs, "REPORT_MAX_BYTES", 100)
    monkeypatch.setattr(nbf, "to_html", lambda name, cells, report=None: "é" * 50)  # 100 bytes in UTF-8
    assert svc.notebook_report_open("n", CELLS, "Ran.")["bytes"] == 100
    monkeypatch.setattr(nbf, "to_html", lambda name, cells, report=None: "é" * 50 + "x")
    with pytest.raises(ValueError, match="larger than 64 MB"):
        svc.notebook_report_open("m", CELLS, "Ran.")
    assert svc.notebook_report_page()["name"] == "n"  # the report kept before stays


def _free_only(monkeypatch, marker):
    def taken(path, mode="r", *a, **k):
        if mode == "x" and marker not in Path(path).name:
            raise FileExistsError(path)
        return REAL_OPEN(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", taken)


def test_an_export_looks_for_a_free_file_name_up_to_number_999(svc, monkeypatch):
    _free_only(monkeypatch, " (999)")
    assert Path(svc.notebook_export("Daily", CELLS, "fabric_py")["path"]).name == f"Daily (999){nbf.EXTENSIONS['fabric_py']}"
    _free_only(monkeypatch, " (1000)")
    with pytest.raises(ValueError, match="free file name in Downloads for the export"):
        svc.notebook_export("Daily", CELLS, "fabric_py")


def test_a_report_export_looks_for_a_free_file_name_up_to_number_999(svc, monkeypatch):
    svc.notebook_report_open("Daily", CELLS, "Ran today.")
    _free_only(monkeypatch, " (999)")
    assert Path(svc.notebook_report_export()["path"]).name == "Daily report (999).html"
    _free_only(monkeypatch, " (1000)")
    with pytest.raises(ValueError, match="free file name in Downloads for the report"):
        svc.notebook_report_export()
