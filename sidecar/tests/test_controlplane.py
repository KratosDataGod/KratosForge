"""CP-001 to CP-003: the KratosForge Environment manifest, its store and the provider declarations."""

from __future__ import annotations

import json
import threading

import pytest

from fabric_desktop.controlplane import manifest as mf
from fabric_desktop.controlplane import providers as pv
from fabric_desktop.controlplane import store as st
from test_agent import FakeDfs

WS = "11111111-1111-4111-8111-111111111111"
LH = "22222222-2222-4222-8222-222222222222"
FN = "33333333-3333-4333-8333-333333333333"
DB = "44444444-4444-4444-8444-444444444444"
OID = "55555555-5555-4555-8555-555555555555"
AT = "2026-11-19T20:00:00Z"


def fabric(**extra):
    kwargs = dict(name="Contoso data platform", provider="fabric",
                  namespace={"workspace_id": WS, "display_name": "KratosForge"},
                  analytical_store={"workspace_id": WS, "lakehouse_id": LH, "display_name": "KratosForgeLakehouse"},
                  families={"fdopt_": 2, "fdacc_": 1}, owner_object_id=OID, services=["optimize", "savings"], at=AT)
    return mf.new_manifest(**(kwargs | extra))


# ---- CP-001 the manifest

def test_a_new_manifest_is_valid_and_has_a_random_id():
    a, b = fabric(), fabric()
    assert a["environment_id"] != b["environment_id"]
    assert a["environment_id"].startswith("kfenv_") and len(a["environment_id"]) == 38
    assert a["manifest_version"] == mf.MANIFEST_VERSION and a["operational_store"] is None
    assert a["analytical_store"]["kind"] == "fabric_lakehouse" and a["capabilities"] == ["analytical_store"]
    assert a["created_at"] == a["updated_at"] == AT


def test_an_operational_store_is_optional_and_checked():
    m = fabric(operational_store={"workspace_id": WS, "functions_id": FN, "database_id": DB,
                                  "display_name": "KratosForgeMetadata"}, operational_version=5)
    assert m["operational_store"]["kind"] == "fabric_sql_udf" and m["schema"]["operational"] == 5
    assert m["capabilities"] == ["analytical_store", "operational_store"]
    with pytest.raises(mf.ManifestError, match="functions_id"):
        fabric(operational_store={"workspace_id": WS})


def test_the_json_round_trips_byte_stable():
    m = fabric()
    data = mf.to_json(m)
    assert mf.from_json(data) == m and mf.to_json(mf.from_json(data)) == data


@pytest.mark.parametrize("field,value,message", [
    ("environment_id", "kfenv_ABC", "environment_id"),
    ("environment_id", "env_" + "0" * 32, "environment_id"),
    ("provider", "bigquery", "provider"),
    ("name", "", "name is required"),
    ("name", "two\nlines", "one line"),
    ("manifest_version", True, "manifest_version"),
    ("manifest_version", 0, "manifest_version"),
    ("created_at", "yesterday", "created_at"),
    ("services", ["optimize", "optimize"], "twice"),
    ("services", ["Bad Name"], "services"),
    ("deployments", {"staging": {"platform_workspace_id": WS}}, "deployments.staging"),
    ("deployments", {"prod": {"platform_workspace": "Fabric Desktop Platform - Prod"}}, "only ids"),
    ("schema", {"families": {"opt": 2}}, "prefix"),
    ("schema", {"families": {"fdopt_": 0}}, "fdopt_"),
    ("migration", {"state": "paused"}, "migration.state"),
    ("migration", {"state": "running", "by": "inst"}, "expires_at"),
    ("config_refs", {"policy": "Files/other/policy.json"}, "config_refs"),
    ("config_refs", {"policy": "Files/_kratosforge/../x"}, "config_refs"),
    ("compatibility", {"min_app_version": "latest"}, "compatibility"),
])
def test_each_rule_refuses(field, value, message):
    m = fabric() | {field: value}
    with pytest.raises(mf.ManifestError, match=message):
        mf.validate(m)


def test_resources_are_ids_never_names():
    for bad in ("FabricDesktop_Central", "", None):
        with pytest.raises(mf.ManifestError, match="GUID"):
            fabric(analytical_store={"workspace_id": WS, "lakehouse_id": bad})
    with pytest.raises(mf.ManifestError, match="namespace.workspace_id"):
        fabric(namespace={"workspace_id": "KratosForge"})
    # A GUID in upper case is still a GUID.
    assert fabric(analytical_store={"workspace_id": WS, "lakehouse_id": LH.upper()})["analytical_store"]["lakehouse_id"] == LH.upper()


@pytest.mark.parametrize("extra", [
    {"config_refs": {"token": "Files/_kratosforge/t"}},
    {"analytical_store": {"kind": "fabric_lakehouse", "workspace_id": WS, "lakehouse_id": LH, "sas": "x"}},
    {"namespace": {"workspace_id": WS, "clientSecret": "x"}},
    {"namespace": {"workspace_id": WS, "connection_string": "x"}},
    {"name": "Server=tcp:x;Password=hunter2"},
    {"x_note": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"},  # pragma: allowlist secret (a fake token)
])
def test_no_secrets_ever(extra):
    with pytest.raises(mf.ManifestError, match="credential"):
        mf.validate(fabric() | extra)


def test_the_owner_is_an_object_id_only():
    with pytest.raises(mf.ManifestError, match="object id only"):
        mf.validate(fabric() | {"owner": {"object_id": OID, "email": "someone@example.com"}})
    with pytest.raises(mf.ManifestError, match="owner.object_id"):
        mf.validate(fabric() | {"owner": {"object_id": "someone@example.com"}})
    assert "owner" not in fabric(owner_object_id="")


def test_unknown_keys_are_kept_on_update():
    m = fabric() | {"future_field": {"a": 1}}
    out = mf.updated(mf.validate(m), {"services": ["optimize", "savings", "incidents"]}, at="2026-11-20T00:00:00Z")
    assert out["future_field"] == {"a": 1} and out["updated_at"] == "2026-11-20T00:00:00Z"
    assert out["created_at"] == AT and "incidents" in out["services"]
    assert out is not m and m["services"] == ["optimize", "savings"]


@pytest.mark.parametrize("key", ["environment_id", "provider", "created_at", "manifest_version"])
def test_identity_fields_never_change(key):
    with pytest.raises(mf.ManifestError, match="can't change"):
        mf.updated(fabric(), {key: "x"})


def test_a_newer_manifest_reads_but_is_read_only():
    newer = {"manifest_version": mf.MANIFEST_VERSION + 1, "environment_id": "kfenv_" + "a" * 32, "name": "Later",
             "provider": "fabric", "namespace": "a shape this version doesn't know"}
    assert mf.validate(newer) is newer and not mf.writable(newer)
    with pytest.raises(mf.ManifestError, match="read-only"):
        mf.updated(newer, {"name": "x"})
    # Even a newer manifest never carries a secret.
    with pytest.raises(mf.ManifestError, match="credential"):
        mf.validate(newer | {"password": "x"})


def test_bad_json_is_refused_not_guessed():
    for data in (b"{", b"\xff", b"[]", b'"x"'):
        with pytest.raises(mf.ManifestError):
            mf.from_json(data)


def test_summary_holds_ids_and_labels_only():
    s = mf.summary(fabric())
    assert set(s) == {"environment_id", "name", "provider", "writable", "workspace_id", "lakehouse_id",
                      "lakehouse_name", "has_operational_store", "operational_name", "families", "updated_at"}
    assert s["lakehouse_name"] == "KratosForgeLakehouse" and s["families"] == {"fdopt_": 2, "fdacc_": 1}


# ---- CP-002 the store

def test_create_once_then_connect(tmp_path):
    store = st.local_store(tmp_path)
    assert store.read() is None
    made = store.create(fabric())
    assert store.read() == made
    with pytest.raises(st.ManifestExists, match="Connect to it"):
        store.create(fabric())  # a second installation never makes a second environment
    assert (tmp_path / "Files" / "_kratosforge" / "environment.json").exists()


def test_a_stale_replace_is_refused(tmp_path):
    store = st.local_store(tmp_path)
    first = store.create(fabric())
    mine = store.replace(first, mf.updated(first.manifest, {"name": "Renamed"}))
    with pytest.raises(st.ManifestChanged, match="Read it again"):
        store.replace(first, mf.updated(first.manifest, {"name": "Lost update"}))
    assert store.read().manifest["name"] == "Renamed" == mine.manifest["name"]


def test_only_one_of_two_concurrent_creates_wins(tmp_path):
    store = st.local_store(tmp_path)
    results, start = [], threading.Barrier(8)

    def go():
        start.wait()
        try:
            results.append(store.create(fabric()).manifest["environment_id"])
        except st.ManifestExists:
            results.append(None)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    won = [r for r in results if r]
    assert len(won) == 1 and store.read().manifest["environment_id"] == won[0]


def test_another_environment_cant_replace_this_one(tmp_path):
    store = st.local_store(tmp_path)
    first = store.create(fabric())
    with pytest.raises(mf.ManifestError, match="another environment"):
        store.replace(first, fabric())


def test_a_broken_manifest_is_shown_not_replaced(tmp_path):
    store = st.local_store(tmp_path)
    store.backend.create(st.NAME, json.dumps({"manifest_version": 1, "environment_id": "nope"}).encode())
    with pytest.raises(mf.ManifestError):
        store.read()
    with pytest.raises(st.ManifestExists):
        store.create(fabric())


def test_onelake_writes_are_conditional_and_in_the_reserved_folder():
    dfs = StreamingDfs()
    store = st.onelake_store(WS, LH, lambda: "tok", session=dfs)
    made = store.create(fabric())
    url = f"https://onelake.dfs.fabric.microsoft.com/{WS}/{LH}/Files/_kratosforge/environment.json"
    assert url in dfs.files and ("PUT", "*") in dfs.calls
    assert store.read().manifest == made.manifest
    with pytest.raises(st.ManifestExists):
        store.create(fabric())
    with pytest.raises(st.ManifestChanged):
        store.replace(st.Read(made.manifest, '"stale"'), mf.updated(made.manifest, {"name": "x"}))


def test_the_cache_works_offline_and_forgetting_touches_nothing(tmp_path):
    cache = st.Cache(tmp_path / "controlplane")
    assert cache.active() is None and cache.all() == []
    a, b = fabric(name="A"), fabric(name="B")
    cache.remember(a)
    cache.remember(b, activate=False)
    assert cache.active()["environment_id"] == a["environment_id"] and [e["name"] for e in cache.all()] == ["A", "B"]
    cache.activate(b["environment_id"])
    assert cache.active()["name"] == "B"
    assert cache.forget(b["environment_id"]) and cache.active() is None and not cache.forget(b["environment_id"])
    with pytest.raises(KeyError):
        cache.activate(b["environment_id"])
    assert "tok" not in (tmp_path / "controlplane" / "environments.json").read_text()


def test_an_unreadable_cache_starts_empty(tmp_path):
    folder = tmp_path / "controlplane"
    folder.mkdir()
    (folder / "environments.json").write_text("[1, 2]")
    assert st.Cache(folder).all() == []


# ---- CP-003 providers

def test_databricks_and_snowflake_claim_nothing_and_are_not_offered():
    for pid in ("databricks", "snowflake"):
        d = pv.REGISTRY[pid].describe()
        assert d["status"] == "Not available yet" and not d["available"] and d["planned"]
        assert all(c["level"] == "unsupported" for c in d["capabilities"].values())


def test_fabric_is_preview_and_not_offered_until_provisioning_and_health_are_built():
    d = pv.REGISTRY["fabric"].describe()
    assert d["preview"] and not d["available"]
    assert d["capabilities"]["discovery"]["level"] == "supported"
    assert d["capabilities"]["provisioning"]["level"] == "unsupported"
    assert d["capabilities"]["operational_store"]["level"] == "partial"


def test_a_claim_without_evidence_or_adapter_is_refused():
    with pytest.raises(ValueError, match="needs evidence"):
        pv.Capability("supported")
    with pytest.raises(ValueError, match="what's missing"):
        pv.Capability("partial", "unit_tests")
    with pytest.raises(ValueError, match="no adapter"):
        pv.ProviderDescriptor("x", "X", adapter=False, capabilities={"manifest": pv.Capability("supported", "unit_tests")},
                              planned="later")
    with pytest.raises(ValueError, match="planned"):
        pv.ProviderDescriptor("x", "X", adapter=False)
    with pytest.raises(ValueError, match="unknown"):
        pv.ProviderDescriptor("x", "X", adapter=True, capabilities={"teleport": pv.Capability()})


def test_available_needs_every_required_capability_and_live_evidence_lifts_preview():
    tested = {k: pv.Capability("supported", "unit_tests") for k in pv.REQUIRED}
    p = pv.ProviderDescriptor("x", "X", adapter=True, capabilities=tested)
    assert p.available and p.preview and p.describe()["status"] == "Preview"
    live = pv.ProviderDescriptor("x", "X", adapter=True,
                                 capabilities={k: pv.Capability("supported", "live_verified") for k in pv.REQUIRED})
    assert live.describe()["status"] == "Available"
    missing = dict(tested)
    missing.pop("health")
    assert not pv.ProviderDescriptor("x", "X", adapter=True, capabilities=missing).available


def test_every_registered_provider_is_listed_and_the_manifest_knows_it():
    assert {d["id"] for d in pv.describe_all()} == set(pv.REGISTRY) == set(mf.PROVIDERS)


def test_the_contracts_are_small_protocols():
    class Calls:
        def call(self, operation, **kwargs):
            return operation

    assert isinstance(Calls(), pv.OperationalMetadataProvider)
    assert not isinstance(object(), pv.PlatformProvisioning)
    plan = pv.ProvisionPlan("fabric", (pv.PlanStep("find", "KratosForge", False),
                                       pv.PlanStep("refuse", "fdopt_", False, blocked="newer schema")))
    assert plan.blocked == ["newer schema"]


# ---- CP-004 Fabric discovery

class FakeFabric:
    """Fabric's list calls only. Anything that would change Fabric fails the test."""

    def __init__(self, lakehouses):
        self.lakehouses = lakehouses

    def list_workspaces(self):
        return [{"id": WS, "displayName": "zeta"}, {"id": "w2", "displayName": "Alpha"}]

    def list_lakehouses(self, workspace_id):
        return self.lakehouses

    def __getattr__(self, name):
        raise AssertionError(f"discovery must not call {name}")


def discovery(tmp_path, lakehouses, tables=None, broken=()):
    from fabric_desktop.controlplane.fabric import FabricDiscovery

    def manifest_store(ws, lh):
        if lh in broken:
            raise PermissionError("403 Forbidden")
        return st.local_store(tmp_path / lh)

    return FabricDiscovery(FakeFabric(lakehouses), manifest_store, lambda ws, lh: (tables or {}).get(lh, []))


def test_discovery_finds_by_manifest_not_by_name(tmp_path):
    env = fabric(name="Ours")
    st.local_store(tmp_path / "lh-renamed").create(env)
    lakes = [{"id": "lh-renamed", "displayName": "Renamed by someone"},
             {"id": "lh-named", "displayName": "KratosForgeLakehouse"},
             {"id": "lh-legacy", "displayName": "FabricDesktop_Central"},
             {"id": "lh-denied", "displayName": "Secret"}]
    d = discovery(tmp_path, lakes, tables={"lh-legacy": ["fdopt_schema_version", "fdopt_findings"]},
                  broken={"lh-denied"})
    found = d.find_environments(WS)
    assert [(f.ref.lakehouse_id, f.state) for f in found] == [
        ("lh-renamed", "environment"), ("lh-legacy", "adoptable"), ("lh-denied", "unreadable")]
    assert found[0].manifest["environment_id"] == env["environment_id"] and found[2].reason == "No access"
    assert not d.truncated


def test_a_damaged_manifest_is_reported_not_trusted(tmp_path):
    st.local_store(tmp_path / "lh1").backend.create(st.NAME, b'{"manifest_version": 1}')
    (f,) = discovery(tmp_path, [{"id": "lh1", "displayName": "x"}]).find_environments(WS)
    assert f.state == "unreadable" and "damaged" in f.reason and f.manifest is None


def test_namespaces_are_sorted_and_discovery_is_bounded(tmp_path):
    from fabric_desktop.controlplane import fabric as fb
    d = discovery(tmp_path, [{"id": f"lh{i}", "displayName": f"L{i}"} for i in range(fb.MAX_LAKEHOUSES + 5)])
    assert [w["display_name"] for w in d.list_namespaces()] == ["Alpha", "zeta"]
    assert d.find_environments(WS) == [] and d.truncated
    assert isinstance(d, pv.EnvironmentDiscovery)


def test_the_secret_key_rule_allows_the_specs_own_names():
    m = fabric(deployments={"prod": {"platform_workspace_id": WS}, "keys": {"workspace_id": WS}})
    assert m["deployments"]["keys"] == {"workspace_id": WS}
    for name in ("api_key", "accountKey", "storage_key", "private_key"):
        with pytest.raises(mf.ManifestError, match="credential"):
            mf.validate(m | {"namespace": {"workspace_id": WS, name: "x"}})


@pytest.mark.parametrize("field,value", [
    ("environment_id", "kfenv_" + "a" * 32 + "\n"),
    ("created_at", AT + "\n"),
    ("services", ["optimize\n"]),
    ("schema", {"families": {"fdopt_\n": 2}}),
    ("compatibility", {"min_app_version": "0.20.0\n"}),
    ("config_refs", {"policy": "Files/_kratosforge/p\n"}),
])
def test_a_trailing_newline_is_refused(field, value):
    with pytest.raises(mf.ManifestError):
        mf.validate(fabric() | {field: value})
    with pytest.raises(mf.ManifestError, match="GUID"):
        fabric(namespace={"workspace_id": WS + "\n"})


def test_an_oversized_or_deeply_nested_manifest_is_refused():
    with pytest.raises(mf.ManifestError, match="larger"):
        mf.from_json(b" " * (mf.MAX_BYTES + 1))
    deep = ('{"a":' * 5000) + "1" + ("}" * 5000)
    with pytest.raises(mf.ManifestError):
        mf.from_json(deep[: mf.MAX_BYTES])


@pytest.mark.parametrize("name,refused", [
    ("tokens", True), ("Secrets", True), ("api_key", True), ("primaryKey", True), ("storage_key", True),
    ("refresh-token", True), ("keys", False), ("monkey", False), ("turnkey_note", False), ("lakehouse_id", False),
    ("keyvault_name", False),
])
def test_the_secret_key_rule_is_by_word(name, refused):
    m = fabric() | {"namespace": {"workspace_id": WS, name: "x"}}
    if refused:
        with pytest.raises(mf.ManifestError, match="credential"):
            mf.validate(m)
    else:
        mf.validate(m)


def test_a_lakehouse_with_only_unrelated_tables_is_left_out(tmp_path):
    d = discovery(tmp_path, [{"id": "lh1", "displayName": "Sales"}, {"id": "lh2", "displayName": "Access"}],
                  tables={"lh1": ["customer", "fact_sales"], "lh2": ["fdacc_scans"]})
    assert d.find_environments(WS) == []


class StreamingDfs(FakeDfs):
    """The fake DFS, answering reads as a stream the way the store asks for them."""

    def get(self, url, headers=None, timeout=None, stream=False):
        resp = super().get(url, headers, timeout)
        resp._content_consumed = True  # iter_content then serves the body held in memory
        return resp


def test_onelake_read_is_bounded():
    dfs = StreamingDfs()
    store = st.onelake_store(WS, LH, lambda: "tok", session=dfs)
    store.create(fabric())
    url = next(iter(dfs.files))
    dfs.files[url] = (b"{" + b" " * mf.MAX_BYTES + b"}", dfs.files[url][1])
    with pytest.raises(mf.ManifestError, match="larger"):
        store.read()


def test_a_huge_manifest_shows_as_damaged_in_discovery(tmp_path):
    from fabric_desktop.controlplane.fabric import FabricDiscovery

    dfs = StreamingDfs()
    url = f"https://onelake.dfs.fabric.microsoft.com/{WS}/{LH}/Files/_kratosforge/environment.json"
    dfs.files[url] = (b"[" * (mf.MAX_BYTES + 10), '"1"')
    d = FabricDiscovery(FakeFabric([{"id": LH, "displayName": "x"}]),
                        lambda ws, lh: st.onelake_store(ws, lh, lambda: "tok", session=dfs), lambda ws, lh: [])
    (f,) = d.find_environments(WS)
    assert f.state == "unreadable" and "damaged" in f.reason


def test_deep_nesting_is_a_manifest_error_not_a_crash():
    with pytest.raises(mf.ManifestError, match="nested"):
        mf.from_json("[" * 30000 + "]" * 30000)
    nested: dict = {}
    cur = nested
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    with pytest.raises(mf.ManifestError):
        mf.validate(fabric() | {"x_extra": nested})


def test_the_streamed_read_stops_past_the_cap_without_content_length():
    dfs = StreamingDfs()
    backend = st.OneLakeManifestBackend(WS, LH, lambda: "tok", session=dfs)
    url = backend._url(st.NAME)
    dfs.files[url] = (b"x" * (mf.MAX_BYTES + 1), '"1"')
    with pytest.raises(mf.ManifestError, match="larger"):
        backend.read(st.NAME)  # the backend itself refuses, before from_json sees anything
    dfs.files[url] = (b"x" * mf.MAX_BYTES, '"2"')
    assert len(backend.read(st.NAME).data) == mf.MAX_BYTES
# ---- CP-005 Fabric provisioning

class ProvFabric:
    """Fabric for provisioning: records every create, so a test can say exactly what was made."""

    def __init__(self, workspaces=(), lakehouses=()):
        self.workspaces = [dict(w) for w in workspaces]
        self.lakehouses = {w["id"]: [] for w in self.workspaces} | {k: list(v) for k, v in dict(lakehouses).items()}
        self.created = []

    def list_workspaces(self):
        return list(self.workspaces)

    def find_lakehouse(self, ws, name):
        return next((lh for lh in self.lakehouses.get(ws, []) if lh["displayName"] == name), None)

    def create_workspace(self, name, capacity_id="", description=""):
        ws = {"id": "66666666-6666-4666-8666-666666666666", "displayName": name}
        self.workspaces.append(ws)
        self.lakehouses[ws["id"]] = []
        self.created.append(("workspace", name, capacity_id))
        return ws

    def create_lakehouse(self, ws, name, description=""):
        lh = {"id": "77777777-7777-4777-8777-777777777777", "displayName": name}
        self.lakehouses[ws].append(lh)
        self.created.append(("lakehouse", name))
        return lh


def provisioning(tmp_path, fab, families=None):
    from fabric_desktop.controlplane.provisioning import FabricProvisioning
    from fabric_desktop.optimize.installation import Installation
    from fabric_desktop.optimize.lakehouse import LocalLakehouse
    from fabric_desktop.optimize import schema

    lakes = {}

    def open_lake(ws, lh, schemas):
        return lakes.setdefault(lh, LocalLakehouse(tmp_path / "lakes" / lh))

    prov = FabricProvisioning(fab, open_lake, lambda ws, lh: st.local_store(tmp_path / "lakes" / lh),
                              Installation(tmp_path / "pc" / "optimize"), families or [schema.OPTIMIZE])
    return prov, lakes


def test_plan_reads_only_and_says_what_it_costs(tmp_path):
    fab = ProvFabric()
    prov, lakes = provisioning(tmp_path, fab)
    plan = prov.plan({"name": "Ours", "capacity_id": "cap"})
    assert fab.created == [] and lakes == {}
    assert [(s.action, s.target) for s in plan.steps] == [
        ("create", "workspace KratosForge"), ("create", "lakehouse KratosForgeLakehouse"),
        ("initialize", "Optimize tables (fdopt_)"), ("write_manifest", "Files/_kratosforge/environment.json")]
    assert plan.steps[0].cost and plan.steps[0].permission and not plan.blocked


def test_apply_without_confirm_creates_nothing(tmp_path):
    fab = ProvFabric()
    prov, lakes = provisioning(tmp_path, fab)
    with pytest.raises(pv.NotConfirmed):
        prov.apply(prov.plan({"name": "Ours", "capacity_id": "cap"}), confirmed=False)
    assert fab.created == [] and lakes == {}


def test_create_makes_each_thing_once_and_writes_the_manifest_last(tmp_path):
    from fabric_desktop.optimize import setup
    fab = ProvFabric()
    prov, lakes = provisioning(tmp_path, fab)
    out = prov.apply(prov.plan({"name": "Ours", "capacity_id": "cap", "owner_object_id": OID}), confirmed=True)
    assert out["ok"] and fab.created == [("workspace", "KratosForge", "cap"), ("lakehouse", "KratosForgeLakehouse")]
    m = out["manifest"]
    assert m["analytical_store"]["lakehouse_id"] == out["lakehouse_id"] and m["schema"]["families"]["fdopt_"] >= 2
    assert m["owner"] == {"object_id": OID} and [s["step"] for s in out["steps"]][-1] == "Manifest"
    assert setup.inspect(lakes[out["lakehouse_id"]])["state"] == setup.READY
    # A second PC asking for the same thing is told the names are taken: it connects instead.
    again = prov.plan({"name": "Ours", "capacity_id": "cap"})
    assert "already exists" in again.blocked[0]
    picked = prov.plan({"name": "Ours", "workspace_id": out["workspace_id"], "lakehouse_id": out["lakehouse_id"]})
    assert picked.blocked == ["This lakehouse already holds a KratosForge Environment. Connect to it instead."]
    with pytest.raises(PermissionError, match="Connect to it"):
        prov.apply(picked, confirmed=True)
    assert len(fab.created) == 2


def test_a_blocked_plan_never_runs_and_says_why(tmp_path):
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "KratosForge"}])
    prov, _ = provisioning(tmp_path, fab)
    for request, why in [({"capacity_id": "cap"}, "name"), ({"name": "x"}, "already exists"),
                         ({"name": "x", "workspace_name": "Other"}, "capacity"),
                         ({"name": "x", "workspace_id": "nope"}, "can open"),
                         ({"name": "x", "workspace_name": "Other", "capacity_id": "c", "lakehouse_id": LH}, "existing workspace")]:
        plan = prov.plan(request)
        assert any(why in b for b in plan.blocked), (request, plan.blocked)
        with pytest.raises(PermissionError):
            prov.apply(plan, confirmed=True)
    assert fab.created == []


def test_an_existing_central_lakehouse_is_upgraded_in_place_and_newer_is_refused(tmp_path):
    from fabric_desktop.optimize import setup
    from fabric_desktop.optimize.installation import Installation
    fab = ProvFabric(workspaces=[{"id": WS, "displayName": "Data"}], lakehouses={WS: [{"id": LH, "displayName": "FabricDesktop_Central"}]})
    prov, lakes = provisioning(tmp_path, fab)
    lake = prov._open_lake(WS, LH, None)
    setup.initialize(lake, Installation(tmp_path / "other" / "optimize"), approve=True)
    plan = prov.plan({"name": "Ours", "workspace_id": WS, "lakehouse_id": LH})
    assert [s.action for s in plan.steps] == ["find", "find", "find", "write_manifest"] and not plan.blocked
    out = prov.apply(plan, confirmed=True)
    assert out["ok"] and fab.created == [] and out["manifest"]["analytical_store"]["lakehouse_id"] == LH
    # A lakehouse a newer app upgraded is read-only here: the plan is blocked, nothing is initialized.
    other = "88888888-8888-4888-8888-888888888888"
    newer = prov._open_lake(WS, other, None)
    setup.initialize(newer, Installation(tmp_path / "o2" / "optimize"), approve=True)
    setup.write(newer, Installation(tmp_path / "o2" / "optimize"), "schema_version",
                [{"record_id": "v99", "version": 99, "description": "later", "applied_at": AT}])
    blocked = prov.plan({"name": "Ours", "workspace_id": WS, "lakehouse_id": other})
    assert blocked.blocked and "newer" in blocked.blocked[0].lower()


def test_a_failed_step_stops_the_rest_and_a_new_plan_finishes_it(tmp_path):
    fab = ProvFabric()
    prov, lakes = provisioning(tmp_path, fab)

    def fails(*a, **k):
        raise RuntimeError("capacity paused")

    fab.create_lakehouse, real = fails, fab.create_lakehouse
    out = prov.apply(prov.plan({"name": "Ours", "capacity_id": "cap"}), confirmed=True)
    assert not out["ok"] and out["steps"][-1]["status"] == "failed" and "capacity paused" in out["steps"][-1]["detail"]
    assert out["manifest"] is None and lakes == {}
    fab.create_lakehouse = real
    done = prov.apply(prov.plan({"name": "Ours", "workspace_id": out["workspace_id"]}), confirmed=True)
    assert done["ok"] and [c[0] for c in fab.created] == ["workspace", "lakehouse"]
