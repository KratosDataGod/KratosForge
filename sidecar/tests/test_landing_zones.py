"""One central landing workspace (Preview): zones in the catalog, setting it up, and where
[source] landing reads from once it's there. Fakes only: nothing here reaches Fabric."""

import sqlite3
from contextlib import closing

import pytest

from fabric_desktop.central import core
from fabric_desktop.central import setup as setup_mod
from fabric_desktop.central.client import FabricPlatform, LocalPlatform
from fabric_desktop.fabric_compute import bundle_files, run_bundle
from fabric_desktop.meta.engine import CheckStop, Engine
from fabric_desktop.meta.store import LANDING_LAKEHOUSE, LocalStore, OneLakeStore
from fabric_desktop.meta.templates import init_project
from fabric_desktop.service import Service

from test_meta import write

ADMIN = "chris@example.com"


@pytest.fixture
def platform(tmp_path):
    return LocalPlatform(tmp_path / "platform.sqlite", lambda: ADMIN)


def _as(platform, user):
    return LocalPlatform(platform.path, lambda: user)


def _audit(platform):
    return [(a["action"], a["target"]) for a in platform.call("audit", limit=50)]


# --------------------------------------------------------------------------- the catalog


def test_zones_are_saved_by_platform_admins_only_and_audited(platform):
    platform.call("save_domain", domain={"name": "HR"})
    zones = platform.call("save_landing_zone", zone={"name": "HR-Secure", "domain_id": "hr", "secure": True,
                                                     "readers": "HR Secure, People Analytics", "label": "Highly Confidential"})
    [z] = zones
    assert z["id"] == "hr_secure" and z["lakehouse_name"] == "LH_Landing_HR_Secure"
    assert z["secure"] and z["devtest"] == "none" and z["readers"] == ["HR Secure", "People Analytics"]
    assert z["label"] == "Highly Confidential" and z["domain_id"] == "hr" and z["sources"] == []
    assert ("save landing zone", "hr_secure") in _audit(platform)
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        _as(platform, "lee@example.com").call("save_landing_zone", zone={"name": "Finance"})
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        _as(platform, "lee@example.com").call("delete_landing_zone", id="hr_secure")
    with pytest.raises(core.PlatformError, match="doesn't exist"):
        platform.call("save_landing_zone", zone={"name": "Ops", "domain_id": "ops"})
    with pytest.raises(core.PlatformError, match="can't be secure"):
        platform.call("save_landing_zone", zone={"name": "Shared", "secure": True})
    with pytest.raises(core.PlatformError, match="Dev and Test"):
        platform.call("save_landing_zone", zone={"name": "Ops", "devtest": "sometimes"})
    # A change keeps what it doesn't name, and where the zone lives.
    platform.call("save_landing_zone", zone={"id": "hr_secure", "lakehouse_id": "lh-1", "workspace_id": "ws-l"})
    [z] = platform.call("save_landing_zone", zone={"id": "hr_secure", "devtest": "read"})
    assert z["devtest"] == "read" and z["secure"] and z["lakehouse_id"] == "lh-1" and z["label"] == "Highly Confidential"


def test_sources_take_a_zone_and_a_used_zone_cant_be_removed(platform):
    platform.call("save_landing_zone", zone={"name": "Shared"})
    platform.call("save_landing_zone", zone={"name": "HR"})
    [shared] = [z for z in platform.call("list_landing_zones") if z["id"] == "shared"]
    assert shared["devtest"] == "read"  # Dev and Test read Shared like Prod
    with pytest.raises(core.PlatformError, match="isn't a landing zone"):
        platform.call("save_source", source={"name": "workday", "zone": "payroll"})
    platform.call("save_source", source={"name": "sap"})
    sources = platform.call("save_source", source={"name": "workday", "zone": "HR"})
    assert {s["name"]: s["zone"] for s in sources} == {"sap": "", "workday": "hr"}
    # Saving without a zone keeps the one it has (callers that don't know zones).
    assert platform.call("save_source", source={"name": "workday", "title": "Workday"})[1]["zone"] == "hr"
    zones = {z["id"]: z["sources"] for z in platform.call("list_landing_zones")}
    assert zones == {"shared": ["sap"], "hr": ["workday"]}
    with pytest.raises(core.PlatformError, match="still holds workday"):
        platform.call("delete_landing_zone", id="hr")
    platform.call("save_source", source={"name": "workday", "zone": ""})
    assert [z["id"] for z in platform.call("delete_landing_zone", id="hr")] == ["shared"]
    assert ("delete landing zone", "hr") in _audit(platform)


def test_a_platform_made_before_zones_gets_them_by_adding_only(tmp_path):
    path = tmp_path / "old.sqlite"
    LocalPlatform(path, lambda: ADMIN).call("save_source", source={"name": "sap"})
    conn = sqlite3.connect(path)  # as it was before: no zone column, no landing_zone table
    conn.execute("ALTER TABLE catalog__source DROP COLUMN zone")
    conn.execute("DROP TABLE catalog__landing_zone")
    conn.commit()
    conn.close()
    with closing(sqlite3.connect(path)) as conn:
        db = core.Db(conn, "sqlite")
        assert core.list_landing_zones(db) == [] and core.list_sources(db)[0]["zone"] == ""  # a read changes nothing
        assert core.ensure_landing_zones(db) == ["catalog.landing_zone", "catalog.source.zone"]
        assert core.ensure_landing_zones(db) == []
        assert core.list_sources(db)[0]["name"] == "sap"


# --------------------------------------------------------------------------- setting it up


class FakeFabric:
    def __init__(self, workspaces=None, lakehouses=None):
        self.workspaces = list(workspaces or [])
        self.lakehouses = dict(lakehouses or {})  # (workspace, name) -> lakehouse
        self.created = []

    def list_workspaces(self):
        return self.workspaces

    def create_workspace(self, name, capacity_id="", description=""):
        ws = {"id": "ws-landing", "displayName": name}
        self.workspaces.append(ws)
        self.created.append(("workspace", name, capacity_id))
        return ws

    def find_lakehouse(self, ws, name):
        return self.lakehouses.get((ws, name))

    def create_lakehouse(self, ws, name, description=""):
        lh = {"id": f"lh-{name}", "displayName": name}
        self.lakehouses[(ws, name)] = lh
        self.created.append(("lakehouse", name, ws))
        return lh


def test_landing_setup_finds_or_creates_the_workspace_and_lh_landing():
    fabric = FakeFabric()
    out = setup_mod.landing_setup(fabric, capacity_id="cap")
    assert out["ready"] and out["config"] == {"workspace_id": "ws-landing", "workspace_name": "Platform - Landing",
                                              "lakehouse_id": "lh-LH_Landing"}
    assert fabric.created == [("workspace", "Platform - Landing", "cap"), ("lakehouse", "LH_Landing", "ws-landing")]
    again = setup_mod.landing_setup(fabric, existing=out["config"])
    assert [s["detail"] for s in again["steps"]] == ["Kept Platform - Landing", "Kept LH_Landing"] and len(fabric.created) == 2
    # A workspace someone already made with that name is used, not made again.
    found = FakeFabric([{"id": "ws-x", "displayName": "Platform - Landing"}])
    assert setup_mod.landing_setup(found)["steps"][0]["detail"] == "Found Platform - Landing"
    assert found.created == [("lakehouse", "LH_Landing", "ws-x")]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    fabric = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    s.fake = fabric
    return s


def test_setting_up_records_the_shared_zone_and_new_zones_get_a_lakehouse(svc):
    assert not svc.platform_status()["landing"]["central"]
    out = svc.platform_landing_setup()
    assert out["ready"], out["steps"]
    assert out["status"]["landing"] == {"central": True, "default_name": "Platform - Landing",
                                             "workspace_id": "ws-landing", "workspace_name": "Platform - Landing",
                                             "lakehouse_id": "lh-LH_Landing"}
    [shared] = svc._catalog().call("list_landing_zones")
    assert (shared["id"], shared["lakehouse_name"], shared["lakehouse_id"], shared["devtest"]) == (
        "shared", "LH_Landing", "lh-LH_Landing", "read")

    svc.platform_save_domain({"name": "HR"})
    made = svc.platform_create_landing_zone({"name": "HR-Secure", "domain_id": "hr", "secure": True,
                                             "readers": ["HR Secure"], "label": "Highly Confidential"})
    assert made["created"] and made["lakehouse"] == "LH_Landing_HR_Secure"
    assert ("lakehouse", "LH_Landing_HR_Secure", "ws-landing") in svc.fake.created
    zone = [z for z in made["zones"] if z["id"] == "hr_secure"][0]
    assert zone["workspace_id"] == "ws-landing" and zone["lakehouse_id"] == "lh-LH_Landing_HR_Secure"
    assert zone["devtest"] == "none" and zone["secure"]
    # Again: the lakehouse is kept, not made twice.
    assert not svc.platform_create_landing_zone({"name": "HR-Secure"})["created"]
    with pytest.raises(ValueError, match="Shared is made"):
        svc.platform_create_landing_zone({"name": "Shared"})


def test_zones_need_the_landing_workspace_and_a_platform_admin(svc):
    with pytest.raises(LookupError, match="Set up the landing workspace first"):
        svc.platform_create_landing_zone({"name": "HR"})
    path = svc._catalog().path
    LocalPlatform(path, lambda: ADMIN).call("claim_admin")
    svc._local_platforms["prod"] = LocalPlatform(path, lambda: "lee@example.com")
    with pytest.raises(PermissionError, match="Only platform admins"):
        svc.platform_landing_setup()
    assert svc.fake.created == []  # nothing was made in Fabric


# --------------------------------------------------------------------------- where landing reads from

ZONES = {"zones": {"shared": {"id": "shared", "name": "Shared", "lakehouse_name": "LH_Landing",
                              "lakehouse_id": "lh-shared", "workspace_id": "ws-landing", "devtest": "read"},
                   "hr": {"id": "hr", "name": "HR", "lakehouse_name": "LH_Landing_HR",
                          "lakehouse_id": "lh-hr", "workspace_id": "ws-landing", "devtest": "read"},
                   "hr_secure": {"id": "hr_secure", "name": "HR-Secure", "lakehouse_name": "LH_Landing_HR_Secure",
                                 "lakehouse_id": "lh-hrs", "workspace_id": "ws-landing", "devtest": "none",
                                 "secure": True}},
         "sources": {"sap": "", "workday": "hr", "payroll": "hr_secure"}}


def _onelake(env, tmp_path, zones=ZONES):
    store = OneLakeStore(env, f"ws-{env}", {"LH_Landing": f"lh-{env}-landing"}, lambda: "tok", tmp_path,
                         lakehouse_workspaces={"LH_Landing": f"ws-{env}-platform"})
    store.landing_zones = (lambda: zones) if zones is not None else None
    return store


def _switched(zones=ZONES):
    shared = zones["zones"]["shared"] | {"switched_at": "2026-10-23T10:00:00+00:00"}
    return {"zones": zones["zones"] | {"shared": shared}, "sources": zones["sources"]}


@pytest.mark.parametrize("env", ["dev", "test", "prod"])
def test_a_new_zone_is_read_in_the_central_workspace_with_no_switch(env, tmp_path):
    store = _onelake(env, tmp_path)
    engine = Engine(None, store)
    assert engine.landing_lakehouse("workday/workers/*.csv") == "LH_Landing_HR"
    assert (store.workspace_of("LH_Landing_HR"), store.lakehouse_ids["LH_Landing_HR"]) == ("ws-landing", "lh-hr")


@pytest.mark.parametrize("env", ["dev", "test", "prod"])
def test_shared_sources_read_their_own_lh_landing_until_reads_are_switched(env, tmp_path):
    before = _onelake(env, tmp_path)
    # Not switched: Shared (and a source the catalog doesn't know) is the environment's own LH_Landing.
    for pattern in ("SAP/orders/*.csv", "unknown/x/*.csv"):
        assert Engine(None, before).landing_lakehouse(pattern) == LANDING_LAKEHOUSE
        assert (before.workspace_of("LH_Landing"), before.lakehouse_ids["LH_Landing"]) == (f"ws-{env}-platform", f"lh-{env}-landing")
    after = _onelake(env, tmp_path, _switched())
    assert Engine(None, after).landing_lakehouse("SAP/orders/*.csv") == LANDING_LAKEHOUSE
    assert (after.workspace_of("LH_Landing"), after.lakehouse_ids["LH_Landing"]) == ("ws-landing", "lh-shared")


def test_dev_and_test_stop_at_a_zone_they_dont_read(tmp_path):
    for env in ("dev", "test"):
        with pytest.raises(CheckStop, match="payroll is in the secure zone HR-Secure, which Dev and Test don't read"):
            Engine(None, _onelake(env, tmp_path)).landing_lakehouse("payroll/runs/*.csv")
    assert Engine(None, _onelake("prod", tmp_path)).landing_lakehouse("payroll/runs/*.csv") == "LH_Landing_HR_Secure"
    # On this PC every zone is the local landing folder, and the same rule holds.
    local = LocalStore(tmp_path / "dev", "dev", landing=tmp_path / "landing")
    local.landing_zones = lambda: ZONES
    assert Engine(None, local).landing_lakehouse("workday/workers/*.csv") == LANDING_LAKEHOUSE
    with pytest.raises(CheckStop, match="secure zone HR-Secure"):
        Engine(None, local).landing_lakehouse("payroll/runs/*.csv")


def test_without_zones_each_environment_reads_its_own_lh_landing(tmp_path):
    for zones in (None, {"zones": {}, "sources": {}}):
        store = _onelake("test", tmp_path, zones)
        assert Engine(None, store).landing_lakehouse("payroll/runs/*.csv") == LANDING_LAKEHOUSE
        assert (store.workspace_of("LH_Landing"), store.lakehouse_ids["LH_Landing"]) == ("ws-test-platform", "lh-test-landing")


def test_switching_reads_is_admin_only_audited_and_kept(platform):
    with pytest.raises(core.PlatformError, match="Set up the landing workspace first"):
        platform.call("switch_landing_reads")
    platform.call("save_landing_zone", zone={"name": "Shared", "workspace_id": "ws-landing", "lakehouse_id": "lh-shared"})
    [shared] = platform.call("list_landing_zones")
    assert shared["switched_at"] == "" and not shared["read"]
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        _as(platform, "lee@example.com").call("switch_landing_reads")
    [shared] = platform.call("switch_landing_reads")
    assert shared["switched_at"] and shared["switched_by"] == ADMIN and shared["read"]
    assert ("switch landing reads", "shared") in _audit(platform)
    # Saving the zone again (setup run twice) keeps the switch.
    [again] = platform.call("save_landing_zone", zone={"name": "Shared"})
    assert again["switched_at"] == shared["switched_at"]


def test_a_landing_zone_table_made_before_the_switch_gains_its_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    LocalPlatform(path, lambda: ADMIN).call("save_landing_zone", zone={"name": "Shared"})
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("ALTER TABLE catalog__landing_zone DROP COLUMN switched_at")
        conn.execute("ALTER TABLE catalog__landing_zone DROP COLUMN switched_by")
        conn.commit()
        db = core.Db(conn, "sqlite")
        assert not core.list_landing_zones(db)[0]["read"]
        assert core.ensure_landing_zones(db) == ["catalog.landing_zone.switched_at", "catalog.landing_zone.switched_by"]
        assert core.ensure_landing_zones(db) == []


def test_a_run_sent_to_fabric_uses_the_zone_map_it_was_sent_with(tmp_path):
    proj = tmp_path / "proj"
    init_project(proj, "3", "Chris", False)
    write(proj, "metadata/entities/bronze.payroll.toml", ENTITY.format(name="payroll", source="payroll"))
    landing = _switched()
    bundle = tmp_path / "bundle"
    for rel, data in bundle_files(proj, {"env": "dev", "landing": landing}).items():
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_bytes(data)
    store = LocalStore(tmp_path / "dev", "dev")
    out = run_bundle(bundle, entities="bronze.payroll", stores=(store, store), results_dir=tmp_path / "r")
    [res] = out["results"]
    assert "payroll is in the secure zone HR-Secure, which Dev and Test don't read" in res["message"]
    assert store.landing_map() == landing


def test_a_catalog_that_cant_be_read_stops_the_landing_read(tmp_path):
    store = _onelake("prod", tmp_path)

    def broken():
        raise PermissionError("UDF_Platform refused")
    store.landing_zones = broken
    with pytest.raises(CheckStop, match="couldn't be read from the platform's catalog"):
        Engine(None, store).landing_lakehouse("sap/orders/*.csv")


ENTITY = """
    name = "bronze.{name}"
    owner = "Chris"
    [source]
    landing = "{source}/workers/*.csv"
    [contract]
    keys = ["id"]
    [contract.columns]
    id = "int64"
"""


def test_runs_use_the_zones_once_the_landing_workspace_is_set_up(svc):
    folder = svc._landing_dir("dev") / "Files"
    for source in ("workday", "payroll"):
        (folder / source / "workers").mkdir(parents=True)
        (folder / source / "workers" / "day1.csv").write_text("id\n1\n2\n", encoding="utf-8")
    root = svc._project().root
    write(root, "metadata/entities/bronze.workday.toml", ENTITY.format(name="workday", source="workday"))
    write(root, "metadata/entities/bronze.payroll.toml", ENTITY.format(name="payroll", source="payroll"))
    # Before: no zones, so no catalog lookup and both read this PC's landing folder.
    assert svc._store(svc._project(), "dev").landing_zones is None
    assert [r["status"] for r in svc.meta_run(["bronze.workday", "bronze.payroll"])] == ["ok", "ok"]

    svc.platform_landing_setup()
    svc.platform_create_landing_zone({"name": "HR-Secure", "secure": True})
    svc.platform_save_source({"name": "workday"})
    svc.platform_save_source({"name": "payroll", "zone": "hr_secure"})
    store = svc._store(svc._project(), "dev")
    assert store.landing_zone("payroll")["id"] == "hr_secure"
    assert store.landing_zone("workday") is None  # Shared: this environment's own, until reads are switched
    svc.platform_switch_landing_reads()
    assert svc._store(svc._project(), "dev").landing_zone("workday")["id"] == "shared"
    by = {r["entity"]: r for r in svc.meta_run(["bronze.workday", "bronze.payroll"])}
    assert by["bronze.workday"]["status"] == "ok"  # Shared, read from the same local folder
    assert by["bronze.payroll"]["status"] != "ok"
    assert "payroll is in the secure zone HR-Secure, which Dev and Test don't read" in by["bronze.payroll"]["message"]


# --------------------------------------------------------------------------- the Platform page


def test_the_landing_tab_lists_zones_and_each_source_in_its_zone(svc, monkeypatch):
    svc.platform_save_source({"name": "sap", "expected_hours": "24"})
    before = svc.platform_landing("dev")
    assert not before["central"] and before["zones"] == [] and before["kind"] == "local"

    svc.platform_landing_setup()
    svc.platform_create_landing_zone({"name": "HR"})
    svc.platform_create_landing_zone({"name": "HR-Secure", "secure": True})
    svc.platform_save_source({"name": "workday", "zone": "hr", "expected_hours": "24"})
    svc.platform_save_source({"name": "payroll", "zone": "hr_secure"})
    listed = {"lh-LH_Landing": [{"path": "sap/orders/a.csv", "size": 10, "modified": "2020-01-01T00:00:00+00:00"}],
              "lh-LH_Landing_HR": [{"path": "workday/workers/w.csv", "size": 5, "modified": "2999-01-01T00:00:00+00:00"}]}

    def listing(self, ws, lh):
        if lh not in listed:
            raise PermissionError("403: no access to this lakehouse")
        return listed[lh]
    monkeypatch.setattr(Service, "_zone_listing", listing)
    for env in ("dev", "prod"):  # the same for every environment
        out = svc.platform_landing(env)
        assert out["central"] and out["where"] == "Platform - Landing"
        assert [z["id"] for z in out["zones"]] == ["shared", "hr", "hr_secure"]
        by = {s["name"]: s for s in out["sources"]}
        assert (by["sap"]["zone_name"], by["workday"]["zone_name"], by["payroll"]["zone_name"]) == ("Shared", "HR", "HR-Secure")
        assert by["sap"]["late"] and not by["workday"]["late"]  # the late flag still works
        assert "HR-Secure (LH_Landing_HR_Secure) couldn't be read" in out["problem"]
    assert [f["path"] for f in svc.platform_landing_files("dev", "workday")] == ["workday/workers/w.csv"]
    assert svc.platform_landing_files("dev", "sap", "orders")[0]["zone"] == "shared"
    assert svc.platform_overview("test")["landing"]["central"]


def test_another_pc_picks_up_the_landing_workspace_from_the_catalog(svc):
    svc.platform_landing_setup()
    svc._save_platform("landing", {})  # this PC never set it up
    assert svc._landing_resolver() is None
    assert svc.platform_landing("dev")["central"]
    assert svc._landing_config()["workspace_id"] == "ws-landing" and svc._landing_resolver() is not None


# --------------------------------------------------------------------------- a UDF_Platform older than zones


class _Old404:
    """A UDF_Platform made before landing zones: Fabric answers 404 for a function it doesn't have."""

    def post(self, url, headers, json, timeout):  # noqa: A002 - requests' own name
        resp = type("Response", (), {"status_code": 404, "content": b"", "text": "", "json": lambda self: {}})()
        return resp


def _old_platform(svc, monkeypatch):
    svc._save_platform("landing", {"workspace_id": "ws-landing", "workspace_name": "Platform - Landing", "lakehouse_id": "lh"})
    old = FabricPlatform(lambda: "tok", "ws-prod", "fn-prod", session=_Old404())
    monkeypatch.setattr(Service, "_catalog", lambda self: old)


def test_an_old_udf_platform_says_to_finish_or_repair(svc, monkeypatch, tmp_path):
    _old_platform(svc, monkeypatch)
    with pytest.raises(LookupError, match=r"can't use landing zones yet: UDF_Platform has no switch_landing_reads "
                                          r"function\. After an update, a platform admin runs Platform > Settings > Finish or repair once"):
        svc.platform_switch_landing_reads()
    with pytest.raises(LookupError, match="UDF_Platform has no delete_landing_zone function"):
        svc.platform_delete_landing_zone("hr")
    # A run in Fabric stops with the same words, not "UDF_Platform wasn't found".
    store = _onelake("prod", tmp_path, None)
    store.landing_zones = svc._landing_resolver()
    with pytest.raises(CheckStop, match="UDF_Platform has no list_landing_zones function"):
        Engine(None, store).landing_lakehouse("sap/orders/*.csv")
    # The Landing zone tab still shows, as before zones.
    assert not svc._landing_zones()


def test_dev_on_a_pc_reads_the_local_folder_when_the_catalog_cant_be_read(svc, monkeypatch):
    folder = svc._landing_dir("dev") / "Files" / "payroll" / "workers"
    folder.mkdir(parents=True)
    (folder / "day1.csv").write_text("id\n1\n2\n", encoding="utf-8")
    write(svc._project().root, "metadata/entities/bronze.payroll.toml", ENTITY.format(name="payroll", source="payroll"))
    _old_platform(svc, monkeypatch)
    store = svc._store(svc._project(), "dev")
    assert isinstance(store, LocalStore) and store.landing_zones is not None
    assert Engine(None, store).landing_lakehouse("payroll/workers/*.csv") == LANDING_LAKEHOUSE
    [res] = svc.meta_run(["bronze.payroll"])
    assert res["status"] == "ok", res
    # When the zone map is known, the Prod-only stop still holds on a PC.
    known = LocalStore(svc._project().local_data / "dev", "dev", landing=svc._landing_dir("dev"))
    known.landing_zones = lambda: ZONES
    with pytest.raises(CheckStop, match="secure zone HR-Secure"):
        Engine(None, known).landing_lakehouse("payroll/workers/*.csv")
