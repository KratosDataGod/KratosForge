"""file_stem never gives a name Windows keeps for a device. A notebook called NUL was exported to
Downloads as NUL.ipynb, which on Windows is the null device: the export said it was written and
nothing was there. A project called aux kept its policy package as aux.json, which Windows can't make."""

from __future__ import annotations

import pytest

from pathlib import Path

from fabric_desktop import known_folders
from fabric_desktop.filenames import file_stem
from fabric_desktop.notebook_formats_service import NotebookFormatsMixin


@pytest.mark.parametrize("name", ["CON", "con", "Nul", "PRN", "aux", "COM1", "COM0", "lpt9", "COM¹", "nul.", " CON ", "con?"])
def test_a_device_name_gets_the_default_after_it(name):
    stem = file_stem(name, "Notebook")
    assert stem.casefold() == f"{name.strip(' .?')} notebook".casefold()


@pytest.mark.parametrize("name", ["CONSOLE", "con1", "COM10", "my nul", "LPT", "Sales"])
def test_other_names_are_kept(name):
    assert file_stem(name, "Notebook") == name


def test_an_empty_name_is_the_default():
    assert file_stem("", "Notebook") == file_stem("..", "Notebook") == "Notebook"


def test_a_notebook_called_nul_is_exported_to_a_file_windows_can_keep(tmp_path, monkeypatch):
    monkeypatch.setattr(known_folders, "downloads", lambda: tmp_path)
    monkeypatch.setattr(known_folders, "in_onedrive", lambda _p: False)
    out = NotebookFormatsMixin.notebook_export(None, "NUL", [{"type": "code", "source": "x = 1"}],  # type: ignore[arg-type]
                                               "fabric_py")
    assert Path(out["path"]).name.startswith("NUL notebook.")
