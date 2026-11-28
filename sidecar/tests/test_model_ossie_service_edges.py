"""Ossie export and import, and Save TMDL to Git: the refusals the tests in test_model_ossie don't reach.
Fabric is the fake in test_model_ossie; nothing reaches a tenant."""

import base64
import json
from pathlib import Path

import pytest

from fabric_desktop import model_ossie, tmdl
from fabric_desktop.meta.store import OneLakeStore
from tests.test_model_ossie import FakeFabric, _ossie, svc  # noqa: F401  (svc is a fixture)


def _parts(**files):
    return {"definition": {"parts": [{"path": p, "payload": base64.b64encode(t.encode()).decode()}
                                     for p, t in files.items()]}}


def _model_folder(svc, name):  # noqa: F811
    folder = Path(svc._project().root) / "semantic" / f"{name}.SemanticModel"
    folder.mkdir(parents=True)
    return folder


def test_a_tmsl_answer_without_model_bim_is_refused(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc.fake, "get_item_definition", lambda *a, **k: _parts(**{"definition.pbism": "{}"}))
    with pytest.raises(RuntimeError, match="Fabric didn't return SM_Gold's model.bim"):
        svc.meta_model_ossie_export("test", "SM_Gold")


def test_a_folder_model_too_large_to_read_is_refused(svc, monkeypatch):  # noqa: F811
    tmdl_model = _model_folder(svc, "Big") / "definition"
    tmdl_model.mkdir()
    (tmdl_model / "model.tmdl").write_text("model Model\n" + "x" * 100, encoding="utf-8")
    (_model_folder(svc, "Bim") / "model.bim").write_text(json.dumps(FakeFabric().bim), encoding="utf-8")
    monkeypatch.setattr(tmdl, "MAX_CHARS", 10)
    with pytest.raises(ValueError, match="Big's TMDL is too large"):
        svc.meta_model_ossie_export_folder("semantic/Big.SemanticModel")
    with pytest.raises(ValueError, match="Bim's model.bim is too large"):
        svc.meta_model_ossie_export_folder("semantic/Bim.SemanticModel")


def test_a_model_folder_with_neither_tmdl_nor_model_bim_is_refused(svc):  # noqa: F811
    _model_folder(svc, "Empty")
    with pytest.raises(ValueError, match="has neither definition/"):
        svc.meta_model_ossie_export_folder("semantic/Empty.SemanticModel")


def test_an_ossie_file_too_large_to_be_a_model_is_refused_before_it_is_read(svc, monkeypatch):  # noqa: F811
    svc.meta_model_ossie_save("semantic/orders.ossie.yaml", _ossie())
    monkeypatch.setattr(model_ossie, "MAX_CHARS", 10)
    with pytest.raises(ValueError, match="too large for an Ossie model"):
        svc.meta_model_ossie_read("semantic/orders.ossie.yaml")


def test_a_lakehouse_not_in_the_environment_yet_says_direct_lake_is_lost(svc, tmp_path, monkeypatch):  # noqa: F811
    empty = OneLakeStore("test", "ws-test", {}, lambda: "tok", tmp_path / "cache2")
    monkeypatch.setattr(svc, "_store", lambda p, env, create=False: empty)
    report = svc.meta_model_ossie_preview("test", _ossie(), "SM_Gold")["report"]
    lost = [r for r in report if r["object"] == "Direct Lake"]
    assert lost and lost[0]["kind"] == "lost" and "promote first" in lost[0]["message"]


def test_save_tmdl_of_a_model_that_is_not_there_is_refused(svc):  # noqa: F811
    with pytest.raises(LookupError, match="no semantic model named Nope in WS Test"):
        svc.meta_model_tmdl_save("test", "Nope")


def test_save_tmdl_with_no_parts_from_fabric_writes_nothing(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc.fake, "get_item_definition", lambda *a, **k: {"definition": {"parts": []}})
    with pytest.raises(RuntimeError, match="returned no definition for SM_Gold"):
        svc.meta_model_tmdl_save("test", "SM_Gold")
    assert not (Path(svc._project().root) / "semantic").exists()


@pytest.mark.parametrize("bad", ["../escape.tmdl", "/abs.tmdl"])
def test_save_tmdl_refuses_a_part_path_that_leaves_the_model_folder(svc, monkeypatch, bad):  # noqa: F811
    monkeypatch.setattr(svc.fake, "get_item_definition", lambda *a, **k: _parts(**{bad: "table x\n"}))
    with pytest.raises(ValueError, match="unexpected part path"):
        svc.meta_model_tmdl_save("test", "SM_Gold")
