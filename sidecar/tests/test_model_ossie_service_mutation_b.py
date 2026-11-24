"""Mutation tests for model_ossie_service.py (batch 452): the size and depth caps at their exact edges, the
saved view folder's entry caps, the file summary's counts, and the import and Save TMDL refusals.
Fabric is the fake in test_model_ossie; nothing reaches a tenant."""

from __future__ import annotations

import base64
import json
import types
from pathlib import Path

import pytest
import yaml

from fabric_desktop import model_ossie, ossie_profiles, tmdl
from fabric_desktop import model_ossie_service as mos
from tests.test_model_ossie import PARTITION_CAPTION, _bim, _orders_folder, _ossie, svc  # noqa: F401  (svc is a fixture)


def _root(svc) -> Path:  # noqa: F811
    return Path(svc._project().root)


def _parts(**files):
    return {"definition": {"parts": [{"path": p, "payload": base64.b64encode(t.encode()).decode()}
                                     for p, t in files.items()]}}


# ---- small helpers


def test_model_counts_count_relationships_and_roles():
    counts = mos._model_counts(_bim())
    assert counts == {"tables": 2, "columns": 5, "measures": 2, "relationships": 1, "roles": 1, "hierarchies": 1}


def test_exactly_three_unreadable_lines_are_named_without_and_more():
    rows = [{"file": "definition/model.tmdl", "kind": "lost", "object": o} for o in ("a", "b", "c")]
    with pytest.raises(ValueError, match=r"model\.tmdl holds lines KratosForge can't read \(a, b, c\), so"):
        mos._refuse_unreadable(rows)


def test_a_file_summary_of_a_file_that_isnt_one_object_is_unreadable():
    assert mos._file_summary("v.yaml", "- a\n") == {"path": "v.yaml", "bytes": 4, "unreadable": True}


def test_a_file_summary_counts_only_metrics_with_dax_text():
    doc = {"metrics": [
        {"name": "dax", "expression": {"dialects": ["text", {"dialect": "DAX", "expression": "SUM(t[a])"}]}},
        {"name": "sql", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(a)"}]}},
        {"name": "sql 2", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "MAX(a)"}]}},
        {"name": "empty", "expression": {"dialects": [{"dialect": "DAX", "expression": ""}]}},
        {"name": "not a list", "expression": {"dialects": {"dialect": "DAX", "expression": "x"}}}]}
    row = mos._file_summary("v.yaml", yaml.safe_dump(doc))
    assert row["metrics"] == 5 and row["dax_metrics"] == 1


def _nested(top: Path, depth: int, name: str) -> Path:
    here = top
    for i in range(depth):
        here = here / f"d{i}"
    here.mkdir(parents=True)
    (here / name).write_text("x", encoding="utf-8")
    return here


def test_tmdl_files_go_down_eight_folders_and_no_further(tmp_path):
    _nested(tmp_path / "ok", mos.MAX_DEPTH, "a.tmdl")
    assert len(mos._tmdl_files(tmp_path / "ok")) == 1
    _nested(tmp_path / "deep", mos.MAX_DEPTH + 1, "a.tmdl")
    with pytest.raises(ValueError, match="nested too deeply"):
        mos._tmdl_files(tmp_path / "deep")


def test_more_than_twenty_thousand_tmdl_files_are_refused(tmp_path):
    for i in range(mos.MAX_FILES + 1):
        (tmp_path / f"{i}.tmdl").touch()
    with pytest.raises(ValueError, match="more than 20000 TMDL files"):
        mos._tmdl_files(tmp_path)


# ---- reading a model folder at the size cap


def test_a_tmdl_folder_of_exactly_the_cap_is_read_and_one_byte_more_isnt(tmp_path, monkeypatch):
    # A cap of 120 bytes for the folder only: the TMDL reader keeps its own cap.
    monkeypatch.setattr(mos, "tmdl", types.SimpleNamespace(MAX_CHARS=30, to_bim=tmdl.to_bim))
    folder = tmp_path / "M.SemanticModel"
    (folder / "definition").mkdir(parents=True)
    (folder / "definition" / "model.tmdl").write_text("model Model\n" + "\n" * 18, encoding="utf-8")  # 30
    (folder / "definition" / "a.tmdl").write_text("\n" * 30, encoding="utf-8")
    (folder / "definition" / "b.tmdl").write_text("\n" * 30, encoding="utf-8")
    (folder / "definition" / "c.tmdl").write_text("\n" * 30, encoding="utf-8")
    bim, _ = mos._folder_bim(folder, "M", "semantic/M.SemanticModel")
    assert bim["name"] == "M"
    (folder / "definition" / "c.tmdl").write_text("\n" * 31, encoding="utf-8")
    with pytest.raises(ValueError, match="M's TMDL is too large to read on this PC"):
        mos._folder_bim(folder, "M", "semantic/M.SemanticModel")


def test_a_model_bim_of_exactly_the_cap_is_read_and_one_byte_more_isnt(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdl, "MAX_CHARS", 10)  # a cap of 40 bytes
    folder = tmp_path / "M.SemanticModel"
    folder.mkdir()
    text = '{"model": {}}'
    (folder / "model.bim").write_text(text + " " * (40 - len(text)), encoding="utf-8")
    assert mos._folder_bim(folder, "M", "p")[0]["name"] == "M"
    (folder / "model.bim").write_text(text + " " * (41 - len(text)), encoding="utf-8")
    with pytest.raises(ValueError, match="M's model.bim is too large to read on this PC"):
        mos._folder_bim(folder, "M", "p")


def test_reading_an_ossie_file_over_four_bytes_a_character_is_refused(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(model_ossie, "MAX_CHARS", 10)  # a cap of 40 bytes
    target = _root(svc) / "semantic" / "m.ossie.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x" * 41, encoding="utf-8")
    with pytest.raises(ValueError, match="is too large for an Ossie model"):
        svc.meta_model_ossie_read("semantic/m.ossie.yaml")
    target.write_text("x" * 40, encoding="utf-8")
    with pytest.raises(ValueError) as caught:  # read, then refused by the parser instead
        svc.meta_model_ossie_read("semantic/m.ossie.yaml")
    assert "too large for an Ossie model" not in str(caught.value)


def test_export_of_a_folder_runs_the_round_trip_check(svc):  # noqa: F811
    folder = _root(svc) / "semantic" / "SM.SemanticModel"
    folder.mkdir(parents=True)
    (folder / "model.bim").write_text(json.dumps(_bim()), encoding="utf-8")
    out = svc.meta_model_ossie_export_folder("semantic/SM.SemanticModel")
    assert "round_trip" in out


# ---- the workspace, import and Save TMDL


def test_without_a_target_test_reads_from_fabric_and_dev_doesnt(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_env_cfg", lambda p, env: {"workspace_id": "ws-test"})
    assert [m["name"] for m in svc.meta_model_ossie_models("test")["models"]] == ["SM_Gold"]
    with pytest.raises(ValueError, match="Test or Prod"):
        svc.meta_model_ossie_models("dev")


def test_a_lakehouse_in_the_environment_needs_no_placeholder_row(svc):  # noqa: F811
    report = svc.meta_model_ossie_preview("test", _ossie(), "SM_Gold")["report"]
    assert not any(r["object"] == "Direct Lake" for r in report)


def test_a_lakehouse_not_in_the_environment_says_it_reads_placeholder_ids(svc, monkeypatch):  # noqa: F811
    store = svc._store(None, "test")
    monkeypatch.setattr(store, "lakehouse_ids", {})
    report = svc.meta_model_ossie_preview("test", _ossie(), "SM_Gold")["report"]
    assert any(r["object"] == "Direct Lake" and "placeholder ids" in r["message"] for r in report)


def test_a_profile_the_converter_cant_read_is_previewed_as_refused(svc):  # noqa: F811
    doc = yaml.safe_load(_ossie())
    doc["version"] = "0.3.0.dev0"
    out = svc.meta_model_ossie_preview("test", yaml.safe_dump(doc), "SM_Gold")
    assert out["convertible"] is False and out["refused"] and out["changes"] == []


def test_import_answers_the_id_fabric_gave(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc.fake, "save_semantic_model", lambda *a, **k: {"id": "m9", "created": False})
    out = svc.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    assert out["id"] == "m9"


def test_import_without_an_id_from_fabric_answers_the_models_id(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc.fake, "save_semantic_model", lambda *a, **k: {"created": False})
    out = svc.meta_model_ossie_import("test", _ossie(), "SM_Gold", confirmed=True)
    assert out["id"] == "m1"


def test_save_tmdl_of_a_model_that_isnt_there_is_a_lookup_error(svc):  # noqa: F811
    with pytest.raises(LookupError, match="There's no semantic model named Nope in WS Test"):
        svc.meta_model_tmdl_save("test", "Nope")


def test_save_tmdl_names_the_first_empty_file(svc, monkeypatch):  # noqa: F811
    parts = _parts(**{"definition/model.tmdl": "model Model\n", "definition/tables/a.tmdl": " ",
                      "definition/tables/b.tmdl": ""})
    monkeypatch.setattr(svc.fake, "get_item_definition", lambda *a, **k: parts)
    with pytest.raises(RuntimeError, match="because Fabric sent definition/tables/a.tmdl empty"):
        svc.meta_model_tmdl_save("test", "SM_Gold")


@pytest.mark.parametrize("path", ["../outside.tmdl", "/abs/x.tmdl"])
def test_save_tmdl_refuses_a_part_path_outside_the_folder(svc, monkeypatch, path):  # noqa: F811
    parts = _parts(**{"definition/model.tmdl": "model Model\n", path: "table x\n"})
    monkeypatch.setattr(svc.fake, "get_item_definition", lambda *a, **k: parts)
    with pytest.raises(ValueError, match="Fabric returned an unexpected part path"):
        svc.meta_model_tmdl_save("test", "SM_Gold")


def _outside(svc, monkeypatch):  # noqa: F811
    """_in_place sending one target outside its folder: the last check before writing refuses it."""
    real = mos._in_place
    away = _root(svc) / "elsewhere"
    away.mkdir(exist_ok=True)

    def moved(folder, rel, existing):
        target = real(folder, rel, existing)
        return away / target.name if target.name in ("model.tmdl", "f.yaml") else target
    monkeypatch.setattr(mos, "_in_place", moved)
    return away


def test_save_tmdl_refuses_a_target_outside_the_models_folder(svc, monkeypatch):  # noqa: F811
    away = _outside(svc, monkeypatch)
    parts = _parts(**{"definition/model.tmdl": "model Model\n"})
    monkeypatch.setattr(svc.fake, "get_item_definition", lambda *a, **k: parts)
    with pytest.raises(ValueError, match="Fabric returned a part outside the model's folder: definition/model.tmdl"):
        svc.meta_model_tmdl_save("test", "SM_Gold")
    assert list(away.iterdir()) == []


def test_a_view_refuses_a_target_outside_its_folder(svc, monkeypatch):  # noqa: F811
    away = _outside(svc, monkeypatch)
    from fabric_desktop import semantic_view
    monkeypatch.setattr(semantic_view, "semantic_view", lambda *a, **k: {"files": {"f.yaml": "name: x\n"}})
    with pytest.raises(ValueError, match="f.yaml would be written outside"):
        svc.meta_model_semantic_view_save("Sales", text=_ossie())
    assert list(away.iterdir()) == []


def test_to_tmdl_writes_the_pbism_with_two_space_indents(svc):  # noqa: F811
    preview = svc.meta_model_ossie_to_tmdl(_ossie(), "SM")["confirm"]
    svc.meta_model_ossie_to_tmdl(_ossie(), "SM", confirmed=True, token=preview["token"])
    pbism = _root(svc) / preview["folder"] / "definition.pbism"
    assert pbism.read_text(encoding="utf-8") == '{\n  "version": "4.0",\n  "settings": {}\n}\n'


# ---- Semantic View files


def test_a_view_from_text_over_the_cap_is_refused(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tmdl, "MAX_CHARS", 10)
    with pytest.raises(ValueError, match="This text is too large to be a semantic model"):
        svc.meta_model_semantic_view_save("Sales", text="x" * 41)


def test_a_stale_view_file_that_stopped_being_a_file_isnt_removed(svc, monkeypatch):  # noqa: F811
    folder = _root(svc) / mos.VIEW_FOLDER / "Sales"
    folder.mkdir(parents=True)
    stale = folder / "old.yaml"
    stale.write_text("name: old\n", encoding="utf-8")
    preview = svc.meta_model_semantic_view_save("Sales", text=_ossie())["confirm"]
    assert preview["removed"] == [f"{mos.VIEW_FOLDER}/Sales/old.yaml"]
    real = mos._real
    calls = []

    def swapped(path, kind):
        if Path(path) == stale and kind is mos.stat.S_ISREG:
            calls.append(path)
            if len(calls) > 1:  # listed as a file, then not one just before the unlink
                return False
        return real(path, kind)

    monkeypatch.setattr(mos, "_real", swapped)
    svc.meta_model_semantic_view_save("Sales", text=_ossie(), confirmed=True, token=preview["token"])
    assert stale.exists()


def _view(svc, n_files=1, name="Sales"):  # noqa: F811
    folder = _root(svc) / mos.VIEW_FOLDER / name
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        (folder / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    return folder


def test_a_view_folder_of_exactly_the_entry_cap_is_read(svc):  # noqa: F811
    _view(svc, mos.VIEW_MAX_ENTRIES)
    assert len(svc.meta_model_semantic_view_status("Sales")["files"]) == 200


def test_a_view_folder_one_entry_over_the_cap_is_refused(svc):  # noqa: F811
    _view(svc, mos.VIEW_MAX_ENTRIES + 1)
    with pytest.raises(ValueError, match="holds more than 200 files or folders"):
        svc.meta_model_semantic_view_status("Sales")


def test_a_view_folder_eight_down_is_read_and_nine_down_isnt(svc):  # noqa: F811
    folder = _view(svc, 0)
    _nested(folder, mos.MAX_DEPTH, "a.txt")
    assert len(svc.meta_model_semantic_view_status("Sales")["files"]) == 1
    _nested(folder / "x", mos.MAX_DEPTH, "b.txt")  # x/d0/.../d7 is nine down
    with pytest.raises(ValueError, match="is nested too deeply for a semantic view"):
        svc.meta_model_semantic_view_status("Sales")


def test_view_yaml_of_exactly_the_fabric_cap_is_read_and_more_isnt(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ossie_profiles, "FABRIC_MAX_YAML_BYTES", 20)
    folder = _view(svc, 0)
    (folder / "a.yaml").write_text("name: abc\n", encoding="utf-8")  # 10
    (folder / "b.yaml").write_text("name: xyz\n", encoding="utf-8")  # 10
    out = svc.meta_model_semantic_view_status("Sales")
    assert out["files"] == ["a.yaml", "b.yaml"]
    (folder / "c.yaml").write_text("x: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hold more than 16 MB"):
        svc.meta_model_semantic_view_status("Sales")


def test_view_yaml_over_the_fabric_cap_is_refused_before_any_file_is_read(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ossie_profiles, "FABRIC_MAX_YAML_BYTES", 15)
    folder = _view(svc, 0)
    (folder / "a.yaml").write_text("name: abc\n", encoding="utf-8")  # 10
    (folder / "b.yaml").write_text("name: xyz\n", encoding="utf-8")  # 10, and the cap is 15
    opened = []
    monkeypatch.setattr(mos, "open", lambda f, *a, **kw: opened.append(Path(f).name) or open(f, *a, **kw),
                        raising=False)
    with pytest.raises(ValueError, match="hold more than 16 MB"):
        svc.meta_model_semantic_view_status("Sales")
    assert [n for n in opened if n.endswith(".yaml")] == []


def test_two_hundred_saved_views_are_listed_and_more_arent_read(svc):  # noqa: F811
    base = _root(svc) / mos.VIEW_FOLDER
    for i in range(mos.VIEW_MAX_ENTRIES):
        (base / f"v{i:03}").mkdir(parents=True)
    assert len(svc.meta_model_semantic_view_status()["views"]) == 200
    (base / "v200").mkdir()
    with pytest.raises(ValueError, match="holds more than 200 entries"):
        svc.meta_model_semantic_view_status()


def test_another_culture_defined_outside_cultures_is_not_refused(svc):  # noqa: F811
    folder = _orders_folder(svc)
    (folder / "definition" / "cultures" / "de-DE.tmdl").write_text(PARTITION_CAPTION, encoding="utf-8")
    (folder / "definition" / "extra.tmdl").write_text(
        "cultureInfo fr-FR\n\ttranslations\n\t\tmodel Model\n\t\t\tcaption: Modele\n", encoding="utf-8")
    plan = svc.meta_model_ossie_to_tmdl(_ossie(), name="orders")["confirm"]
    assert any(r["object"] == "cultures/de-DE.tmdl" for r in plan["report"])
