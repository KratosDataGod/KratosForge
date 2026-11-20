"""Notebook files at their exact edges (boundary re-score of batch 9, mutation batch 158,
docs/quality/mutation-testing.md): a file of exactly the size limit, and Recently deleted at exactly
KEEP_DAYS."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fabric_desktop import notebook_files
from fabric_desktop.notebook import NotebookShelf

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def shelf(tmp_path):
    return NotebookShelf(tmp_path / "app" / "notebooks")


def _clock(monkeypatch, when):
    monkeypatch.setattr(notebook_files, "_now", lambda: when)


def _new(shelf, name):
    return notebook_files.create(shelf, name, None, [{"type": "code", "source": "x = 1"}], None, None, None)


def test_a_file_of_exactly_the_size_limit_is_opened(monkeypatch):
    text = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []})
    monkeypatch.setattr(notebook_files, "MAX_FILE_BYTES", len(text.encode("utf-8")))
    assert notebook_files._parse(text)["cells"] == []
    monkeypatch.setattr(notebook_files, "MAX_FILE_BYTES", len(text.encode("utf-8")) - 1)
    with pytest.raises(ValueError, match="isn't opened"):
        notebook_files._parse(text)


def test_a_notebook_deleted_exactly_keep_days_ago_isnt_purged_yet(shelf, monkeypatch):
    _new(shelf, "Old")
    _clock(monkeypatch, T0)
    notebook_files.delete(shelf, "Old", [])
    _new(shelf, "Now")
    _clock(monkeypatch, T0 + dt.timedelta(days=notebook_files.KEEP_DAYS))
    notebook_files.delete(shelf, "Now", [])
    trash = shelf.folder.parent / "notebooks-deleted"
    assert len(list(trash.glob("*.ipynb"))) == 2


def test_a_notebook_is_listed_until_the_end_of_its_kept_until(shelf, monkeypatch):
    _new(shelf, "Old")
    _clock(monkeypatch, T0)
    notebook_files.delete(shelf, "Old", [])
    _clock(monkeypatch, T0 + dt.timedelta(days=notebook_files.KEEP_DAYS))
    assert [r["name"] for r in notebook_files.deleted(shelf)] == ["Old"]
    _clock(monkeypatch, T0 + dt.timedelta(days=notebook_files.KEEP_DAYS, microseconds=1))
    assert notebook_files.deleted(shelf) == []
