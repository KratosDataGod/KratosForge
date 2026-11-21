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
    assert not (Path(new) / ".kratosforge-copied-from").exists()
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


def test_a_copy_whose_setting_wasnt_saved_is_used_by_the_next_run(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    old = home / "OneDrive - Contoso" / "Documents" / "FabricDesktop" / "metadata"
    old.mkdir(parents=True)
    (old / "layers.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(known_folders, "documents", lambda: home / "OneDrive - Contoso" / "Documents")
    first = Service(root=tmp_path / "app")
    real_save = first._save_settings

    def fails(**_):
        raise OSError(13, "Access is denied")
    monkeypatch.setattr(first, "_save_settings", fails)
    with pytest.raises(OSError):
        first._out_of_onedrive(str(old))
    new = home / "KratosForge" / "metadata"
    assert (new / "layers.toml").exists() and (new / ".kratosforge-copied-from").exists()
    real_save(meta_project=str(old))
    again = Service(root=tmp_path / "app")
    assert again._out_of_onedrive(str(old)) == str(new)  # its own finished copy, so it switches
    assert again._settings()["meta_project"] == str(new)
    assert not (new / ".kratosforge-copied-from").exists()


def test_a_folder_someone_else_made_at_the_new_place_is_still_never_used(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    old = home / "OneDrive - Contoso" / "Documents" / "FabricDesktop" / "metadata"
    old.mkdir(parents=True)
    (old / "layers.toml").write_text("", encoding="utf-8")
    new = home / "KratosForge" / "metadata"
    new.mkdir(parents=True)
    (new / ".kratosforge-copied-from").write_text(str(home / "elsewhere"), encoding="utf-8")
    monkeypatch.setattr(known_folders, "documents", lambda: home / "OneDrive - Contoso" / "Documents")
    assert Service(root=tmp_path / "app")._out_of_onedrive(str(old)) == str(old)


# ---- pinned by mutation testing (each test below fails on a change the tests above let through)

class _FakeKey:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_winreg(accounts):
    """A stand-in for winreg holding HKCU\\...\\OneDrive\\Accounts: {account: UserFolder value, or an
    exception to raise reading it}; accounts=None is a user OneDrive was never set up for."""
    import types

    def open_key(parent, name):
        if parent == "HKCU":
            if accounts is None:
                raise FileNotFoundError(2, "The system cannot find the file specified")
            assert name == r"Software\Microsoft\OneDrive\Accounts"
            return _FakeKey(list(accounts.items()))
        return _FakeKey(dict(parent.entries)[name])

    def enum_key(key, i):
        if i >= len(key.entries):
            raise OSError(259, "No more data is available")
        return key.entries[i][0]

    def query_value_ex(key, name):
        assert name == "UserFolder"
        if isinstance(key.entries, Exception):
            raise key.entries
        return key.entries, 1

    return types.SimpleNamespace(HKEY_CURRENT_USER="HKCU", OpenKey=open_key, EnumKey=enum_key,
                                 QueryValueEx=query_value_ex)


def test_each_onedrive_account_folder_is_read_from_the_registry(tmp_path, monkeypatch):
    import sys
    org, personal = tmp_path / "OneDrive - Contoso", tmp_path / "OneDrive"
    accounts = {"Business1": f"  {org}  ", "Broken": OSError(2, "no UserFolder"), "Relative": "OneDrive\\x",
                "Blank": "   ", "Number": 5, "Personal": str(personal)}
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(accounts))
    with monkeypatch.context() as m:
        m.setattr(known_folders.sys, "platform", "win32")
        found = known_folders._registry_onedrive()
    assert found == [org, personal]  # trimmed; an unreadable, relative, blank or non-text one is skipped
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    with monkeypatch.context() as m:
        m.setattr(known_folders.sys, "platform", "win32")
        assert known_folders._registry_onedrive() == []  # OneDrive never set up
        m.setitem(sys.modules, "winreg", None)  # a Python without winreg
        assert known_folders._registry_onedrive() == []
    with monkeypatch.context() as m:
        m.setattr(known_folders.sys, "platform", "linux")
        assert known_folders._registry_onedrive() == []  # never asked off Windows


def test_onedrive_variables_are_trimmed_and_listed_once(home, tmp_path, monkeypatch):
    sync = tmp_path / "Sync"
    monkeypatch.setenv("OneDrive", f" {sync} ")
    monkeypatch.setenv("OneDriveCommercial", str(sync))
    monkeypatch.setenv("OneDriveConsumer", "relative")  # would resolve against the start folder
    monkeypatch.setattr(known_folders, "_registry_onedrive", lambda: [sync, tmp_path / "Personal"])
    assert known_folders.onedrive_roots() == [sync, tmp_path / "Personal"]
    monkeypatch.delenv("OneDriveCommercial")
    monkeypatch.setattr(known_folders, "_registry_onedrive", lambda: [])
    assert known_folders.onedrive_roots() == [sync]  # OneDrive alone is enough
    monkeypatch.setenv("OneDriveConsumer", str(tmp_path / "Home"))
    monkeypatch.delenv("OneDrive")
    assert known_folders.onedrive_roots() == [tmp_path / "Home"]  # and so is OneDriveConsumer


def test_a_onedrive_folder_itself_is_in_onedrive(home, tmp_path, monkeypatch):
    monkeypatch.setenv("OneDrive", str(tmp_path / "Sync"))
    assert known_folders.onedrive_root(tmp_path / "Sync") == tmp_path / "Sync"
    assert known_folders.onedrive_root(home / "OneDrive - Contoso") == home / "OneDrive - Contoso"


def test_exports_go_to_the_same_local_folder_every_time(home, monkeypatch):
    monkeypatch.setattr(known_folders, "downloads", lambda: home / "OneDrive" / "Downloads")
    assert known_folders.export_folder() == known_folders.export_folder() == home / "KratosForge" / "Exports"


def test_a_refusal_and_the_ready_to_work_warning_name_the_local_folder(home, tmp_path):
    local = str(home / "KratosForge")
    said = known_folders.onedrive_refusal(home / "OneDrive" / "x", "a project")
    assert said is not None and said.endswith(f"such as {local}.")
    assert known_folders.onedrive_refusal(home / "x", "a project") is None
    row = readiness.onedrive_check({"A": str(home / "OneDrive" / "a")})
    assert row["text"].startswith(f"These folders are in OneDrive: A ({home / 'OneDrive' / 'a'}). ")
    assert f"such as {local}, then open it" in row["text"] and row["doc"] == "troubleshooting.md#files-in-onedrive"


def test_ready_to_work_lists_every_folder_in_onedrive_and_skips_unset_ones(home, tmp_path):
    a, b, c = (str(home / "OneDrive" / n) for n in "abc")
    two = readiness.onedrive_check({"A": a, "B": b, "Unset": "", "Here": str(tmp_path / "here")})
    assert two["text"].startswith(f"These folders are in OneDrive: A ({a}) and B ({b}). ")
    three = readiness.onedrive_check({"A": a, "B": b, "C": c})
    assert three["text"].startswith(f"These folders are in OneDrive: A ({a}), B ({b}) and C ({c}). ")


def test_projects_in_onedrive_spill_by_their_folder_one_temp_folder_each(home, tmp_path):
    from fabric_desktop.localdata.projects import Project
    one = Project(id="p", name="A", folder=str(home / "OneDrive" / "a"), storage="persistent")
    two = Project(id="p", name="B", folder=str(home / "OneDrive" / "b"), storage="persistent")
    same = Project(id="q", name="A", folder=str(home / "OneDrive" / "a"), storage="persistent")
    assert one.temp_dir != two.temp_dir and one.temp_dir == same.temp_dir


def _old_project(home, monkeypatch):
    old = home / "OneDrive - Contoso" / "Documents" / "FabricDesktop" / "metadata"
    old.mkdir(parents=True)
    (old / "layers.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(known_folders, "documents", lambda: home / "OneDrive - Contoso" / "Documents")
    return old


def test_a_copy_an_earlier_run_left_unfinished_is_cleared_and_nothing_is_left_beside_the_new_folder(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    old = _old_project(home, monkeypatch)
    left = home / "KratosForge" / "metadata.copying"
    left.mkdir(parents=True)
    (left / "stale").write_text("x", encoding="utf-8")
    new = Service(root=tmp_path / "app")._out_of_onedrive(str(old))
    assert new == str(home / "KratosForge" / "metadata")
    assert not (Path(new) / "stale").exists() and (Path(new) / "layers.toml").exists()
    assert sorted(p.name for p in (home / "KratosForge").iterdir()) == ["metadata"]


def test_links_in_the_project_are_copied_as_links(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    old = _old_project(home, monkeypatch)
    (tmp_path / "big").mkdir()
    try:
        (old / "data").symlink_to(tmp_path / "big", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    new = Path(Service(root=tmp_path / "app")._out_of_onedrive(str(old)))
    assert (new / "data").is_symlink()  # not followed: a junction loop or a large share isn't copied in


def test_a_failed_copy_isnt_tried_again_in_the_same_run(home, tmp_path, monkeypatch):
    from fabric_desktop import meta_service
    from fabric_desktop.service import Service
    old = _old_project(home, monkeypatch)
    tries = []

    def full_disk(src, dst, symlinks=False):
        tries.append(dst)
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(meta_service.shutil, "copytree", full_disk)
    svc = Service(root=tmp_path / "app")
    assert svc._out_of_onedrive(str(old)) == svc._out_of_onedrive(str(old)) == str(old)
    assert len(tries) == 1


def test_a_marker_that_cant_be_removed_doesnt_stop_the_switch(home, tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    old = _old_project(home, monkeypatch)
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self.name == ".kratosforge-copied-from":
            raise PermissionError(13, "The process cannot access the file")
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", locked)
    svc = Service(root=tmp_path / "app")
    assert svc._out_of_onedrive(str(old)) == str(home / "KratosForge" / "metadata")
    assert svc._settings()["meta_project"] == str(home / "KratosForge" / "metadata")
