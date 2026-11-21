"""Warehouse and SQL analytics endpoint controls (FEA-303): pass, fail and not-evaluated cases; a
failed read and the read cap; evidence keeps names and counts, never SQL; the warehouse read uses only
a token the app already holds; the demo calls nothing."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import estate_service, excellence_service
from fabric_desktop.auth import FABRIC_SCOPES
from fabric_desktop.excellence import adapters, assess, catalog, catalog_warehouse as cw, controls as ct
from fabric_desktop.service import Service

AT = "2026-11-20T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
LH = "1a1a1a1a-0000-4000-8000-000000000001"
EP = "2b2b2b2b-0000-4000-8000-000000000001"
BIN2 = "Latin1_General_100_BIN2_UTF8"
CI = "Latin1_General_100_CI_AS_KS_WS_SC_UTF8"
SQL_TEXT = "CREATE VIEW dbo.secret_view AS SELECT ssn FROM hr.people"
LEAKS = ("secret_view", "hr.people", "ssn", "SELECT", "datawarehouse.fabric.microsoft.com")
WH_CONTROLS = [c.id for c in cw.CONTROLS]


def wh_id(i: int) -> str:
    return f"3c3c3c3c-0000-4000-8000-{i:012d}"


def ws() -> dict:
    return {"id": WSID, "displayName": "Sales"}


def item(i: str, kind: str, name: str | None = None) -> dict:
    return {"id": i, "type": kind, "displayName": name or i}


def lakehouse(status: str | None = "Success", endpoint: str = EP) -> dict:
    props: dict = {"id": endpoint, "connectionString": "x.datawarehouse.fabric.microsoft.com"}
    if status is not None:
        props["provisioningStatus"] = status
    return {"displayName": "LH_Sales", "properties": {"sqlEndpointProperties": props}}


def warehouse(collation: str | None) -> dict:
    props: dict = {"connectionString": "x.datawarehouse.fabric.microsoft.com", "description": SQL_TEXT}
    if collation is not None:
        props["collationType"] = collation
    return {"displayName": "ignored", "description": SQL_TEXT, "properties": props}


def run(items: list[dict] | None, *, lakehouses: dict | None = None, warehouses: dict | None = None) -> dict:
    records = (adapters.workspace(ws(), items, collected_at=AT)
               + cw.evidence(ws(), items, lakehouses or {}, warehouses or {}, collected_at=AT))
    return assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})


def outcome_of(snap: dict, cid: str, subject_id: str | None = None) -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid and (subject_id is None or o["subject"]["id"] == subject_id)]
    assert len(got) == 1, got
    return got[0]


def test_the_controls_are_registered_complete_and_justify_their_severity():
    r = catalog.registry()
    assert 2 <= len(cw.CONTROLS) <= 4
    for c in cw.CONTROLS:
        assert r.get(c.id) is c and ct.problems(c) == []
        assert c.id.startswith("FEA-WH-") and c.scope == "item" and set(c.item_types) <= {"Warehouse", "SQLEndpoint"}
        assert any(ref["url"].startswith("https://learn.microsoft.com/") for ref in c.references)
        assert f"{c.severity.capitalize()}:" in (c.evaluate.__doc__ or "")
    assert {"Warehouse", "SQLEndpoint"} <= r.item_types()


# ---- FEA-WH-001: SQL analytics endpoint provisioned


def endpoint_items() -> list[dict]:
    return [item(LH, "Lakehouse", "LH_Sales"), item(EP, "SQLEndpoint", "LH_Sales")]


@pytest.mark.parametrize("status, outcome", [("Success", "pass"), ("Failed", "fail"), ("InProgress", "not_evaluated"),
                                             (None, "not_evaluated")])
def test_the_endpoint_passes_only_when_provisioned(status, outcome):
    o = outcome_of(run(endpoint_items(), lakehouses={LH: lakehouse(status)}), "FEA-WH-001", EP)
    assert o["outcome"] == outcome
    if outcome == "fail":
        assert o["basis"] == "verified_configuration" and "LH_Sales" in o["text"]


def test_an_endpoint_whose_lakehouse_wasnt_read_or_isnt_named_is_not_evaluated():
    assert outcome_of(run(endpoint_items()), "FEA-WH-001", EP)["outcome"] == "not_evaluated"
    other = run(endpoint_items(), lakehouses={LH: lakehouse(endpoint="9d9d9d9d-0000-4000-8000-000000000001")})
    assert outcome_of(other, "FEA-WH-001", EP)["outcome"] == "not_evaluated"
    cov = {c["item"]["id"]: c["state"] for c in run(endpoint_items())["item_coverage"]}
    assert cov[EP] == "not_inspected"


# ---- FEA-WH-002: one collation per workspace


def wh_items(n: int) -> list[dict]:
    return [item(wh_id(i), "Warehouse", f"WH_{i}") for i in range(n)]


def test_one_warehouse_or_matching_ones_pass_and_a_different_one_fails():
    one = run(wh_items(1), warehouses={wh_id(0): warehouse(BIN2)})
    assert outcome_of(one, "FEA-WH-002", wh_id(0))["outcome"] == "pass"
    same = run(wh_items(2), warehouses={wh_id(0): warehouse(BIN2), wh_id(1): warehouse(BIN2.lower())})
    assert {outcome_of(same, "FEA-WH-002", wh_id(i))["outcome"] for i in range(2)} == {"pass"}
    mixed = run(wh_items(2), warehouses={wh_id(0): warehouse(BIN2), wh_id(1): warehouse(CI)})
    bad = outcome_of(mixed, "FEA-WH-002", wh_id(0))
    assert bad["outcome"] == "fail" and bad["basis"] == "verified_configuration"
    assert f"WH_1 ({CI})" in bad["text"] and bad["detail"] == {"collation": BIN2, "different": 1}


def test_a_warehouse_not_read_or_without_a_collation_is_not_evaluated_and_never_passes_the_others():
    unread = run(wh_items(2), warehouses={wh_id(0): warehouse(BIN2)})
    assert outcome_of(unread, "FEA-WH-002", wh_id(1))["outcome"] == "not_evaluated"
    o = outcome_of(unread, "FEA-WH-002", wh_id(0))
    assert o["outcome"] == "not_evaluated" and "1 other warehouse" in o["text"]
    silent = run(wh_items(2), warehouses={wh_id(0): warehouse(None), wh_id(1): warehouse(BIN2)})
    assert outcome_of(silent, "FEA-WH-002", wh_id(0))["outcome"] == "not_evaluated"
    assert outcome_of(silent, "FEA-WH-002", wh_id(1))["outcome"] == "not_evaluated"
    # a mismatch already read still fails, even with another warehouse unread
    part = run(wh_items(3), warehouses={wh_id(0): warehouse(BIN2), wh_id(1): warehouse(CI)})
    assert outcome_of(part, "FEA-WH-002", wh_id(0))["outcome"] == "fail"
    assert outcome_of(run(wh_items(1)), "FEA-WH-002", wh_id(0))["outcome"] == "not_evaluated"


def test_no_warehouses_means_not_applicable():
    snap = run([item(LH, "Lakehouse")])
    assert outcome_of(snap, "FEA-WH-002")["outcome"] == "not_applicable"
    assert outcome_of(snap, "FEA-WH-001")["outcome"] == "not_applicable"


def test_evidence_keeps_names_and_counts_never_sql_or_connections():
    long_name = "W" * 5000
    items = endpoint_items() + [item(wh_id(i), "Warehouse", long_name if i == 0 else f"WH_{i}") for i in range(60)]
    whs = {wh_id(i): warehouse(BIN2 if i else CI) for i in range(60)}
    snap = run(items, lakehouses={LH: lakehouse()}, warehouses=whs)
    text = json.dumps(snap)
    for leak in LEAKS:
        assert leak not in text, leak
    listing = next(e for e in snap["evidence"] if e["kind"] == cw.WAREHOUSES)["payload"]
    assert listing["count"] == 60 and listing["read"] == 60 and len(listing["warehouses"]) == cw.MAX_LIST
    named = [e["subject"]["name"] for e in snap["evidence"] if e["kind"] == cw.WAREHOUSE]
    assert max(len(n) for n in named) == cw.MAX_NAME
    for e in snap["evidence"]:
        if e["kind"] == cw.WAREHOUSE:
            assert set(e["payload"]) == {"collation"}
        if e["kind"] == cw.ENDPOINT:
            assert set(e["payload"]) == {"lakehouse_id", "lakehouse", "provisioning_status"}


# ---- the engine call


class FakeFabric:
    """The client the assessment already uses (self.fabric). It must never read a warehouse."""

    def __init__(self, warehouses: int = 2):
        self.calls: list[tuple] = []
        self.n = warehouses

    def get_workspace(self, w):
        self.calls.append(("get_workspace", w))
        return {"id": w, "displayName": "Sales", "description": "d"}

    def list_items(self, w):
        self.calls.append(("list_items", w))
        return endpoint_items() + wh_items(self.n)

    def list_role_assignments(self, w):
        self.calls.append(("list_role_assignments", w))
        return []

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        return []

    def get_lakehouse(self, w, lh):
        self.calls.append(("get_lakehouse", w, lh))
        return lakehouse("Failed")

    def get_warehouse(self, w, wh):
        raise AssertionError("a warehouse is read only with a token the app already holds")


class SilentFabric:
    """The FabricClient _silent_client builds: it records the token it was given."""

    made: list["SilentFabric"] = []
    fail: set[str] = set()

    def __init__(self, token):
        self.token = token()
        self.calls: list[tuple] = []
        SilentFabric.made.append(self)

    def get_warehouse(self, w, wh):
        self.calls.append(("get_warehouse", w, wh))
        if wh in SilentFabric.fail:
            raise RuntimeError("403 Forbidden token=abc.def.ghi")
        return warehouse(BIN2)


class FakeAuth:
    def __init__(self, token: str | None):
        self.token, self.silent = token, []

    def silent_token(self, scopes):
        self.silent.append(list(scopes))
        return self.token

    def _token(self, *a, **k):
        raise AssertionError("an assessment never asks to sign in")

    def __getattr__(self, name):
        raise AttributeError(name)


@pytest.fixture
def make(tmp_path, monkeypatch):
    def build(token: str | None = "silent-token", warehouses: int = 2, fail: set[str] | None = None):
        fake = FakeFabric(warehouses)
        SilentFabric.made, SilentFabric.fail = [], fail or set()
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        monkeypatch.setattr(estate_service, "FabricClient", SilentFabric)
        auth = FakeAuth(token)
        monkeypatch.setattr(Service, "auth", property(lambda self: auth))
        s = Service(root=tmp_path / "app")
        s.fake = fake
        return s
    return build


def reads_of(r: dict) -> dict:
    return next(x for x in r["reads"] if x["read"] == "warehouse properties")


def test_the_call_reads_warehouses_with_a_silent_token_only(make):
    s = make()
    r = s.excellence_assess(workspace_id=WSID)
    assert s.auth.silent == [FABRIC_SCOPES] and len(SilentFabric.made) == 1
    assert SilentFabric.made[0].token == "silent-token"
    assert [c[0] for c in SilentFabric.made[0].calls] == ["get_warehouse", "get_warehouse"]
    assert {outcome_of(r, "FEA-WH-002", wh_id(i))["outcome"] for i in range(2)} == {"pass"}
    assert outcome_of(r, "FEA-WH-001", EP)["outcome"] == "fail"  # from the lakehouse read the assessment already makes
    assert reads_of(r)["ok"] is True and reads_of(r)["count"] == 2
    assert not [c for c in s.fake.calls if c[0] == "get_warehouse"]
    text = json.dumps(r)
    assert all(leak not in text for leak in LEAKS)


def test_without_a_silent_token_nothing_is_read_or_asked_and_the_controls_are_not_evaluated(make):
    s = make(token=None)
    r = s.excellence_assess(workspace_id=WSID)
    assert SilentFabric.made == [] and s.auth.silent == [FABRIC_SCOPES]
    got = reads_of(r)
    assert got["ok"] is False and "without asking" in got["problem"]
    assert {outcome_of(r, "FEA-WH-002", wh_id(i))["outcome"] for i in range(2)} == {"not_evaluated"}


def test_no_warehouse_means_no_token_is_asked_for(make):
    s = make(warehouses=0)
    r = s.excellence_assess(workspace_id=WSID)
    assert s.auth.silent == [] and not [x for x in r["reads"] if x["read"] == "warehouse properties"]


def test_a_failed_warehouse_read_is_reported_and_leaves_its_controls_not_evaluated(make):
    s = make(fail={wh_id(1)})
    r = s.excellence_assess(workspace_id=WSID)
    got = reads_of(r)
    assert got["ok"] is False and "1 of 2 couldn't be read" in got["problem"] and "WH_1" in got["problem"]
    assert "abc.def.ghi" not in json.dumps(r)
    assert outcome_of(r, "FEA-WH-002", wh_id(1))["outcome"] == "not_evaluated"
    assert outcome_of(r, "FEA-WH-002", wh_id(0))["outcome"] == "not_evaluated"  # can't be compared with WH_1


def test_warehouse_reads_are_capped(make, monkeypatch):
    monkeypatch.setattr(excellence_service, "MAX_WAREHOUSE_READS", 2)
    s = make(warehouses=4)
    r = s.excellence_assess(workspace_id=WSID)
    assert len(SilentFabric.made[0].calls) == 2
    got = reads_of(r)
    assert got["ok"] is False and "2 past the first 2" in got["problem"] and got["count"] == 2
    for i in (2, 3):
        assert outcome_of(r, "FEA-WH-002", wh_id(i))["outcome"] == "not_evaluated"


def test_a_warehouse_id_that_isnt_a_guid_is_never_read(make):
    s = make(warehouses=0)
    s.fake.list_items = lambda w: [item("../../x", "Warehouse", "WH_bad")]
    r = s.excellence_assess(workspace_id=WSID)
    assert SilentFabric.made and SilentFabric.made[0].calls == []
    assert reads_of(r)["ok"] is False


def test_the_demo_decides_the_warehouse_controls_with_no_call(make):
    s = make()
    r = s.excellence_assess(demo=True)
    assert s.fake.calls == [] and SilentFabric.made == [] and s.auth.silent == [] and r["fixture"] is True
    decided = {(o["control_id"], o["subject"]["id"]): o["outcome"] for o in r["outcomes"]
               if o["control_id"] in WH_CONTROLS}
    assert set(decided.values()) == {"pass", "fail"}
    assert {k[1] for k in decided} == {i["id"] for i in excellence_service.FIXTURE_SQL_ITEMS}
    assert all(c["state"] == "inspected" for c in r["item_coverage"]
               if c["item"]["item_type"] in ("Warehouse", "SQLEndpoint"))
    assert all(f["fixture"] for f in r["findings"])
    assert assess.rescore(json.loads(json.dumps(r)), catalog.registry())["scores"] == r["scores"]


# ---- malformed answers from Fabric


@pytest.mark.parametrize("answer", [["x"], "text", 7, {"properties": "x"}, {"properties": ["x"]},
                                    {"properties": {"collationType": 7}}, {"properties": {"collationType": "  "}}])
def test_a_malformed_warehouse_answer_is_not_evaluated_not_a_crash(answer):
    snap = run(wh_items(2), warehouses={wh_id(0): answer, wh_id(1): warehouse(BIN2)})
    assert {outcome_of(snap, "FEA-WH-002", wh_id(i))["outcome"] for i in range(2)} == {"not_evaluated"}


@pytest.mark.parametrize("props", ["x", ["x"], 7])
def test_a_malformed_sql_endpoint_answer_is_not_evaluated_not_a_crash(props):
    got = {"displayName": "LH_Sales", "properties": {"sqlEndpointProperties": props}}
    assert outcome_of(run(endpoint_items(), lakehouses={LH: got}), "FEA-WH-001", EP)["outcome"] == "not_evaluated"
    for bad in (["x"], "text", {"properties": "x"}):
        assert cw.endpoint_properties(WSID, endpoint_items(), {LH: bad}, collected_at=AT) == []
