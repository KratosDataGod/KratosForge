"""Mutation batch 69: notebook_resources_service.py, Show in File Explorer's folder and how the program
is started (docs/quality/mutation-testing.md). Nothing is opened: the program is a stand-in."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_resources_service as nrs
from tests.test_notebook_resources_calls import shown, svc  # noqa: F401 - the fixtures are used by name


def test_showing_the_folder_again_shows_the_same_folder(svc, shown):  # noqa: F811
    first = svc.notebook_resource_reveal("Sales")
    assert svc.notebook_resource_reveal("Sales") == first and len(shown) == 2


def test_without_a_no_window_flag_the_program_starts_with_no_flags(tmp_path, shown, monkeypatch):  # noqa: F811
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    nrs._reveal(tmp_path / "a.csv", select=True)
    assert shown[0][1]["creationflags"] == 0


def test_with_a_no_window_flag_the_program_starts_with_it(tmp_path, shown, monkeypatch):  # noqa: F811
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    nrs._reveal(tmp_path / "a.csv", select=True)
    assert shown[0][1]["creationflags"] == 0x08000000


@pytest.mark.parametrize("name, close_fds", [("nt", False), ("posix", True)])
def test_handles_are_closed_for_the_program_except_on_windows(tmp_path, shown, monkeypatch, name, close_fds):  # noqa: F811
    monkeypatch.setattr(nrs, "os", SimpleNamespace(name=name))
    nrs._reveal(tmp_path / "a.csv", select=False)
    assert shown[0][1]["close_fds"] is close_fds
