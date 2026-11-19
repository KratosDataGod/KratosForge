"""Performance controls on table layout (FEA-204): pass, fail and not-evaluated fixtures for every
control; a table that wasn't read never lets a lakehouse pass; the engine call reads tables only
through the X-ray's reader and within its budget, and the demo calls nothing."""

from __future__ import annotations

import json

import pytest
from deltalake import DeltaTable, write_deltalake
import pyarrow as pa

from fabric_desktop import delta_profile as dp, excellence_service, maintenance
from fabric_desktop.excellence import adapters, assess, catalog, catalog_performance as perf, controls as ct
from fabric_desktop.excellence_service import FIXTURE_TABLES
from fabric_desktop.service import Service

AT = "2026-11-19T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
IDS = ("FEA-PERF-003", "FEA-PERF-004", "FEA-PERF-005")
MB = 1024 * 1024


def lakehouse(i: str = "lh0", name: str | None = None) -> dict:
    return {"id": i, "type": "Lakehouse", "displayName": name or i}


def table(name: str = "dbo.t", files: int = 4, avg_mb: float = 200, since_vacuum: int = 3,
          features: list[str] | None = None, vacuumed: bool = True) -> dict:
    avg = int(avg_mb * MB)
    return perf.table_facts(name, {"exists": True, "files": files, "bytes": files * avg, "avg_file_bytes": avg,
                                   "versions_since_vacuum": since_vacuum,
                                   "last_vacuum": AT if vacuumed else None}, features or [])


def unread(name: str = "dbo.gone") -> dict:
    return perf.table_facts(name, None, None, error="403 Forbidden")


def run(read: dict, items: list[dict] | None = None) -> dict:
    items = items if items is not None else [lakehouse()]
    ws = {"id": WSID, "displayName": "Sales", "description": "d"}
    records = adapters.workspace(ws, items, collected_at=AT) + perf.table_layout(WSID, items, read, collected_at=AT)
    return assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})


def outcome_of(snap: dict, cid: str, subject_id: str = "lh0") -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid and o["subject"]["id"] == subject_id]
    assert len(got) == 1, got
    return got[0]


def test_performance_controls_are_registered_and_complete():
    r = catalog.registry()
    ids = [c.id for c in r.controls("performance")]
    assert ids == ["FEA-PERF-001", "FEA-PERF-002", *IDS]
    for cid in IDS:
        c = r.get(cid)
        assert ct.problems(c) == [] and c.item_types == ("Lakehouse",) and c.requirement == "kratosforge_recommendation"
        assert any(ref["url"].startswith("https://learn.microsoft.com/") for ref in c.references)
        assert "severity " + c.severity in (c.evaluate.__doc__ or "").lower()
        assert "saved" not in c.remediation["benefit"].lower().replace("not money saved", "")


def test_the_evidence_is_measured_and_keeps_only_numbers():
    recs = perf.table_layout(WSID, [lakehouse()], {"lh0": {"tables": [table()]}}, collected_at=AT)
    assert [r["confidence"] for r in recs] == ["measured"] and recs[0]["kind"] == "lakehouse.tables"
    assert set(recs[0]["payload"]["tables"][0]) == {"name", "read", "delta", "files", "bytes", "avg_file_bytes",
                                                    "versions_since_vacuum", "last_vacuum", "features"}


def test_small_files_pass_fail_and_not_evaluated():
    assert outcome_of(run({"lh0": {"tables": [table(files=500, avg_mb=200)]}}), "FEA-PERF-003")["outcome"] == "pass"
    # Under 20 files is fine however small they are.
    assert outcome_of(run({"lh0": {"tables": [table(files=19, avg_mb=0.1)]}}), "FEA-PERF-003")["outcome"] == "pass"
    o = outcome_of(run({"lh0": {"tables": [table("dbo.raw", files=1840, avg_mb=0.6), table()]}}), "FEA-PERF-003")
    assert o["outcome"] == "fail" and "dbo.raw (1,840 files averaging 0.6 MB)" in o["text"] and o["basis"] == "measured"
    o = outcome_of(run({"lh0": {"tables": [table(), unread()]}}), "FEA-PERF-003")
    assert o["outcome"] == "not_evaluated" and "dbo.gone" in o["text"] and "dbo.gone" in o["needs"]


def test_vacuum_pass_fail_and_not_evaluated():
    assert outcome_of(run({"lh0": {"tables": [table(since_vacuum=29)]}}), "FEA-PERF-004")["outcome"] == "pass"
    o = outcome_of(run({"lh0": {"tables": [table("dbo.raw", since_vacuum=212, vacuumed=False)]}}), "FEA-PERF-004")
    assert o["outcome"] == "fail" and "212 versions, never vacuumed" in o["text"]
    o = outcome_of(run({"lh0": {"tables": [table()], "past_budget": 3}}), "FEA-PERF-004")
    assert o["outcome"] == "not_evaluated" and "3 tables past" in o["text"]


def test_delta_features_pass_fail_and_not_evaluated():
    # Deletion vectors and column mapping by name: the SQL endpoint and Direct Lake read both.
    ok = run({"lh0": {"tables": [table(features=[dp.DELETION_VECTORS, dp.CM_NAME])]}})
    assert outcome_of(ok, "FEA-PERF-005")["outcome"] == "pass"
    o = outcome_of(run({"lh0": {"tables": [table("dbo.notes", features=[dp.CM_ID])]}}), "FEA-PERF-005")
    assert o["outcome"] == "fail" and "dbo.notes (column mapping by ID)" in o["text"]
    # No source says whether they read V2 checkpoints: not decided either way.
    o = outcome_of(run({"lh0": {"tables": [table(features=[dp.V2_CHECKPOINTS])]}}), "FEA-PERF-005")
    assert o["outcome"] == "not_evaluated" and "V2 checkpoints" in o["text"]
    assert outcome_of(run({"lh0": {"tables": [unread()]}}), "FEA-PERF-005")["outcome"] == "not_evaluated"


def test_a_failing_table_fails_even_when_another_was_not_read():
    snap = run({"lh0": {"tables": [table(files=900, avg_mb=1, since_vacuum=40, features=[dp.CM_ID]), unread()]}})
    assert {cid: outcome_of(snap, cid)["outcome"] for cid in IDS} == dict.fromkeys(IDS, "fail")


def test_missing_data_is_never_a_pass():
    # The Tables folder wasn't listed (or the lakehouse wasn't read): no evidence.
    snap = run({"lh0": None})
    for cid in IDS:
        o = outcome_of(snap, cid)
        assert o["outcome"] == "not_evaluated" and "lakehouse.tables" in o["needs"]
    snap = run({})
    assert {outcome_of(snap, cid)["outcome"] for cid in IDS} == {"not_evaluated"}
    # Every table's log failed: still not evaluated, never passed.
    snap = run({"lh0": {"tables": [unread("dbo.a"), unread("dbo.b")]}})
    assert {outcome_of(snap, cid)["outcome"] for cid in IDS} == {"not_evaluated"}


def test_no_tables_staging_and_folders_that_are_not_delta_do_not_apply():
    empty = run({"lh0": {"tables": []}})
    assert {outcome_of(empty, cid)["outcome"] for cid in IDS} == {"not_applicable"}
    staging = run({"lh0": {"tables": [], "staging": True}})
    assert {outcome_of(staging, cid)["outcome"] for cid in IDS} == {"not_applicable"}
    not_delta = perf.table_facts("dbo.csvs", {"exists": False}, [])
    snap = run({"lh0": {"tables": [not_delta]}})
    assert {outcome_of(snap, cid)["outcome"] for cid in IDS} == {"not_applicable"}
    snap = run({"lh0": {"tables": [not_delta, table()]}})
    assert {outcome_of(snap, cid)["outcome"] for cid in IDS} == {"pass"}


# ---- the read the controls rest on


def test_health_on_a_loaded_table_is_the_same_as_health_on_its_path(tmp_path):
    uri = str(tmp_path / "t")
    for _ in range(3):
        write_deltalake(uri, pa.table({"id": [1, 2]}), mode="append")
    loaded = DeltaTable(uri)
    assert maintenance.health(uri, table=loaded) == maintenance.health(uri)
    assert dp.features_used(loaded) == []


# ---- the engine call


class FakeFabric:
    def __init__(self, lakehouses: list[dict]):
        self.calls: list[tuple] = []
        self.lakehouses = lakehouses

    def get_workspace(self, w):
        self.calls.append(("get_workspace", w))
        return {"id": w, "displayName": "Sales", "description": "d"}

    def list_items(self, w):
        self.calls.append(("list_items", w))
        return self.lakehouses

    def list_role_assignments(self, w):
        self.calls.append(("list_role_assignments", w))
        return []

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        return []

    def get_lakehouse(self, w, lh):
        self.calls.append(("get_lakehouse", w, lh))
        return {"id": lh, "properties": {"defaultSchema": "dbo"}}


class FakeStore:
    """A lakehouse's Tables folder: lh0 has schema dbo with tables, lh1 has none, lh2 can't be listed."""

    def __init__(self, tables: int = 2, broken: set[str] | None = None):
        self.calls: list[tuple] = []
        self.tables, self.broken = tables, broken or set()

    def list(self, w, lh, directory):
        self.calls.append(("list", lh, directory))
        if lh == "lh2":
            raise RuntimeError("403 Forbidden token=abc.def.ghi")
        if lh == "lh1" or directory == "Tables/Files":
            return []
        if directory == "Tables":
            return [{"name": "dbo", "is_dir": True}]
        return [{"name": f"t{i}", "is_dir": True} for i in range(self.tables)]

    def read(self, w, lh, schema, name):
        self.calls.append(("read", lh, schema, name))
        if name in self.broken:
            raise RuntimeError("connection reset token=abc.def.ghi")
        return ({"exists": True, "files": 400, "bytes": 400 * MB, "avg_file_bytes": MB, "versions_since_vacuum": 2,
                 "last_vacuum": AT}, [])


@pytest.fixture
def make(tmp_path, monkeypatch):
    def build(items: list[dict], store: FakeStore | Exception):
        fake = FakeFabric(items)
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))

        def onelake(self):
            if isinstance(store, Exception):
                raise store
            return store
        monkeypatch.setattr(Service, "_excellence_onelake", onelake)
        s = Service(root=tmp_path / "app")
        s.fake = fake
        return s
    return build


def layout_read(r: dict) -> dict:
    return next(x for x in r["reads"] if x["read"] == "table layout")


def test_the_call_reads_tables_through_the_xray_reader_only(make):
    store = FakeStore(broken={"t1"})
    s = make([lakehouse("lh0"), lakehouse("lh1"), lakehouse("lh2", "LH_Locked")], store)
    r = s.excellence_assess(workspace_id=WSID)
    assert {c[0] for c in s.fake.calls} <= {"get_workspace", "list_items", "list_role_assignments", "list_capacities",
                                            "get_lakehouse"}
    assert {c[0] for c in store.calls} == {"list", "read"}
    assert outcome_of(r, "FEA-PERF-003", "lh0")["outcome"] == "fail"  # t0 has 400 files of 1 MB
    assert outcome_of(r, "FEA-PERF-004", "lh0")["outcome"] == "not_evaluated"  # t1 wasn't read
    assert outcome_of(r, "FEA-PERF-003", "lh1")["outcome"] == "not_applicable"  # no tables
    assert outcome_of(r, "FEA-PERF-003", "lh2")["outcome"] == "not_evaluated"  # not listed
    got = layout_read(r)
    assert got["ok"] is False and "LH_Locked" in got["problem"] and "1 of 2 table logs" in got["problem"]
    assert "abc.def.ghi" not in json.dumps(r)


def test_table_reads_are_bounded(make, monkeypatch):
    monkeypatch.setattr(excellence_service, "MAX_TABLE_READS", 3)
    store = FakeStore(tables=5)
    r = make([lakehouse("lh0")], store).excellence_assess(workspace_id=WSID)
    assert sum(1 for c in store.calls if c[0] == "read") == 3
    assert "2 tables past the first 3" in layout_read(r)["problem"]
    o = outcome_of(r, "FEA-PERF-004", "lh0")
    assert o["outcome"] == "not_evaluated" and "past the assessment's read budget" in o["text"]


def test_no_onelake_sign_in_leaves_the_controls_not_evaluated(make):
    s = make([lakehouse("lh0")], RuntimeError("Sign in again"))
    r = s.excellence_assess(workspace_id=WSID)
    assert layout_read(r)["ok"] is False and "Sign in again" in layout_read(r)["problem"]
    assert {outcome_of(r, cid, "lh0")["outcome"] for cid in IDS} == {"not_evaluated"}


def test_the_demo_shows_performance_without_calling_fabric_or_onelake(make):
    s = make([lakehouse("lh0")], AssertionError("the demo must not read OneLake"))
    r = s.excellence_assess(demo=True)
    assert s.fake.calls == [] and r["fixture"] is True
    assert not any(x["read"] == "table layout" for x in r["reads"])
    rules = {f["rule"].split("@")[0] for f in r["findings"]}
    assert set(IDS) <= rules and all(f["fixture"] for f in r["findings"])
    assert set(FIXTURE_TABLES) <= {o["subject"]["id"] for o in r["outcomes"] if o["control_id"] == "FEA-PERF-003"}
    again = assess.rescore(json.loads(json.dumps(r)), catalog.registry())
    assert again["scores"] == r["scores"]


class _NoPrompt:
    """An auth that has no OneLake token cached: a prompt would be ``storage_token``, which fails."""

    def silent_token(self, scopes):
        return None

    def storage_token(self):
        raise AssertionError("an assessment must never prompt for a sign-in")


def test_the_onelake_read_is_silent_and_never_prompts(tmp_path):
    s = Service(root=tmp_path / "app")
    s.__dict__["auth"] = _NoPrompt()
    with pytest.raises(PermissionError, match="no OneLake sign-in yet"):
        s._excellence_onelake()
