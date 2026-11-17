"""localdata/discovery.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 4)
showed no test pinned. Which format wins when two files make one table, the depth and file limits at
their edges, exclusions, dotted names below the source, writer leftovers and links inside a table
folder, the flags and counts a table carries, and where an issue points. Only the file system is read."""

from __future__ import annotations

import os
import sys

import pytest

from fabric_desktop.localdata import discovery as D

links = pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need extra rights on Windows")


def touch(path, text="a\n1\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def delta(folder):
    (folder / "_delta_log").mkdir(parents=True)
    return folder


def tables(tmp_path, **kw):
    found = D.discover(D.Source(str(tmp_path), naming="flat", **kw))
    return {t.key: t for t in found.tables}, found.issues


# Each pair: the format that should win sits at "z", the one that should lose at "a", so a tie in
# precedence would hand the table to "a" (the paths break ties).
@pytest.mark.parametrize("winner, loser, make_winner, make_loser", [
    ("delta", "parquet", lambda p: delta(p / "z"), lambda p: touch(p / "a.parquet")),
    ("parquet", "csv", lambda p: touch(p / "z.parquet"), lambda p: touch(p / "a.csv")),
    ("csv", "json", lambda p: touch(p / "z.csv"), lambda p: touch(p / "a.json", "{}")),
    ("json", "excel", lambda p: touch(p / "z.jsonl", "{}"), lambda p: touch(p / "a.xlsx")),
])
def test_the_format_that_comes_first_wins_a_shared_table_name(tmp_path, winner, loser, make_winner, make_loser):
    w, lo = make_winner(tmp_path), make_loser(tmp_path)
    names = {os.path.relpath(w, tmp_path): "dbo.t", os.path.relpath(lo, tmp_path): "dbo.t"}
    found, issues = tables(tmp_path, names=names)
    assert found["dbo.t"].format == winner
    assert [i.path for i in issues] == [str(lo)]  # the loser is pointed at by its own path
    assert f"({winner}) is" in issues[0].problem


def test_four_folders_down_is_searched_by_default_and_five_is_not(tmp_path):
    touch(tmp_path / "a" / "b" / "c" / "d" / "four.csv")
    touch(tmp_path / "a" / "b" / "c" / "d" / "e" / "five.csv")
    found, _ = tables(tmp_path)
    assert list(found) == ["dbo.a_b_c_d_four"]


@pytest.mark.parametrize("depth, ok", [(0, True), (10, True), (-1, False), (11, False)])
def test_max_depth_from_zero_to_ten(tmp_path, depth, ok):
    if ok:
        assert D.Source(str(tmp_path), max_depth=depth).max_depth == depth
    else:
        with pytest.raises(ValueError, match="between 0 and 10"):
            D.Source(str(tmp_path), max_depth=depth)


def test_identifiers_up_to_128_characters():
    assert D.check_identifier("table", "t" * 128) == "t" * 128
    with pytest.raises(ValueError, match="1-128 characters"):
        D.check_identifier("table", "t" * 129)


def test_an_excluded_file_pattern_hides_the_file(tmp_path):
    touch(tmp_path / "keep.csv")
    touch(tmp_path / "old.bak.csv")
    found, _ = tables(tmp_path, exclude=["*.BAK.csv"])
    assert list(found) == ["dbo.keep"]


def test_a_dotted_name_sets_the_schema_only_at_the_top_of_a_flat_source(tmp_path):
    touch(tmp_path / "sales.orders.csv")
    touch(tmp_path / "sub" / "hr.people.csv")
    touch(tmp_path / "trailing..csv")  # "trailing." has no table after its dot
    found, issues = tables(tmp_path)
    assert sorted(found) == ["dbo.sub_hr.people", "dbo.trailing.", "sales.orders"]
    assert issues == []


def test_a_single_file_table_counts_one_file_and_no_partitions(tmp_path):
    f = touch(tmp_path / "orders.csv")
    t = tables(tmp_path)[0]["dbo.orders"]
    assert (t.files, t.hive, t.folder, t.size) == (1, False, None, f.stat().st_size)


def test_a_delta_folder_has_no_partitions_flag_and_a_hive_folder_has_one(tmp_path):
    delta(tmp_path / "d")
    touch(tmp_path / "h" / "year=2026" / "part-0.parquet")
    found, _ = tables(tmp_path)
    assert (found["dbo.d"].format, found["dbo.d"].hive) == ("delta", False)
    assert (found["dbo.h"].format, found["dbo.h"].hive) == ("parquet", True)


def test_writer_files_starting_with_an_underscore_dont_stop_a_part_folder(tmp_path):
    touch(tmp_path / "t" / "part-0.parquet")
    touch(tmp_path / "t" / "_tmp.parquet")
    found, _ = tables(tmp_path)
    assert found["dbo.t"].format == "parquet" and found["dbo.t"].folder == str(tmp_path / "t")


@links
def test_links_inside_a_part_folder_are_ignored(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    touch(outside / "x.csv")
    touch(tmp_path / "t" / "part-0.parquet")
    os.symlink(outside, tmp_path / "t" / "linked_dir", target_is_directory=True)
    os.symlink(outside / "x.csv", tmp_path / "t" / "part-1.csv")
    found, _ = tables(tmp_path)
    assert found["dbo.t"].format == "parquet" and found["dbo.t"].folder == str(tmp_path / "t")


@links
def test_a_linked_folder_below_the_source_is_not_walked(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-elsewhere")
    touch(outside / "inner" / "secret.csv")
    os.symlink(outside, tmp_path / "linked", target_is_directory=True)
    found, issues = tables(tmp_path)
    assert found == {} and issues == []


def test_a_single_file_with_an_unusable_table_name_is_pointed_at(tmp_path):
    bad = touch(tmp_path / ("t" * 129 + ".csv"))
    found, issues = tables(tmp_path)
    assert found == {} and [i.path for i in issues] == [str(bad)]


def test_a_file_that_cant_be_read_says_why_in_words(tmp_path, monkeypatch):
    touch(tmp_path / "orders.csv")
    real = os.scandir

    class Entry:
        def __init__(self, e):
            self._e, self.name, self.path = e, e.name, e.path

        def is_dir(self, follow_symlinks=True):
            return self._e.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, follow_symlinks=True):
            return self._e.is_file(follow_symlinks=follow_symlinks)

        def stat(self, follow_symlinks=True):
            raise PermissionError(13, "Permission denied")

    class Listing:
        def __init__(self, path):
            self._it = real(path)

        def __iter__(self):
            return (Entry(e) for e in self._it)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._it.close()

    monkeypatch.setattr(D.os, "scandir", Listing)
    found, issues = tables(tmp_path)
    assert found == {}
    assert [(i.path, i.problem) for i in issues] == [(str(tmp_path / "orders.csv"), "Can't read the file: Permission denied")]


def test_discovery_stops_after_20000_data_files(tmp_path):
    for i in range(20001):
        (tmp_path / f"f{i}.csv").touch()
    found = D.discover(D.Source(str(tmp_path), naming="flat"))
    assert len(found.tables) == 20000
    assert any("More than 20000 data files" in i.problem for i in found.issues)
