"""Finding a local data source's tables (localdata/discovery.py), the paths the main tests don't reach:
source settings that can't be used, folders and files that can't be opened, folders that hold tables
rather than being one, names that can't be table names, links, and a source with too many files.
Only the file system is read."""

from __future__ import annotations

import os

import pytest

from fabric_desktop.localdata import discovery as D


def touch(path, text="a\n1\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---- source settings


@pytest.mark.parametrize("kw, needle", [
    ({"naming": "nested"}, "naming must be one of"),
    ({"max_depth": 11}, "max_depth must be between 0 and 10"),
    ({"max_depth": -1}, "max_depth must be between 0 and 10"),
    ({"schema": ' dbo'}, "Invalid schema name"),
    ({"names": {"a.csv": "orders"}}, "Name 'a.csv' as schema.table"),
    ({"names": {"a.csv": 'sales.or"ders'}}, "Invalid table name"),
])
def test_source_settings_that_cant_be_used_are_refused(tmp_path, kw, needle):
    with pytest.raises(ValueError, match=needle):
        D.Source(str(tmp_path), **kw)


def test_a_folder_that_isnt_there_is_one_issue(tmp_path):
    out = D.discover(D.Source(str(tmp_path / "nope")))
    assert out.tables == [] and out.fingerprint == "missing"
    assert out.issues[0].problem == "The folder doesn't exist or can't be opened."


# ---- folders


def test_a_folder_with_partitions_and_a_real_sub_folder_groups_tables(tmp_path):
    touch(tmp_path / "sales" / "year=2026" / "part-0.parquet")
    touch(tmp_path / "sales" / "returns" / "r.csv")
    assert D._dataset_folder(tmp_path / "sales", []) is None
    out = D.discover(D.Source(str(tmp_path)))
    assert [t.key for t in out.tables] == ["sales.returns_r", "sales.year=2026"]


def test_partitions_without_data_files_arent_a_table(tmp_path):
    touch(tmp_path / "events" / "day=1" / "readme.txt")
    assert D._hive_format(tmp_path / "events") is None
    assert D._dataset_folder(tmp_path / "events", []) is None


def test_part_files_beside_other_files_are_still_one_table(tmp_path):
    touch(tmp_path / "orders" / "part-0.csv")
    touch(tmp_path / "orders" / "part-1.csv")
    touch(tmp_path / "orders" / "README.md")
    assert D._dataset_folder(tmp_path / "orders", []) == ("csv", False)
    out = D.discover(D.Source(str(tmp_path), naming="flat"))
    assert [(t.key, t.files) for t in out.tables] == [("dbo.orders", 3)]


def test_folders_and_files_that_cant_be_opened_are_issues_and_the_rest_are_found(tmp_path, monkeypatch):
    touch(tmp_path / "locked" / "a.csv")
    touch(tmp_path / "ok.csv")
    real = os.scandir

    def scandir(path):
        if os.fspath(path).endswith("locked"):
            raise PermissionError(13, "Access is denied")
        return real(path)

    monkeypatch.setattr(D.os, "scandir", scandir)
    assert D._dataset_folder(tmp_path / "locked", []) is None
    out = D.discover(D.Source(str(tmp_path)))
    assert [t.key for t in out.tables] == ["dbo.ok"]
    assert [(os.path.basename(i.path), i.problem) for i in out.issues] == [
        ("locked", "Can't open the folder: Access is denied")]


def test_a_file_that_vanishes_while_a_table_folder_is_sized_is_left_out(tmp_path, monkeypatch):
    touch(tmp_path / "t" / "part-0.csv")
    touch(tmp_path / "t" / "part-1.csv", "a\n1\n2\n")
    real = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path).endswith("part-0.csv"):
            raise FileNotFoundError(2, "gone")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(D.os, "stat", stat)
    size, count, _newest = D._folder_stats(tmp_path / "t")
    assert (size, count) == (6, 1)


# ---- files


def test_a_file_whose_name_cant_be_a_table_name_is_an_issue(tmp_path):
    touch(tmp_path / 'say "hi".csv')
    touch(tmp_path / "ok.csv")
    out = D.discover(D.Source(str(tmp_path)))
    assert [t.key for t in out.tables] == ["dbo.ok"]
    assert len(out.issues) == 1 and out.issues[0].problem.startswith("Invalid table name")


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need a privilege on Windows")
def test_links_and_files_that_arent_data_are_left_out(tmp_path):
    outside = touch(tmp_path / "outside" / "secret.csv")
    src = tmp_path / "src"
    touch(src / "notes.txt")
    touch(src / "ok.csv")
    (src / "link.csv").symlink_to(outside)
    out = D.discover(D.Source(str(src)))
    assert [t.key for t in out.tables] == ["dbo.ok"] and out.issues == []


def test_a_source_with_more_files_than_the_limit_stops_and_says_so(tmp_path):
    for n in range(4):
        touch(tmp_path / f"t{n}.csv")
    out = D.discover(D.Source(str(tmp_path)), max_files=2)
    assert [t.table for t in out.tables] == ["t0", "t1"]
    assert out.issues[-1].problem.startswith("More than 2 data files: discovery stopped there.")


def test_the_fingerprint_changes_when_a_file_is_added(tmp_path):
    touch(tmp_path / "a.csv")
    before = D.fingerprint(D.Source(str(tmp_path)))
    assert before == D.fingerprint(D.Source(str(tmp_path)))
    touch(tmp_path / "b.csv")
    assert D.fingerprint(D.Source(str(tmp_path))) != before
