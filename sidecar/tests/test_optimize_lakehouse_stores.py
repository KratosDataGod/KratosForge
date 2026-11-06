"""Where Optimize's tables live (optimize/lakehouse.py): listing tables and schemas in a OneLake lakehouse
with and without schemas, a new lakehouse with no Tables folder yet, and the folder on this PC without
schemas. No network: a stand-in for OneLake's file listing."""

from __future__ import annotations

import pytest
import requests

from fabric_desktop.optimize import schema
from fabric_desktop.optimize.lakehouse import LocalLakehouse, OneLakeLakehouse, _Tables


class Files:
    """OneLake's directory listing: folder -> entries, or an exception to raise."""

    def __init__(self, folders):
        self.folders, self.asked = folders, []

    def list(self, workspace_id, lakehouse_id, folder):
        self.asked.append((workspace_id, lakehouse_id, folder))
        found = self.folders.get(folder, [])
        if isinstance(found, Exception):
            raise found
        return found


def _dir(name):
    return {"name": name, "is_dir": True}


def _missing():
    resp = requests.Response()
    resp.status_code = 404
    return requests.HTTPError("404", response=resp)


def lake(tmp_path, folders, schemas=True):
    return OneLakeLakehouse("ws-1", "lh-1", schemas, lambda: "tok", tmp_path / "cache", files=Files(folders))


def test_a_lakehouse_with_schemas_lists_dbo_and_the_family_schemas_it_has(tmp_path):
    other = next((s for s in schema.schemas() if s != schema.SCHEMA), None)
    folders = {"Tables": [_dir("dbo"), {"name": "readme.txt", "is_dir": False}],
               "Tables/dbo": [_dir("fdopt_runs"), _dir("fdopt_findings"), {"name": "x", "is_dir": False}]}
    if other:
        folders[f"Tables/{other}"] = [_dir("never_listed")]  # its schema folder isn't there
    lh = lake(tmp_path, folders)
    assert lh.schema_names() == ["dbo"]
    assert lh.table_names() == ["fdopt_findings", "fdopt_runs"]
    assert all(a[:2] == ("ws-1", "lh-1") for a in lh._files.asked)
    assert lh.describe() == {"kind": "onelake", "workspace_id": "ws-1", "lakehouse_id": "lh-1"}


def test_a_new_lakehouse_has_no_tables_yet(tmp_path):
    lh = lake(tmp_path, {"Tables": _missing(), "Tables/dbo": _missing()})
    assert lh.schema_names() == [] and lh.table_names() == []


def test_any_other_listing_failure_is_raised(tmp_path):
    resp = requests.Response()
    resp.status_code = 403
    lh = lake(tmp_path, {"Tables": requests.HTTPError("403", response=resp)})
    with pytest.raises(requests.HTTPError):
        lh.schema_names()
    with pytest.raises(ValueError):
        lake(tmp_path, {"Tables/dbo": ValueError("bad listing")}).table_names()


def test_a_lakehouse_without_schemas_lists_tables_straight_under_tables(tmp_path):
    lh = lake(tmp_path, {"Tables": [_dir("fdopt_runs")]}, schemas=False)
    assert lh.schema_names() == [] and lh.table_names() == ["fdopt_runs"]
    assert lh.uri("optimization", "dbo", "fdopt_runs").endswith("/Tables/fdopt_runs")
    with_schemas = lake(tmp_path, {}, schemas=True)
    assert with_schemas.uri("optimization", "dbo", "fdopt_runs").endswith("/Tables/dbo/fdopt_runs")


def test_a_folder_without_schemas_keeps_its_tables_under_tables(tmp_path):
    lh = LocalLakehouse(tmp_path / "lake", schemas=False)
    assert lh.schema_names() == [] and lh.table_names() == []
    (tmp_path / "lake" / "Tables" / "fdopt_runs").mkdir(parents=True)
    assert lh.table_names() == ["fdopt_runs"]
    assert lh.uri("optimization", "dbo", "fdopt_runs") == str(tmp_path / "lake" / "Tables" / "fdopt_runs")
    assert lh.describe() == {"kind": "folder", "path": str(tmp_path / "lake")}


def test_a_folder_with_schemas_lists_its_schema_folders(tmp_path):
    lh = LocalLakehouse(tmp_path / "lake")
    assert lh.schema_names() == []  # no Tables folder yet
    (tmp_path / "lake" / "Tables" / "dbo").mkdir(parents=True)
    (tmp_path / "lake" / "Tables" / "notes.txt").write_text("")
    assert lh.schema_names() == ["dbo"]


def test_the_base_has_no_listing_of_its_own():
    with pytest.raises(NotImplementedError):
        _Tables().table_names()
    with pytest.raises(NotImplementedError):
        _Tables().schema_names()
    with pytest.raises(NotImplementedError):
        _Tables().describe()
