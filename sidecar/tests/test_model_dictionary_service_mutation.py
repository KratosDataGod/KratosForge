"""Mutation batch 69: model_dictionary_service.py, how a save that fails part way names what wasn't saved
(docs/quality/mutation-testing.md). A metadata project in a temporary folder; saving is stood in for."""

from __future__ import annotations

import pytest

from fabric_desktop import model_dictionary as md
from fabric_desktop.service import Service
from tests.test_model_dictionary import _edit, svc  # noqa: F401 - the fixture is used by name


def _refuse(monkeypatch):
    def save(self, entity, fields, column):
        raise PermissionError("403 Forbidden")
    monkeypatch.setattr(Service, "mesh_save_about", save)


def test_a_column_that_isnt_saved_is_named_with_its_table(svc, monkeypatch):  # noqa: F811
    text = _edit(md.to_csv(svc.meta_model_dictionary()["items"]), "dim_customer", "email", "Contact email.")
    _refuse(monkeypatch)
    with pytest.raises(RuntimeError, match=r"^Saved 0 of 1; gold\.dim_customer\.email wasn't saved "):
        svc.meta_model_dictionary_import(text, confirmed=True)


def test_a_table_that_isnt_saved_is_named_on_its_own(svc, monkeypatch):  # noqa: F811
    text = _edit(md.to_csv(svc.meta_model_dictionary()["items"]), "dim_customer", "(table)", "Customers.")
    _refuse(monkeypatch)
    with pytest.raises(RuntimeError, match=r"^Saved 0 of 1; gold\.dim_customer wasn't saved "):
        svc.meta_model_dictionary_import(text, confirmed=True)
