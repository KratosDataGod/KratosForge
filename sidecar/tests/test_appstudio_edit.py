"""AS-11 conversational edits: request -> the person's model -> named changes -> patch operations made here ->
validated preview -> approve -> one undoable step. Untouched components stay byte-equal."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit
from fabric_desktop.appstudio import discovery, edit, generate
from fabric_desktop.appstudio import providers as prov
from fabric_desktop.appstudio import spec as specs

from test_appstudio_generate import BIM, ITEM, PLAN, WS, _Resp, _Svc


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = _Svc(tmp_path / "data")
    s.sent = []

    class _Session:
        def request(self, method, url, headers=None, json=None, **kw):
            s.sent.append({"url": url, "headers": headers, "body": json})
            return _Resp({"choices": [{"message": {"content": __import__("json").dumps(s.reply)}}],
                          "usage": {"prompt_tokens": 800, "completion_tokens": 100}})

    monkeypatch.setattr("fabric_desktop.appstudio_service.http_session.session", lambda: _Session())
    monkeypatch.setattr(prov, "resolve_key", lambda ref, reader=None: "the-key")
    s.appstudio_connection_save({"name": "Contoso", "provider": "openai-compatible", "model": "m1",
                                 "endpoint": "https://llm.contoso.com/v1", "key_ref": "credman:KratosForge/c",
                                 "consent": {"context": True, "sample_rows": False}})
    s.reply = PLAN
    got = s.appstudio_plan("Sales", source={"workspace_id": WS, "item_id": ITEM, "name": "Sales model"})
    s.project = s.appstudio_create_from_plan(got["plan_id"], got["plan"])["project"]
    s.sent.clear()
    return s


def spec_of(svc):
    return svc.appstudio_open(svc.project)["spec"]


def test_edits_are_audited():
    assert audit.audited("appstudio_change_plan") and audit.audited("appstudio_apply_change")


def test_a_change_is_previewed_then_applied_as_one_undoable_step(svc):
    before = spec_of(svc)
    svc.reply = {"summary": "Region chart as columns, profit card", "changes": [
        {"action": "set_type", "id": "c_3", "type": "column"},
        {"action": "add_component", "page": "pg_1", "type": "card", "title": "Big deals", "measure": "Big deals"},
        {"action": "set_title", "id": "c_4", "title": "Revenue by month"}]}
    got = svc.appstudio_change_plan(svc.project, "Make the region chart a column chart and add a big deals card")
    assert got["left_out"] == [] and len(got["done"]) == 3
    assert spec_of(svc) == before, "nothing is saved before approval"
    preview = {c["id"]: c for c in got["spec"]["pages"][0]["components"]}
    assert preview["c_3"]["type"] == "column" and preview["c_4"]["title"] == "Revenue by month"
    new = [c for c in got["spec"]["pages"][0]["components"] if c["id"] not in {x["id"] for x in before["pages"][0]["components"]}]
    assert new[0]["type"] == "card" and new[0]["measures"] == ["m_2"]
    assert {"id": "m_2", "name": "Big deals", "source": "src_1", "expression": "[Big deals]"} in got["spec"]["measures"]
    after = svc.appstudio_apply_change(svc.project, got["change_id"])
    old = {c["id"]: c for c in before["pages"][0]["components"]}
    for c in after["spec"]["pages"][0]["components"]:
        if c["id"] in ("c_1", "c_2"):
            assert json.dumps(c, sort_keys=True) == json.dumps(old[c["id"]], sort_keys=True), "untouched stays byte-equal"
    hist = svc.appstudio_history(svc.project)
    assert hist[0]["source"] == "ai" and hist[0]["note"] == "Region chart as columns, profit card"
    assert svc.appstudio_undo(svc.project)["spec"] == before
    with pytest.raises(ValueError, match="expired"):
        svc.appstudio_apply_change(svc.project, got["change_id"])


def test_the_model_is_shown_ids_and_names_not_formulas_or_sources(svc):
    svc.reply = {"summary": "s", "changes": [{"action": "set_title", "id": "c_1", "title": "Total"}]}
    svc.appstudio_change_plan(svc.project, "Call the revenue card Total")
    body = json.dumps(svc.sent[0]["body"])
    assert '\\"id\\": \\"c_1\\"' in body or '"c_1"' in body
    for gone in (WS, ITEM, "SUM(Sales[Amount])", "Acme Corp", "the-key", "Secret"):
        assert gone not in body, gone


def test_what_doesnt_fit_is_left_out_and_nothing_usable_is_refused(svc):
    svc.reply = {"summary": "s", "changes": [
        {"action": "set_type", "id": "c_3", "type": "hologram"},
        {"action": "set_measure", "id": "c_3", "measure": "Margin"},
        {"action": "set_field", "id": "c_3", "field": "Sales[Secret2]"},
        {"action": "set_title", "id": "nope", "title": "x"},
        {"action": "set_width", "id": "c_3", "width": 5},
        {"action": "drop_table", "id": "c_3"},
        {"action": "set_field", "id": "c_3", "field": "Date[Month]"}]}
    got = svc.appstudio_change_plan(svc.project, "do things")
    assert len(got["left_out"]) == 6 and got["done"] == ["Show c_3 by Date[Month]"]
    svc.reply = {"summary": "s", "changes": [{"action": "set_type", "id": "c_3", "type": "hologram"}]}
    with pytest.raises(specs.SpecError, match="Nothing"):
        svc.appstudio_change_plan(svc.project, "hologram please")


def test_only_components_and_pages_can_be_removed(svc):
    svc.reply = {"summary": "s", "changes": [{"action": "remove", "id": "c_1"}, {"action": "remove", "id": "m_1"},
                                             {"action": "remove", "id": "app"}]}
    got = svc.appstudio_change_plan(svc.project, "remove")
    assert got["changed"] == ["c_1"] and len(got["left_out"]) == 2


def test_a_change_planned_on_an_older_app_is_refused(svc):
    svc.reply = {"summary": "s", "changes": [{"action": "set_title", "id": "c_1", "title": "Total"}]}
    got = svc.appstudio_change_plan(svc.project, "rename")
    svc.appstudio_change(svc.project, [{"op": "set", "id": "c_2", "field": "title", "value": "Area"}])
    with pytest.raises(ValueError, match="changed since"):
        svc.appstudio_apply_change(svc.project, got["change_id"])


def test_a_change_belongs_to_its_project_and_isnt_a_plan(svc):
    svc.reply = {"summary": "s", "changes": [{"action": "set_title", "id": "c_1", "title": "Total"}]}
    got = svc.appstudio_change_plan(svc.project, "rename")
    other = svc.appstudio_create("Other", "blank")["project"]
    with pytest.raises(ValueError, match="expired"):
        svc.appstudio_apply_change(other, got["change_id"])
    with pytest.raises(ValueError, match="expired"):
        svc.appstudio_create_from_plan(got["change_id"], PLAN)
    assert svc.appstudio_apply_change(svc.project, got["change_id"])["valid"], "still usable by its own project"


def test_policy_and_consent_come_first(svc, tmp_path):
    (tmp_path / "pd" / "FabricDesktop").mkdir(parents=True)
    (tmp_path / "pd" / "FabricDesktop" / "policy.json").write_text('{"appstudio_models": false}')
    reads = len(svc.bim_reads)
    with pytest.raises(prov.ProviderError, match="turns off"):
        svc.appstudio_change_plan(svc.project, "anything")
    assert svc.sent == [] and len(svc.bim_reads) == reads


def test_request_limits(svc):
    with pytest.raises(ValueError, match="Say what"):
        svc.appstudio_change_plan(svc.project, "  ")
    with pytest.raises(ValueError, match="under"):
        svc.appstudio_change_plan(svc.project, "x" * 4001)


def test_add_page_and_rename_app():
    meta = discovery.from_bim(BIM)
    doc = generate.to_spec(generate.check_plan(PLAN, meta, "powerbi-report")["plan"], meta, "powerbi-report",
                           {"kind": "sample", "name": "S"})
    made = edit.to_ops({"summary": "", "changes": [{"action": "add_page", "title": "Details"},
                                                   {"action": "rename_app", "title": "Sales 2"}]}, doc, meta)
    out = edit.preview(doc, made["ops"])
    assert out["spec"]["name"] == "Sales 2" and out["spec"]["pages"][-1]["title"] == "Details"
