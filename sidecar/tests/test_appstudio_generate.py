"""AS-10 generation: description -> the person's model -> a plan checked against the metadata -> approve ->
a specification made here. The model is a fake server; what it is sent and what comes back are checked."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit
from fabric_desktop.appstudio import discovery, generate
from fabric_desktop.appstudio import providers as prov
from fabric_desktop.appstudio import spec as specs
from fabric_desktop.appstudio_service import AppStudioMixin

WS = "11111111-2222-3333-4444-555555555555"
ITEM = "66666666-7777-8888-9999-000000000000"

BIM = {"model": {"tables": [
    {"name": "Sales", "columns": [{"name": "Amount", "dataType": "decimal"}, {"name": "Secret", "dataType": "string", "isHidden": True}],
     "measures": [{"name": "Revenue", "expression": "SUM(Sales[Amount])", "description": "Ask jane@contoso.com"},
                  {"name": "Big deals", "expression": "CALCULATE([Revenue], Sales[Customer] = \"Acme Corp\")"}],
     "partitions": [{"source": {"expression": "Sql.Database(\"srv.contoso.com\", \"db\")"}}]},
    {"name": "Region Lookup", "columns": [{"name": "Name", "dataType": "string"}]},
    {"name": "Date", "columns": [{"name": "Month", "dataType": "string"}]}],
    "relationships": [{"fromTable": "Sales", "fromColumn": "Amount", "toTable": "Date", "toColumn": "Month"}]}}

PLAN = {"name": "Sales overview", "purpose": "Revenue for leads", "audience": ["Sales leads"], "pages": [
    {"title": "Overview", "components": [
        {"type": "card", "title": "Revenue", "measure": "Revenue"},
        {"type": "slicer", "title": "Region", "field": "'Region Lookup'[Name]"},
        {"type": "bar", "title": "Revenue by region", "measure": "Revenue", "field": "Region Lookup[Name]"},
        {"type": "line", "title": "By month", "measure": "Revenue", "field": "Date[Month]"},
        {"type": "hologram", "title": "3D", "measure": "Revenue"},
        {"type": "bar", "title": "Invented", "measure": "Margin", "field": "Date[Month]"},
        {"type": "bar", "title": "Bad column", "measure": "Revenue", "field": "Sales[Nope]"}]}],
    "questions": ["Which fiscal year?"]}


class _Svc(AppStudioMixin):
    def __init__(self, root):
        self.root = root
        self.bim_reads = []

    def _model_bim(self, ws, model):
        self.bim_reads.append((ws, model["id"]))
        return BIM


class _Resp:
    def __init__(self, doc):
        self.status_code, self.raw, self.content = 200, None, json.dumps(doc).encode()

    def close(self):
        pass


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = _Svc(tmp_path / "data")
    s.sent = []

    class _Session:
        def request(self, method, url, headers=None, json=None, **kw):
            s.sent.append({"url": url, "headers": headers, "body": json})
            return _Resp({"choices": [{"message": {"content": __import__("json").dumps(s.reply)}}],
                          "usage": {"prompt_tokens": 1200, "completion_tokens": 300}})

    s.reply = PLAN
    monkeypatch.setattr("fabric_desktop.appstudio_service.http_session.session", lambda: _Session())
    monkeypatch.setattr(prov, "resolve_key", lambda ref, reader=None: "the-key")
    s.conn = s.appstudio_connection_save({"name": "Contoso", "provider": "openai-compatible", "model": "m1",
                                          "endpoint": "https://llm.contoso.com/v1", "key_ref": "credman:KratosForge/c",
                                          "consent": {"context": True, "sample_rows": False}})
    return s


def model_source():
    return {"workspace_id": WS, "item_id": ITEM, "name": "Sales model"}


def test_generation_is_audited():
    assert audit.audited("appstudio_plan") and audit.audited("appstudio_create_from_plan")


def test_a_plan_keeps_only_what_the_target_and_metadata_support(svc):
    got = svc.appstudio_plan("A sales overview with revenue by region and month", source=model_source())
    titles = [c["title"] for c in got["plan"]["pages"][0]["components"]]
    assert titles == ["Revenue", "Region", "Revenue by region", "By month"]
    assert got["plan"]["pages"][0]["components"][1]["field"] == "Region Lookup[Name]", "a quoted table is accepted"
    left = " ".join(got["left_out"])
    assert "hologram" in left and "'Margin'" in left and "Sales[Nope]" in left
    assert got["plan"]["questions"] == ["Which fiscal year?"]
    assert got["sent"] == {"tables": 3, "columns": 3, "measures": 2, "expressions": False, "rows": 0, "description_chars": 49}
    assert got["usage"]["input_tokens"] == 1200 and got["connection"]["model"] == "m1"


def test_what_the_model_is_sent_is_metadata_only(svc):
    svc.appstudio_plan("Sales overview", source=model_source())
    (req,) = svc.sent
    assert req["headers"]["Authorization"] == "Bearer the-key"
    text = json.dumps(req["body"])
    assert "the-key" not in text
    for gone in ("Secret", "srv.contoso.com", "SUM(Sales[Amount])", "Acme Corp", "jane@contoso.com", "partitions"):
        assert gone not in text, gone
    assert "[email]" in text, "descriptions are redacted for support"
    assert "\\u003cDATA" not in text and "DATA kind=" in text


def test_formulas_go_only_when_asked(svc):
    got = svc.appstudio_plan("Sales overview", source=model_source(), include_expressions=True)
    assert got["sent"]["expressions"] is True
    assert "SUM(Sales[Amount])" in json.dumps(svc.sent[-1]["body"])


def test_no_consent_no_request(svc):
    svc.appstudio_connection_save({**svc.conn, "consent": {"context": False, "sample_rows": False}})
    with pytest.raises(prov.ConsentError):
        svc.appstudio_plan("Sales overview", source=model_source())
    assert svc.sent == [] and svc.bim_reads == [], "consent is checked before the model is read or called"


def test_policy_off_no_request(svc, tmp_path):
    (tmp_path / "pd" / "FabricDesktop").mkdir(parents=True)
    (tmp_path / "pd" / "FabricDesktop" / "policy.json").write_text('{"appstudio_models": false}')
    with pytest.raises(prov.ProviderError, match="turns off"):
        svc.appstudio_plan("Sales overview")
    assert svc.sent == []


def test_approving_makes_a_valid_project_once(svc):
    got = svc.appstudio_plan("Sales overview", source=model_source())
    made = svc.appstudio_create_from_plan(got["plan_id"], got["plan"], "Sales leads app")
    spec = made["spec"]
    assert made["valid"] and spec["name"] == "Sales leads app" and specs.validate(spec) == []
    assert spec["sources"] == [{"kind": "semantic-model", "name": "Sales model", "workspace_id": WS, "item_id": ITEM, "id": "src_1"}]
    assert spec["measures"] == [{"id": "m_1", "name": "Revenue", "source": "src_1", "expression": "[Revenue]"}]
    comps = spec["pages"][0]["components"]
    assert [c["fields"] for c in comps] == [[], ["'Region Lookup'[Name]"], ["'Region Lookup'[Name]"], ["Date[Month]"]]
    assert all(c["layout"]["x"] + c["layout"]["w"] <= 12 for c in comps)
    with pytest.raises(ValueError, match="expired"):
        svc.appstudio_create_from_plan(got["plan_id"], got["plan"])


def test_an_edited_plan_is_checked_again(svc):
    got = svc.appstudio_plan("Sales overview", source=model_source())
    edited = json.loads(json.dumps(got["plan"]))
    edited["pages"][0]["components"].append({"type": "table", "title": "Sneaky", "measure": "Revenue", "field": "Sales[Secret2]"})
    made = svc.appstudio_create_from_plan(got["plan_id"], edited)
    assert all(c["title"] != "Sneaky" for c in made["spec"]["pages"][0]["components"])
    assert any("Sales[Secret2]" in x for x in made["left_out"])


def test_a_plan_with_nothing_usable_is_refused(svc):
    svc.reply = {"name": "x", "purpose": "", "pages": [{"title": "P", "components": [{"type": "bar", "title": "x", "measure": "Nope", "field": "A[B]"}]}]}
    with pytest.raises(specs.SpecError, match="Nothing"):
        svc.appstudio_plan("Sales overview", source=model_source())
    svc.reply = "not an object"
    with pytest.raises(prov.ProviderError):
        svc.appstudio_plan("Sales overview", source=model_source())


def test_sample_data_plans_without_a_model_read(svc):
    svc.reply = {"name": "Ops", "purpose": "", "pages": [{"title": "Today", "components": [
        {"type": "card", "title": "Late", "measure": "Late"}, {"type": "column", "title": "Late by site", "measure": "Late", "field": "Site[Name]"}]}]}
    got = svc.appstudio_plan("Operations status")
    made = svc.appstudio_create_from_plan(got["plan_id"], got["plan"])
    assert made["spec"]["sources"][0]["kind"] == "sample" and svc.bim_reads == []
    assert len(svc.appstudio_preview(made["project"])["components"]) == 2


@pytest.mark.parametrize("bad", [{"workspace_id": "../x", "item_id": ITEM}, {"workspace_id": WS}, {"workspace_id": WS, "item_id": "x"}])
def test_model_ids_must_be_ids(svc, bad):
    with pytest.raises(ValueError, match="Choose a semantic model"):
        svc.appstudio_plan("Sales", source=bad)


def test_description_limits(svc):
    with pytest.raises(ValueError, match="Describe"):
        svc.appstudio_plan("   ")
    with pytest.raises(ValueError, match="under"):
        svc.appstudio_plan("x" * 4001)


def test_kratosforge_target_refuses_what_it_cant_show():
    meta = discovery.from_bim(BIM)
    got = generate.check_plan({"name": "x", "pages": [{"title": "P", "components": [
        {"type": "matrix", "title": "M", "measure": "Revenue", "field": "Date[Month]"},
        {"type": "card", "title": "C", "measure": "Revenue"}]}]}, meta, "kratosforge-app")
    assert [c["type"] for c in got["plan"]["pages"][0]["components"]] == ["card"]
    assert "matrix" in got["left_out"][0]


def test_sample_metadata_covers_every_template():
    meta = generate.sample_metadata()
    names = {t["name"] for t in meta["tables"]}
    assert {"Region", "Date", "Product", "Site", "Measures"} <= names


def test_a_failed_approval_keeps_the_plan_and_a_second_approval_finds_it_used(svc):
    got = svc.appstudio_plan("Sales overview", source=model_source())
    svc.appstudio_create("Sales overview", "blank")  # the plan's name is taken
    with pytest.raises(ValueError, match="already exists"):
        svc.appstudio_create_from_plan(got["plan_id"], got["plan"])
    made = svc.appstudio_create_from_plan(got["plan_id"], got["plan"], "Another name")
    assert made["valid"]
    with pytest.raises(ValueError, match="expired"):
        svc.appstudio_create_from_plan(got["plan_id"], got["plan"], "Third")
