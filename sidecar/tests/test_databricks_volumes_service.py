"""Workspaces > Databricks > Volumes on this PC (Preview): the screen's RPCs (databricks_volumes_service.py).
Each call reads its paths through databricks_volumes.local_path, so a path that leaves its volume, by name or
through a link, is refused before anything is read or written; drop and delete wait for ``confirm``; a download
never lands in OneDrive or in the app's data folder. Each test has a volume root of its own (LOCALAPPDATA)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fabric_desktop import audit, known_folders, server
from fabric_desktop import databricks_volumes as dv
from fabric_desktop import databricks_volumes_service as dvs

V = "/Volumes/main/sales/landing"


@pytest.fixture
def here(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERNAME", "ana")
    for name in known_folders._ONEDRIVE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(known_folders, "_registry_onedrive", lambda: [])
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "me"))
    return tmp_path / "appdata" / "FabricDesktop" / "volumes" / "main"


@pytest.fixture
def svc(here):
    s = dvs.DatabricksVolumesMixin()
    s.databricks_volume_create("sales", "landing", "raw drops")
    return s


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "secret.txt").write_text("not yours", encoding="utf-8")
    return folder


def _link(link: Path, to: Path) -> None:
    try:
        os.symlink(to, link, target_is_directory=to.is_dir())
    except (OSError, NotImplementedError) as e:  # Windows without the right to make links
        pytest.skip(f"can't make a link here: {e}")


def test_every_call_is_an_rpc_reads_skip_the_audit_and_removals_fail_closed():
    assert dvs.METHODS <= server.METHODS
    assert dvs.READS <= audit.READ_ONLY
    assert not (dvs.METHODS - dvs.READS) & audit.READ_ONLY
    for method in ("databricks_volume_drop", "databricks_volume_delete"):
        assert audit.fails_closed(method, {"confirm": True})
    for method in ("databricks_volume_upload", "databricks_volume_download"):
        assert audit.fails_closed(method, {"overwrite": True})


# ---- list, describe, create, rename, comment, drop

def test_create_lists_and_describes_a_volume(svc, here):
    out = svc.databricks_volumes()
    assert out["root"] == str(here)
    [v] = out["volumes"]
    assert (v["name"], v["database"], v["catalog"], v["comment"], v["path"], v["items"]) == (
        "landing", "sales", "main", "raw drops", V, 0)
    assert v["folder"] == str(here / "sales" / "landing") and v["volume_type"] == "MANAGED" and v["owner"] == "ana"
    assert svc.databricks_volume_describe("sales", "landing")["path"] == V
    assert svc.databricks_volumes("finance")["volumes"] == []
    made = svc.databricks_volume_create(" Finance ", "Raw", "")
    assert (made["database"], made["name"], made["comment"]) == ("finance", "raw", None)
    assert [v["name"] for v in svc.databricks_volumes()["volumes"]] == ["raw", "landing"]


def test_create_refuses_a_volume_that_is_there_and_names_that_cannot_be_folders(svc):
    with pytest.raises(ValueError, match=r"\[VOLUME_ALREADY_EXISTS\]"):
        svc.databricks_volume_create("sales", "landing")
    with pytest.raises(ValueError, match="name main for the schema"):
        svc.databricks_volume_create("main", "x")
    for bad in ("..", "a/b", "a\\b", "con", "x y", ""):
        with pytest.raises(ValueError):
            svc.databricks_volume_create("sales", bad)
    with pytest.raises(ValueError):
        svc.databricks_volume_describe("..", "landing")


def test_create_refuses_a_volume_root_in_onedrive(here, monkeypatch):
    monkeypatch.setenv("OneDrive", str(here.parents[3]))
    with pytest.raises(ValueError, match="is in OneDrive"):
        dvs.DatabricksVolumesMixin().databricks_volume_create("sales", "landing")


def test_rename_and_set_comment(svc, here):
    out = svc.databricks_volume_rename("sales", "landing", "Inbox")
    assert out["name"] == "inbox" and out["comment"] == "raw drops" and (here / "sales" / "inbox").is_dir()
    with pytest.raises(ValueError, match="cannot be found"):
        svc.databricks_volume_describe("sales", "landing")
    with pytest.raises(ValueError, match="a volume name can't hold"):
        svc.databricks_volume_rename("sales", "inbox", "../x")
    assert svc.databricks_volume_set_comment("sales", "inbox", "  new words ")["comment"] == "new words"
    assert svc.databricks_volume_set_comment("sales", "inbox", "")["comment"] is None


def test_drop_needs_confirm_and_removes_the_files(svc, here):
    (here / "sales" / "landing" / "a.csv").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="needs your confirmation"):
        svc.databricks_volume_drop("sales", "landing")
    with pytest.raises(ValueError, match="needs your confirmation"):
        svc.databricks_volume_drop("sales", "landing", confirm="yes")  # type: ignore[arg-type]
    assert (here / "sales" / "landing" / "a.csv").exists()
    assert svc.databricks_volume_drop("sales", "landing", confirm=True) == {"dropped": "main.sales.landing"}
    assert not (here / "sales" / "landing").exists() and svc.databricks_volumes()["volumes"] == []
    with pytest.raises(ValueError, match="cannot be found"):
        svc.databricks_volume_drop("sales", "landing", confirm=True)


def test_drop_removes_a_link_inside_without_following_it(svc, here, outside):
    _link(here / "sales" / "landing" / "out", outside)
    svc.databricks_volume_drop("sales", "landing", confirm=True)
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "not yours"


# ---- files

def test_files_lists_folders_first_and_caps_the_list(svc, here):
    folder = here / "sales" / "landing"
    (folder / "b.csv").write_bytes(b"12345")
    (folder / "A.parquet").write_bytes(b"1")
    (folder / "2026-11-22").mkdir()
    out = svc.databricks_volume_files(V)
    assert out["path"] == V and out["truncated"] is False
    assert [(e["name"], e["is_dir"], e["size"]) for e in out["entries"]] == [
        ("2026-11-22", True, 0), ("A.parquet", False, 1), ("b.csv", False, 5)]
    assert out["entries"][2]["path"] == f"dbfs:{V}/b.csv"
    capped = svc.databricks_volume_files(V, limit=2)
    assert len(capped["entries"]) == 2 and capped["truncated"] is True
    assert svc.databricks_volume_files(f"dbfs:{V}/2026-11-22")["entries"] == []


@pytest.mark.parametrize("bad", [f"{V}/../finance", f"{V}/./x", f"{V}/a\\..\\b", "/Volumes/other/sales/landing",
                                 f"{V}/C:", "C:\\Windows", "/Volumes/main/sales", f"{V}/nul"])
def test_files_refuses_a_path_that_names_somewhere_else(svc, bad):
    with pytest.raises(ValueError):
        svc.databricks_volume_files(bad)


def test_files_refuses_a_folder_reached_through_a_link(svc, here, outside):
    _link(here / "sales" / "landing" / "out", outside)
    assert [e["name"] for e in svc.databricks_volume_files(V)["entries"]] == ["out"]  # listed by name only
    with pytest.raises(ValueError, match="leads outside its volume"):
        svc.databricks_volume_files(f"{V}/out")


def test_files_of_a_volume_that_is_not_there(svc):
    with pytest.raises(ValueError, match="cannot be found"):
        svc.databricks_volume_files("/Volumes/main/sales/nothing")
    with pytest.raises(FileNotFoundError):
        svc.databricks_volume_files(f"{V}/missing")


# ---- new folder

def test_new_folder(svc, here):
    assert svc.databricks_volume_new_folder(f"{V}/2026-11-22") == {"path": f"{V}/2026-11-22"}
    assert (here / "sales" / "landing" / "2026-11-22").is_dir()
    with pytest.raises(ValueError, match="already there"):
        svc.databricks_volume_new_folder(f"{V}/2026-11-22")
    with pytest.raises(ValueError, match="isn't there"):
        svc.databricks_volume_new_folder(f"{V}/no/such")
    with pytest.raises(ValueError, match="is the volume itself"):
        svc.databricks_volume_new_folder(V)


@pytest.mark.parametrize("bad", [f"{V}/..", f"{V}/../../x", f"{V}/a\\b", f"{V}/x:y", f"{V}/trail.", f"{V}/CON"])
def test_new_folder_refuses_escapes_and_names_windows_changes(svc, here, bad):
    with pytest.raises(ValueError):
        svc.databricks_volume_new_folder(bad)
    assert sorted(p.name for p in (here / "sales").iterdir()) == [".landing.volume.json", "landing"]


def test_new_folder_refuses_one_through_a_link(svc, here, outside):
    _link(here / "sales" / "landing" / "out", outside)
    with pytest.raises(ValueError, match="leads outside"):
        svc.databricks_volume_new_folder(f"{V}/out/made")
    assert not (outside / "made").exists()


# ---- upload

def test_upload_copies_a_file_in_and_asks_before_replacing(svc, here, tmp_path):
    src = tmp_path / "orders.csv"
    src.write_text("id\n1\n", encoding="utf-8")
    assert svc.databricks_volume_upload(str(src), V) == {"path": f"{V}/orders.csv", "size": 5}
    assert (here / "sales" / "landing" / "orders.csv").read_text(encoding="utf-8") == "id\n1\n"
    src.write_text("id\n2\n", encoding="utf-8")
    assert svc.databricks_volume_upload(str(src), V) == {"exists": True, "path": f"{V}/orders.csv"}
    assert (here / "sales" / "landing" / "orders.csv").read_text(encoding="utf-8") == "id\n1\n"
    svc.databricks_volume_upload(str(src), f"dbfs:{V}", overwrite=True)
    assert (here / "sales" / "landing" / "orders.csv").read_text(encoding="utf-8") == "id\n2\n"
    assert [p.name for p in (here / "sales" / "landing").iterdir()] == ["orders.csv"]  # no .part left


def test_upload_refuses_what_is_not_a_file_or_not_a_folder_in_a_volume(svc, here, tmp_path):
    src = tmp_path / "orders.csv"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="full path"):
        svc.databricks_volume_upload("orders.csv", V)
    with pytest.raises(ValueError, match="isn't a file"):
        svc.databricks_volume_upload(str(tmp_path), V)
    with pytest.raises(ValueError, match="isn't a folder in the volume"):
        svc.databricks_volume_upload(str(src), f"{V}/missing")
    (here / "sales" / "landing" / "orders.csv").mkdir()
    with pytest.raises(ValueError, match="is a folder"):
        svc.databricks_volume_upload(str(src), V)


@pytest.mark.parametrize("bad", [f"{V}/..", "/Volumes/main/sales", f"{V}/a\\..\\..", "C:\\Temp", "/tmp"])
def test_upload_refuses_a_target_outside_a_volume(svc, tmp_path, bad):
    src = tmp_path / "orders.csv"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        svc.databricks_volume_upload(str(src), bad)


def test_upload_refuses_a_folder_reached_through_a_link(svc, here, tmp_path, outside):
    _link(here / "sales" / "landing" / "out", outside)
    src = tmp_path / "orders.csv"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="leads outside"):
        svc.databricks_volume_upload(str(src), f"{V}/out")
    assert not (outside / "orders.csv").exists()


def test_upload_refuses_a_file_named_as_a_volume_path_cannot_hold(svc, tmp_path):
    src = tmp_path / "bad:name.csv"
    try:
        src.write_text("x", encoding="utf-8")
    except OSError:
        pytest.skip("this file system can't hold that name either")
    with pytest.raises(ValueError, match="can't hold"):
        svc.databricks_volume_upload(str(src), V)


# ---- download

def test_download_copies_a_file_out_and_asks_before_replacing(svc, here, tmp_path):
    (here / "sales" / "landing" / "orders.csv").write_text("id\n1\n", encoding="utf-8")
    out = tmp_path / "me" / "Downloads"
    out.mkdir(parents=True)
    target = out / "orders.csv"
    assert svc.databricks_volume_download(f"dbfs:{V}/orders.csv", str(target)) == {"saved_to": str(target), "size": 5}
    assert target.read_text(encoding="utf-8") == "id\n1\n"
    target.write_text("mine", encoding="utf-8")
    assert svc.databricks_volume_download(f"{V}/orders.csv", str(target)) == {"exists": True, "saved_to": str(target)}
    assert target.read_text(encoding="utf-8") == "mine"
    svc.databricks_volume_download(f"{V}/orders.csv", str(target), overwrite=True)
    assert target.read_text(encoding="utf-8") == "id\n1\n"


def test_download_refuses_a_target_in_onedrive(svc, here, tmp_path, monkeypatch):
    (here / "sales" / "landing" / "orders.csv").write_text("x", encoding="utf-8")
    cloud = tmp_path / "me" / "OneDrive - Contoso" / "Documents"
    cloud.mkdir(parents=True)
    with pytest.raises(ValueError, match="is in OneDrive .*a file downloaded from a volume can't be kept there"):
        svc.databricks_volume_download(f"{V}/orders.csv", str(cloud / "orders.csv"))
    monkeypatch.setenv("OneDriveCommercial", str(tmp_path / "Sync"))
    (tmp_path / "Sync").mkdir()
    with pytest.raises(ValueError, match="is in OneDrive"):
        svc.databricks_volume_download(f"{V}/orders.csv", str(tmp_path / "Sync" / "orders.csv"))
    assert not (cloud / "orders.csv").exists() and not (tmp_path / "Sync" / "orders.csv").exists()


def test_download_refuses_a_target_in_the_volumes_folder_or_not_a_file(svc, here, tmp_path):
    folder = here / "sales" / "landing"
    (folder / "orders.csv").write_text("x", encoding="utf-8")
    (folder / "sub").mkdir()
    with pytest.raises(ValueError, match="inside this app's own data folder"):
        svc.databricks_volume_download(f"{V}/orders.csv", str(folder / "copy.csv"))
    with pytest.raises(ValueError, match="full path"):
        svc.databricks_volume_download(f"{V}/orders.csv", "copy.csv")
    with pytest.raises(ValueError, match="isn't a folder on this PC"):
        svc.databricks_volume_download(f"{V}/orders.csv", str(tmp_path / "no" / "copy.csv"))
    with pytest.raises(ValueError, match="is a folder"):
        svc.databricks_volume_download(f"{V}/orders.csv", str(tmp_path))
    with pytest.raises(ValueError, match="isn't a file"):
        svc.databricks_volume_download(f"{V}/sub", str(tmp_path / "sub"))
    with pytest.raises(ValueError, match="is the volume itself"):
        svc.databricks_volume_download(V, str(tmp_path / "all"))


def test_download_never_writes_into_the_apps_data_folder(svc, here, tmp_path):
    # the audit trail, settings and caches live beside the volumes; a download may not replace one of them
    (here / "sales" / "landing" / "orders.csv").write_text("x", encoding="utf-8")
    app = tmp_path / "appdata" / "FabricDesktop"
    (app / "audit").mkdir(parents=True)
    trail = app / "audit" / "x.jsonl"
    trail.write_text("kept", encoding="utf-8")
    for target in (trail, app / "settings.json", app / "audit" / "new.jsonl"):
        with pytest.raises(ValueError, match="inside this app's own data folder"):
            svc.databricks_volume_download(f"{V}/orders.csv", str(target), overwrite=True)
    assert trail.read_text(encoding="utf-8") == "kept"
    assert not (app / "settings.json").exists() and not (app / "audit" / "new.jsonl").exists()


def test_download_through_a_link_into_the_apps_data_folder_is_refused(svc, here, tmp_path):
    (here / "sales" / "landing" / "orders.csv").write_text("x", encoding="utf-8")
    app = tmp_path / "appdata" / "FabricDesktop"
    (app / "audit").mkdir(parents=True)
    try:
        (tmp_path / "elsewhere").symlink_to(app / "audit", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    with pytest.raises(ValueError, match="inside this app's own data folder"):
        svc.databricks_volume_download(f"{V}/orders.csv", str(tmp_path / "elsewhere" / "x.jsonl"))
    assert not (app / "audit" / "x.jsonl").exists()


def test_upload_never_copies_a_file_of_the_apps_data_folder(svc, here, tmp_path):
    app = tmp_path / "appdata" / "FabricDesktop"
    (app / "audit").mkdir(parents=True)
    (app / "settings.json").write_text("{}", encoding="utf-8")
    (app / "audit" / "x.jsonl").write_text("{}", encoding="utf-8")
    (here / "sales" / "landing" / "a.csv").write_text("x", encoding="utf-8")
    for source in (app / "settings.json", app / "audit" / "x.jsonl", here / "sales" / "landing" / "a.csv"):
        with pytest.raises(ValueError, match="inside this app's own data folder"):
            svc.databricks_volume_upload(str(source), f"{V}/")
    assert sorted(p.name for p in (here / "sales" / "landing").iterdir()) == ["a.csv"]


@pytest.mark.parametrize("bad", [f"{V}/../../finance/raw/x.csv", f"{V}/..\\x", "/Volumes/hr/sales/landing/x",
                                 "C:\\Windows\\win.ini", "/etc/passwd", f"{V}/x.csv:stream"])
def test_download_refuses_a_source_outside_a_volume(svc, tmp_path, bad):
    with pytest.raises(ValueError):
        svc.databricks_volume_download(bad, str(tmp_path / "x"))
    assert not (tmp_path / "x").exists()


def test_download_refuses_a_file_reached_through_a_link(svc, here, tmp_path, outside):
    _link(here / "sales" / "landing" / "secret.txt", outside / "secret.txt")
    with pytest.raises(ValueError, match="leads outside"):
        svc.databricks_volume_download(f"{V}/secret.txt", str(tmp_path / "copy.txt"))
    assert not (tmp_path / "copy.txt").exists()


# ---- delete

def test_delete_needs_confirm_and_removes_files_and_folders(svc, here):
    folder = here / "sales" / "landing"
    (folder / "a.csv").write_text("x", encoding="utf-8")
    (folder / "day").mkdir()
    (folder / "day" / "b.csv").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="needs your confirmation"):
        svc.databricks_volume_delete([f"{V}/a.csv"])
    assert (folder / "a.csv").exists()
    out = svc.databricks_volume_delete([f"{V}/a.csv", f"dbfs:{V}/day"], confirm=True)
    assert out == {"deleted": [f"{V}/a.csv", f"{V}/day"]}
    assert list(folder.iterdir()) == []
    with pytest.raises(FileNotFoundError):
        svc.databricks_volume_delete([f"{V}/a.csv"], confirm=True)
    with pytest.raises(ValueError, match="pick the files"):
        svc.databricks_volume_delete([], confirm=True)


def test_delete_refuses_the_volume_itself_and_paths_outside_it_removing_nothing(svc, here):
    keep = here / "sales" / "landing" / "keep.csv"
    keep.write_text("x", encoding="utf-8")
    for bad in (V, f"dbfs:{V}/", f"{V}/..", f"{V}/../landing/keep.csv", "/Volumes/main/sales", f"{V}/a\\b",
                "C:\\Windows"):
        with pytest.raises(ValueError):
            svc.databricks_volume_delete([f"{V}/keep.csv", bad], confirm=True)
    assert keep.exists()


def test_delete_refuses_a_folder_holding_a_link_and_a_path_through_one(svc, here, outside):
    folder = here / "sales" / "landing"
    (folder / "day").mkdir()
    _link(folder / "day" / "out", outside)
    with pytest.raises(ValueError, match="holds a link or junction"):
        svc.databricks_volume_delete([f"{V}/day"], confirm=True)
    with pytest.raises(ValueError, match="leads outside"):
        svc.databricks_volume_delete([f"{V}/day/out/secret.txt"], confirm=True)
    assert (outside / "secret.txt").exists() and (folder / "day" / "out").exists()


# ---- reveal

def test_reveal_shows_a_volume_or_a_file_and_refuses_outside(svc, here, monkeypatch, outside):
    import fabric_desktop.notebook_resources_service as nrs
    shown = []
    monkeypatch.setattr(nrs, "_reveal", lambda path, select: shown.append((Path(path), select)))
    (here / "sales" / "landing" / "a.csv").write_text("x", encoding="utf-8")
    assert svc.databricks_volume_reveal(V) == {"shown": str(here / "sales" / "landing")}
    svc.databricks_volume_reveal(f"{V}/a.csv")
    assert shown == [(here / "sales" / "landing", False), (here / "sales" / "landing" / "a.csv", True)]
    with pytest.raises(FileNotFoundError):
        svc.databricks_volume_reveal(f"{V}/missing")
    _link(here / "sales" / "landing" / "out", outside)
    for bad in (f"{V}/out", f"{V}/..", "/Volumes/main/sales"):
        with pytest.raises(ValueError):
            svc.databricks_volume_reveal(bad)
    assert len(shown) == 2


def test_the_rpcs_answer_over_the_engine_s_json_lines(here, tmp_path):
    import json

    from fabric_desktop.service import Service
    s = Service(client_id="x", root=tmp_path / "app")
    call = lambda method, **params: server.handle(s, json.dumps({"id": 1, "method": method, "params": params}))  # noqa: E731
    assert call("databricks_volume_create", schema="sales", volume="landing")["result"]["path"] == V
    assert call("databricks_volumes")["result"]["volumes"][0]["name"] == "landing"
    assert "error" in call("databricks_volume_files", path=f"{V}/..")
    assert call("databricks_volume_drop", schema="sales", volume="landing", confirm=True)["result"] == {
        "dropped": "main.sales.landing"}
    assert dv.volumes() == []
