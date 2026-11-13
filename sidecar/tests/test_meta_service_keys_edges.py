"""Metadata project service edges around new entities, key tables and the platform's source systems
(meta_service.py) that the main tests don't reach: no sample files yet, no signed-in owner, a platform
that can't be read, and gates that refuse a sync or a key notebook. A local project; nothing in Fabric."""

from __future__ import annotations

import types

import pytest

from fabric_desktop.meta import keys as K
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")  # source systems CRM (1) and SHOP (2)
    return s


def _signed_out():
    raise RuntimeError("no account")


def test_source_tables_outside_the_environment_read_with_the_storage_sign_in(svc):
    svc._auth = types.SimpleNamespace(storage_token=lambda: "st-token")
    options = svc._source_options()
    assert options["bearer_token"] == "st-token" and options["use_fabric_endpoint"] == "true"


def test_a_files_entity_with_no_files_yet_starts_with_a_placeholder_and_no_owner(svc, monkeypatch):
    def no_files(*a, **k):
        raise FileNotFoundError("no files under landing/x yet")
    store = svc._store(svc._project(), "dev")
    monkeypatch.setattr(type(store), "read_files", no_files)
    svc._auth = types.SimpleNamespace(signed_in_user=_signed_out)
    out = svc.meta_new_entity("files", "bronze.newfeed", source="landing/newfeed/*.csv")
    assert out["name"] == "bronze.newfeed"
    text = (svc._project().entities_dir / "bronze.newfeed.toml").read_text(encoding="utf-8")
    assert "landing/newfeed/*.csv" in text


def test_a_custom_entity_writes_its_code_file_once(svc):
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "ana@contoso.com")
    svc.meta_new_entity("custom", "silver.scored")
    project = svc._project()
    made = [p for p in project.root.rglob("*.py") if "scored" in p.name]
    assert made, "the custom step's Python file is written"
    made[0].write_text("# edited by hand\n", encoding="utf-8")
    svc.meta_delete_entity("silver.scored")
    svc.meta_new_entity("custom", "silver.scored")
    assert made[0].read_text(encoding="utf-8") == "# edited by hand\n"  # an existing file is never replaced


def test_the_project_sources_still_show_when_the_platform_cant_be_read(svc):
    def unreachable():
        raise ConnectionError("platform offline")
    svc._read_platform_sources = unreachable
    out = svc.meta_keys(platform=True)
    assert out["platform_sources"] is None and "platform offline" in out["platform_problem"]
    assert [s["code"] for s in out["source_systems"]] == ["CRM", "SHOP"]


def _platform(*sources):
    return lambda: [{"name": s["code"].lower(), "source_id": s["id"], "code": s["code"], "title": s.get("title", s["code"]),
                     "system_type": "sql", "owner": "", "description": "", "active": True} for s in sources]


def test_reading_a_platform_source_the_project_already_reads_is_refused(svc):
    svc._read_platform_sources = _platform({"code": "CRM", "id": 1})
    with pytest.raises(ValueError, match="This project already reads CRM"):
        svc.meta_source_read("crm")
    with pytest.raises(ValueError, match="isn't one of the platform's source systems"):
        svc.meta_source_read("erp")


def test_a_platform_source_whose_id_the_project_uses_for_another_is_refused(svc):
    svc._read_platform_sources = _platform({"code": "ERP", "id": 2})
    with pytest.raises(ValueError, match="id 2 is SHOP in this project. Move sources to the platform first"):
        svc.meta_source_read(" ERP ")


def test_a_source_system_saves_with_its_own_fields_when_the_platform_is_unreachable(svc):
    def unreachable():
        raise ConnectionError("platform offline")
    svc._read_platform_sources = unreachable
    saved = svc.meta_save_source_system({"id": 9, "code": "ERP", "name": "Finance ERP"})["source_systems"]
    assert saved[-1]["code"] == "ERP" and saved[-1]["name"] == "Finance ERP"


def test_a_new_key_table_with_no_signed_in_user_has_no_owner(svc, monkeypatch):
    from fabric_desktop import meta_service
    logged = []
    monkeypatch.setattr(meta_service._log, "info", lambda *a, **k: logged.append(a[0]))
    svc._auth = types.SimpleNamespace(signed_in_user=_signed_out)
    out = svc.meta_new_key_table(" Vendor ", "Suppliers")
    assert "vendor" in [t["name"] for t in out["key_tables"]]
    assert logged and "owner is left blank" in logged[0]
    with pytest.raises(ValueError, match="key table vendor already exists"):
        svc.meta_new_key_table("vendor")


def test_a_key_sync_with_a_broken_source_system_is_refused(svc, monkeypatch):
    from fabric_desktop import meta_service
    monkeypatch.setattr(meta_service, "validate", lambda project, today: ["source system 9: no code", "silver.x: fine"])
    with pytest.raises(GateError) as raised:
        svc.meta_keys_sync()
    assert raised.value.errors == ["source system 9: no code"]


def test_a_key_notebook_for_an_entity_that_doesnt_validate_is_refused(svc, monkeypatch):
    from fabric_desktop import meta_service
    name = next(e.name for e in svc._project().entities.values() if K.entity_keys(e))
    monkeypatch.setattr(meta_service, "validate", lambda project, today: [f"{name}: the key column is missing"])
    with pytest.raises(GateError) as raised:
        svc.meta_key_notebook(name)
    assert raised.value.errors == [f"{name}: the key column is missing"]
