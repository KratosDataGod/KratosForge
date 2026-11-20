"""Semantic model controls (FEA-304): each has a pass, a fail and a not-evaluated case; the evidence
keeps names and counts, never DAX, M or the definition text; the demo makes no Fabric call."""

from __future__ import annotations

import base64
import copy
import json

import pytest

from fabric_desktop import excellence_service
from fabric_desktop.excellence import adapters, assess, catalog, catalog_models, inspect_models
from fabric_desktop.service import Service

AT = "2026-11-18T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
SM1 = "5e5e5e5e-0000-4000-8000-000000000001"
SECRET_DAX = "CALCULATE(SUM(Sales[secret_margin]), KEEPFILTERS(Region[code] = \"zz-hidden\"))"  # pragma: allowlist secret (made-up DAX that must never reach evidence)
SECRET_FILTER = "[owner_email] = USERPRINCIPALNAME() && [tier] = \"platinum-only\""
SECRET_M = 'let Source = Sql.Database("hidden-endpoint.example", "aaaaaaaa-2222-3333-4444-555555555555") in Source'
LEAKS = ("secret_margin", "zz-hidden", "platinum-only", "USERPRINCIPALNAME", "hidden-endpoint", "Sql.Database",
         "KEEPFILTERS")
MODEL_CONTROLS = [c.id for c in catalog_models.CONTROLS]


def col(name: str, kind: str | None = "int64", hidden: bool = False) -> dict:
    c = {"name": name, "sourceColumn": name, "isHidden": hidden}
    if kind:
        c["dataType"] = kind
    return c


def good_bim() -> dict:
    """A model every control passes: one-way, integer, hidden keys, a row filter, Direct Lake only."""
    dl = {"type": "entity", "entityName": "x", "expressionSource": "DatabaseQuery"}
    return {"model": {
        "directLakeBehavior": "DirectLakeOnly",
        "expressions": [{"name": "DatabaseQuery", "kind": "m", "expression": SECRET_M}],
        "tables": [
            {"name": "Sales", "partitions": [{"name": "p", "mode": "directLake", "source": dl}],
             "columns": [col("region_sk", hidden=True), col("amount", "decimal")],
             "measures": [{"name": "Margin", "expression": SECRET_DAX}]},
            {"name": "Region", "partitions": [{"name": "p", "mode": "directLake", "source": dl}],
             "columns": [col("region_sk", hidden=True), col("code", "string")]}],
        "relationships": [{"name": "r", "fromTable": "Sales", "fromColumn": "region_sk", "toTable": "Region",
                           "toColumn": "region_sk", "crossFilteringBehavior": "oneDirection"}],
        "roles": [{"name": "Owners", "members": [{"memberName": "someone@example.com"}],
                   "tablePermissions": [{"name": "Sales", "filterExpression": SECRET_FILTER}]}]}}


def _rel(b: dict) -> dict:
    return b["model"]["relationships"][0]


def _bidi(b):
    _rel(b)["crossFilteringBehavior"] = "bothDirections"


def _no_rls(b):
    b["model"]["roles"] = []


def _fallback(b):
    b["model"]["directLakeBehavior"] = "Automatic"


def _types(b):
    b["model"]["tables"][0]["columns"][0]["dataType"] = "string"


def _text_key(b):
    for t in b["model"]["tables"]:
        t["columns"][0]["dataType"] = "string"


def _visible(b):
    b["model"]["tables"][0]["columns"][0]["isHidden"] = False


FAILS = {"FEA-SM-001": _bidi, "FEA-SM-002": _no_rls, "FEA-SM-003": _fallback, "FEA-SM-004": _types,
         "FEA-SM-005": _text_key, "FEA-SM-006": _visible}


def run(models: list[tuple[str, dict | None]]) -> dict:
    """Assess a workspace holding these models (``None``: its definition couldn't be read)."""
    reads = [{"item_id": i, "name": i, "workspace_id": WSID,
              "inspected": inspect_models.inspect(b) if b is not None else {"read": False, "problem": "403"}}
             for i, b in models]
    items = [{"id": i, "type": "SemanticModel", "displayName": i} for i, _ in models]
    records = (adapters.workspace({"id": WSID, "displayName": "Sales"}, items, collected_at=AT)
               + adapters.models(reads, collected_at=AT))
    return assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})


def outcome_of(snap: dict, cid: str, subject_id: str) -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid and o["subject"]["id"] == subject_id]
    assert len(got) == 1, got
    return got[0]


def test_there_are_three_to_six_semantic_model_controls_and_each_justifies_its_severity():
    assert 3 <= len(catalog_models.CONTROLS) <= 6
    for c in catalog_models.CONTROLS:
        assert c.dimension == "semantic_models" and c.item_types == ("SemanticModel",)
        assert c.references and f"{c.severity.capitalize()}:" in (c.evaluate.__doc__ or "")


@pytest.mark.parametrize("cid", MODEL_CONTROLS)
def test_each_control_passes_fails_and_is_not_evaluated(cid):
    bad = good_bim()
    FAILS[cid](bad)
    snap = run([("good", good_bim()), ("bad", bad), ("unread", None)])
    assert outcome_of(snap, cid, "good")["outcome"] == "pass"
    failed = outcome_of(snap, cid, "bad")
    assert failed["outcome"] == "fail" and failed["basis"] == "verified_configuration"
    assert outcome_of(snap, cid, "unread")["outcome"] == "not_evaluated"
    # every other control still passes on the bad model: each fixture breaks exactly one rule
    others = {o["control_id"]: o["outcome"] for o in snap["outcomes"]
              if o["subject"]["id"] == "bad" and o["control_id"] in MODEL_CONTROLS and o["control_id"] != cid}
    if cid not in ("FEA-SM-004", "FEA-SM-005"):  # a type change can trip both type rules
        assert set(others.values()) == {"pass"}, others


def test_findings_name_objects_and_counts():
    bad = good_bim()
    _bidi(bad)
    _visible(bad)
    snap = run([("bad", bad)])
    bidi = outcome_of(snap, "FEA-SM-001", "bad")
    assert "Sales.region_sk to Region.region_sk" in bidi["text"] and bidi["detail"]["count"] == 1
    vis = outcome_of(snap, "FEA-SM-006", "bad")
    assert vis["detail"] == {"rule": "SM004", "count": 1, "objects": ["Sales.region_sk"]}


def test_direct_lake_fallback_is_not_applicable_without_direct_lake_and_passes_on_onelake():
    imported = good_bim()
    for t in imported["model"]["tables"]:
        t["partitions"] = [{"name": "p", "mode": "import", "source": {"type": "m", "expression": SECRET_M}}]
    onelake = good_bim()
    _fallback(onelake)
    onelake["model"]["expressions"][0]["expression"] = (
        'AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/'
        'aaaaaaaa-2222-3333-4444-555555555555/bbbbbbbb-2222-3333-4444-555555555555")')
    snap = run([("import", imported), ("onelake", onelake)])
    assert outcome_of(snap, "FEA-SM-003", "import")["outcome"] == "not_applicable"
    assert outcome_of(snap, "FEA-SM-003", "onelake")["outcome"] == "pass"


def test_rls_with_roles_but_no_row_filter_is_informational():
    b = good_bim()
    b["model"]["roles"][0]["tablePermissions"] = []
    o = outcome_of(run([("m", b)]), "FEA-SM-002", "m")
    assert o["outcome"] == "fail" and o["severity"] == "informational" and "1 role" in o["text"]


def test_a_relationship_whose_types_arent_shown_is_not_evaluated_not_passed():
    b = good_bim()
    del b["model"]["tables"][1]["columns"][0]["dataType"]
    snap = run([("m", b)])
    for cid in ("FEA-SM-004", "FEA-SM-005"):
        assert outcome_of(snap, cid, "m")["outcome"] == "not_evaluated"
    b = good_bim()
    b["model"]["relationships"][0]["fromColumn"] = "missing_col"
    assert outcome_of(run([("m", b)]), "FEA-SM-006", "m")["outcome"] == "not_evaluated"


def test_a_definition_without_a_model_is_not_evaluated():
    reads = [{"item_id": "m", "name": "m", "workspace_id": WSID, "inspected": inspect_models.inspect({"x": 1})}]
    recs = adapters.models(reads, collected_at=AT)
    assert recs[0]["confidence"] == "insufficient"


def test_no_definition_text_reaches_evidence_or_the_result():
    bad = good_bim()
    for f in FAILS.values():
        f(bad)
    snap = run([("good", good_bim()), ("bad", bad)])
    text = json.dumps(snap)
    for leak in (*LEAKS, "someone@example.com"):
        assert leak not in text, leak


# ---- the engine call


class FakeFabric:
    def __init__(self, fail_models: bool = False):
        self.calls: list[tuple] = []
        self.fail_models = fail_models

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return {"id": ws, "displayName": "Sales", "description": "d"}

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": SM1, "type": "SemanticModel", "displayName": "Sales"}]

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", item, fmt))
        if self.fail_models:
            raise PermissionError("403 Forbidden")
        bad = good_bim()
        _bidi(bad)
        return {"definition": {"parts": [{"path": "model.bim", "payloadType": "InlineBase64",
                                          "payload": base64.b64encode(json.dumps(bad).encode()).decode()}]}}

    def list_job_instances(self, ws, item):
        self.calls.append(("list_job_instances", item))
        return []

    def list_role_assignments(self, ws):
        self.calls.append(("list_role_assignments", ws))
        return []

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        return []


def test_the_call_reads_model_definitions_as_tmsl_and_keeps_no_text(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    r = Service(root=tmp_path / "app").excellence_assess(workspace_id=WSID)
    assert ("get_item_definition", SM1, "TMSL") in fake.calls
    assert outcome_of(r, "FEA-SM-001", SM1)["outcome"] == "fail"
    assert outcome_of(r, "FEA-SM-002", SM1)["outcome"] == "pass"
    assert any(x["read"] == "semantic model definitions" and x["ok"] for x in r["reads"])
    text = json.dumps(r)
    assert all(leak not in text for leak in LEAKS)


def test_a_failed_model_read_is_reported_and_leaves_the_controls_not_evaluated(tmp_path, monkeypatch):
    fake = FakeFabric(fail_models=True)
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    r = Service(root=tmp_path / "app").excellence_assess(workspace_id=WSID)
    got = next(x for x in r["reads"] if x["read"] == "semantic model definitions")
    assert got["ok"] is False and "1 couldn't be read" in got["problem"]
    assert {outcome_of(r, c, SM1)["outcome"] for c in MODEL_CONTROLS} == {"not_evaluated"}


def test_model_reads_are_capped(monkeypatch):
    monkeypatch.setattr(excellence_service, "MAX_MODELS", 2)
    opt = {"workloads": [{"item_id": f"m{i}", "type": "SemanticModel", "name": f"m{i}", "workspace_id": WSID}
                         for i in range(3)]}
    seen = []
    rows, entry = excellence_service._models(opt, lambda i: seen.append(i) or copy.deepcopy(good_bim()))
    assert seen == ["m0", "m1"] and rows[2]["inspected"]["read"] is False
    assert entry is not None and entry["ok"] is False and entry["count"] == 2


def test_the_demo_decides_the_model_controls_with_no_fabric_call(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    r = Service(root=tmp_path / "app").excellence_assess(demo=True)
    assert fake.calls == [] and r["fixture"] is True
    decided = {o["control_id"]: o["outcome"] for o in r["outcomes"] if o["control_id"] in MODEL_CONTROLS}
    assert set(decided) == set(MODEL_CONTROLS)
    assert {"pass", "fail"} <= set(decided.values()) and "not_evaluated" not in decided.values()
    text = json.dumps(r)
    assert "SUM(Orders[amount])" not in text and '[country] = "NL"' not in text and "Sql.Database" not in text
    assert assess.rescore(json.loads(json.dumps(r)), catalog.registry())["scores"] == r["scores"]


def test_an_inactive_relationship_doesnt_count_as_filtering_both_ways():
    b = good_bim()
    _bidi(b)
    _rel(b)["isActive"] = False
    got = inspect_models.inspect(b)
    assert got["bidirectional"] == [] and got["bidirectional_count"] == 0


def test_each_kept_name_is_capped():
    b = good_bim()
    b["model"]["roles"][0]["name"] = "r" * 5000
    got = inspect_models.inspect(b)
    assert len(got["row_filter_roles"][0]) == inspect_models.MAX_NAME
    assert all(len(n) <= inspect_models.MAX_NAME for n in json.loads(json.dumps(got, default=str)).get("row_filter_tables"))


@pytest.mark.parametrize("bad", [
    {"roles": ["Owners"]}, {"roles": [None]}, {"tables": "Sales"}, {"tables": [None]},
    {"relationships": [{"fromTable": ["Sales"], "toTable": "Region"}]},
])
def test_a_definition_of_the_wrong_shape_is_an_error_not_a_crash(bad):
    b = good_bim()
    b["model"].update(bad)
    got = inspect_models.inspect(b)
    assert got["read"] is True and "isn't shaped like a model.bim" in got["error"]


def test_an_item_id_that_isnt_a_guid_is_never_read(tmp_path, monkeypatch):
    fake = FakeFabric()
    fake.list_items = lambda ws: [{"id": "../../x", "type": "SemanticModel", "displayName": "Sales"}]
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    r = Service(root=tmp_path / "app").excellence_assess(workspace_id=WSID)
    assert not [c for c in fake.calls if c[0] == "get_item_definition"]
    got = next(x for x in r["reads"] if x["read"] == "semantic model definitions")
    assert got["ok"] is False and "1 couldn't be read" in got["problem"]
