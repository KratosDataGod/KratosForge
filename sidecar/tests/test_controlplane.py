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
    {"x_note": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"},
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
    dfs = FakeDfs()
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


def test_fabric_is_preview_and_not_offered_until_discovery_and_provisioning_are_built():
    d = pv.REGISTRY["fabric"].describe()
    assert d["preview"] and not d["available"]
    assert d["capabilities"]["discovery"]["level"] == "unsupported"
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
