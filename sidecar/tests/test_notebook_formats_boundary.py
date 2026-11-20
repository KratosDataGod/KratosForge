"""Notebook imports at their exact limits (boundary re-score of batch 9, mutation batch 159,
docs/quality/mutation-testing.md): a file, an archive, its entries and its notebooks each of exactly
the size or count allowed is read, and one over is refused."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from fabric_desktop import notebook_formats as nf

DOC = json.dumps({"name": "One", "language": "python", "commands": [{"command": "x = 1", "position": 1}]}).encode()


def _dbc(*docs: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, doc in enumerate(docs):
            zf.writestr(f"n{i}.python", doc)
    return buf.getvalue()


def test_a_source_file_of_exactly_max_text_is_read(monkeypatch):
    monkeypatch.setattr(nf, "MAX_TEXT", len("x = 1"))
    assert nf.parse_source("x = 1")["cells"]
    monkeypatch.setattr(nf, "MAX_TEXT", len("x = 1") - 1)
    with pytest.raises(ValueError, match="32 MB"):
        nf.parse_source("x = 1")


def test_an_ipynb_of_exactly_max_text_is_read(monkeypatch):
    text = json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5})
    monkeypatch.setattr(nf, "MAX_TEXT", len(text))
    assert nf.parse_ipynb(text)["cells"] == []
    monkeypatch.setattr(nf, "MAX_TEXT", len(text) - 1)
    with pytest.raises(ValueError, match="32 MB"):
        nf.parse_ipynb(text)


def test_an_archive_of_exactly_max_dbc_bytes_is_read(monkeypatch):
    data = _dbc(DOC)
    monkeypatch.setattr(nf, "MAX_DBC", len(data))
    assert [n["name"] for n in nf.parse_dbc(data)] == ["One"]


def test_an_archive_of_exactly_max_dbc_entries_files_is_read(monkeypatch):
    monkeypatch.setattr(nf, "MAX_DBC_ENTRIES", 2)
    assert len(nf.parse_dbc(_dbc(DOC, DOC))) == 2
    monkeypatch.setattr(nf, "MAX_DBC_ENTRIES", 1)
    with pytest.raises(ValueError, match="at most 1"):
        nf.parse_dbc(_dbc(DOC, DOC))


def test_an_entry_of_exactly_max_dbc_entry_bytes_is_read(monkeypatch):
    monkeypatch.setattr(nf, "MAX_DBC_ENTRY", len(DOC))
    assert len(nf.parse_dbc(_dbc(DOC))) == 1


def test_entries_adding_up_to_exactly_max_dbc_total_are_read(monkeypatch):
    monkeypatch.setattr(nf, "MAX_DBC_TOTAL", 2 * len(DOC))
    assert len(nf.parse_dbc(_dbc(DOC, DOC))) == 2
    monkeypatch.setattr(nf, "MAX_DBC_TOTAL", 2 * len(DOC) - 1)
    with pytest.raises(ValueError, match="128 MB"):
        nf.parse_dbc(_dbc(DOC, DOC))


def test_exactly_max_notebooks_notebooks_are_imported(monkeypatch):
    monkeypatch.setattr(nf, "MAX_NOTEBOOKS", 2)
    assert len(nf.parse_dbc(_dbc(DOC, DOC))) == 2
    monkeypatch.setattr(nf, "MAX_NOTEBOOKS", 1)
    with pytest.raises(ValueError, match="at most 1"):
        nf.parse_dbc(_dbc(DOC, DOC))
