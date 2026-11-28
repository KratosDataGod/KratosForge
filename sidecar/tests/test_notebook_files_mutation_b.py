"""Mutation batch 513, a re-score of notebook_files.py (batches 9 and 158): a cell of another type is
numbered among the cells that are kept, and each Fabric item keeps its own published copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import notebook_files as nf
from fabric_desktop.notebook_files import SHIM_MARKER


def _cell(kind: str, source: str) -> dict:
    cell = {"cell_type": kind, "source": source, "metadata": {}}
    if kind == "code":
        cell.update(outputs=[], execution_count=None)
    return cell


def test_a_raw_cell_is_numbered_among_the_kept_cells_after_the_setup_cell_is_left_out():
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {},
          "cells": [_cell("code", SHIM_MARKER + "\nx = 1"), _cell("raw", "raw text"), _cell("markdown", "hi")]}
    notes = nf.read(nb)["summary"]["notes"]
    assert notes[0] == "Cell 1. The cell's type (raw) isn't code or markdown, so it comes in as a code cell."


def test_a_raw_cell_after_two_kept_cells_is_cell_3():
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {},
          "cells": [_cell("markdown", "a"), _cell("code", "x = 1"), _cell("raw", "raw text")]}
    assert nf.read(nb)["summary"]["notes"][0].startswith("Cell 3. ")


def test_each_fabric_item_keeps_its_own_published_copy(tmp_path):
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [_cell("code", "x = 1")]}
    nf.remember_published(tmp_path, "item-a", "ws-1", nb, name="A")
    nf.remember_published(tmp_path, "item-b", "ws-1", nb, name="B")
    assert nf.published(tmp_path, "item-a")["name"] == "A"
    assert nf.published(tmp_path, "item-b")["name"] == "B"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["item-a.json", "item-b.json"]


def test_an_item_id_that_isnt_one_is_refused():
    with pytest.raises(ValueError, match="isn't a Fabric item id"):
        nf.published(Path("."), "../x")
