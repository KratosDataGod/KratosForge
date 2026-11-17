"""App Studio publishing: the readiness check's refusals and warnings, read with no Fabric call at all."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop.appstudio import discovery, generate, pbir
from fabric_desktop.appstudio import publish as publishing
from fabric_desktop.appstudio.spec import SpecError

from test_appstudio_generate import BIM, ITEM, PLAN, WS


@pytest.fixture
def ready():
    meta = discovery.from_bim(BIM)
    checked = generate.check_plan(PLAN, meta, "powerbi-report")
    spec = generate.to_spec(checked["plan"], meta, "powerbi-report",
                            {"kind": "semantic-model", "name": "Sales model", "workspace_id": WS, "item_id": ITEM})
    return spec, meta


def _first_component(spec):
    return next(c for p in spec["pages"] for c in p["components"] if c.get("fields"))


def test_only_a_power_bi_report_publishes_and_sample_data_must_be_connected_first(ready):
    spec, _ = ready
    other = {**spec, "target": "not-a-target"}
    got = publishing.check(other, None, ITEM)
    assert got == {"ready": False, "warnings": [], "parts": {}, "problems": [
        "Only a Power BI report publishes from App Studio today; this app is a not-a-target.",
        "The app reads sample data. Connect it to a semantic model before publishing."]}


def test_a_field_that_isnt_a_table_column_reference_blocks_publishing(ready, monkeypatch):
    spec, meta = ready
    spec = copy.deepcopy(spec)
    comp = _first_component(spec)
    comp["fields"] = ["just words"]
    monkeypatch.setattr(pbir, "compile_report", lambda *a, **k: {"warnings": [], "parts": {"x": "y"}})
    got = publishing.check(spec, meta, ITEM)
    assert not got["ready"] and got["parts"] == {}
    assert any("isn't a Table[Column] reference" in p for p in got["problems"])


def test_a_column_the_model_doesnt_have_blocks_and_keeps_the_compiled_warnings(ready, monkeypatch):
    spec, meta = ready
    spec = copy.deepcopy(spec)
    comp = _first_component(spec)
    comp["fields"] = ["Nowhere[Missing]"]
    monkeypatch.setattr(pbir, "compile_report", lambda *a, **k: {"warnings": ["w1"], "parts": {"x": "y"}})
    got = publishing.check(spec, meta, ITEM)
    assert not got["ready"] and got["warnings"] == ["w1"] and got["parts"] == {}
    assert any("uses Nowhere[Missing], which the semantic model doesn't have." in p for p in got["problems"])


def test_a_specification_that_wont_compile_lists_the_compilers_problems(ready, monkeypatch):
    spec, meta = ready

    def refuse(*a, **k):
        raise SpecError(["page 1 has no visuals"])

    monkeypatch.setattr(pbir, "compile_report", refuse)
    got = publishing.check(spec, meta, ITEM)
    assert got == {"ready": False, "problems": ["page 1 has no visuals"], "warnings": [], "parts": {}}


def test_a_model_larger_than_app_studio_reads_publishes_with_a_warning(ready):
    spec, meta = ready
    got = publishing.check(spec, {**meta, "truncated": True}, ITEM)
    assert got["ready"] and got["parts"]
    assert "larger than App Studio reads" in got["warnings"][-1]


def test_history_and_conflicts_read_only_this_projects_publishes():
    history = [{"kind": "publish", "workspace_id": WS, "report_id": "r1", "state": "done"},
               {"kind": "publish", "workspace_id": "other", "report_id": "r2"},
               {"kind": "publish", "workspace_id": WS, "report_id": ""},
               {"kind": "edit", "workspace_id": WS, "report_id": "r3"},
               {"kind": "publish", "workspace_id": WS, "report_id": None, "state": "started"}]
    assert publishing.published_ids(history, WS) == {"r1"}
    assert publishing.unfinished(history)["state"] == "started"
    assert publishing.unfinished(history[:1]) is None and publishing.unfinished([]) is None
    items = [{"type": "SemanticModel", "displayName": "Sales"}, {"type": "Report", "displayName": "Sales"}]
    assert publishing.conflict(items, "Sales", {"r1"}) == {"id": "", "ours": False}
    assert publishing.conflict(items, "Other", set()) is None


def test_the_fingerprint_changes_with_the_report_it_would_replace():
    a = publishing.fingerprint({"s": 1}, WS, ITEM, "n", {"p": "t"}, None)
    b = publishing.fingerprint({"s": 1}, WS, ITEM, "n", {"p": "t"}, {"id": "r1"})
    assert a != b and a == publishing.fingerprint({"s": 1}, WS, ITEM, "n", {"p": "t"}, {})
    assert publishing.model_source({"sources": [{"kind": "semantic-model", "workspace_id": WS}]}) is None
    assert publishing.model_source({}) is None
