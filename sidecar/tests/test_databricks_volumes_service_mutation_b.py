"""Mutation batch 486: the Volumes screen's list cap, a delete that refuses before it removes
anything, links inside a volume, and an upload from a link."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from fabric_desktop import databricks_volumes_service as dvs
from fabric_desktop import known_folders

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
    s.databricks_volume_create("sales", "landing")
    return s


def _link(link: Path, to: Path) -> None:
    try:
        os.symlink(to, link, target_is_directory=to.is_dir())
    except (OSError, NotImplementedError) as e:  # Windows without the right to make links
        pytest.skip(f"can't make a link here: {e}")


def test_a_folder_lists_at_most_2000_entries_whatever_limit_is_asked(svc, here):
    folder = here / "sales" / "landing"
    for i in range(2001):
        (folder / f"f{i:05d}").write_bytes(b"")
    for limit in (None, 5000):
        out = svc.databricks_volume_files(V, limit=limit)
        assert len(out["entries"]) == 2000 and out["truncated"] is True


def test_a_limit_below_one_lists_one_entry(svc, here):
    folder = here / "sales" / "landing"
    (folder / "a").write_bytes(b"")
    (folder / "b").write_bytes(b"")
    out = svc.databricks_volume_files(V, limit=-3)
    assert [e["name"] for e in out["entries"]] == ["a"] and out["truncated"] is True


def test_a_folder_holding_exactly_the_limit_is_not_truncated(svc, here):
    folder = here / "sales" / "landing"
    (folder / "a").write_bytes(b"")
    (folder / "b").write_bytes(b"")
    out = svc.databricks_volume_files(V, limit=2)
    assert len(out["entries"]) == 2 and out["truncated"] is False


def test_a_delete_naming_a_missing_file_removes_nothing(svc, here):
    keep = here / "sales" / "landing" / "a.csv"
    keep.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="doesn't exist"):
        svc.databricks_volume_delete([f"{V}/a.csv", f"{V}/gone.csv"], confirm=True)
    assert keep.exists()


def test_a_link_to_a_folder_in_the_volume_is_deleted_as_a_link_without_reading_inside(svc, here):
    folder = here / "sales" / "landing"
    (folder / "real").mkdir()
    (folder / "real" / "x.csv").write_text("x", encoding="utf-8")
    _link(folder / "real" / "inner", folder / "real" / "x.csv")
    _link(folder / "alias", folder / "real")
    assert svc.databricks_volume_delete([f"{V}/alias"], confirm=True) == {"deleted": [f"{V}/alias"]}
    assert not os.path.lexists(folder / "alias") and (folder / "real" / "x.csv").exists()


def test_a_file_picked_through_a_link_and_the_link_itself_are_both_removed(svc, here):
    folder = here / "sales" / "landing"
    (folder / "real").mkdir()
    (folder / "real" / "x.csv").write_text("x", encoding="utf-8")
    _link(folder / "alias", folder / "real")
    out = svc.databricks_volume_delete([f"{V}/alias/x.csv", f"{V}/alias"], confirm=True)
    assert out == {"deleted": [f"{V}/alias/x.csv", f"{V}/alias"]}
    assert not (folder / "real" / "x.csv").exists() and not os.path.lexists(folder / "alias")


def test_an_upload_from_a_link_copies_the_file_it_points_at(svc, here, tmp_path):
    real = tmp_path / "real.csv"
    real.write_bytes(b"id\n1\n")
    _link(tmp_path / "orders.csv", real)
    assert svc.databricks_volume_upload(str(tmp_path / "orders.csv"), V) == {"path": f"{V}/orders.csv", "size": 5}
    target = here / "sales" / "landing" / "orders.csv"
    assert not os.path.islink(target) and target.read_bytes() == b"id\n1\n"


def test_a_link_picked_before_a_file_through_it_is_deleted_without_an_error(svc, here):
    folder = here / "sales" / "landing"
    (folder / "real").mkdir()
    (folder / "real" / "x.csv").write_text("x", encoding="utf-8")
    _link(folder / "alias", folder / "real")
    out = svc.databricks_volume_delete([f"{V}/alias", f"{V}/alias/x.csv"], confirm=True)
    assert f"{V}/alias" in out["deleted"] and not os.path.lexists(folder / "alias")
