"""A local data project's Delta writes on a Windows PC where long paths are off.

delta-rs writes a data file past 260 characters anyway, but then the flush to disk and DuckDB can't
open it, so the write used to report success and leave a table that can't be read. A write whose
files would pass the limit is now refused before anything is written, and a committed table that
can't be read is reported as written but unreadable, never as a plain success. Here long paths are
reported off rather than really off; a Windows PC with them off is UNVERIFIED by this test.
"""

from __future__ import annotations

import pytest

from fabric_desktop import support_bundle
from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as engine_mod


@pytest.fixture
def project(tmp_path):
    data = LocalData(tmp_path / "local-data")
    pid = data.create("long paths")["id"]
    try:
        yield data.engine(pid)
    finally:
        data.close_all()


def test_a_write_whose_files_would_pass_260_characters_is_refused_before_anything_is_written(project, monkeypatch):
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    name = "t" + "x" * max(1, 230 - len(str(project.tables_dir / "dbo")))
    with pytest.raises(ValueError, match="long paths aren't turned on"):
        project.write_delta(name, "SELECT 1 AS id")
    assert not (project.tables_dir / "dbo" / name).exists()
    with pytest.raises(Exception, match="does not exist"):
        project.execute(f"SELECT * FROM {name}")


def test_the_same_write_goes_ahead_where_long_paths_are_on_or_off_windows(project, monkeypatch):
    name = "t" + "x" * max(1, 230 - len(str(project.tables_dir / "dbo")))
    for state in (True, None):
        monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda state=state: state)
        project.write_delta(name, "SELECT 1 AS id", mode="overwrite")
    assert project.execute(f"SELECT id FROM {name}")["rows"] == [[1]]


def test_a_short_path_is_written_with_long_paths_off(project, monkeypatch):
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    project.write_delta("p", "SELECT 1 AS id, 'a' AS r", partition_by=["r"])
    assert project.execute("SELECT id, r FROM p")["rows"] == [[1, "a"]]


def test_a_committed_table_that_cant_be_read_isnt_reported_as_a_plain_success(project, monkeypatch):
    def unreadable(*_a, **_k):
        raise OSError("[Errno 2] No such file or directory")
    monkeypatch.setattr(engine_mod, "delta_select", unreadable)
    with pytest.raises(ValueError, match=r"p was written \(version 0\), but it can't be read here"):
        project.write_delta("p", "SELECT 1 AS id")
    assert (project.tables_dir / "dbo" / "p" / "_delta_log").is_dir()
