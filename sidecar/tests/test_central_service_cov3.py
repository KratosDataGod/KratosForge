"""The platform and mesh service's less travelled paths: shared Fabric platforms, unreadable catalogs and
workspaces, older platforms, and the edges of links, products and keys. Fakes only: nothing reaches Fabric."""

from types import SimpleNamespace

import pytest

from fabric_desktop.auth import TENANT_WRITE_SCOPES
from fabric_desktop.central import service as service_mod
from fabric_desktop.central.client import FabricPlatform, LocalPlatform
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.meta.project import GateError
from fabric_desktop.onelake import TableRef
from fabric_desktop.service import Service

ADMIN = "chris@example.com"


class Cat:
    """A catalog that answers each operation from a table: a value, a callable, or an exception to raise."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def call(self, op, **kw):
        self.calls.append((op, kw))
        answer = self.answers[op]
        if isinstance(answer, Exception):
            raise answer
        return answer(**kw) if callable(answer) else answer


class Wrap:
    """A real catalog with some operations answered or refused instead."""

    def __init__(self, inner, **override):
        self.inner, self.override = inner, override

    def call(self, op, **kw):
        if op in self.override:
            answer = self.override[op]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return self.inner.call(op, **kw)


@pytest.fixture
def bare(tmp_path):
    return Service(root=tmp_path / "app")


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def _shared_functions(svc, monkeypatch):
    cfg = {"dev": {"functions_id": "f1", "workspace_id": "w1"}, "test": {"functions_id": "f1", "workspace_id": "w1"},
           "prod": {}}
    monkeypatch.setattr(svc, "_platform_config", lambda: {k: dict(v) for k, v in cfg.items()})


# ---------------------------------------------------------------- which platform


def test_an_environment_with_functions_uses_fabric_and_a_shared_one_is_listed_once(bare, monkeypatch):
    _shared_functions(bare, monkeypatch)
    dev = bare._platform("dev")
    assert isinstance(dev, FabricPlatform)
    assert (dev.workspace_id, dev.functions_id) == ("w1", "f1")
    # Moving keys: Prod has no database on this PC (and looking doesn't make one); Test and Dev are one.
    sources = bare._share_sources()
    assert [env for env, _ in sources] == ["test"]
    assert not (bare.root / "platform" / "platform-prod.sqlite").exists()
    distinct = bare._distinct_platforms()
    assert [env for env, _ in distinct] == ["dev", "prod"]  # Test shares Dev's functions
    assert isinstance(distinct[1][1], LocalPlatform)


def test_a_keys_workspace_not_ready_yet_is_saved_without_adopting_keys(bare, monkeypatch):
    monkeypatch.setattr(Service, "fabric", property(lambda self: object()))
    monkeypatch.setattr(service_mod.keys_home, "setup",
                        lambda fabric, token, name, capacity, cfg: {"config": {"workspace_id": "k1"}, "ready": False,
                                                                    "steps": []})

    def never():
        raise AssertionError("keys adopted before the workspace was ready")
    monkeypatch.setattr(bare, "_adopt_live_keys", never)
    out = bare.platform_keys_setup("Keys")
    assert out["ready"] is False
    assert out["status"]["keys"]["workspace_id"] == "k1"
    assert out["status"]["keys"]["shared"] is False


def test_keys_another_admin_already_moved_are_used_without_a_move(bare, monkeypatch):
    monkeypatch.setattr(bare, "_keys_config", lambda: {})
    monkeypatch.setattr(bare, "_check_can_share", lambda: {})
    monkeypatch.setattr(bare, "_adopt_live_keys", lambda: True)
    out = bare.platform_share_keys(approve=True)
    assert out["ok"] is True and out["already"] is True and out["tables"] == [] and out["renumbered"] == 0


def test_the_first_person_to_move_keys_claims_the_shared_database(svc, monkeypatch):
    svc._platform("prod").call("ensure_key_table", name="customer")
    calls = []

    class Unclaimed(Wrap):
        def call(self, op, **kw):
            calls.append(op)
            return super().call(op, **kw)
    shared = Unclaimed(svc._shared_keys(), whoami={"can_claim": True}, claim_admin={})
    monkeypatch.setattr(svc, "_shared_keys", lambda: shared)
    out = svc.platform_share_keys(approve=True)
    assert out["ok"] is True
    assert calls.index("whoami") < calls.index("claim_admin") < calls.index("list_admins")


# ---------------------------------------------------------------- domains from Fabric


def test_the_fabric_admin_client_asks_for_write_scopes_only_to_create(bare, monkeypatch):
    asked = []
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(_token=lambda s: asked.append(s) or "t")))
    assert bare._fabric_admin(write=True)._token() == "t"
    assert bare._fabric_admin()._token() == "t"
    assert asked[0] == TENANT_WRITE_SCOPES and asked[1] != TENANT_WRITE_SCOPES


def test_a_fabric_failure_other_than_a_refusal_is_raised_as_it_is(bare, monkeypatch):
    class Admin:
        def list_admin_domains(self):
            raise FabricError("server error", status=500)
    monkeypatch.setattr(bare, "_fabric_admin", lambda write=False: Admin())
    with pytest.raises(FabricError, match="server error"):
        bare.platform_import_fabric_domains()


def test_workspace_domains_handle_unreadable_and_shared_workspaces(bare, monkeypatch):
    project = SimpleNamespace(environments={
        "dev": {"workspace_id": "w1", "workspace_name": "Dev", "workspaces": {"ingest": {"id": "w2", "name": "Ing"}}},
        "test": {"workspace_id": "w1"},
        "prod": {"workspace_id": "w3", "workspace_name": "Prod"},
    })

    class Fabric:
        def get_workspace(self, wid):
            if wid == "w3":
                raise RuntimeError("no access")
            return {"w1": {"displayName": "One", "domainId": "fd1"}, "w2": {"domainId": "fd9"}}[wid]
    monkeypatch.setattr(Service, "fabric", property(lambda self: Fabric()))
    monkeypatch.setattr(bare, "_project", lambda: project)
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_domains=[{"id": "sales", "fabric_domain": "fd1"}]))
    out = bare.mesh_workspace_domains()
    rows = {(r["env"], r["designation"]): r for r in out["workspaces"]}
    assert rows[("test", "")]["workspace_name"] == "One" and rows[("test", "")]["domain"] == "sales"  # Dev's answer
    assert rows[("prod", "")]["problem"] == "no access" and rows[("prod", "")]["domain"] is None
    assert rows[("dev", "ingest")]["fabric_domain"] == "fd9"
    assert out["suggested"] == {"domain": "sales", "subdomain": ""}  # Prod has none, so Test's
    assert out["unnamed"] == ["fd9"] and out["imported"] is True
    # Nothing imported: no suggestion at all.
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_domains=[]))
    out = bare.mesh_workspace_domains()
    assert out["suggested"] is None and out["imported"] is False


# ---------------------------------------------------------------- the Platform page


def test_an_older_platform_has_no_change_window(bare, monkeypatch):
    def old(catalog, op, purpose, **kw):
        raise LookupError("Run Finish or repair")
    monkeypatch.setattr(service_mod, "call_or_repair", old)
    assert bare.platform_change_window() == {"available": False, "problem": "Run Finish or repair", "window": None}


def test_an_older_platform_has_no_owner_coverage(bare, monkeypatch):
    def old(catalog, op, purpose, **kw):
        raise LookupError("Run Finish or repair")
    monkeypatch.setattr(service_mod, "call_or_repair", old)
    out = bare.platform_owner_coverage()
    assert out["available"] is False and out["problem"] == "Run Finish or repair"
    assert out["gaps"] == [] and out["checked"] == 0 and out["quiet_days"] == service_mod.core.QUIET_DAYS


def test_each_platform_stops_at_the_catalog_and_reports_the_rest(bare, monkeypatch):
    ok, bad = Cat(ensure_key_table={"name": "t"}), Cat(ensure_key_table=RuntimeError("offline"))
    monkeypatch.setattr(bare, "_distinct_platforms", lambda: [("dev", bad), ("prod", ok)])
    out = bare._each_platform("ensure_key_table", name="t")
    assert out == [{"env": "prod", "ok": True, "result": {"name": "t"}}, {"env": "dev", "ok": False, "message": "offline"}]
    monkeypatch.setattr(bare, "_distinct_platforms", lambda: [("dev", ok), ("prod", bad)])
    with pytest.raises(RuntimeError, match="offline"):
        bare._each_platform("ensure_key_table", name="t")


def test_a_landing_setup_without_a_lakehouse_yet_isnt_used(bare, monkeypatch):
    monkeypatch.setattr(Service, "fabric", property(lambda self: object()))
    monkeypatch.setattr(bare, "_require_platform_admin", lambda action: None)
    monkeypatch.setattr(service_mod.setup_mod, "landing_setup",
                        lambda fabric, name, cap, cfg: {"config": {"workspace_id": "wl"}, "ready": False, "steps": []})
    saved = []
    monkeypatch.setattr(bare, "_save_platform", lambda env, cfg: saved.append(env))
    out = bare.platform_landing_setup()
    assert out["steps"] == [] and out["ready"] is False and saved == []
    assert out["status"]["landing"]["central"] is False


def test_zone_files_skip_zones_with_no_lakehouse(bare):
    assert bare._zone_files([{"id": "hr", "workspace_id": "w"}, {"id": "x"}]) == ([], [])


def test_an_environment_landing_lakehouse_in_fabric_is_listed_there(bare, monkeypatch):
    monkeypatch.setattr(bare, "_platform_config",
                        lambda: {"dev": {"workspace_id": "w", "landing_id": "lh"}, "test": {}, "prod": {}})
    monkeypatch.setattr(bare, "_zone_listing", lambda ws, lh: [{"path": f"{ws}/{lh}/a.csv", "size": 1, "modified": ""}])
    assert bare._landing_files("dev") == [{"path": "w/lh/a.csv", "size": 1, "modified": ""}]


def test_the_landing_page_shows_sources_when_files_cant_be_read_or_have_odd_times(bare, monkeypatch, tmp_path):
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_sources=[{"name": "sap", "expected_hours": 4}]))
    monkeypatch.setattr(bare, "_landing_zones", lambda: [])
    monkeypatch.setattr(bare, "_landing_dir", lambda env: tmp_path / "landing")

    def unreadable(env):
        raise OSError("disk gone")
    monkeypatch.setattr(bare, "_landing_files", unreadable)
    out = bare.platform_landing("dev")
    assert out["problem"] == "The landing zone couldn't be read: disk gone"
    [sap] = out["sources"]
    assert sap["registered"] and sap["entities"] == [] and sap["late"] is True  # expected, never arrived
    monkeypatch.setattr(bare, "_landing_files", lambda env: [
        {"path": "loose.csv", "size": 5, "modified": "2026-01-01T00:00:00+00:00"},
        {"path": "sap/orders/a.csv", "size": 2, "modified": "yesterday"}])
    out = bare.platform_landing("dev")
    assert out["problem"] is None
    [sap] = out["sources"]  # a file at the top of Files belongs to no source
    assert sap["entities"] == [{"name": "orders", "files": 1, "size": 2, "newest": "yesterday"}]
    assert sap["late"] is False  # a time that can't be read is never called late


def test_landing_files_fall_back_or_say_why_a_zone_cant_be_read(bare, monkeypatch):
    monkeypatch.setattr(bare, "_central_landing", lambda: {"workspace_id": "wl"})
    monkeypatch.setattr(bare, "_landing_zones", lambda: [])
    monkeypatch.setattr(bare, "_landing_files", lambda env: [
        {"path": "sap/a.csv", "size": 1, "modified": "1"}, {"path": "sap/b.csv", "size": 1, "modified": "2"},
        {"path": "other/c.csv", "size": 1, "modified": "3"}])
    assert [f["path"] for f in bare.platform_landing_files("dev", "sap")] == ["sap/b.csv", "sap/a.csv"]
    monkeypatch.setattr(bare, "_landing_zones", lambda: [{"id": "shared", "workspace_id": "w", "lakehouse_id": "l"}])
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_sources=[{"name": "sap"}]))
    monkeypatch.setattr(bare, "_zone_files", lambda zones: ([], ["Shared couldn't be read: 403"]))
    with pytest.raises(PermissionError, match="403"):
        bare.platform_landing_files("dev", "sap")


def test_a_request_for_a_named_project_doesnt_open_one(bare, monkeypatch):
    cat = Cat(request={"id": 7})
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    monkeypatch.setattr(bare, "_project", lambda: (_ for _ in ()).throw(AssertionError("opened")))
    told = []
    monkeypatch.setattr(bare, "_notify_request", lambda req, build: told.append(req))
    assert bare.platform_request("access", "crm/customers", project="finance") == {"id": 7}
    assert cat.calls[0][1]["project"] == "finance" and told == [{"id": 7}]


def test_a_request_without_an_id_tells_nobody(bare, monkeypatch):
    monkeypatch.setattr(bare, "_governance_notify", lambda events: pytest.fail("notified"), raising=False)
    assert bare._notify_request({}, None) is None
    assert bare._notify_request({"kind": "access"}, None) is None


def test_an_approved_key_table_that_one_environment_cant_make_is_a_problem(bare, monkeypatch):
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(decide_request={
        "kind": "key_table", "target": "orders", "detail": {"business_key": "id"}, "reason": "for sales"}))
    prod, dev, test = Cat(), Cat(ensure_key_table=RuntimeError("offline")), Cat(ensure_key_table={})
    monkeypatch.setattr(bare, "_key_platforms", lambda: [("prod", prod), ("dev", dev), ("test", test)])
    monkeypatch.setattr(bare, "_notify_request", lambda req, build: None)
    # Other mixins wrap platform_decide; this is the platform's own step.
    out = service_mod.PlatformMixin.platform_decide(bare, "r1", approve=True)
    assert out["problems"] == ["Dev: offline"]
    assert prod.calls == []  # the catalog's platform made it already
    assert test.calls == [("ensure_key_table", {"name": "orders", "business_key": "id", "description": "for sales"})]


def test_the_overview_lists_unreadable_keys_and_landing(bare, monkeypatch):
    cat = Cat(list_domains=[{"id": "a"}, {"id": "b", "parent_id": "a"}], list_projects=[{"team": "x"}, {}],
              list_products=[{"status": "published"}, {"status": "draft"}],
              list_requests=[{"can_decide": True}, {"mine": True}], audit=[])
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    monkeypatch.setattr(bare, "platform_whoami", lambda: {"admin": True})

    def keys(env):
        raise RuntimeError("keys offline")

    def landing(env):
        raise RuntimeError("landing offline")
    monkeypatch.setattr(bare, "_keys_platform", keys)
    monkeypatch.setattr(bare, "platform_landing", landing)
    out = bare.platform_overview()
    assert out["problems"] == ["keys offline"]
    assert out["landing"] == {"sources": [], "problem": "landing offline"}
    assert out["counts"]["domains"] == 1 and out["counts"]["subdomains"] == 1
    assert out["counts"]["key_tables"] == 0 and out["counts"]["published"] == 1 and out["counts"]["teams"] == 1


def test_mesh_home_and_map_without_a_catalog_or_project(bare, monkeypatch):
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=RuntimeError("catalog offline"),
                                                      list_domains=RuntimeError("catalog offline")))
    home = bare.mesh_home("team")
    assert home["view"] == "team" and "catalog offline" in home["problem"]
    assert bare.mesh_map()["problem"] == "catalog offline"

    def none():
        raise LookupError("no project")
    monkeypatch.setattr(bare, "_project", none)
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_domains=[], list_projects=[], list_products=[
        {"id": "crm/a", "project": "crm", "status": "published", "consumers": [{"consumer_project": "fin"}]}]))
    out = bare.mesh_map()
    assert out["me"] == "" and out["links"] == [{"from": "fin", "product": "crm/a", "to": "crm"}]


def test_a_domain_product_in_my_domain_can_be_linked(bare, monkeypatch):
    product = {"id": "crm/a", "project": "crm", "access": "domain", "domain_id": "sales", "status": "published",
               "consumers": []}
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=[product], list_requests=[]))
    monkeypatch.setattr(bare, "_project", lambda: object())
    monkeypatch.setattr(service_mod.mesh, "identity", lambda project: {"name": "fin", "domain": "sales"})
    monkeypatch.setattr(bare, "_my_follows", lambda: None)
    monkeypatch.setattr(bare, "_product_groups", lambda catalog: None)
    monkeypatch.setattr(bare, "_product_health", lambda: None)
    [p] = bare.platform_products()["products"]
    assert p["can_link"] is True and p["why"] == "" and p["linked"] is False


def test_a_project_without_mesh_info_isnt_registered(bare, monkeypatch):
    cat = Cat()
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    monkeypatch.setattr(bare, "_project", lambda: SimpleNamespace(info={}))
    bare._register_open_project()
    assert cat.calls == []


def test_members_are_changed_in_the_open_project_or_a_named_one(svc, monkeypatch):
    seen = []
    monkeypatch.setattr(service_mod, "call_or_repair", lambda catalog, op, purpose, **kw: seen.append((op, kw)) or kw)
    svc.mesh_set_member("lee@example.com", "steward")
    svc.mesh_set_member("ana@example.com", project="finance")
    assert seen[0] == ("set_project_member", {"project": "crm", "user": "lee@example.com", "role": "steward"})
    assert seen[1][1]["project"] == "finance" and seen[1][1]["role"] == ""


# ---------------------------------------------------------------- the open project in the mesh


def test_the_mesh_overview_still_shows_the_project_when_the_catalog_cant_be_read(svc, monkeypatch):
    monkeypatch.setattr(svc, "_catalog", lambda: Wrap(None, list_domains=RuntimeError("offline")))
    out = svc.mesh_overview()
    assert out["problems"] == ["The platform's catalog couldn't be read: offline"]
    assert out["domains"] == [] and out["published"] == [] and out["projects"] == []
    assert out["project"]["name"] == "crm"


def test_setting_a_project_without_a_domain_skips_the_domain_checks(svc):
    out = svc.mesh_set_project({"team": "sales"})
    assert out["project"]["team"] == "sales" and out["domain_request"] is None


def test_a_subdomain_of_another_domain_is_refused(svc, monkeypatch):
    inner = svc._catalog()
    monkeypatch.setattr(svc, "_catalog", lambda: Wrap(inner, list_domains=[{"id": "sales"}, {"id": "hr"}]))
    with pytest.raises(GateError, match="hr isn't a subdomain of sales"):
        svc.mesh_set_project({"domain": "sales", "subdomain": "hr"})


def _product(name="customers", **kw):
    return {"name": name, "access": "public", "status": "published", "version": "1",
            "ports": [{"name": "customers", "entity": "silver.customers"}]} | kw


def test_a_renamed_product_drops_its_old_file_and_a_deleted_one_goes(svc):
    folder = svc._project().root / "metadata" / "products"
    svc.mesh_save_product(_product())
    svc.mesh_save_product(_product("clients"), original="customers")
    assert not (folder / "customers.toml").exists() and (folder / "clients.toml").exists()
    out = svc.mesh_delete_product("clients")
    assert not (folder / "clients.toml").exists()
    assert all(p.get("name") != "clients" for p in out["products"])


def test_publishing_skips_drafts(svc):
    svc.mesh_save_product(_product(status="draft"))
    assert svc.mesh_publish("dev") == {"env": "dev", "published": []}


def test_a_failed_publish_with_no_notices_yet_is_raised(svc, monkeypatch):
    svc.mesh_save_product(_product())
    monkeypatch.setattr(service_mod.mesh, "publish_problems", lambda project, saved: [])
    inner = svc._catalog()
    monkeypatch.setattr(svc, "_catalog", lambda: Wrap(inner, register_product=RuntimeError("catalog refused")))
    told = []
    monkeypatch.setattr(svc, "_governance_notify", lambda events: told.append(events))
    with pytest.raises(RuntimeError, match="catalog refused"):
        svc.mesh_publish("dev")
    assert told == []


def test_a_deprecation_whose_readers_cant_be_read_says_so(bare):
    problems, events, tell = [], [], []
    p = SimpleNamespace(status="deprecated", name="customers")
    bare._publish_notices(p, {"domain": ""}, {}, None, "crm", "dev", events, tell, problems)
    assert len(problems) == 1 and "readers couldn't be read" in problems[0]
    assert events == [] and tell == []


def test_a_deprecation_notice_with_no_one_to_tell_is_not_sent(bare, monkeypatch):
    monkeypatch.setattr(service_mod.notices, "product_deprecated", lambda product, consumers: None)
    problems, events, tell = [], [], []
    p = SimpleNamespace(status="deprecated", name="customers")
    before = {"crm/customers": {"id": "crm/customers", "status": "published", "consumers": []}}
    bare._publish_notices(p, {"domain": "", "deprecated_on": "2027-01-01"}, {}, before, "crm", "dev", events, tell,
                          problems)
    assert events == [] and tell == [] and problems == []


def test_products_unreadable_before_a_publish_give_none(bare):
    project = SimpleNamespace(products={"a": SimpleNamespace(status="deprecated")})
    assert bare._published_before(Cat(list_products=RuntimeError("offline")), project) is None


def test_a_link_alias_moved_to_another_product_keeps_nothing_of_the_old_one(svc):
    pinned = {"dev": "abfss://w@onelake.dfs.fabric.microsoft.com/l/Tables/x"}
    svc.mesh_save_link({"alias": "ext", "product": "crm/one", "port": "p", "accepts": "2",
                        "columns": {"id": "int64"}, "pinned": pinned})
    assert svc._project().links["ext"].accepts == "2"
    svc.mesh_save_link({"alias": "ext", "product": "crm/two", "port": "p", "pinned": pinned})
    link = svc._project().links["ext"]
    assert link.product == "crm/two" and link.accepts == "" and link.columns == {}
    svc.mesh_delete_link("ext")
    assert "ext" not in svc._project().links
    assert not (svc._project().root / "metadata" / "links" / "ext.toml").exists()


def test_sync_links_skips_pinned_links_and_waits_for_a_readable_catalog(bare, monkeypatch):
    monkeypatch.setattr(service_mod.mesh, "project_name", lambda project: "fin")
    pinned = SimpleNamespace(alias="ext", location=lambda env: "abfss://t" if env == "prod" else "")
    project = SimpleNamespace(links={"ext": pinned})
    cat = Cat(list_products=[])
    assert bare._sync_links(project, cat) == []
    assert cat.calls == [("list_products", {})]
    out = bare._sync_links(project, Cat(list_products=RuntimeError("offline")))
    assert len(out) == 1 and out[0].startswith("The project's links weren't checked against the catalog: ")
    assert "offline" in out[0]


def test_a_new_link_accepts_any_version_when_the_catalog_cant_say(bare, monkeypatch):
    monkeypatch.setattr(bare, "_catalog", lambda: Cat(list_products=RuntimeError("offline")))
    assert bare._current_version("crm/a") == ""


def test_links_resolve_to_a_pinned_table_or_the_published_port(bare, monkeypatch):
    links = {"pinned": SimpleNamespace(location=lambda env: "abfss://pinned"),
             "ported": SimpleNamespace(location=lambda env: "", product="crm/a", port="p", reads=lambda env: "prod")}
    cat = Cat(resolve_port={"workspace_id": "w", "lakehouse_id": "l", "schema_name": "", "table_name": "t"})
    monkeypatch.setattr(bare, "_catalog", lambda: cat)
    resolve = bare._link_table(SimpleNamespace(links=links), "dev")
    assert resolve("pinned") == "abfss://pinned"
    assert resolve("ported") == TableRef("w", "l", "dbo", "t").uri
    assert cat.calls == [("resolve_port", {"product_id": "crm/a", "port": "p", "env": "prod"})]


def test_catalog_text_shows_the_problem_when_the_catalog_cant_be_read(svc, monkeypatch):
    monkeypatch.setattr(svc, "_catalog", lambda: Wrap(None, enrichment=RuntimeError("offline")))
    out = svc.mesh_about("silver.customers")
    assert out["problem"] == "offline" and out["glossary"] == [] and out["entity"] == "silver.customers"


# ---------------------------------------------------------------- keys into SQL_Platform


def test_keys_that_dont_all_match_dont_switch_the_environment(bare, monkeypatch):
    project = SimpleNamespace(environments={"dev": {}})
    monkeypatch.setattr(bare, "_project", lambda: project)
    monkeypatch.setattr(bare, "_keys_in_platform", lambda project, env: False)
    monkeypatch.setattr(bare, "_keys_store", lambda project, env: "lh")
    monkeypatch.setattr(bare, "_keys_config", lambda: {})
    monkeypatch.setattr(bare, "_platform_keys", lambda env: "platform")
    monkeypatch.setattr(service_mod, "migrate", lambda project, lh, pk, **kw: {"ok": False, "tables": ["t"]})
    written = []
    monkeypatch.setattr(bare, "_write_environments", lambda project, envs: written.append(envs))
    assert bare.meta_keys_to_platform("dev") == {"env": "dev", "ok": False, "tables": ["t"]}
    assert written == []


def test_going_back_to_lh_keys_when_the_platform_issued_nothing_new(bare, monkeypatch):
    project = SimpleNamespace(environments={"dev": {"keys": "platform", "keys_moved_at": "2026-01-01"}},
                              key_tables={"customer": SimpleNamespace(name="customer")}, reload=lambda: None)
    monkeypatch.setattr(bare, "_project", lambda: project)
    monkeypatch.setattr(bare, "_keys_in_platform", lambda project, env: True)
    monkeypatch.setattr(bare, "_platform_keys", lambda env: SimpleNamespace(stats=lambda name: {"next": 3}))
    monkeypatch.setattr(bare, "_keys_store", lambda project, env: "lh")
    monkeypatch.setattr(service_mod.keys_mod, "key_stats", lambda store, kt: {"next": 3})
    written = []
    monkeypatch.setattr(bare, "_write_environments", lambda project, envs: written.append(envs))
    assert bare.meta_keys_to_lakehouse("dev") == {"env": "dev", "keys": "lakehouse"}
    assert written == [{"dev": {"keys": "lakehouse"}}]
