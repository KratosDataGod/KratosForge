"""Data dictionary: what happens when the catalog can't be read or a save fails part way. A metadata
project in a temporary folder; the catalog is stood in for."""

import pytest

from fabric_desktop import model_dictionary as md
from fabric_desktop import model_dictionary_service as mds
from fabric_desktop.service import Service
from tests.test_model_dictionary import _edit, svc  # noqa: F401 - the fixture is used by name


class DownCatalog:
    def call(self, op, **kwargs):
        raise ConnectionError("The platform can't be reached")


def test_a_catalog_that_cant_be_read_still_makes_a_dictionary_and_says_why(svc, monkeypatch):  # noqa: F811
    logged = []
    monkeypatch.setattr(mds._log, "info", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(Service, "_catalog", lambda self: DownCatalog())
    out = svc.meta_model_dictionary()
    assert out["items"] and "can't be reached" in out["problem"]
    assert logged and "Catalog not read" in logged[0][0]


def test_an_import_saves_nothing_while_the_catalog_cant_be_reached(svc, monkeypatch):  # noqa: F811
    text = _edit(md.to_csv(svc.meta_model_dictionary()["items"]), "dim_customer", "email", "Contact email.")
    monkeypatch.setattr(Service, "_catalog", lambda self: DownCatalog())
    with pytest.raises(RuntimeError, match="nothing was saved"):
        svc.meta_model_dictionary_import(text, confirmed=True)


def test_a_save_that_fails_part_way_says_how_many_were_saved(svc, monkeypatch):  # noqa: F811
    text = md.to_csv(svc.meta_model_dictionary()["items"])
    text = _edit(text, "dim_customer", "email", "Contact email.")
    text = _edit(text, "dim_customer", "name", "Full name.")
    calls = []

    def save(self, entity, fields, column):
        calls.append(column)
        if len(calls) == 2:
            raise PermissionError("403 Forbidden")

    monkeypatch.setattr(Service, "mesh_save_about", save)
    with pytest.raises(RuntimeError, match=r"Saved 1 of 2; .*wasn't saved \(PermissionError: 403 Forbidden\)") as raised:
        svc.meta_model_dictionary_import(text, confirmed=True)
    assert isinstance(raised.value.__cause__, PermissionError)
