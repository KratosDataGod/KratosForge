"""Files the app stores or works on stay out of OneDrive (Chris, 2026-11-20): new folders in OneDrive are
refused, exports and spill files go elsewhere when theirs is in OneDrive, the old default metadata folder is
copied out, and Ready to work names any folder still in it. Local only: OneDrive is stood in for by folders."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import known_folders, readiness


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "me"
    (home / "OneDrive - Contoso" / "Documents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for name in known_folders._ONEDRIVE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(known_folders, "_registry_onedrive", lambda: [])
    return home


def test_a_folder_in_onedrive_is_found_by_its_variable_its_account_or_its_name(home, tmp_path, monkeypatch):
    elsewhere = tmp_path / "Sync"
    monkeypatch.setenv("OneDriveCommercial", str(elsewhere))
    assert known_folders.onedrive_root(elsewhere / "a" / "b") == elsewhere
    monkeypatch.setattr(known_folders, "_registry_onedrive", lambda: [tmp_path / "Personal"])
    assert known_folders.in_onedrive(tmp_path / "Personal" / "x")
    assert known_folders.onedrive_root(home / "OneDrive - Contoso" / "Documents" / "p") == home / "OneDrive - Contoso"
    assert known_folders.in_onedrive(home / "OneDrive" / "p")
    assert not known_folders.in_onedrive(home / "KratosForge" / "metadata")
    assert not known_folders.in_onedrive(home / "OneDriveNotes")  # a name that only starts the same
    assert not known_folders.in_onedrive(tmp_path / "SyncOther")  # a sibling that starts like a root


def test_exports_leave_a_downloads_folder_moved_into_onedrive(home, monkeypatch):
    monkeypatch.setattr(known_folders, "downloads", lambda: home / "Downloads")
    assert known_folders.export_folder() == home / "Downloads"
    monkeypatch.setattr(known_folders, "downloads", lambda: home / "OneDrive - Contoso" / "Downloads")
    folder = known_folders.export_folder()
    assert folder == home / "KratosForge" / "Exports" and folder.is_dir()


def test_new_folders_in_onedrive_are_refused_with_where_to_put_them(home, tmp_path):
    from fabric_desktop.app_settings import _sql_folder
    from fabric_desktop.localdata.projects import ProjectStore
    inside = str(home / "OneDrive - Contoso" / "Documents" / "x")
    with pytest.raises(ValueError, match=r"is in OneDrive .*can.t be kept there\. OneDrive would upload .* Pick a folder on this PC outside OneDrive"):
        _sql_folder(inside)
    assert _sql_folder(str(home / "temp")) == str(home / "temp")
    with pytest.raises(ValueError, match="a local data project can't be kept there"):
        ProjectStore(tmp_path / "local-data").create("p", "persistent", inside)


def test_a_new_repository_in_onedrive_is_refused(home):
    from fabric_desktop.git.cli import GitError
    from fabric_desktop.git_service import _not_in_onedrive
    with pytest.raises(GitError, match="a new repository can't be kept there"):
        _not_in_onedrive(str(home / "OneDrive" / "repos"))
    _not_in_onedrive(str(home / "source" / "repos"))


def test_a_local_data_project_already_in_onedrive_spills_to_the_pc_s_temp(home, tmp_path):
    from fabric_desktop.localdata.projects import Project
    moved = Project(id="..\\..\\x", name="Sales", folder=str(home / "OneDrive" / "sales"), storage="persistent")
    assert not known_folders.in_onedrive(moved.temp_dir) and moved.temp_dir.name == "tmp"
    assert moved.temp_dir.parent.parent.name == "kratosforge-local" and ".." not in moved.temp_dir.parts  # not by id
    here = Project(id="sales", name="Sales", folder=str(tmp_path / "sales"), storage="persistent")
    assert here.temp_dir == tmp_path / "sales" / "tmp"


def test_the_sql_page_ignores_a_temp_folder_already_in_onedrive(home, tmp_path):
    from fabric_desktop.sql_exec import SqlExecutions
    limits = {"sql_temp_dir": str(home / "OneDrive" / "tmp")}
    runs = SqlExecutions(tmp_path / "app", lambda: "tok", limits=lambda: limits)
    assert runs.base() == tmp_path / "app" / "sql-runs"
    limits["sql_temp_dir"] = str(tmp_path / "fast")
    assert runs.base() == tmp_path / "fast"


def test_the_old_default_metadata_project_is_copied_out_and_the_original_kept(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    old = home / "OneDrive - Contoso" / "Documents" / "FabricDesktop" / "metadata"
    (old / "semantic").mkdir(parents=True)
    (old / "layers.toml").write_text("# project\n", encoding="utf-8")
    (old / "semantic" / "m.tmdl").write_text("model\n", encoding="utf-8")
    monkeypatch.setattr(known_folders, "documents", lambda: home / "OneDrive - Contoso" / "Documents")
    svc = Service(root=tmp_path / "app")
    svc._save_settings(meta_project=str(old))
    new = svc._out_of_onedrive(str(old))
    assert new == str(home / "KratosForge" / "metadata")
    assert (Path(new) / "semantic" / "m.tmdl").read_text(encoding="utf-8") == "model\n"
    assert (old / "layers.toml").exists()  # nothing removed in OneDrive
    settings = svc._settings()
    assert settings["meta_project"] == new and settings["meta_project_moved_from"] == str(old)
    assert svc._out_of_onedrive(str(old)) == new  # once per run
    folders = svc._local_folders()
    row = readiness.onedrive_check(folders)
    assert row["status"] == "warn" and row["text"].startswith("These folders are in OneDrive: ")
    assert "old copy, which you can delete" in row["text"] and str(old) in row["text"]


def test_another_project_in_onedrive_is_left_where_the_person_put_it(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    mine = home / "OneDrive" / "team-project"
    mine.mkdir(parents=True)
    (mine / "layers.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(known_folders, "documents", lambda: home / "OneDrive - Contoso" / "Documents")
    svc = Service(root=tmp_path / "app")
    assert svc._out_of_onedrive(str(mine)) == str(mine)
    assert not (home / "KratosForge").exists()


def test_the_copy_waits_when_the_new_folder_already_exists(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    old = home / "OneDrive - Contoso" / "Documents" / "FabricDesktop" / "metadata"
    old.mkdir(parents=True)
    (old / "layers.toml").write_text("", encoding="utf-8")
    (home / "KratosForge" / "metadata").mkdir(parents=True)
    monkeypatch.setattr(known_folders, "documents", lambda: home / "OneDrive - Contoso" / "Documents")
    svc = Service(root=tmp_path / "app")
    assert svc._out_of_onedrive(str(old)) == str(old)  # never written over: Ready to work names it instead


def test_a_new_metadata_project_defaults_out_of_onedrive_and_refuses_onedrive(home, tmp_path):
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")
    assert svc.meta_project()["default_path"] == str(home / "KratosForge" / "metadata")
    with pytest.raises(ValueError, match="a metadata project can't be kept there"):
        svc.meta_init(str(home / "OneDrive - Contoso" / "Documents" / "m"))


def test_ready_to_work_is_ok_when_nothing_is_in_onedrive(home, tmp_path):
    row = readiness.onedrive_check({"The app's data folder": str(tmp_path / "app")})
    assert row["status"] == "ok" and row["key"] == "onedrive"


def test_a_relative_onedrive_account_folder_is_ignored(home, monkeypatch):
    monkeypatch.setattr(known_folders.sys, "platform", "linux")  # the registry read itself is Windows only
    assert known_folders._registry_onedrive() == []


def test_a_copy_that_fails_part_way_leaves_nothing_in_the_profile(home, tmp_path, monkeypatch):
    from fabric_desktop import meta_service
    from fabric_desktop.service import Service
    old = home / "OneDrive - Contoso" / "Documents" / "FabricDesktop" / "metadata"
    old.mkdir(parents=True)
    (old / "layers.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(known_folders, "documents", lambda: home / "OneDrive - Contoso" / "Documents")

    def full_disk(src, dst, symlinks=False):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "half").write_text("x", encoding="utf-8")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(meta_service.shutil, "copytree", full_disk)
    svc = Service(root=tmp_path / "app")
    assert svc._out_of_onedrive(str(old)) == str(old)
    assert not (home / "KratosForge").exists() or not any((home / "KratosForge").iterdir())
