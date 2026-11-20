"""warehouse_project_service.py limits at their exact edges (mutation batch 163, a boundary re-score of
batch 21, docs/quality/mutation-testing.md): a file or script of exactly the size limit is taken, the
compare takes exactly the object limit, and files sit at most the folder limit deep."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import warehouse_project_service as wp
from fabric_desktop.service import Service

LIMIT = 64  # MAX_FILE is set this small, so a file of exactly the limit is cheap to make


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture
def svc(tmp_path):
    return Service(client_id="x", root=tmp_path / "app")


@pytest.fixture
def root(svc, tmp_path, monkeypatch):
    r = (tmp_path / "sales").resolve()
    r.mkdir()
    monkeypatch.setattr(svc, "git_repos", lambda: {"repos": [{"exists": True, "path": str(r), "name": "Sales repo"}]})
    return r


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(wp, "MAX_FILE", LIMIT)


def _exactly(text: str) -> str:
    """``text`` padded with a SQL comment to exactly LIMIT bytes."""
    pad = LIMIT - len(text.encode()) - 3
    assert pad >= 0
    return text + "--" + "x" * pad + "\n"


TABLE = "CREATE TABLE dbo.A (x int);\n"


def test_a_file_of_exactly_the_limit_is_listed_with_its_object(svc, root, small):
    project = root / "WH.Warehouse"
    project.mkdir()
    (project / "a.sql").write_text(_exactly(TABLE), newline="\n")
    assert (project / "a.sql").stat().st_size == LIMIT
    [item] = svc.wh_project_files(str(root), "WH.Warehouse")["files"]
    assert item["name"] == "A" and item["problem"] == ""


def test_a_save_of_exactly_the_limit_is_written(svc, root, small):
    project = root / "WH.Warehouse"
    project.mkdir()
    (project / "a.sql").write_text(TABLE, newline="\n")
    expect = svc.wh_project_read(str(root), "WH.Warehouse", "a.sql")["version"]
    text = _exactly(TABLE)
    svc.wh_project_save(str(root), "WH.Warehouse", "a.sql", text, expect)
    assert (project / "a.sql").read_text() == text


def test_an_object_whose_script_is_exactly_the_limit_is_added(svc, root, small):
    (root / "WH.Warehouse").mkdir()
    text = _exactly(TABLE)
    out = svc.wh_project_write(str(root), [{"kind": "table", "schema": "dbo", "name": "A", "text": text}],
                               folder="WH.Warehouse", dry_run=True)
    assert out["counts"]["added"] == 1 and out["counts"]["skipped"] == 0


def test_an_existing_file_of_exactly_the_limit_still_says_where_its_object_lives(svc, root, small):
    project = root / "WH.Warehouse"
    (project / "odd").mkdir(parents=True)
    (project / "odd" / "a.sql").write_text(_exactly(TABLE), newline="\n")
    out = svc.wh_project_write(str(root), [{"kind": "table", "schema": "dbo", "name": "A", "text": TABLE}],
                               folder="WH.Warehouse", dry_run=True)
    assert [r["path"] for r in out["changed"]] == ["odd/a.sql"]


def test_compare_reads_a_file_of_exactly_the_limit(svc, root, small):
    project = root / "WH.Warehouse"
    project.mkdir()
    (project / "a.sql").write_text(_exactly(TABLE), newline="\n")
    out = svc.wh_project_compare(str(root), "WH.Warehouse", [])
    assert out["unread"] == []


def test_compare_takes_exactly_the_object_limit(svc, root):
    (root / "WH.Warehouse").mkdir()
    assert isinstance(svc.wh_project_compare(str(root), "WH.Warehouse", [{}] * wp.MAX_OBJECTS), dict)


def test_files_are_listed_down_to_the_folder_limit_and_no_deeper(svc, root):
    project = root / "WH.Warehouse"
    deep = project.joinpath(*[f"d{i}" for i in range(wp.MAX_DEPTH)])
    deep.mkdir(parents=True)
    (deep / "a.sql").write_text(TABLE)
    (deep / "more").mkdir()
    (deep / "more" / "b.sql").write_text(TABLE.replace(".A", ".B"))
    paths = [f["path"] for f in svc.wh_project_files(str(root), "WH.Warehouse")["files"]]
    assert paths == ["/".join([f"d{i}" for i in range(wp.MAX_DEPTH)] + ["a.sql"])]
