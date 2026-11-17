"""Mutation testing batch 9 (docs/quality/mutation-testing.md): notebook files.

Each test pins a behavior a surviving mutant changed without any other test noticing: search's
limits and the snippet around a match, an empty search, a free name past "<name> 2", the size limit
on a file to open, the summary of an opened notebook, keeping what was published, comparing Fabric's
cells by id, and Recently deleted (what is purged, the order, a resources folder, a restore into a
folder that was removed, and a restore that finds the name taken after all)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fabric_desktop import notebook_files
from fabric_desktop.notebook import NotebookShelf

CELL = [{"type": "code", "source": "x = 1"}]


@pytest.fixture
def shelf(tmp_path):
    return NotebookShelf(tmp_path / "app" / "notebooks")


def _new(shelf, name, cells=CELL):
    return notebook_files.create(shelf, name, None, cells, None, None, None)


def _ipynb(cells, kernel=None):
    meta = {"kernelspec": {"name": kernel}} if kernel else {}
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": meta,
            "cells": [{"cell_type": t, "metadata": {}, "source": s} for t, s in cells]}


# Search


def test_an_empty_search_searches_nothing(shelf):
    _new(shelf, "Sales")
    assert notebook_files.search(shelf, "   ") == {"query": "", "notebooks": [], "searched": 0, "problems": []}


def test_a_notebook_gives_at_most_20_hits(shelf):
    _new(shelf, "Many", [{"type": "code", "source": "\n".join(f"needle_{i} = {i}" for i in range(25))}])
    (row,) = notebook_files.search(shelf, "needle")["notebooks"]
    assert [h["line"] for h in row["hits"]] == list(range(1, 21))
    assert row["more"] is True


def test_nineteen_hits_arent_more(shelf):
    _new(shelf, "Some", [{"type": "code", "source": "\n".join(f"needle_{i} = {i}" for i in range(19))}])
    (row,) = notebook_files.search(shelf, "needle")["notebooks"]
    assert len(row["hits"]) == 19 and row["more"] is False


def test_a_search_finds_at_most_100_notebooks(shelf):
    for i in range(101):
        _new(shelf, f"report {i:03d}")
    got = notebook_files.search(shelf, "report", names_only=True)
    assert len(got["notebooks"]) == 100 and got["more"] is True


def test_a_line_up_to_160_characters_is_kept_whole():
    line = "x" * 150 + "needle" + "y" * 4
    assert notebook_files._snippet(line, 150, 156) == (line, 150, 156)


def test_a_long_line_is_cut_to_160_characters_around_the_match():
    line = "a" * 100 + "needle" + "b" * 200
    text, s, e = notebook_files._snippet(line, 100, 106)
    assert text == "…" + line[60:220] + "…"
    assert text[s:e] == "needle" and (s, e) == (41, 47)


def test_a_match_near_the_start_of_a_long_line_has_no_leading_cut():
    line = "a" * 10 + "needle" + "b" * 300
    text, s, e = notebook_files._snippet(line, 10, 16)
    assert text == line[:160] + "…"
    assert (s, e) == (10, 16)


def test_a_match_near_the_end_of_a_long_line_has_no_trailing_cut():
    line = "a" * 290 + "needle"
    text, s, e = notebook_files._snippet(line, 290, 296)
    assert text == "…" + line[136:]
    assert text[s:e] == "needle"


# New names


def test_a_free_name_skips_every_number_already_taken(shelf):
    _new(shelf, "Load")
    _new(shelf, "Load 2")
    assert notebook_files.free_name(shelf, "Load") == "Load 3"


def test_a_free_name_is_cut_to_80_characters(shelf):
    assert notebook_files.free_name(shelf, "n" * 100) == "n" * 80


# Opening a file


def test_a_file_over_20_mb_isnt_opened(shelf):
    content = '{"cells": [], "pad": "' + "a" * (20 * 1024 * 1024) + '"}'
    with pytest.raises(ValueError, match=r"^The file is over 20 MB, so it isn't opened\.$"):
        notebook_files.import_check(shelf, "big.ipynb", content)


def test_an_opened_notebook_counts_code_and_markdown_and_names_its_spark_cells():
    nb = _ipynb([("markdown", "# Title"), ("code", "df = spark.read.table('t')"), ("code", "spark.sql('select 1')"),
                 ("code", "x = 1")], kernel="python3")
    got = notebook_files.read(nb)["summary"]
    assert (got["cells"], got["code"], got["markdown"], got["language"]) == (4, 3, 1, "python3")
    assert got["notes"] == [("Cells 2, 3 use Spark. On this PC spark runs on DuckDB's Spark-compatible API, "
                             "so check them with Validate.")]


def test_one_spark_cell_is_named_in_the_singular():
    got = notebook_files.read(_ipynb([("markdown", "spark.read.table('t')"), ("code", "spark.sql('x')")]))["summary"]
    assert got["notes"] == [("Cell 2 uses Spark. On this PC spark runs on DuckDB's Spark-compatible API, "
                             "so check them with Validate.")]
    assert got["language"] == "python"


# What was published


def test_what_was_published_is_kept_in_a_folder_made_for_it_and_can_be_kept_again(tmp_path):
    folder = tmp_path / "data" / "published"
    nb = _ipynb([("code", "x = 1")])
    notebook_files.remember_published(folder, "item-1", "ws1", nb, name="Sales")
    notebook_files.remember_published(folder, "item-1", "ws1", nb, name="Sales")
    assert notebook_files.published(folder, "item-1")["name"] == "Sales"


def test_cells_with_ids_that_fabric_reordered_are_a_reorder_not_two_changes():
    base = {"hash": "old", "at": "t", "cells": [{"id": "a", "cell_type": "code", "source": "1"},
                                                {"id": "b", "cell_type": "code", "source": "2"}]}
    current = {"cells": [{"id": "b", "cell_type": "code", "source": "2"}, {"id": "a", "cell_type": "code", "source": "1"}]}
    got = notebook_files.fabric_changes(base, current)
    assert [c["kind"] for c in got["fabric"]] == ["reordered"]


# Recently deleted


def _clock(monkeypatch, when):
    monkeypatch.setattr(notebook_files, "_now", lambda: when)


def test_a_delete_purges_only_what_is_older_than_30_days(shelf, monkeypatch):
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    for name, days in (("Old", 0), ("Recent", 20)):
        _new(shelf, name)
        _clock(monkeypatch, t0 + dt.timedelta(days=days))
        notebook_files.delete(shelf, name, [])
    _new(shelf, "Now")
    _clock(monkeypatch, t0 + dt.timedelta(days=31))
    notebook_files.delete(shelf, "Now", [])
    assert [r["name"] for r in notebook_files.deleted(shelf)] == ["Now", "Recent"]


def test_an_expired_entry_whose_file_is_gone_is_still_purged(shelf, monkeypatch):
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    _new(shelf, "Old")
    _clock(monkeypatch, t0)
    tid = notebook_files.delete(shelf, "Old", [])["id"]
    trash = shelf.folder.parent / "notebooks-deleted"
    (trash / f"{tid}.ipynb").unlink()
    _new(shelf, "Now")
    _clock(monkeypatch, t0 + dt.timedelta(days=31))
    notebook_files.delete(shelf, "Now", [])
    assert not (trash / f"{tid}.json").exists()


def test_recently_deleted_lists_the_newest_first(shelf, monkeypatch):
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    for i, name in enumerate(("First", "Second", "Third")):
        _new(shelf, name)
        _clock(monkeypatch, t0 + dt.timedelta(hours=i))
        notebook_files.delete(shelf, name, [])
    assert [r["name"] for r in notebook_files.deleted(shelf)] == ["Third", "Second", "First"]


def test_a_delete_says_whether_a_resources_folder_was_kept(shelf):
    _new(shelf, "Plain")
    assert notebook_files.delete(shelf, "Plain", [])["resources_kept"] is None
    _new(shelf, "WithFiles")
    res = notebook_files.notebook_resources.folder_for(shelf.path("WithFiles"))
    res.mkdir(parents=True)
    assert notebook_files.delete(shelf, "WithFiles", [])["resources_kept"] == str(res)


def test_a_restore_brings_back_folders_that_were_removed(shelf):
    _new(shelf, "Team/Sales/Daily load")
    tid = notebook_files.delete(shelf, "Team/Sales/Daily load", [])["id"]
    (shelf.folder / "Team" / "Sales").rmdir()
    (shelf.folder / "Team").rmdir()
    got = notebook_files.restore(shelf, tid)
    assert got["name"] == "Team/Sales/Daily load"
    assert (shelf.folder / "Team" / "Sales" / "Daily load.ipynb").is_file()


def test_a_restore_that_finds_the_file_there_after_all_keeps_the_deleted_copy(shelf, monkeypatch):
    _new(shelf, "Twice")
    tid = notebook_files.delete(shelf, "Twice", [])["id"]
    _new(shelf, "Twice")
    before = shelf.path("Twice").read_bytes()
    monkeypatch.setattr(shelf, "_taken", lambda name: False)  # made between the check and the write
    with pytest.raises(ValueError, match="already exists"):
        notebook_files.restore(shelf, tid)
    assert shelf.path("Twice").read_bytes() == before
    trash = shelf.folder.parent / "notebooks-deleted"
    assert (trash / f"{tid}.ipynb").is_file() and json.loads((trash / f"{tid}.json").read_text())["name"] == "Twice"
