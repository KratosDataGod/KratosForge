"""#1173 follow-up: a metadata project's folder is a full path, never one taken from the engine's start folder."""

from __future__ import annotations

import pytest

from fabric_desktop.service import Service


@pytest.mark.parametrize("path", ["CON", "crm", "../crm", "projects/crm"])
def test_a_relative_project_folder_is_refused_and_nothing_is_written(tmp_path, monkeypatch, path):
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    svc = Service(root=tmp_path / "app")
    for call in (lambda: svc.meta_init(path, "3"), lambda: svc.meta_open(path)):
        with pytest.raises(ValueError, match="full path"):
            call()
    assert not any((tmp_path / "cwd").iterdir()) and not (tmp_path / "crm").exists()
    assert svc.meta_project()["path"] is None


def test_a_full_path_still_creates_and_opens_the_project(tmp_path):
    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3", sample=False)
    assert svc.meta_open(str(tmp_path / "crm"))["path"] == str(tmp_path / "crm")
