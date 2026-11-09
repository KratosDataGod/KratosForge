"""A metadata project's source systems and key notebooks (meta_service.py), the paths the main tests
don't reach: a code already taken, a save the gate refuses (the file on disk stays as it was), a
password typed into a connection (never written), and a key notebook for an entity with no keys."""

from __future__ import annotations

import pytest

from fabric_desktop.meta import keys as K
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")  # source systems CRM (1) and SHOP (2)
    return s


def _file(svc):
    return K.keys_dir(svc._project().root) / K.SOURCE_SYSTEMS_FILE


def test_a_code_another_source_system_has_is_refused(svc):
    before = _file(svc).read_text()
    with pytest.raises(ValueError, match="code crm is already used by another source system"):
        svc.meta_save_source_system({"id": 9, "code": "crm", "name": "Copy"})
    assert _file(svc).read_text() == before


def test_a_save_the_gate_refuses_leaves_the_file_as_it_was(svc):
    before = _file(svc).read_text()
    with pytest.raises(GateError, match="code must be letters, digits or _ and start with a letter"):
        svc.meta_save_source_system({"id": 9, "code": "9bad", "name": "Bad"})
    assert _file(svc).read_text() == before
    assert [s.code for s in svc._project().source_systems] == ["CRM", "SHOP"]


def test_a_password_typed_into_a_connection_is_never_written(svc, tmp_path):
    erp = {"id": 9, "code": "ERP", "name": "Finance ERP",
           "connections": {"dev": {"server_host": "sql-dev.local", "password": "hunter2-SECRET"}}}
    saved = svc.meta_save_source_system(erp)["source_systems"]
    assert [s["code"] for s in saved] == ["CRM", "SHOP", "ERP"]
    assert saved[2]["connections"]["dev"]["server_host"] == "sql-dev.local"
    assert "hunter2-SECRET" not in repr(saved)
    written = [f for f in tmp_path.rglob("*") if f.is_file() and b"hunter2-SECRET" in f.read_bytes()]
    assert written == []  # not in the source systems file, nor any other file the save touched


def test_a_key_notebook_needs_an_entity_with_keys(svc):
    with pytest.raises(ValueError, match="bronze.customers has no \\[\\[keys\\]\\] block"):
        svc.meta_key_notebook("bronze.customers")
