"""Mutation batch 305: merging a notebook changed in KratosForge and in another program (notebook_sync). Each test
pins one behaviour the first mutation run showed no test held. They call merge_cells, _copy_id and SyncBases
directly, with small notebooks."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from fabric_desktop import notebook_sync as ns


def cell(cid, source="x", **extra):
    return {"cell_id": cid, "type": "code", "source": source, **extra}


def ids(result):
    return [c["cell_id"] for c in result["cells"]]


# ---- what counts as a change

def test_a_change_to_a_cells_settings_alone_is_a_change():
    base = [cell("a")]
    mine = [cell("a", meta={"title": "Load"})]
    out = ns.merge_cells(base, mine, base)
    assert out["resolved"] and out["cells"] == mine


def test_a_change_to_a_cells_title_is_a_change_from_the_title_before():
    base = [cell("a", meta={"title": "Old"})]
    mine = [cell("a", meta={"title": "New"})]
    out = ns.merge_cells(base, mine, base)
    assert out["resolved"] and out["cells"] == mine


def test_a_cell_id_that_isnt_text_is_refused():
    with pytest.raises(ValueError, match="needs its cell id"):
        ns.merge_cells([], [{"cell_id": 5, "source": "x"}], [])


# ---- where a cell only one side has goes

def test_a_cell_the_other_side_added_at_the_top_stays_at_the_top():
    base = [cell("a"), cell("b")]
    theirs = [cell("new"), cell("a"), cell("b")]
    assert ids(ns.merge_cells(base, base, theirs)) == ["new", "a", "b"]


def test_a_cell_the_other_side_added_after_its_first_cell_goes_right_after_it():
    base = [cell("a"), cell("b")]
    theirs = [cell("a"), cell("new"), cell("b")]
    assert ids(ns.merge_cells(base, base, theirs)) == ["a", "new", "b"]


def test_cells_moved_the_same_way_on_both_sides_keep_this_apps_order_for_what_each_added():
    base = [cell("a"), cell("b")]
    mine = [cell("b"), cell("x"), cell("a")]
    theirs = [cell("b"), cell("y"), cell("a")]
    out = ns.merge_cells(base, mine, theirs)
    assert out["resolved"]
    assert ids(out) == ["b", "y", "x", "a"]  # mine leads; theirs' new cell goes right after the cell it follows


# ---- the id of the second copy of a cell kept both ways

def test_the_copy_of_a_cell_is_named_after_it():
    assert ns._copy_id("load", set()) == "load-theirs"
    assert ns._copy_id("load", {"load-theirs"}) == "load-theirs2"
    assert ns._copy_id("load", {"load-theirs", "load-theirs2"}) == "load-theirs3"


def test_the_copy_name_keeps_only_safe_characters_and_at_most_40_of_them():
    assert ns._copy_id("a b/c", set()) == "abc-theirs"
    assert ns._copy_id("x" * 45, set()) == "x" * 40 + "-theirs"
    assert ns._copy_id("/// ", set()) == "cell-theirs"


def test_when_every_numbered_copy_name_is_taken_a_random_id_is_used():
    taken = {"a-theirs"} | {f"a-theirs{n}" for n in range(2, 1000)}
    new = ns._copy_id("a", taken)
    assert re.fullmatch(r"[0-9a-f]{16}", new)
    assert ns._copy_id("a", taken - {"a-theirs999"}) == "a-theirs999"


def test_a_cell_kept_both_ways_gets_a_copy_after_it_titled_where_it_came_from():
    base = [cell("a")]
    out = ns.merge_cells(base, [cell("a", "mine")], [cell("a", "theirs")], {"a": "both"})
    assert ids(out) == ["a", "a-theirs"]
    assert out["cells"][1]["meta"] == {"title": ns.THEIRS_TITLE}


# ---- the bases kept on disk

def test_a_notebooks_bases_are_kept_in_a_folder_named_by_its_hash(tmp_path):
    bases = ns.SyncBases(tmp_path)
    bases.remember("Sales", "stamp0001", [cell("a")])
    folder = tmp_path / hashlib.sha256(b"Sales").hexdigest()[:24]
    assert (folder / "stamp0001.json").is_file()


def test_a_base_kept_for_another_notebook_or_without_cells_is_no_base(tmp_path):
    bases = ns.SyncBases(tmp_path)
    bases.remember("Sales", "stamp0001", [cell("a")])
    path = bases._folder("Sales") / "stamp0001.json"
    path.write_text(json.dumps({"name": "Other", "cells": [cell("a")]}), encoding="utf-8")
    assert bases.find("Sales", "stamp0001") is None
    path.write_text(json.dumps({"name": "Sales", "cells": {"a": 1}}), encoding="utf-8")
    assert bases.find("Sales", "stamp0001") is None
    path.write_text(json.dumps(["Sales"]), encoding="utf-8")
    assert bases.find("Sales", "stamp0001") is None
    path.write_text(json.dumps({"name": "Sales", "cells": [cell("a")]}), encoding="utf-8")
    assert bases.find("Sales", "stamp0001") == [cell("a")]
