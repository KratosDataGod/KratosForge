"""Notebook files on this PC (notebook_files_service.py), edges the mutation run (batch 82) found
unchecked: Windows' path limits, what counts as a link, which paths are read as local, where a copy of
a Fabric notebook is saved, and which link counts as "already here". Against a fake Fabric in
tmp_path; nothing is sent."""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_files_service as N

from test_notebook_files import FakeFabric, FilesService, ipynb, save  # noqa: F401


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    s = FilesService(root=tmp_path / "app")
    s.fake = FakeFabric()
    return s


def test_the_long_path_reason_starts_at_260_characters_and_stops_past_32767():
    assert (N.WINDOWS_PATH_LIMIT, N.WINDOWS_LONG_PATH_LIMIT) == (260, 32_767)
    assert N._long_path_problem("x" * 259) is None
    at = N._long_path_problem("x" * 260)
    assert at.startswith("KratosForge couldn't open it. Its path is 260 characters, past the 260 Windows allows")
    assert N._long_path_problem("x" * 32_767).startswith("KratosForge couldn't open it. Its path is 32767 characters")
    assert N._long_path_problem("x" * 32_768) is None


def _st(mode=stat.S_IFREG | 0o644, tag=None):
    return SimpleNamespace(st_mode=mode) if tag is None else SimpleNamespace(st_mode=mode, st_reparse_tag=tag)


@pytest.mark.parametrize("st, link", [
    (_st(), False),
    (_st(stat.S_IFLNK | 0o777), True),           # a symbolic link off Windows, with no reparse tag
    (_st(stat.S_IFLNK | 0o777, 0), True),
    (_st(tag=0xA000000C), True),                 # a Windows symbolic link
    (_st(tag=0xA0000003), True),                 # a junction
    (_st(tag=0x20000000), True),                 # the name-surrogate bit alone
    (_st(tag=0x9000001A), False),                # a OneDrive placeholder is a file in place
    (_st(tag=0x80000001), False),                # a tag without the name-surrogate bit
    (_st(tag=0), False),
])
def test_only_a_symbolic_link_or_a_name_surrogate_reparse_tag_is_a_link(st, link):
    assert N._NAME_SURROGATE == 0x20000000
    assert N._is_link(st) is link


@pytest.mark.parametrize("os_name", ["posix", "nt"])
@pytest.mark.parametrize("path", [5, None, b"/notebooks/a.ipynb", "/note\x00books/a.ipynb", "C:\\a\x00.ipynb"])
def test_a_path_that_isnt_text_or_holds_a_nul_is_never_local(monkeypatch, os_name, path):
    monkeypatch.setattr(N, "os", SimpleNamespace(name=os_name))
    assert N._local_path(path) is False


@pytest.mark.parametrize("path, local", [
    ("/home/me/nb.ipynb", True),
    ("nb.ipynb", False),
    ("notebooks/nb.ipynb", False),
    ("//host/share/nb.ipynb", False),
    ("/\\host\\share\\nb.ipynb", False),
])
def test_off_windows_only_an_absolute_path_that_isnt_a_share_is_local(monkeypatch, path, local):
    monkeypatch.setattr(N, "os", SimpleNamespace(name="posix"))
    assert N._local_path(path) is local


def test_a_relative_path_has_no_name_even_from_inside_the_notebooks_folder(svc, monkeypatch):
    save(svc, "Daily", "x = 1")
    assert svc.notebook_name_for_path(str(svc.notebooks.path("Daily"))) == {"name": "Daily"}
    monkeypatch.chdir(svc.notebooks.folder)
    assert svc.notebook_name_for_path("Daily.ipynb") == {"name": None}


def test_a_long_path_that_isnt_a_notebook_gets_no_reason(svc):
    folder = str(svc.notebooks.folder)
    long_txt = os.path.join(folder, "x" * 150, "y" * 150 + ".txt")
    assert len(long_txt) >= N.WINDOWS_PATH_LIMIT
    assert svc.notebook_name_for_path(long_txt) == {"name": None}
    long_nb = long_txt[:-len(".txt")] + ".ipynb"
    assert svc.notebook_name_for_path(long_nb)["why"].startswith("KratosForge couldn't open it")


def test_a_missing_part_below_the_folder_is_not_plain(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.ipynb").write_text("{}", encoding="utf-8")
    assert N._no_link_below(str(tmp_path), str(tmp_path / "a" / "b.ipynb")) is True
    assert N._no_link_below(str(tmp_path), str(tmp_path / "a" / "missing.ipynb")) is False
    assert N._no_link_below(str(tmp_path), str(tmp_path / "gone" / "b.ipynb")) is False


def test_only_a_link_to_the_same_item_in_the_same_workspace_with_its_notebook_here_counts(svc):
    svc.fake.add("item-1", "ws1", "Margin", ipynb([("code", "x = 1")]))
    svc.notebook_from_fabric("ws1", "item-1", "Margin", "Sales WS")
    assert svc._linked_here("ws1", "item-1") == "Margin"
    assert svc._linked_here("ws2", "item-1") is None
    assert svc._linked_here("ws1", "item-2") is None
    assert svc._linked_here("ws2", "item-2") is None
    svc.notebooks.path("Margin").unlink()
    assert svc._linked_here("ws1", "item-1") is None


def test_a_copy_of_fabrics_version_is_saved_where_the_notebook_is_unless_a_place_is_given(svc, tmp_path):
    repo = tmp_path / "repo"
    (repo / "notebooks").mkdir(parents=True)
    svc.notebooks._places = lambda: [(str(repo), "repo", repo / "notebooks")]  # noqa: SLF001
    svc.fake.add("item-1", "ws1", "Margin", ipynb([("code", "x = 1")]))
    svc.notebook_from_fabric("ws1", "item-1", "Margin", "Sales WS", place=str(repo))
    assert svc.notebooks.place_of("Margin") == {"root": str(repo), "label": "repo"}
    svc.notebook_from_fabric("ws1", "item-1", "Margin", "Sales WS", copy_name="Margin (Fabric)")
    assert svc.notebooks.place_of("Margin (Fabric)") == {"root": str(repo), "label": "repo"}
    svc.notebook_from_fabric("ws1", "item-1", "Margin", "Sales WS", place="", copy_name="Margin (app)")
    assert svc.notebooks.place_of("Margin (app)") is None
    # A copy named after a notebook that isn't here goes to the app's own folder.
    svc.fake.add("item-2", "ws1", "Other", ipynb([("code", "y = 1")]))
    svc.notebook_from_fabric("ws1", "item-2", "Other", "Sales WS", copy_name="Other (Fabric)")
    assert svc.notebooks.place_of("Other (Fabric)") is None


def _can_link(tmp_path):
    try:
        os.symlink(tmp_path, tmp_path / "probe", target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    (tmp_path / "probe").unlink()
    return True


def test_a_link_below_the_folder_is_not_plain(tmp_path):
    if not _can_link(tmp_path):
        pytest.skip("this account can't make symbolic links")
    real = tmp_path / "real"
    real.mkdir()
    (real / "b.ipynb").write_text("{}", encoding="utf-8")
    top = tmp_path / "top"
    top.mkdir()
    os.symlink(real, top / "linked", target_is_directory=True)
    assert N._no_link_below(str(top), str(top / "linked" / "b.ipynb")) is False


def test_a_folder_named_like_a_notebook_has_no_name(svc):
    save(svc, "Daily", "x = 1")
    (svc.notebooks.folder / "Folder.ipynb").mkdir()
    assert svc.notebook_name_for_path(str(svc.notebooks.folder / "Folder.ipynb")) == {"name": None}
